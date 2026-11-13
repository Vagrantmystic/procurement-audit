"""Adaptador Profit 2K12 → esquema interno del sistema de compras."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from data_processing.loader import LoadedData
from data_processing.profit_loader import ProfitData
from utils.config import Config


def to_loaded_data(profit: ProfitData, config: Config,
                   logger: logging.Logger | None = None) -> LoadedData:
    logger = logger or logging.getLogger("compras")

    sales = profit.sales.copy()
    sales["EAN"] = sales["EAN"].astype(str).str.strip()
    sales["Monto_USD"] = pd.to_numeric(sales["Importe_USD"], errors="coerce").fillna(0.0)
    sales["Precio_USD"] = pd.to_numeric(sales["PrecioUnitario_USD"], errors="coerce")
    sales["Precio_Unitario"] = pd.to_numeric(sales["PrecioUnitario"], errors="coerce")
    sales["Monto_Total"] = pd.to_numeric(sales["DocBruto"], errors="coerce").fillna(0.0)
    sales["Tasa_Cambio"] = pd.to_numeric(sales["Tasa"], errors="coerce")
    sales["Cliente_ID"] = sales["CodCliente"].fillna("").astype(str)
    sales["Sucursal"] = sales["Sucursal"].fillna("").astype(str)
    sales["Vendedor"] = sales["Vendedor"].fillna("").astype(str)
    sales["Descripcion"] = sales["Descripcion"].fillna("").astype(str)
    sales = sales[sales["Cantidad"] > 0].copy()
    sales = sales.sort_values(["Fecha", "EAN"]).reset_index(drop=True)

    daily = (sales.groupby(["EAN", "Fecha"], as_index=False)
             .agg(Cantidad=("Cantidad", "sum"), Monto_USD=("Monto_USD", "sum"),
                  Transacciones=("Cantidad", "size"), Clientes=("Cliente_ID", "nunique")))

    catalog = _build_catalog(profit, config)
    stock = profit.stock.copy()
    for col in ("Stock_Actual", "Stock_Transito", "Stock_Comprometido", "Stock_Disponible"):
        if col not in stock.columns:
            stock[col] = 0.0
        stock[col] = pd.to_numeric(stock[col], errors="coerce").fillna(0.0)
    stock = stock.rename(columns={"Stock_Transito": "En_Transito"})
    stock["Comprometido"] = stock["Stock_Comprometido"]

    quality = profit.quality.copy()
    extra = pd.DataFrame([
        ("Fuente de datos", "Profit 2K12", "Conexión de solo lectura"),
        ("Productos con costo", int((catalog["Costo_USD"] > 0).sum()), "Costo de última compra"),
        ("Productos con proveedor", int((catalog["Proveedor"] != "SIN ASIGNAR").sum()), "Última compra o relación EAN-proveedor"),
        ("Productos en catálogo", len(catalog), "Artículos de venta con movimiento o stock"),
    ], columns=["Chequeo", "Resultado", "Detalle"])
    quality = pd.concat([quality, extra], ignore_index=True)

    logger.info("Profit adaptado: %s ventas | %s productos | %s stock",
                f"{len(sales):,}", f"{len(catalog):,}", f"{len(stock):,}")
    return LoadedData(
        sales=sales, daily=daily, catalog=catalog, stock=stock, quality=quality,
        as_of=profit.as_of, sales_path=None, catalog_path=None, stock_path=None,
        warehouse_info=getattr(profit, "warehouse_info", {}) or {},
    )


def _build_catalog(profit: ProfitData, config: Config) -> pd.DataFrame:
    products = profit.products.copy()
    products["EAN"] = products["EAN"].astype(str).str.strip()
    products = products[products["TipoArticulo"].isin(["V", "C"])].copy()

    purchases = profit.purchases[profit.purchases["TipoArticulo"].isin(["V", "C"])].copy()
    cost = (purchases.sort_values("Fecha").groupby("EAN")["CostoUnitario_USD"].last()
            .rename("Costo_Ultimo"))
    supplier = (purchases.sort_values("Fecha").groupby("EAN")["Proveedor"].last()
                .rename("Proveedor_Compra"))
    rel = profit.product_supplier.set_index("EAN")["Proveedor_Asignado"] if not profit.product_supplier.empty else pd.Series(dtype=str)

    out = products.set_index("EAN")
    out = out.join(cost).join(supplier)
    if "Proveedor_Compra" not in out.columns:
        out["Proveedor_Compra"] = np.nan
    if "Costo_Ultimo" not in out.columns:
        out["Costo_Ultimo"] = np.nan
    rel_map = rel.to_dict() if len(rel) else {}
    fallback = pd.Series([rel_map.get(ean, "SIN ASIGNAR") for ean in out.index], index=out.index)
    out["Proveedor"] = out["Proveedor_Compra"].fillna(fallback).fillna("SIN ASIGNAR")
    out["Proveedor"] = out["Proveedor"].replace({"": "SIN ASIGNAR"})
    out["Costo_USD"] = pd.to_numeric(out["Costo_Ultimo"], errors="coerce")
    out["Categoria"] = out["Categoria"].fillna("SIN CLASIFICAR")
    out["Subcategoria"] = out["Sublinea"].fillna("")
    out["Descripcion"] = out["Descripcion"].fillna("")
    out["Multiplo_Compra"] = int(config.get("inventory.default_multiplier", 24))
    out["Lead_Time"] = out["Proveedor"].map(
        lambda p: config.provider(str(p)).lead_time if str(p) != "SIN ASIGNAR" else int(config.get("inventory.default_lead_time", 30)))
    out["Stock_Minimo"] = pd.to_numeric(out["Stock_Minimo"], errors="coerce").replace(0, np.nan)
    out["Stock_Maximo"] = pd.to_numeric(out["Stock_Maximo"], errors="coerce").replace(0, np.nan)
    out["Origen"] = "PROFIT"
    out["Costo_Estimado"] = out["Costo_USD"].isna() | (out["Costo_USD"] <= 0)
    out["Catalogo_Completo"] = (out["Proveedor"] != "SIN ASIGNAR") & (~out["Costo_Estimado"])
    out = _add_price_metrics(out, profit, config)
    out = out.reset_index()
    columns = ["EAN", "Descripcion", "Proveedor", "Categoria", "Subcategoria", "Costo_USD",
               "Multiplo_Compra", "Lead_Time", "Stock_Minimo", "Stock_Maximo",
               "Origen", "Costo_Estimado", "Catalogo_Completo",
               "Costo_Ultimo_USD", "Costo_Ref_90d", "Var_Precio_Pct", "Impacto_Precio_USD",
               "Precio_Techo_USD", "Precio_Estado", "Margen_Nuevo_Pct",
               "Requiere_Aprobacion_Precio", "Demanda_Anual_Unidades", "Compras_Historico"]
    for col in columns:
        if col not in out.columns:
            out[col] = np.nan
    return out[columns]


def _add_price_metrics(out: pd.DataFrame, profit: ProfitData, config: Config) -> pd.DataFrame:
    """Calcula referencia de costo, variación, techo y margen para la regla de precios."""
    as_of = pd.Timestamp(profit.as_of)
    buys = profit.purchases[profit.purchases["TipoArticulo"].isin(["V", "C"])].copy()
    buys["EAN"] = buys["EAN"].astype(str).str.strip()
    buys = buys[(buys["Fecha"] <= as_of) & (buys["Cantidad"] > 0)].copy()
    buys["Precio_USD"] = pd.to_numeric(buys["CostoUnitario_USD"], errors="coerce")
    buys = buys[buys["Precio_USD"] > 0]

    sales = profit.sales[profit.sales["TipoArticulo"].isin(["V", "C"])].copy()
    sales["EAN"] = sales["EAN"].astype(str).str.strip()
    sales = sales[(sales["Fecha"] <= as_of) & (sales["Fecha"] >= as_of - pd.Timedelta(days=365))]
    annual = sales.groupby("EAN")["Cantidad"].sum().rename("Demanda_Anual_Unidades")

    if not buys.empty:
        sorted_buys = buys.sort_values("Fecha")
        last = sorted_buys.groupby("EAN").agg(Costo_Ultimo_USD=("Precio_USD", "last"),
                                              Compras_Historico=("Precio_USD", "size"))
        recent_start = as_of - pd.Timedelta(days=int(config.get("price.recent_window_days", 30)))
        previous_days = int(config.get("price.previous_window_days", 90))
        prev_start = recent_start - pd.Timedelta(days=previous_days)
        previous = buys[(buys["Fecha"] >= prev_start) & (buys["Fecha"] < recent_start)].copy()
        previous["_usd"] = previous["Precio_USD"] * previous["Cantidad"]
        if not previous.empty:
            ref = (previous.groupby("EAN").agg(_usd=("_usd", "sum"), _qty=("Cantidad", "sum")))
            ref["Costo_Ref_90d"] = ref["_usd"] / ref["_qty"].replace(0, np.nan)
            ref = ref[["Costo_Ref_90d"]]
        else:
            ref = pd.DataFrame(columns=["Costo_Ref_90d"])
        out = out.join(last).join(ref).join(annual)
    else:
        out = out.join(annual)
        out["Costo_Ultimo_USD"] = np.nan
        out["Compras_Historico"] = 0
        out["Costo_Ref_90d"] = np.nan

    if "Costo_Ultimo_USD" not in out.columns:
        out["Costo_Ultimo_USD"] = out.get("Costo_USD", np.nan)
    out["Costo_Ultimo_USD"] = pd.to_numeric(out["Costo_Ultimo_USD"], errors="coerce").fillna(
        pd.to_numeric(out.get("Costo_USD"), errors="coerce"))
    out["Costo_Ref_90d"] = pd.to_numeric(out["Costo_Ref_90d"], errors="coerce").fillna(
        out["Costo_Ultimo_USD"])
    out["Demanda_Anual_Unidades"] = pd.to_numeric(
        out.get("Demanda_Anual_Unidades"), errors="coerce").fillna(0.0)
    out["Compras_Historico"] = pd.to_numeric(
        out.get("Compras_Historico"), errors="coerce").fillna(0).astype(int)

    ref = out["Costo_Ref_90d"].where(out["Costo_Ref_90d"] > 0)
    current = out["Costo_Ultimo_USD"].where(out["Costo_Ultimo_USD"] > 0)
    out["Var_Precio_Pct"] = np.where(ref.notna() & current.notna(),
                                     (current / ref - 1.0) * 100.0, np.nan)
    out["Impacto_Precio_USD"] = np.where(
        current.notna() & ref.notna(),
        (current - ref) * out["Demanda_Anual_Unidades"], 0.0)
    max_increase = float(config.get("price.max_increase_pct", 20.0)) / 100.0
    out["Precio_Techo_USD"] = ref * (1.0 + max_increase)
    out["Margen_Nuevo_Pct"] = np.nan

    warning = float(config.get("price.warning_pct", 5.0))
    approval = float(config.get("price.approval_pct", 10.0))
    max_pct = float(config.get("price.max_increase_pct", 20.0))
    impact_threshold = float(config.get("price.impact_threshold_usd", 2000.0))
    var = out["Var_Precio_Pct"]
    out["Precio_Estado"] = np.select(
        [out["Compras_Historico"] <= 0, var > max_pct, var > approval, var > warning],
        ["SIN_REFERENCIA", "ROJO", "NARANJA", "AMARILLO"], default="VERDE")
    out["Requiere_Aprobacion_Precio"] = (
        out["Precio_Estado"].isin(["NARANJA", "ROJO"]) |
        ((out["Impacto_Precio_USD"].abs() >= impact_threshold) & (var >= warning))
    )
    return out
