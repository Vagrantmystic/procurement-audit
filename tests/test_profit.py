"""Pruebas de la capa Profit: solo lectura, conversión USD y adaptador."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_processing.profit_connection import ProfitConnection, ReadOnlyViolation  # noqa: E402
from data_processing.profit_loader import ProfitData, ProfitLoader  # noqa: E402
from data_processing.profit_source import to_loaded_data  # noqa: E402
from utils.config import Config  # noqa: E402


class TestReadOnly(unittest.TestCase):
    def test_select_allowed(self):
        ProfitConnection._validate("SELECT 1")
        ProfitConnection._validate("WITH x AS (SELECT 1 AS a) SELECT * FROM x")

    def test_writes_rejected(self):
        for sql in ("DELETE FROM saFacturaCompra", "UPDATE saArticulo SET stock_min=0",
                    "INSERT INTO saProveedor VALUES (1)", "EXEC sp_who", "DROP TABLE x"):
            with self.assertRaises(ReadOnlyViolation):
                ProfitConnection._validate(sql)

    def test_multiple_statements_rejected(self):
        with self.assertRaises(ReadOnlyViolation):
            ProfitConnection._validate("SELECT 1; SELECT 2")


class TestCurrency(unittest.TestCase):
    def test_usd_document_uses_invoice_rate(self):
        df = pd.DataFrame({
            "Fecha": ["2026-01-15"], "Moneda": ["USD   "], "Tasa": [800.0],
            "Cantidad": [10.0], "Importe": [8000.0], "DescuentoLinea": [0.0],
        })
        out = ProfitLoader._to_usd(df, pd.DataFrame(columns=["Mes", "Tasa"]), "Importe")
        self.assertAlmostEqual(out["Importe_USD"].iloc[0], 10.0, places=4)
        self.assertAlmostEqual(out["CostoUnitario_USD"].iloc[0], 1.0, places=4)

    def test_bs_document_uses_monthly_rate(self):
        df = pd.DataFrame({
            "Fecha": ["2026-01-15"], "Moneda": ["BS    "], "Tasa": [1.0],
            "Cantidad": [10.0], "Importe": [7500.0], "DescuentoLinea": [0.0],
        })
        rates = pd.DataFrame({"Mes": ["2026-01"], "Tasa": [750.0]})
        out = ProfitLoader._to_usd(df, rates, "Importe")
        self.assertAlmostEqual(out["Importe_USD"].iloc[0], 10.0, places=4)


class TestAdapter(unittest.TestCase):
    def test_to_loaded_data(self):
        cfg = Config("config/parameters.yaml", base_dir=ROOT)
        as_of = pd.Timestamp("2026-10-23")
        sales = pd.DataFrame({
            "EAN": ["A1", "A1"], "Fecha": [pd.Timestamp("2026-01-02"), pd.Timestamp("2026-02-02")],
            "Cantidad": [5.0, 7.0], "Importe_USD": [50.0, 70.0],
            "PrecioUnitario_USD": [10.0, 10.0], "PrecioUnitario": [8000.0, 8000.0],
            "DocBruto": [50.0, 70.0], "Tasa": [800.0, 800.0], "CodCliente": ["C1", "C1"],
            "Sucursal": ["01", "01"], "Vendedor": ["V1", "V1"], "Descripcion": ["TEST", "TEST"],
            "TipoArticulo": ["V", "V"],
        })
        products = pd.DataFrame({
            "EAN": ["A1"], "Descripcion": ["TEST"], "TipoArticulo": ["V"],
            "CodLinea": ["L1"], "Linea": ["LINEA"], "CodSublinea": ["S1"], "Sublinea": ["SUB"],
            "CodCategoria": ["C1"], "Categoria": ["CAT"], "Stock_Minimo": [0.0], "Stock_Maximo": [0.0],
            "Anulado": [False], "Fecha_Inactivacion": [None],
        })
        purchases = pd.DataFrame({
            "EAN": ["A1"], "Fecha": [pd.Timestamp("2026-01-01")], "TipoArticulo": ["V"],
            "CostoUnitario_USD": [6.0], "Importe_USD": [60.0], "Cantidad": [10.0],
            "Proveedor": ["PROV"], "CodProveedor": ["P1"], "Moneda": ["USD"], "Tasa": [800.0],
        })
        stock = pd.DataFrame({"EAN": ["A1"], "Stock_Actual": [20.0], "Stock_Transito": [5.0],
                              "Stock_Comprometido": [0.0], "Stock_Disponible": [25.0]})
        empty = pd.DataFrame()
        data = ProfitData(purchases=purchases, sales=sales, products=products,
                          suppliers=empty, stock=stock, product_supplier=empty,
                          returns=empty, rates=empty, as_of=as_of, quality=empty)
        loaded = to_loaded_data(data, cfg)
        self.assertEqual(len(loaded.sales), 2)
        self.assertEqual(len(loaded.catalog), 1)
        self.assertEqual(loaded.catalog.iloc[0]["Proveedor"], "PROV")
        self.assertAlmostEqual(loaded.catalog.iloc[0]["Costo_USD"], 6.0)
        self.assertAlmostEqual(loaded.stock.iloc[0]["Stock_Disponible"], 25.0)
        self.assertEqual(loaded.as_of, as_of)


if __name__ == "__main__":
    unittest.main(verbosity=2)
