"""Guardia de precios para la emisión de órdenes de compra.

Regla:
* compara el costo actual contra una referencia previa en USD,
* clasifica el aumento (verde/amarillo/naranja/rojo),
* calcula el techo y el impacto anualizado,
* bloquea la OC si supera el techo o destruye el margen mínimo.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from utils.config import Config


@dataclass
class PriceAssessment:
    reference: float = 0.0
    current: float = 0.0
    var_pct: float = 0.0
    impact_usd: float = 0.0
    ceiling: float = 0.0
    status: str = "SIN_REFERENCIA"
    requires_approval: bool = False
    approver: str = "Comprador"
    blocked: bool = False
    reason: str = ""
    margin_pct: float | None = None
    margin_min_pct: float = 5.0
    estimated_cost: bool = False


class PriceGuard:
    def __init__(self, config: Config, logger: logging.Logger | None = None):
        self.config = config
        self.logger = logger or logging.getLogger("compras")
        self.warning = float(config.get("price.warning_pct", 5.0))
        self.approval = float(config.get("price.approval_pct", 10.0))
        self.max_increase = float(config.get("price.max_increase_pct", 20.0))
        self.impact_threshold = float(config.get("price.impact_threshold_usd", 2000.0))
        self.min_margin = float(config.get("inventory.min_margin_pct", 5.0))
        self.block_above_ceiling = bool(config.get("price.block_above_ceiling", True))
        self.require_reference = bool(config.get("price.require_reference_for_estimated", False))

    def evaluate(self, product: pd.Series) -> PriceAssessment:
        result = PriceAssessment(margin_min_pct=self.min_margin)
        result.estimated_cost = bool(product.get("Costo_Estimado", False))

        reference = _num(product.get("Costo_Ref_90d"))
        current = _num(product.get("Costo_Ultimo_USD"))
        if current <= 0:
            current = _num(product.get("Costo_USD"))
        result.reference = max(reference, 0.0)
        result.current = max(current, 0.0)

        if result.current <= 0:
            result.status = "SIN_COSTO"
            result.reason = "Sin costo unitario confiable; validar con el proveedor antes de comprar."
            return result
        if result.reference <= 0 or (result.estimated_cost and self.require_reference):
            result.status = "SIN_REFERENCIA"
            result.reason = "Sin referencia de compra previa; se requiere revisión manual."
            result.requires_approval = bool(result.estimated_cost)
            result.approver = "Jefe de Compras"
            return result

        stored_var = _num(product.get("Var_Precio_Pct"), default=np.nan)
        result.var_pct = stored_var if not np.isnan(stored_var) else (result.current / result.reference - 1.0) * 100.0
        stored_impact = _num(product.get("Impacto_Precio_USD"), default=np.nan)
        if np.isnan(stored_impact):
            annual = _num(product.get("Demanda_Anual_Unidades"))
            if annual <= 0:
                annual = _num(product.get("Venta_365d"))
            stored_impact = (result.current - result.reference) * annual
        result.impact_usd = stored_impact
        result.ceiling = result.reference * (1.0 + self.max_increase / 100.0)

        sale_price = _num(product.get("Precio_Prom_USD"))
        stored_margin = _num(product.get("Margen_Nuevo_Pct"), default=np.nan)
        if sale_price > 0:
            result.margin_pct = stored_margin if not np.isnan(stored_margin) else (
                (sale_price - result.current) / sale_price * 100.0)

        if result.var_pct > self.max_increase:
            result.status = "ROJO"
            result.requires_approval = True
            result.approver = "Gerencia General"
            result.reason = (f"Aumento de costo {result.var_pct:+.1f}% supera el techo de "
                             f"{self.max_increase:.0f}% (ref. ${result.reference:.2f}).")
            result.blocked = self.block_above_ceiling
        elif result.var_pct > self.approval:
            result.status = "NARANJA"
            result.requires_approval = True
            result.approver = "Gerente de Compras"
            result.reason = (f"Aumento de costo {result.var_pct:+.1f}% entre {self.approval:.0f}% y "
                             f"{self.max_increase:.0f}%; requiere negociación/aprobación.")
        elif result.var_pct > self.warning:
            result.status = "AMARILLO"
            result.approver = "Jefe de Compras"
            result.reason = (f"Aumento de costo {result.var_pct:+.1f}% sobre la referencia; "
                             "revisar justificación del proveedor.")
        else:
            result.status = "VERDE"
            result.reason = f"Variación de costo {result.var_pct:+.1f}% dentro de lo aceptable."

        if result.impact_usd >= self.impact_threshold and result.var_pct >= self.warning:
            result.requires_approval = True
            if result.approver == "Comprador":
                result.approver = "Gerente de Compras"
            result.reason += f" Impacto anual ${result.impact_usd:,.0f} supera el umbral."

        if result.margin_pct is not None and not result.estimated_cost:
            if result.margin_pct < self.min_margin:
                result.blocked = True
                result.requires_approval = True
                result.approver = "Gerencia General"
                result.reason += (f" Margen nuevo {result.margin_pct:.1f}% < mínimo "
                                  f"{self.min_margin:.1f}%.")
                if result.status == "VERDE":
                    result.status = "MARGEN_BAJO"
        return result


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return default
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
