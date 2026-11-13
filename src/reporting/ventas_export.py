"""Exportación Excel de Ventas con xlsxwriter.

La hoja crítica es ``Detalle_Cliente_Laboratorio``: usa la dimensión maestra
``Laboratorio`` (nunca ``Linea_Terapeutica``) y conserva ``SIN_LABORATORIO``.
"""
from __future__ import annotations

import math
from io import BytesIO
from typing import Any, Iterable

import pandas as pd

from analytics.ventas_analytics import VentasAnalytics

DETAIL_SHEET = "Detalle_Cliente_Laboratorio"


def build_detalle_cliente_laboratorio(df: pd.DataFrame) -> pd.DataFrame:
    """Agrupa ventas por cliente + laboratorio real, sin filtrar SIN_LABORATORIO."""
    columns = ["CodCliente", "Cliente", "Laboratorio", "Venta_USD", "Venta_Bs",
               "Margen_USD", "Margen_Pct", "Unidades", "Facturas", "SKU", "Ultima_Compra"]
    if df is None or df.empty:
        return pd.DataFrame(columns=columns)
    work = df.copy()
    if "Laboratorio" not in work.columns:
        raise ValueError("El detalle no contiene la dimensión Laboratorio; no use Línea como laboratorio.")
    work["Laboratorio"] = work["Laboratorio"].fillna("SIN_LABORATORIO").astype(str).str.strip()
    work.loc[work["Laboratorio"] == "", "Laboratorio"] = "SIN_LABORATORIO"
    grouped = work.groupby(["CodCliente", "Cliente", "Laboratorio"], dropna=False).agg(
        Venta_USD=("Venta_USD", "sum"),
        Venta_Bs=("Venta_Bs", "sum"),
        Margen_USD=("Margen_USD", "sum"),
        Unidades=("Cantidad", "sum"),
        Facturas=("DocNum", "nunique"),
        SKU=("EAN", "nunique"),
        Ultima_Compra=("Fecha", "max"),
    ).reset_index()
    grouped["Margen_Pct"] = grouped.apply(
        lambda r: (r["Margen_USD"] / r["Venta_USD"] * 100) if r["Venta_USD"] else 0.0, axis=1)
    grouped["Ultima_Compra"] = pd.to_datetime(grouped["Ultima_Compra"], errors="coerce").dt.strftime("%Y-%m-%d")
    grouped = grouped[columns].sort_values(["Cliente", "Venta_USD"], ascending=[True, False])
    return grouped.reset_index(drop=True)


def _write_number(ws, row: int, col: int, value: Any, fmt) -> None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        ws.write_blank(row, col, None, fmt)
        return
    if not math.isfinite(number):
        ws.write_blank(row, col, None, fmt)
        return
    ws.write_number(row, col, number, fmt)


def _write_sheet(writer, name: str, df: pd.DataFrame, title: str) -> None:
    df.to_excel(writer, sheet_name=name, index=False, startrow=1)
    ws = writer.sheets[name]
    workbook = writer.book
    title_fmt = workbook.add_format({"bold": True, "font_size": 14, "font_color": "#FFFFFF",
                                     "bg_color": "#1F3864", "valign": "vcenter"})
    header_fmt = workbook.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#2F5597",
                                      "border": 1, "text_wrap": True, "valign": "vcenter"})
    money_fmt = workbook.add_format({"num_format": '#,##0.00'})
    int_fmt = workbook.add_format({"num_format": '#,##0'})
    pct_fmt = workbook.add_format({"num_format": '0.0"%"'})
    ws.merge_range(0, 0, 0, max(len(df.columns) - 1, 1), title, title_fmt)
    ws.set_row(0, 24)
    for col, name_col in enumerate(df.columns):
        ws.write(1, col, name_col, header_fmt)
        longest = max((len(str(v)) for v in df[name_col].head(80)), default=0) if len(df) else 0
        width = max(12, min(45, max(len(str(name_col)) + 2, longest + 2)))
        ws.set_column(col, col, width)
    for row in range(2, len(df) + 2):
        for col, name_col in enumerate(df.columns):
            value = df.iloc[row - 2, col]
            if name_col in {"Venta_USD", "Venta_Bs", "Margen_USD", "Costo_Unitario_USD",
                            "Costo_Total_USD", "Impacto_USD"}:
                _write_number(ws, row, col, value, money_fmt)
            elif name_col in {"Unidades", "Facturas", "SKU", "Cantidad"}:
                _write_number(ws, row, col, value, int_fmt)
            elif name_col in {"Margen_Pct", "Var_Precio_Pct"}:
                _write_number(ws, row, col, value, pct_fmt)
            else:
                ws.write(row, col, "" if pd.isna(value) else value)
    ws.freeze_panes(2, 0)
    ws.autofilter(1, 0, len(df) + 1, len(df.columns) - 1)


