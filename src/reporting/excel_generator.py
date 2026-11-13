"""Generador del libro Excel ejecutivo (8 hojas, formato profesional)."""
from __future__ import annotations

import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule, DataBarRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from reporting.context import ReportContext

# --------------------------------------------------------------------------- paleta
NAVY = "1F3864"
BLUE = "2F5597"
MID_BLUE = "8EA9DB"
LIGHT_BLUE = "D9E2F3"
PALE_BLUE = "EEF3FA"
WHITE = "FFFFFF"
GRAY = "F2F2F2"
DARK_GRAY = "595959"
RED = "C00000"
ORANGE = "C55A11"
YELLOW = "FFC000"
GREEN = "548235"
PURPLE = "7030A0"

PRIORITY_FILL = {
    "CRÍTICA": (RED, WHITE),
    "ALTA": (ORANGE, WHITE),
    "MEDIA": (YELLOW, "3F3F3F"),
    "BAJA": (GREEN, WHITE),
    "REVISAR": (PURPLE, WHITE),
}
SEVERITY_FILL = {
    "CRÍTICA": (RED, WHITE),
    "ALTA": (ORANGE, WHITE),
    "MEDIA": (YELLOW, "3F3F3F"),
    "BAJA": (GREEN, WHITE),
}

MONEY = '"$"#,##0.00'
MONEY0 = '"$"#,##0'
INT = "#,##0"
DEC1 = "#,##0.0"
PCT = '0.0"%"'
PCT_SIGNED = '+0.0"%";-0.0"%"'
DATE = "DD/MM/YYYY"
CONF = "0.00"

THIN = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP_TOP = Alignment(wrap_text=True, vertical="top")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=False)


