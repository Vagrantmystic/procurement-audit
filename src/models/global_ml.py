"""Modelo global de demanda (ML + red neuronal) sobre datos de Profit.

Enfoque:
* panel semanal producto × semana,
* features de lags, medias móviles, estacionalidad, intermitencia y atributos del producto,
* objetivo: demanda de las próximas 4 semanas (≈30 días),
* validación temporal: últimos 6 meses,
* comparación contra baselines (media móvil 4 y 8 semanas).

El modelo es **global**: aprende patrones entre productos, no una serie aislada.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler

from data_processing.profit_loader import ProfitData

FEATURES = [
    "lag1", "lag2", "lag4", "lag8", "lag13", "lag26",
    "mean4", "mean8", "mean13", "mean26", "std8", "zero13", "trend",
    "zero_run", "week_sin", "week_cos", "week_index", "month",
    "prod_mean", "prod_std", "prod_cv", "prod_zero", "price", "line_code", "cat_code",
]


@dataclass
class GlobalMLResult:
    metrics: dict[str, Any]
    by_volume: pd.DataFrame
    by_product: pd.DataFrame
    feature_importance: pd.DataFrame
    predictions: pd.DataFrame
    elapsed: float


def _wape(actual: np.ndarray, pred: np.ndarray) -> float:
    total = np.sum(np.abs(actual))
    return float(np.sum(np.abs(pred - actual)) / total * 100) if total > 0 else np.nan


def _metrics(actual: np.ndarray, pred: np.ndarray) -> dict[str, float]:
    mask = actual > 0
    return {
        "wape": _wape(actual, pred),
        "bias": float(np.sum(pred - actual) / np.sum(actual) * 100) if np.sum(actual) > 0 else np.nan,
        "hit25": float(np.mean(np.abs(pred[mask] - actual[mask]) / actual[mask] <= 0.25) * 100) if mask.any() else np.nan,
        "mae": float(mean_absolute_error(actual, pred)),
    }


def build_dataset(profit: ProfitData, as_of: pd.Timestamp,
                  cutoff_weeks: int = 26) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Timestamp]:
    sales = profit.sales[profit.sales["TipoArticulo"].isin(["V", "C"])].copy()
    sales = sales[sales["Fecha"] <= as_of]
    sales["Semana"] = sales["Fecha"].dt.to_period("W-SUN")
    panel = sales.pivot_table(index="EAN", columns="Semana", values="Cantidad",
                              aggfunc="sum", fill_value=0)
    weeks = pd.period_range(panel.columns.min(), panel.columns.max(), freq="W-SUN")
    panel = panel.reindex(columns=weeks, fill_value=0)
    # Excluir la última semana incompleta
    panel = panel.iloc[:, :-1]
    weeks = weeks[:-1]
    n_products, n_weeks = panel.shape
    cutoff = max(20, n_weeks - cutoff_weeks)

    values = panel.to_numpy(dtype=float)

    # Objetivo: suma de las próximas 4 semanas
    frame = pd.DataFrame(values, index=panel.index, columns=panel.columns)
    target = frame.T.rolling(4).sum().T.shift(-4, axis=1)

    feat: dict[str, np.ndarray] = {}
    for lag in (1, 2, 4, 8, 13, 26):
        feat[f"lag{lag}"] = np.roll(values, lag, axis=1)
        feat[f"lag{lag}"][:, :lag] = 0
    for window in (4, 8, 13, 26):
        feat[f"mean{window}"] = frame.T.rolling(window).mean().T.shift(1, axis=1).to_numpy()
    feat["std8"] = frame.T.rolling(8).std().T.shift(1, axis=1).to_numpy()
    feat["zero13"] = (frame == 0).T.rolling(13).mean().T.shift(1, axis=1).to_numpy()
    feat["trend"] = feat["mean4"] / np.where(feat["mean13"] > 0, feat["mean13"], np.nan) - 1

    zero_run = np.zeros_like(values)
    for j in range(1, n_weeks):
        zero_run[:, j] = np.where(values[:, j - 1] == 0, zero_run[:, j - 1] + 1, 0)
    feat["zero_run"] = zero_run

    week_of_year = np.array([w.week for w in weeks], dtype=float)
    month = np.array([w.end_time.month for w in weeks], dtype=float)
    feat["week_sin"] = np.tile(np.sin(2 * np.pi * week_of_year / 52.0), (n_products, 1))
    feat["week_cos"] = np.tile(np.cos(2 * np.pi * week_of_year / 52.0), (n_products, 1))
    feat["week_index"] = np.tile(np.arange(n_weeks, dtype=float), (n_products, 1))
    feat["month"] = np.tile(month, (n_products, 1))

    train_panel = values[:, :cutoff]
    prod_mean = train_panel.mean(axis=1)
    prod_std = train_panel.std(axis=1)
    prod_zero = (train_panel == 0).mean(axis=1)
    prod_cv = np.divide(prod_std, prod_mean, out=np.zeros_like(prod_std), where=prod_mean > 0)
    feat["prod_mean"] = np.repeat(prod_mean[:, None], n_weeks, axis=1)
    feat["prod_std"] = np.repeat(prod_std[:, None], n_weeks, axis=1)
    feat["prod_cv"] = np.repeat(prod_cv[:, None], n_weeks, axis=1)
    feat["prod_zero"] = np.repeat(prod_zero[:, None], n_weeks, axis=1)

    products = profit.products.set_index("EAN")
    price = (sales.groupby("EAN").apply(
        lambda x: x["Importe_USD"].sum() / x["Cantidad"].sum() if x["Cantidad"].sum() > 0 else np.nan,
        include_groups=False).reindex(panel.index).fillna(0.0))
    line_codes = pd.Categorical(products.reindex(panel.index)["CodLinea"].fillna("NA")).codes
    cat_codes = pd.Categorical(products.reindex(panel.index)["CodCategoria"].fillna("NA")).codes
    feat["price"] = np.repeat(price.to_numpy()[:, None], n_weeks, axis=1)
    feat["line_code"] = np.repeat(line_codes[:, None], n_weeks, axis=1)
    feat["cat_code"] = np.repeat(cat_codes[:, None], n_weeks, axis=1)

    # Aplanar
    rows = {name: feat[name].ravel() for name in FEATURES}
    X = pd.DataFrame(rows)
    y = target.to_numpy(dtype=float).ravel()
    week_idx = np.tile(np.arange(n_weeks), n_products)
    ean = np.repeat(panel.index.to_numpy(), n_weeks)
    valid = (~np.isnan(y)) & (week_idx >= 13)
    X = X.loc[valid].reset_index(drop=True)
    y_series = pd.Series(y[valid], name="y")
    meta = pd.DataFrame({"EAN": ean[valid], "Semana": week_idx[valid]})
    X = X.fillna(0.0).replace([np.inf, -np.inf], 0.0)
    return X, y_series, meta, weeks[cutoff]


def run_experiment(profit: ProfitData, logger: logging.Logger | None = None,
                   cutoff_weeks: int = 26) -> GlobalMLResult:
    logger = logger or logging.getLogger("compras")
    started = time.time()
    X, y, meta, cutoff_date = build_dataset(profit, profit.as_of, cutoff_weeks=cutoff_weeks)
    n_weeks_total = int(meta["Semana"].max()) + 1
    cutoff = n_weeks_total - cutoff_weeks
    train_mask = meta["Semana"] < cutoff - 4
    test_mask = ~train_mask
    # El objetivo de las últimas 4 semanas no está completo
    X_train, y_train = X[train_mask], y[train_mask]
    X_test, y_test = X[test_mask], y[test_mask]
    logger.info("ML: %s muestras train | %s test | %s features",
                f"{len(X_train):,}", f"{len(X_test):,}", X.shape[1])

    # Baselines
    pred_ma4 = X_test["mean4"].to_numpy() * 4
    pred_ma8 = X_test["mean8"].to_numpy() * 4
    baseline = {
        "MA4 × 4": _metrics(y_test.to_numpy(), pred_ma4),
        "MA8 × 4": _metrics(y_test.to_numpy(), pred_ma8),
    }

    # ML: gradient boosting
    gbr = HistGradientBoostingRegressor(
        max_iter=250, learning_rate=0.06, max_depth=6,
        l2_regularization=1.0, min_samples_leaf=30, random_state=42,
    )
    t0 = time.time()
    gbr.fit(X_train, y_train)
    pred_gbr = np.maximum(gbr.predict(X_test), 0)
    logger.info("ML: HistGradientBoosting entrenado en %.1f s", time.time() - t0)

    # DL: red neuronal (MLP) sobre una muestra para acotar tiempo
    sample_n = min(120_000, len(X_train))
    rng = np.random.default_rng(42)
    idx = rng.choice(len(X_train), size=sample_n, replace=False) if sample_n < len(X_train) else np.arange(len(X_train))
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train.iloc[idx])
    X_test_scaled = scaler.transform(X_test)
    mlp = MLPRegressor(
        hidden_layer_sizes=(64, 32), activation="relu", solver="adam",
        batch_size=512, learning_rate_init=0.001, max_iter=60,
        early_stopping=True, n_iter_no_change=6, validation_fraction=0.12,
        random_state=42,
    )
    t0 = time.time()
    mlp.fit(X_train_scaled, y_train.iloc[idx])
    pred_train = np.maximum(mlp.predict(X_train_scaled), 0)
    calibrate = float(y_train.iloc[idx].sum() / pred_train.sum()) if pred_train.sum() > 0 else 1.0
    pred_mlp = np.maximum(mlp.predict(X_test_scaled), 0) * calibrate
    logger.info("ML: MLP entrenado en %.1f s (%s muestras, calibración %.2f)",
                time.time() - t0, f"{sample_n:,}", calibrate)

    metrics = {
        "MA4 × 4": baseline["MA4 × 4"],
        "MA8 × 4": baseline["MA8 × 4"],
        "HistGradientBoosting": _metrics(y_test.to_numpy(), pred_gbr),
        "MLP (red neuronal)": _metrics(y_test.to_numpy(), pred_mlp),
    }
    # Híbrido: MLP para volumen medio/alto, MA8 para cola de baja rotación
    volume_flag = X_test["prod_mean"].to_numpy() * 4 > 10
    pred_hybrid = np.where(volume_flag, pred_mlp, pred_ma8)
    metrics["Híbrido (MLP + MA8)"] = _metrics(y_test.to_numpy(), pred_hybrid)
    best_name = min(metrics, key=lambda k: metrics[k]["wape"] if not np.isnan(metrics[k]["wape"]) else 9e9)
    metrics["mejor"] = best_name

    # Por producto (ML)
    test_df = meta[test_mask].copy()
    test_df["y"] = y_test.to_numpy()
    test_df["pred_gbr"] = pred_gbr
    test_df["pred_mlp"] = pred_mlp
    test_df["pred_ma4"] = pred_ma4
    test_df["pred_ma8"] = pred_ma8
    test_df["pred_hybrid"] = pred_hybrid
    by_product = []
    for ean, part in test_df.groupby("EAN"):
        if part["y"].sum() <= 0:
            continue
        by_product.append({
            "EAN": ean,
            "Actual": part["y"].sum(),
            "Pred_ML": part["pred_gbr"].sum(),
            "Pred_DL": part["pred_mlp"].sum(),
            "Pred_MA4": part["pred_ma4"].sum(),
            "Pred_MA8": part["pred_ma8"].sum(),
            "WAPE_ML": _wape(part["y"].to_numpy(), part["pred_gbr"].to_numpy()),
            "WAPE_DL": _wape(part["y"].to_numpy(), part["pred_mlp"].to_numpy()),
            "WAPE_MA4": _wape(part["y"].to_numpy(), part["pred_ma4"].to_numpy()),
            "WAPE_MA8": _wape(part["y"].to_numpy(), part["pred_ma8"].to_numpy()),
            "WAPE_Hibrido": _wape(part["y"].to_numpy(), part["pred_hybrid"].to_numpy()),
        })
    by_product = pd.DataFrame(by_product)

    # Por volumen
    if not by_product.empty:
        by_product["Volumen"] = by_product["Actual"] / 26.0
        bins = [-1, 10, 25, 50, 100, 250, np.inf]
        labels = ["< 10", "10-25", "25-50", "50-100", "100-250", "> 250"]
        by_product["Tramo"] = pd.cut(by_product["Volumen"], bins=bins, labels=labels)
        by_volume = by_product.groupby("Tramo", observed=True).agg(
            Productos=("EAN", "count"),
            WAPE_ML=("WAPE_ML", "median"),
            WAPE_DL=("WAPE_DL", "median"),
            WAPE_MA4=("WAPE_MA4", "median"),
            WAPE_MA8=("WAPE_MA8", "median"),
            WAPE_Hibrido=("WAPE_Hibrido", "median"),
        ).reset_index()
    else:
        by_volume = pd.DataFrame()

    importance = pd.DataFrame({"Feature": FEATURES, "Importancia": np.zeros(len(FEATURES))})
    if hasattr(gbr, "feature_importances_"):
        importance["Importancia"] = gbr.feature_importances_
    else:
        sample_idx = rng.choice(len(X_test), size=min(4000, len(X_test)), replace=False)
        try:
            perm = permutation_importance(
                gbr, X_test.iloc[sample_idx], y_test.iloc[sample_idx],
                n_repeats=3, random_state=42, scoring="neg_mean_absolute_error")
            importance["Importancia"] = perm.importances_mean
        except Exception:
            importance["Importancia"] = np.nan

    return GlobalMLResult(
        metrics=metrics,
        by_volume=by_volume,
        by_product=by_product,
        feature_importance=importance.sort_values("Importancia", ascending=False),
        predictions=test_df,
        elapsed=time.time() - started,
    )
