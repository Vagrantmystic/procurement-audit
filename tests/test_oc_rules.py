"""Pruebas de las reglas de OC: ACT, LLE, EOL y precios."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_processing.catalog_enricher import CatalogEnricher  # noqa: E402
from models.forecast_engine import ProductForecast  # noqa: E402
from recommendation.order_calculator import OrderCalculator  # noqa: E402
from utils.config import Config, Provider  # noqa: E402


class TestOrderRules(unittest.TestCase):
    def setUp(self):
        self.base = Config("config/parameters.yaml", base_dir=ROOT)
        self.provider = Provider(nombre="TEST", lead_time=30, revision=7, minimo=24, multiplo=24)
        self.forecast = ProductForecast(
            ean="X1", weekly_path=np.full(20, 50.0), model="ses", horizon_days=30,
            wape=20, mape=20, bias=0, hit25=50, sigma_28=10.0, confidence=0.7,
            weeks_history=60, last_sale=pd.Timestamp("2026-10-01"), status="ACTIVO",
        )

    def _calculator(self, **settings) -> OrderCalculator:
        config = self.base.copy()
        for key, value in settings.items():
            config.set(key, value)
        return OrderCalculator(config)

    def _product(self, **overrides):
        base = {
            "EAN": "X1", "Descripcion": "TEST", "Proveedor": "TEST", "ABC_Valor": "A",
            "Costo_USD": 10.0, "Costo_Ultimo_USD": 10.0, "Costo_Ref_90d": 10.0,
            "Var_Precio_Pct": 0.0, "Precio_Prom_USD": 20.0, "Costo_Estimado": False,
            "Multiplo_Compra": 24, "Lead_Time": 30, "Stock_Informado": True,
            "Stock_Actual": 10.0, "Comprometido": 0.0, "En_Transito": 0.0,
            "Stock_Disponible": 10.0, "Stock_Minimo": np.nan, "Stock_Maximo": np.nan,
            "Estado_Ciclo_Vida": "ACTIVO", "Compra_Bloqueada": False,
        }
        base.update(overrides)
        return pd.Series(base)

    def test_lle_never_counts_as_stock_and_blocks(self):
        calc = self._calculator(**{"inventory.lle_policy": "block"})
        product = self._product(Stock_Actual=10.0, Comprometido=0.0, En_Transito=100.0,
                                Stock_Disponible=110.0)
        decision = calc.calculate(self.forecast, product, self.provider)
        self.assertEqual(decision.motivo, "LLE_PENDIENTE")
        self.assertEqual(decision.cantidad, 0)
        self.assertEqual(decision.stock_usado_oc, 10.0)
        self.assertEqual(decision.stock_lle, 100.0)

    def test_lle_ignored_uses_act_not_disponible(self):
        calc = self._calculator(**{"inventory.lle_policy": "ignore"})
        product = self._product(Stock_Actual=10.0, Comprometido=0.0, En_Transito=100.0,
                                Stock_Disponible=110.0)
        decision = calc.calculate(self.forecast, product, self.provider)
        self.assertGreater(decision.cantidad, 0)
        self.assertEqual(decision.stock_usado_oc, 10.0)

    def test_committed_is_subtracted(self):
        calc = self._calculator(**{"inventory.lle_policy": "ignore"})
        product = self._product(Stock_Actual=100.0, Comprometido=90.0, En_Transito=0.0,
                                Stock_Disponible=100.0)
        decision = calc.calculate(self.forecast, product, self.provider)
        self.assertEqual(decision.stock_usado_oc, 10.0)

    def test_eol_blocks_order(self):
        calc = self._calculator()
        product = self._product(Estado_Ciclo_Vida="OBSOLETO", Compra_Bloqueada=True,
                                Motivo_Bloqueo="Sin ventas y con stock inmovilizado")
        decision = calc.calculate(self.forecast, product, self.provider)
        self.assertEqual(decision.motivo, "EOL_BLOQUEADO")
        self.assertEqual(decision.cantidad, 0)
        self.assertEqual(decision.estado_ciclo_vida, "OBSOLETO")

    def test_price_above_ceiling_blocks(self):
        calc = self._calculator()
        product = self._product(Costo_Ref_90d=10.0, Costo_Ultimo_USD=13.0,
                                Var_Precio_Pct=30.0, Precio_Prom_USD=20.0)
        decision = calc.calculate(self.forecast, product, self.provider)
        self.assertEqual(decision.motivo, "PRECIO_FUERA_DE_TECHO")
        self.assertEqual(decision.cantidad, 0)
        self.assertEqual(decision.precio_estado, "ROJO")

    def test_price_requires_approval_but_allows_order(self):
        calc = self._calculator()
        product = self._product(Costo_Ref_90d=10.0, Costo_Ultimo_USD=11.2,
                                Var_Precio_Pct=12.0, Precio_Prom_USD=20.0)
        decision = calc.calculate(self.forecast, product, self.provider)
        self.assertGreater(decision.cantidad, 0)
        self.assertTrue(decision.aprobacion_requerida)
        self.assertEqual(decision.precio_estado, "NARANJA")

    def test_low_margin_blocks(self):
        calc = self._calculator()
        product = self._product(Costo_Ref_90d=10.0, Costo_Ultimo_USD=10.0,
                                Var_Precio_Pct=0.0, Precio_Prom_USD=10.5)
        decision = calc.calculate(self.forecast, product, self.provider)
        self.assertEqual(decision.motivo, "MARGEN_BAJO")
        self.assertEqual(decision.cantidad, 0)


class TestLifecycle(unittest.TestCase):
    def setUp(self):
        self.config = Config("config/parameters.yaml", base_dir=ROOT)

    def _products(self):
        return pd.DataFrame([
            {"EAN": "A", "Ultima_Venta": pd.Timestamp("2026-01-01"),
             "Primera_Venta": pd.Timestamp("2025-01-01"), "Stock_Actual": 10.0,
             "Stock_Informado": True, "Estado_Ciclo_Vida_Manual": "",
             "Compra_Bloqueada_Manual": False, "Dias_Para_Vencer": np.nan},
            {"EAN": "B", "Ultima_Venta": pd.NaT, "Primera_Venta": pd.NaT,
             "Stock_Actual": 0.0, "Stock_Informado": True,
             "Estado_Ciclo_Vida_Manual": "", "Compra_Bloqueada_Manual": False,
             "Dias_Para_Vencer": np.nan},
            {"EAN": "C", "Ultima_Venta": pd.Timestamp("2026-09-01"),
             "Primera_Venta": pd.Timestamp("2025-01-01"), "Stock_Actual": 10.0,
             "Stock_Informado": True, "Estado_Ciclo_Vida_Manual": "EOL",
             "Compra_Bloqueada_Manual": False, "Dias_Para_Vencer": np.nan},
            {"EAN": "D", "Ultima_Venta": pd.Timestamp("2026-09-01"),
             "Primera_Venta": pd.Timestamp("2025-01-01"), "Stock_Actual": 10.0,
             "Stock_Informado": True, "Estado_Ciclo_Vida_Manual": "",
             "Compra_Bloqueada_Manual": False, "Dias_Para_Vencer": 90.0},
        ])

    def test_lifecycle_states(self):
        enricher = CatalogEnricher(self.config)
        out = enricher._lifecycle(self._products(), pd.Timestamp("2026-10-23"))
        by_ean = out.set_index("EAN")
        self.assertEqual(by_ean.loc["A", "Estado_Ciclo_Vida"], "OBSOLETO")
        self.assertTrue(bool(by_ean.loc["A", "Compra_Bloqueada"]))
        self.assertEqual(by_ean.loc["B", "Estado_Ciclo_Vida"], "EN_OBSERVACION")
        self.assertFalse(bool(by_ean.loc["B", "Compra_Bloqueada"]))
        self.assertEqual(by_ean.loc["C", "Estado_Ciclo_Vida"], "EOL")
        self.assertTrue(bool(by_ean.loc["C", "Compra_Bloqueada"]))
        self.assertEqual(by_ean.loc["D", "Estado_Ciclo_Vida"], "LIQUIDACION")
        self.assertTrue(bool(by_ean.loc["D", "Compra_Bloqueada"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
