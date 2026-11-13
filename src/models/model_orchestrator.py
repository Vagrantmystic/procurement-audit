"""Orquestador de pronósticos: series, backtesting, política por patrón y consolidación."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from models.forecast_engine import (
    ForecastEngine,
    MODEL_LABELS,
    ProductForecast,
    SeriesAnalysis,
    build_weekly_panel,
    evaluate_policy_cv,
    select_policy,
)
from utils.config import Config


@dataclass
class ForecastBundle:
    forecasts: dict[str, ProductForecast]
    validation: pd.DataFrame
    model_summary: pd.DataFrame
    segment_summary: pd.DataFrame
    abc_summary: pd.DataFrame
    policy_table: pd.DataFrame
    metrics: dict[str, Any]
    forecast_start: pd.Timestamp
    max_weeks: int
    panel_weeks: int


class ModelOrchestrator:
    def __init__(self, config: Config, logger: logging.Logger | None = None):
        self.config = config
        self.logger = logger or logging.getLogger("compras")
        self.engine = ForecastEngine(config, self.logger)

    # ------------------------------------------------------------------ público
    def run(self, sales: pd.DataFrame, products: pd.DataFrame,
            segmentation: pd.DataFrame, as_of: pd.Timestamp) -> ForecastBundle:
        panel, forecast_start = build_weekly_panel(sales, as_of)
        if panel.empty:
            raise ValueError("No se pudo construir la serie semanal de ventas")

        max_coverage = int(self.config.get("inventory.max_coverage_days", 120))
        max_lead = int(products["Lead_Time"].fillna(30).max()) if "Lead_Time" in products.columns else 30
        max_weeks = max(8, math.ceil(max(max_coverage, max_lead + 7) / 7) + 2)

        product_map = products.set_index("EAN", drop=False)
        segment_map = segmentation.set_index("EAN")

        self.logger.info("Analizando %s productos con %s semanas de historia (corte %s)",
                         f"{len(panel.index):,}", panel.shape[1], f"{forecast_start:%d/%m/%Y}")

        # ---------------- Paso 1: analizar cada serie
        analyses: dict[str, SeriesAnalysis] = {}
        meta: dict[str, dict[str, Any]] = {}
        groups: dict[str, list[SeriesAnalysis]] = {}
        groups_ab: dict[str, list[SeriesAnalysis]] = {}

        for position, ean in enumerate(panel.index, start=1):
            product = product_map.loc[ean] if ean in product_map.index else None
            segment = segment_map.loc[ean] if ean in segment_map.index else None
            status = str(product["Estado"]) if product is not None else "ACTIVO"
            xyz = str(segment["XYZ"]) if segment is not None else "Z"
            abc = str(segment["ABC_Valor"]) if segment is not None else "C"
            band = xyz
            meta[ean] = {
                "product": product,
                "segment": segment,
                "status": status,
                "xyz": xyz,
                "abc": abc,
                "band": band,
            }
            if status == "INACTIVO":
                continue
            analysis = self.engine.analyze_series(panel.loc[ean].to_numpy(dtype=float))
            analyses[ean] = analysis
            if analysis.backtest is not None:
                groups.setdefault(band, []).append(analysis)
                if abc in {"A", "B"}:
                    groups_ab.setdefault(band, []).append(analysis)
            if position % 300 == 0:
                self.logger.info("  ... %s/%s series analizadas", position, len(panel.index))

        # ---------------- Paso 2: política por patrón (X/Y/Z)
        policy = select_policy(groups)
        global_policy = select_policy({"ALL": [a for items in groups.values() for a in items]})
        for pattern, entry in policy.items():
            if entry["productos"] < 5 and global_policy:
                policy[pattern] = dict(global_policy["ALL"], productos=entry["productos"], sustituido=True)
        policy.setdefault("NUEVO", {"modelo": "promedio", "wape": np.nan, "productos": 0,
                                    "venta_real": 0.0, "error_abs": 0.0})
        policy.setdefault("SIN", {"modelo": "promedio", "wape": np.nan, "productos": 0,
                                  "venta_real": 0.0, "error_abs": 0.0})

        cv_all = evaluate_policy_cv(groups, max_origins=self.engine.backtest_months) if groups else {}
        cv_ab = evaluate_policy_cv(groups_ab, max_origins=self.engine.backtest_months) if groups_ab else {}

        # ---------------- Paso 3: construir pronósticos bajo la política
        forecasts: dict[str, ProductForecast] = {}
        validation_rows: list[dict[str, Any]] = []

        for ean, analysis in analyses.items():
            info = meta[ean]
            product, segment, status = info["product"], info["segment"], info["status"]
            group = info["band"] if analysis.backtest is not None else "NUEVO"
            model = policy.get(group, {}).get("modelo", "ma4")
            last_sale = product["Ultima_Venta"] if product is not None else None
            lead_time = int(product["Lead_Time"]) if product is not None and pd.notna(product["Lead_Time"]) else 30

            forecast = self.engine.build_forecast(
                ean, analysis, model, max_weeks, last_sale=last_sale,
                status=status, lead_time=lead_time, pattern=group,
            )
            gap = product["Dias_Sin_Venta"] if product is not None else 0
            forecast.gap_days = int(gap) if pd.notna(gap) else 999
            forecast.censoring_risk = self._censoring_risk(forecast, product, segment)
            forecasts[ean] = forecast

            validation_rows.append({
                "EAN": ean,
                "Descripcion": segment["Descripcion"] if segment is not None else "",
                "Proveedor": segment["Proveedor"] if segment is not None else "",
                "ABC": info["abc"],
                "XYZ": info["xyz"] if analysis.backtest is not None else "NUEVO",
                "Segmento": f"{info['abc']}{info['xyz']}",
                "Grupo_Politica": group,
                "Modelo": forecast.model,
                "Modelo_Detalle": MODEL_LABELS.get(forecast.model, forecast.model),
                "WAPE": forecast.wape,
                "MAPE": forecast.mape,
                "Sesgo": forecast.bias,
                "Hit25": forecast.hit25,
                "WAPE_Naive": forecast.wape_naive,
                "Mejora_vs_Naive": forecast.mejora_vs_naive,
                "Sigma_28d": forecast.sigma_28,
                "Semanas_Historia": forecast.weeks_history,
                "Origenes": forecast.origins,
                "Confianza": forecast.confidence,
                "Demanda_30d": forecast.forecast_30d,
                "Error_Abs": forecast.error_abs_sum,
                "Actual": forecast.actual_sum,
                "Posible_Quiebre": forecast.censoring_risk,
                "Estado": status,
            })

        # Productos inactivos: pronóstico cero explícito
        for ean in panel.index:
            if ean in forecasts:
                continue
            info = meta.get(ean, {})
            product = info.get("product")
            gap = product["Dias_Sin_Venta"] if product is not None and pd.notna(product["Dias_Sin_Venta"]) else 999
            forecasts[ean] = ProductForecast(
                ean=ean, weekly_path=np.zeros(max_weeks), model="sin_demanda",
                horizon_days=self.engine.horizon_days, wape=np.nan, mape=np.nan,
                bias=np.nan, hit25=np.nan, sigma_28=0.0, confidence=0.0,
                weeks_history=int(panel.shape[1]), last_sale=None, status="INACTIVO",
                gap_days=int(gap), lead_time=30, pattern="SIN",
            )

        validation = pd.DataFrame(validation_rows)
        if not validation.empty:
            validation["Nivel_Confianza"] = pd.cut(
                validation["WAPE"], bins=[-np.inf, 25, 50, np.inf],
                labels=["ALTA", "MEDIA", "BAJA"],
            ).astype(str).replace("nan", "SIN VALIDACION")

        policy_table = self._policy_table(groups, policy, cv_all)
        model_summary = self._model_summary(validation)
        segment_summary = self._segment_summary(validation)
        abc_summary = self._abc_summary(validation)
        metrics = self._metrics(validation, cv_all, cv_ab)

        self.logger.info("Política aplicada: %s",
                         {k: v["modelo"] for k, v in policy.items() if k not in ("NUEVO", "SIN")})
        self.logger.info("WAPE A/B (política): %.1f%% | WAPE A/B validación cruzada: %.1f%% | Hit±25%%: %.1f%%",
                         metrics.get("wape_ab", 0), metrics.get("wape_ab_cv", 0), metrics.get("hit25_ab", 0))

        return ForecastBundle(
            forecasts=forecasts,
            validation=validation,
            model_summary=model_summary,
            segment_summary=segment_summary,
            abc_summary=abc_summary,
            policy_table=policy_table,
            metrics=metrics,
            forecast_start=forecast_start,
            max_weeks=max_weeks,
            panel_weeks=panel.shape[1],
        )

    # ------------------------------------------------------------------ interno
    @staticmethod
    def _censoring_risk(forecast: ProductForecast, product: pd.Series | None,
                        segment: pd.Series | None) -> bool:
        """Marca brechas de venta anómalas (posible quiebre o descontinuación)."""
        if product is None or segment is None:
            return False
        gap = product.get("Dias_Sin_Venta")
        if gap is None or pd.isna(gap):
            return False
        adi = segment.get("ADI")
        group_days = 28.0
        expected = (float(adi) * group_days) if adi is not None and pd.notna(adi) and np.isfinite(adi) else 28.0
        threshold = max(14.0, 2.0 * expected)
        return bool(gap > threshold and str(segment.get("ABC_Valor", "C")) in {"A", "B"})

    @staticmethod
    def _policy_table(groups: dict[str, list[SeriesAnalysis]], policy: dict[str, dict],
                      cv: dict[str, float]) -> pd.DataFrame:
        rows = []
        for pattern, analyses in groups.items():
            entry = policy.get(pattern, {})
            model = entry.get("modelo", "ma4")
            abs_sum = bias_sum = actual_sum = 0.0
            hits = total_obs = 0
            for analysis in analyses:
                bt = analysis.backtest
                if bt is None or model not in bt.errors:
                    continue
                errors = bt.errors[model]
                abs_sum += sum(abs(e) for e in errors)
                bias_sum += sum(errors)
                actual_sum += bt.actual_sum
                hits += sum(1 for e, a in zip(errors, bt.actuals) if a > 0 and abs(e) / a <= 0.25)
                total_obs += sum(1 for a in bt.actuals if a > 0)
            rows.append({
                "Grupo": pattern,
                "Modelo": model,
                "Productos": int(entry.get("productos", len(analyses))),
                "Venta_Real_4S": actual_sum,
                "WAPE": abs_sum / actual_sum * 100 if actual_sum else np.nan,
                "WAPE_Validacion_Cruzada": cv.get(f"wape_{pattern}", np.nan),
                "Sesgo": bias_sum / actual_sum * 100 if actual_sum else np.nan,
                "Hit25": hits / total_obs * 100 if total_obs else np.nan,
            })
        table = pd.DataFrame(rows)
        return table.sort_values("Grupo").reset_index(drop=True) if not table.empty else table

    @staticmethod
    def _model_summary(validation: pd.DataFrame) -> pd.DataFrame:
        if validation.empty:
            return pd.DataFrame()
        valid = validation[validation["Origenes"] > 0]
        if valid.empty:
            return pd.DataFrame()
        grouped = valid.groupby("Modelo").agg(
            Productos=("EAN", "count"),
            Error_Abs=("Error_Abs", "sum"),
            Actual=("Actual", "sum"),
            WAPE_Mediana=("WAPE", "median"),
            MAPE_Mediana=("MAPE", "median"),
            Hit25_Promedio=("Hit25", "mean"),
            Confianza_Promedio=("Confianza", "mean"),
        ).reset_index()
        grouped["WAPE_Portafolio"] = grouped["Error_Abs"] / grouped["Actual"].replace(0, np.nan) * 100
        weighted_bias = valid.assign(_bias_x_actual=valid["Sesgo"] * valid["Actual"]).groupby("Modelo").agg(
            _bias=("_bias_x_actual", "sum"), _actual=("Actual", "sum"))
        grouped["Sesgo_Portafolio"] = grouped["Modelo"].map(
            (weighted_bias["_bias"] / weighted_bias["_actual"].replace(0, np.nan))).astype(float)
        grouped["Modelo_Detalle"] = grouped["Modelo"].map(MODEL_LABELS).fillna(grouped["Modelo"])
        grouped["%_Productos"] = grouped["Productos"] / grouped["Productos"].sum() * 100
        return grouped.sort_values("Productos", ascending=False).reset_index(drop=True)

    @staticmethod
    def _segment_summary(validation: pd.DataFrame) -> pd.DataFrame:
        if validation.empty:
            return pd.DataFrame()
        valid = validation[(validation["Actual"] > 0) & (validation["Origenes"] > 0)].copy()
        if valid.empty:
            return pd.DataFrame()
        rows = []
        for segment, part in valid.groupby("Segmento"):
            total_actual = part["Actual"].sum()
            origins = part["Origenes"].replace(0, np.nan)
            weighted_hit = float((part["Hit25"] * origins).sum(skipna=True) / origins.sum(skipna=True)) if origins.sum(skipna=True) else np.nan
            bias = float((part["Sesgo"] * part["Actual"]).sum() / total_actual) if total_actual else np.nan
            wape_seg = part["Error_Abs"].sum() / total_actual * 100 if total_actual else np.nan
            naive_wape = float(((part["WAPE_Naive"] / 100) * part["Actual"]).sum() / total_actual * 100) if total_actual else np.nan
            mejora = ((naive_wape - wape_seg) / naive_wape * 100) if naive_wape and not np.isnan(naive_wape) else np.nan
            rows.append({
                "Segmento": segment,
                "Productos": int(len(part)),
                "WAPE_Portafolio": wape_seg,
                "MAPE_Mediana": float(part["MAPE"].median(skipna=True)),
                "Sesgo_Portafolio": bias,
                "Hit25_Ponderado": weighted_hit,
                "%_Productos_WAPE_25": float((part["WAPE"] <= 25).mean() * 100),
                "Mejora_vs_Naive": float(mejora) if not np.isnan(mejora) else np.nan,
            })
        return pd.DataFrame(rows).sort_values("Segmento").reset_index(drop=True)

    @staticmethod
    def _abc_summary(validation: pd.DataFrame) -> pd.DataFrame:
        if validation.empty:
            return pd.DataFrame()
        rows = []
        for cls in ("A", "B", "C"):
            part = validation[validation["ABC"] == cls]
            valid = part[(part["Actual"] > 0) & (part["Origenes"] > 0)]
            total_actual = valid["Actual"].sum()
            rows.append({
                "Clase_ABC": cls,
                "Productos": int(len(part)),
                "Con_Validacion": int(len(valid)),
                "WAPE_Portafolio": valid["Error_Abs"].sum() / total_actual * 100 if total_actual else np.nan,
                "MAPE_Mediana": float(valid["MAPE"].median()) if len(valid) else np.nan,
                "Hit25_Medio": float(valid["Hit25"].mean()) if len(valid) else np.nan,
                "%_Productos_WAPE_25": float((valid["WAPE"] <= 25).mean() * 100) if len(valid) else 0.0,
                "%_Productos_MAPE_25": float((valid["MAPE"] <= 25).mean() * 100) if len(valid) else 0.0,
            })
        return pd.DataFrame(rows)

    @staticmethod
    def _metrics(validation: pd.DataFrame, cv_all: dict, cv_ab: dict) -> dict[str, Any]:
        if validation.empty:
            return {}
        valid = validation[(validation["Actual"] > 0) & (validation["Origenes"] > 0)]
        ab = valid[valid["ABC"].isin(["A", "B"])]
        total_actual = ab["Actual"].sum()
        wape_ab = ab["Error_Abs"].sum() / total_actual * 100 if total_actual else np.nan
        naive_wape = float(((ab["WAPE_Naive"] / 100) * ab["Actual"]).sum() / total_actual * 100) if total_actual else np.nan
        origins = ab["Origenes"].replace(0, np.nan)
        hit25 = float((ab["Hit25"] * origins).sum(skipna=True) / origins.sum(skipna=True)) if origins.sum(skipna=True) else np.nan
        bias_ab = float((ab["Sesgo"] * ab["Actual"]).sum() / total_actual) if total_actual else np.nan
        mejora = ((naive_wape - wape_ab) / naive_wape * 100) if naive_wape and not np.isnan(naive_wape) else np.nan
        return {
            "productos_validados": int(len(valid)),
            "productos_ab": int(len(ab)),
            "wape_ab": float(wape_ab) if not np.isnan(wape_ab) else 0.0,
            "wape_naive_ab": float(naive_wape) if not np.isnan(naive_wape) else 0.0,
            "wape_ab_cv": float(cv_ab.get("wape", np.nan)),
            "wape_global_cv": float(cv_all.get("wape", np.nan)),
            "mape_ab": float(ab["MAPE"].median()) if len(ab) else 0.0,
            "hit25_ab": hit25 if not np.isnan(hit25) else 0.0,
            "bias_ab": bias_ab if not np.isnan(bias_ab) else 0.0,
            "pct_ab_wape25": float((ab["WAPE"] <= 25).mean() * 100) if len(ab) else 0.0,
            "pct_ab_mape25": float((ab["MAPE"] <= 25).mean() * 100) if len(ab) else 0.0,
            "mejora_naive_ab": float(mejora) if not np.isnan(mejora) else 0.0,
            "confianza_media": float(valid["Confianza"].mean()) if len(valid) else 0.0,
            "cobertura_usd_ab": float(ab["Actual"].sum()) if len(ab) else 0.0,
            "origenes_ab": int(origins.sum(skipna=True)) if len(origins) else 0,
            "productos_quiebre": int(ab["Posible_Quiebre"].sum()) if len(ab) else 0,
        }