def _summary_frame(resumen: dict[str, Any], start: str, end: str,
                   warehouses: Iterable[str], filters: dict[str, Any]) -> pd.DataFrame:
    k = resumen.get("kpis", {})
    rows = [
        ("Período desde", start), ("Período hasta", end),
        ("Almacenes", ", ".join(warehouses) if warehouses else "Selección configurada"),
        ("Laboratorio", filters.get("laboratorio") or "Todos"),
        ("Línea terapéutica", filters.get("linea_terapeutica") or "Todas"),
        ("Categoría", filters.get("categoria") or "Todas"),
        ("Venta neta USD", k.get("venta_usd", 0)),
        ("Venta neta Bs", k.get("venta_bs", 0)),
        ("Margen USD", k.get("margen_usd", 0)),
        ("Margen %", k.get("margen_pct", 0)),
        ("Ticket promedio USD", k.get("ticket_promedio", 0)),
        ("Clientes activos", k.get("clientes", 0)),
        ("SKU vendidos", k.get("sku", 0)),
        ("Facturas", k.get("facturas", 0)),
        ("Laboratorio #1", (resumen.get("top_laboratorio") or {}).get("nombre", "")),
        ("Cobertura laboratorio %", (resumen.get("cobertura_laboratorio") or {}).get("venta_pct", 0)),
    ]
    return pd.DataFrame(rows, columns=["Indicador", "Valor"])


def build_ventas_excel(analytics: VentasAnalytics, start: str, end: str,
                       warehouses: Iterable[str], filters: dict[str, Any]) -> bytes:
    """Genera el libro multi-hoja y devuelve los bytes."""
    warehouses = list(warehouses or analytics.base_filter.selection.sales_codes)
    df = analytics.export_frame(start, end, warehouses, **filters)
    detalle = build_detalle_cliente_laboratorio(df)
    resumen = analytics.resumen(start, end, warehouses, **filters)
    labs = pd.DataFrame(analytics.laboratorios(start, end, warehouses, limit=200, **filters).get("items", []))
    lineas = pd.DataFrame(analytics.lineas_terapeuticas(start, end, warehouses, limit=100, **filters).get("items", []))
    productos = pd.DataFrame(analytics.productos(start, end, warehouses, limit=500, **filters).get("items", []))
    clientes = pd.DataFrame(analytics.clientes(start, end, warehouses, limit=500, **filters).get("items", []))
    serie = pd.DataFrame(resumen.get("serie_diaria", []))

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
        _write_sheet(writer, "RESUMEN", _summary_frame(resumen, start, end, warehouses, filters), "VENTAS · RESUMEN EJECUTIVO")
        _write_sheet(writer, "SERIE_DIARIA", serie, "VENTA NETA DIARIA")
        _write_sheet(writer, "LABORATORIOS", labs, "VENTAS POR LABORATORIO REAL")
        _write_sheet(writer, "LINEAS_TERAPEUTICAS", lineas, "VENTAS POR LÍNEA TERAPÉUTICA")
        _write_sheet(writer, "PRODUCTOS", productos, "TOP PRODUCTOS")
        _write_sheet(writer, "CLIENTES", clientes, "TOP CLIENTES")
        _write_sheet(writer, DETAIL_SHEET, detalle,
                     "DETALLE CLIENTE × LABORATORIO (dimensión maestra; SIN_LABORATORIO incluido)")
        _write_sheet(writer, "DETALLE_VENTAS", df, "DETALLE DE VENTAS FILTRADO")

        workbook = writer.book
        ws = workbook.add_worksheet("GRAFICAS")
        ws.write(0, 0, "Gráficas de ventas", workbook.add_format({"bold": True, "font_size": 14, "font_color": "#1F3864"}))
        if not labs.empty:
            chart = workbook.add_chart({"type": "bar"})
            n = min(len(labs), 10)
            chart.add_series({
                "name": "Venta USD",
                "categories": ["LABORATORIOS", 2, 0, n + 1, 0],
                "values": ["LABORATORIOS", 2, 1, n + 1, 1],
                "fill": {"color": "#2F5597"},
            })
            chart.set_title({"name": "Top laboratorios por venta"})
            chart.set_legend({"none": True})
            chart.set_size({"width": 720, "height": 360})
            ws.insert_chart("A3", chart)
        if not serie.empty:
            chart = workbook.add_chart({"type": "line"})
            n = min(len(serie), 120)
            chart.add_series({
                "name": "Venta USD",
                "categories": ["SERIE_DIARIA", 2, 0, n + 1, 0],
                "values": ["SERIE_DIARIA", 2, 1, n + 1, 1],
                "line": {"color": "#2563EB", "width": 2},
            })
            chart.set_title({"name": "Venta neta diaria"})
            chart.set_legend({"none": True})
            chart.set_size({"width": 720, "height": 320})
            ws.insert_chart("A22", chart)
        ws.set_column(0, 20, 18)

    buffer.seek(0)
    return buffer.getvalue()