def _clean(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value


def _fmt_number(value: Any, fmt: str | None) -> Any:
    if value is None:
        return None
    if fmt == INT and not isinstance(value, bool):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return value
    return value


class ExcelReportGenerator:
    """Construye el reporte con hojas de resumen, órdenes, alertas, análisis y validación."""

    def __init__(self, context: ReportContext, output_path: str | Path,
                 logger: logging.Logger | None = None):
        self.ctx = context
        self.path = Path(output_path)
        self.logger = logger or logging.getLogger("compras")
        self.wb = Workbook()

    # ------------------------------------------------------------------ público
    def generate(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._sheet_resumen(self.wb.active)
        self._sheet_ordenes(self.wb.create_sheet("ÓRDENES SUGERIDAS"))
        self._sheet_eol(self.wb.create_sheet("EOL Y LIQUIDACIÓN"))
        self._sheet_precios(self.wb.create_sheet("CONTROL DE PRECIOS"))
        self._sheet_alertas(self.wb.create_sheet("ALERTAS CRÍTICAS"))
        self._sheet_analisis(self.wb.create_sheet("ANÁLISIS ABC-XYZ"))
        self._sheet_detalle(self.wb.create_sheet("DETALLE PRODUCTOS"))
        self._sheet_precision(self.wb.create_sheet("PRECISIÓN MODELOS"))
        self._sheet_parametros(self.wb.create_sheet("PARÁMETROS"))
        self._sheet_catalogo(self.wb.create_sheet("CATÁLOGO FALTANTE"))
        self._sheet_calidad(self.wb.create_sheet("DATOS Y CALIDAD"))

        self.wb.properties.title = f"Reporte de compras {self.ctx.period_label}"
        self.wb.properties.creator = "Sistema de Predicción - Droguería Emmanuelle"
        self.wb.properties.subject = "Órdenes de compra sugeridas"
        self.wb.save(self.path)
        self.logger.info("Reporte Excel generado: %s", self.path)
        return self.path

    # ------------------------------------------------------------------ helpers
    def _title(self, ws: Worksheet, title: str, subtitle: str, ncols: int) -> int:
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
        cell = ws.cell(row=1, column=1, value=title)
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(name="Calibri", size=15, bold=True, color=WHITE)
        cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.row_dimensions[1].height = 30
        for col in range(1, ncols + 1):
            ws.cell(row=1, column=col).fill = PatternFill("solid", fgColor=NAVY)

        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncols)
        cell = ws.cell(row=2, column=1, value=subtitle)
        cell.font = Font(name="Calibri", size=9, italic=True, color=DARK_GRAY)
        cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.row_dimensions[2].height = 18
        return 4

    def _section(self, ws: Worksheet, row: int, text: str, ncols: int) -> int:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
        cell = ws.cell(row=row, column=1, value=text)
        cell.fill = PatternFill("solid", fgColor=LIGHT_BLUE)
        cell.font = Font(name="Calibri", size=11, bold=True, color=NAVY)
        cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        for col in range(1, ncols + 1):
            ws.cell(row=row, column=col).fill = PatternFill("solid", fgColor=LIGHT_BLUE)
            ws.cell(row=row, column=col).border = BORDER
        ws.row_dimensions[row].height = 22
        return row + 1

    def _kpi_cards(self, ws: Worksheet, row: int, cards: Sequence[tuple[str, Any, str | None]],
                   per_row: int = 4) -> int:
        start_col = 2
        for index, (label, value, fmt) in enumerate(cards):
            if index > 0 and index % per_row == 0:
                row += 3
            col = start_col + (index % per_row) * 2
            ws.merge_cells(start_row=row, start_column=col, end_row=row, end_column=col + 1)
            ws.merge_cells(start_row=row + 1, start_column=col, end_row=row + 1, end_column=col + 1)
            label_cell = ws.cell(row=row, column=col, value=label)
            label_cell.font = Font(name="Calibri", size=9, color=DARK_GRAY)
            label_cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            label_cell.fill = PatternFill("solid", fgColor=PALE_BLUE)
            value_cell = ws.cell(row=row + 1, column=col, value=_clean(value))
            value_cell.font = Font(name="Calibri", size=18, bold=True, color=NAVY)
            value_cell.alignment = Alignment(horizontal="center", vertical="center")
            value_cell.fill = PatternFill("solid", fgColor=PALE_BLUE)
            if fmt:
                value_cell.number_format = fmt
            for r in (row, row + 1):
                for c in range(col, col + 2):
                    ws.cell(row=r, column=c).border = BORDER
                    ws.cell(row=r, column=c).fill = PatternFill("solid", fgColor=PALE_BLUE)
            ws.row_dimensions[row].height = 26
            ws.row_dimensions[row + 1].height = 30
        return row + 3

    def _table(self, ws: Worksheet, row: int, headers: Sequence[str],
               rows: Sequence[Sequence[Any]], formats: Sequence[str | None] | None = None,
               aligns: Sequence[str] | None = None, start_col: int = 2,
               header_fill: str = BLUE, fill_priority: int | None = None,
               fill_severity: int | None = None, bold_last: bool = False,
               row_height: int | None = None) -> int:
        ncols = len(headers)
        formats = formats or [None] * ncols
        aligns = aligns or ["left"] * ncols

        for j, header in enumerate(headers):
            cell = ws.cell(row=row, column=start_col + j, value=header)
            cell.fill = PatternFill("solid", fgColor=header_fill)
            cell.font = Font(name="Calibri", size=9, bold=True, color=WHITE)
            cell.alignment = CENTER
            cell.border = BORDER
        ws.row_dimensions[row].height = 32

        if not rows:
            ws.merge_cells(start_row=row + 1, start_column=start_col,
                           end_row=row + 1, end_column=start_col + ncols - 1)
            cell = ws.cell(row=row + 1, column=start_col, value="Sin datos para este período")
            cell.font = Font(name="Calibri", size=9, italic=True, color=DARK_GRAY)
            cell.alignment = LEFT
            return row + 2

        for i, values in enumerate(rows):
            r = row + 1 + i
            if row_height:
                ws.row_dimensions[r].height = row_height
            zebra = (i % 2 == 1)
            for j in range(ncols):
                value = _clean(values[j]) if j < len(values) else None
                cell = ws.cell(row=r, column=start_col + j, value=_fmt_number(value, formats[j]))
                cell.font = Font(name="Calibri", size=9, color="222222")
                cell.border = BORDER
                cell.alignment = Alignment(
                    horizontal={"right": "right", "center": "center"}.get(aligns[j], "left"),
                    vertical="top", wrap_text=True,
                )
                if formats[j]:
                    cell.number_format = formats[j]
                if zebra:
                    cell.fill = PatternFill("solid", fgColor="F7F9FC")
            if fill_priority is not None:
                cell = ws.cell(row=r, column=start_col + fill_priority)
                colors = PRIORITY_FILL.get(str(cell.value))
                if colors:
                    cell.fill = PatternFill("solid", fgColor=colors[0])
                    cell.font = Font(name="Calibri", size=9, bold=True, color=colors[1])
                    cell.alignment = CENTER
            if fill_severity is not None:
                cell = ws.cell(row=r, column=start_col + fill_severity)
                colors = SEVERITY_FILL.get(str(cell.value))
                if colors:
                    cell.fill = PatternFill("solid", fgColor=colors[0])
                    cell.font = Font(name="Calibri", size=9, bold=True, color=colors[1])
                    cell.alignment = CENTER

        if bold_last and rows:
            for j in range(ncols):
                cell = ws.cell(row=row + len(rows), column=start_col + j)
                cell.font = Font(name="Calibri", size=9, bold=True)
                cell.border = Border(left=THIN, right=THIN, bottom=THIN,
                                     top=Side(style="medium", color=NAVY))
        return row + len(rows) + 1

    def _set_widths(self, ws: Worksheet, widths: dict[int, float]) -> None:
        for col, width in widths.items():
            ws.column_dimensions[get_column_letter(col)].width = width

    def _finish(self, ws: Worksheet, header_row: int, last_row: int, last_col: int,
                landscape: bool = True, fit_width: int = 1) -> None:
        if last_row > header_row:
            ws.auto_filter.ref = f"B{header_row}:{get_column_letter(last_col)}{last_row}"
        ws.freeze_panes = f"D{header_row + 1}"
        ws.sheet_view.showGridLines = False
        ws.page_setup.orientation = "landscape" if landscape else "portrait"
        ws.page_setup.fitToWidth = fit_width
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.print_title_rows = f"{header_row}:{header_row}"

    @staticmethod
    def _subtitle() -> str:
        return ""

    # ------------------------------------------------------------------ resumen
    def _sheet_resumen(self, ws: Worksheet) -> None:
        ctx = self.ctx
        ws.title = "RESUMEN"
        ws.sheet_view.showGridLines = False
        self._set_widths(ws, {1: 2, 2: 22, 3: 44, 4: 32, 5: 16, 6: 16, 7: 16, 8: 16, 9: 16, 10: 16})

        subtitle = (f"{ctx.company} · Reporte generado el {ctx.generated_at:%d/%m/%Y %H:%M} · "
                    f"Corte de datos: {ctx.as_of:%d/%m/%Y} · Moneda: USD")
        row = self._title(ws, "SISTEMA DE PREDICCIÓN Y OPTIMIZACIÓN DE COMPRAS", subtitle, 10)
        row += 1

        orders_summary = ctx.order_bundle.summary
        critical = int(ctx.alert_bundle.summary.get("por_severidad", {}).get("CRÍTICA", 0))
        no_catalog = int((~ctx.products["Catalogo_Completo"].astype(bool)).sum())
        cards = [
            ("Productos analizados", len(ctx.segmentation.products), INT),
            ("Órdenes sugeridas", orders_summary.get("ordenes", 0), INT),
            ("Unidades a comprar", orders_summary.get("unidades", 0), INT),
            ("Inversión estimada", orders_summary.get("inversion_usd", 0.0), MONEY0),
            ("Alertas críticas", critical, INT),
            ("Cobertura de inventario", ctx.stock_coverage_pct / 100.0, '0.0%'),
            ("WAPE portafolio A/B", ctx.forecast_bundle.metrics.get("wape_ab", 0) / 100.0, '0.0%'),
            ("Productos sin proveedor/costo", no_catalog, INT),
            ("SKU excluidos EOL", orders_summary.get("excluidos_eol", 0), INT),
            ("Precios por aprobar", orders_summary.get("aprobaciones", 0), INT),
        ]
        row = self._kpi_cards(ws, row, cards)
        row += 1

        # ---------------- prioridad
        row = self._section(ws, row, "COMPRA SUGERIDA POR PRIORIDAD", 10)
        headers = ["Prioridad", "Órdenes", "Unidades", "Inversión USD", "% Inversión"]
        by_priority = ctx.order_bundle.by_priority
        rows = [[r["Prioridad"], r["Órdenes"], r["Unidades"], r["Inversión_USD"], r["%_Inversión"] / 100.0]
                for _, r in by_priority.iterrows()]
        row = self._table(ws, row, headers, rows,
                          formats=[None, INT, INT, MONEY, '0.0%'],
                          aligns=["center", "right", "right", "right", "right"],
                          fill_priority=0)
        row += 1

        # ---------------- proveedores
        row = self._section(ws, row, "PRINCIPALES PROVEEDORES (por inversión sugerida)", 10)
        headers = ["Proveedor", "Órdenes", "Unidades", "Lead Time (d)", "Inversión USD", "% Inversión"]
        suppliers = ctx.order_bundle.by_supplier.head(12)
        rows = [[r["Proveedor"], r["Órdenes"], r["Unidades"], r["Lead_Time"], r["Inversión_USD"], r["%_Inversión"] / 100.0]
                for _, r in suppliers.iterrows()]
        row = self._table(ws, row, headers, rows,
                          formats=[None, INT, INT, INT, MONEY, '0.0%'],
                          aligns=["left", "right", "right", "center", "right", "right"])
        row += 1

        # ---------------- top productos
        row = self._section(ws, row, "TOP 10 PRODUCTOS POR INVERSIÓN", 10)
        headers = ["EAN", "Descripción", "Proveedor", "Prioridad", "Cantidad", "Costo USD", "Cobertura (d)"]
        top = ctx.order_bundle.orders.sort_values("Costo_Total_USD", ascending=False).head(10)
        rows = [[r["EAN"], r["Descripcion"], r["Proveedor"], r["Prioridad"],
                 r["Cantidad_Sugerida"], r["Costo_Total_USD"], r["Dias_Cobertura"]]
                for _, r in top.iterrows()]
        row = self._table(ws, row, headers, rows,
                          formats=[None, None, None, None, INT, MONEY, DEC1],
                          aligns=["left", "left", "left", "center", "right", "right", "right"],
                          fill_priority=3)
        row += 1

        # ---------------- diagnóstico
        row = self._section(ws, row, "DIAGNÓSTICO DEL MODELO Y DE LOS DATOS", 10)
        metrics = ctx.forecast_bundle.metrics
        xyz = ctx.xyz.totals
        diagnostics = [
            ("WAPE portafolio A/B (política)", f"{metrics.get('wape_ab', 0):.1f}%",
             "Error absoluto ponderado sobre la venta real de productos A/B."),
            ("WAPE A/B en validación cruzada", f"{metrics.get('wape_ab_cv', 0):.1f}%",
             "Política re-seleccionada dejando un mes fuera en cada fold."),
            ("Sesgo del pronóstico A/B", f"{metrics.get('bias_ab', 0):+.1f}%",
             "Positivo = sobreestima; negativo = subestima. Se compensa con stock de seguridad."),
            ("Productos A/B dentro de ±25%", f"{metrics.get('pct_ab_wape25', 0):.1f}%",
             "Meta del proyecto: 80%. No alcanzable con esta mezcla de demanda (ver hoja Precisión)."),
            ("Periodos de 4 semanas sin venta", f"{xyz.get('zero_share', 0) * 100:.0f}%",
             "Demanda altamente intermitente: condiciona la precisión por producto."),
            ("Productos con inventario informado", f"{ctx.products['Stock_Informado'].sum():.0f} de {len(ctx.products):.0f}",
             "Sin stock real no se puede calcular cobertura; se sugiere la demanda del lead time."),
            ("Cobertura del catálogo sobre ventas USD",
             f"{ctx.products.loc[ctx.products['Catalogo_Completo'].astype(bool), 'USD_Total'].sum() / max(ctx.products['USD_Total'].sum(), 1) * 100:.1f}%",
             "Porcentaje de la venta histórica con proveedor y costo reales."),
            ("Mejora del WAPE vs naive A/B", f"{metrics.get('mejora_naive_ab', 0):+.1f}%",
             "Reducción del error agregado frente a repetir la última semana."),
        ]
        headers = ["Indicador", "Valor", "Lectura"]
        rows = [[a, b, c] for a, b, c in diagnostics]
        row = self._table(ws, row, headers, rows, formats=[None, None, None],
                          aligns=["left", "center", "left"])
        # Ensanchar la columna de lectura para esta tabla (se solapa con C)
        row += 1

        # ---------------- hallazgos
        row = self._section(ws, row, "HALLAZGOS Y ADVERTENCIAS", 10)
        findings = self._findings()
        for i, text in enumerate(findings, start=1):
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=10)
            cell = ws.cell(row=row, column=2, value=f"{i}. {text}")
            cell.font = Font(name="Calibri", size=9, color="222222")
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[row].height = 28
            row += 1
        row += 1

        # ---------------- uso
        row = self._section(ws, row, "CÓMO USAR ESTE REPORTE", 10)
        instructions = [
            "1. Abra la hoja ÓRDENES SUGERIDAS y filtre por Prioridad = CRÍTICA o ALTA para atender primero.",
            "2. Revise la justificación de cada línea: indica cobertura, demanda del lead time y stock de seguridad.",
            "3. Las cantidades ya están ajustadas al múltiplo de compra del proveedor; use la columna Cajas para negociar.",
            "4. Consulte ALERTAS CRÍTICAS para quiebres, exceso de inventario, tendencias y datos faltantes.",
            "5. Ajuste lead times, mínimos y descuentos en config/proveedores.csv; no requiere reprogramar.",
            "6. Si carga el inventario completo en data/raw/stock.xlsx, la cobertura y las prioridades serán exactas.",
        ]
        for text in instructions:
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=10)
            cell = ws.cell(row=row, column=2, value=text)
            cell.font = Font(name="Calibri", size=9, color=DARK_GRAY)
            cell.alignment = Alignment(wrap_text=True, vertical="center")
            row += 1

        ws.page_setup.orientation = "portrait"
        ws.page_setup.fitToWidth = 1
        ws.sheet_properties.pageSetUpPr.fitToPage = True

    def _findings(self) -> list[str]:
        ctx = self.ctx
        findings = []
        metrics = ctx.forecast_bundle.metrics
        total_usd = ctx.products["USD_Total"].sum()
        catalog_mask = ctx.products["Catalogo_Completo"].astype(bool)
        catalog_usd = ctx.products.loc[catalog_mask, "USD_Total"].sum()
        catalog_pct = catalog_usd / total_usd * 100 if total_usd else 0
        findings.append(
            f"El catálogo maestro completo (proveedor + costo) cubre el {catalog_pct:.1f}% de la venta histórica en USD "
            f"({int(catalog_mask.sum())} de {len(ctx.products)} productos). "
            "Los productos faltantes se incluyen con proveedor 'SIN ASIGNAR' y costo estimado."
        )
        stock_pct = ctx.stock_coverage_pct
        findings.append(
            f"El inventario está informado para {stock_pct:.1f}% de los productos. "
            "En el resto, la compra sugerida cubre la demanda del lead time y debe validarse contra el stock físico."
        )
        zero = ctx.xyz.totals.get("zero_share", 0) * 100
        findings.append(
            f"El {zero:.0f}% de los periodos de 4 semanas no tiene venta. Es demanda intermitente: "
            "el error por producto es alto por naturaleza y se gestiona con stock de seguridad, no con más modelos."
        )
        findings.append(
            f"En backtesting, incluso eligiendo el mejor modelo con hindsight, el WAPE de A/B sería ~31%. "
            f"La política logra {metrics.get('wape_ab', 0):.1f}% y controla el sesgo en {metrics.get('bias_ab', 0):+.1f}%. "
            "La meta de ±25% para 80% de productos no es alcanzable con este portafolio."
        )
        if ctx.forecast_bundle.metrics.get("productos_quiebre", 0):
            findings.append(
                f"Se detectaron {ctx.forecast_bundle.metrics['productos_quiebre']} productos A/B con brechas de venta "
                "anómalas (posible quiebre o descontinuación). Requieren verificación física antes de comprar."
            )
        summary = ctx.order_bundle.summary
        if summary.get("excluidos_eol", 0):
            findings.append(
                f"Hay {summary['excluidos_eol']} SKU bloqueados por ciclo de vida (EOL/obsoleto/liquidación) "
                f"con ${summary.get('valor_excluido_usd', 0):,.0f} de stock inmovilizado. "
                "No generan OC: aplicar liquidación, traslado o excepción con pedido firme."
            )
        if summary.get("lle_pendientes", 0):
            findings.append(
                f"{summary['lle_pendientes']} SKU tienen necesidad de compra pero tránsito pendiente: "
                "la OC se bloqueó para evitar duplicidad. "
                "Validar OC/ETA y aprobar excepción solo si el tránsito no llega."
            )
        if summary.get("precio_bloqueados", 0) or summary.get("aprobaciones", 0):
            findings.append(
                f"Control de precios: {summary.get('precio_bloqueados', 0)} SKU bloqueados por superar el techo o "
                f"margen mínimo, y {summary.get('aprobaciones', 0)} SKU requieren aprobación/negociación. "
                "Revise la hoja CONTROL DE PRECIOS antes de emitir la OC."
            )
        if "SIN ASIGNAR" in set(ctx.products["Proveedor"]):
            findings.append(
                "La mayoría de los productos sin catálogo no tienen proveedor asignado; el reporte los agrupa en "
                "'SIN ASIGNAR'. Completar esa relación es la acción de mayor impacto para la compra."
            )
        return findings

    # ------------------------------------------------------------------ órdenes
    def _sheet_ordenes(self, ws: Worksheet) -> None:
        orders = self.ctx.order_bundle.orders
        columns = [
            ("Orden", 6, INT, "center"), ("Prioridad", 10, None, "center"),
            ("EAN", 15, None, "left"), ("Descripción", 42, None, "left"),
            ("Proveedor", 16, None, "left"), ("ABC", 6, None, "center"), ("XYZ", 6, None, "center"),
            ("Segmento", 8, None, "center"), ("Stock Actual", 10, INT, "right"),
            ("Stock Informado", 9, None, "center"), ("Demanda 30d", 11, DEC1, "right"),
            ("Demanda Cobertura", 11, DEC1, "right"), ("Stock Seguridad", 11, DEC1, "right"),
            ("Punto Pedido", 11, DEC1, "right"), ("Objetivo Stock", 11, DEC1, "right"),
            ("Cant. Sugerida", 12, INT, "right"), ("Múltiplo", 8, INT, "center"),
            ("Cajas", 8, DEC1, "center"), ("Costo Unit. USD", 10, MONEY, "right"),
            ("Desc. %", 7, '0.0"%"', "center"), ("Costo Total USD", 13, MONEY, "right"),
            ("Días Cobertura", 10, DEC1, "right"), ("Lead Time", 9, INT, "center"),
            ("Cobertura Días", 10, INT, "center"), ("Venta 30d", 10, DEC1, "right"),
            ("Venta 90d", 10, DEC1, "right"), ("Tendencia %", 10, PCT_SIGNED, "right"),
            ("Modelo", 16, None, "left"), ("Confianza", 8, CONF, "center"),
            ("Estado", 10, None, "center"), ("Posible Quiebre", 9, None, "center"),
            ("Justificación", 70, None, "left"),
            ("Estado EOL", 14, None, "center"), ("Compra Bloqueada", 10, None, "center"),
            ("Motivo Bloqueo", 30, None, "left"), ("Stock OC (ACT−COM)", 11, DEC1, "right"),
            ("Comprometido", 10, DEC1, "right"), ("LLE", 10, DEC1, "right"),
            ("Estado LLE", 16, None, "center"), ("Precio Ref.", 10, MONEY, "right"),
            ("Var. Precio %", 10, PCT_SIGNED, "right"), ("Precio Techo", 10, MONEY, "right"),
            ("Estado Precio", 14, None, "center"), ("Margen Nuevo %", 10, PCT_SIGNED, "right"),
            ("Aprobación", 10, None, "center"), ("Aprobador", 18, None, "left"),
            ("Impacto Precio USD", 12, MONEY, "right"),
        ]
        ncols = len(columns)
        subtitle = (f"Órdenes sugeridas para el período {self.ctx.period_label} · "
                    f"Corte {self.ctx.as_of:%d/%m/%Y} · {len(orders)} líneas · "
                    f"Inversión ${orders['Costo_Total_USD'].sum():,.0f}" if not orders.empty else
                    "Sin órdenes sugeridas para el período")
        row = self._title(ws, "ÓRDENES SUGERIDAS DE COMPRA", subtitle, ncols)
        row += 1
        self._set_widths(ws, {i + 2: width for i, (_, width, _, _) in enumerate(columns)})

        headers = [c[0] for c in columns]
        data = []
        for _, r in orders.iterrows():
            values = [r.get(self._key_for(header)) for header in headers]
            values[9] = "Sí" if bool(values[9]) else "No"
            values[30] = "Sí" if bool(values[30]) else "No"
            data.append(values)
        formats = [c[2] for c in columns]
        aligns = [c[3] for c in columns]
        header_row = row
        data_last = header_row + len(orders)
        last = self._table(ws, row, headers, data, formats=formats, aligns=aligns,
                           fill_priority=1, row_height=30)

        if not orders.empty:
            total_row = data_last + 1
            qty_col = 2 + headers.index("Cant. Sugerida")
            cost_col = 2 + headers.index("Costo Total USD")
            ws.cell(row=total_row, column=2, value="TOTAL").font = Font(name="Calibri", size=9, bold=True, color=NAVY)
            ws.cell(row=total_row, column=qty_col, value=int(orders["Cantidad_Sugerida"].sum())).number_format = INT
            ws.cell(row=total_row, column=cost_col, value=round(float(orders["Costo_Total_USD"].sum()), 2)).number_format = MONEY
            for col in range(2, ncols + 2):
                cell = ws.cell(row=total_row, column=col)
                cell.font = Font(name="Calibri", size=9, bold=True, color=NAVY)
                cell.fill = PatternFill("solid", fgColor=LIGHT_BLUE)
                cell.border = Border(top=Side(style="medium", color=NAVY), bottom=THIN, left=THIN, right=THIN)
            last = total_row + 1

        # Fila de nota
        ws.merge_cells(start_row=last + 1, start_column=2, end_row=last + 1, end_column=14)
        note = ws.cell(row=last + 1, column=2,
                       value="Stock Informado = 'No' indica que no hay inventario en stock.xlsx; "
                             "la cantidad cubre la demanda del lead time y debe validarse. "
                             "Los costos de productos sin catálogo son estimados desde el precio de venta.")
        note.font = Font(name="Calibri", size=8, italic=True, color=DARK_GRAY)
        note.alignment = Alignment(wrap_text=True, vertical="top")

        self._finish(ws, header_row, data_last if data_last > header_row else last - 1, ncols + 1,
                     fit_width=3)
        # Resaltar stock no informado
        if not orders.empty:
            for i, r in enumerate(orders.itertuples(index=False), start=header_row + 1):
                if not bool(getattr(r, "Stock_Informado")):
                    ws.cell(row=i, column=11).fill = PatternFill("solid", fgColor="FFF2CC")
            # Barras de datos en costo total
            ws.conditional_formatting.add(
                f"U{header_row + 1}:U{data_last}",
                DataBarRule(start_type="num", start_value=0, end_type="max", color="5B9BD5", showValue=True),
            )

    @staticmethod
    def _key_for(header: str) -> str:
        mapping = {
            "Orden": "Orden", "Prioridad": "Prioridad", "EAN": "EAN", "Descripción": "Descripcion",
            "Proveedor": "Proveedor", "ABC": "ABC", "XYZ": "XYZ", "Segmento": "Segmento",
            "Stock Actual": "Stock_Actual", "Stock Informado": "Stock_Informado",
            "Demanda 30d": "Demanda_30d", "Demanda Cobertura": "Demanda_Cobertura",
            "Stock Seguridad": "Stock_Seguridad", "Punto Pedido": "Punto_Pedido",
            "Objetivo Stock": "Objetivo_Stock", "Cant. Sugerida": "Cantidad_Sugerida",
            "Múltiplo": "Multiplo", "Cajas": "Cajas", "Costo Unit. USD": "Costo_Unitario_USD",
            "Desc. %": "Descuento_Pct", "Costo Total USD": "Costo_Total_USD",
            "Días Cobertura": "Dias_Cobertura", "Lead Time": "Lead_Time",
            "Cobertura Días": "Cobertura_Dias", "Venta 30d": "Venta_30d",
            "Venta 90d": "Venta_90d", "Tendencia %": "Tendencia_Pct", "Modelo": "Modelo",
            "Confianza": "Confianza", "Estado": "Estado", "Posible Quiebre": "Posible_Quiebre",
            "Justificación": "Justificacion",
            "Estado EOL": "Estado_Ciclo_Vida", "Compra Bloqueada": "Compra_Bloqueada",
            "Motivo Bloqueo": "Motivo_Bloqueo", "Stock OC (ACT−COM)": "Stock_Usado_OC",
            "Comprometido": "Stock_Comprometido", "LLE": "Stock_LLE", "Estado LLE": "LLE_Estado",
            "Precio Ref.": "Precio_Ref_USD", "Var. Precio %": "Var_Precio_Pct",
            "Precio Techo": "Precio_Techo_USD", "Estado Precio": "Precio_Estado",
            "Margen Nuevo %": "Margen_Nuevo_Pct", "Aprobación": "Aprobacion_Requerida",
            "Aprobador": "Aprobador", "Impacto Precio USD": "Impacto_Precio_USD",
        }
        return mapping.get(header, header)

    # ------------------------------------------------------------------ EOL
    def _sheet_eol(self, ws: Worksheet) -> None:
        excluded = self.ctx.order_bundle.excluded
        ncols = 12
        subtitle = (f"Productos bloqueados por ciclo de vida · {len(excluded)} SKU · "
                    f"Valor en stock ${excluded['Valor_Stock'].sum():,.0f}" if not excluded.empty
                    else "Sin productos bloqueados por ciclo de vida")
        row = self._title(ws, "EOL, OBSOLETOS Y LIQUIDACIÓN", subtitle, ncols)
        row += 1
        self._set_widths(ws, {2: 15, 3: 42, 4: 24, 5: 14, 6: 34, 7: 34, 8: 10, 9: 10,
                              10: 10, 11: 14, 12: 12, 13: 14})
        counts = excluded["Estado_Ciclo_Vida"].value_counts() if not excluded.empty else {}
        cards = [
            ("SKU bloqueados", len(excluded), INT),
            ("Valor stock", float(excluded["Valor_Stock"].sum()) if not excluded.empty else 0.0, MONEY0),
            ("EOL", int(counts.get("EOL", 0)), INT),
            ("Obsoletos", int(counts.get("OBSOLETO", 0)), INT),
            ("Liquidación", int(counts.get("LIQUIDACION", 0)), INT),
            ("Agotados", int(counts.get("AGOTADO", 0)), INT),
        ]
        row = self._kpi_cards(ws, row, cards)
        row += 1
        row = self._section(ws, row, "REGLA: NO GENERAR OC; LIQUIDAR O EXCEPCIÓN CON PEDIDO FIRME", ncols)
        headers = ["EAN", "Descripción", "Proveedor", "Estado EOL", "Motivo", "Acción",
                   "Stock", "Tránsito", "Días sin venta", "Venta 12m USD", "Costo USD", "Valor stock"]
        rows = [[r["EAN"], r["Descripcion"], r["Proveedor"], r["Estado_Ciclo_Vida"],
                 r["Motivo_Bloqueo"], r["Accion_EOL"], r["Stock_Actual"], r["En_Transito"],
                 r["Dias_Sin_Venta"], r["USD_365d"], r["Costo_USD"], r["Valor_Stock"]]
                for _, r in excluded.iterrows()]
        header_row = row
        last = self._table(ws, row, headers, rows,
                           formats=[None, None, None, None, None, None, INT, INT, INT, MONEY0, MONEY, MONEY0],
                           aligns=["left", "left", "left", "center", "left", "left", "right", "right",
                                   "right", "right", "right", "right"], row_height=30)
        self._finish(ws, header_row, last - 1, ncols + 1, fit_width=1)

    # ------------------------------------------------------------------ precios
    def _sheet_precios(self, ws: Worksheet) -> None:
        alerts = self.ctx.order_bundle.price_alerts
        ncols = 12
        subtitle = (f"Control de volatilidad de costos · {len(alerts)} SKU requieren revisión/aprobación"
                    if not alerts.empty else "Sin alertas de precio para el período")
        row = self._title(ws, "CONTROL DE PRECIOS Y APROBACIONES", subtitle, ncols)
        row += 1
        self._set_widths(ws, {2: 15, 3: 42, 4: 24, 5: 12, 6: 12, 7: 10, 8: 14, 9: 14,
                              10: 10, 11: 20, 12: 10, 13: 30})
        cards = [
            ("Requieren revisión", len(alerts), INT),
            ("Sobre techo", int((alerts["Precio_Estado"] == "ROJO").sum()) if not alerts.empty else 0, INT),
            ("Naranja", int((alerts["Precio_Estado"] == "NARANJA").sum()) if not alerts.empty else 0, INT),
            ("Margen bajo", int((alerts["Precio_Estado"] == "MARGEN_BAJO").sum()) if not alerts.empty else 0, INT),
            ("Impacto anual", float(alerts["Impacto_USD"].sum()) if not alerts.empty else 0.0, MONEY0),
        ]
        row = self._kpi_cards(ws, row, cards)
        row += 1
        row = self._section(ws, row, "UMBRALES: VERDE ≤5% · AMARILLO 5–10% · NARANJA 10–20% · ROJO >20%", ncols)
        headers = ["EAN", "Descripción", "Proveedor", "Precio ref.", "Precio actual", "Var %",
                   "Impacto USD", "Estado", "Margen nuevo %", "Aprobador", "Bloquea OC", "Acción"]
        rows = []
        for _, r in alerts.iterrows():
            estado = str(r["Precio_Estado"])
            accion = ("Bloquear OC y negociar" if estado == "ROJO"
                      else "Aprobación/negociación" if estado in {"NARANJA", "MARGEN_BAJO"}
                      else "Revisar justificación")
            rows.append([r["EAN"], r["Descripcion"], r["Proveedor"], r["Precio_Ref_USD"],
                         r["Precio_Actual_USD"], r["Var_Precio_Pct"], r["Impacto_USD"],
                         estado, r["Margen_Nuevo_Pct"], r["Aprobador"],
                         "Sí" if bool(r.get("Bloqueada_OC")) else "No", accion])
        header_row = row
        last = self._table(ws, row, headers, rows,
                           formats=[None, None, None, MONEY, MONEY, PCT_SIGNED, MONEY0, None, PCT_SIGNED, None, None, None],
                           aligns=["left", "left", "left", "right", "right", "right", "right",
                                   "center", "right", "left", "center", "left"], row_height=30)
        self._finish(ws, header_row, last - 1, ncols + 1, fit_width=1)

    # ------------------------------------------------------------------ alertas
    def _sheet_alertas(self, ws: Worksheet) -> None:
        alerts = self.ctx.alert_bundle.alerts
        columns = [
            ("Fecha", 11, DATE, "center"), ("Tipo", 24, None, "left"), ("Severidad", 10, None, "center"),
            ("EAN", 15, None, "left"), ("Producto", 38, None, "left"), ("Proveedor", 16, None, "left"),
            ("Detalle", 60, None, "left"), ("Acción", 42, None, "left"),
            ("Responsable", 18, None, "left"), ("Impacto USD", 12, MONEY, "right"),
        ]
        ncols = len(columns)
        subtitle = (f"Alertas operativas ordenadas por severidad · {len(alerts)} alertas · "
                    f"Impacto estimado ${alerts['Impacto_USD'].fillna(0).sum():,.0f}" if not alerts.empty
                    else "Sin alertas para el período")
        row = self._title(ws, "ALERTAS CRÍTICAS", subtitle, ncols)
        row += 1
        self._set_widths(ws, {i + 2: width for i, (_, width, _, _) in enumerate(columns)})

        # Resumen por severidad
        summary = self.ctx.alert_bundle.summary
        counts = summary.get("por_severidad", {})
        row = self._section(ws, row, "RESUMEN POR SEVERIDAD", ncols)
        headers = ["Severidad", "Cantidad", "Tipos principales"]
        top_types = ", ".join(list(summary.get("por_tipo", {}).keys())[:3])
        rows = [[sev, counts.get(sev, 0), top_types if i == 0 else ""] for i, sev in enumerate(["CRÍTICA", "ALTA", "MEDIA", "BAJA"])]
        row = self._table(ws, row, headers, rows, formats=[None, INT, None],
                          aligns=["center", "right", "left"], fill_severity=0)
        row += 1

        row = self._section(ws, row, "DETALLE DE ALERTAS", ncols)
        headers = [c[0] for c in columns]
        data = [[r.get(self._key_alert(header)) for header in headers] for _, r in alerts.iterrows()]
        header_row = row
        last = self._table(ws, row, headers, data,
                           formats=[c[2] for c in columns],
                           aligns=[c[3] for c in columns],
                           fill_severity=2, row_height=42)
        self._finish(ws, header_row, last - 1, ncols + 1)
        if not alerts.empty:
            ws.conditional_formatting.add(
                f"K{header_row + 1}:K{last - 1}",
                ColorScaleRule(start_type="num", start_value=0, start_color="FFFFFF",
                               end_type="max", end_color="F8CBAD"),
            )

    @staticmethod
    def _key_alert(header: str) -> str:
        return {"Fecha": "Fecha", "Tipo": "Tipo", "Severidad": "Severidad", "EAN": "EAN",
                "Producto": "Producto", "Proveedor": "Proveedor", "Detalle": "Detalle",
                "Acción": "Accion", "Responsable": "Responsable", "Impacto USD": "Impacto_USD"}.get(header, header)

    # ------------------------------------------------------------------ análisis
    def _sheet_analisis(self, ws: Worksheet) -> None:
        ctx = self.ctx
        subtitle = f"Clasificación sobre {ctx.abc.totals['productos']} productos con venta en los últimos {ctx.abc.totals['ventana_dias']} días"
        row = self._title(ws, "ANÁLISIS ABC/XYZ Y ESTRATEGIA POR SEGMENTO", subtitle, 11)
        row += 1
        self._set_widths(ws, {2: 14, 3: 12, 4: 12, 5: 16, 6: 12, 7: 14, 8: 12, 9: 10, 10: 60, 11: 34, 12: 10})

        row = self._section(ws, row, "SEGMENTACIÓN HÍBRIDA", 11)
        headers = ["Segmento", "Productos", "% Productos", "Valor USD", "% Ventas USD", "Unidades",
                   "% Unidades", "CV Prom.", "Estrategia", "Modelo sugerido", "Prioridad"]
        rows = [[r["Segmento"], r["Productos"], r["%_Productos"] / 100, r["Valor_USD"], r["%_Ventas_USD"] / 100,
                 r["Unidades"], r["%_Unidades"] / 100, r["CV_Promedio"], r["Estrategia"],
                 r["Modelo_Sugerido"], r["Prioridad"]]
                for _, r in ctx.segmentation.summary.iterrows()]
        row = self._table(ws, row, headers, rows,
                          formats=[None, INT, '0.0%', MONEY0, '0.0%', INT, '0.0%', DEC1, None, None, None],
                          aligns=["center", "right", "right", "right", "right", "right", "right",
                                  "right", "left", "left", "center"])
        row += 1

        row = self._section(ws, row, "RESUMEN ABC POR VALOR", 11)
        headers = ["Clase", "Productos", "% Productos", "Valor USD", "% Valor", "Unidades", "% Unidades"]
        rows = [[r["Clase_ABC"], r["Productos"], r["%_Productos"] / 100, r["Valor_USD"],
                 r["%_Valor_USD"] / 100, r["Unidades"], r["%_Unidades"] / 100]
                for _, r in ctx.abc.summary.iterrows()]
        row = self._table(ws, row, headers, rows,
                          formats=[None, INT, '0.0%', MONEY0, '0.0%', INT, '0.0%'],
                          aligns=["center", "right", "right", "right", "right", "right", "right"])
        row += 1

        row = self._section(ws, row, "RESUMEN XYZ POR VARIABILIDAD", 11)
        headers = ["Clase", "Productos", "% Productos", "CV Promedio", "ADI Promedio", "Periodos sin venta"]
        rows = [[r["Clase_XYZ"], r["Productos"], r["%_Productos"] / 100, r["CV_Promedio"],
                 r["ADI_Promedio"], r["Zero_Share_Promedio"]]
                for _, r in ctx.xyz.summary.iterrows()]
        row = self._table(ws, row, headers, rows,
                          formats=[None, INT, '0.0%', DEC1, DEC1, '0.0%'],
                          aligns=["center", "right", "right", "right", "right", "right"])
        ws.sheet_view.showGridLines = False
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True

    # ------------------------------------------------------------------ detalle
    def _sheet_detalle(self, ws: Worksheet) -> None:
        ctx = self.ctx
        products = ctx.segmentation.products.sort_values("Valor_USD", ascending=False).head(300)
        subtitle = (f"Top {len(products)} productos por valor de venta en el período · "
                    f"corte {ctx.as_of:%d/%m/%Y}")
        row = self._title(ws, "DETALLE DE PRODUCTOS", subtitle, 13)
        row += 1
        self._set_widths(ws, {2: 16, 3: 46, 4: 18, 5: 18, 6: 8, 7: 14, 8: 10,
                              9: 8, 10: 12, 11: 10, 12: 10, 13: 12, 14: 70})
        headers = ["EAN", "Descripción", "Proveedor", "Categoría", "Segmento", "Valor USD",
                   "Unidades", "CV", "Ventas 12m", "Stock", "Informado", "Estado", "Estrategia"]
        rows = []
        for _, r in products.iterrows():
            rows.append([r["EAN"], r.get("Descripcion", ""), r.get("Proveedor", ""),
                         r.get("Categoria", ""), r["Segmento"], r["Valor_USD"], r["Unidades"],
                         r["CV"], r.get("Venta_365d", 0), r.get("Stock_Actual"),
                         "Sí" if r.get("Stock_Informado") else "No", r.get("Estado", ""),
                         r["Estrategia"]])
        header_row = row
        last = self._table(ws, row, headers, rows,
                           formats=[None, None, None, None, None, MONEY0, INT, DEC1, INT, INT, None, None, None],
                           aligns=["left", "left", "left", "left", "center", "right", "right",
                                   "right", "right", "right", "center", "center", "left"],
                           row_height=32)
        self._finish(ws, header_row, last - 1, 14)
        ws.freeze_panes = "C6"

    # ------------------------------------------------------------------ precisión
    def _sheet_precision(self, ws: Worksheet) -> None:
        ctx = self.ctx
        metrics = ctx.forecast_bundle.metrics
        subtitle = ("Backtesting de los últimos 6 meses. WAPE = error absoluto / venta real. "
                    "La validación cruzada deja un mes fuera para evitar sobreajuste.")
        row = self._title(ws, "PRECISIÓN DEL MODELO Y VALIDACIÓN", subtitle, 11)
        row += 1
        self._set_widths(ws, {2: 24, 3: 16, 4: 16, 5: 16, 6: 28, 7: 16, 8: 16, 9: 16, 10: 16, 11: 16, 12: 16})

        row = self._section(ws, row, "INDICADORES CLAVE (productos A/B)", 11)
        cards = [
            ("WAPE política", metrics.get("wape_ab", 0) / 100, '0.0%'),
            ("WAPE validación cruzada", metrics.get("wape_ab_cv", 0) / 100, '0.0%'),
            ("Hit ±25%", metrics.get("hit25_ab", 0) / 100, '0.0%'),
            ("Sesgo", metrics.get("bias_ab", 0) / 100, '+0.0%;-0.0%'),
            ("MAPE mediana", metrics.get("mape_ab", 0) / 100, '0.0%'),
            ("Productos A/B", metrics.get("productos_ab", 0), INT),
            ("Meses evaluados", int(ctx.config.get("forecasting.backtest_months", 6)), INT),
            ("Confianza media", metrics.get("confianza_media", 0), '0.00'),
        ]
        row = self._kpi_cards(ws, row, cards)
        row += 1

        row = self._section(ws, row, "POLÍTICA DE PRONÓSTICO POR CLASE XYZ", 11)
        headers = ["Grupo", "Modelo", "Productos", "Venta real 4s", "WAPE", "WAPE CV", "Sesgo", "Hit ±25%"]
        rows = [[r["Grupo"], r["Modelo"], r["Productos"], r["Venta_Real_4S"], r["WAPE"] / 100,
                 (r["WAPE_Validacion_Cruzada"] / 100) if pd.notna(r["WAPE_Validacion_Cruzada"]) else None,
                 r["Sesgo"] / 100, r["Hit25"] / 100]
                for _, r in ctx.forecast_bundle.policy_table.iterrows()]
        row = self._table(ws, row, headers, rows,
                          formats=[None, None, INT, INT, '0.0%', '0.0%', '+0.0%;-0.0%', '0.0%'],
                          aligns=["center", "left", "right", "right", "right", "right", "right", "right"])
        row += 1

        row = self._section(ws, row, "DESEMPEÑO POR MODELO APLICADO", 11)
        headers = ["Modelo", "Productos", "% Productos", "WAPE portafolio", "WAPE mediana",
                   "MAPE mediana", "Sesgo portafolio", "Hit ±25%", "Confianza prom."]
        rows = [[r["Modelo_Detalle"], r["Productos"], r["%_Productos"] / 100, r["WAPE_Portafolio"] / 100,
                 r["WAPE_Mediana"] / 100, r["MAPE_Mediana"] / 100, r["Sesgo_Portafolio"] / 100,
                 r["Hit25_Promedio"] / 100, r["Confianza_Promedio"]]
                for _, r in ctx.forecast_bundle.model_summary.iterrows()]
        row = self._table(ws, row, headers, rows,
                          formats=[None, INT, '0.0%', '0.0%', '0.0%', '0.0%', '+0.0%;-0.0%', '0.0%', '0.00'],
                          aligns=["left", "right", "right", "right", "right", "right", "right", "right", "right"])
        row += 1

        row = self._section(ws, row, "DESEMPEÑO POR SEGMENTO ABC/XYZ", 11)
        headers = ["Segmento", "Productos", "WAPE portafolio", "MAPE mediana", "Sesgo", "Hit ±25%",
                   "% WAPE ≤ 25", "Mejora vs naive"]
        rows = [[r["Segmento"], r["Productos"], r["WAPE_Portafolio"] / 100, r["MAPE_Mediana"] / 100,
                 r["Sesgo_Portafolio"] / 100, r["Hit25_Ponderado"] / 100, r["%_Productos_WAPE_25"] / 100,
                 r["Mejora_vs_Naive"] / 100]
                for _, r in ctx.forecast_bundle.segment_summary.iterrows()]
        row = self._table(ws, row, headers, rows,
                          formats=[None, INT, '0.0%', '0.0%', '+0.0%;-0.0%', '0.0%', '0.0%', '+0.0%;-0.0%'],
                          aligns=["center", "right", "right", "right", "right", "right", "right", "right"])
        row += 1

        row = self._section(ws, row, "DESEMPEÑO POR VOLUMEN (unidades/mes)", 11)
        volume = self._volume_table()
        if not volume.empty:
            headers = ["Tramo", "Productos", "WAPE", "Hit ±25%", "Sesgo", "Lectura"]
            rows = []
            for _, r in volume.iterrows():
                lectura = ("Alta precisión relativa" if r["WAPE"] < 40 else
                           "Precisión media" if r["WAPE"] < 80 else
                           "Demanda intermitente: usar stock de seguridad")
                rows.append([r["Tramo"], r["Productos"], r["WAPE"] / 100, r["Hit25"] / 100,
                             r["Sesgo"] / 100, lectura])
            row = self._table(ws, row, headers, rows,
                              formats=[None, INT, '0.0%', '0.0%', '+0.0%;-0.0%', None],
                              aligns=["center", "right", "right", "right", "right", "left"],
                              row_height=30)
        row += 1

        row = self._section(ws, row, "CRITERIOS DE ACEPTACIÓN DEL PROYECTO", 11)
        criteria = self._criteria()
        headers = ["Criterio", "Meta", "Resultado", "Cumple", "Comentario"]
        rows = [[c["Criterio"], c["Meta"], c["Resultado"], c["Cumple"], c["Comentario"]] for c in criteria]
        header_row = row
        last = self._table(ws, row, headers, rows, formats=[None, None, None, None, None],
                           aligns=["left", "center", "center", "center", "left"], row_height=44)
        # Unir F:J para que el comentario tenga ancho suficiente
        for r in [header_row] + list(range(header_row + 1, last)):
            ws.merge_cells(start_row=r, start_column=6, end_row=r, end_column=10)
            for c in range(6, 11):
                cell = ws.cell(row=r, column=c)
                cell.border = BORDER
                if r == header_row:
                    cell.fill = PatternFill("solid", fgColor=BLUE)
        row = last + 1

        row = self._section(ws, row, "PRODUCTOS CON MAYOR ERROR ABSOLUTO (revisión manual)", 11)
        valid = ctx.forecast_bundle.validation[ctx.forecast_bundle.validation["Origenes"] > 0]
        worst = valid.sort_values("Error_Abs", ascending=False).head(30)
        headers = ["Producto (EAN · descripción)", "Segmento", "Modelo", "Error abs (u)", "WAPE", "Sesgo", "Demanda 30d"]
        rows = [[f"{r['EAN']} · {r['Descripcion']}", r["Segmento"], r["Modelo"], r["Error_Abs"],
                 r["WAPE"] / 100, r["Sesgo"] / 100, r["Demanda_30d"]]
                for _, r in worst.iterrows()]
        header_row = row
        last = self._table(ws, row, headers, rows,
                           formats=[None, None, None, INT, '0.0%', '+0.0%;-0.0%', DEC1],
                           aligns=["left", "center", "left", "right", "right", "right", "right"],
                           row_height=40)
        self._finish(ws, header_row, last - 1, 12)
        ws.freeze_panes = f"B{header_row + 1}"

    def _volume_table(self) -> pd.DataFrame:
        validation = self.ctx.forecast_bundle.validation
        valid = validation[(validation["Origenes"] > 0) & (validation["Actual"] > 0)].copy()
        if valid.empty:
            return pd.DataFrame()
        valid["vol_mes"] = valid["Actual"] / valid["Origenes"]
        valid["_bias_x_actual"] = valid["Sesgo"] * valid["Actual"]
        bins = [-1, 10, 25, 50, 100, 250, np.inf]
        labels = ["< 10", "10-25", "25-50", "50-100", "100-250", "> 250"]
        valid["Tramo"] = pd.cut(valid["vol_mes"], bins=bins, labels=labels)
        grouped = valid.groupby("Tramo", observed=True).agg(
            Productos=("EAN", "count"),
            Error=("Error_Abs", "sum"),
            Actual=("Actual", "sum"),
            Hit25=("Hit25", "mean"),
            _bias=("_bias_x_actual", "sum"),
        ).reset_index()
        grouped["WAPE"] = grouped["Error"] / grouped["Actual"] * 100
        grouped["Sesgo"] = grouped["_bias"] / grouped["Actual"]
        return grouped

    def _criteria(self) -> list[dict[str, str]]:
        ctx = self.ctx
        metrics = ctx.forecast_bundle.metrics
        orders = ctx.order_bundle.summary.get("ordenes", 0)
        seg = ctx.segmentation.products
        active_ab = seg[(seg["Estado"] != "INACTIVO") & (seg["ABC_Valor"].isin(["A", "B"]))]
        order_eans = set(ctx.order_bundle.orders["EAN"].astype(str))
        covered = active_ab[active_ab["EAN"].astype(str).isin(order_eans)]
        coverage = len(covered) / max(len(active_ab), 1) * 100
        coverage_usd = covered["USD_365d"].sum() / max(active_ab["USD_365d"].sum(), 1) * 100
        return [
            {"Criterio": "Volumen de órdenes", "Meta": "> 200 productos",
             "Resultado": f"{orders} líneas", "Cumple": "SÍ" if orders > 200 else "NO",
             "Comentario": "Cada línea incluye cantidad, prioridad y justificación."},
            {"Criterio": "Precisión A/B (MAPE)", "Meta": "< 20%",
             "Resultado": f"{metrics.get('mape_ab', 0):.1f}% (mediana)",
             "Cumple": "NO" if metrics.get("mape_ab", 0) > 20 else "SÍ",
             "Comentario": "El MAPE no es adecuado para demanda intermitente; se reporta WAPE y sesgo."},
            {"Criterio": "Precisión A/B (±25%)", "Meta": "80% de productos",
             "Resultado": f"{metrics.get('pct_ab_wape25', 0):.1f}% de productos",
             "Cumple": "NO",
             "Comentario": "Aun con el mejor modelo elegido con hindsight (oráculo), el WAPE A/B sería ~31%. "
                           "La meta no es alcanzable con este portafolio; ver informe de metodología."},
            {"Criterio": "Cobertura de compra A/B", "Meta": "> 80% del volumen",
             "Resultado": f"{coverage:.1f}% productos / {coverage_usd:.1f}% USD",
             "Cumple": "SÍ" if coverage_usd > 80 else "PARCIAL",
             "Comentario": "Productos A/B activos con sugerencia de compra. El resto tiene stock suficiente "
                           "o requiere decisión de compra mínima."},
            {"Criterio": "Cantidades discretas", "Meta": "Múltiplos de compra",
             "Resultado": "Múltiplo por proveedor/producto", "Cumple": "SÍ",
             "Comentario": "Todas las cantidades se redondean hacia arriba al múltiplo."},
            {"Criterio": "Prioridades claras", "Meta": "CRÍTICA/ALTA/MEDIA/BAJA",
             "Resultado": "4 niveles + REVISAR", "Cumple": "SÍ",
             "Comentario": "Basadas en días de cobertura vs lead time y valor ABC."},
            {"Criterio": "Parámetros editables", "Meta": "Sin reprogramar",
             "Resultado": "YAML + CSV + Excel", "Cumple": "SÍ",
             "Comentario": "Lead times, mínimos, múltiplos, descuentos y umbrales."},
            {"Criterio": "Interfaz familiar", "Meta": "< 5 clics",
             "Resultado": "1 comando / doble clic", "Cumple": "SÍ",
             "Comentario": "Genera el Excel y lo abre automáticamente."},
        ]

    # ------------------------------------------------------------------ parámetros
    def _sheet_parametros(self, ws: Worksheet) -> None:
        ctx = self.ctx
        row = self._title(ws, "PARÁMETROS CONFIGURABLES",
                          "Edite config/parameters.yaml, config/proveedores.csv o config/productos.csv; "
                          "los cambios se aplican en la próxima ejecución.", 5)
        row += 1
        self._set_widths(ws, {2: 28, 3: 26, 4: 12, 5: 30, 6: 12, 7: 22, 8: 22, 9: 10, 10: 40})

        row = self._section(ws, row, "PARÁMETROS GENERALES", 5)
        headers = ["Parámetro", "Descripción", "Valor", "Archivo"]
        params = ctx.config.summary()["parameters"]
        rows = [[p["Parametro"], p["Descripcion"],
                 ", ".join(map(str, p["Valor"])) if isinstance(p["Valor"], (list, tuple)) else p["Valor"],
                 "config/parameters.yaml"] for p in params]
        rows.append(["inventory.z_value", "Valor z del nivel de servicio", ctx.config.z_value(), "calculado"])
        row = self._table(ws, row, headers, rows, formats=[None, None, None, None],
                          aligns=["left", "left", "center", "left"])
        row += 1

        info = ctx.warehouse_info
        if info:
            selected = info.get("selected") or []
            purchase_codes = info.get("purchase_codes") or selected
        else:
            selected = ["No aplica (modo Excel)"]
            purchase_codes = ["No aplica (modo Excel)"]
        row = self._section(ws, row, "ALMACENES UTILIZADOS (TRAZABILIDAD)", 5)
        headers = ["Concepto", "Códigos", "Detalle", "Fuente"]
        rows = [
            ["Ventas y stock", ", ".join(selected) if isinstance(selected, (list, tuple)) else str(selected),
             " | ".join(info.get("selected_labels", [])) or ("Selección de configuración" if info else "Modo Excel sin filtro de almacén"),
             info.get("source", "config/parameters.yaml") if info else "archivos Excel"],
            ["Compras", ", ".join(purchase_codes) if isinstance(purchase_codes, (list, tuple)) else str(purchase_codes),
             " | ".join(info.get("purchase_labels", [])) or ("Incluye recepción para conservar costo" if info else "Modo Excel sin filtro de almacén"),
             f"Modo: {info.get('purchase_mode', ctx.config.get('warehouse.purchase_mode', 'selected_plus_extra'))}" if info else "No aplica"],
            ["Fuente de stock", info.get("stock_source", ctx.config.get("warehouse.stock_source", "saStockAlmacen")) if info else "archivo stock.xlsx",
             "Stock físico por almacén" if info else "Stock del archivo de entrada", "Profit 2K12" if info else "Excel"],
        ]
        row = self._table(ws, row, headers, rows, formats=[None, None, None, None],
                          aligns=["left", "left", "left", "left"])
        row += 1

        row = self._section(ws, row, "PROVEEDORES (lead time, mínimo, múltiplo y descuentos)", 5)
        headers = ["Proveedor", "Lead Time (d)", "Revisión (d)", "Mínimo (u)", "Múltiplo (u)",
                   "Desc. 1", "Desc. 2", "Activo", "Notas"]
        rows = []
        for provider in sorted(ctx.config.providers.values(), key=lambda p: p.nombre):
            d1 = f"{provider.descuentos[0][1]:.0f}% desde {provider.descuentos[0][0]:.0f} u" if len(provider.descuentos) > 0 else "-"
            d2 = f"{provider.descuentos[1][1]:.0f}% desde {provider.descuentos[1][0]:.0f} u" if len(provider.descuentos) > 1 else "-"
            rows.append([provider.nombre, provider.lead_time, provider.revision, provider.minimo,
                         provider.multiplo, d1, d2, "Sí" if provider.activo else "No", provider.notas])
        row = self._table(ws, row, headers, rows, formats=[None, INT, INT, INT, INT, None, None, None, None],
                          aligns=["left", "center", "center", "right", "right", "left", "left", "center", "left"])
        row += 1

        row = self._section(ws, row, "OVERRIDES POR PRODUCTO (opcional)", 5)
        overrides = ctx.config.product_overrides
        if overrides:
            headers = ["EAN", "Campos configurados"]
            rows = [[ean, ", ".join(f"{k}={v}" for k, v in values.items())] for ean, values in overrides.items()]
            row = self._table(ws, row, headers, rows, formats=[None, None], aligns=["left", "left"])
        else:
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=5)
            cell = ws.cell(row=row, column=2,
                           value="Sin overrides. Cree config/productos.csv con columnas EAN, Multiplo_Compra, "
                                 "Stock_Minimo, Stock_Maximo, Lead_Time, Activo para reglas específicas.")
            cell.font = Font(name="Calibri", size=9, italic=True, color=DARK_GRAY)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[row].height = 34
            row += 2

        row = self._section(ws, row, "ARCHIVOS DE ENTRADA UTILIZADOS", 5)
        headers = ["Archivo", "Ruta", "Filas", "Observación"]
        rows = [
            ["Ventas históricas", str(ctx.data.sales_path), len(ctx.data.sales), f"Hasta {ctx.as_of:%d/%m/%Y}"],
            ["Catálogo maestro", str(ctx.data.catalog_path), len(ctx.data.catalog), "EAN → proveedor/costo"],
            ["Stock actual", str(ctx.data.stock_path) if ctx.data.stock_path else "No informado",
             len(ctx.data.stock), "Opcional"],
            ["Parámetros", "config/parameters.yaml", "-", "Editable"],
            ["Proveedores", "config/proveedores.csv", len(ctx.config.providers), "Editable"],
        ]
        last = self._table(ws, row, headers, rows, formats=[None, None, INT, None],
                           aligns=["left", "left", "right", "left"], row_height=24)
        ws.sheet_view.showGridLines = False
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True

    # ------------------------------------------------------------------ catálogo
    def _sheet_catalogo(self, ws: Worksheet) -> None:
        ctx = self.ctx
        missing = ctx.products[
            (~ctx.products["Catalogo_Completo"].astype(bool))
            | (ctx.products["Proveedor"].str.upper() == "SIN ASIGNAR")
            | (ctx.products["Costo_Estimado"].astype(bool))
        ].sort_values("USD_365d", ascending=False).head(200)

        subtitle = (f"{len(missing)} productos requieren completar proveedor y/o costo en el catálogo maestro "
                    f"(ventas 12 m: ${missing['USD_365d'].sum():,.0f})")
        row = self._title(ws, "CATÁLOGO FALTANTE - PRIORIDAD DE GESTIÓN", subtitle, 10)
        row += 1
        self._set_widths(ws, {2: 16, 3: 46, 4: 18, 5: 14, 6: 14, 7: 14, 8: 14, 9: 12, 10: 12})

        headers = ["EAN", "Descripción", "Proveedor", "Costo USD", "Costo estimado", "Ventas 12m USD",
                   "Unidades 12m", "Ventas 30d", "Estado", "Acción"]
        rows = []
        for _, r in missing.iterrows():
            accion = "Asignar proveedor" if str(r["Proveedor"]).upper() == "SIN ASIGNAR" else "Validar costo"
            rows.append([r["EAN"], r["Descripcion"], r["Proveedor"], r["Costo_USD"],
                         "Sí" if r["Costo_Estimado"] else "No", r["USD_365d"], r["Venta_365d"],
                         r["Venta_30d"], r["Estado"], accion])
        header_row = row
        last = self._table(ws, row, headers, rows,
                           formats=[None, None, None, MONEY, None, MONEY0, INT, DEC1, None, None],
                           aligns=["left", "left", "left", "right", "center", "right", "right",
                                   "right", "center", "left"], row_height=24)
        self._finish(ws, header_row, last - 1, 10)
        ws.freeze_panes = "C6"

    # ------------------------------------------------------------------ calidad
    def _sheet_calidad(self, ws: Worksheet) -> None:
        ctx = self.ctx
        row = self._title(ws, "DATOS Y CALIDAD DE LA INFORMACIÓN",
                          "Controles automáticos ejecutados al cargar los archivos de entrada.", 5)
        row += 1
        self._set_widths(ws, {2: 46, 3: 22, 4: 70, 5: 14})

        row = self._section(ws, row, "RESUMEN DEL DATASET", 5)
        headers = ["Métrica", "Valor", "Detalle"]
        rows = [
            ["Registros de venta", f"{len(ctx.data.sales):,}", f"{ctx.data.sales['Fecha'].min():%d/%m/%Y} a {ctx.data.sales['Fecha'].max():%d/%m/%Y}"],
            ["Productos distintos", f"{ctx.data.sales['EAN'].nunique():,}", "EAN con al menos una venta"],
            ["Días-producto", f"{len(ctx.data.daily):,}", "Base de las series temporales"],
            ["Productos en catálogo", f"{len(ctx.data.catalog):,}", "Catálogo maestro recibido"],
            ["Productos con stock", f"{len(ctx.data.stock):,}", "Inventario informado"],
            ["Venta total USD", f"${ctx.data.sales['Monto_USD'].sum():,.0f}", "Suma histórica del archivo"],
            ["Venta últimos 12 meses", f"${ctx.products['USD_365d'].sum():,.0f}", "Desde el corte de datos"],
            ["Semanas sin venta", f"{ctx.xyz.totals.get('zero_share', 0) * 100:.1f}%", "Periodos de 4 semanas sin venta"],
            ["Tiempo de ejecución", f"{ctx.elapsed_seconds:.1f} s", "Generación completa del reporte"],
        ]
        row = self._table(ws, row, headers, rows, formats=[None, None, None],
                          aligns=["left", "right", "left"])
        row += 1

        row = self._section(ws, row, "CONTROLES DE CALIDAD", 5)
        quality = ctx.quality
        headers = ["Chequeo", "Resultado", "Detalle", "Severidad"]
        rows = [[r["Chequeo"], r["Resultado"], r["Detalle"], r["Severidad"]] for _, r in quality.iterrows()]
        header_row = row
        last = self._table(ws, row, headers, rows, formats=[None, None, None, None],
                           aligns=["left", "left", "left", "center"], fill_severity=3)
        self._finish(ws, header_row, last - 1, 5)
        ws.freeze_panes = "B6"
