"""Pruebas de exportación Excel de Ventas: laboratorio real, no línea terapéutica."""
from __future__ import annotations

import sys
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "ui"))

from reporting.ventas_export import (  # noqa: E402
    DETAIL_SHEET, build_detalle_cliente_laboratorio, build_ventas_excel,
)
from ventas_bp import _cliente_pdf_context  # noqa: E402


class FakeAnalytics:
    def __init__(self, frame: pd.DataFrame):
        self.frame_data = frame
        self.base_filter = SimpleNamespace(
            selection=SimpleNamespace(sales_codes=("CCS", "ZUL"))
        )

    def export_frame(self, *args, **kwargs):
        return self.frame_data.copy()

    def resumen(self, *args, **kwargs):
        return {
            "kpis": {"venta_usd": 100.0, "venta_bs": 800.0, "margen_usd": 30.0,
                     "margen_pct": 30.0, "ticket_promedio": 100.0, "clientes": 1,
                     "sku": 1, "facturas": 1},
            "top_laboratorio": {"nombre": "COFRADIA", "venta_usd": 100.0},
            "cobertura_laboratorio": {"venta_pct": 100.0},
            "serie_diaria": [{"fecha": "2026-09-01", "Venta_USD": 100.0}],
        }

    def laboratorios(self, *args, **kwargs):
        return {"items": [{"Laboratorio": "COFRADIA", "Venta_USD": 80.0,
                           "Margen_Pct": 30.0, "Unidades": 2, "Clientes": 1},
                          {"Laboratorio": "SIN_LABORATORIO", "Venta_USD": 20.0,
                           "Margen_Pct": 10.0, "Unidades": 1, "Clientes": 1}]}

    def lineas_terapeuticas(self, *args, **kwargs):
        return {"items": [{"Linea_Terapeutica": "ANSIOLITICO", "Venta_USD": 100.0,
                           "Margen_Pct": 30.0, "Unidades": 3}]}

    def productos(self, *args, **kwargs):
        return {"items": [{"EAN": "1001", "Descripcion": "PRODUCTO",
                           "Laboratorio": "COFRADIA", "Linea_Terapeutica": "ANSIOLITICO",
                           "Venta_USD": 100.0, "Unidades": 3}]}

    def clientes(self, *args, **kwargs):
        return {"items": [{"CodCliente": "C1", "Cliente": "CLIENTE UNO",
                           "Venta_USD": 100.0, "Margen_Pct": 30.0,
                           "Facturas": 1, "SKU": 1}]}


def sample_frame() -> pd.DataFrame:
    return pd.DataFrame([
        {"Fecha": pd.Timestamp("2026-09-01"), "DocNum": "F1", "CodCliente": "C1",
         "Cliente": "CLIENTE UNO", "EAN": "1001", "Descripcion": "PRODUCTO",
         "Laboratorio": "COFRADIA", "Linea_Terapeutica": "ANSIOLITICO",
         "Categoria": "MEDICAMENTOS", "Sublinea": "TEST", "Almacen": "CCS",
         "Cantidad": 2.0, "Venta_USD": 80.0, "Venta_Bs": 640.0,
         "Costo_Unitario_USD": 1.0, "Costo_Total_USD": 2.0,
         "Margen_USD": 78.0, "Margen_Pct": 97.5},
        {"Fecha": pd.Timestamp("2026-09-02"), "DocNum": "F2", "CodCliente": "C1",
         "Cliente": "CLIENTE UNO", "EAN": "9999", "Descripcion": "SIN MAESTRO",
         "Laboratorio": "SIN_LABORATORIO", "Linea_Terapeutica": "ANSIOLITICO",
         "Categoria": "MEDICAMENTOS", "Sublinea": "TEST", "Almacen": "CCS",
         "Cantidad": 1.0, "Venta_USD": 20.0, "Venta_Bs": 160.0,
         "Costo_Unitario_USD": 1.0, "Costo_Total_USD": 1.0,
         "Margen_USD": 19.0, "Margen_Pct": 95.0},
    ])


class TestVentasExport(unittest.TestCase):
    def test_detail_uses_laboratorio_not_linea(self):
        detail = build_detalle_cliente_laboratorio(sample_frame())
        self.assertIn("COFRADIA", set(detail["Laboratorio"]))
        self.assertIn("SIN_LABORATORIO", set(detail["Laboratorio"]))
        self.assertNotIn("ANSIOLITICO", set(detail["Laboratorio"]))

    def test_excel_contains_real_lab_and_keeps_unmatched(self):
        data = build_ventas_excel(FakeAnalytics(sample_frame()), "2026-09-01", "2026-09-30",
                                  ["CCS", "ZUL"], {})
        book = pd.ExcelFile(BytesIO(data))
        self.assertIn(DETAIL_SHEET, book.sheet_names)
        detail = pd.read_excel(BytesIO(data), sheet_name=DETAIL_SHEET, header=1)
        labs = set(detail["Laboratorio"].astype(str))
        self.assertIn("COFRADIA", labs)
        self.assertIn("SIN_LABORATORIO", labs)
        self.assertNotIn("ANSIOLITICO", labs)
        lineas = pd.read_excel(BytesIO(data), sheet_name="LINEAS_TERAPEUTICAS", header=1)
        self.assertIn("ANSIOLITICO", set(lineas["Linea_Terapeutica"].astype(str)))

    def test_empty_frame_does_not_break_excel(self):
        empty = sample_frame().iloc[0:0].copy()
        data = build_ventas_excel(FakeAnalytics(empty), "2026-09-01", "2026-09-30", ["CCS"], {})
        self.assertGreater(len(data), 1000)

    def test_client_pdf_context_uses_real_lab(self):
        data = {
            "cliente": {
                "codigo": "C1", "cliente": "CLIENTE UNO", "ultima_compra": "2026-09-01",
                "facturas": 1, "venta_usd": 100.0, "venta_bs": 800.0, "unidades": 3,
                "margen_usd": 30.0, "margen_pct": 30.0, "ticket_promedio": 100.0,
            },
            "items": [{"Laboratorio": "COFRADIA", "Venta_USD": 80, "Margen_Pct": 30,
                       "Unidades": 2, "Facturas": 1, "Ultima_Venta": "2026-09-01"},
                      {"Laboratorio": "SIN_LABORATORIO", "Venta_USD": 20, "Margen_Pct": 10,
                       "Unidades": 1, "Facturas": 1, "Ultima_Venta": "2026-09-01"}],
            "productos": [{"Descripcion": "PRODUCTO", "Laboratorio": "COFRADIA",
                           "Linea_Terapeutica": "ANSIOLITICO", "Venta_USD": 80, "Unidades": 2}],
        }
        ctx = _cliente_pdf_context(data, "2026-09-01", "2026-09-30", ["CCS"], {},
                                   [{"label": "Evolución", "data": "data:image/png;base64,AAAA"}])
        labs = {r["Laboratorio"] for r in ctx["labs"]}
        self.assertIn("COFRADIA", labs)
        self.assertIn("SIN_LABORATORIO", labs)
        self.assertNotIn("ANSIOLITICO", labs)
        self.assertEqual(ctx["productos"][0]["Linea_Terapeutica"], "ANSIOLITICO")
        self.assertTrue(ctx["chart"].startswith("data:image/png"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
