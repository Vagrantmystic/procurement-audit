"""Analítica de ventas para el BI interno (solo lectura).

Reutiliza el loader de Profit para respetar:
* filtro de almacenes,
* conversión a USD,
* política read-only,
* nombres de clientes/laboratorios/productos.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
import pandas as pd

from data_processing.laboratorio_loader import (
    attach_laboratorio,
    coverage_report,
    load_laboratorio_dim,
)
from data_processing.profit_connection import ProfitConnection
from data_processing.profit_loader import ProfitLoader
from data_processing.warehouse_filter import WarehouseFilter
from utils.config import Config

_FRAME_CACHE: dict[tuple, tuple[float, pd.DataFrame]] = {}
_CACHE_LOCK = threading.Lock()
DEFAULT_TTL = 900


class VentasAnalytics:
    def __init__(self, config: Config, connection, warehouse_filter: WarehouseFilter | None = None,
                 ttl_seconds: int | None = None):
        self.config = config
        self.db = connection
        self.base_filter = warehouse_filter or WarehouseFilter(config, connection)
        self.ttl = int(ttl_seconds or config.get("ventas.cache_ttl_seconds", DEFAULT_TTL))
        self.parallel = bool(config.get("ventas.parallel_queries", False))
        self.lab_path = config.get("ventas.laboratorio_file", "docs/PRODUCTOS POR LABORATORIO.md")
        self.dim = load_laboratorio_dim(self.lab_path)
        self.dim_mtime = _mtime(self.lab_path)

    # ------------------------------------------------------------------ datos
    def _loader(self, warehouses: Iterable[str] | None = None) -> ProfitLoader:
        wf = self.base_filter.with_selection(warehouses) if warehouses else self.base_filter
        return ProfitLoader(self.db, warehouse_filter=wf)

    def _parallel_queries(self, start_ts: pd.Timestamp, end_ts: pd.Timestamp,
                          wf: WarehouseFilter) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Ejecuta ventas, compras y tasas en conexiones separadas.

        SQL Server Express responde mejor a tres consultas independientes que a
        una sola consulta con joins grandes. Si no hay conexión configurable,
        cae al modo serial.
        """
        config_path = getattr(self.db, "config_path", None)
        if not self.parallel or config_path is None:
            loader = ProfitLoader(self.db, warehouse_filter=wf)
            return (
                loader.load_sales(start_ts.strftime("%Y-%m-%d"), end_ts.strftime("%Y-%m-%d")),
                loader.load_purchases((start_ts - pd.Timedelta(days=365)).strftime("%Y-%m-%d"),
                                      end_ts.strftime("%Y-%m-%d")),
                loader.load_rates((start_ts - pd.Timedelta(days=365)).strftime("%Y-%m-%d"),
                                  end_ts.strftime("%Y-%m-%d")),
            )

        def run(kind: str) -> pd.DataFrame:
            conn = ProfitConnection(config_path)
            try:
                loader = ProfitLoader(conn, warehouse_filter=wf)
                if kind == "sales":
                    return loader.load_sales(start_ts.strftime("%Y-%m-%d"), end_ts.strftime("%Y-%m-%d"))
                if kind == "purchases":
                    return loader.load_purchases((start_ts - pd.Timedelta(days=365)).strftime("%Y-%m-%d"),
                                                 end_ts.strftime("%Y-%m-%d"))
                return loader.load_rates((start_ts - pd.Timedelta(days=365)).strftime("%Y-%m-%d"),
                                         end_ts.strftime("%Y-%m-%d"))
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=3) as pool:
            sales_f = pool.submit(run, "sales")
            purchases_f = pool.submit(run, "purchases")
            rates_f = pool.submit(run, "rates")
            return sales_f.result(), purchases_f.result(), rates_f.result()

    def load_frame(self, start: str | date, end: str | date,
                   warehouses: Iterable[str] | None = None) -> pd.DataFrame:
        start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
        codes = tuple(sorted(str(c).strip().upper() for c in (warehouses or self.base_filter.selection.sales_codes)))
        key = (str(start_ts.date()), str(end_ts.date()), codes, self.dim_mtime)
        now = time.time()
        with _CACHE_LOCK:
            cached = _FRAME_CACHE.get(key)
            if cached and now - cached[0] < self.ttl:
                return cached[1].copy()

        wf = self.base_filter.with_selection(codes)
        sales, purchases, rates = self._parallel_queries(start_ts, end_ts, wf)
        if sales.empty:
            empty = pd.DataFrame(columns=["Fecha", "DocNum", "CodCliente", "Cliente", "EAN",
                                          "Descripcion", "CodLinea", "Linea_Terapeutica",
                                          "Laboratorio", "Categoria", "Sublinea", "Cantidad",
                                          "Venta_USD", "Venta_Bs", "Costo_Unitario_USD",
                                          "Costo_Total_USD", "Margen_USD", "Margen_Pct", "Almacen"])
            return empty

        sales = ProfitLoader._to_usd(sales, rates, "Importe")
        purchases = ProfitLoader._to_usd(purchases, rates, "Importe")
        cost_map = _cost_map(purchases)

        df = sales.copy()
        df = attach_laboratorio(df, self.dim)
        df["Fecha"] = pd.to_datetime(df["Fecha"], errors="coerce")
        df["Venta_USD"] = pd.to_numeric(df["Importe_USD"], errors="coerce").fillna(0.0)
        df["Venta_Bs"] = pd.to_numeric(df["Importe"], errors="coerce").fillna(0.0)
        df["Cantidad"] = pd.to_numeric(df["Cantidad"], errors="coerce").fillna(0.0)
        df["Costo_Unitario_USD"] = df["EAN"].map(cost_map)
        df["Costo_Total_USD"] = df["Cantidad"] * df["Costo_Unitario_USD"]
        df["Margen_USD"] = df["Venta_USD"] - df["Costo_Total_USD"]
        df["Margen_Pct"] = np.where(df["Venta_USD"] != 0, df["Margen_USD"] / df["Venta_USD"] * 100, np.nan)
        df["Cliente"] = df["Cliente"].fillna("SIN CLIENTE").astype(str).str.strip()
        df["CodCliente"] = df["CodCliente"].fillna("").astype(str).str.strip()
        df["Linea_Terapeutica"] = df["Linea_Terapeutica"].fillna("SIN LÍNEA").astype(str).str.strip()
        df.loc[df["Linea_Terapeutica"] == "", "Linea_Terapeutica"] = "SIN LÍNEA"
        df["Categoria"] = df["Categoria"].fillna("SIN CATEGORÍA").astype(str).str.strip()
        df["Sublinea"] = df["Sublinea"].fillna("").astype(str).str.strip()
        df["Almacen"] = df["Almacen"].fillna("").astype(str).str.strip()
        df["DocNum"] = df["DocNum"].astype(str).str.strip()
        df = df.dropna(subset=["Fecha"])
        with _CACHE_LOCK:
            _FRAME_CACHE[key] = (now, df.copy())
        return df

    def frame(self, start: str | date, end: str | date, warehouses: Iterable[str] | None = None,
              laboratorio: str | None = None, categoria: str | None = None,
              sublinea: str | None = None, linea_terapeutica: str | None = None) -> pd.DataFrame:
        df = self.load_frame(start, end, warehouses)
        if laboratorio:
            df = df[df["Laboratorio"].str.casefold() == laboratorio.casefold()]
        if linea_terapeutica:
            df = df[df["Linea_Terapeutica"].str.casefold() == linea_terapeutica.casefold()]
        if categoria:
            df = df[df["Categoria"].str.casefold() == categoria.casefold()]
        if sublinea:
            df = df[df["Sublinea"].str.casefold() == sublinea.casefold()]
        return df

    # ------------------------------------------------------------------ KPIs
    def resumen(self, start: str | date, end: str | date,
                warehouses: Iterable[str] | None = None, **filters: Any) -> dict[str, Any]:
        start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
        days = max((end_ts - start_ts).days + 1, 1)
        current = self.frame(start, end, warehouses, **filters)
        previous = self.frame((start_ts - pd.Timedelta(days=days)).strftime("%Y-%m-%d"),
                              (start_ts - pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                              warehouses, **filters)
        kpi_now = _kpis(current)
        kpi_prev = _kpis(previous)
        delta = {}
        for key in ("venta_usd", "margen_usd", "margen_pct", "ticket_promedio", "clientes", "sku", "facturas"):
            before = kpi_prev.get(key)
            after = kpi_now.get(key)
            delta[key] = _var(after, before)
        top_lab = kpi_now.get("top_laboratorio") or {}
        return {
            "desde": start_ts.strftime("%Y-%m-%d"),
            "hasta": end_ts.strftime("%Y-%m-%d"),
            "dias": days,
            "kpis": kpi_now,
            "comparacion": kpi_prev,
            "variacion_pct": delta,
            "top_laboratorio": top_lab,
            "cobertura_laboratorio": coverage_report(current),
            "serie_diaria": _serie_diaria(current),
            "heatmap": _heatmap(current),
            "almacenes": list(warehouses or self.base_filter.selection.sales_codes),
        }

    def laboratorios(self, start: str | date, end: str | date,
                     warehouses: Iterable[str] | None = None, limit: int = 50,
                     **filters: Any) -> dict[str, Any]:
        df = self.frame(start, end, warehouses, **filters)
        grouped = _group(df, ["Laboratorio"]) if not df.empty else pd.DataFrame()
        if grouped.empty:
            return {"items": [], "total": 0}
        total = grouped["Venta_USD"].sum()
        grouped["Share_Pct"] = grouped["Venta_USD"] / total * 100 if total else 0
        grouped = grouped.sort_values("Venta_USD", ascending=False).head(max(1, min(limit, 200)))
        return {"items": _records(grouped), "total": int(len(grouped)), "venta_total": float(total)}

    def lineas_terapeuticas(self, start: str | date, end: str | date,
                            warehouses: Iterable[str] | None = None, limit: int = 20,
                            **filters: Any) -> dict[str, Any]:
        df = self.frame(start, end, warehouses, **filters)
        grouped = _group(df, ["Linea_Terapeutica"]) if not df.empty else pd.DataFrame()
        if grouped.empty:
            return {"items": [], "total": 0}
        total = grouped["Venta_USD"].sum()
        grouped["Share_Pct"] = grouped["Venta_USD"] / total * 100 if total else 0
        grouped = grouped.sort_values("Venta_USD", ascending=False).head(max(1, min(limit, 100)))
        return {"items": _records(grouped), "total": int(len(grouped)), "venta_total": float(total)}

    def productos(self, start: str | date, end: str | date,
                  warehouses: Iterable[str] | None = None, limit: int = 50,
                  offset: int = 0, **filters: Any) -> dict[str, Any]:
        df = self.frame(start, end, warehouses, **filters)
        grouped = _group(df, ["EAN", "Descripcion", "Laboratorio", "Linea_Terapeutica"]) if not df.empty else pd.DataFrame()
        if grouped.empty:
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        grouped = grouped.sort_values("Venta_USD", ascending=False)
        total = int(len(grouped))
        part = grouped.iloc[offset:offset + max(1, min(limit, 200))]
        return {"items": _records(part), "total": total, "limit": limit, "offset": offset}

    def clientes(self, start: str | date, end: str | date,
                 warehouses: Iterable[str] | None = None, limit: int = 50,
                 offset: int = 0, **filters: Any) -> dict[str, Any]:
        df = self.frame(start, end, warehouses, **filters)
        grouped = _group(df, ["CodCliente", "Cliente"]) if not df.empty else pd.DataFrame()
        if grouped.empty:
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        grouped = grouped.sort_values("Venta_USD", ascending=False)
        total = int(len(grouped))
        part = grouped.iloc[offset:offset + max(1, min(limit, 200))]
        return {"items": _records(part), "total": total, "limit": limit, "offset": offset}

    def cliente(self, codigo: str, start: str | date, end: str | date,
                warehouses: Iterable[str] | None = None,
                product_limit: int | None = 100) -> dict[str, Any]:
        df = self.frame(start, end, warehouses)
        df = df[df["CodCliente"].str.casefold() == str(codigo).strip().casefold()]
        if df.empty:
            return {"cliente": None, "items": [], "productos": [], "serie_mensual": []}
        ficha = {
            "codigo": str(codigo),
            "cliente": str(df["Cliente"].iloc[0]),
            "ultima_compra": df["Fecha"].max().strftime("%Y-%m-%d"),
            "facturas": int(df["DocNum"].nunique()),
            "venta_usd": float(df["Venta_USD"].sum()),
            "venta_bs": float(df["Venta_Bs"].sum()),
            "unidades": float(df["Cantidad"].sum()),
            "margen_usd": float(df["Margen_USD"].sum()),
            "margen_pct": _margin_pct(df),
            "ticket_promedio": float(df["Venta_USD"].sum() / df["DocNum"].nunique()) if df["DocNum"].nunique() else 0,
        }
        labs = _group(df, ["Laboratorio"]).sort_values("Venta_USD", ascending=False)
        prods = _group(df, ["EAN", "Descripcion", "Laboratorio", "Linea_Terapeutica"]).sort_values("Venta_USD", ascending=False)
        if product_limit:
            prods = prods.head(product_limit)
        serie = (df.assign(Mes=df["Fecha"].dt.strftime("%Y-%m"))
                 .groupby("Mes", as_index=False).agg(Venta_USD=("Venta_USD", "sum")))
        return {"cliente": ficha, "items": _records(labs), "productos": _records(prods),
                "serie_mensual": _records(serie)}

    def filtros(self, start: str | date, end: str | date,
                warehouses: Iterable[str] | None = None) -> dict[str, Any]:
        df = self.frame(start, end, warehouses)
        return {
            "laboratorios": sorted(df["Laboratorio"].dropna().unique().tolist()) if not df.empty else [],
            "lineas_terapeuticas": sorted(df["Linea_Terapeutica"].dropna().unique().tolist()) if not df.empty else [],
            "categorias": sorted(df["Categoria"].dropna().unique().tolist()) if not df.empty else [],
            "sublineas": sorted(df["Sublinea"].dropna().unique().tolist()) if not df.empty else [],
            "cobertura_laboratorio": coverage_report(df),
        }

    def export_frame(self, start: str | date, end: str | date,
                     warehouses: Iterable[str] | None = None, **filters: Any) -> pd.DataFrame:
        df = self.frame(start, end, warehouses, **filters)
        cols = ["Fecha", "DocNum", "CodCliente", "Cliente", "EAN", "Descripcion",
                "Laboratorio", "Linea_Terapeutica", "Categoria", "Sublinea", "Almacen",
                "Cantidad", "Venta_USD", "Venta_Bs", "Costo_Unitario_USD", "Costo_Total_USD",
                "Margen_USD", "Margen_Pct"]
        return df[[c for c in cols if c in df.columns]].copy()

    @staticmethod
    def clear_cache() -> None:
        with _CACHE_LOCK:
            _FRAME_CACHE.clear()


def _cost_map(purchases: pd.DataFrame) -> pd.Series:
    if purchases is None or purchases.empty:
        return pd.Series(dtype=float)
    p = purchases.copy()
    p["Cantidad"] = pd.to_numeric(p["Cantidad"], errors="coerce").fillna(0.0)
    p["Importe_USD"] = pd.to_numeric(p["Importe_USD"], errors="coerce").fillna(0.0)
    p = p[p["Cantidad"] > 0]
    agg = p.groupby("EAN").agg(_usd=("Importe_USD", "sum"), _qty=("Cantidad", "sum"))
    return (agg["_usd"] / agg["_qty"].replace(0, np.nan)).dropna()


def _kpis(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"venta_usd": 0.0, "venta_bs": 0.0, "margen_usd": 0.0, "margen_pct": 0.0,
                "ticket_promedio": 0.0, "clientes": 0, "sku": 0, "facturas": 0,
                "top_laboratorio": None, "cobertura_costo_pct": 0.0}
    venta = float(df["Venta_USD"].sum())
    venta_costo = float(df.loc[df["Costo_Unitario_USD"].notna(), "Venta_USD"].sum())
    margen = float(df["Margen_USD"].fillna(0).sum())
    facturas = int(df["DocNum"].nunique())
    top = (df.groupby("Laboratorio")["Venta_USD"].sum().sort_values(ascending=False)
           .head(1))
    return {
        "venta_usd": venta,
        "venta_bs": float(df["Venta_Bs"].sum()),
        "margen_usd": margen,
        "margen_pct": margen / venta_costo * 100 if venta_costo else 0.0,
        "ticket_promedio": venta / facturas if facturas else 0.0,
        "clientes": int(df["CodCliente"].nunique()),
        "sku": int(df["EAN"].nunique()),
        "facturas": facturas,
        "top_laboratorio": {"nombre": str(top.index[0]), "venta_usd": float(top.iloc[0])} if len(top) else None,
        "cobertura_costo_pct": venta_costo / venta * 100 if venta else 0.0,
    }


def _group(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=keys + ["Venta_USD", "Venta_Bs", "Margen_USD", "Margen_Pct",
                                            "Unidades", "Facturas", "Clientes", "SKU", "Ultima_Venta"])
    agg = df.groupby(keys, dropna=False).agg(
        Venta_USD=("Venta_USD", "sum"),
        Venta_Bs=("Venta_Bs", "sum"),
        Margen_USD=("Margen_USD", "sum"),
        Unidades=("Cantidad", "sum"),
        Facturas=("DocNum", "nunique"),
        Clientes=("CodCliente", "nunique"),
        SKU=("EAN", "nunique"),
        Ultima_Venta=("Fecha", "max"),
    ).reset_index()
    agg["Margen_Pct"] = np.where(agg["Venta_USD"] != 0, agg["Margen_USD"] / agg["Venta_USD"] * 100, np.nan)
    agg["Ultima_Venta"] = pd.to_datetime(agg["Ultima_Venta"], errors="coerce").dt.strftime("%Y-%m-%d")
    return agg


def _serie_diaria(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    out = df.groupby(df["Fecha"].dt.strftime("%Y-%m-%d"), as_index=False).agg(
        Venta_USD=("Venta_USD", "sum"), Margen_USD=("Margen_USD", "sum"))
    out = out.rename(columns={"Fecha": "fecha"})
    return _records(out)


def _heatmap(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    days = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    temp = df.copy()
    temp["Dia"] = temp["Fecha"].dt.weekday.map(lambda i: days[int(i)] if pd.notna(i) else "")
    temp["Mes"] = temp["Fecha"].dt.strftime("%Y-%m")
    out = temp.groupby(["Dia", "Mes"], as_index=False).agg(Venta_USD=("Venta_USD", "sum"))
    return _records(out)


def _margin_pct(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    base = df.loc[df["Costo_Unitario_USD"].notna(), "Venta_USD"].sum()
    return float(df["Margen_USD"].fillna(0).sum() / base * 100) if base else 0.0


def _var(after: Any, before: Any) -> float | None:
    try:
        after = float(after)
        before = float(before)
    except (TypeError, ValueError):
        return None
    if before == 0:
        return None
    return (after / before - 1) * 100


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d")
    return out.replace({np.nan: None}).to_dict("records")


def _mtime(path: str | Path) -> float:
    try:
        p = Path(path)
        if not p.is_absolute():
            p = Path(__file__).resolve().parents[2] / p
        return p.stat().st_mtime if p.exists() else 0.0
    except Exception:
        return 0.0
