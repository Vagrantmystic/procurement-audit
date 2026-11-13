"""Punto de entrada del Sistema de Predicción y Optimización de Compras.

Uso:
    python main.py
    python main.py --sales data/raw/ventas.xlsx --catalog data/raw/catalogo.xlsx --stock data/raw/stock.xlsx
    python main.py --as-of 30/09/2025 --horizon 45 --service-level 0.975
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

# --------------------------------------------------------------------------- path
BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from analytics.abc_analyzer import ABCAnalyzer  # noqa: E402
from analytics.segmentation import Segmenter  # noqa: E402
from analytics.xyz_classifier import XYZClassifier  # noqa: E402
from data_processing.catalog_enricher import CatalogEnricher  # noqa: E402
from data_processing.loader import DataLoader  # noqa: E402
from data_processing.profit_connection import ProfitConnection  # noqa: E402
from data_processing.profit_loader import ProfitLoader  # noqa: E402
from data_processing.profit_source import to_loaded_data  # noqa: E402
from models.model_orchestrator import ModelOrchestrator  # noqa: E402
from recommendation.alert_system import AlertSystem  # noqa: E402
from recommendation.order_manager import OrderManager  # noqa: E402
from reporting.context import ReportContext  # noqa: E402
from reporting.dashboard_generator import DashboardGenerator  # noqa: E402
from reporting.excel_generator import ExcelReportGenerator  # noqa: E402
from utils.config import Config  # noqa: E402
from utils.helpers import find_input_file  # noqa: E402
from utils.logging_utils import setup_logging  # noqa: E402


def configure_console() -> None:
    """Evita errores de codificación en consolas Windows con acentos y símbolos."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sistema de Predicción y Optimización de Compras - Droguería Emmanuelle",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--sales", help="Archivo Excel de ventas históricas")
    parser.add_argument("--catalog", help="Archivo Excel del catálogo maestro")
    parser.add_argument("--stock", help="Archivo Excel de stock actual (opcional)")
    parser.add_argument("--source", choices=["excel", "profit"], default="excel",
                        help="Origen de datos: excel (archivos) o profit (SQL Server, solo lectura)")
    parser.add_argument("--db-config", default="config/database.local.yaml",
                        help="Configuración de conexión a Profit")
    parser.add_argument("--config", default="config/parameters.yaml", help="Archivo de parámetros")
    parser.add_argument("--output", default=None, help="Directorio de salida")
    parser.add_argument("--as-of", dest="as_of", default=None,
                        help="Fecha de corte DD/MM/AAAA para simular un período histórico")
    parser.add_argument("--horizon", type=int, default=None, help="Horizonte de pronóstico en días")
    parser.add_argument("--service-level", type=float, default=None,
                        help="Nivel de servicio para stock de seguridad (0.90, 0.95, 0.975, 0.99)")
    parser.add_argument("--lead-time", type=int, default=None, help="Lead time por defecto en días")
    parser.add_argument("--no-open", action="store_true", help="No abrir el Excel al finalizar")
    parser.add_argument("--no-dashboard", action="store_true", help="No generar el dashboard HTML")
    parser.add_argument("--quiet", action="store_true", help="Reducir mensajes en consola")
    return parser.parse_args(argv)


def _resolve_inputs(args: argparse.Namespace, config: Config) -> tuple[Path, Path, Path | None]:
    sales = find_input_file(BASE_DIR, args.sales or config.get("data.sales_file"),
                            ["data/raw/ventas*.xlsx", "data/raw/*venta*.xlsx", "data/raw/*.xlsx"])
    catalog = find_input_file(BASE_DIR, args.catalog or config.get("data.catalog_file"),
                              ["data/raw/catalogo*.xlsx", "data/raw/*catalog*.xlsx"])
    stock = find_input_file(BASE_DIR, args.stock or config.get("data.stock_file"),
                            ["data/raw/stock*.xlsx", "data/raw/*stock*.xlsx", "data/raw/*inventario*.xlsx"])
    if sales is None:
        raise FileNotFoundError("No se encontró el archivo de ventas. Use --sales o colóquelo en data/raw/.")
    if catalog is None:
        raise FileNotFoundError("No se encontró el catálogo maestro. Use --catalog o colóquelo en data/raw/.")
    return sales, catalog, stock


