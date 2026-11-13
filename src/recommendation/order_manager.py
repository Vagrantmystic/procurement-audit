"""Generación de la lista de órdenes de compra sugeridas."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from models.forecast_engine import ProductForecast
from recommendation.order_calculator import OrderCalculator, OrderDecision
from utils.config import Config
from utils.helpers import percent_change

PRIORITY_ORDER = {"CRÍTICA": 0, "ALTA": 1, "MEDIA": 2, "BAJA": 3, "REVISAR": 4}

ORDER_COLUMNS = [
    "Orden", "Prioridad", "EAN", "Descripcion", "Proveedor", "ABC", "XYZ", "Segmento",
    "Stock_Actual", "Stock_Informado", "Demanda_30d", "Demanda_Cobertura",
    "Stock_Seguridad", "Punto_Pedido", "Objetivo_Stock", "Cantidad_Sugerida", "Multiplo",
    "Cajas", "Costo_Unitario_USD", "Descuento_Pct", "Costo_Total_USD",
    "Dias_Cobertura", "Lead_Time", "Cobertura_Dias", "Venta_30d", "Venta_90d",
    "Tendencia_Pct", "Modelo", "Confianza", "Estado", "Posible_Quiebre", "Justificacion",
    "Estado_Ciclo_Vida", "Compra_Bloqueada", "Motivo_Bloqueo",
    "Stock_Usado_OC", "Stock_Comprometido", "Stock_LLE", "LLE_Estado",
    "Precio_Ref_USD", "Var_Precio_Pct", "Precio_Techo_USD", "Precio_Estado",
    "Margen_Nuevo_Pct", "Aprobacion_Requerida", "Aprobador", "Impacto_Precio_USD",
]


@dataclass
class OrderBundle:
    orders: pd.DataFrame
    summary: dict[str, Any]
    by_priority: pd.DataFrame
    by_supplier: pd.DataFrame
    decisions: dict[str, OrderDecision]
    excluded: pd.DataFrame
    price_alerts: pd.DataFrame


class OrderManager:
    def __init__(self, config: Config, logger: logging.Logger | None = None):
        self.config = config
        self.logger = logger or logging.getLogger("compras")
        self.calculator = OrderCalculator(config, self.logger)

    # ------------------------------------------------------------------ público
    def generate(self, forecasts: dict[str, ProductForecast], products: pd.DataFrame,
                 segmentation: pd.DataFrame) -> OrderBundle:
        master = products.merge(
            segmentation[["EAN", "Segmento", "ABC_Valor", "XYZ", "CV", "Patron_Demanda"]],
            on="EAN", how="left", suffixes=("", "_seg"),
        )
        master = master.set_index("EAN", drop=False)
        if "Compra_Bloqueada" not in master.columns:
            master["Compra_Bloqueada"] = False
        master["Compra_Bloqueada"] = master["Compra_Bloqueada"].fillna(False).astype(bool)

        rows: list[dict[str, Any]] = []
        decisions: dict[str, OrderDecision] = {}
        skipped = {"inactivo": 0, "sin_demanda": 0, "sin_cantidad": 0, "no_activo": 0,
                   "bloqueado_eol": 0, "lle_pendiente": 0, "precio_bloqueado": 0}

        excluded = self._excluded(master)
        for ean, forecast in forecasts.items():
            if ean not in master.index:
                continue
            product = master.loc[ean]
            if str(product.get("Estado", "")) == "INACTIVO":
                skipped["inactivo"] += 1
                continue
            if not bool(product.get("Activo_Override", True)):
                skipped["no_activo"] += 1
                continue
            if bool(product.get("Compra_Bloqueada", False)):
                skipped["bloqueado_eol"] += 1
                continue
            provider = self.config.provider(str(product.get("Proveedor", "")))
            decision = self.calculator.calculate(forecast, product, provider)
            decisions[ean] = decision
            if decision.cantidad <= 0:
                if decision.motivo == "SIN_DEMANDA":
                    skipped["sin_demanda"] += 1
                elif decision.motivo == "LLE_PENDIENTE":
                    skipped["lle_pendiente"] += 1
                elif decision.motivo in {"PRECIO_FUERA_DE_TECHO", "MARGEN_BAJO"}:
                    skipped["precio_bloqueado"] += 1
                else:
                    skipped["sin_cantidad"] += 1
                continue

            row = self._row(product, forecast, decision)
            rows.append(row)

        orders = pd.DataFrame(rows, columns=ORDER_COLUMNS) if rows else pd.DataFrame(columns=ORDER_COLUMNS)
        if not orders.empty:
            orders["_prio"] = orders["Prioridad"].map(PRIORITY_ORDER).fillna(9)
            orders = (orders.sort_values(["_prio", "Costo_Total_USD", "EAN"], ascending=[True, False, True])
                      .drop(columns="_prio").reset_index(drop=True))
            orders["Orden"] = np.arange(1, len(orders) + 1)

        price_alerts = self._price_alerts(master, decisions)
        summary = self._summary(orders, skipped, excluded, price_alerts)
        by_priority = self._by_priority(orders)
        by_supplier = self._by_supplier(orders)
        self.logger.info("Órdenes sugeridas: %s | unidades: %s | inversión: $%s | "
                         "EOL excluidos: %s | LLE pendientes: %s | precios bloqueados: %s",
                         f"{len(orders):,}", f"{orders['Cantidad_Sugerida'].sum():,.0f}" if not orders.empty else "0",
                         f"{orders['Costo_Total_USD'].sum():,.0f}" if not orders.empty else "0",
                         len(excluded), skipped["lle_pendiente"], skipped["precio_bloqueado"])
        return OrderBundle(orders=orders, summary=summary, by_priority=by_priority,
                           by_supplier=by_supplier, decisions=decisions,
                           excluded=excluded, price_alerts=price_alerts)

    # ------------------------------------------------------------------ interno
    def _row(self, product: pd.Series, forecast: ProductForecast, decision: OrderDecision) -> dict[str, Any]:
        venta_30 = float(product.get("Venta_30d") or 0.0)
        venta_60 = float(product.get("Venta_60d") or 0.0)
        previous = venta_60 - venta_30
        tendencia = percent_change(venta_30, previous)
        stock = float(decision.stock_actual) if decision.stock_informado else None
        dias = decision.dias_cobertura if decision.stock_informado else None
        justificacion = decision.justificacion
        if bool(product.get("Costo_Estimado")):
            justificacion = "Costo estimado desde el precio de venta. " + justificacion
        if str(product.get("Proveedor", "")).strip().upper() == "SIN ASIGNAR":
            justificacion = "Proveedor sin asignar en el catálogo. " + justificacion
        return {
            "Orden": 0,
            "Prioridad": decision.prioridad,
            "EAN": str(product["EAN"]),
            "Descripcion": str(product.get("Descripcion", "")),
            "Proveedor": str(product.get("Proveedor", "")),
            "ABC": str(product.get("ABC_Valor", "")),
            "XYZ": str(product.get("XYZ", "")),
            "Segmento": str(product.get("Segmento", "")),
            "Stock_Actual": stock,
            "Stock_Informado": bool(decision.stock_informado),
            "Demanda_30d": round(forecast.forecast_30d, 1),
            "Demanda_Cobertura": round(decision.demanda_cobertura, 1),
            "Stock_Seguridad": round(decision.stock_seguridad, 1),
            "Punto_Pedido": round(decision.punto_pedido, 1),
            "Objetivo_Stock": round(decision.objetivo, 1),
            "Cantidad_Sugerida": int(decision.cantidad),
            "Multiplo": int(decision.multiplo),
            "Cajas": round(decision.cantidad / decision.multiplo, 1) if decision.multiplo else None,
            "Costo_Unitario_USD": round(decision.costo_unitario, 2),
            "Descuento_Pct": round(decision.descuento_pct, 1),
            "Costo_Total_USD": round(decision.costo_total, 2),
            "Dias_Cobertura": round(dias, 1) if dias is not None else None,
            "Lead_Time": int(decision.lead_time),
            "Cobertura_Dias": int(decision.cobertura_dias),
            "Venta_30d": round(venta_30, 1),
            "Venta_90d": round(float(product.get("Venta_90d") or 0.0), 1),
            "Tendencia_Pct": round(tendencia, 1) if tendencia is not None else None,
            "Modelo": forecast.model,
            "Confianza": round(float(forecast.confidence), 2),
            "Estado": str(product.get("Estado", "")),
            "Posible_Quiebre": bool(forecast.censoring_risk),
            "Justificacion": justificacion,
            "Estado_Ciclo_Vida": decision.estado_ciclo_vida,
            "Compra_Bloqueada": decision.compra_bloqueada,
            "Motivo_Bloqueo": decision.motivo_bloqueo,
            "Stock_Usado_OC": round(decision.stock_usado_oc, 1),
            "Stock_Comprometido": round(decision.comprometido, 1),
            "Stock_LLE": round(decision.stock_lle, 1),
            "LLE_Estado": decision.lle_estado,
            "Precio_Ref_USD": round(decision.precio_ref, 2),
            "Var_Precio_Pct": round(decision.var_precio_pct, 1),
            "Precio_Techo_USD": round(decision.precio_techo, 2),
            "Precio_Estado": decision.precio_estado,
            "Margen_Nuevo_Pct": round(decision.margen_nuevo_pct, 1) if decision.margen_nuevo_pct is not None else None,
            "Aprobacion_Requerida": bool(decision.aprobacion_requerida),
            "Aprobador": decision.aprobador,
            "Impacto_Precio_USD": round(decision.impacto_precio_usd, 2),
        }

    @staticmethod
    def _excluded(master: pd.DataFrame) -> pd.DataFrame:
        columns = ["EAN", "Descripcion", "Proveedor", "Estado_Ciclo_Vida", "Motivo_Bloqueo",
                   "Accion_EOL", "Stock_Actual", "En_Transito", "Dias_Sin_Venta",
                   "USD_365d", "Costo_USD", "Valor_Stock"]
        if master.empty:
            return pd.DataFrame(columns=columns)
        blocked = master[master["Compra_Bloqueada"].fillna(False).astype(bool)].copy()
        if blocked.empty:
            return pd.DataFrame(columns=columns)
        for col in ("Stock_Actual", "En_Transito", "Dias_Sin_Venta", "USD_365d", "Costo_USD"):
            if col not in blocked.columns:
                blocked[col] = 0.0
            blocked[col] = pd.to_numeric(blocked[col], errors="coerce")
            if col == "Dias_Sin_Venta":
                blocked[col] = blocked[col].fillna(9999.0)
            else:
                blocked[col] = blocked[col].fillna(0.0)
        blocked["Valor_Stock"] = blocked["Stock_Actual"] * blocked["Costo_USD"]
        blocked["Estado_Ciclo_Vida"] = blocked.get("Estado_Ciclo_Vida", "EOL")
        blocked["Motivo_Bloqueo"] = blocked.get("Motivo_Bloqueo", "")
        blocked["Accion_EOL"] = blocked.get("Accion_EOL", "No comprar")
        blocked = blocked.sort_values("Valor_Stock", ascending=False)
        return blocked[columns].reset_index(drop=True)

    @staticmethod
    def _price_alerts(master: pd.DataFrame, decisions: dict[str, OrderDecision]) -> pd.DataFrame:
        columns = ["EAN", "Descripcion", "Proveedor", "Precio_Ref_USD", "Precio_Actual_USD",
                   "Var_Precio_Pct", "Impacto_USD", "Precio_Estado", "Margen_Nuevo_Pct",
                   "Aprobador", "Bloqueada_OC"]
        rows: list[dict[str, Any]] = []
        for ean, decision in decisions.items():
            if not (decision.aprobacion_requerida or decision.precio_estado in {"NARANJA", "ROJO", "MARGEN_BAJO"}):
                continue
            if ean not in master.index:
                continue
            product = master.loc[ean]
            rows.append({
                "EAN": ean,
                "Descripcion": str(product.get("Descripcion", "")),
                "Proveedor": str(product.get("Proveedor", "")),
                "Precio_Ref_USD": round(decision.precio_ref, 2),
                "Precio_Actual_USD": round(decision.precio_actual, 2),
                "Var_Precio_Pct": round(decision.var_precio_pct, 1),
                "Impacto_USD": round(decision.impacto_precio_usd, 2),
                "Precio_Estado": decision.precio_estado,
                "Margen_Nuevo_Pct": round(decision.margen_nuevo_pct, 1) if decision.margen_nuevo_pct is not None else None,
                "Aprobador": decision.aprobador,
                "Bloqueada_OC": decision.motivo in {"PRECIO_FUERA_DE_TECHO", "MARGEN_BAJO"},
            })
        if not rows:
            return pd.DataFrame(columns=columns)
        return pd.DataFrame(rows, columns=columns).sort_values(
            "Impacto_USD", ascending=False).reset_index(drop=True)

    @staticmethod
    def _summary(orders: pd.DataFrame, skipped: dict[str, int],
                 excluded: pd.DataFrame, price_alerts: pd.DataFrame) -> dict[str, Any]:
        if orders.empty:
            return {
                "ordenes": 0, "unidades": 0, "inversion_usd": 0.0, "productos": 0,
                "criticas": 0, "altas": 0, "medias": 0, "bajas": 0,
                "con_stock": 0, "sin_stock": 0,
                "skipped": skipped, "excluidos_eol": int(len(excluded)),
                "valor_excluido_usd": float(excluded["Valor_Stock"].sum()) if not excluded.empty else 0.0,
                "lle_pendientes": int(skipped.get("lle_pendiente", 0)),
                "precio_bloqueados": int(skipped.get("precio_bloqueado", 0)),
                "aprobaciones": int(len(price_alerts)),
            }
        return {
            "ordenes": int(len(orders)),
            "unidades": int(orders["Cantidad_Sugerida"].sum()),
            "inversion_usd": float(orders["Costo_Total_USD"].sum()),
            "productos": int(orders["EAN"].nunique()),
            "criticas": int((orders["Prioridad"] == "CRÍTICA").sum()),
            "altas": int((orders["Prioridad"] == "ALTA").sum()),
            "medias": int((orders["Prioridad"] == "MEDIA").sum()),
            "bajas": int((orders["Prioridad"] == "BAJA").sum()),
            "con_stock": int(orders["Stock_Informado"].sum()),
            "sin_stock": int((~orders["Stock_Informado"]).sum()),
            "skipped": skipped,
            "excluidos_eol": int(len(excluded)),
            "valor_excluido_usd": float(excluded["Valor_Stock"].sum()) if not excluded.empty else 0.0,
            "lle_pendientes": int(skipped.get("lle_pendiente", 0)),
            "precio_bloqueados": int(skipped.get("precio_bloqueado", 0)),
            "aprobaciones": int(len(price_alerts)),
        }

    @staticmethod
    def _by_priority(orders: pd.DataFrame) -> pd.DataFrame:
        if orders.empty:
            return pd.DataFrame(columns=["Prioridad", "Órdenes", "Unidades", "Inversión_USD", "%_Inversión"])
        grouped = orders.groupby("Prioridad").agg(
            Órdenes=("EAN", "count"),
            Unidades=("Cantidad_Sugerida", "sum"),
            Inversión_USD=("Costo_Total_USD", "sum"),
        ).reindex(["CRÍTICA", "ALTA", "MEDIA", "BAJA", "REVISAR"]).dropna(how="all").reset_index()
        total = grouped["Inversión_USD"].sum()
        grouped["%_Inversión"] = grouped["Inversión_USD"] / total * 100 if total else 0.0
        return grouped

    @staticmethod
    def _by_supplier(orders: pd.DataFrame) -> pd.DataFrame:
        if orders.empty:
            return pd.DataFrame(columns=["Proveedor", "Órdenes", "Unidades", "Inversión_USD", "%_Inversión"])
        grouped = orders.groupby("Proveedor").agg(
            Órdenes=("EAN", "count"),
            Unidades=("Cantidad_Sugerida", "sum"),
            Inversión_USD=("Costo_Total_USD", "sum"),
            Lead_Time=("Lead_Time", "max"),
        ).reset_index().sort_values("Inversión_USD", ascending=False)
        total = grouped["Inversión_USD"].sum()
        grouped["%_Inversión"] = grouped["Inversión_USD"] / total * 100 if total else 0.0
        grouped["Lead_Time"] = grouped["Lead_Time"].astype(int)
        return grouped
