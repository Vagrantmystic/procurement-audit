"""Cálculo de la orden de compra sugerida para un producto.

Reglas:
1. La base de inventario es ``ACT − COM``. ``LLE`` no se suma nunca.
2. ``LLE`` se usa como bandera de pedido pendiente: si hay tránsito, la OC se
   bloquea para evitar duplicidad hasta validar la orden/ETA.
3. Los productos EOL/obsoletos/liquidación no generan OC.
4. El precio se valida contra una referencia previa; si supera el techo o el
   margen mínimo, la OC se bloquea o requiere aprobación.
5. Solo si pasa los controles se calcula la cantidad con order-up-to, stock de
   seguridad, mínimo, múltiplo y cobertura máxima.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from models.forecast_engine import ProductForecast
from recommendation.price_guard import PriceAssessment, PriceGuard
from utils.config import Config, Provider
from utils.helpers import round_to_multiple, safe_div


@dataclass
class OrderDecision:
    cantidad: int = 0
    prioridad: str = "BAJA"
    motivo: str = ""
    justificacion: str = ""
    lead_time: int = 30
    revision: int = 7
    cobertura_dias: int = 37
    demanda_cobertura: float = 0.0
    stock_seguridad: float = 0.0
    punto_pedido: float = 0.0
    objetivo: float = 0.0
    stock_usado: float = 0.0
    stock_informado: bool = False
    dias_cobertura: float | None = None
    dias_cobertura_post: float | None = None
    multiplo: int = 24
    minimo_pedido: int = 24
    descuento_pct: float = 0.0
    costo_unitario: float = 0.0
    costo_total: float = 0.0
    politica_stock: str = "minimum"
    exceso_unidades: float = 0.0
    # ---- reglas de decisión OC
    stock_actual: float = 0.0
    comprometido: float = 0.0
    stock_lle: float = 0.0
    stock_usado_oc: float = 0.0
    lle_estado: str = "SIN_LLE"
    lle_confirmado: bool = False
    oc_abierta: bool = False
    estado_ciclo_vida: str = "ACTIVO"
    compra_bloqueada: bool = False
    motivo_bloqueo: str = ""
    accion_eol: str = ""
    precio_ref: float = 0.0
    precio_actual: float = 0.0
    var_precio_pct: float = 0.0
    precio_techo: float = 0.0
    precio_estado: str = "SIN_REFERENCIA"
    aprobacion_requerida: bool = False
    aprobador: str = "Comprador"
    margen_nuevo_pct: float | None = None
    impacto_precio_usd: float = 0.0


class OrderCalculator:
    def __init__(self, config: Config, logger: logging.Logger | None = None):
        self.config = config
        self.logger = logger or logging.getLogger("compras")
        self.z = config.z_value()
        self.max_coverage_days = int(config.get("inventory.max_coverage_days", 120))
        self.safety_buffer_days = int(config.get("inventory.safety_buffer_days", 15))
        self.max_safety_days = int(config.get("inventory.max_safety_days", 30))
        self.critical_days = int(config.get("alerts.critical_stock_days", 7))
        self.low_days = int(config.get("alerts.low_stock_days", 15))
        self.excess_days = int(config.get("alerts.excess_stock_days", 540))
        self.unknown_policy = str(config.get("inventory.unknown_stock_policy", "minimum")).lower()
        self.min_forecast_30d = float(config.get("inventory.min_forecast_30d", 1.0))
        self.use_committed = bool(config.get("inventory.use_committed_in_oc", True))
        self.lle_policy = str(config.get("inventory.lle_policy", "block")).lower()
        self.price_guard = PriceGuard(config, self.logger)

    # ------------------------------------------------------------------ público
    def calculate(self, forecast: ProductForecast, product: pd.Series,
                  provider: Provider) -> OrderDecision:
        decision = OrderDecision()
        stock_informado = bool(product.get("Stock_Informado", False))
        unit_cost = _val(product.get("Costo_Ultimo_USD"), _val(product.get("Costo_USD")))
        multiple = int(product.get("Multiplo_Compra") or provider.multiplo or 24)
        minimum = int(provider.minimo or 1)
        lead = int(product.get("Lead_Time") or provider.lead_time or 30)
        review = int(provider.revision or self.config.get("forecasting.review_period_days", 7))
        coverage_days = max(lead + review, 1)
        daily = forecast.daily_for_days(max(30, coverage_days))
        demand_cover = forecast.units_for_days(coverage_days)

        decision.lead_time = lead
        decision.revision = review
        decision.cobertura_dias = coverage_days
        decision.demanda_cobertura = demand_cover
        decision.multiplo = max(multiple, 1)
        decision.minimo_pedido = minimum
        decision.stock_informado = stock_informado
        decision.costo_unitario = unit_cost
        decision.politica_stock = self.unknown_policy if not stock_informado else "real"

        # ---------------- ciclo de vida: bloqueo duro
        decision.estado_ciclo_vida = str(product.get("Estado_Ciclo_Vida", "ACTIVO") or "ACTIVO")
        decision.compra_bloqueada = bool(product.get("Compra_Bloqueada", False))
        decision.motivo_bloqueo = str(product.get("Motivo_Bloqueo", "") or "")
        decision.accion_eol = str(product.get("Accion_EOL", "") or "")
        if decision.compra_bloqueada:
            decision.motivo = "EOL_BLOQUEADO"
            decision.prioridad = "REVISAR"
            decision.justificacion = (
                f"Producto en estado {decision.estado_ciclo_vida}: {decision.motivo_bloqueo}. "
                "No se genera OC automática; aplicar flujo de liquidación o excepción con pedido firme.")
            decision.costo_total = 0.0
            return decision

        # ---------------- inventario para OC: ACT − COM; LLE NUNCA se suma
        stock_actual = _val(product.get("Stock_Actual"))
        has_actual = "Stock_Actual" in product.index and not pd.isna(product.get("Stock_Actual"))
        if stock_informado and not has_actual:
            # Compatibilidad con datos antiguos que solo traían Stock_Disponible.
            legacy = _val(product.get("Stock_Disponible"))
            if legacy > 0:
                stock_actual = legacy
        comprometido = _val(product.get("Comprometido"),
                             _val(product.get("Stock_Comprometido")))
        lle = _val(product.get("En_Transito"), _val(product.get("Stock_Transito")))
        stock_for_oc = max(0.0, stock_actual - comprometido) if self.use_committed else stock_actual
        if not stock_informado:
            stock_for_oc = 0.0
        decision.stock_actual = stock_actual
        decision.comprometido = comprometido
        decision.stock_lle = lle
        decision.stock_usado_oc = stock_for_oc
        decision.stock_usado = stock_for_oc
        decision.lle_confirmado = bool(product.get("LLE_Confirmado", False))
        decision.oc_abierta = bool(product.get("OC_Abierta", False))
        decision.dias_cobertura = safe_div(stock_for_oc, daily, default=None) if daily > 0 else None
        if lle > 0:
            decision.lle_estado = "OC_ABIERTA" if decision.oc_abierta else (
                "LLE_CONFIRMADO" if decision.lle_confirmado else "LLE_SIN_CONFIRMAR")
        else:
            decision.lle_estado = "SIN_LLE"
        stock = stock_for_oc

        # ---------------- sin demanda
        if forecast.model == "sin_demanda" or (demand_cover <= 0 and daily <= 0):
            decision.motivo = "SIN_DEMANDA"
            decision.prioridad = "BAJA"
            decision.justificacion = "Sin demanda pronosticada en la ventana reciente; no se recomienda comprar."
            decision.costo_total = 0.0
            return decision

        # ---------------- stock de seguridad
        z = self.z
        sigma = float(forecast.sigma_28 or 0.0)
        sigma_cover = sigma * math.sqrt(coverage_days / 28.0)
        safety = z * sigma_cover if sigma_cover > 0 else daily * self.safety_buffer_days
        safety = min(safety, daily * self.max_safety_days) if daily > 0 else safety
        safety = max(safety, 0.0)
        decision.stock_seguridad = safety
        decision.punto_pedido = daily * lead + safety

        # ---------------- objetivo
        if stock_informado:
            target = demand_cover + safety
        else:
            # Sin inventario real: sugerir solo la demanda del lead time + revisión
            target = demand_cover if self.unknown_policy == "minimum" else demand_cover + safety

        stock_min = product.get("Stock_Minimo")
        stock_max = product.get("Stock_Maximo")
        capped_by_max = False
        if pd.notna(stock_min) and stock_min and stock_min > 0:
            target = max(target, float(stock_min))
        if pd.notna(stock_max) and stock_max and stock_max > 0:
            if target > float(stock_max):
                capped_by_max = True
            target = min(target, float(stock_max))
        # El máximo es un objetivo blando: nunca debe impedir cubrir el lead time.
        if target < demand_cover:
            target = demand_cover
            capped_by_max = capped_by_max or (pd.notna(stock_max) and bool(stock_max))
        decision.objetivo = target

        # ---------------- cantidad
        if not stock_informado and self.unknown_policy == "skip":
            decision.motivo = "STOCK_NO_INFORMADO"
            decision.prioridad = "REVISAR"
            decision.justificacion = ("No hay stock informado para este producto. "
                                      "Se recomienda cargar el inventario para calcular la compra.")
            return decision

        qty_raw = target - stock
        coverage_now = decision.dias_cobertura

        # Regla de baja rotación: sin stock informado y demanda muy baja -> no sugerir
        if (not stock_informado and forecast.forecast_30d < self.min_forecast_30d
                and str(product.get("ABC_Valor", "C")) == "C"):
            decision.motivo = "BAJA_ROTACION"
            decision.prioridad = "BAJA"
            decision.justificacion = (f"Demanda prevista de {forecast.forecast_30d:.1f} u/30 d. "
                                      "Producto C sin stock informado: revisar antes de comprar.")
            return decision

        if qty_raw <= 0:
            decision.motivo = "STOCK_SUFICIENTE"
            decision.prioridad = "BAJA"
            if coverage_now is not None:
                decision.justificacion = (f"Stock ACT−COM de {stock:.0f} u cubre {coverage_now:.0f} días "
                                          f"(objetivo {coverage_days} d). No requiere compra.")
            else:
                decision.justificacion = "No requiere compra."
            decision.costo_total = 0.0
            return decision

        # ---------------- control de precio (antes de emitir la OC)
        assessment = self.price_guard.evaluate(product)
        self._apply_price(decision, assessment)
        if assessment.blocked:
            decision.motivo = "PRECIO_FUERA_DE_TECHO" if assessment.status == "ROJO" else "MARGEN_BAJO"
            decision.prioridad = "REVISAR"
            decision.justificacion = assessment.reason
            decision.costo_total = 0.0
            return decision

        # ---------------- LLE: no suma, pero bloquea duplicidad
        if lle > 0:
            must_block = (self.lle_policy == "block") or decision.oc_abierta or decision.lle_confirmado
            if must_block:
                decision.motivo = "LLE_PENDIENTE"
                decision.prioridad = "REVISAR"
                decision.justificacion = (
                    f"Hay {lle:.0f} u en tránsito ({decision.lle_estado}) y el stock ACT−COM es "
                    f"{stock:.0f} u. No se emite OC automática para evitar duplicidad; "
                    "validar la orden pendiente/ETA. Si el tránsito no llega, generar excepción.")
                decision.costo_total = 0.0
                return decision

        qty = max(qty_raw, minimum)
        qty = round_to_multiple(qty, multiple, mode="up")

        # Tope por cobertura máxima y por stock máximo
        max_coverage = max(self.max_coverage_days, coverage_days)
        max_target = forecast.units_for_days(max_coverage) + (safety if stock_informado else 0)
        if stock_max is not None and pd.notna(stock_max) and stock_max and stock_max > 0:
            max_target = min(max_target, float(stock_max))
        max_target = max(max_target, demand_cover)
        max_qty = max(0.0, max_target - stock)
        if qty > max_qty:
            capped = round_to_multiple(max_qty, multiple, mode="down") if max_qty >= multiple else 0
            if capped <= 0:
                decision.motivo = "MAX_COBERTURA"
                decision.prioridad = "BAJA"
                decision.justificacion = (f"El stock actual ya cubre el máximo de {max_coverage} días; "
                                          "no se recomienda comprar.")
                return decision
            qty = int(capped)

        decision.cantidad = int(qty)
        decision.costo_unitario = unit_cost
        decision.descuento_pct = float(provider.descuento_pct(qty))
        decision.costo_total = qty * unit_cost * (1.0 - decision.descuento_pct / 100.0)
        decision.dias_cobertura_post = safe_div(stock + qty, daily, default=None) if daily > 0 else None

        # ---------------- prioridad
        decision.prioridad = self._priority(
            stock_informado=stock_informado,
            coverage=coverage_now,
            lead=lead,
            abc=str(product.get("ABC_Valor", "C")),
            censoring=bool(forecast.censoring_risk),
            qty=qty,
        )
        decision.motivo = "REPOSICION" if stock_informado else "STOCK_NO_INFORMADO"
        decision.justificacion = self._justification(
            decision, forecast, stock, coverage_now, stock_informado,
        )
        if capped_by_max:
            decision.justificacion += (f" El stock máximo configurado ({float(stock_max):.0f} u) "
                                       "limita el pedido; revise ese parámetro si la demanda es mayor.")
        return decision

    # ------------------------------------------------------------------ interno
    def _apply_price(self, decision: OrderDecision, assessment: PriceAssessment) -> None:
        decision.precio_ref = round(assessment.reference, 4)
        decision.precio_actual = round(assessment.current, 4)
        decision.var_precio_pct = round(assessment.var_pct, 2)
        decision.precio_techo = round(assessment.ceiling, 4)
        decision.precio_estado = assessment.status
        decision.aprobacion_requerida = bool(assessment.requires_approval)
        decision.aprobador = assessment.approver
        decision.margen_nuevo_pct = assessment.margin_pct
        decision.impacto_precio_usd = round(assessment.impact_usd, 2)

    def _priority(self, stock_informado: bool, coverage: float | None, lead: int,
                  abc: str, censoring: bool, qty: int) -> str:
        if stock_informado and coverage is not None:
            if coverage <= max(self.critical_days, lead):
                return "CRÍTICA"
            if coverage <= lead + self.low_days:
                return "ALTA"
            if coverage <= lead + 30:
                return "MEDIA"
            return "BAJA"
        # Sin inventario: prioridad por valor y riesgo de quiebre
        if censoring:
            return "ALTA"
        if abc == "A":
            return "ALTA"
        if abc == "B":
            return "MEDIA"
        return "BAJA"

    def _justification(self, decision: OrderDecision, forecast: ProductForecast,
                       stock: float, coverage_now: float | None, stock_informado: bool) -> str:
        parts: list[str] = []
        if stock_informado and coverage_now is not None:
            parts.append(f"Cobertura actual {coverage_now:.0f} d (lead time {decision.lead_time} d)")
        elif not stock_informado:
            parts.append("Stock no informado")
        parts.append(f"demanda {decision.cobertura_dias} d: {decision.demanda_cobertura:.0f} u")
        if stock_informado:
            parts.append(f"ACT {decision.stock_actual:.0f} u")
            if decision.comprometido > 0:
                parts.append(f"comprometido {decision.comprometido:.0f} u")
            parts.append(f"stock OC {stock:.0f} u")
            parts.append(f"stock de seguridad: {decision.stock_seguridad:.0f} u")
            if decision.stock_lle > 0:
                parts.append(f"LLE {decision.stock_lle:.0f} u no sumado ({decision.lle_estado})")
        if decision.var_precio_pct:
            parts.append(f"costo {decision.var_precio_pct:+.1f}% vs referencia "
                         f"({decision.precio_estado})")
        if decision.aprobacion_requerida:
            parts.append(f"requiere aprobación de {decision.aprobador}")
        if forecast.censoring_risk:
            parts.append(f"sin ventas {forecast.gap_days} d (posible quiebre)")
        if decision.descuento_pct > 0:
            parts.append(f"descuento {decision.descuento_pct:.0f}%")
        return "; ".join(parts) + "."


def _val(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
