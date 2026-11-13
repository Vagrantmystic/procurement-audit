"""Lanzador amigable para el equipo de compras.

Ejecuta el sistema con la configuración y los archivos por defecto de data/raw.
También acepta los mismos argumentos que main.py.
"""
from __future__ import annotations

import sys

from main import main as run_main


BANNER = r"""
==============================================================
  DROGUERIA EMMANUELLE
  Sistema de Prediccion y Optimizacion de Compras
==============================================================
  Genera un Excel con:
    - Ordenes de compra sugeridas y priorizadas
    - Alertas criticas (quiebres, exceso, tendencias)
    - Analisis ABC/XYZ y estrategia por segmento
    - Precision del modelo y parametros editables
==============================================================
"""


def main() -> int:
    print(BANNER)
    args = sys.argv[1:]
    return run_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
