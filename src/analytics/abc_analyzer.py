"""Análisis ABC por valor (USD) y por volumen (unidades)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from utils.config import Config


@dataclass
class ABCResult:
    products: pd.DataFrame
    summary: pd.DataFrame
    totals: dict[str, Any]


class ABCAnalyzer:
    """Clasifica productos según la concentración de ventas (Pareto 80/95 y 70/90)."""

    def __init__(self, config: Config, logger: logging.Logger | None = None):
        self.config = config
        self.logger = logger or logging.getLogger("compras")
        thresholds = config.get("analysis.abc_value_thresholds", [80.0, 95.0])
        self.value_thresholds = tuple(float(x) for x in thresholds)
        vol = config.get("analysis.abc_volume_thresholds", [70.0, 90.0])
        self.volume_thresholds = tuple(float(x) for x in vol)

    # ------------------------------------------------------------------ público
    def analyze(self, sales: pd.DataFrame, as_of: pd.Timestamp,
                window_days: int | None = None) -> ABCResult:
        window_days = int(window_days or self.config.get("analysis.window_days", 365))
        cutoff = as_of - pd.Timedelta(days=window_days)
        window = sales[sales["Fecha"] > cutoff].copy()
        if window.empty:
            raise ValueError("No hay ventas en la ventana de análisis ABC")

        agg = window.groupby("EAN").agg(
            Valor_USD=("Monto_USD", "sum"),
            Unidades=("Cantidad", "sum"),
            Transacciones=("Cantidad", "size"),
            Clientes=("Cliente_ID", "nunique"),
        ).reset_index()

        agg = self._classify(agg, "Valor_USD", "ABC_Valor", "Participacion_Valor", "Acumulado_Valor",
                             self.value_thresholds)
        agg = self._classify(agg, "Unidades", "ABC_Volumen", "Participacion_Volumen", "Acumulado_Volumen",
                             self.volume_thresholds)

        total_usd = float(agg["Valor_USD"].sum())
        total_units = float(agg["Unidades"].sum())
        agg["Precio_Prom_USD"] = np.where(agg["Unidades"] > 0, agg["Valor_USD"] / agg["Unidades"], np.nan)
        agg["Participacion_Valor_Pct"] = agg["Participacion_Valor"] * 100
        agg["Participacion_Volumen_Pct"] = agg["Participacion_Volumen"] * 100

        summary = self._summary(agg, total_usd, total_units)
        totals = {
            "productos": int(len(agg)),
            "valor_usd": total_usd,
            "unidades": total_units,
            "transacciones": int(agg["Transacciones"].sum()),
            "ventana_dias": window_days,
            "desde": cutoff.date(),
            "hasta": as_of.date(),
            "a_value_share": float(agg.loc[agg["ABC_Valor"] == "A", "Participacion_Valor"].sum()),
            "a_product_share": float((agg["ABC_Valor"] == "A").mean()),
        }
        self.logger.info("ABC: %s productos | A=%s (%.1f%% USD) | B=%s | C=%s", len(agg),
                         int((agg["ABC_Valor"] == "A").sum()), totals["a_value_share"] * 100,
                         int((agg["ABC_Valor"] == "B").sum()), int((agg["ABC_Valor"] == "C").sum()))
        return ABCResult(products=agg, summary=summary, totals=totals)

    # ------------------------------------------------------------------ interno
    @staticmethod
    def _classify(df: pd.DataFrame, value_col: str, class_col: str,
                  share_col: str, cumulative_col: str, thresholds: tuple[float, float]) -> pd.DataFrame:
        ordered = df.sort_values(value_col, ascending=False).copy()
        total = ordered[value_col].sum()
        if total <= 0:
            ordered[share_col] = 0.0
            ordered[cumulative_col] = 0.0
            ordered[class_col] = "C"
            return ordered

        ordered[share_col] = ordered[value_col] / total
        ordered[cumulative_col] = ordered[share_col].cumsum()
        cut_a, cut_b = thresholds[0] / 100.0, thresholds[1] / 100.0

        # El primer producto siempre es A (aunque concentre más del 80%)
        classes = np.where(ordered[cumulative_col] <= cut_a + 1e-12, "A",
                           np.where(ordered[cumulative_col] <= cut_b + 1e-12, "B", "C"))
        if len(classes):
            classes[0] = "A"
        ordered[class_col] = classes
        return ordered

    def _summary(self, df: pd.DataFrame, total_usd: float, total_units: float) -> pd.DataFrame:
        rows = []
        for cls in ("A", "B", "C"):
            part = df[df["ABC_Valor"] == cls]
            rows.append({
                "Clase_ABC": cls,
                "Productos": int(len(part)),
                "%_Productos": len(part) / len(df) * 100 if len(df) else 0.0,
                "Valor_USD": float(part["Valor_USD"].sum()),
                "%_Valor_USD": float(part["Valor_USD"].sum()) / total_usd * 100 if total_usd else 0.0,
                "Unidades": float(part["Unidades"].sum()),
                "%_Unidades": float(part["Unidades"].sum()) / total_units * 100 if total_units else 0.0,
            })
        return pd.DataFrame(rows)
