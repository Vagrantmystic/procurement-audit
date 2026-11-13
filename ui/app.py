"""Interfaz web local para el sistema de compras (Flask).

Ejecuta auditorías, órdenes desde Profit y experimentos ML/DL sin exponer
las credenciales al navegador. Solo escucha en 127.0.0.1.
"""
from __future__ import annotations

import logging
import math
import sys
import threading
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from audit.purchase_audit import PurchaseAudit  # noqa: E402
from data_processing.profit_connection import ProfitConnection  # noqa: E402
from data_processing.profit_loader import ProfitLoader  # noqa: E402
from data_processing.warehouse_filter import WarehouseFilter  # noqa: E402
from models.global_ml import run_experiment  # noqa: E402
from reporting.audit_dashboard import AuditDashboardGenerator  # noqa: E402
from reporting.audit_excel import AuditExcelGenerator  # noqa: E402
from utils.config import Config  # noqa: E402

APP_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
DB_CONFIG = BASE_DIR / "config" / "database.local.yaml"
APP = Flask(__name__, template_folder=str(APP_DIR / "templates"), static_folder=str(APP_DIR / "static"))
JOBS: dict[str, dict[str, Any]] = {}
LAST: dict[str, Any] = {}
JOB_LOCK = threading.Lock()
RUNNING = {"id": None}

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
from ventas_bp import bp as ventas_bp, prewarm as prewarm_ventas  # noqa: E402

APP.register_blueprint(ventas_bp, url_prefix="/ventas")


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, pd.DataFrame):
        return [_clean_row(r) for r in value.to_dict("records")]
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {str(k): _clean(v) for k, v in row.items()}


def _start_job(kind: str, fn: Callable[[], Any]) -> str:
    with JOB_LOCK:
        if RUNNING["id"]:
            running = JOBS.get(RUNNING["id"], {})
            if running.get("status") == "running":
                raise RuntimeError("Ya hay un proceso en ejecución. Espere a que termine.")
        job_id = uuid.uuid4().hex[:10]
        JOBS[job_id] = {"id": job_id, "kind": kind, "status": "running",
                        "started": datetime.now().strftime("%H:%M:%S"), "result": None, "error": None}
        RUNNING["id"] = job_id

    def runner():
        try:
            result = fn()
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["result"] = _clean(result)
        except Exception as exc:  # noqa: BLE001
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(exc)
        finally:
            JOBS[job_id]["finished"] = datetime.now().strftime("%H:%M:%S")

    threading.Thread(target=runner, daemon=True).start()
    return job_id


def _config() -> Config:
    return Config(BASE_DIR / "config" / "parameters.yaml", base_dir=BASE_DIR)


def _data(start: str, end: str, as_of: str):
    logger = logging.getLogger("compras")
    connection = ProfitConnection(DB_CONFIG, logger)
    return ProfitLoader(connection, logger).load_all(start, end, as_of=as_of)


def _run_audit(year: int, as_of: str) -> dict[str, Any]:
    config = _config()
    data = _data(f"{year - 1}-01-01", f"{year + 1}-01-01", as_of)
    audit = PurchaseAudit(config).run(data, year=year)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel = OUTPUT_DIR / f"auditoria_compras_{year}_{stamp}.xlsx"
    AuditExcelGenerator(audit, excel).generate()
    dash = OUTPUT_DIR / f"auditoria_compras_{year}_{stamp}.html"
    AuditDashboardGenerator(audit, dash, excel_name=excel.name).generate()
    products = audit.products
    miss = products[products["Estado"].isin(["QUIEBRE", "CRÍTICO", "FALTANTE"])] \
        .sort_values("Venta_USD", ascending=False).head(12)
    over = products[products["Estado"] == "SOBRECOMPRA"].sort_values("Exceso_USD", ascending=False).head(12)
    dead = products[products["Estado"] == "SIN VENTA"].sort_values("Stock_Valor", ascending=False).head(10)
    result = {
        "year": year,
        "metrics": audit.metrics,
        "findings": audit.findings,
        "excel": excel.name,
        "dashboard": dash.name,
        "top_missing": miss[["EAN", "Descripcion", "Stock_Actual", "Stock_Transito",
                             "Venta_USD", "Faltante_USD", "Estado"]].to_dict("records"),
        "top_over": over[["EAN", "Descripcion", "Stock_Actual", "Stock_Transito",
                          "Cobertura_Disponible_Dias", "Exceso_USD"]].to_dict("records"),
        "top_dead": dead[["EAN", "Descripcion", "Stock_Actual", "Stock_Transito", "Stock_Valor"]].to_dict("records"),
        "suppliers": audit.suppliers.head(10)[["Proveedor", "Compra_USD", "Share_Pct", "Productos"]].to_dict("records"),
        "monthly": audit.monthly.to_dict("records"),
        "generated": datetime.now().strftime("%d/%m/%Y %H:%M"),
    }
    LAST["audit"] = _clean(result)
    return result


