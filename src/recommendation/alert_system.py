"""Sistema de alertas accionables para el área de compras."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from models.forecast_engine import ProductForecast
from recommendation.order_manager import OrderBundle
from utils.config import Config
from utils.helpers import percent_change

SEVERITY_ORDER = {"CRÍTICA": 0, "ALTA": 1, "MEDIA": 2, "BAJA": 3}
MAX_PER_TYPE = 50


@dataclass
class AlertBundle:
    alerts: pd.DataFrame
    summary: pd.DataFrame


class AlertSystem:
    def __init__(self, config: Config, logger: logging.Logger | None = None):
        self.config = config
        self.logger = logger or logging.getLogger("compras")
        self.critical_days = int(config.get("alerts.critical_stock_days", 7))
        self.low_days = int(config.get("alerts.low_stock_days", 15))
        self.excess_days = int(config.get("alerts.excess_stock_days", 540))
        self.trend_pct = float(config.get("alerts.trend_change_pct", 30))
        self.trend_min_units = float(config.get("alerts.trend_min_units", 20))
        self.big_purchase = float(config.get("alerts.big_purchase_usd", 10000))
        self.uncertainty_wape = float(config.get("alerts.uncertainty_wape", 60))

    # ------------------------------------------------------------------ público
    def generate(self, orders: OrderBundle, products: pd.DataFrame,
                 forecasts: dict[str, ProductForecast],
                 segmentation: pd.DataFrame,
                 quality: pd.DataFrame | None = None) -> AlertBundle:
        master = products.merge(
            segmentation[["EAN", "ABC_Valor", "XYZ", "Segmento"]],
            on="EAN", how="left",
        ).set_index("EAN", drop=False)

        rows: list[dict[str, Any]] = []
        now = datetime.now().strftime("%d/%m/%Y")

        def add(tipo: str, severidad: str, ean: str, producto: str, proveedor: str,
                detalle: str, accion: str, responsable: str, impacto: float | None = None) -> None:
            rows.append({
                "Fecha": now, "Tipo": tipo, "Severidad": severidad, "EAN": ean,
                "Producto": producto, "Proveedor": proveedor, "Detalle": detalle,
                "Accion": accion, "Responsable": responsable,
                "Impacto_USD": round(float(impacto), 2) if impacto is not None and not pd.isna(impacto) else None,
            })

        # ---------------- 1. alertas de stock con inventario informado
        for _, row in orders.orders.iterrows():
            coverage = row.get("Dias_Cobertura")
            if pd.isna(coverage):
                continue
            ean = str(row["EAN"])
            forecast = forecasts.get(ean)
            impacto = self._revenue_at_risk(forecast, master.loc[ean] if ean in master.index else None)
            if coverage <= self.critical_days:
                add("STOCK CRÍTICO", "CRÍTICA", ean, row["Descripcion"], row["Proveedor"],
                    f"Stock ACT−COM {row['Stock_Usado_OC']:.0f} u cubre {coverage:.1f} días (crítico < {self.critical_days} d). "
                    f"Lead time {row['Lead_Time']} d.",
                    f"Comprar urgente: {row['Cantidad_Sugerida']:.0f} unidades", "Gerente de Compras", impacto)
            elif coverage <= row["Lead_Time"]:
                add("QUIEBRE ANTES DE ENTREGA", "CRÍTICA", ean, row["Descripcion"], row["Proveedor"],
                    f"Stock ACT−COM {row['Stock_Usado_OC']:.0f} u cubre {coverage:.1f} días, menos que el lead time de {row['Lead_Time']} d.",
                    f"Adelantar pedido de {row['Cantidad_Sugerida']:.0f} unidades", "Gerente de Compras", impacto)
            elif coverage <= self.low_days:
                add("STOCK BAJO", "ALTA", ean, row["Descripcion"], row["Proveedor"],
                    f"Stock ACT−COM {row['Stock_Usado_OC']:.0f} u cubre {coverage:.1f} días.",
                    f"Programar compra de {row['Cantidad_Sugerida']:.0f} unidades", "Analista de Compras", impacto)

        # Productos con stock informado y sin orden: pueden estar en sobre-stock
        order_eans = set(orders.orders["EAN"].astype(str)) if not orders.orders.empty else set()
        for ean, forecast in forecasts.items():
            if ean not in master.index:
                continue
            product = master.loc[ean]
            if not bool(product.get("Stock_Informado", False)):
                continue
            daily = forecast.daily_for_days(30)
            stock = max(0.0, float(product.get("Stock_Actual") or 0.0)
                        - float(product.get("Comprometido") or 0.0))
            if daily <= 0:
                coverage = 9999.0 if stock > 0 else 0.0
            else:
                coverage = stock / daily
            if coverage > self.excess_days:
                excess_units = max(stock - daily * self.excess_days, 0.0)
                impacto = excess_units * float(product.get("Costo_USD") or 0.0)
                add("EXCESO DE INVENTARIO", "ALTA" if impacto > 500 else "MEDIA", ean,
                    product["Descripcion"], product["Proveedor"],
                    f"Stock {stock:.0f} u cubre {coverage:,.0f} días (>{self.excess_days} d). "
                    f"Exceso estimado {excess_units:,.0f} u.",
                    "Suspender compras y evaluar liquidación/traslado", "Gerente de Inventario", impacto)
            elif ean not in order_eans and coverage < self.low_days:
                impacto = self._revenue_at_risk(forecast, product)
                add("STOCK BAJO SIN ORDEN", "ALTA", ean, product["Descripcion"], product["Proveedor"],
                    f"Stock {stock:.0f} u cubre {coverage:.1f} días y no se generó orden (revisar parámetros).",
                    "Revisar mínimo/múltiplo y generar pedido", "Analista de Compras", impacto)

        # ---------------- 1b. bloqueos de ciclo de vida (EOL / obsoleto / liquidación)
        excluded = getattr(orders, "excluded", pd.DataFrame())
        if excluded is not None and not excluded.empty:
            for _, row in excluded.head(MAX_PER_TYPE * 2).iterrows():
                add("PRODUCTO BLOQUEADO EOL", "ALTA", str(row["EAN"]), row["Descripcion"],
                    row["Proveedor"],
                    f"Estado {row['Estado_Ciclo_Vida']}: {row['Motivo_Bloqueo']}. "
                    f"Stock {row['Stock_Actual']:.0f} u; valor ${row['Valor_Stock']:,.0f}.",
                    row["Accion_EOL"] if row["Accion_EOL"] else "No comprar; liquidar o excluir",
                    "Gerente de Inventario", row["Valor_Stock"])

        # ---------------- 1c. LLE pendiente: no suma, pero bloquea duplicidad
        for ean, decision in orders.decisions.items():
            if decision.motivo != "LLE_PENDIENTE" or ean not in master.index:
                continue
            product = master.loc[ean]
            add("LLE PENDIENTE / POSIBLE DUPLICIDAD", "ALTA", ean, product["Descripcion"],
                product["Proveedor"],
                f"Stock ACT−COM {decision.stock_usado_oc:.0f} u y {decision.stock_lle:.0f} u en tránsito "
                f"({decision.lle_estado}). No se emitió OC para no duplicar.",
                "Validar OC/ETA del tránsito; si no llega, aprobar excepción",
                "Gerente de Compras", self._revenue_at_risk(forecasts.get(ean), product))

        # ---------------- 1d. control de precios
        price_alerts = getattr(orders, "price_alerts", pd.DataFrame())
        if price_alerts is not None and not price_alerts.empty:
            for _, row in price_alerts.head(MAX_PER_TYPE * 2).iterrows():
                estado = str(row["Precio_Estado"])
                sev = "CRÍTICA" if estado == "ROJO" else "ALTA"
                tipo = "PRECIO FUERA DE TECHO" if estado == "ROJO" else "PRECIO REQUIERE APROBACIÓN"
                add(tipo, sev, str(row["EAN"]), row["Descripcion"], row["Proveedor"],
                    f"Costo ref. ${row['Precio_Ref_USD']:.2f} → actual ${row['Precio_Actual_USD']:.2f} "
                    f"({row['Var_Precio_Pct']:+.1f}%, {estado}). Impacto anual ${row['Impacto_USD']:,.0f}.",
                    f"Negociar / aprobar con {row['Aprobador']}; no comprar sobre el techo",
                    row["Aprobador"], row["Impacto_USD"])

        # ---------------- 2. posible quiebre por falta de ventas
        for ean, forecast in forecasts.items():
            if not forecast.censoring_risk or ean not in master.index:
                continue
            product = master.loc[ean]
            add("POSIBLE QUIEBRE", "ALTA", ean, product["Descripcion"], product["Proveedor"],
                f"Sin ventas {forecast.gap_days} días; el patrón sugiere desabastecimiento o descontinuación.",
                "Verificar disponibilidad en almacén antes de comprar", "Analista de Compras",
                self._revenue_at_risk(forecast, product))

        # ---------------- 3. cambios de tendencia
        trend_candidates = master[master["Estado"].isin(["ACTIVO", "NUEVO", "EN_RIESGO"])].copy()
        trend_candidates["_prev30"] = trend_candidates["Venta_60d"] - trend_candidates["Venta_30d"]
        trend_candidates = trend_candidates[trend_candidates["_prev30"] >= self.trend_min_units]
        trend_candidates["_trend"] = trend_candidates.apply(
            lambda r: percent_change(r["Venta_30d"], r["_prev30"]), axis=1)
        trend_candidates = trend_candidates[trend_candidates["_trend"].notna()]
        trend_candidates["_abs"] = trend_candidates["_trend"].abs()
        trend_candidates = trend_candidates[trend_candidates["_abs"] >= self.trend_pct]
        trend_candidates = trend_candidates.sort_values("_abs", ascending=False).head(MAX_PER_TYPE)
        for _, row in trend_candidates.iterrows():
            direction = "subió" if row["_trend"] > 0 else "cayó"
            add("CAMBIO DE TENDENCIA", "MEDIA", str(row["EAN"]), row["Descripcion"], row["Proveedor"],
                f"Ventas 30 d {direction} {abs(row['_trend']):.1f}% vs 30 d previos "
                f"({row['_prev30']:.0f} u → {row['Venta_30d']:.0f} u).",
                "Investigar causa y ajustar compra" if row["_trend"] > 0 else "Revisar sobre-stock y compras",
                "Analista de Demanda", row.get("USD_30d"))

        # ---------------- 4. productos sin venta reciente / descontinuados
        inactive = master[(master["Estado"] == "EN_RIESGO") & (master["USD_365d"] > 0)]
        inactive = inactive.sort_values("USD_365d", ascending=False).head(MAX_PER_TYPE)
        for _, row in inactive.iterrows():
            add("SIN VENTAS RECIENTES", "MEDIA", str(row["EAN"]), row["Descripcion"], row["Proveedor"],
                f"Última venta hace {row['Dias_Sin_Venta']:.0f} días. Ventas últimos 12 meses: ${row['USD_365d']:,.0f}.",
                "Verificar si sigue vigente; no comprar hasta confirmar", "Gerente de Producto", row["USD_365d"])

        discontinued = master[(master["Estado"] == "INACTIVO") & (master["USD_365d"] > 0)]
        discontinued = discontinued.sort_values("USD_365d", ascending=False).head(MAX_PER_TYPE)
        for _, row in discontinued.iterrows():
            add("POSIBLE DESCONTINUACIÓN", "MEDIA", str(row["EAN"]), row["Descripcion"], row["Proveedor"],
                f"Sin ventas {row['Dias_Sin_Venta']:.0f} días. Ventas últimos 12 meses: ${row['USD_365d']:,.0f}.",
                "Retirar del catálogo activo o revisar con ventas", "Gerente de Producto", row["USD_365d"])

        # ---------------- 5. productos nuevos
        new_products = master[master["Estado"] == "NUEVO"].sort_values("USD_30d", ascending=False).head(MAX_PER_TYPE)
        for _, row in new_products.iterrows():
            add("PRODUCTO NUEVO", "BAJA", str(row["EAN"]), row["Descripcion"], row["Proveedor"],
                f"Primera venta hace {row['Historial_Dias']:.0f} días; historial insuficiente.",
                "Monitorear y comprar cantidades conservadoras", "Gerente de Producto", row["USD_30d"])

        # ---------------- 6. incertidumbre del pronóstico
        uncertainty = []
        for ean, forecast in forecasts.items():
            if ean not in master.index or pd.isna(forecast.wape):
                continue
            if forecast.wape > self.uncertainty_wape:
                product = master.loc[ean]
                uncertainty.append((float(product.get("USD_365d") or 0.0), ean, forecast, product))
        uncertainty.sort(reverse=True, key=lambda item: item[0])
        for usd, ean, forecast, product in uncertainty[:MAX_PER_TYPE]:
            add("ALTA INCERTIDUMBRE", "BAJA", ean, product["Descripcion"], product["Proveedor"],
                f"WAPE {forecast.wape:.0f}% en backtesting. Pronóstico poco confiable.",
                "Revisar manualmente y usar stock de seguridad", "Analista de Demanda", usd)

        # ---------------- 7. compras grandes por proveedor
        for _, row in orders.by_supplier.iterrows():
            if row["Inversión_USD"] >= self.big_purchase:
                add("COMPRA GRANDE", "MEDIA", "N/A", f"Pedido a {row['Proveedor']}", row["Proveedor"],
                    f"${row['Inversión_USD']:,.0f} en {row['Órdenes']:.0f} productos ({row['Unidades']:,.0f} u).",
                    "Negociar descuento y programar entregas", "Gerente de Compras", row["Inversión_USD"])

        # ---------------- 8. datos faltantes (agregado)
        if not master.empty:
            no_stock = master[(~master["Stock_Informado"].astype(bool)) & (master["Estado"] != "INACTIVO")]
            if len(no_stock):
                for supplier, part in no_stock.groupby("Proveedor"):
                    add("INVENTARIO NO INFORMADO", "ALTA" if part["USD_365d"].sum() > 10000 else "MEDIA",
                        "N/A", f"{len(part):.0f} productos de {supplier}", supplier,
                        f"{len(part):.0f} productos activos sin stock informado (${part['USD_365d'].sum():,.0f} de ventas 12 m).",
                        "Cargar inventario actual para calcular cobertura real", "Gerente de Inventario",
                        part["USD_365d"].sum())

            # Productos A/B sin stock cuya compra mínima excede la cobertura máxima
            min_case = []
            for ean, decision in orders.decisions.items():
                if decision.motivo != "MAX_COBERTURA" or ean not in master.index:
                    continue
                product = master.loc[ean]
                if str(product.get("ABC_Valor", "C")) in {"A", "B"} and not bool(product.get("Stock_Informado", False)):
                    min_case.append(product)
            if min_case:
                part = pd.DataFrame(min_case)
                add("COMPRA MÍNIMA EXCEDE COBERTURA", "MEDIA", "N/A",
                    f"{len(part):.0f} productos A/B sin stock informado", "VARIOS",
                    f"Un bulto de compra (24 u) cubre más de 120 días en {len(part):.0f} productos A/B "
                    f"(${part['USD_365d'].sum():,.0f} de ventas 12 m). No se generó orden automática.",
                    "Definir stock mínimo, compra por unidad o cargar inventario", "Gerente de Compras",
                    part["USD_365d"].sum())

            incomplete = master[(~master["Catalogo_Completo"].astype(bool)) | (master["Proveedor"].str.upper() == "SIN ASIGNAR")
                                | (master["Costo_Estimado"].astype(bool))]
            if len(incomplete):
                add("CATÁLOGO INCOMPLETO", "ALTA", "N/A",
                    f"{len(incomplete):.0f} productos sin proveedor/costo confiable", "VARIOS",
                    f"El catálogo maestro no cubre {len(incomplete):.0f} productos "
                    f"(${incomplete['USD_365d'].sum():,.0f} de ventas 12 m). Costos estimados desde el precio de venta.",
                    "Completar catálogo EAN → proveedor → costo", "Gerente de Compras",
                    incomplete["USD_365d"].sum())

        alerts = pd.DataFrame(rows, columns=["Fecha", "Tipo", "Severidad", "EAN", "Producto",
                                             "Proveedor", "Detalle", "Accion", "Responsable", "Impacto_USD"])
        if not alerts.empty:
            # Evitar cientos de alertas del mismo tipo: conservar las de mayor impacto
            limited = []
            for tipo, part in alerts.groupby("Tipo", sort=False):
                if len(part) <= MAX_PER_TYPE:
                    limited.append(part)
                    continue
                keep = part.sort_values("Impacto_USD", ascending=False).head(MAX_PER_TYPE)
                rest = part.drop(keep.index)
                limited.append(keep)
                limited.append(pd.DataFrame([{
                    "Fecha": now, "Tipo": tipo, "Severidad": "MEDIA", "EAN": "N/A",
                    "Producto": f"{len(rest):,} alertas adicionales de {tipo}", "Proveedor": "VARIOS",
                    "Detalle": f"Se muestran las {MAX_PER_TYPE} de mayor impacto; el resto está en el detalle completo.",
                    "Accion": "Revisar el resto en el Excel o filtrar por tipo", "Responsable": "Analista de Compras",
                    "Impacto_USD": round(float(rest["Impacto_USD"].fillna(0).sum()), 2),
                }]))
            alerts = pd.concat(limited, ignore_index=True)
            alerts["_sev"] = alerts["Severidad"].map(SEVERITY_ORDER).fillna(9)
            alerts["_impact"] = alerts["Impacto_USD"].fillna(0)
            alerts = (alerts.sort_values(["_sev", "_impact"], ascending=[True, False])
                      .drop(columns=["_sev", "_impact"]).reset_index(drop=True))

        summary = self._summary(alerts)
        self.logger.info("Alertas generadas: %s (%s)", len(alerts),
                         ", ".join(f"{k}: {v}" for k, v in summary["por_severidad"].items()))
        return AlertBundle(alerts=alerts, summary=summary)

    # ------------------------------------------------------------------ interno
    @staticmethod
    def _revenue_at_risk(forecast: ProductForecast | None, product: pd.Series | None) -> float:
        if forecast is None or product is None:
            return 0.0
        price = float(product.get("Precio_Prom_USD") or 0.0)
        if price <= 0:
            return 0.0
        return max(forecast.forecast_30d, 0.0) * price

    @staticmethod
    def _summary(alerts: pd.DataFrame) -> dict[str, Any]:
        if alerts.empty:
            return {"total": 0, "por_severidad": {}, "por_tipo": {}, "impacto_usd": 0.0}
        return {
            "total": int(len(alerts)),
            "por_severidad": {k: int(v) for k, v in alerts["Severidad"].value_counts().items()},
            "por_tipo": {k: int(v) for k, v in alerts["Tipo"].value_counts().items()},
            "impacto_usd": float(alerts["Impacto_USD"].fillna(0).sum()),
        }
