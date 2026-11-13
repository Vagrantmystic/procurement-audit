"""Blueprint del módulo de ventas (BI interno)."""
from __future__ import annotations

import math
import re
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from flask import Blueprint, jsonify, render_template, request, send_file

from analytics.ventas_analytics import VentasAnalytics
from data_processing.profit_connection import ProfitConnection
from data_processing.warehouse_filter import WarehouseFilter
from reporting.ventas_export import build_detalle_cliente_laboratorio, build_ventas_excel
from reporting.ventas_pdf import render_pdf
from utils.config import Config

BASE_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = BASE_DIR / "config" / "parameters.yaml"
DB_CONFIG = BASE_DIR / "config" / "database.local.yaml"

bp = Blueprint("ventas", __name__)


@contextmanager
def _context(warehouses: Iterable[str] | None = None):
    config = Config(CONFIG_PATH, base_dir=BASE_DIR)
    connection = ProfitConnection(DB_CONFIG)
    try:
        wf = WarehouseFilter(config, connection)
        if warehouses:
            wf = wf.with_selection(warehouses)
        yield config, connection, VentasAnalytics(config, connection, wf)
    finally:
        connection.close()


def _params() -> tuple[str, str, list[str], dict[str, Any]]:
    today = date.today()
    default_days = int(Config(CONFIG_PATH, base_dir=BASE_DIR).get("ventas.default_days", 90))
    start_raw = request.args.get("desde") or (today - timedelta(days=default_days - 1)).isoformat()
    end_raw = request.args.get("hasta") or today.isoformat()
    try:
        start = datetime.strptime(start_raw, "%Y-%m-%d").date()
        end = datetime.strptime(end_raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Fechas inválidas; use YYYY-MM-DD.") from exc
    if start > end:
        raise ValueError("La fecha inicial no puede ser mayor que la final.")
    max_days = int(Config(CONFIG_PATH, base_dir=BASE_DIR).get("ventas.max_days", 366))
    if (end - start).days + 1 > max_days:
        raise ValueError(f"El rango máximo es {max_days} días.")
    warehouses = [c.strip().upper() for c in (request.args.get("almacen") or "").split(",") if c.strip()]
    filters = {
        "laboratorio": (request.args.get("laboratorio") or "").strip() or None,
        "linea_terapeutica": (request.args.get("linea") or "").strip() or None,
        "categoria": (request.args.get("categoria") or "").strip() or None,
        "sublinea": (request.args.get("sublinea") or "").strip() or None,
    }
    return start.isoformat(), end.isoformat(), warehouses, filters


def _validate_warehouses(analytics: VentasAnalytics, codes: list[str]) -> None:
    if not codes:
        return
    available = set(analytics.base_filter.available["Codigo"]) if not analytics.base_filter.available.empty else set()
    if available:
        invalid = [c for c in codes if c not in available]
        if invalid:
            raise ValueError(f"Almacenes inválidos: {', '.join(invalid)}")


def _safe(value: Any) -> Any:
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
        return value.strftime("%Y-%m-%d")
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, pd.DataFrame):
        return [_safe(r) for r in value.to_dict("records")]
    return value


def _ok(payload: Any):
    return jsonify(_safe(payload))


def _money(value: Any) -> str:
    try:
        return f"${float(value):,.0f}"
    except (TypeError, ValueError):
        return "—"


def _pct(value: Any) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "—"


def _int(value: Any) -> str:
    try:
        return f"{int(float(value)):,}"
    except (TypeError, ValueError):
        return "—"


