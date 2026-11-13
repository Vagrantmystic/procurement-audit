"""Pruebas del sistema con datos sintéticos (sin dependencias externas)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analytics.abc_analyzer import ABCAnalyzer  # noqa: E402
from analytics.segmentation import Segmenter  # noqa: E402
from analytics.xyz_classifier import XYZClassifier  # noqa: E402
from data_processing.catalog_enricher import CatalogEnricher  # noqa: E402
from data_processing.loader import DataLoader  # noqa: E402
from models.forecast_engine import ForecastEngine, ProductForecast  # noqa: E402
from models.model_orchestrator import ModelOrchestrator  # noqa: E402
from recommendation.order_calculator import OrderCalculator  # noqa: E402
from utils.config import Config, Provider  # noqa: E402
from utils.helpers import round_to_multiple, to_ean, to_number  # noqa: E402


def build_fixture(base: Path) -> dict[str, Path]:
    """Crea ventas, catálogo y stock sintéticos con patrones distintos."""
    rng = np.random.default_rng(7)
    weeks = pd.date_range("2024-01-01", "2025-06-30", freq="W-MON")
    profiles = {
        "P001": (120, 1), "P002": (70, 1), "P003": (35, 1), "P004": (18, 2),
        "P005": (9, 2), "P006": (5, 3), "P007": (2, 3), "P008": (1, 4),
        "P009": (3, 3), "P010": (0, 0), "P011": (4, 0), "P012": (6, 0),
    }
    rows = []
    for ean, (mean_qty, zeros) in profiles.items():
        for index, date in enumerate(weeks):
            if ean == "P010" and index < len(weeks) - 8:
                continue
            if ean == "P011" and date > pd.Timestamp("2024-12-01"):
                continue
            if ean == "P012" and date > pd.Timestamp("2024-06-30"):
                continue
            if zeros and index % (zeros + 1) == zeros:
                continue
            qty = max(1, int(rng.normal(mean_qty, max(1, mean_qty * 0.35))))
            price = 4.0 + (int(ean[-1]) % 5)
            rows.append({
                "EAN": ean, "Fecha": date, "Cantidad": qty,
                "Precio_Unitario": price, "Monto_Total": qty * price * 30.0,
                "Tasa_Cambio": 30.0, "Monto_USD": qty * price,
                "Cliente_ID": f"CLI-{index % 20:03d}", "Sucursal": "MATRIZ",
                "Vendedor": "VEND-001", "Descripcion": f"PRODUCTO {ean}",
            })
    sales = pd.DataFrame(rows)

    catalog = pd.DataFrame([
        {"EAN": f"P00{i}", "Descripcion": f"PRODUCTO P00{i}", "Proveedor": "VALMORCA",
         "Categoria": "ANALGESICO", "Subcategoria": "TEST", "Costo_USD": 3.0 + i,
         "Multiplo_Compra": 12 if i % 2 == 0 else 24, "Lead_Time": 30,
         "Stock_Minimo": 12, "Stock_Maximo": 2000}
        for i in range(1, 6)
    ])
    stock = pd.DataFrame([
        {"EAN": "P001", "Stock_Actual": 5},
        {"EAN": "P002", "Stock_Actual": 5000},
        {"EAN": "P003", "Stock_Actual": 100},
    ])

    sales_path = base / "ventas.xlsx"
    catalog_path = base / "catalogo.xlsx"
    stock_path = base / "stock.xlsx"
    with pd.ExcelWriter(sales_path, engine="openpyxl") as writer:
        sales.to_excel(writer, sheet_name="Ventas", index=False)
    with pd.ExcelWriter(catalog_path, engine="openpyxl") as writer:
        catalog.to_excel(writer, sheet_name="Productos", index=False)
    with pd.ExcelWriter(stock_path, engine="openpyxl") as writer:
        stock.to_excel(writer, sheet_name="stock", index=False)
    return {"sales": sales_path, "catalog": catalog_path, "stock": stock_path, "frame": sales}


class TestHelpers(unittest.TestCase):
    def test_ean_normalization(self):
        values = pd.Series([7591020000611, 7591020000611.0, "7591020000611.0", " 7591020000611 "])
        self.assertEqual(set(to_ean(values)), {"7591020000611"})

    def test_number_parsing(self):
        values = pd.Series(["1.234,56", "1,234.56", "$ 12,50", "(3,5)"])
        result = to_number(values)
        self.assertAlmostEqual(result.iloc[0], 1234.56, places=2)
        self.assertAlmostEqual(result.iloc[1], 1234.56, places=2)
        self.assertAlmostEqual(result.iloc[2], 12.50, places=2)
        self.assertAlmostEqual(result.iloc[3], -3.5, places=2)

    def test_multiple_rounding(self):
        self.assertEqual(round_to_multiple(25, 24), 48)
        self.assertEqual(round_to_multiple(23, 24, mode="down"), 0)
        self.assertEqual(round_to_multiple(0, 24), 0)


class TestAnalytics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.paths = build_fixture(Path(cls.tmp.name))
        cls.config = Config("config/parameters.yaml", base_dir=ROOT)
        cls.data = DataLoader(cls.paths["sales"], cls.paths["catalog"], cls.paths["stock"]).load_all()
        cls.enriched = CatalogEnricher(cls.config).build(
            cls.data.sales, cls.data.catalog, cls.data.stock, cls.data.as_of)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_catalog_fills_missing_products(self):
        products = self.enriched.products
        self.assertEqual(len(products), 12)
        self.assertGreater(products["Costo_USD"].min(), 0)
        self.assertTrue((products.loc[products["EAN"] == "P010", "Origen"] == "ESTIMADO").all())

    def test_abc_xyz_segmentation(self):
        abc = ABCAnalyzer(self.config).analyze(self.data.sales, self.data.as_of)
        xyz = XYZClassifier(self.config).classify(self.data.sales, self.data.as_of)
        seg = Segmenter(self.config).build(abc.products, xyz.products, self.enriched.products)
        self.assertEqual(len(abc.summary), 3)
        self.assertAlmostEqual(abc.summary["%_Valor_USD"].sum(), 100.0, places=4)
        self.assertTrue(set(xyz.products["XYZ"]).issubset({"X", "Y", "Z"}))
        self.assertGreaterEqual(len(seg.summary), 3)  # al menos A, B y C
        self.assertTrue({"A", "B", "C"}.issubset(set(seg.products["ABC_Valor"])))
        self.assertTrue(seg.products["Segmento"].str.len().eq(2).all())

    def test_forecast_bundle(self):
        abc = ABCAnalyzer(self.config).analyze(self.data.sales, self.data.as_of)
        xyz = XYZClassifier(self.config).classify(self.data.sales, self.data.as_of)
        seg = Segmenter(self.config).build(abc.products, xyz.products, self.enriched.products)
        bundle = ModelOrchestrator(self.config).run(
            self.data.sales, self.enriched.products, seg.products, self.data.as_of)
        self.assertGreater(len(bundle.forecasts), 8)
        self.assertFalse(bundle.validation.empty)
        self.assertTrue((bundle.validation["Origenes"] >= 0).all())
        self.assertIn("X", set(bundle.policy_table["Grupo"]))


class TestOrderRules(unittest.TestCase):
    def setUp(self):
        self.config = Config("config/parameters.yaml", base_dir=ROOT)
        self.calculator = OrderCalculator(self.config)
        self.provider = Provider(nombre="TEST", lead_time=30, revision=7, minimo=24, multiplo=24)
        self.forecast = ProductForecast(
            ean="X1", weekly_path=np.full(20, 50.0), model="ses", horizon_days=30,
            wape=20, mape=20, bias=0, hit25=50, sigma_28=10.0, confidence=0.7,
            weeks_history=60, last_sale=pd.Timestamp("2025-06-30"), status="ACTIVO",
        )

    def _product(self, **overrides):
        base = {
            "EAN": "X1", "Descripcion": "TEST", "Proveedor": "TEST", "ABC_Valor": "A",
            "Costo_USD": 2.0, "Multiplo_Compra": 24, "Lead_Time": 30,
            "Stock_Informado": True, "Stock_Actual": 500.0, "Stock_Disponible": 500.0,
            "Stock_Minimo": np.nan, "Stock_Maximo": np.nan,
        }
        base.update(overrides)
        return pd.Series(base)

    def test_enough_stock_returns_zero(self):
        decision = self.calculator.calculate(self.forecast, self._product(), self.provider)
        self.assertEqual(decision.cantidad, 0)
        self.assertEqual(decision.motivo, "STOCK_SUFICIENTE")

    def test_critical_stock_orders_multiple(self):
        product = self._product(Stock_Actual=5.0, Stock_Disponible=5.0)
        decision = self.calculator.calculate(self.forecast, product, self.provider)
        self.assertGreater(decision.cantidad, 0)
        self.assertEqual(decision.cantidad % 24, 0)
        self.assertEqual(decision.prioridad, "CRÍTICA")

    def test_unknown_stock_uses_demand(self):
        product = self._product(Stock_Informado=False, Stock_Actual=np.nan, Stock_Disponible=np.nan)
        decision = self.calculator.calculate(self.forecast, product, self.provider)
        self.assertGreater(decision.cantidad, 0)
        self.assertEqual(decision.cantidad % 24, 0)
        self.assertFalse(decision.stock_informado)

    def test_no_demand_returns_zero(self):
        forecast = ProductForecast(ean="X1", weekly_path=np.zeros(20), model="sin_demanda",
                                   horizon_days=30, wape=np.nan, mape=np.nan, bias=np.nan,
                                   hit25=np.nan, sigma_28=0, confidence=0, weeks_history=0,
                                   last_sale=None, status="ACTIVO")
        decision = self.calculator.calculate(forecast, self._product(), self.provider)
        self.assertEqual(decision.cantidad, 0)
        self.assertEqual(decision.motivo, "SIN_DEMANDA")


class TestEndToEnd(unittest.TestCase):
    def test_pipeline_generates_excel_and_dashboard(self):
        from reporting.context import ReportContext
        from reporting.dashboard_generator import DashboardGenerator
        from reporting.excel_generator import ExcelReportGenerator
        from recommendation.alert_system import AlertSystem
        from recommendation.order_manager import OrderManager
        from datetime import datetime

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            paths = build_fixture(base)
            config = Config("config/parameters.yaml", base_dir=ROOT)
            config.set("data.provider_file", str(base / "proveedores.csv"))
            config.set("data.product_file", str(base / "productos.csv"))
            config.set("output.open_when_done", False)
            config._load_providers()

            data = DataLoader(paths["sales"], paths["catalog"], paths["stock"]).load_all()
            rich = CatalogEnricher(config).build(data.sales, data.catalog, data.stock, data.as_of)
            abc = ABCAnalyzer(config).analyze(data.sales, data.as_of)
            xyz = XYZClassifier(config).classify(data.sales, data.as_of)
            seg = Segmenter(config).build(abc.products, xyz.products, rich.products)
            forecasts = ModelOrchestrator(config).run(data.sales, rich.products, seg.products, data.as_of)
            orders = OrderManager(config).generate(forecasts.forecasts, rich.products, seg.products)
            alerts = AlertSystem(config).generate(orders, rich.products, forecasts.forecasts, seg.products)

            self.assertGreater(len(orders.orders), 0)
            self.assertTrue((orders.orders["Cantidad_Sugerida"] % orders.orders["Multiplo"] == 0).all())
            self.assertFalse(alerts.alerts.empty)

            context = ReportContext(
                config=config, data=data, products=rich.products, abc=abc, xyz=xyz,
                segmentation=seg, forecast_bundle=forecasts, order_bundle=orders,
                alert_bundle=alerts, quality=data.quality, generated_at=datetime.now(),
                output_dir=base, elapsed_seconds=1.0,
            )
            excel = ExcelReportGenerator(context, base / "reporte.xlsx").generate()
            dashboard = DashboardGenerator(context, base / "dashboard.html").generate()
            self.assertTrue(excel.exists() and excel.stat().st_size > 20000)
            self.assertTrue(dashboard.exists())
            html = dashboard.read_text(encoding="utf-8")
            self.assertIn("Dashboard ejecutivo", html)

            from openpyxl import load_workbook
            wb = load_workbook(excel)
            expected = {"RESUMEN", "ÓRDENES SUGERIDAS", "ALERTAS CRÍTICAS", "ANÁLISIS ABC-XYZ",
                        "DETALLE PRODUCTOS", "PRECISIÓN MODELOS", "PARÁMETROS",
                        "CATÁLOGO FALTANTE", "DATOS Y CALIDAD"}
            self.assertTrue(expected.issubset(set(wb.sheetnames)))

    def test_cli_runs(self):
        import main as cli
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            paths = build_fixture(base)
            config_data = yaml.safe_load((ROOT / "config" / "parameters.yaml").read_text(encoding="utf-8"))
            config_data["data"]["provider_file"] = str(base / "proveedores.csv")
            config_data["data"]["product_file"] = str(base / "productos.csv")
            config_data["output"]["open_when_done"] = False
            config_data["output"]["dashboard"] = False
            config_path = base / "parameters.yaml"
            config_path.write_text(yaml.safe_dump(config_data, allow_unicode=True), encoding="utf-8")

            result = cli.run([
                "--sales", str(paths["sales"]), "--catalog", str(paths["catalog"]),
                "--stock", str(paths["stock"]), "--config", str(config_path),
                "--output", str(base / "out"), "--no-open", "--no-dashboard", "--quiet",
            ])
            self.assertTrue(Path(result["excel"]).exists())
            self.assertIsNone(result["dashboard"])
            self.assertGreater(len(result["orders"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
