"""Pruebas de filtro de almacenes, KPIs de ventas, drill-down y cache."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analytics.ventas_analytics import (  # noqa: E402
    VentasAnalytics, _cost_map, _group, _kpis,
)
from data_processing.profit_loader import ProfitLoader  # noqa: E402
from data_processing.warehouse_filter import (  # noqa: E402
    WarehouseFilter, sql_placeholders,
)
from utils.config import Config  # noqa: E402


class TestWarehouseFilter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Config("config/parameters.yaml", base_dir=ROOT)
        self.config.set("warehouse.selection_file", str(Path(self.tmp.name) / "selection.json"))
        self.config.set("warehouse.selected", ["CCS", "ZUL"])
        self.config.set("warehouse.purchase_extra", ["REC"])
        self.config.set("warehouse.purchase_mode", "selected_plus_extra")

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_selection_and_purchase_codes(self):
        wf = WarehouseFilter(self.config, None)
        self.assertEqual(wf.selection.selected, ("CCS", "ZUL"))
        self.assertEqual(wf.selection.purchase_codes, ("CCS", "ZUL", "REC"))
        self.assertEqual(wf.selection.sales_codes, ("CCS", "ZUL"))

    def test_save_and_reload_selection(self):
        wf = WarehouseFilter(self.config, None)
        wf.save(["CCS"], purchase_mode="selected_only")
        payload = json.loads(Path(self.config.get("warehouse.selection_file")).read_text(encoding="utf-8"))
        self.assertEqual(payload["selected"], ["CCS"])
        self.assertEqual(payload["purchase_mode"], "selected_only")
        reloaded = WarehouseFilter(self.config, None)
        self.assertEqual(reloaded.selection.selected, ("CCS",))
        self.assertEqual(reloaded.selection.purchase_codes, ("CCS",))

    def test_empty_selection_is_rejected(self):
        wf = WarehouseFilter(self.config, None)
        with self.assertRaises(ValueError):
            wf.save([])

    def test_with_selection_clone(self):
        wf = WarehouseFilter(self.config, None)
        clone = wf.with_selection(["ZUL"])
        self.assertEqual(clone.selection.selected, ("ZUL",))
        self.assertEqual(wf.selection.selected, ("CCS", "ZUL"))
        self.assertEqual(sql_placeholders(["CCS", "ZUL"]), "?,?")

    def test_loader_injects_warehouse_clause(self):
        class FakeDB:
            def __init__(self):
                self.sql = ""
                self.params = []

            def query(self, sql, params=None):
                self.sql = sql
                self.params = list(params or [])
                return pd.DataFrame()

        db = FakeDB()
        wf = WarehouseFilter(self.config, None)
        loader = ProfitLoader(db, warehouse_filter=wf)
        loader.load_sales("2026-01-01", "2026-01-31")
        self.assertIn("r.co_alma IN (?,?)", db.sql)
        self.assertEqual(db.params[-2:], ["CCS", "ZUL"])
        loader.load_stock()
        self.assertIn("saStockAlmacen", db.sql)
        self.assertIn("co_alma IN (?,?)", db.sql)


class TestVentasMetrics(unittest.TestCase):
    def setUp(self):
        self.df = pd.DataFrame({
            "Fecha": pd.to_datetime(["2026-09-01", "2026-09-02", "2026-09-03"]),
            "DocNum": ["F1", "F2", "F3"],
            "CodCliente": ["C1", "C1", "C2"],
            "Cliente": ["UNO", "UNO", "DOS"],
            "EAN": ["A", "A", "B"],
            "Descripcion": ["A", "A", "B"],
            "Linea": ["LAB1", "LAB1", "LAB2"],
            "Laboratorio": ["L1", "L1", "L2"],
            "Categoria": ["CAT", "CAT", "CAT"],
            "Sublinea": ["", "", ""],
            "Almacen": ["CCS", "CCS", "CCS"],
            "Cantidad": [2.0, 3.0, 1.0],
            "Venta_USD": [2.0, 3.0, 1.0],
            "Venta_Bs": [1600.0, 2400.0, 800.0],
            "Costo_Unitario_USD": [1.0, 1.0, 0.5],
            "Costo_Total_USD": [2.0, 3.0, 0.5],
            "Margen_USD": [0.0, 0.0, 0.5],
            "Margen_Pct": [0.0, 0.0, 50.0],
        })

    def test_kpis_90d(self):
        k = _kpis(self.df)
        self.assertAlmostEqual(k["venta_usd"], 6.0)
        self.assertAlmostEqual(k["margen_usd"], 0.5)
        self.assertAlmostEqual(k["ticket_promedio"], 2.0)
        self.assertEqual(k["clientes"], 2)
        self.assertEqual(k["sku"], 2)
        self.assertEqual(k["facturas"], 3)

    def test_group_by_lab(self):
        g = _group(self.df, ["Linea"]).set_index("Linea")
        self.assertAlmostEqual(g.loc["LAB1", "Venta_USD"], 5.0)
        self.assertAlmostEqual(g.loc["LAB2", "Venta_USD"], 1.0)

    def test_cost_map_weighted(self):
        purchases = pd.DataFrame({
            "EAN": ["A", "A", "B"],
            "Cantidad": [1.0, 3.0, 2.0],
            "Importe_USD": [2.0, 2.0, 1.0],
        })
        cost = _cost_map(purchases)
        self.assertAlmostEqual(cost["A"], 1.0)
        self.assertAlmostEqual(cost["B"], 0.5)


class TestVentasService(unittest.TestCase):
    def setUp(self):
        VentasAnalytics.clear_cache()
        self.config = Config("config/parameters.yaml", base_dir=ROOT)
        self.config.set("warehouse.selected", ["CCS"])
        self.config.set("warehouse.purchase_mode", "selected_plus_extra")
        self.wf = WarehouseFilter(self.config, None)
        self.analytics = VentasAnalytics(self.config, connection=object(), warehouse_filter=self.wf)
        self.analytics.dim = pd.DataFrame({
            "Codigo": ["1001", "1002"], "Laboratorio": ["LAB1", "LAB2"],
            "Descripcion": ["A", "B"], "Fuente": ["test", "test"],
        })
        self.analytics.dim_mtime = 0.0
        self.calls = 0
        self.sales = pd.DataFrame({
            "Fecha": ["2026-09-01", "2026-09-02"],
            "DocNum": ["F1", "F2"],
            "CodCliente": ["C1", "C2"],
            "Cliente": ["UNO", "DOS"],
            "EAN": ["1001", "1002"],
            "Descripcion": ["A", "B"],
            "Linea": ["LAB1", "LAB2"],
            "Categoria": ["CAT", "CAT"],
            "Sublinea": ["", ""],
            "Almacen": ["CCS", "CCS"],
            "Cantidad": [2.0, 1.0],
            "Importe": [1600.0, 800.0],
            "Moneda": ["USD", "USD"],
            "Tasa": [800.0, 800.0],
            "DescuentoLinea": [0.0, 0.0],
        })
        self.purchases = pd.DataFrame({
            "EAN": ["1001", "1002"],
            "Cantidad": [2.0, 1.0],
            "Importe": [800.0, 400.0],
            "Moneda": ["USD", "USD"],
            "Tasa": [800.0, 800.0],
            "Fecha": ["2026-08-01", "2026-08-01"],
            "DescuentoLinea": [0.0, 0.0],
        })

        def fake_parallel(start, end, wf):
            self.calls += 1
            return self.sales.copy(), self.purchases.copy(), pd.DataFrame(columns=["Mes", "Tasa"])

        self.analytics._parallel_queries = fake_parallel

    def tearDown(self):
        VentasAnalytics.clear_cache()

    def test_resumen_and_cache(self):
        r = self.analytics.resumen("2026-09-01", "2026-09-30")
        self.assertAlmostEqual(r["kpis"]["venta_usd"], 3.0)
        self.assertEqual(r["kpis"]["clientes"], 2)
        self.assertEqual(self.calls, 2)  # período actual + anterior
        self.analytics.resumen("2026-09-01", "2026-09-30")
        self.assertEqual(self.calls, 2)  # cache

    def test_client_drilldown(self):
        c = self.analytics.cliente("C1", "2026-09-01", "2026-09-30")
        self.assertEqual(c["cliente"]["codigo"], "C1")
        self.assertEqual(c["cliente"]["cliente"], "UNO")
        self.assertEqual(len(c["items"]), 1)
        self.assertEqual(c["items"][0]["Laboratorio"], "LAB1")
        self.assertAlmostEqual(c["cliente"]["venta_usd"], 2.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
