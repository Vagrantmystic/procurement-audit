"""Auditoría de compras directamente desde Profit 2K12 (solo lectura).

Uso:
    python auditoria.py
    python auditoria.py --year 2026 --as-of 23/10/2026
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from audit.purchase_audit import PurchaseAudit  # noqa: E402
from data_processing.profit_connection import ProfitConnection  # noqa: E402
from data_processing.profit_loader import ProfitLoader  # noqa: E402
from reporting.audit_dashboard import AuditDashboardGenerator  # noqa: E402
from reporting.audit_excel import AuditExcelGenerator  # noqa: E402
from utils.config import Config  # noqa: E402
from utils.logging_utils import setup_logging  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auditoría de compras Profit 2K12")
    parser.add_argument("--year", type=int, default=None, help="Año a auditar (default: año actual)")
    parser.add_argument("--as-of", dest="as_of", default=None, help="Fecha de corte DD/MM/AAAA")
    parser.add_argument("--start", default=None, help="Inicio del histórico a extraer (YYYY-MM-DD)")
    parser.add_argument("--config", default="config/parameters.yaml")
    parser.add_argument("--db-config", default="config/database.local.yaml")
    parser.add_argument("--output", default="outputs")
    parser.add_argument("--no-dashboard", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = parse_args(argv)
    started = time.time()
    config = Config(args.config, base_dir=BASE_DIR)
    output_dir = Path(args.output)
    if not output_dir.is_absolute():
        output_dir = BASE_DIR / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = setup_logging("WARNING" if args.quiet else "INFO", output_dir / "logs")

    as_of = args.as_of or datetime.now().strftime("%d/%m/%Y")
    year = args.year or datetime.now().year
    start = args.start or f"{year - 1}-01-01"
    end = f"{year + 1}-01-01"

    logger.info("=" * 68)
    logger.info("AUDITORÍA DE COMPRAS %s · Profit 2K12 (solo lectura)", year)
    logger.info("=" * 68)
    loader = ProfitLoader(ProfitConnection(args.db_config, logger), logger)
    data = loader.load_all(start, end, as_of=as_of)
    audit = PurchaseAudit(config, logger).run(data, year=year)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_path = output_dir / f"auditoria_compras_{year}_{timestamp}.xlsx"
    AuditExcelGenerator(audit, excel_path, logger).generate()
    dashboard_path = None
    if not args.no_dashboard:
        dashboard_path = output_dir / f"auditoria_compras_{year}_{timestamp}.html"
        AuditDashboardGenerator(audit, dashboard_path, excel_name=excel_path.name, logger=logger).generate()

    m = audit.metrics
    logger.info("-" * 68)
    logger.info("Compra: $%s | Venta: $%s | Margen: %.1f%%",
                f"{m['compra_usd']:,.0f}", f"{m['venta_usd']:,.0f}", m["margen_pct"])
    logger.info("Quiebres: %s | Críticos: %s | Faltantes: %s | Sobrecompra: %s",
                m["quiebres"], m["criticos"], m["faltantes"], m["sobrecompra"])
    logger.info("Capital en exceso: $%s | Venta inmediata en riesgo: $%s",
                f"{m['capital_exceso_usd']:,.0f}", f"{m['venta_en_riesgo_usd']:,.0f}")
    logger.info("Excel: %s", excel_path)
    if dashboard_path:
        logger.info("Dashboard: %s", dashboard_path)
    logger.info("Tiempo: %.1f s", time.time() - started)
    print(f"\nAuditoría generada: {excel_path}")
    if dashboard_path:
        print(f"Dashboard: {dashboard_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