def _run_orders(as_of: str, service_level: float | None, lead_time: int | None) -> dict[str, Any]:
    import main as pipeline

    args = ["--source", "profit", "--as-of", as_of, "--no-open", "--no-dashboard", "--quiet"]
    if service_level:
        args += ["--service-level", str(service_level)]
    if lead_time:
        args += ["--lead-time", str(lead_time)]
    result = pipeline.run(args)
    orders = result["orders"]
    summary = result.get("order_summary", {})
    excluded = result.get("excluded", pd.DataFrame())
    price_alerts = result.get("price_alerts", pd.DataFrame())
    if not price_alerts.empty and "Bloqueada_OC" not in price_alerts.columns:
        price_alerts["Bloqueada_OC"] = False
    by_priority = []
    if not orders.empty:
        grouped = orders.groupby("Prioridad").agg(n=("EAN", "size"), u=("Cantidad_Sugerida", "sum"),
                                                  c=("Costo_Total_USD", "sum")).reset_index()
        order_map = {"CRÍTICA": 0, "ALTA": 1, "MEDIA": 2, "BAJA": 3, "REVISAR": 4}
        grouped["_o"] = grouped["Prioridad"].map(order_map).fillna(9)
        grouped = grouped.sort_values("_o").drop(columns="_o")
        by_priority = grouped.to_dict("records")
    payload = {
        "excel": Path(result["excel"]).name,
        "summary": {
            "ordenes": int(len(orders)),
            "unidades": int(orders["Cantidad_Sugerida"].sum()) if not orders.empty else 0,
            "inversion": float(orders["Costo_Total_USD"].sum()) if not orders.empty else 0.0,
            "criticas": int((orders["Prioridad"] == "CRÍTICA").sum()) if not orders.empty else 0,
            "excluidos_eol": int(summary.get("excluidos_eol", len(excluded))),
            "lle_pendientes": int(summary.get("lle_pendientes", 0)),
            "precio_bloqueados": int(summary.get("precio_bloqueados", 0)),
            "aprobaciones": int(summary.get("aprobaciones", len(price_alerts))),
        },
        "by_priority": by_priority,
        "metrics": result["metrics"],
        "top": orders.head(15)[["Prioridad", "EAN", "Descripcion", "Proveedor", "Stock_Actual",
                                "Cantidad_Sugerida", "Costo_Total_USD", "Dias_Cobertura",
                                "Estado_Ciclo_Vida", "LLE_Estado", "Precio_Estado",
                                "Aprobacion_Requerida"]].to_dict("records")
        if not orders.empty else [],
        "excluded": excluded.head(10)[["EAN", "Descripcion", "Estado_Ciclo_Vida",
                                       "Motivo_Bloqueo", "Stock_Actual", "Valor_Stock"]].to_dict("records")
        if not excluded.empty else [],
        "price_alerts": price_alerts.head(10)[["EAN", "Descripcion", "Precio_Estado",
                                              "Var_Precio_Pct", "Impacto_USD", "Aprobador",
                                              "Bloqueada_OC"]].to_dict("records")
        if not price_alerts.empty else [],
        "generated": datetime.now().strftime("%d/%m/%Y %H:%M"),
    }
    LAST["orders"] = _clean(payload)
    return payload


