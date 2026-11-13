"""Contexto compartido para los generadores de reportes (Excel y dashboard)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from analytics.abc_analyzer import ABCResult
from analytics.segmentation import SegmentationResult
from analytics.xyz_classifier import XYZResult
from data_processing.loader import LoadedData
from models.model_orchestrator import ForecastBundle
from recommendation.alert_system import AlertBundle
from recommendation.order_manager import OrderBundle
from utils.config import Config


@dataclass
class ReportContext:
    config: Config
    data: LoadedData
    products: pd.DataFrame
    abc: ABCResult
    xyz: XYZResult
    segmentation: SegmentationResult
    forecast_bundle: ForecastBundle
    order_bundle: OrderBundle
    alert_bundle: AlertBundle
    quality: pd.DataFrame
    generated_at: datetime
    output_dir: Path
    elapsed_seconds: float = 0.0

    @property
    def company(self) -> str:
        return str(self.config.get("system.company", "Droguería Emmanuelle"))

    @property
    def as_of(self) -> pd.Timestamp:
        return self.data.as_of

    @property
    def period_label(self) -> str:
        return f"{self.as_of:%m-%Y}"

    @property
    def stock_coverage_pct(self) -> float:
        total = len(self.products)
        if total == 0:
            return 0.0
        return float(self.products["Stock_Informado"].sum()) / total * 100

    @property
    def warehouse_info(self) -> dict:
        return getattr(self.data, "warehouse_info", {}) or {}
