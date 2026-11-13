"""Clasificación XYZ por variabilidad de la demanda y patrón de intermitencia.

La demanda de una droguería mayorista es naturalmente intermitente. Por eso la
variabilidad se calcula sobre **periodos de 4 semanas** (13 periodos = 1 año), que
reducen el ruido diario sin ocultar los quiebres. Los umbrales de CV son
configurables (``analysis.xyz_cv_thresholds``) y deben recalibrarse si cambia la
mezcla de portafolio.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from utils.config import Config


@dataclass
class XYZResult:
    products: pd.DataFrame
    summary: pd.DataFrame
    totals: dict[str, Any]


class XYZClassifier:
    def __init__(self, config: Config, logger: logging.Logger | None = None):
        self.config = config
        self.logger = logger or logging.getLogger("compras")
        thresholds = config.get("analysis.xyz_cv_thresholds", [1.0, 2.0])
        self.cv_thresholds = tuple(float(x) for x in thresholds)
        self.period_days = int(config.get("analysis.xyz_period_days", 28))
        self.periods = int(config.get("analysis.xyz_periods", 13))

    # ------------------------------------------------------------------ público
    def classify(self, sales: pd.DataFrame, as_of: pd.Timestamp,
                 window_days: int | None = None) -> XYZResult:
        period = self.period_days
        n_periods = self.periods
        cutoff = as_of - pd.Timedelta(days=period * n_periods)
        window = sales[sales["Fecha"] > cutoff].copy()
        if window.empty:
            raise ValueError("No hay ventas en la ventana de análisis XYZ")

        # 0 = periodo más reciente (últimos `period` días antes de as_of)
        days = (as_of - window["Fecha"]).dt.days
        window["Periodo"] = np.minimum(days // period, n_periods - 1).astype(int)
        labels = [f"P{i:02d}" for i in range(n_periods)]
        pivot = (window.pivot_table(index="EAN", columns="Periodo", values="Cantidad",
                                    aggfunc="sum", fill_value=0)
                 .reindex(columns=range(n_periods), fill_value=0))
        pivot.columns = labels

        values = pivot.to_numpy(dtype=float)
        rows = []
        for idx, ean in enumerate(pivot.index):
            y = values[idx]
            nonzero = y[y > 0]
            mean = float(y.mean()) if n_periods else 0.0
            std = float(y.std(ddof=1)) if n_periods > 1 else 0.0
            cv = std / mean if mean > 0 else np.nan
            zero_share = float((y == 0).mean()) if n_periods else 1.0
            adi = n_periods / len(nonzero) if len(nonzero) else np.inf
            cv2 = (float(nonzero.std(ddof=1)) / float(nonzero.mean())) ** 2 \
                if len(nonzero) > 1 and nonzero.mean() > 0 else np.nan

            rows.append({
                "EAN": ean,
                "CV": cv,
                "XYZ": self._classify_cv(cv),
                "Patron_Demanda": self._pattern(adi, cv2),
                "ADI": adi,
                "CV2": cv2,
                "Periodos": n_periods,
                "Periodos_Con_Venta": int(len(nonzero)),
                "Zero_Share": zero_share,
                "Media_Periodo": mean,
                "Std_Periodo": std,
                "P90_Periodo": float(np.percentile(y, 90)) if n_periods else 0.0,
                "Max_Periodo": float(y.max()) if n_periods else 0.0,
            })

        products = pd.DataFrame(rows)
        summary = self._summary(products)
        totals = {
            "productos": int(len(products)),
            "periodos": n_periods,
            "periodo_dias": period,
            "zero_share": float((values == 0).mean()),
            "cv_medio": float(products["CV"].mean(skipna=True)) if len(products) else 0.0,
        }
        self.logger.info("XYZ: X=%s | Y=%s | Z=%s | %s periodos de %sd | %.0f%% periodos sin venta",
                         int((products["XYZ"] == "X").sum()), int((products["XYZ"] == "Y").sum()),
                         int((products["XYZ"] == "Z").sum()), n_periods, period,
                         totals["zero_share"] * 100)
        return XYZResult(products=products, summary=summary, totals=totals)

    # ------------------------------------------------------------------ interno
    def _classify_cv(self, cv: float) -> str:
        if cv is None or (isinstance(cv, float) and np.isnan(cv)):
            return "Z"
        if cv <= self.cv_thresholds[0]:
            return "X"
        if cv <= self.cv_thresholds[1]:
            return "Y"
        return "Z"

    @staticmethod
    def _pattern(adi: float, cv2: float) -> str:
        if not np.isfinite(adi):
            return "SIN DEMANDA"
        adi_alto = adi >= 1.32
        cv2_alto = (cv2 is None) or (isinstance(cv2, float) and np.isnan(cv2)) or cv2 >= 0.49
        if not adi_alto and not cv2_alto:
            return "SUAVE"
        if adi_alto and not cv2_alto:
            return "INTERMITENTE"
        if not adi_alto and cv2_alto:
            return "ERRATICO"
        return "IRREGULAR"

    def _summary(self, df: pd.DataFrame) -> pd.DataFrame:
        rows = []
        total = len(df) or 1
        for cls in ("X", "Y", "Z"):
            part = df[df["XYZ"] == cls]
            rows.append({
                "Clase_XYZ": cls,
                "Productos": int(len(part)),
                "%_Productos": len(part) / total * 100,
                "CV_Promedio": float(part["CV"].mean(skipna=True)) if len(part) else 0.0,
                "ADI_Promedio": float(part["ADI"].replace(np.inf, np.nan).mean(skipna=True)) if len(part) else 0.0,
                "Zero_Share_Promedio": float(part["Zero_Share"].mean()) if len(part) else 0.0,
            })
        return pd.DataFrame(rows)
