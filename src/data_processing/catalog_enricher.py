"""Construcción del maestro de productos y enriquecimiento del catálogo.

El catálogo real puede estar incompleto (en los datos de ejemplo cubre una fracción
de las ventas). Este módulo garantiza que **todo producto vendido** exista en el
maestro, marcando explícitamente qué datos son reales y cuáles estimados, para que
las recomendaciones nunca se bloqueen por un catálogo incompleto.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from utils.config import Config
from utils.helpers import to_number


@dataclass
class EnrichedCatalog:
    products: pd.DataFrame
    quality: pd.DataFrame
    summary: dict[str, Any]


class CatalogEnricher:
    def __init__(self, config: Config, logger: logging.Logger | None = None):
        self.config = config
        self.logger = logger or logging.getLogger("compras")

    # ------------------------------------------------------------------ público
    def build(self, sales: pd.DataFrame, catalog: pd.DataFrame, stock: pd.DataFrame,
              as_of: pd.Timestamp) -> EnrichedCatalog:
        cfg = self.config
        margin = float(cfg.get("inventory.default_margin_pct", 15.0)) / 100.0
        default_multiple = int(cfg.get("inventory.default_multiplier", 24))
        default_lead = int(cfg.get("inventory.default_lead_time", 30))

        # ---------------- estadísticas por producto
        stats = self._sales_stats(sales, as_of)

        # ---------------- base de catálogo
        base = catalog.copy()
        catalog_eans = set(base["EAN"].astype(str)) if not base.empty else set()
        for col in ("EAN", "Descripcion", "Proveedor", "Categoria", "Subcategoria"):
            if col not in base.columns:
                base[col] = ""
        if base.empty:
            base = pd.DataFrame(columns=["EAN", "Descripcion", "Proveedor", "Categoria", "Subcategoria",
                                         "Costo_USD", "Multiplo_Compra", "Lead_Time",
                                         "Stock_Minimo", "Stock_Maximo", "Costo_Estimado",
                                         "Catalogo_Completo", "Origen"])

        products = base.merge(stats, on="EAN", how="outer")
        products["EAN"] = products["EAN"].astype(str)
        for col in ("Descripcion", "Proveedor", "Categoria", "Subcategoria"):
            products[col] = products[col].fillna("").astype(str).str.strip()
        products["Proveedor"] = products["Proveedor"].replace({"": "SIN ASIGNAR"})
        products["Categoria"] = products["Categoria"].replace({"": "SIN CLASIFICAR"})
        products["Descripcion"] = products["Descripcion"].replace({"": ""})

        for col in ("Costo_USD", "Multiplo_Compra", "Lead_Time", "Stock_Minimo", "Stock_Maximo"):
            if col not in products.columns:
                products[col] = np.nan
            products[col] = to_number(products[col])

        if "Origen" not in products.columns:
            products["Origen"] = "CATALOGO"
        products["Origen"] = products["Origen"].fillna("CATALOGO")
        products.loc[~products["EAN"].isin(catalog_eans), "Origen"] = "ESTIMADO"
        if "Costo_Estimado" not in products.columns:
            products["Costo_Estimado"] = False
        products["Costo_Estimado"] = products["Costo_Estimado"].fillna(False).astype(bool)

        # ---------------- completar descripción y proveedor
        products.loc[products["Descripcion"] == "", "Descripcion"] = (
            "PRODUCTO SIN CATALOGO " + products.loc[products["Descripcion"] == "", "EAN"]
        )
        products.loc[products["Proveedor"].str.upper() == "SIN ASIGNAR", "Proveedor"] = "SIN ASIGNAR"

        # ---------------- estimar costo faltante
        need_cost = products["Costo_USD"].isna() | (products["Costo_USD"] <= 0)
        price = products["Precio_Prom_USD"].where(products["Precio_Prom_USD"] > 0)
        estimated = (price * (1.0 - margin)).round(4)
        estimated = estimated.where(estimated >= 0.01, np.nan).fillna(1.0)
        products.loc[need_cost, "Costo_USD"] = estimated.loc[need_cost]
        products.loc[need_cost, "Costo_Estimado"] = True

        # ---------------- métricas de precio (Profit las trae; Excel usa costo actual)
        price_defaults = {
            "Costo_Ultimo_USD": products["Costo_USD"],
            "Costo_Ref_90d": products["Costo_USD"],
            "Var_Precio_Pct": np.nan,
            "Impacto_Precio_USD": 0.0,
            "Precio_Techo_USD": np.nan,
            "Precio_Estado": "SIN_REFERENCIA",
            "Margen_Nuevo_Pct": np.nan,
            "Requiere_Aprobacion_Precio": False,
            "Demanda_Anual_Unidades": np.nan,
            "Compras_Historico": 0,
        }
        for col, default in price_defaults.items():
            if col not in products.columns:
                products[col] = default
        products["Requiere_Aprobacion_Precio"] = products["Requiere_Aprobacion_Precio"].fillna(False).astype(bool)
        products["Compras_Historico"] = to_number(products["Compras_Historico"]).fillna(0).astype(int)
        for col in ("Costo_Ultimo_USD", "Costo_Ref_90d", "Var_Precio_Pct", "Impacto_Precio_USD",
                    "Precio_Techo_USD", "Margen_Nuevo_Pct", "Demanda_Anual_Unidades"):
            products[col] = to_number(products[col])
        products["Precio_Estado"] = products["Precio_Estado"].fillna("SIN_REFERENCIA").astype(str)
        # Margen nuevo: el precio de venta viene de las estadísticas de ventas.
        if "Precio_Prom_USD" in products.columns:
            price = to_number(products["Precio_Prom_USD"])
            cost = to_number(products["Costo_Ultimo_USD"])
            products["Margen_Nuevo_Pct"] = np.where(
                price > 0, (price - cost) / price * 100.0, np.nan)

        # ---------------- defaults logísticos
        products["Multiplo_Compra"] = products["Multiplo_Compra"].fillna(default_multiple)
        products.loc[products["Multiplo_Compra"] <= 0, "Multiplo_Compra"] = default_multiple
        products["Lead_Time"] = products["Lead_Time"].fillna(default_lead)
        products.loc[products["Lead_Time"] <= 0, "Lead_Time"] = default_lead

        # ---------------- overrides del usuario (config/productos.csv)
        products = self._apply_product_overrides(products)

        # ---------------- stock
        if stock is not None and not stock.empty:
            products = products.merge(
                stock[["EAN", "Stock_Actual", "En_Transito", "Comprometido", "Stock_Disponible"]],
                on="EAN", how="left",
            )
            products["Stock_Informado"] = products["Stock_Actual"].notna()
            products["Stock_Actual"] = products["Stock_Actual"].fillna(0.0)
            products["En_Transito"] = products["En_Transito"].fillna(0.0)
            products["Comprometido"] = products["Comprometido"].fillna(0.0)
            products["Stock_Disponible"] = products["Stock_Disponible"].fillna(0.0)
        else:
            products["Stock_Actual"] = np.nan
            products["En_Transito"] = 0.0
            products["Comprometido"] = 0.0
            products["Stock_Disponible"] = np.nan
            products["Stock_Informado"] = False

        # ---------------- estado de ciclo de vida
        products = self._lifecycle(products, as_of)

        # ---------------- marcas finales
        products["Catalogo_Completo"] = (
            (products["Descripcion"] != "")
            & (products["Proveedor"].str.upper() != "SIN ASIGNAR")
            & (products["Costo_USD"] > 0)
            & (products["Origen"].isin(["CATALOGO", "PROFIT"]))
        )
        products["Proveedor_Clave"] = products["Proveedor"].str.upper().str.strip()

        products = products.sort_values("USD_365d", ascending=False, na_position="last").reset_index(drop=True)

        # ---------------- proveedores nuevos al CSV
        suppliers = sorted(products["Proveedor"].dropna().unique().tolist())
        added = self.config.ensure_provider_file(suppliers)

        # ---------------- calidad
        quality = self._quality(products, sales, stock, as_of)
        if added:
            quality = pd.concat([quality, pd.DataFrame([{
                "Chequeo": "Proveedores agregados a config/proveedores.csv",
                "Resultado": str(len(added)),
                "Detalle": ", ".join(added[:10]) + ("..." if len(added) > 10 else ""),
                "Severidad": "INFO",
            }])], ignore_index=True)

        summary = {
            "productos": int(len(products)),
            "con_catalogo": int((products["Origen"].isin(["CATALOGO", "PROFIT"])).sum()),
            "sin_catalogo": int((~products["Origen"].isin(["CATALOGO", "PROFIT"])).sum()),
            "con_proveedor": int((products["Proveedor"].str.upper() != "SIN ASIGNAR").sum()),
            "sin_proveedor": int((products["Proveedor"].str.upper() == "SIN ASIGNAR").sum()),
            "costo_estimado": int(products["Costo_Estimado"].sum()),
            "stock_informado": int(products["Stock_Informado"].sum()),
            "activos": int((products["Estado"] == "ACTIVO").sum()),
            "proveedores": int(products["Proveedor"].nunique()),
            "costo_estimado_usd": float(products["Costo_USD"].sum()),
            "bloqueados_eol": int(products["Compra_Bloqueada"].sum()),
            "eol": int((products["Estado_Ciclo_Vida"] == "EOL").sum()),
            "obsoletos": int((products["Estado_Ciclo_Vida"] == "OBSOLETO").sum()),
            "liquidacion": int((products["Estado_Ciclo_Vida"] == "LIQUIDACION").sum()),
            "precios_aprobacion": int(products["Requiere_Aprobacion_Precio"].sum()),
        }
        self.logger.info("Maestro de productos: %s (%s en catálogo, %s sin catálogo, %s con stock informado)",
                         f"{summary['productos']:,}", f"{summary['con_catalogo']:,}",
                         f"{summary['sin_catalogo']:,}", f"{summary['stock_informado']:,}")
        return EnrichedCatalog(products=products, quality=quality, summary=summary)

    # ------------------------------------------------------------------ interno
    @staticmethod
    def _sales_stats(sales: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
        stats = sales.groupby("EAN").agg(
            Primera_Venta=("Fecha", "min"),
            Ultima_Venta=("Fecha", "max"),
            Transacciones=("Cantidad", "size"),
            Unidades_Total=("Cantidad", "sum"),
            USD_Total=("Monto_USD", "sum"),
        ).reset_index()

        def window(days: int) -> pd.DataFrame:
            cutoff = as_of - pd.Timedelta(days=days)
            part = sales[sales["Fecha"] > cutoff]
            out = part.groupby("EAN").agg(**{
                f"Venta_{days}d": ("Cantidad", "sum"),
                f"USD_{days}d": ("Monto_USD", "sum"),
            }).reset_index()
            return out

        for days in (7, 30, 60, 90, 180, 365):
            stats = stats.merge(window(days), on="EAN", how="left")

        for days in (7, 30, 60, 90, 180, 365):
            stats[f"Venta_{days}d"] = stats[f"Venta_{days}d"].fillna(0)
            stats[f"USD_{days}d"] = stats[f"USD_{days}d"].fillna(0)

        # Precio promedio ponderado (últimos 365 días) y último precio observado
        recent = sales[sales["Fecha"] > as_of - pd.Timedelta(days=365)]
        agg = recent.groupby("EAN").agg(_unidades=("Cantidad", "sum"), _usd=("Monto_USD", "sum"))
        price = (agg["_usd"] / agg["_unidades"].replace(0, np.nan)).rename("Precio_Prom_USD").reset_index()
        last_price = (recent.sort_values("Fecha").groupby("EAN")["Precio_USD"].last()
                      .rename("Precio_Ultimo_USD").reset_index())
        stats = stats.merge(price, on="EAN", how="left").merge(last_price, on="EAN", how="left")
        return stats

    def _apply_product_overrides(self, products: pd.DataFrame) -> pd.DataFrame:
        overrides = self.config.product_overrides
        products["Activo_Override"] = True
        products["Estado_Ciclo_Vida_Manual"] = ""
        products["Compra_Bloqueada_Manual"] = False
        products["Fecha_EOL"] = ""
        products["Sustituto_EAN"] = ""
        if not overrides:
            return products
        mapping = {
            "Multiplo_Compra": int, "Lead_Time": int, "Stock_Minimo": float,
            "Stock_Maximo": float, "Costo_USD": float, "Proveedor": str,
            "Descripcion": str, "Categoria": str, "Subcategoria": str,
            "Sustituto_EAN": str, "Fecha_EOL": str, "Dias_Para_Vencer": float,
        }
        applied = 0
        for ean, values in overrides.items():
            mask = products["EAN"] == str(ean)
            if not mask.any():
                continue
            if "Activo" in values and values["Activo"] not in ("", None):
                activo = str(values["Activo"]).strip().upper() in {"SI", "SÍ", "S", "YES", "TRUE", "1", "X"}
                products.loc[mask, "Activo_Override"] = activo
            if "Estado_Ciclo_Vida" in values and values["Estado_Ciclo_Vida"] not in ("", None):
                products.loc[mask, "Estado_Ciclo_Vida_Manual"] = str(values["Estado_Ciclo_Vida"]).strip().upper()
            if "Compra_Bloqueada" in values and values["Compra_Bloqueada"] not in ("", None):
                products.loc[mask, "Compra_Bloqueada_Manual"] = str(values["Compra_Bloqueada"]).strip().upper() in {
                    "SI", "SÍ", "S", "YES", "TRUE", "1", "X"}
            for key, caster in mapping.items():
                if key not in values or values[key] in ("", None):
                    continue
                try:
                    value = caster(values[key])
                except (TypeError, ValueError):
                    continue
                products.loc[mask, key] = value
                applied += 1
                if key == "Costo_USD":
                    products.loc[mask, "Costo_Estimado"] = False
        if applied:
            self.logger.info("Overrides de producto aplicados: %s", applied)
        return products

    def _lifecycle(self, products: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
        active_days = int(self.config.get("analysis.active_days", 60))
        inactive_days = int(self.config.get("analysis.inactive_days", 120))
        new_days = int(self.config.get("analysis.new_product_days", 90))
        observation_days = int(self.config.get("lifecycle.observation_days", 90))
        obsolete_days = int(self.config.get("lifecycle.obsolete_days", 180))
        liquidation_days = int(self.config.get("lifecycle.liquidation_days", 180))
        block_states = {str(s).upper() for s in self.config.get("lifecycle.block_states", [
            "EOL", "OBSOLETO", "LIQUIDACION", "EXCLUIDO", "AGOTADO"])}

        last = pd.to_datetime(products["Ultima_Venta"], errors="coerce")
        first = pd.to_datetime(products["Primera_Venta"], errors="coerce")
        products["Dias_Sin_Venta"] = (as_of - last).dt.days
        products["Historial_Dias"] = (last - first).dt.days

        estado = pd.Series("INACTIVO", index=products.index, dtype=object)
        estado[products["Dias_Sin_Venta"] <= inactive_days] = "EN_RIESGO"
        estado[products["Dias_Sin_Venta"] <= active_days] = "ACTIVO"
        nuevo = (products["Historial_Dias"] <= new_days) & (products["Dias_Sin_Venta"] <= active_days)
        estado[nuevo] = "NUEVO"
        products["Estado"] = estado

        # ---------------- ciclo de vida del artículo (EOL / obsoleto / liquidación)
        stock = to_number(products["Stock_Actual"]).fillna(0.0) if "Stock_Actual" in products else 0.0
        stock = stock if isinstance(stock, pd.Series) else pd.Series(stock, index=products.index)
        dias_vencer = (to_number(products["Dias_Para_Vencer"])
                       if "Dias_Para_Vencer" in products.columns
                       else pd.Series(np.nan, index=products.index))
        dias_sin_venta = products["Dias_Sin_Venta"].fillna(10 ** 6)
        es_nuevo = products["Estado"].eq("NUEVO")

        ciclo = pd.Series("ACTIVO", index=products.index, dtype=object)
        ciclo[(dias_vencer <= liquidation_days) & (stock > 0)] = "LIQUIDACION"
        ciclo[(dias_sin_venta >= obsolete_days) & (~es_nuevo) & (stock > 0)] = "OBSOLETO"
        obs = (dias_sin_venta >= observation_days) & (~es_nuevo) & ~ciclo.isin(["LIQUIDACION", "OBSOLETO"])
        ciclo[obs] = "EN_OBSERVACION"

        manual = products.get("Estado_Ciclo_Vida_Manual", pd.Series("", index=products.index))
        manual = manual.fillna("").astype(str).str.upper()
        if "Fecha_EOL" in products.columns:
            fecha_eol = pd.to_datetime(products["Fecha_EOL"], errors="coerce")
            ciclo[fecha_eol.notna() & (fecha_eol <= as_of)] = "EOL"
        for state in ("EXCLUIDO", "EOL", "LIQUIDACION", "OBSOLETO", "AGOTADO", "EN_OBSERVACION"):
            ciclo[manual == state] = state
        ciclo[ciclo.isin(["EOL", "OBSOLETO"]) & (stock <= 0)] = "AGOTADO"
        products["Estado_Ciclo_Vida"] = ciclo

        manual_block = products.get("Compra_Bloqueada_Manual", pd.Series(False, index=products.index))
        products["Compra_Bloqueada"] = ciclo.isin(block_states) | manual_block.fillna(False).astype(bool)
        motives = {
            "EOL": "Fin de vida útil / retirado del mercado",
            "OBSOLETO": "Sin ventas y con stock inmovilizado",
            "LIQUIDACION": "Próximo a vencer o en liquidación",
            "EXCLUIDO": "Excluido por decisión comercial",
            "AGOTADO": "Fin de vida útil sin stock",
            "EN_OBSERVACION": "Sin ventas recientes; revisar antes de comprar",
        }
        products["Motivo_Bloqueo"] = ciclo.map(motives).fillna("")
        products["Accion_EOL"] = ciclo.map({
            "EOL": "Liquidar, migrar a sustituto y no comprar",
            "OBSOLETO": "Liquidar/transferir; no comprar",
            "LIQUIDACION": "FEFO + descuento; no comprar",
            "EXCLUIDO": "Mantener excluido de OC",
            "AGOTADO": "Cerrar; no reponer",
            "EN_OBSERVACION": "Revisar demanda antes de comprar",
        }).fillna("")
        products["Sin_Venta_Reciente"] = (products["Dias_Sin_Venta"] > active_days).fillna(False)
        return products

    def _quality(self, products: pd.DataFrame, sales: pd.DataFrame, stock: pd.DataFrame,
                 as_of: pd.Timestamp) -> pd.DataFrame:
        total_usd = sales["Monto_USD"].sum()
        catalog_usd = products.loc[products["Origen"].isin(["CATALOGO", "PROFIT"]), "USD_Total"].sum()
        rows = [
            {"Chequeo": "Maestro: productos totales", "Resultado": f"{len(products):,}",
             "Detalle": "Ventas + catálogo", "Severidad": "INFO"},
            {"Chequeo": "Maestro: productos sin catálogo", "Resultado": f"{int((~products['Origen'].isin(['CATALOGO', 'PROFIT'])).sum()):,}",
             "Detalle": "Se crean con proveedor y costo estimados", "Severidad": "ALTA"},
            {"Chequeo": "Maestro: sin proveedor asignado", "Resultado": f"{int((products['Proveedor'].str.upper() == 'SIN ASIGNAR').sum()):,}",
             "Detalle": "Requieren gestión del área de compras", "Severidad": "ALTA"},
            {"Chequeo": "Maestro: costo estimado", "Resultado": f"{int(products['Costo_Estimado'].sum()):,}",
             "Detalle": "Costo calculado desde el precio de venta", "Severidad": "MEDIA"},
            {"Chequeo": "Cobertura del catálogo sobre ventas USD",
             "Resultado": f"{(catalog_usd / total_usd * 100 if total_usd else 0):.1f}%",
             "Detalle": f"USD {catalog_usd:,.0f} de {total_usd:,.0f}", "Severidad": "ALTA"},
            {"Chequeo": "Cobertura de inventario informado",
             "Resultado": f"{int(products['Stock_Informado'].sum()):,} de {len(products):,}",
             "Detalle": "Productos con stock real para calcular cobertura", "Severidad": "ALTA"},
            {"Chequeo": "Productos bloqueados por ciclo de vida",
             "Resultado": f"{int(products['Compra_Bloqueada'].sum()):,}",
             "Detalle": "EOL/obsoleto/liquidación: no generan OC", "Severidad": "INFO"},
            {"Chequeo": "Precios que requieren aprobación",
             "Resultado": f"{int(products['Requiere_Aprobacion_Precio'].sum()):,}",
             "Detalle": "Variación de costo sobre umbral o margen bajo", "Severidad": "ALTA"},
        ]
        return pd.DataFrame(rows)