def _open_file(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except OSError:
        pass


def run(argv: list[str] | None = None) -> dict:
    configure_console()
    args = parse_args(argv)
    started = time.time()

    config = Config(args.config, base_dir=BASE_DIR)
    if args.output:
        config.set("output.dir", args.output)
    if args.horizon:
        config.set("forecasting.horizon_days", args.horizon)
    if args.service_level:
        config.set("inventory.service_level", args.service_level)
    if args.lead_time:
        config.set("inventory.default_lead_time", args.lead_time)
        if "DEFAULT" in config.providers:
            config.providers["DEFAULT"].lead_time = args.lead_time

    output_dir = Path(config.get("output.dir", "outputs"))
    if not output_dir.is_absolute():
        output_dir = BASE_DIR / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = setup_logging("WARNING" if args.quiet else config.get("logging.level", "INFO"),
                           output_dir / "logs")
    logger.info("=" * 68)
    logger.info("SISTEMA DE PREDICCIÓN Y OPTIMIZACIÓN DE COMPRAS")
    logger.info("Droguería Emmanuelle | %s", datetime.now().strftime("%d/%m/%Y %H:%M"))
    logger.info("=" * 68)

    # ---------------- 1. datos
    logger.info("Paso 1/7 · Cargando datos de entrada...")
    if args.source == "profit":
        as_of_ts = pd.to_datetime(args.as_of, dayfirst=True) if args.as_of else pd.Timestamp.today().normalize()
        start = f"{as_of_ts.year - 2}-01-01"
        end = (as_of_ts + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        db_config = Path(args.db_config)
        if not db_config.is_absolute():
            db_config = BASE_DIR / db_config
        logger.info("  Fuente:   Profit 2K12 (solo lectura) · %s a %s", start, end)
        connection = ProfitConnection(db_config, logger)
        profit = ProfitLoader(connection, logger).load_all(start, end, as_of=as_of_ts)
        data = to_loaded_data(profit, config, logger)
        output_prefix = "ordenes_compra_profit"
    else:
        sales_path, catalog_path, stock_path = _resolve_inputs(args, config)
        logger.info("  Ventas:   %s", sales_path)
        logger.info("  Catálogo: %s", catalog_path)
        logger.info("  Stock:    %s", stock_path or "(no informado)")
        loader = DataLoader(sales_path, catalog_path, stock_path, logger)
        data = loader.load_all(as_of=args.as_of)
        output_prefix = str(config.get("output.excel_prefix", "ordenes_compra"))
    logger.info("  Corte: %s | %s ventas | %s productos", f"{data.as_of:%d/%m/%Y}",
                f"{len(data.sales):,}", f"{data.sales['EAN'].nunique():,}")

    # ---------------- 2. catálogo
    logger.info("Paso 2/7 · Construyendo maestro de productos...")
    enricher = CatalogEnricher(config, logger)
    catalog = enricher.build(data.sales, data.catalog, data.stock, data.as_of)
    products = catalog.products

    # ---------------- 3. ABC/XYZ
    logger.info("Paso 3/7 · Análisis ABC/XYZ y segmentación...")
    abc = ABCAnalyzer(config, logger).analyze(data.sales, data.as_of)
    xyz = XYZClassifier(config, logger).classify(data.sales, data.as_of)
    segmentation = Segmenter(config, logger).build(abc.products, xyz.products, products)

    # ---------------- 4. pronóstico
    logger.info("Paso 4/7 · Pronóstico y backtesting...")
    forecast_bundle = ModelOrchestrator(config, logger).run(
        data.sales, products, segmentation.products, data.as_of)

    # ---------------- 5. órdenes
    logger.info("Paso 5/7 · Cálculo de órdenes de compra...")
    order_bundle = OrderManager(config, logger).generate(
        forecast_bundle.forecasts, products, segmentation.products)

    # ---------------- 6. alertas
    logger.info("Paso 6/7 · Generación de alertas...")
    alert_bundle = AlertSystem(config, logger).generate(
        order_bundle, products, forecast_bundle.forecasts, segmentation.products, data.quality)

    # ---------------- 7. reportes
    logger.info("Paso 7/7 · Generando reportes...")
    quality = pd.concat([data.quality, catalog.quality], ignore_index=True)
    context = ReportContext(
        config=config, data=data, products=products, abc=abc, xyz=xyz,
        segmentation=segmentation, forecast_bundle=forecast_bundle,
        order_bundle=order_bundle, alert_bundle=alert_bundle, quality=quality,
        generated_at=datetime.now(), output_dir=output_dir,
        elapsed_seconds=time.time() - started,
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = output_prefix
    excel_path = output_dir / f"{prefix}_{timestamp}.xlsx"
    context.elapsed_seconds = time.time() - started
    ExcelReportGenerator(context, excel_path, logger).generate()

    dashboard_path = None
    if config.get("output.dashboard", True) and not args.no_dashboard:
        dashboard_path = output_dir / f"dashboard_{timestamp}.html"
        context.elapsed_seconds = time.time() - started
        DashboardGenerator(context, dashboard_path, excel_name=excel_path.name, logger=logger).generate()
    context.elapsed_seconds = time.time() - started

    # ---------------- resumen
    summary = order_bundle.summary
    metrics = forecast_bundle.metrics
    logger.info("=" * 68)
    logger.info("PROCESO COMPLETADO en %.1f s", context.elapsed_seconds)
    logger.info("  Productos analizados: %s", f"{len(segmentation.products):,}")
    logger.info("  Órdenes sugeridas:    %s", f"{summary['ordenes']:,}")
    logger.info("  Unidades:             %s", f"{summary['unidades']:,}")
    logger.info("  Inversión estimada:   $%s", f"{summary['inversion_usd']:,.2f}")
    logger.info("  Excluidos EOL:        %s (stock $%s)", f"{summary.get('excluidos_eol', 0):,}",
                f"{summary.get('valor_excluido_usd', 0):,.0f}")
    logger.info("  LLE pendientes:       %s", f"{summary.get('lle_pendientes', 0):,}")
    logger.info("  Precios bloqueados:   %s | aprobaciones: %s",
                f"{summary.get('precio_bloqueados', 0):,}", f"{summary.get('aprobaciones', 0):,}")
    logger.info("  Alertas:              %s", f"{len(alert_bundle.alerts):,}")
    logger.info("  WAPE A/B:             %.1f%% (CV %.1f%%)",
                metrics.get("wape_ab", 0), metrics.get("wape_ab_cv", 0))
    logger.info("  Excel:                %s", excel_path)
    if dashboard_path:
        logger.info("  Dashboard:            %s", dashboard_path)
    logger.info("=" * 68)

    should_open = config.get("output.open_when_done", False) and not args.no_open
    if should_open:
        _open_file(excel_path)

    return {
        "excel": excel_path,
        "dashboard": dashboard_path,
        "orders": order_bundle.orders,
        "order_summary": order_bundle.summary,
        "excluded": order_bundle.excluded,
        "price_alerts": order_bundle.price_alerts,
        "alerts": alert_bundle.alerts,
        "segmentation": segmentation.products,
        "forecasts": forecast_bundle.forecasts,
        "metrics": metrics,
        "config": config,
        "elapsed": context.elapsed_seconds,
    }


def main(argv: list[str] | None = None) -> int:
    try:
        result = run(argv)
    except FileNotFoundError as exc:
        configure_console()
        print(f"ERROR: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001
        configure_console()
        logging.getLogger("compras").exception("Error inesperado: %s", exc)
        print(f"ERROR INESPERADO: {exc}")
        print("Revise el log en outputs/logs/ para el detalle.")
        return 1
    print(f"\nListo. Reporte generado en: {result['excel']}")
    if result.get("dashboard"):
        print(f"Dashboard: {result['dashboard']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
