"""Segmentación híbrida ABC/XYZ y estrategia de compra por segmento."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from utils.config import Config

STRATEGIES: dict[str, tuple[str, str, str]] = {
    # segmento: (estrategia, modelo sugerido, prioridad)
    "AX": ("Alta prioridad. Revisión semanal, stock de seguridad calculado y seguimiento de quiebres.",
           "Suavizado exponencial estacional / SARIMA", "ALTA"),
    "AY": ("Alta prioridad. Pronóstico semanal con ensemble; validar cambios de tendencia.",
           "Ensemble (Holt + Croston)", "ALTA"),
    "AZ": ("Alta prioridad. Demanda errática: comprar por evento, stock de seguridad alto y revisión manual.",
           "Croston / reglas de compra", "ALTA"),
    "BX": ("Prioridad media. Revisión quincenal y pronóstico con modelos intermedios.",
           "Suavizado exponencial", "MEDIA"),
    "BY": ("Prioridad media. Revisión quincenal; media móvil con seguimiento de tendencia.",
           "Media móvil + tendencia", "MEDIA"),
    "BZ": ("Prioridad media-baja. Reglas simples y control de exceso.",
           "Croston / reglas", "MEDIA"),
    "CX": ("Baja prioridad. Reposición automática por mínimos; revisión mensual.",
           "Media móvil", "BAJA"),
    "CY": ("Baja prioridad. Stock mínimo y compras consolidadas.",
           "Media móvil", "BAJA"),
    "CZ": ("Baja prioridad. Reducir inventario; comprar solo por necesidad o pedido.",
           "Reglas / naive", "BAJA"),
}


@dataclass
class SegmentationResult:
    products: pd.DataFrame
    summary: pd.DataFrame
    strategy_table: pd.DataFrame


class Segmenter:
    """Combina ABC (valor) y XYZ (variabilidad) en 9 segmentos accionables."""

    def __init__(self, config: Config, logger: logging.Logger | None = None):
        self.config = config
        self.logger = logger or logging.getLogger("compras")

    def build(self, abc_products: pd.DataFrame, xyz_products: pd.DataFrame,
              master: pd.DataFrame | None = None) -> SegmentationResult:
        abc_cols = ["EAN", "Valor_USD", "Unidades", "Transacciones", "Clientes",
                    "ABC_Valor", "ABC_Volumen", "Participacion_Valor", "Acumulado_Valor"]
        abc = abc_products[[c for c in abc_cols if c in abc_products.columns]].copy()

        xyz_cols = ["EAN", "CV", "XYZ", "Patron_Demanda", "ADI", "Zero_Share",
                    "Media_Periodo", "Periodos", "Periodos_Con_Venta"]
        xyz = xyz_products[[c for c in xyz_cols if c in xyz_products.columns]].copy()

        merged = abc.merge(xyz, on="EAN", how="left")
        merged["XYZ"] = merged["XYZ"].fillna("Z")
        merged["CV"] = merged["CV"].fillna(1.0)
        merged["Patron_Demanda"] = merged["Patron_Demanda"].fillna("SIN DEMANDA")
        merged["Segmento"] = merged["ABC_Valor"].astype(str) + merged["XYZ"].astype(str)

        merged["Estrategia"] = merged["Segmento"].map(lambda s: STRATEGIES.get(s, ("Gestión estándar", "", "MEDIA"))[0])
        merged["Modelo_Sugerido"] = merged["Segmento"].map(lambda s: STRATEGIES.get(s, ("", "Reglas", "MEDIA"))[1])
        merged["Prioridad_Segmento"] = merged["Segmento"].map(lambda s: STRATEGIES.get(s, ("", "", "MEDIA"))[2])

        if master is not None and not master.empty:
            extra = [c for c in ("Descripcion", "Proveedor", "Categoria", "Subcategoria",
                                 "Costo_USD", "Multiplo_Compra", "Lead_Time", "Estado",
                                 "Stock_Informado", "Venta_365d", "USD_365d") if c in master.columns]
            merged = merged.merge(master[["EAN"] + extra], on="EAN", how="left")

        summary = self._summary(merged)
        strategy_table = self._strategy_table()
        products = merged.sort_values(["Valor_USD", "EAN"], ascending=[False, True]).reset_index(drop=True)

        self.logger.info("Segmentación: %s productos en %s segmentos", len(products),
                         products["Segmento"].nunique())
        return SegmentationResult(products=products, summary=summary, strategy_table=strategy_table)

    # ------------------------------------------------------------------ interno
    @staticmethod
    def _summary(df: pd.DataFrame) -> pd.DataFrame:
        total_products = len(df) or 1
        total_usd = df["Valor_USD"].sum()
        total_units = df["Unidades"].sum()
        rows = []
        for segment in sorted(df["Segmento"].dropna().unique()):
            part = df[df["Segmento"] == segment]
            strategy, model, priority = STRATEGIES.get(segment, ("Gestión estándar", "Reglas", "MEDIA"))
            rows.append({
                "Segmento": segment,
                "Productos": int(len(part)),
                "%_Productos": len(part) / total_products * 100,
                "Valor_USD": float(part["Valor_USD"].sum()),
                "%_Ventas_USD": float(part["Valor_USD"].sum()) / total_usd * 100 if total_usd else 0.0,
                "Unidades": float(part["Unidades"].sum()),
                "%_Unidades": float(part["Unidades"].sum()) / total_units * 100 if total_units else 0.0,
                "CV_Promedio": float(part["CV"].mean(skipna=True)) if len(part) else 0.0,
                "Estrategia": strategy,
                "Modelo_Sugerido": model,
                "Prioridad": priority,
            })
        out = pd.DataFrame(rows)
        if not out.empty:
            out = out.sort_values(["Segmento"]).reset_index(drop=True)
        return out

    @staticmethod
    def _strategy_table() -> pd.DataFrame:
        rows = []
        for segment in [a + x for a in "ABC" for x in "XYZ"]:
            strategy, model, priority = STRATEGIES[segment]
            rows.append({"Segmento": segment, "Estrategia": strategy,
                         "Modelo_Sugerido": model, "Prioridad": priority})
        return pd.DataFrame(rows)
