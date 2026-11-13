"""Carga, normalización y validación de los archivos de entrada."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from utils.helpers import ensure_datetime, rename_columns, to_ean, to_number

SALES_ALIASES = {
    "EAN": ["EAN", "EAN13", "Codigo", "Código", "Codigo_Articulo", "Cod_Art", "Codigo_Barras",
            "Codigo de Barras", "Barra", "UPV", "UPC", "Articulo", "Código Artículo", "Item"],
    "Fecha": ["Fecha", "Fecha_Factura", "Fecha_Venta", "Date", "Dia", "Fecha Emisión"],
    "Cantidad": ["Cantidad", "Cant", "Unidades", "Qty", "Cantidad_Vendida", "Unid"],
    "Precio_Unitario": ["Precio_Unitario", "Precio", "Precio_Unit", "PVP", "Precio_Venta", "Precio Unitario"],
    "Monto_Total": ["Monto_Total", "Monto", "Total", "Importe", "Monto_Bs", "Total_Bs", "Monto Total"],
    "Tasa_Cambio": ["Tasa_Cambio", "Tasa", "Cambio", "Tasa_BCV", "Tasa_USD", "Tasa Cambio"],
    "Monto_USD": ["Monto_USD", "Total_USD", "USD", "Monto_Dolares", "Monto USD"],
    "Cliente_ID": ["Cliente_ID", "Cliente", "Codigo_Cliente", "Código Cliente", "RIF", "Cedula"],
    "Sucursal": ["Sucursal", "Tienda", "Almacen", "Almacén"],
    "Vendedor": ["Vendedor", "Vendedora", "Cod_Vendedor"],
    "Descripcion": ["Descripcion", "Descripción", "Producto", "Nombre", "Descripcion_Producto"],
}

CATALOG_ALIASES = {
    "EAN": ["EAN", "EAN13", "Codigo", "Código", "Codigo_Articulo", "Cod_Art", "Codigo_Barras", "Barra"],
    "Descripcion": ["Descripcion", "Descripción", "Producto", "Nombre", "Descripcion_Producto"],
    "Proveedor": ["Proveedor", "Suplidor", "Laboratorio", "Supplier"],
    "Categoria": ["Categoria", "Categoría", "Linea", "Línea", "Grupo"],
    "Subcategoria": ["Subcategoria", "Subcategoría", "Sublinea", "SubLínea", "Molecula", "Molécula"],
    "Costo_USD": ["Costo_USD", "Costo", "Precio_Compra", "Costo_Unitario", "Costo Unitario"],
    "Multiplo_Compra": ["Multiplo_Compra", "Múltiplo", "Multiplo", "Minimo_Compra", "Pedido_Minimo"],
    "Lead_Time": ["Lead_Time", "Lead Time", "Dias_Entrega", "Días Entrega", "Plazo_Entrega"],
    "Stock_Minimo": ["Stock_Minimo", "Stock Mínimo", "Minimo"],
    "Stock_Maximo": ["Stock_Maximo", "Stock Máximo", "Maximo"],
}

STOCK_ALIASES = {
    "EAN": ["EAN", "EAN13", "Codigo", "Código", "Codigo_Articulo", "Cod_Art", "Codigo_Barras", "Barra"],
    "Stock_Actual": ["Stock_Actual", "Stock", "Existencia", "Disponible", "Cantidad_Disponible", "Stock Actual"],
    "En_Transito": ["En_Transito", "Transito", "Tránsito", "Pedido_Transito"],
    "Comprometido": ["Comprometido", "Reservado", "Comprometido_Unidades"],
}


@dataclass
class LoadedData:
    sales: pd.DataFrame
    daily: pd.DataFrame
    catalog: pd.DataFrame
    stock: pd.DataFrame
    quality: pd.DataFrame
    as_of: pd.Timestamp
    sales_path: Path | None = None
    catalog_path: Path | None = None
    stock_path: Path | None = None
    warehouse_info: dict[str, Any] = field(default_factory=dict)


class DataLoader:
    """Carga los tres archivos de entrada y deja un esquema estable."""

    def __init__(self, sales_path: str | Path, catalog_path: str | Path,
                 stock_path: str | Path | None = None,
                 logger: logging.Logger | None = None):
        self.sales_path = Path(sales_path)
        self.catalog_path = Path(catalog_path)
        self.stock_path = Path(stock_path) if stock_path else None
        self.logger = logger or logging.getLogger("compras")
        self._quality: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ helpers
    def _add_quality(self, chequeo: str, resultado: str, detalle: str = "", severidad: str = "INFO") -> None:
        self._quality.append({
            "Chequeo": chequeo,
            "Resultado": resultado,
            "Detalle": detalle,
            "Severidad": severidad,
        })

    @staticmethod
    def _read_excel(path: Path, sheet: str | None = None, required: tuple[str, ...] = ()) -> pd.DataFrame:
        with pd.ExcelFile(path) as xl:
            sheet_names = list(xl.sheet_names)
            candidates = [sheet] if sheet else []
            candidates += [s for s in sheet_names if s not in candidates]
            last_error: Exception | None = None
            for name in candidates:
                if name is None or name not in sheet_names:
                    continue
                try:
                    df = xl.parse(name, dtype=object)
                except Exception as exc:  # hoja corrupta: probar la siguiente
                    last_error = exc
                    continue
                keys = {str(c).strip().lower() for c in df.columns}
                if not required or all(any(r.lower() in k for k in keys) for r in required):
                    return df
        if last_error:
            raise last_error
        raise ValueError(f"No se encontró una hoja válida en {path.name}")

    # ------------------------------------------------------------------ ventas
    def load_sales(self, sheet: str | None = None) -> pd.DataFrame:
        if not self.sales_path.exists():
            raise FileNotFoundError(f"Archivo de ventas no encontrado: {self.sales_path}")

        raw = self._read_excel(self.sales_path, sheet, required=("EAN", "Fecha"))
        rows_in = len(raw)
        df = rename_columns(raw, SALES_ALIASES)

        missing = [c for c in ("EAN", "Fecha", "Cantidad") if c not in df.columns]
        if missing:
            raise ValueError(f"Faltan columnas obligatorias en ventas: {missing}. "
                             f"Columnas encontradas: {list(raw.columns)}")

        df["EAN"] = to_ean(df["EAN"])
        df["Fecha"] = ensure_datetime(df["Fecha"])
        df["Cantidad"] = to_number(df["Cantidad"])

        invalid_dates = int(df["Fecha"].isna().sum())
        invalid_ean = int((df["EAN"] == "").sum())
        df = df.dropna(subset=["Fecha"])
        df = df[df["EAN"] != ""]
        df = df[df["Cantidad"].notna()]

        negative_qty = int((df["Cantidad"] <= 0).sum())
        df = df[df["Cantidad"] > 0].copy()
        df["Cantidad"] = df["Cantidad"].round().astype(int)

        # -------- importes en USD
        for col in ("Precio_Unitario", "Monto_Total", "Tasa_Cambio", "Monto_USD"):
            if col in df.columns:
                df[col] = to_number(df[col])

        monto = df["Monto_Total"] if "Monto_Total" in df.columns else pd.Series(np.nan, index=df.index)
        precio = df["Precio_Unitario"] if "Precio_Unitario" in df.columns else pd.Series(np.nan, index=df.index)
        tasa = df["Tasa_Cambio"] if "Tasa_Cambio" in df.columns else pd.Series(np.nan, index=df.index)
        usd = df["Monto_USD"] if "Monto_USD" in df.columns else pd.Series(np.nan, index=df.index)

        # Completar precio a partir del monto
        precio = precio.fillna(monto / df["Cantidad"].replace(0, np.nan))
        # Completar monto local
        monto = monto.fillna(precio * df["Cantidad"])
        # Completar USD: monto/tasa -> monto/precio
        usd_calc = pd.Series(np.nan, index=df.index)
        valid_rate = tasa.notna() & (tasa > 0)
        usd_calc[valid_rate] = monto[valid_rate] / tasa[valid_rate]
        usd_calc = usd_calc.fillna(monto / 1.0)  # si no hay tasa, se asume USD
        usd = usd.where(usd.notna() & (usd >= 0), usd_calc)
        # Si el monto USD existe pero es 0 y hay monto local, recalcular
        zero_usd = usd.fillna(0) <= 0
        usd.loc[zero_usd] = usd_calc.loc[zero_usd]
        usd = usd.fillna(0.0)
        # Precio unitario USD
        precio_usd = usd / df["Cantidad"].replace(0, np.nan)
        precio_usd = precio_usd.where(precio_usd > 0, np.nan)

        df["Monto_Total"] = monto.fillna(0.0)
        df["Precio_Unitario"] = precio.fillna(0.0)
        df["Tasa_Cambio"] = tasa
        df["Monto_USD"] = usd
        df["Precio_USD"] = precio_usd

        # -------- otras columnas
        for col in ("Cliente_ID", "Sucursal", "Vendedor", "Descripcion"):
            if col not in df.columns:
                df[col] = ""
            df[col] = df[col].fillna("").astype(str).str.strip()

        # -------- fechas válidas
        max_date = df["Fecha"].max()
        min_date = df["Fecha"].min()
        future = int((df["Fecha"] > pd.Timestamp.now().normalize() + pd.Timedelta(days=1)).sum())
        df = df[df["Fecha"] <= max_date]

        df = df.sort_values(["Fecha", "EAN"]).reset_index(drop=True)
        self._quality.extend([
            {"Chequeo": "Ventas: filas leídas", "Resultado": f"{rows_in:,}", "Detalle": self.sales_path.name, "Severidad": "INFO"},
            {"Chequeo": "Ventas: filas válidas", "Resultado": f"{len(df):,}",
             "Detalle": f"{invalid_dates} fechas inválidas, {invalid_ean} EAN vacíos", "Severidad": "INFO"},
            {"Chequeo": "Ventas: cantidades <= 0", "Resultado": f"{negative_qty:,}",
             "Detalle": "Excluidas del análisis (devoluciones o ajustes)", "Severidad": "MEDIA" if negative_qty else "INFO"},
            {"Chequeo": "Ventas: fechas futuras", "Resultado": f"{future:,}",
             "Detalle": "Registros posteriores a hoy", "Severidad": "MEDIA" if future else "INFO"},
            {"Chequeo": "Ventas: rango de fechas", "Resultado": f"{min_date:%d/%m/%Y} a {max_date:%d/%m/%Y}",
             "Detalle": f"{(max_date - min_date).days:,} días", "Severidad": "INFO"},
            {"Chequeo": "Ventas: EAN únicos", "Resultado": f"{df['EAN'].nunique():,}",
             "Detalle": "Productos distintos con ventas", "Severidad": "INFO"},
        ])
        if future:
            self.logger.warning("Se detectaron %s ventas con fecha futura; se conservan.", future)
        return df

    # ------------------------------------------------------------------ catálogo
    def load_catalog(self, sheet: str | None = None) -> pd.DataFrame:
        if not self.catalog_path.exists():
            self.logger.warning("Catálogo no encontrado: %s. Se inferirá desde ventas.", self.catalog_path)
            self._add_quality("Catálogo", "No encontrado", str(self.catalog_path), "CRÍTICA")
            return pd.DataFrame(columns=["EAN", "Descripcion", "Proveedor", "Categoria", "Subcategoria",
                                         "Costo_USD", "Multiplo_Compra", "Lead_Time",
                                         "Stock_Minimo", "Stock_Maximo", "Catalogo_Completo", "Origen"])

        raw = self._read_excel(self.catalog_path, sheet, required=("EAN",))
        df = rename_columns(raw, CATALOG_ALIASES)
        if "EAN" not in df.columns:
            raise ValueError(f"El catálogo no tiene columna EAN. Columnas: {list(raw.columns)}")
        df["EAN"] = to_ean(df["EAN"])
        df = df[df["EAN"] != ""].copy()

        defaults = {
            "Descripcion": "", "Proveedor": "SIN ASIGNAR", "Categoria": "SIN CLASIFICAR",
            "Subcategoria": "", "Costo_USD": np.nan, "Multiplo_Compra": np.nan,
            "Lead_Time": np.nan, "Stock_Minimo": np.nan, "Stock_Maximo": np.nan,
        }
        for col, default in defaults.items():
            if col not in df.columns:
                df[col] = default

        for col in ("Costo_USD", "Multiplo_Compra", "Lead_Time", "Stock_Minimo", "Stock_Maximo"):
            df[col] = to_number(df[col])
        for col in ("Descripcion", "Proveedor", "Categoria", "Subcategoria"):
            df[col] = df[col].fillna("").astype(str).str.strip()
        df["Proveedor"] = df["Proveedor"].replace({"": "SIN ASIGNAR"})
        df["Descripcion"] = df["Descripcion"].replace({"": ""})

        # Descartar duplicados priorizando la fila más completa
        df["_completo"] = df[["Descripcion", "Proveedor", "Costo_USD", "Multiplo_Compra"]].notna().sum(axis=1)
        duplicate_eans = int(df["EAN"].duplicated().sum())
        df = (df.sort_values("_completo", ascending=False)
                .drop_duplicates("EAN", keep="first")
                .drop(columns="_completo")
                .reset_index(drop=True))

        df["Origen"] = "CATALOGO"
        df["Costo_Estimado"] = df["Costo_USD"].isna() | (df["Costo_USD"] <= 0)
        df["Catalogo_Completo"] = (
            (df["Descripcion"].str.len() > 0)
            & (df["Proveedor"].str.upper() != "SIN ASIGNAR")
            & (~df["Costo_Estimado"])
        )
        self._quality.extend([
            {"Chequeo": "Catálogo: productos", "Resultado": f"{len(df):,}", "Detalle": self.catalog_path.name, "Severidad": "INFO"},
            {"Chequeo": "Catálogo: EAN duplicados", "Resultado": f"{duplicate_eans:,}",
             "Detalle": "Se conservó la fila más completa", "Severidad": "MEDIA" if duplicate_eans else "INFO"},
            {"Chequeo": "Catálogo: sin proveedor", "Resultado": f"{int((df['Proveedor'].str.upper() == 'SIN ASIGNAR').sum()):,}",
             "Detalle": "Se asigna 'SIN ASIGNAR'",
             "Severidad": "ALTA" if (df["Proveedor"].str.upper() == "SIN ASIGNAR").any() else "INFO"},
            {"Chequeo": "Catálogo: sin costo", "Resultado": f"{int(df['Costo_Estimado'].sum()):,}",
             "Detalle": "El costo se estima desde el precio de venta",
             "Severidad": "ALTA" if df["Costo_Estimado"].any() else "INFO"},
        ])
        return df

    # ------------------------------------------------------------------ stock
    def load_stock(self, sheet: str | None = None) -> pd.DataFrame:
        empty = pd.DataFrame(columns=["EAN", "Stock_Actual", "En_Transito", "Comprometido"])
        if not self.stock_path or not self.stock_path.exists():
            self._add_quality("Stock", "No informado", "No se pudo calcular cobertura real", "ALTA")
            return empty

        raw = self._read_excel(self.stock_path, sheet, required=("EAN",))
        df = rename_columns(raw, STOCK_ALIASES)
        if "EAN" not in df.columns:
            self._add_quality("Stock", "Formato inválido", "Falta columna EAN", "ALTA")
            return empty
        df["EAN"] = to_ean(df["EAN"])
        if "Stock_Actual" not in df.columns:
            self._add_quality("Stock", "Formato inválido", "Falta columna Stock_Actual", "ALTA")
            return empty
        df["Stock_Actual"] = to_number(df["Stock_Actual"])
        for col in ("En_Transito", "Comprometido"):
            if col not in df.columns:
                df[col] = 0.0
            df[col] = to_number(df[col]).fillna(0.0)
        df = df[df["EAN"] != ""].copy()
        df["Stock_Actual"] = df["Stock_Actual"].fillna(0.0)
        # Sumar duplicados (distintos almacenes)
        df = df.groupby("EAN", as_index=False).agg(
            Stock_Actual=("Stock_Actual", "sum"),
            En_Transito=("En_Transito", "sum"),
            Comprometido=("Comprometido", "sum"),
        )
        df["Stock_Disponible"] = df["Stock_Actual"] - df["Comprometido"] + df["En_Transito"]
        self._quality.append({
            "Chequeo": "Stock: productos informados", "Resultado": f"{len(df):,}",
            "Detalle": self.stock_path.name, "Severidad": "INFO",
        })
        return df

    # ------------------------------------------------------------------ pipeline
    def load_all(self, as_of: str | pd.Timestamp | None = None) -> LoadedData:
        start = datetime.now()
        self.logger.info("Cargando ventas: %s", self.sales_path.name)
        sales = self.load_sales()

        if as_of is None:
            as_of_date = sales["Fecha"].max().normalize()
        else:
            as_of_date = pd.Timestamp(as_of).normalize()
            sales = sales[sales["Fecha"] <= as_of_date].copy()
            if sales.empty:
                raise ValueError(f"No hay ventas hasta la fecha de corte {as_of_date:%d/%m/%Y}")

        self.logger.info("Cargando catálogo: %s", self.catalog_path.name)
        catalog = self.load_catalog()
        self.logger.info("Cargando stock: %s", self.stock_path.name if self.stock_path else "(no informado)")
        stock = self.load_stock()

        daily = (sales.groupby(["EAN", "Fecha"], as_index=False)
                      .agg(Cantidad=("Cantidad", "sum"), Monto_USD=("Monto_USD", "sum"),
                           Transacciones=("Cantidad", "size"), Clientes=("Cliente_ID", "nunique")))

        quality = pd.DataFrame(self._quality)
        self.logger.info("Datos cargados en %.1fs | %s ventas | %s productos catálogo | %s stock",
                         (datetime.now() - start).total_seconds(), f"{len(sales):,}",
                         f"{len(catalog):,}", f"{len(stock):,}")
        return LoadedData(sales=sales, daily=daily, catalog=catalog, stock=stock, quality=quality,
                          as_of=as_of_date, sales_path=self.sales_path,
                          catalog_path=self.catalog_path, stock_path=self.stock_path)