def _charts_payload(raw: Any) -> list[dict[str, str]]:
    """Acepta solo imágenes PNG/JPEG Base64 y limita el tamaño."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw[:8]:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "Gráfico")[:80]
        data = str(item.get("data") or "")
        if not re.match(r"^data:image/(png|jpeg|jpg);base64,[A-Za-z0-9+/=\s]+$", data):
            continue
        if len(data) > 8_000_000:
            continue
        out.append({"label": label, "data": data})
    return out


def _pdf_context(analytics: VentasAnalytics, start: str, end: str,
                 warehouses: list[str], filters: dict[str, Any]) -> dict[str, Any]:
    resumen = analytics.resumen(start, end, warehouses, **filters)
    labs = analytics.laboratorios(start, end, warehouses, limit=10, **filters).get("items", [])
    lineas = analytics.lineas_terapeuticas(start, end, warehouses, limit=10, **filters).get("items", [])
    df = analytics.export_frame(start, end, warehouses, **filters)
    detalle = build_detalle_cliente_laboratorio(df)
    top_clients = (detalle.groupby(["CodCliente", "Cliente"], as_index=False)["Venta_USD"].sum()
                   .sort_values("Venta_USD", ascending=False).head(30))
    clientes = []
    for _, row in top_clients.iterrows():
        part = detalle[(detalle["CodCliente"] == row["CodCliente"]) &
                       (detalle["Cliente"] == row["Cliente"])].sort_values("Venta_USD", ascending=False)
        clientes.append({
            "codigo": row["CodCliente"],
            "cliente": row["Cliente"],
            "total": _money(row["Venta_USD"]),
            "labs": [{
                "Laboratorio": r["Laboratorio"],
                "Venta_USD": _money(r["Venta_USD"]),
                "Margen_USD": _money(r["Margen_USD"]),
                "Margen_Pct": _pct(r["Margen_Pct"]),
                "Unidades": _int(r["Unidades"]),
                "Facturas": _int(r["Facturas"]),
                "Ultima_Compra": r["Ultima_Compra"] or "—",
            } for _, r in part.iterrows()],
        })
    k = resumen.get("kpis", {})
    cov = resumen.get("cobertura_laboratorio", {})
    return {
        "titulo": f"últimos {(pd.Timestamp(end) - pd.Timestamp(start)).days + 1} días",
        "desde": start,
        "hasta": end,
        "dias": (pd.Timestamp(end) - pd.Timestamp(start)).days + 1,
        "almacenes": ", ".join(warehouses) if warehouses else "Selección configurada",
        "generado": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "filtros": " · ".join(f"{k}: {v}" for k, v in filters.items() if v) or "Sin filtros adicionales",
        "kpis": {
            "venta_usd": _money(k.get("venta_usd")),
            "venta_bs": _money(k.get("venta_bs")),
            "margen_pct": _pct(k.get("margen_pct")),
            "ticket_promedio": _money(k.get("ticket_promedio")),
            "clientes": _int(k.get("clientes")),
            "sku": _int(k.get("sku")),
            "top_laboratorio": (resumen.get("top_laboratorio") or {}).get("nombre", "—"),
            "cobertura_laboratorio": _pct(cov.get("venta_pct")),
        },
        "laboratorios": [{
            "Laboratorio": r["Laboratorio"], "Venta_USD": _money(r["Venta_USD"]),
            "Margen_Pct": _pct(r["Margen_Pct"]), "Unidades": _int(r["Unidades"]),
            "Clientes": _int(r["Clientes"]),
        } for r in labs],
        "lineas": [{
            "Linea_Terapeutica": r["Linea_Terapeutica"], "Venta_USD": _money(r["Venta_USD"]),
            "Margen_Pct": _pct(r["Margen_Pct"]), "Unidades": _int(r["Unidades"]),
        } for r in lineas],
        "clientes": clientes,
    }


def _cliente_pdf_context(data: dict[str, Any], start: str, end: str,
                         warehouses: list[str], filters: dict[str, Any],
                         charts: list[dict[str, str]]) -> dict[str, Any]:
    f = data.get("cliente") or {}
    labs = [{
        "Laboratorio": r.get("Laboratorio") or "SIN_LABORATORIO",
        "Venta_USD": _money(r.get("Venta_USD")),
        "Margen_Pct": _pct(r.get("Margen_Pct")),
        "Unidades": _int(r.get("Unidades")),
        "Facturas": _int(r.get("Facturas")),
        "Ultima_Venta": r.get("Ultima_Venta") or "—",
    } for r in data.get("items", [])]
    productos = [{
        "Descripcion": r.get("Descripcion", ""),
        "Laboratorio": r.get("Laboratorio") or "SIN_LABORATORIO",
        "Linea_Terapeutica": r.get("Linea_Terapeutica") or "SIN LÍNEA",
        "Venta_USD": _money(r.get("Venta_USD")),
        "Unidades": _int(r.get("Unidades")),
    } for r in data.get("productos", [])]
    return {
        "codigo": f.get("codigo", ""),
        "cliente": f.get("cliente", ""),
        "ultima_compra": f.get("ultima_compra", "—"),
        "facturas": _int(f.get("facturas")),
        "venta_usd": _money(f.get("venta_usd")),
        "venta_bs": _money(f.get("venta_bs")),
        "margen_pct": _pct(f.get("margen_pct")),
        "margen_usd": _money(f.get("margen_usd")),
        "ticket_promedio": _money(f.get("ticket_promedio")),
        "unidades": _int(f.get("unidades")),
        "desde": start,
        "hasta": end,
        "almacenes": ", ".join(warehouses) if warehouses else "Selección configurada",
        "generado": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "filtros": " · ".join(f"{k}: {v}" for k, v in filters.items() if v) or "Sin filtros adicionales",
        "chart": charts[0]["data"] if charts else None,
        "labs": labs,
        "productos": productos,
    }


def prewarm() -> None:
    """Precarga el período por defecto para que la primera visita sea rápida."""
    try:
        config = Config(CONFIG_PATH, base_dir=BASE_DIR)
        days = int(config.get("ventas.default_days", 90))
        end = date.today()
        start = end - timedelta(days=days - 1)
        with _context() as (_, _, analytics):
            analytics.resumen(start.isoformat(), end.isoformat())
    except Exception:  # noqa: BLE001
        pass


@bp.route("/", strict_slashes=False)
def home():
    return render_template("ventas/resumen_90d.html", vista="resumen", cliente="")


@bp.route("/laboratorios")
def por_laboratorio():
    return render_template("ventas/por_laboratorio.html", vista="laboratorios", cliente="")


@bp.route("/productos")
def por_producto():
    return render_template("ventas/por_producto.html", vista="productos", cliente="")


@bp.route("/clientes")
def por_cliente():
    return render_template("ventas/por_cliente.html", vista="clientes", cliente="")


@bp.route("/cliente/<codigo>")
def drilldown_cliente(codigo: str):
    return render_template("ventas/drilldown_cliente.html", vista="drilldown", cliente=codigo)


@bp.route("/api/almacenes")
def api_almacenes():
    try:
        config = Config(CONFIG_PATH, base_dir=BASE_DIR)
        connection = ProfitConnection(DB_CONFIG)
        try:
            wf = WarehouseFilter(config, connection)
            return _ok({"available": wf.available_payload(), "summary": wf.selection.summary()})
        finally:
            connection.close()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/resumen")
def api_resumen():
    try:
        start, end, warehouses, filters = _params()
        with _context(warehouses) as (_, _, analytics):
            _validate_warehouses(analytics, warehouses)
            return _ok(analytics.resumen(start, end, warehouses, **filters))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/laboratorios")
def api_laboratorios():
    try:
        start, end, warehouses, filters = _params()
        limit = min(max(int(request.args.get("limit", 30)), 1), 200)
        with _context(warehouses) as (_, _, analytics):
            _validate_warehouses(analytics, warehouses)
            return _ok(analytics.laboratorios(start, end, warehouses, limit=limit, **filters))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/lineas")
def api_lineas():
    try:
        start, end, warehouses, filters = _params()
        limit = min(max(int(request.args.get("limit", 20)), 1), 100)
        with _context(warehouses) as (_, _, analytics):
            _validate_warehouses(analytics, warehouses)
            return _ok(analytics.lineas_terapeuticas(start, end, warehouses, limit=limit, **filters))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/productos")
def api_productos():
    try:
        start, end, warehouses, filters = _params()
        limit = min(max(int(request.args.get("limit", 50)), 1), 200)
        offset = max(int(request.args.get("offset", 0)), 0)
        with _context(warehouses) as (_, _, analytics):
            _validate_warehouses(analytics, warehouses)
            return _ok(analytics.productos(start, end, warehouses, limit=limit, offset=offset, **filters))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/clientes")
def api_clientes():
    try:
        start, end, warehouses, filters = _params()
        limit = min(max(int(request.args.get("limit", 50)), 1), 200)
        offset = max(int(request.args.get("offset", 0)), 0)
        with _context(warehouses) as (_, _, analytics):
            _validate_warehouses(analytics, warehouses)
            return _ok(analytics.clientes(start, end, warehouses, limit=limit, offset=offset, **filters))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/cliente/<codigo>")
def api_cliente(codigo: str):
    try:
        start, end, warehouses, _ = _params()
        with _context(warehouses) as (_, _, analytics):
            _validate_warehouses(analytics, warehouses)
            return _ok(analytics.cliente(codigo, start, end, warehouses))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/cliente/<codigo>/pdf", methods=["POST"])
def api_cliente_pdf(codigo: str):
    try:
        start, end, warehouses, filters = _params()
        body = request.get_json(silent=True) or {}
        charts = _charts_payload(body.get("charts"))
        with _context(warehouses) as (_, _, analytics):
            _validate_warehouses(analytics, warehouses)
            data = analytics.cliente(codigo, start, end, warehouses, product_limit=None)
            if not data.get("cliente"):
                return jsonify({"error": "Cliente sin ventas en el período"}), 404
            context = _cliente_pdf_context(data, start, end, warehouses, filters, charts)
            html = render_template("ventas/export_cliente_pdf.html", **context)
            pdf = render_pdf(html, BASE_DIR)
        return send_file(BytesIO(pdf), as_attachment=True,
                         download_name=f"cliente_{codigo}_{start}_{end}.pdf",
                         mimetype="application/pdf")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/filtros")
def api_filtros():
    try:
        start, end, warehouses, _ = _params()
        with _context(warehouses) as (_, _, analytics):
            _validate_warehouses(analytics, warehouses)
            return _ok(analytics.filtros(start, end, warehouses))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/export")
def api_export():
    try:
        start, end, warehouses, filters = _params()
        with _context(warehouses) as (_, _, analytics):
            _validate_warehouses(analytics, warehouses)
            payload = build_ventas_excel(analytics, start, end, warehouses, filters)
        return send_file(BytesIO(payload), as_attachment=True,
                         download_name=f"ventas_{start}_{end}.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/export/pdf", methods=["POST"])
def api_export_pdf():
    try:
        start, end, warehouses, filters = _params()
        body = request.get_json(silent=True) or {}
        charts = _charts_payload(body.get("charts"))
        with _context(warehouses) as (_, _, analytics):
            _validate_warehouses(analytics, warehouses)
            context = _pdf_context(analytics, start, end, warehouses, filters)
            context["charts"] = charts
            html = render_template("ventas/export_pdf.html", **context)
            pdf = render_pdf(html, BASE_DIR)
        return send_file(BytesIO(pdf), as_attachment=True,
                         download_name=f"ventas_{start}_{end}.pdf",
                         mimetype="application/pdf")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