def _run_ml() -> dict[str, Any]:
    data = _data("2023-01-01", "2026-12-31", datetime.now().strftime("%d/%m/%Y"))
    result = run_experiment(data)
    payload = {
        "metrics": result.metrics,
        "by_volume": result.by_volume.to_dict("records"),
        "feature_importance": result.feature_importance.head(15).to_dict("records"),
        "elapsed": result.elapsed,
        "generated": datetime.now().strftime("%d/%m/%Y %H:%M"),
    }
    LAST["ml"] = _clean(payload)
    return payload


@APP.route("/")
def index():
    return render_template("index.html")


@APP.route("/api/health")
def health():
    try:
        with ProfitConnection(DB_CONFIG) as db:
            row = db.query("SELECT @@SERVERNAME AS servidor, DB_NAME() AS base, SUSER_SNAME() AS usuario").iloc[0]
        return jsonify({"ok": True, "server": str(row["servidor"]), "database": str(row["base"]),
                        "user": str(row["usuario"])})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 503


@APP.route("/api/config")
def config_info():
    cfg = _config()
    return jsonify({
        "service_level": cfg.get("inventory.service_level"),
        "lead_time": cfg.get("inventory.default_lead_time"),
        "horizon": cfg.get("forecasting.horizon_days"),
        "target_coverage": cfg.get("audit.target_coverage_days"),
        "oc_base": cfg.get("inventory.oc_base", "ACT"),
        "lle_policy": cfg.get("inventory.lle_policy", "block"),
        "price_max": cfg.get("price.max_increase_pct", 20),
        "server": cfg.get("database.server") or "HOST",
        "database": cfg.get("database.database") or "DATABASE",
    })


@APP.route("/api/warehouses", methods=["GET", "POST"])
def api_warehouses():
    try:
        config = _config()
        with ProfitConnection(DB_CONFIG) as db:
            wf = WarehouseFilter(config, db)
            if request.method == "GET":
                return jsonify({"available": wf.available_payload(), "summary": wf.selection.summary()})
            body = request.get_json(silent=True) or {}
            selected = body.get("selected") or []
            extra = body.get("purchase_extra")
            mode = body.get("purchase_mode")
            wf.save(selected, purchase_extra=extra, purchase_mode=mode)
            return jsonify({"ok": True, "summary": wf.selection.summary(),
                            "available": wf.available_payload()})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@APP.route("/api/audit", methods=["POST"])
def api_audit():
    body = request.get_json(silent=True) or {}
    year = int(body.get("year") or datetime.now().year)
    as_of = body.get("as_of") or datetime.now().strftime("%d/%m/%Y")
    try:
        job = _start_job("audit", lambda: _run_audit(year, as_of))
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"job": job}), 202


@APP.route("/api/orders", methods=["POST"])
def api_orders():
    body = request.get_json(silent=True) or {}
    as_of = body.get("as_of") or datetime.now().strftime("%d/%m/%Y")
    service = body.get("service_level")
    lead = body.get("lead_time")
    try:
        job = _start_job("orders", lambda: _run_orders(
            as_of, float(service) if service else None, int(lead) if lead else None))
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"job": job}), 202


@APP.route("/api/ml", methods=["POST"])
def api_ml():
    try:
        job = _start_job("ml", _run_ml)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"job": job}), 202


@APP.route("/api/job/<job_id>")
def api_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job no encontrado"}), 404
    return jsonify(_clean(job))


@APP.route("/api/last")
def api_last():
    return jsonify(LAST)


@APP.route("/files/<path:name>")
def files(name: str):
    return send_from_directory(OUTPUT_DIR, name, as_attachment=False)


@APP.route("/download/<path:name>")
def download(name: str):
    return send_from_directory(OUTPUT_DIR, name, as_attachment=True)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=prewarm_ventas, daemon=True).start()
    print("Interfaz de compras: http://127.0.0.1:5050")
    APP.run(host="127.0.0.1", port=5050, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
