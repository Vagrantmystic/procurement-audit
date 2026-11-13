"""Motor de pronóstico: modelos, backtesting y política por patrón de demanda.

El motor trabaja con series **semanales** (semana terminada en domingo) porque
reduce el ruido diario, conserva suficientes observaciones y permite evaluar
horizontes de 4 semanas (≈30 días).

Decisión de diseño importante: el modelo **no se elige producto a producto**.
Con historias cortas y demanda intermitente, elegir entre 9 modelos por producto
sobreajusta el backtesting. En su lugar se evalúan los modelos para cada clase de
demanda XYZ (X/Y/Z) y se aplica una política única por clase. Es más estable,
explicable y auditable; los resultados se validan dejando un origen fuera.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import pandas as pd

from utils.config import Config

EPS = 1e-9
FALLBACK_ORDER = ["naive", "ma4", "ma8", "ses", "holt", "seasonal_naive", "croston", "tsb", "ensemble", "ensemble_dinamico"]
COMMON_MODELS = ["naive", "ma4", "ma8", "ses", "holt", "seasonal_naive", "croston", "tsb", "ensemble", "ensemble_dinamico"]

MODEL_LABELS = {
    "naive": "Naive (última semana)",
    "ma4": "Media móvil 4 semanas",
    "ma8": "Media móvil 8 semanas",
    "ses": "Suavizado exponencial simple",
    "holt": "Holt amortiguado",
    "seasonal_naive": "Naive estacional (52 semanas)",
    "croston": "Croston-SBA",
    "tsb": "TSB (probabilidad × tamaño)",
    "ensemble": "Ensemble (mediana de modelos)",
    "ensemble_dinamico": "Ensemble dinámico (ponderado por error reciente)",
    "promedio": "Promedio histórico",
    "sin_demanda": "Sin demanda reciente",
}


# --------------------------------------------------------------------------- modelos
def _nonneg(values: np.ndarray) -> np.ndarray:
    return np.maximum(np.asarray(values, dtype=float), 0.0)


def fit_naive(y: np.ndarray, h: int) -> np.ndarray:
    return np.full(h, y[-1] if len(y) else 0.0)


def fit_ma(y: np.ndarray, h: int, window: int = 4) -> np.ndarray:
    n = min(window, len(y))
    return np.full(h, float(np.mean(y[-n:])) if n else 0.0)


def _ses_level(y: np.ndarray, alpha: float) -> float:
    level = float(y[0])
    for value in y[1:]:
        level = alpha * float(value) + (1.0 - alpha) * level
    return level


def _best_alpha(y: np.ndarray) -> float:
    best_alpha, best_sse = 0.3, np.inf
    for alpha in np.arange(0.05, 0.96, 0.05):
        level = float(y[0])
        sse = 0.0
        for value in y[1:]:
            sse += (float(value) - level) ** 2
            level = alpha * float(value) + (1.0 - alpha) * level
        if sse < best_sse:
            best_sse, best_alpha = sse, float(alpha)
    return best_alpha


def fit_ses(y: np.ndarray, h: int, alpha: float | None = None) -> np.ndarray:
    if len(y) == 0:
        return np.zeros(h)
    if alpha is None:
        alpha = _best_alpha(y)
    return np.full(h, max(_ses_level(y, alpha), 0.0))


def fit_holt(y: np.ndarray, h: int, alpha: float = 0.30, beta: float = 0.10, phi: float = 0.90) -> np.ndarray:
    if len(y) == 0:
        return np.zeros(h)
    level = float(y[0])
    trend = float(y[1] - y[0]) if len(y) > 1 else 0.0
    for value in y[1:]:
        previous = level
        level = alpha * float(value) + (1.0 - alpha) * (level + phi * trend)
        trend = beta * (level - previous) + (1.0 - beta) * phi * trend
    out = []
    accumulator = 0.0
    for step in range(1, h + 1):
        accumulator += phi ** step
        out.append(level + accumulator * trend)
    return _nonneg(np.array(out))


def fit_seasonal_naive(y: np.ndarray, h: int, lag: int = 52) -> np.ndarray:
    if len(y) < lag + 2:
        return fit_ma(y, h, 8)
    seasonal = y[-lag:]
    return _nonneg(np.array([seasonal[k % lag] for k in range(h)], dtype=float))


def fit_croston(y: np.ndarray, h: int, alpha: float = 0.10, sba: bool = True) -> np.ndarray:
    nonzero = np.flatnonzero(y > 0)
    if len(nonzero) == 0:
        return np.zeros(h)
    size = float(y[nonzero[0]])
    interval = float(nonzero[0] + 1)
    last = int(nonzero[0])
    for index in range(nonzero[0] + 1, len(y)):
        if y[index] > 0:
            size = size + alpha * (float(y[index]) - size)
            interval = interval + alpha * ((index - last) - interval)
            last = index
    rate = size / max(interval, EPS)
    if sba:
        rate *= (1.0 - alpha / 2.0)
    return np.full(h, max(rate, 0.0))


def fit_tsb(y: np.ndarray, h: int, alpha: float = 0.20, beta: float = 0.20) -> np.ndarray:
    """Teunter-Syntetos-Babai: probabilidad de demanda × tamaño de demanda."""
    if len(y) == 0:
        return np.zeros(h)
    nonzero = y[y > 0]
    size = float(nonzero.mean()) if len(nonzero) else 0.0
    probability = float((y > 0).mean())
    for value in y:
        if value > 0:
            probability = probability + beta * (1.0 - probability)
            size = size + alpha * (float(value) - size)
        else:
            probability = probability + beta * (0.0 - probability)
    return np.full(h, max(probability * size, 0.0))


def fit_ensemble(y: np.ndarray, h: int, predictions: dict[str, np.ndarray] | None = None) -> np.ndarray:
    if predictions:
        values = [p for name, p in predictions.items() if name != "ensemble"]
    else:
        values = [fit_ma(y, h, 4), fit_ses(y, h), fit_croston(y, h), fit_tsb(y, h)]
    if not values:
        return fit_ma(y, h, 4)
    return np.median(np.vstack([_nonneg(v) for v in values]), axis=0)


# --------------------------------------------------------------------------- utilidades
def build_weekly_panel(sales: pd.DataFrame, as_of: pd.Timestamp) -> tuple[pd.DataFrame, pd.Timestamp]:
    """Matriz EAN × semana (domingo), rellenando con cero las semanas sin venta."""
    days_since_sunday = (as_of.weekday() + 1) % 7
    last_sunday = as_of - pd.Timedelta(days=days_since_sunday)
    if sales.empty:
        return pd.DataFrame(), last_sunday
    first = sales["Fecha"].min()
    if last_sunday < first:
        last_sunday = as_of
    window = sales[(sales["Fecha"] >= first) & (sales["Fecha"] <= last_sunday)].copy()
    if window.empty:
        return pd.DataFrame(), last_sunday
    window["Semana"] = window["Fecha"].dt.to_period("W-SUN")
    pivot = window.pivot_table(index="EAN", columns="Semana", values="Cantidad",
                               aggfunc="sum", fill_value=0)
    weeks = pd.period_range(window["Semana"].min(), last_sunday, freq="W-SUN")
    pivot = pivot.reindex(columns=weeks, fill_value=0)
    pivot.columns = [str(c) for c in pivot.columns]
    return pivot, last_sunday


def trim_leading_zeros(y: np.ndarray) -> np.ndarray:
    nonzero = np.flatnonzero(y > 0)
    if len(nonzero) == 0:
        return y
    return y[nonzero[0]:]


def candidate_models(y: np.ndarray) -> list[str]:
    """Candidatos comunes para que la comparación entre modelos sea justa."""
    return list(COMMON_MODELS)


def predict_all(y: np.ndarray, h: int, candidates: Iterable[str], season_length: int = 52) -> dict[str, np.ndarray]:
    predictions: dict[str, np.ndarray] = {}
    for name in candidates:
        if name == "ensemble":
            continue
        if name == "naive":
            predictions[name] = fit_naive(y, h)
        elif name == "ma4":
            predictions[name] = fit_ma(y, h, 4)
        elif name == "ma8":
            predictions[name] = fit_ma(y, h, 8)
        elif name == "ses":
            predictions[name] = fit_ses(y, h)
        elif name == "holt":
            predictions[name] = fit_holt(y, h)
        elif name == "seasonal_naive":
            predictions[name] = fit_seasonal_naive(y, h, season_length)
        elif name == "croston":
            predictions[name] = fit_croston(y, h)
        elif name == "tsb":
            predictions[name] = fit_tsb(y, h)
    if "ensemble" in candidates:
        predictions["ensemble"] = fit_ensemble(y, h, predictions)
    for name in predictions:
        predictions[name] = _nonneg(predictions[name])
    return predictions


# --------------------------------------------------------------------------- backtest
def _dynamic_ensemble(predictions: dict[str, np.ndarray],
                      history_errors: dict[str, list[float]],
                      history_actuals: list[float],
                      temperature: float = 25.0) -> np.ndarray:
    """Promedio ponderado por desempeño reciente de cada modelo (online)."""
    base = {k: v for k, v in predictions.items() if k != "ensemble_dinamico"}
    if not base:
        return np.zeros(1)
    if not history_actuals:
        return np.mean(np.vstack(list(base.values())), axis=0)
    total_actual = float(sum(history_actuals))
    weights: dict[str, float] = {}
    for name in base:
        errors = history_errors.get(name, [])
        if not errors or total_actual <= 0:
            weights[name] = 1.0
        else:
            wape = sum(abs(e) for e in errors) / total_actual * 100.0
            weights[name] = float(np.exp(-wape / temperature))
    weight_sum = sum(weights.values()) or 1.0
    stack = np.vstack([base[name] for name in base])
    weights_array = np.array([weights[name] for name in base]) / weight_sum
    return (stack * weights_array[:, None]).sum(axis=0)


@dataclass
class BacktestResult:
    scores: dict[str, float]
    mape: dict[str, float]
    bias: dict[str, float]
    hit25: dict[str, float]
    errors: dict[str, list[float]]
    actuals: list[float]
    actual_sum: float
    origins: int


def rolling_backtest(y: np.ndarray, candidates: list[str], horizon_weeks: int = 4,
                     origins: int = 6, min_train_weeks: int = 20,
                     season_length: int = 52) -> BacktestResult | None:
    """Backtesting con orígenes mensuales móviles.

    Para cada origen se entrena con la historia disponible y se pronostican 4
    semanas. La métrica de selección es el WAPE agregado del producto:
    ``sum(|pronóstico - real|) / sum(real)``. Así los meses sin venta (posible
    quiebre) no distorsionan la comparación como lo haría un MAPE clásico; los
    orígenes con venta cero se omiten.
    """
    n = len(y)
    if n < min_train_weeks + horizon_weeks:
        return None

    errors: dict[str, list[float]] = {m: [] for m in candidates}
    actuals: list[float] = []
    used_origins = 0
    history_errors: dict[str, list[float]] = {m: [] for m in candidates if m != "ensemble_dinamico"}
    history_actuals: list[float] = []

    for k in range(origins, 0, -1):
        cutoff = n - k * horizon_weeks
        if cutoff < min_train_weeks:
            continue
        train = y[:cutoff]
        actual = y[cutoff:cutoff + horizon_weeks]
        actual_total = float(actual.sum())
        used_origins += 1
        actuals.append(actual_total)
        predictions = predict_all(train, horizon_weeks, candidates, season_length)
        if "ensemble_dinamico" in candidates:
            predictions["ensemble_dinamico"] = _dynamic_ensemble(predictions, history_errors, history_actuals)
        for name, path in predictions.items():
            errors[name].append(float(path.sum()) - actual_total)
        for name in history_errors:
            if name in predictions:
                history_errors[name].append(float(predictions[name].sum()) - actual_total)
        history_actuals.append(actual_total)

    if used_origins == 0:
        return None

    total_actual = float(sum(actuals))
    scores, mapes, biases, hits = {}, {}, {}, {}
    for name in candidates:
        abs_sum = float(sum(abs(e) for e in errors[name]))
        err_sum = float(sum(errors[name]))
        scores[name] = abs_sum / total_actual * 100.0 if total_actual else np.nan
        biases[name] = err_sum / total_actual * 100.0 if total_actual else np.nan
        per_origin = [abs(e) / a * 100.0 for e, a in zip(errors[name], actuals) if a > 0]
        mapes[name] = float(np.mean(per_origin)) if per_origin else np.nan
        hits[name] = float(np.mean([abs(e) / a <= 0.25 for e, a in zip(errors[name], actuals) if a > 0])) * 100.0 if any(a > 0 for a in actuals) else np.nan
    return BacktestResult(scores=scores, mape=mapes, bias=biases, hit25=hits,
                          errors=errors, actuals=actuals, actual_sum=total_actual,
                          origins=used_origins)


# --------------------------------------------------------------------------- análisis
@dataclass
class SeriesAnalysis:
    y: np.ndarray
    candidates: list[str]
    backtest: BacktestResult | None
    short_history: bool
    weeks: int
    nonzero_weeks: int


@dataclass
class ProductForecast:
    ean: str
    weekly_path: np.ndarray
    model: str
    horizon_days: int
    wape: float
    mape: float
    bias: float
    hit25: float
    sigma_28: float
    confidence: float
    weeks_history: int
    last_sale: pd.Timestamp | None
    status: str
    error_abs_sum: float = 0.0
    actual_sum: float = 0.0
    origins: int = 0
    candidates: dict[str, float] = field(default_factory=dict)
    censoring_risk: bool = False
    gap_days: int = 0
    lead_time: int = 30
    wape_naive: float = np.nan
    mejora_vs_naive: float = np.nan
    pattern: str = ""

    @property
    def model_label(self) -> str:
        return MODEL_LABELS.get(self.model, self.model)

    def units_for_days(self, days: int) -> float:
        if days <= 0 or len(self.weekly_path) == 0:
            return 0.0
        full_weeks, remainder = divmod(int(days), 7)
        path = self.weekly_path
        if full_weeks <= len(path):
            total = float(path[:full_weeks].sum())
            index = full_weeks
        else:
            total = float(path.sum()) + float(path[-4:].mean()) * (full_weeks - len(path))
            index = len(path) - 1
        total += float(path[min(index, len(path) - 1)]) * (remainder / 7.0)
        return max(total, 0.0)

    def daily_for_days(self, days: int) -> float:
        return self.units_for_days(days) / max(days, 1)

    @property
    def forecast_30d(self) -> float:
        return self.units_for_days(30)

    @property
    def forecast_daily(self) -> float:
        return self.daily_for_days(self.horizon_days)


class ForecastEngine:
    """Analiza series, ejecuta backtesting y construye pronósticos bajo una política."""

    def __init__(self, config: Config, logger: logging.Logger | None = None):
        self.config = config
        self.logger = logger or logging.getLogger("compras")
        self.horizon_days = int(config.get("forecasting.horizon_days", 30))
        self.backtest_months = int(config.get("forecasting.backtest_months", 6))
        self.min_train_weeks = int(config.get("forecasting.min_train_weeks", 20))
        self.season_length = int(config.get("forecasting.season_length_weeks", 52))
        self.min_sales_events = int(config.get("forecasting.min_sales_events", 3))

    # ------------------------------------------------------------------ análisis
    def analyze_series(self, y: np.ndarray) -> SeriesAnalysis:
        y = trim_leading_zeros(np.asarray(y, dtype=float))
        weeks = len(y)
        nonzero = int(np.count_nonzero(y))
        if weeks < self.min_train_weeks or nonzero < self.min_sales_events:
            return SeriesAnalysis(y, [], None, True, weeks, nonzero)
        candidates = candidate_models(y)
        backtest = rolling_backtest(
            y, candidates, horizon_weeks=4, origins=self.backtest_months,
            min_train_weeks=self.min_train_weeks, season_length=self.season_length,
        )
        return SeriesAnalysis(y, candidates, backtest, backtest is None, weeks, nonzero)

    # ------------------------------------------------------------------ construcción
    def build_forecast(self, ean: str, analysis: SeriesAnalysis, model: str, max_weeks: int,
                       last_sale: pd.Timestamp | None = None, status: str = "ACTIVO",
                       lead_time: int = 30, pattern: str = "") -> ProductForecast:
        y = analysis.y
        n = len(y)

        if n == 0 or np.count_nonzero(y) == 0:
            return ProductForecast(ean, np.zeros(max_weeks), "sin_demanda", self.horizon_days,
                                   np.nan, np.nan, np.nan, np.nan, 0.0, 0.0, n,
                                   last_sale, status, 0.0, 0.0, 0, {}, False, 0, lead_time, pattern=pattern)

        if analysis.backtest is None:
            level = float(np.mean(y[-8:])) if n else 0.0
            path = np.full(max_weeks, max(level, 0.0))
            return ProductForecast(ean, path, "promedio", self.horizon_days, np.nan, np.nan, np.nan, np.nan,
                                   0.30 * level, 0.20, n, last_sale, status, 0.0, float(y.sum()),
                                   0, {}, False, 0, lead_time, pattern=pattern)

        if model not in analysis.candidates:
            model = "ensemble"
        predictions = predict_all(y, max_weeks, analysis.candidates, self.season_length)
        if model == "ensemble_dinamico":
            base = {k: v for k, v in predictions.items() if k != "ensemble_dinamico"}
            path = _dynamic_ensemble(base, analysis.backtest.errors, analysis.backtest.actuals)
        else:
            path = predictions.get(model, predictions["ensemble"])
        path = _nonneg(path)
        backtest = analysis.backtest

        errors = np.array(backtest.errors[model], dtype=float)
        actual_sum = backtest.actual_sum
        abs_error = float(np.abs(errors).sum())
        wape = abs_error / actual_sum * 100.0 if actual_sum > 0 else np.nan
        bias = float(errors.sum()) / actual_sum * 100.0 if actual_sum > 0 else np.nan
        per_origin = [abs(e) / a * 100.0 for e, a in zip(errors, backtest.actuals) if a > 0]
        mape = float(np.mean(per_origin)) if per_origin else np.nan
        hit25 = float(np.mean([abs(e) / a <= 0.25 for e, a in zip(errors, backtest.actuals) if a > 0])) * 100.0 if per_origin else np.nan
        sigma = float(np.sqrt(np.mean(errors ** 2))) if len(errors) else 0.0
        confidence = float(np.clip(1.0 - wape / 100.0, 0.05, 0.95)) if not np.isnan(wape) else 0.35
        confidence *= float(min(1.0, n / 52.0))
        confidence = float(np.clip(confidence, 0.05, 0.95))

        wape_naive = backtest.scores.get("naive", np.nan)
        if wape_naive and not np.isnan(wape_naive) and not np.isnan(wape):
            mejora = (wape_naive - wape) / wape_naive * 100.0
        else:
            mejora = np.nan

        return ProductForecast(
            ean=ean,
            weekly_path=path,
            model=model,
            horizon_days=self.horizon_days,
            wape=float(wape) if not np.isnan(wape) else np.nan,
            mape=float(mape) if not np.isnan(mape) else np.nan,
            bias=float(bias) if not np.isnan(bias) else np.nan,
            hit25=float(hit25),
            sigma_28=sigma,
            confidence=confidence,
            weeks_history=n,
            last_sale=last_sale,
            status=status,
            error_abs_sum=abs_error,
            actual_sum=actual_sum,
            origins=backtest.origins,
            candidates={k: round(v, 1) for k, v in backtest.scores.items()},
            lead_time=lead_time,
            wape_naive=float(wape_naive) if not np.isnan(wape_naive) else np.nan,
            mejora_vs_naive=float(mejora) if not np.isnan(mejora) else np.nan,
            pattern=pattern,
        )

    # ------------------------------------------------------------------ compatibilidad
    def forecast(self, ean: str, y: np.ndarray, max_weeks: int,
                 last_sale: pd.Timestamp | None = None, status: str = "ACTIVO",
                 lead_time: int = 30) -> ProductForecast:
        analysis = self.analyze_series(y)
        model = "promedio" if analysis.backtest is None else _best_model(analysis.backtest)
        return self.build_forecast(ean, analysis, model, max_weeks, last_sale, status, lead_time)


def _best_model(backtest: BacktestResult) -> str:
    order = {name: idx for idx, name in enumerate(FALLBACK_ORDER)}
    best = min(backtest.scores.items(),
               key=lambda kv: (round(kv[1], 6), abs(backtest.bias.get(kv[0], 0.0)), order.get(kv[0], 99)))
    return best[0]


# --------------------------------------------------------------------------- políticas
def aggregate_wape(analyses: list[SeriesAnalysis], model: str,
                   exclude_origin: int | None = None) -> tuple[float, float, int, float]:
    """Agrega error absoluto, venta real, observaciones y error con signo."""
    abs_sum = 0.0
    actual_sum = 0.0
    signed_sum = 0.0
    observations = 0
    for analysis in analyses:
        bt = analysis.backtest
        if bt is None or model not in bt.errors:
            continue
        for index, error in enumerate(bt.errors[model]):
            if exclude_origin is not None and index == exclude_origin:
                continue
            abs_sum += abs(error)
            signed_sum += error
            actual_sum += bt.actuals[index]
            observations += 1
    return abs_sum, actual_sum, observations, signed_sum


def _policy_score(abs_sum: float, actual_sum: float, signed_sum: float) -> tuple[float, float, float]:
    """WAPE penalizado por sesgo: evita políticas que subestiman la demanda."""
    if actual_sum <= 0:
        return np.inf, np.nan, np.nan
    wape = abs_sum / actual_sum * 100.0
    bias = signed_sum / actual_sum * 100.0
    score = wape + 0.5 * abs(bias)
    if abs(bias) > 60.0:
        score += 50.0  # penalización fuerte a políticas muy sesgadas
    return score, wape, bias


def select_policy(groups: dict[str, list[SeriesAnalysis]],
                  models: list[str] | None = None) -> dict[str, dict[str, float]]:
    """Selecciona el modelo de menor WAPE ajustado por sesgo para cada grupo XYZ."""
    models = models or COMMON_MODELS
    policy: dict[str, dict[str, float]] = {}
    for pattern, analyses in groups.items():
        if not analyses:
            continue
        best = {"modelo": "ma4", "score": np.inf, "wape": np.nan, "sesgo": np.nan,
                "venta_real": 0.0, "error_abs": 0.0}
        for model in models:
            abs_sum, actual_sum, observations, signed_sum = aggregate_wape(analyses, model)
            score, wape, bias = _policy_score(abs_sum, actual_sum, signed_sum)
            if observations == 0 or not np.isfinite(score):
                continue
            order = FALLBACK_ORDER.index(model) if model in FALLBACK_ORDER else 99
            best_order = FALLBACK_ORDER.index(best["modelo"]) if best["modelo"] in FALLBACK_ORDER else 99
            if (round(score, 6), order) < (round(best["score"], 6), best_order):
                best = {"modelo": model, "score": score, "wape": wape, "sesgo": bias,
                        "venta_real": actual_sum, "error_abs": abs_sum}
        best["productos"] = len(analyses)
        policy[pattern] = best
    return policy


def evaluate_policy_cv(groups: dict[str, list[SeriesAnalysis]],
                       max_origins: int = 6) -> dict[str, float]:
    """Evalúa la política dejando un origen fuera en cada fold.

    En cada fold se elige el modelo por grupo con los orígenes restantes y se
    mide sobre el origen excluido. Devuelve métricas agregadas honestas.
    """
    total_abs = total_actual = 0.0
    per_pattern: dict[str, list[float]] = {}
    for fold in range(max_origins):
        fold_policy: dict[str, str] = {}
        for pattern, analyses in groups.items():
            best_model, best_score = "ma4", np.inf
            for model in COMMON_MODELS:
                abs_sum, actual_sum, obs, signed_sum = aggregate_wape(analyses, model, exclude_origin=fold)
                score, _, _ = _policy_score(abs_sum, actual_sum, signed_sum)
                if obs == 0 or not np.isfinite(score):
                    continue
                if score < best_score:
                    best_model, best_score = model, score
            fold_policy[pattern] = best_model
        for pattern, analyses in groups.items():
            model = fold_policy.get(pattern, "ma4")
            for analysis in analyses:
                bt = analysis.backtest
                if bt is None or model not in bt.errors or fold >= len(bt.actuals):
                    continue
                error = abs(bt.errors[model][fold])
                actual = bt.actuals[fold]
                total_abs += error
                total_actual += actual
                bucket = per_pattern.setdefault(pattern, [0.0, 0.0])
                bucket[0] += error
                bucket[1] += actual
    result = {
        "wape": total_abs / total_actual * 100.0 if total_actual else np.nan,
        "venta_real": total_actual,
        "error_abs": total_abs,
    }
    for pattern, (error, actual) in per_pattern.items():
        result[f"wape_{pattern}"] = error / actual * 100.0 if actual else np.nan
    return result
