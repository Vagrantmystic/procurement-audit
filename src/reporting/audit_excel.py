"""Reporte Excel de la auditoría de compras."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from audit.purchase_audit import AuditResult
from reporting.excel_generator import (
    BLUE, BORDER, CENTER, DARK_GRAY, INT, LIGHT_BLUE, MONEY, MONEY0, NAVY,
    PALE_BLUE, PCT, WHITE, _clean, _fmt_number,
)

STATE_FILL = {
    "QUIEBRE": ("C00000", WHITE),
    "CRÍTICO": ("C55A11", WHITE),
    "FALTANTE": ("FFC000", "3F3F3F"),
    "SOBRECOMPRA": ("7030A0", WHITE),
    "SIN VENTA": ("7F7F7F", WHITE),
    "SIN COMPRA 2026": ("2F5597", WHITE),
    "OK": ("548235", WHITE),
}


class AuditExcelGenerator:
    def __init__(self, audit: AuditResult, output_path: str | Path,
                 logger: logging.Logger | None = None):
        self.audit = audit
        self.path = Path(output_path)
        self.logger = logger or logging.getLogger("compras")
        self.wb = Workbook()

    # ------------------------------------------------------------------ helpers
    def _title(self, ws: Worksheet, title: str, subtitle: str, ncols: int) -> int:
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
        cell = ws.cell(row=1, column=1, value=title)
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(name="Calibri", size=15, bold=True, color=WHITE)
        cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        for col in range(1, ncols + 1):
            ws.cell(row=1, column=col).fill = PatternFill("solid", fgColor=NAVY)
        ws.row_dimensions[1].height = 30
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncols)
        cell = ws.cell(row=2, column=1, value=subtitle)
        cell.font = Font(name="Calibri", size=9, italic=True, color=DARK_GRAY)
        cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
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

    def _kpis(self, ws: Worksheet, row: int, cards: Sequence[tuple[str, Any, str | None]],
              per_row: int = 4) -> int:
        for index, (label, value, fmt) in enumerate(cards):
            if index > 0 and index % per_row == 0:
                row += 3
            col = 2 + (index % per_row) * 2
            ws.merge_cells(start_row=row, start_column=col, end_row=row, end_column=col + 1)
            ws.merge_cells(start_row=row + 1, start_column=col, end_row=row + 1, end_column=col + 1)
            label_cell = ws.cell(row=row, column=col, value=label)
            label_cell.font = Font(name="Calibri", size=9, color=DARK_GRAY)
            label_cell.alignment = CENTER
            value_cell = ws.cell(row=row + 1, column=col, value=_clean(value))
            value_cell.font = Font(name="Calibri", size=18, bold=True, color=NAVY)
            value_cell.alignment = CENTER
            if fmt:
                value_cell.number_format = fmt
            for r in (row, row + 1):
                for c in range(col, col + 2):
                    ws.cell(row=r, column=c).fill = PatternFill("solid", fgColor=PALE_BLUE)
                    ws.cell(row=r, column=c).border = BORDER
            ws.row_dimensions[row].height = 24
            ws.row_dimensions[row + 1].height = 30
        return row + 3

    def _table(self, ws: Worksheet, row: int, headers: Sequence[str],
               rows: Sequence[Sequence[Any]], formats: Sequence[str | None] | None = None,
               aligns: Sequence[str] | None = None, start_col: int = 2,
               state_col: int | None = None, row_height: int | None = None) -> int:
        ncols = len(headers)
        formats = formats or [None] * ncols
        aligns = aligns or ["left"] * ncols
        for j, header in enumerate(headers):
            cell = ws.cell(row=row, column=start_col + j, value=header)
            cell.fill = PatternFill("solid", fgColor=BLUE)
            cell.font = Font(name="Calibri", size=9, bold=True, color=WHITE)
            cell.alignment = CENTER
            cell.border = BORDER
        ws.row_dimensions[row].height = 32
        if not rows:
            ws.merge_cells(start_row=row + 1, start_column=start_col,
                           end_row=row + 1, end_column=start_col + ncols - 1)
            cell = ws.cell(row=row + 1, column=start_col, value="Sin datos")
            cell.font = Font(name="Calibri", size=9, italic=True, color=DARK_GRAY)
            return row + 2
        for i, values in enumerate(rows):
            r = row + 1 + i
            if row_height:
                ws.row_dimensions[r].height = row_height
            for j in range(ncols):
                value = _clean(values[j]) if j < len(values) else None
                cell = ws.cell(row=r, column=start_col + j, value=_fmt_number(value, formats[j]))
                cell.font = Font(name="Calibri", size=9, color="222222")
                cell.border = BORDER
                cell.alignment = Alignment(
                    horizontal={"right": "right", "center": "center"}.get(aligns[j], "left"),
                    vertical="top", wrap_text=True)
                if formats[j]:
                    cell.number_format = formats[j]
                if (i % 2 == 1):
                    cell.fill = PatternFill("solid", fgColor="F7F9FC")
            if state_col is not None:
                cell = ws.cell(row=r, column=start_col + state_col)
                colors = STATE_FILL.get(str(cell.value))
                if colors:
                    cell.fill = PatternFill("solid", fgColor=colors[0])
                    cell.font = Font(name="Calibri", size=9, bold=True, color=colors[1])
                    cell.alignment = CENTER
        return row + len(rows) + 1

    def _finish(self, ws: Worksheet, header_row: int, last_row: int, last_col: int,
                fit_width: int = 1) -> None:
        if last_row > header_row:
            ws.auto_filter.ref = f"B{header_row}:{get_column_letter(last_col)}{last_row}"
        ws.freeze_panes = f"D{header_row + 1}"
        ws.sheet_view.showGridLines = False
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = fit_width
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.print_title_rows = f"{header_row}:{header_row}"

    # ------------------------------------------------------------------ público
    def generate(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._resumen(self.wb.active)
        self._productos(self.wb.create_sheet("PRODUCTOS"))
        self._faltantes(self.wb.create_sheet("QUIEBRES Y FALTANTES"))
        self._sobrecompra(self.wb.create_sheet("SOBRECOMPRA"))
        self._proveedores(self.wb.create_sheet("PROVEEDORES"))
        self._categorias(self.wb.create_sheet("CATEGORÍAS"))
        self._compradores(self.wb.create_sheet("COMPRADORES"))
        self._precios(self.wb.create_sheet("PRECIOS Y MARGEN"))
        self._mensual(self.wb.create_sheet("MENSUAL"))
        self._metodologia(self.wb.create_sheet("METODOLOGÍA"))
        self.wb.properties.title = f"Auditoría de compras {self.audit.year}"
        self.wb.properties.creator = "Sistema de Compras - Droguería Emmanuelle"
        self.wb.save(self.path)
        self.logger.info("Auditoría Excel generada: %s", self.path)
        return self.path

    # ------------------------------------------------------------------ hojas
    def _resumen(self, ws: Worksheet) -> None:
        a = self.audit
        m = a.metrics
        ws.title = "RESUMEN AUDITORÍA"
        ws.sheet_view.showGridLines = False
        self._set_widths(ws, {1: 2, 2: 22, 3: 38, 4: 18, 5: 16, 6: 16, 7: 16, 8: 16, 9: 16, 10: 16})
        subtitle = (f"Droguería Emmanuelle · Auditoría de compras {a.year} · "
                    f"Corte {a.as_of:%d/%m/%Y} · Fuente: Profit 2K12")
        row = self._title(ws, f"AUDITORÍA DE COMPRAS {a.year} — ¿COMPRAMOS BIEN?", subtitle, 10)
        row += 1
        cards = [
            ("Compra de mercancía", m["compra_usd"], MONEY0),
            ("Venta de mercancía", m["venta_usd"], MONEY0),
            ("Margen bruto", m["margen_pct"] / 100, "0.0%"),
            ("Cobertura de inventario", m["cobertura_dias"], '0" días"'),
            ("Quiebres reales", m["quiebres"], INT),
            ("SKU sobrecomprados", m["sobrecompra"], INT),
            ("Capital en exceso", m["capital_exceso_usd"], MONEY0),
            ("Venta inmediata en riesgo", m["venta_en_riesgo_usd"], MONEY0),
        ]
        row = self._kpis(ws, row, cards)
        row += 1

        row = self._section(ws, row, "VEREDICTO", 10)
        for i, text in enumerate(a.findings, start=1):
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=10)
            cell = ws.cell(row=row, column=2, value=f"{i}. {text}")
            cell.font = Font(name="Calibri", size=9)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[row].height = 34
            row += 1
        row += 1

        row = self._section(ws, row, "TOP 10 SOBRECOMPRA POR CAPITAL EXCEDENTE", 10)
        over = a.products[a.products["Estado"] == "SOBRECOMPRA"].sort_values("Exceso_USD", ascending=False).head(10)
        headers = ["EAN", "Descripción", "Proveedor", "Stock", "Tránsito", "Cobertura (d)", "Exceso USD"]
        rows = [[r["EAN"], r["Descripcion"], r["Proveedor"], r["Stock_Actual"], r["Stock_Transito"],
                 r["Cobertura_Disponible_Dias"], r["Exceso_USD"]] for _, r in over.iterrows()]
        row = self._table(ws, row, headers, rows,
                          formats=[None, None, None, INT, INT, INT, MONEY0],
                          aligns=["left", "left", "left", "right", "right", "right", "right"])
        row += 1

        row = self._section(ws, row, "TOP 10 FALTANTES POR VENTA", 10)
        miss = a.products[a.products["Estado"].isin(["QUIEBRE", "CRÍTICO", "FALTANTE"])] \
            .sort_values("Venta_USD", ascending=False).head(10)
        headers = ["EAN", "Descripción", "Proveedor", "Stock", "Tránsito", "Venta 2026", "Estado"]
        rows = [[r["EAN"], r["Descripcion"], r["Proveedor"], r["Stock_Actual"], r["Stock_Transito"],
                 r["Venta_USD"], r["Estado"]] for _, r in miss.iterrows()]
        row = self._table(ws, row, headers, rows,
                          formats=[None, None, None, INT, INT, MONEY0, None],
                          aligns=["left", "left", "left", "right", "right", "right", "center"],
                          state_col=6)
        row += 1

        row = self._section(ws, row, "COMPRA VS VENTA POR MES", 10)
        headers = ["Mes", "Compra USD", "Venta USD", "Compra unidades", "Venta unidades", "Ratio compra/venta"]
        rows = [[r["Mes"], r["Compra_USD"], r["Venta_USD"], r["Compra_Unidades"],
                 r["Venta_Unidades"], r["Ratio_Compra_Venta"]] for _, r in a.monthly.iterrows()]
        self._table(ws, row, headers, rows,
                    formats=[None, MONEY0, MONEY0, INT, INT, '0.00'],
                    aligns=["center", "right", "right", "right", "right", "right"])
        ws.page_setup.orientation = "portrait"
        ws.page_setup.fitToWidth = 1
        ws.sheet_properties.pageSetUpPr.fitToPage = True

    def _set_widths(self, ws: Worksheet, widths: dict[int, float]) -> None:
        for col, width in widths.items():
            ws.column_dimensions[get_column_letter(col)].width = width

    def _productos(self, ws: Worksheet) -> None:
        df = self.audit.products
        row = self._title(ws, "AUDITORÍA POR PRODUCTO",
                          f"{len(df):,} SKU con compra, venta o inventario en {self.audit.year}", 20)
        row += 1
        self._set_widths(ws, {2: 15, 3: 44, 4: 20, 5: 18, 6: 18, 7: 8, 8: 12, 9: 12, 10: 12,
                              11: 12, 12: 12, 13: 12, 14: 12, 15: 12, 16: 12, 17: 12, 18: 12,
                              19: 14, 20: 14, 21: 34})
        headers = ["EAN", "Descripción", "Proveedor", "Línea", "Categoría", "Compra USD", "Compra u",
                   "Venta USD", "Venta u", "Margen %", "Stock", "Tránsito", "Cobertura d",
                   "Ratio c/v", "Exceso USD", "Faltante USD", "Estado", "Acción"]
        rows = [[r["EAN"], r["Descripcion"], r["Proveedor"], r["Linea"], r["Categoria"],
                 r["Compra_USD"], r["Compra_Unidades"], r["Venta_USD"], r["Venta_Unidades"],
                 r["Margen_Pct"] / 100 if pd.notna(r["Margen_Pct"]) else None,
                 r["Stock_Actual"], r["Stock_Transito"],
                 r["Cobertura_Disponible_Dias"] if r["Cobertura_Disponible_Dias"] < 999 else None,
                 r["Ratio_Compra_Venta"], r["Exceso_USD"], r["Faltante_USD"],
                 r["Estado"], r["Accion"]] for _, r in df.iterrows()]
        header_row = row
        last = self._table(ws, row, headers, rows,
                           formats=[None, None, None, None, None, MONEY0, INT, MONEY0, INT,
                                    '0.0%', INT, INT, INT, '0.00', MONEY0, MONEY0, None, None],
                           aligns=["left", "left", "left", "left", "left", "right", "right",
                                   "right", "right", "right", "right", "right", "right", "right",
                                   "right", "right", "center", "left"],
                           state_col=16, row_height=26)
        self._finish(ws, header_row, last - 1, 21, fit_width=3)

    def _faltantes(self, ws: Worksheet) -> None:
        df = self.audit.products[self.audit.products["Estado"].isin(["QUIEBRE", "CRÍTICO", "FALTANTE"])] \
            .sort_values(["Estado", "Venta_USD"], ascending=[True, False])
        row = self._title(ws, "QUIEBRES Y FALTANTES — ¿QUÉ NOS FALTA?",
                          f"{len(df):,} SKU requieren reposición o verificación", 14)
        row += 1
        self._set_widths(ws, {2: 15, 3: 46, 4: 20, 5: 10, 6: 12, 7: 12, 8: 12, 9: 12,
                              10: 12, 11: 14, 12: 14, 13: 14, 14: 30})
        headers = ["EAN", "Descripción", "Proveedor", "Stock", "Tránsito", "Cobertura d",
                   "Venta 2026", "Compra 2026", "Faltante u", "Faltante USD", "Estado", "Acción"]
        rows = [[r["EAN"], r["Descripcion"], r["Proveedor"], r["Stock_Actual"], r["Stock_Transito"],
                 r["Cobertura_Disponible_Dias"] if r["Cobertura_Disponible_Dias"] < 999 else None,
                 r["Venta_USD"], r["Compra_USD"], r["Faltante_Unidades"], r["Faltante_USD"],
                 r["Estado"], r["Accion"]] for _, r in df.iterrows()]
        header_row = row
        last = self._table(ws, row, headers, rows,
                           formats=[None, None, None, INT, INT, INT, MONEY0, MONEY0, INT, MONEY0, None, None],
                           aligns=["left", "left", "left", "right", "right", "right", "right",
                                   "right", "right", "right", "center", "left"],
                           state_col=10, row_height=28)
        self._finish(ws, header_row, last - 1, 14, fit_width=2)

    def _sobrecompra(self, ws: Worksheet) -> None:
        df = self.audit.products[self.audit.products["Estado"].isin(["SOBRECOMPRA", "SIN VENTA"])] \
            .sort_values("Exceso_USD", ascending=False)
        row = self._title(ws, "SOBRECOMPRA Y CAPITAL INMOVILIZADO",
                          f"{len(df):,} SKU con más de 180 días de cobertura o sin venta", 14)
        row += 1
        self._set_widths(ws, {2: 15, 3: 46, 4: 20, 5: 10, 6: 12, 7: 12, 8: 12, 9: 12,
                              10: 14, 11: 14, 12: 14, 13: 14, 14: 30})
        headers = ["EAN", "Descripción", "Proveedor", "Stock", "Tránsito", "Cobertura d",
                   "Compra 2026", "Venta 2026", "Stock USD", "Tránsito USD", "Exceso USD", "Estado", "Acción"]
        rows = [[r["EAN"], r["Descripcion"], r["Proveedor"], r["Stock_Actual"], r["Stock_Transito"],
                 r["Cobertura_Disponible_Dias"] if r["Cobertura_Disponible_Dias"] < 999 else None,
                 r["Compra_USD"], r["Venta_USD"], r["Stock_Valor"], r["Transito_Valor"],
                 r["Exceso_USD"], r["Estado"], r["Accion"]] for _, r in df.iterrows()]
        header_row = row
        last = self._table(ws, row, headers, rows,
                           formats=[None, None, None, INT, INT, INT, MONEY0, MONEY0, MONEY0,
                                    MONEY0, MONEY0, None, None],
                           aligns=["left", "left", "left", "right", "right", "right", "right",
                                   "right", "right", "right", "right", "center", "left"],
                           state_col=11, row_height=28)
        self._finish(ws, header_row, last - 1, 14, fit_width=2)

    def _proveedores(self, ws: Worksheet) -> None:
        df = self.audit.suppliers
        row = self._title(ws, "AUDITORÍA POR PROVEEDOR",
                          f"{len(df)} proveedores con compra en {self.audit.year}", 13)
        row += 1
        self._set_widths(ws, {2: 14, 3: 40, 4: 14, 5: 12, 6: 10, 7: 10, 8: 14, 9: 10, 10: 12, 11: 14, 12: 14})
        headers = ["Código", "Proveedor", "Compra USD", "Unidades", "Docs", "Productos",
                   "Ticket prom. USD", "Share %", "Var. costo %", "Devoluciones USD", "Riesgo"]
        rows = []
        for _, r in df.iterrows():
            riesgo = "ALTO" if r["Share_Pct"] > 30 else ("MEDIO" if r["Share_Pct"] > 15 else "BAJO")
            rows.append([r["CodProveedor"], r["Proveedor"], r["Compra_USD"], r["Unidades"], r["Docs"],
                         r["Productos"], r["Ticket_Prom_USD"], r["Share_Pct"] / 100,
                         r["Costo_Var_Pct"] / 100 if pd.notna(r["Costo_Var_Pct"]) else None,
                         r["Devoluciones_USD"], riesgo])
        header_row = row
        last = self._table(ws, row, headers, rows,
                           formats=[None, None, MONEY0, INT, INT, INT, MONEY0, '0.0%', '+0.0%;-0.0%', MONEY0, None],
                           aligns=["left", "left", "right", "right", "right", "right", "right",
                                   "right", "right", "right", "center"], row_height=24)
        self._finish(ws, header_row, last - 1, 12)

    def _categorias(self, ws: Worksheet) -> None:
        df = self.audit.categories
        row = self._title(ws, "AUDITORÍA POR CATEGORÍA",
                          "Compra, venta, margen y capital por categoría", 11)
        row += 1
        self._set_widths(ws, {2: 30, 3: 14, 4: 14, 5: 12, 6: 12, 7: 12, 8: 14, 9: 14, 10: 12, 11: 14})
        headers = ["Categoría", "Compra USD", "Venta USD", "Compra u", "Venta u", "Margen USD",
                   "Margen %", "Stock USD", "Tránsito USD", "Ratio c/v", "Productos"]
        rows = [[r["Categoria"], r["Compra_USD"], r["Venta_USD"], r["Compra_Unidades"], r["Venta_Unidades"],
                 r["Margen_USD"], r["Margen_Pct"] / 100 if pd.notna(r["Margen_Pct"]) else None,
                 r["Stock_Valor"], r["Transito_Valor"],
                 r["Ratio_Compra_Venta"], r["Productos"]] for _, r in df.iterrows()]
        header_row = row
        last = self._table(ws, row, headers, rows,
                           formats=[None, MONEY0, MONEY0, INT, INT, MONEY0, '0.0%', MONEY0, MONEY0, '0.00', INT],
                           aligns=["left", "right", "right", "right", "right", "right", "right",
                                   "right", "right", "right", "right"], row_height=24)
        self._finish(ws, header_row, last - 1, 11)

    def _compradores(self, ws: Worksheet) -> None:
        df = self.audit.buyers
        row = self._title(ws, "AUDITORÍA POR COMPRADOR",
                          "Concentración de la gestión de compras por usuario de Profit", 8)
        row += 1
        self._set_widths(ws, {2: 20, 3: 14, 4: 12, 5: 10, 6: 12, 7: 12, 8: 12})
        headers = ["Comprador", "Compra USD", "Unidades", "Docs", "Proveedores", "Productos", "Share %"]
        rows = [[r["Comprador"], r["Compra_USD"], r["Unidades"], r["Docs"], r["Proveedores"],
                 r["Productos"], r["Share_Pct"] / 100] for _, r in df.iterrows()]
        header_row = row
        last = self._table(ws, row, headers, rows,
                           formats=[None, MONEY0, INT, INT, INT, INT, '0.0%'],
                           aligns=["left", "right", "right", "right", "right", "right", "right"])
        self._finish(ws, header_row, last - 1, 8)

    def _precios(self, ws: Worksheet) -> None:
        v = self.audit.price_variance
        row = self._title(ws, "PRECIOS Y MARGEN",
                          "Variación de costo unitario en USD y productos con margen negativo", 10)
        row += 1
        self._set_widths(ws, {2: 15, 3: 44, 4: 20, 5: 14, 6: 14, 7: 12, 8: 14, 9: 14})
        row = self._section(ws, row, "VARIACIÓN DE COSTO 2025 → 2026 (misma referencia)", 10)
        headers = ["EAN", "Descripción", "Proveedor", "Costo 2025", "Costo 2026", "Var %", "Compra 2026 USD"]
        rows = [[r["EAN"], r["Descripcion"], r["Proveedor"], r["Costo_2025"], r["Costo_2026"],
                 r["Var_Pct"] / 100, r["Compra_2026_USD"]]
                for _, r in v.head(200).iterrows()]
        row = self._table(ws, row, headers, rows,
                          formats=[None, None, None, MONEY, MONEY, '+0.0%;-0.0%', MONEY0],
                          aligns=["left", "left", "left", "right", "right", "right", "right"], row_height=24)
        row += 1

        row = self._section(ws, row, "PRODUCTOS CON MARGEN NEGATIVO (venta < costo)", 10)
        neg = self.audit.products[(self.audit.products["Margen_USD"] < 0) &
                                  (self.audit.products["Venta_USD"] > 0)] \
            .sort_values("Margen_USD").head(100)
        headers = ["EAN", "Descripción", "Proveedor", "Venta USD", "Costo unit.", "Precio unit.", "Margen USD", "Margen %"]
        rows = [[r["EAN"], r["Descripcion"], r["Proveedor"], r["Venta_USD"], r["Costo_Unit_USD"],
                 r["Precio_Unit_USD"], r["Margen_USD"], r["Margen_Pct"] / 100]
                for _, r in neg.iterrows()]
        self._table(ws, row, headers, rows,
                    formats=[None, None, None, MONEY0, MONEY, MONEY, MONEY0, '0.0%'],
                    aligns=["left", "left", "left", "right", "right", "right", "right", "right"], row_height=24)

    def _mensual(self, ws: Worksheet) -> None:
        df = self.audit.monthly
        row = self._title(ws, "EVOLUCIÓN MENSUAL",
                          "Compra vs venta y estacionalidad del gasto", 10)
        row += 1
        self._set_widths(ws, {2: 12, 3: 14, 4: 14, 5: 14, 6: 14, 7: 12, 8: 12, 9: 10})
        headers = ["Mes", "Compra USD", "Venta USD", "Compra u", "Venta u", "Docs compra", "Docs venta", "Ratio"]
        rows = [[r["Mes"], r["Compra_USD"], r["Venta_USD"], r["Compra_Unidades"], r["Venta_Unidades"],
                 r["Docs_Compra"], r["Docs_Venta"], r["Ratio_Compra_Venta"]] for _, r in df.iterrows()]
        header_row = row
        last = self._table(ws, row, headers, rows,
                           formats=[None, MONEY0, MONEY0, INT, INT, INT, INT, '0.00'],
                           aligns=["center", "right", "right", "right", "right", "right", "right", "right"],
                           row_height=22)
        self._finish(ws, header_row, last - 1, 9)

    def _metodologia(self, ws: Worksheet) -> None:
        a = self.audit
        row = self._title(ws, "METODOLOGÍA Y CALIDAD DE LA AUDITORÍA",
                          "Definiciones, supuestos y controles aplicados", 6)
        row += 1
        self._set_widths(ws, {2: 34, 3: 50, 4: 18})
        row = self._section(ws, row, "DEFINICIONES", 6)
        headers = ["Indicador", "Definición", "Valor"]
        rows = [
            ["Quiebre", "Stock actual y tránsito ≤ 0, con ventas en el año", a.metrics["quiebres"]],
            ["Crítico", "Cobertura disponible < 15 días", a.metrics["criticos"]],
            ["Faltante", "Cobertura disponible 15–30 días", a.metrics["faltantes"]],
            ["Sobrecompra", "Cobertura disponible > 180 días", a.metrics["sobrecompra"]],
            ["Sin venta", "Sin ventas en 2026 y con stock o tránsito", a.metrics["sin_venta"]],
            ["Capital en exceso", "Unidades sobre 90 días de demanda × costo", f"${a.metrics['capital_exceso_usd']:,.0f}"],
            ["Venta inmediata en riesgo", "Faltante de lead time × precio de venta", f"${a.metrics['venta_en_riesgo_usd']:,.0f}"],
            ["Cobertura", "(Stock + tránsito − comprometido) / venta diaria 90 días", f"{a.metrics['cobertura_dias']:.0f} días"],
            ["Margen bruto", "(Venta − unidades vendidas × costo promedio) / venta", f"{a.metrics['margen_pct']:.1f}%"],
            ["Almacenes ventas/stock", "Selección usada para ventas, stock y cobertura",
             ", ".join(a.warehouse_info.get("selected", [])) or "No informado"],
            ["Almacenes compras", "Incluye recepción para conservar el costo real",
             ", ".join(a.warehouse_info.get("purchase_codes", [])) or "No informado"],
        ]
        row = self._table(ws, row, headers, rows, formats=[None, None, None],
                          aligns=["left", "left", "right"], row_height=26)
        row += 1

        row = self._section(ws, row, "SUPUESTOS", 6)
        assumptions = [
            "Solo se usan facturas de compra y venta no anuladas; se excluyen artículos tipo servicio (S).",
            "Los importes en USD se obtienen dividiendo el importe en Bs entre la tasa del documento; "
            "los documentos en BS usan la tasa mensual de referencia.",
            "El costo unitario es el promedio ponderado de las compras 2026; si no hay compra, se usa la última compra histórica.",
            "La demanda diaria se calcula con las ventas de los últimos 90 días.",
            "El tránsito (LLE) se toma de v_stockActual; puede incluir órdenes de compra no recibidas.",
            "La auditoría es de solo lectura: no modifica datos en Profit.",
        ]
        for text in assumptions:
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=6)
            cell = ws.cell(row=row, column=2, value=f"• {text}")
            cell.font = Font(name="Calibri", size=9)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[row].height = 28
            row += 1
        row += 1

        row = self._section(ws, row, "CALIDAD DE DATOS", 6)
        headers = ["Chequeo", "Resultado", "Detalle"]
        rows = [[r["Chequeo"], r["Resultado"], r["Detalle"]] for _, r in a.quality.iterrows()]
        self._table(ws, row, headers, rows, formats=[None, None, None],
                    aligns=["left", "right", "left"], row_height=22)
        ws.sheet_view.showGridLines = False
        ws.page_setup.orientation = "portrait"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
