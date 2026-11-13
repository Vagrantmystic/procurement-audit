"""Extracción normalizada de Profit 2K12 para la auditoría y el pronóstico.

Todas las consultas son **SELECT**. La conversión a USD se hace en Python:
* documentos en USD: importe / tasa (la tasa es Bs/USD);
* documentos en BS: importe / tasa de referencia del mes (mediana de documentos USD).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from data_processing.profit_connection import ProfitConnection
from data_processing.warehouse_filter import WarehouseFilter, sql_placeholders
from utils.config import Config

PURCHASE_COLUMNS = [
    "DocNum", "NroFact", "Fecha", "CodProveedor", "Proveedor", "Moneda", "Tasa",
    "Comprador", "Sucursal", "DocBruto", "DocNeto", "DocDescuento",
    "Renglon", "EAN", "Descripcion", "TipoArticulo", "CodLinea", "Linea",
    "CodSublinea", "Sublinea", "CodCategoria", "Categoria",
    "Cantidad", "CostoUnitario", "Importe", "DescuentoLinea", "Unidad", "Almacen",
]

SALES_COLUMNS = [
    "DocNum", "Fecha", "CodCliente", "Cliente", "Moneda", "Tasa",
    "Vendedor", "Sucursal", "DocBruto", "DocNeto",
    "Renglon", "EAN", "Descripcion", "TipoArticulo", "CodLinea", "Linea",
    "CodSublinea", "Sublinea", "CodCategoria", "Categoria",
    "Cantidad", "PrecioUnitario", "Importe", "DescuentoLinea", "Unidad", "Almacen",
]


@dataclass
class ProfitData:
    purchases: pd.DataFrame
    sales: pd.DataFrame
    products: pd.DataFrame
    suppliers: pd.DataFrame
    stock: pd.DataFrame
    product_supplier: pd.DataFrame
    returns: pd.DataFrame
    rates: pd.DataFrame
    as_of: pd.Timestamp
    quality: pd.DataFrame
    warehouse_info: dict[str, Any] = field(default_factory=dict)


class ProfitLoader:
    def __init__(self, connection: ProfitConnection | None = None,
                 logger: logging.Logger | None = None,
                 warehouse_filter: WarehouseFilter | None = None):
        self.db = connection or ProfitConnection()
        self.logger = logger or logging.getLogger("compras")
        self.warehouse = warehouse_filter or WarehouseFilter(Config(), self.db, self.logger)

    # ------------------------------------------------------------------ consultas
    @staticmethod
    def _warehouse_clause(column: str, codes: tuple[str, ...]) -> str:
        return f" AND {column} IN ({sql_placeholders(codes)})" if codes else ""

    def _purchases_query(self, codes: tuple[str, ...] = ()) -> str:
        return f"""
            SELECT f.doc_num, f.nro_fact, f.fec_emis, f.co_prov, p.prov_des,
                   f.co_mone, f.tasa, f.co_us_in, f.co_sucu_in,
                   f.total_bruto, f.total_neto, f.monto_desc_glob,
                   r.reng_num, r.co_art, a.art_des, a.tipo,
                   a.co_lin, l.lin_des, a.co_subl, s.subl_des, a.co_cat, c.cat_des,
                   r.total_art, r.cost_unit, r.reng_neto, r.monto_desc,
                   r.co_uni, r.co_alma
            FROM saFacturaCompra f
            JOIN saFacturaCompraReng r ON r.doc_num = f.doc_num
            LEFT JOIN saProveedor p ON p.co_prov = f.co_prov
            LEFT JOIN saArticulo a ON a.co_art = r.co_art
            LEFT JOIN saLineaArticulo l ON l.co_lin = a.co_lin
            LEFT JOIN saSubLinea s ON s.co_lin = a.co_lin AND s.co_subl = a.co_subl
            LEFT JOIN saCatArticulo c ON c.co_cat = a.co_cat
            WHERE f.fec_emis >= ? AND f.fec_emis < ? AND f.anulado = 0
              {self._warehouse_clause('r.co_alma', codes)}
        """

    def _sales_query(self, codes: tuple[str, ...] = ()) -> str:
        return f"""
            SELECT f.doc_num, f.fec_emis, f.co_cli, cl.cli_des,
                   f.co_mone, f.tasa, f.co_ven, f.co_sucu_in,
                   f.total_bruto, f.total_neto,
                   r.reng_num, r.co_art, a.art_des, a.tipo,
                   a.co_lin, l.lin_des, a.co_subl, s.subl_des, a.co_cat, c.cat_des,
                   r.total_art, r.prec_vta, r.reng_neto, r.monto_desc,
                   r.co_uni, r.co_alma
            FROM saFacturaVenta f
            JOIN saFacturaVentaReng r ON r.doc_num = f.doc_num
            LEFT JOIN saCliente cl ON cl.co_cli = f.co_cli
            LEFT JOIN saArticulo a ON a.co_art = r.co_art
            LEFT JOIN saLineaArticulo l ON l.co_lin = a.co_lin
            LEFT JOIN saSubLinea s ON s.co_lin = a.co_lin AND s.co_subl = a.co_subl
            LEFT JOIN saCatArticulo c ON c.co_cat = a.co_cat
            WHERE f.fec_emis >= ? AND f.fec_emis < ? AND f.anulado = 0
              {self._warehouse_clause('r.co_alma', codes)}
        """

    def load_purchases(self, start: str | date, end: str | date) -> pd.DataFrame:
        codes = self.warehouse.selection.purchase_codes
        raw = self.db.query(self._purchases_query(codes), [str(start), str(end), *codes])
        df = raw.rename(columns={
            "doc_num": "DocNum", "nro_fact": "NroFact", "fec_emis": "Fecha",
            "co_prov": "CodProveedor", "prov_des": "Proveedor", "co_mone": "Moneda",
            "tasa": "Tasa", "co_us_in": "Comprador", "co_sucu_in": "Sucursal",
            "total_bruto": "DocBruto", "total_neto": "DocNeto",
            "monto_desc_glob": "DocDescuento", "reng_num": "Renglon",
            "co_art": "EAN", "art_des": "Descripcion", "tipo": "TipoArticulo",
            "co_lin": "CodLinea", "lin_des": "Linea", "co_subl": "CodSublinea",
            "subl_des": "Sublinea", "co_cat": "CodCategoria", "cat_des": "Categoria",
            "total_art": "Cantidad", "cost_unit": "CostoUnitario", "reng_neto": "Importe",
            "monto_desc": "DescuentoLinea", "co_uni": "Unidad", "co_alma": "Almacen",
        })
        for col in PURCHASE_COLUMNS:
            if col not in df.columns:
                df[col] = None
        df["EAN"] = df["EAN"].astype(str).str.strip()
        df["Almacen"] = df["Almacen"].astype(str).str.strip()
        return df[PURCHASE_COLUMNS]

    def load_sales(self, start: str | date, end: str | date) -> pd.DataFrame:
        codes = self.warehouse.selection.sales_codes
        raw = self.db.query(self._sales_query(codes), [str(start), str(end), *codes])
        df = raw.rename(columns={
            "doc_num": "DocNum", "fec_emis": "Fecha", "co_cli": "CodCliente",
            "cli_des": "Cliente", "co_mone": "Moneda", "tasa": "Tasa",
            "co_ven": "Vendedor", "co_sucu_in": "Sucursal",
            "total_bruto": "DocBruto", "total_neto": "DocNeto",
            "reng_num": "Renglon", "co_art": "EAN", "art_des": "Descripcion",
            "tipo": "TipoArticulo", "co_lin": "CodLinea", "lin_des": "Linea",
            "co_subl": "CodSublinea", "subl_des": "Sublinea",
            "co_cat": "CodCategoria", "cat_des": "Categoria",
            "total_art": "Cantidad", "prec_vta": "PrecioUnitario", "reng_neto": "Importe",
            "monto_desc": "DescuentoLinea", "co_uni": "Unidad", "co_alma": "Almacen",
        })
        for col in SALES_COLUMNS:
            if col not in df.columns:
                df[col] = None
        df["EAN"] = df["EAN"].astype(str).str.strip()
        df["Almacen"] = df["Almacen"].astype(str).str.strip()
        return df[SALES_COLUMNS]

    def load_products(self) -> pd.DataFrame:
        df = self.db.query("""
            SELECT a.co_art, a.art_des, a.tipo, a.co_lin, l.lin_des, a.co_subl, s.subl_des,
                   a.co_cat, c.cat_des, a.stock_min, a.stock_max, a.anulado, a.fecha_inac
            FROM saArticulo a
            LEFT JOIN saLineaArticulo l ON l.co_lin = a.co_lin
            LEFT JOIN saSubLinea s ON s.co_lin = a.co_lin AND s.co_subl = a.co_subl
            LEFT JOIN saCatArticulo c ON c.co_cat = a.co_cat
        """).rename(columns={
            "co_art": "EAN", "art_des": "Descripcion", "tipo": "TipoArticulo",
            "co_lin": "CodLinea", "lin_des": "Linea", "co_subl": "CodSublinea",
            "subl_des": "Sublinea", "co_cat": "CodCategoria", "cat_des": "Categoria",
            "stock_min": "Stock_Minimo", "stock_max": "Stock_Maximo",
            "anulado": "Anulado", "fecha_inac": "Fecha_Inactivacion",
        })
        df["EAN"] = df["EAN"].astype(str).str.strip()
        return df

    def load_suppliers(self) -> pd.DataFrame:
        return self.db.query("""
            SELECT co_prov, prov_des, rif, inactivo, telefonos, email, plaz_pag
            FROM saProveedor
        """).rename(columns={"co_prov": "CodProveedor", "prov_des": "Proveedor",
                             "inactivo": "Inactivo", "plaz_pag": "Plazo_Pago"})

    def load_stock(self) -> pd.DataFrame:
        codes = self.warehouse.selection.stock_codes
        source = self.warehouse.selection.stock_source
        if source not in {"saStockAlmacen", "v_saStockActual"}:
            self.logger.warning("Fuente de stock '%s' no permitida; se usa saStockAlmacen", source)
            source = "saStockAlmacen"
        if source == "saStockAlmacen":
            sql = (f"SELECT co_art, tipo, stock FROM saStockAlmacen "
                   f"WHERE co_alma IN ({sql_placeholders(codes)})")
            raw = self.db.query(sql, list(codes))
        else:
            raw = self.db.query("SELECT co_art, tipo, stock FROM v_saStockActual")
        if raw.empty:
            return pd.DataFrame(columns=["EAN", "Stock_Actual", "Stock_Transito", "Stock_Comprometido", "Stock_Disponible"])
        raw["co_art"] = raw["co_art"].astype(str).str.strip()
        raw["tipo"] = raw["tipo"].astype(str).str.strip()
        pivot = raw.pivot_table(index="co_art", columns="tipo", values="stock", aggfunc="sum", fill_value=0)
        for col in ("ACT", "LLE", "COM"):
            if col not in pivot.columns:
                pivot[col] = 0.0
        out = pd.DataFrame({
            "EAN": pivot.index,
            "Stock_Actual": pivot["ACT"].clip(lower=0),
            "Stock_Transito": pivot["LLE"].clip(lower=0),
            "Stock_Comprometido": pivot["COM"].clip(lower=0),
        }).reset_index(drop=True)
        out["Stock_Disponible"] = out["Stock_Actual"] - out["Stock_Comprometido"] + out["Stock_Transito"]
        return out

    def load_product_supplier(self) -> pd.DataFrame:
        df = self.db.query("""
            SELECT r.co_art, r.co_prov, p.prov_des, r.fecha
            FROM saArtProveedorReng r
            LEFT JOIN saProveedor p ON p.co_prov = r.co_prov
        """).rename(columns={"co_art": "EAN", "co_prov": "CodProveedor",
                             "prov_des": "Proveedor_Asignado", "fecha": "Fecha_Asignacion"})
        df["EAN"] = df["EAN"].astype(str).str.strip()
        return df

    def load_returns(self, start: str | date, end: str | date) -> pd.DataFrame:
        codes = self.warehouse.selection.purchase_codes
        raw = self.db.query(f"""
            SELECT d.doc_num, d.fec_emis, d.co_prov, p.prov_des, d.co_mone, d.tasa,
                   d.total_bruto, d.total_neto, r.co_art, r.total_art, r.reng_neto, r.co_alma
            FROM saDevolucionProveedor d
            JOIN saDevolucionProveedorReng r ON r.doc_num = d.doc_num
            LEFT JOIN saProveedor p ON p.co_prov = d.co_prov
            WHERE d.fec_emis >= ? AND d.fec_emis < ? AND d.anulado = 0
              {self._warehouse_clause('r.co_alma', codes)}
        """, [str(start), str(end), *codes])
        if raw.empty:
            return pd.DataFrame(columns=["DocNum", "Fecha", "CodProveedor", "Proveedor",
                                         "Moneda", "Tasa", "DocBruto", "DocNeto",
                                         "EAN", "Cantidad", "Importe"])
        return raw.rename(columns={"doc_num": "DocNum", "fec_emis": "Fecha", "co_prov": "CodProveedor",
                                   "prov_des": "Proveedor", "co_mone": "Moneda", "tasa": "Tasa",
                                   "total_bruto": "DocBruto", "total_neto": "DocNeto",
                                   "co_art": "EAN", "total_art": "Cantidad", "reng_neto": "Importe",
                                   "co_alma": "Almacen"})

    def load_rates(self, start: str | date, end: str | date) -> pd.DataFrame:
        """Tasa Bs/USD de referencia por mes (mediana de documentos USD)."""
        codes = self.warehouse.selection.sales_codes
        raw = self.db.query(f"""
            SELECT FORMAT(f.fec_emis,'yyyy-MM') AS Mes, AVG(f.tasa) AS Tasa
            FROM saFacturaVenta f
            JOIN saFacturaVentaReng r ON r.doc_num = f.doc_num
            WHERE f.fec_emis >= ? AND f.fec_emis < ? AND f.anulado = 0
              AND f.co_mone = 'USD' AND f.tasa > 1
              {self._warehouse_clause('r.co_alma', codes)}
            GROUP BY FORMAT(f.fec_emis,'yyyy-MM')
            ORDER BY Mes
        """, [str(start), str(end), *codes])
        return raw

    # ------------------------------------------------------------------ pipeline
    def get_almacenes(self) -> pd.DataFrame:
        """Almacenes existentes en Profit con sus banderas operativas."""
        return self.warehouse.available

    def load_all(self, start: str | date, end: str | date,
                 as_of: str | pd.Timestamp | None = None) -> ProfitData:
        start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
        as_of_ts = pd.Timestamp(as_of) if as_of else min(end_ts, pd.Timestamp.today().normalize())
        self.logger.info("Extrayendo Profit: compras y ventas %s a %s...", start_ts.date(), end_ts.date())
        self.logger.info("  almacenes venta/stock: %s | compras: %s",
                         ", ".join(self.warehouse.selection.sales_codes),
                         ", ".join(self.warehouse.selection.purchase_codes))
        purchases = self.load_purchases(start, end)
        sales = self.load_sales(start, end)
        self.logger.info("  compras: %s líneas | ventas: %s líneas", f"{len(purchases):,}", f"{len(sales):,}")
        products = self.load_products()
        suppliers = self.load_suppliers()
        stock = self.load_stock()
        product_supplier = self.load_product_supplier()
        returns = self.load_returns(start, end)
        rates = self.load_rates(start, end)

        purchases = self._to_usd(purchases, rates, "Importe")
        sales = self._to_usd(sales, rates, "Importe")
        returns = self._to_usd(returns, rates, "Importe") if not returns.empty else returns

        quality = self._quality(purchases, sales, products, stock, returns, rates)
        info = self.warehouse.selection.summary()
        quality = pd.concat([quality, pd.DataFrame([{
            "Chequeo": "Almacenes venta/stock",
            "Resultado": ", ".join(info["selected"]),
            "Detalle": " | ".join(info["selected_labels"]),
        }, {
            "Chequeo": "Almacenes compras",
            "Resultado": ", ".join(info["purchase_codes"]),
            "Detalle": f"Modo {info['purchase_mode']} | " + " | ".join(info["purchase_labels"]),
        }])], ignore_index=True)
        return ProfitData(purchases=purchases, sales=sales, products=products, suppliers=suppliers,
                          stock=stock, product_supplier=product_supplier, returns=returns,
                          rates=rates, as_of=as_of_ts, quality=quality, warehouse_info=info)

    @staticmethod
    def _to_usd(df: pd.DataFrame, rates: pd.DataFrame, amount_col: str) -> pd.DataFrame:
        if df.empty:
            df["Importe_USD"] = []
            df["Tasa_USD"] = []
            return df
        out = df.copy()
        out["Fecha"] = pd.to_datetime(out["Fecha"], errors="coerce")
        out["Tasa"] = pd.to_numeric(out["Tasa"], errors="coerce")
        for col in ("Cantidad", amount_col, "DescuentoLinea"):
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
        out["Mes"] = out["Fecha"].dt.strftime("%Y-%m")
        rate_map = dict(zip(rates["Mes"], rates["Tasa"])) if not rates.empty else {}
        fallback = float(rates["Tasa"].median()) if not rates.empty else 1.0
        monthly = out["Mes"].map(rate_map).fillna(fallback)
        currency = out["Moneda"].astype(str).str.strip().str.upper()
        usd_rate = np.where((currency == "USD") & (out["Tasa"] > 1), out["Tasa"], monthly)
        usd_rate = np.where((usd_rate is None) | (pd.isna(usd_rate)) | (usd_rate <= 0), 1.0, usd_rate)
        out["Tasa_USD"] = usd_rate
        out["Importe_USD"] = out[amount_col] / out["Tasa_USD"]
        out["CostoUnitario_USD"] = np.where(out["Cantidad"] > 0, out["Importe_USD"] / out["Cantidad"], np.nan)
        out["PrecioUnitario_USD"] = out["CostoUnitario_USD"]
        return out

    @staticmethod
    def _quality(purchases: pd.DataFrame, sales: pd.DataFrame, products: pd.DataFrame,
                 stock: pd.DataFrame, returns: pd.DataFrame, rates: pd.DataFrame) -> pd.DataFrame:
        rows = [
            ("Compras: líneas extraídas", len(purchases), "2026 y años previos según rango"),
            ("Ventas: líneas extraídas", len(sales), "2026 y años previos según rango"),
            ("Artículos en maestro", len(products), "Incluye servicios e inactivos"),
            ("Artículos con stock", len(stock), "v_saStockActual"),
            ("Devoluciones a proveedor", len(returns), "Documentos del rango"),
            ("Meses con tasa de referencia", len(rates), "Para documentos en BS"),
        ]
        return pd.DataFrame(rows, columns=["Chequeo", "Resultado", "Detalle"])
