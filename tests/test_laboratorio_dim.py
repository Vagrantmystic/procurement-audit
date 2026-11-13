"""Pruebas de la dimensión maestra de laboratorio."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import data_processing.laboratorio_loader as loader  # noqa: E402
from data_processing.laboratorio_loader import (  # noqa: E402
    attach_laboratorio, coverage_report, load_laboratorio_dim, normalize_code,
)


class TestLaboratorioDimension(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "lab.md"
        self.path.write_text(
            "CODIGO\t PROVEEDOR\t DESCRIPCION\n"
            "759.712.81\t ALESS \t LIDOCAINA\n"
            "75971304\tALESS\tAMTACILYN\n"
            "75971304\tOTRO\tDUPLICADO\n"
            "75999999\tBAYER\tASPIRINA\n",
            encoding="utf-8",
        )
        self.old_cache = loader.CACHE_DIR
        loader.CACHE_DIR = Path(self.tmp.name) / "cache"

    def tearDown(self):
        loader.CACHE_DIR = self.old_cache
        self.tmp.cleanup()

    def test_normalize_code(self):
        self.assertEqual(normalize_code("759.712.81"), "75971281")
        self.assertEqual(normalize_code(" 075971281 "), "075971281")
        self.assertEqual(normalize_code("75971281.0"), "75971281")
        self.assertEqual(normalize_code(None), "")

    def test_parse_and_dedup_most_frequent(self):
        dim = load_laboratorio_dim(self.path, use_cache=False)
        self.assertEqual(len(dim), 3)
        index = dim.set_index("Codigo")
        self.assertEqual(index.loc["75971281", "Laboratorio"], "ALESS")
        self.assertEqual(index.loc["75971304", "Laboratorio"], "ALESS")
        self.assertEqual(index.loc["75999999", "Laboratorio"], "BAYER")
        self.assertEqual(set(dim.columns), {"Codigo", "Laboratorio", "Descripcion", "Fuente"})

    def test_attach_and_unmatched_bucket(self):
        dim = load_laboratorio_dim(self.path, use_cache=False)
        frame = pd.DataFrame({
            "EAN": ["75971281", "75999999", "0000000000001"],
            "Linea": ["ANSIOLITICO", "ANALGESICO", ""],
            "Venta_USD": [80.0, 20.0, 10.0],
        })
        out = attach_laboratorio(frame, dim)
        self.assertEqual(out.loc[0, "Laboratorio"], "ALESS")
        self.assertEqual(out.loc[1, "Laboratorio"], "BAYER")
        self.assertEqual(out.loc[2, "Laboratorio"], "SIN_LABORATORIO")
        self.assertEqual(out.loc[0, "Linea_Terapeutica"], "ANSIOLITICO")
        self.assertNotIn("Linea", out.columns)
        cov = coverage_report(out)
        self.assertAlmostEqual(cov["venta_pct"], 90.909, places=2)
        self.assertEqual(cov["codigos_match"], 2)

    def test_cache_roundtrip(self):
        first = load_laboratorio_dim(self.path, use_cache=True)
        second = load_laboratorio_dim(self.path, use_cache=True)
        self.assertEqual(len(first), len(second))
        self.assertEqual(second.attrs.get("cache"), "hit")


if __name__ == "__main__":
    unittest.main(verbosity=2)
