"""Configuración central del sistema.

Fuentes, en orden de prioridad (la última gana):
1. ``config/parameters.yaml``    -> parámetros generales.
2. ``config/parametros.xlsx``    -> hoja PARAMETROS (clave/valor) editable por el usuario.
3. ``config/proveedores.csv``    -> lead times, mínimos, múltiplos y descuentos.
4. ``config/productos.csv``      -> overrides por producto (opcional).
"""
from __future__ import annotations

import csv
import copy
import math
from dataclasses import dataclass, field
from pathlib import Path
from statistics import NormalDist
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Provider:
    """Parámetros logísticos y comerciales de un proveedor."""

    nombre: str
    lead_time: int = 30
    revision: int = 7
    minimo: int = 24
    multiplo: int = 24
    descuentos: list[tuple[float, float]] = field(default_factory=list)
    activo: bool = True
    notas: str = ""

    def descuento_pct(self, cantidad: float) -> float:
        """Descuento aplicable para una cantidad (el tramo más alto que cumpla)."""
        aplicable = 0.0
        for desde, pct in sorted(self.descuentos):
            if cantidad >= desde:
                aplicable = pct
        return aplicable


def _to_int(value: Any, default: int) -> int:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float) -> float:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().upper() in {"SI", "SÍ", "S", "YES", "Y", "TRUE", "1", "X"}


class Config:
    """Contenedor de configuración con acceso por ruta ``seccion.clave``."""

    def __init__(self, path: str | Path = "config/parameters.yaml", base_dir: str | Path | None = None):
        self.base_dir = Path(base_dir) if base_dir else PROJECT_ROOT
        self.path = Path(path)
        if not self.path.is_absolute():
            self.path = self.base_dir / self.path
        self.data: dict[str, Any] = {}
        self.providers: dict[str, Provider] = {}
        self.product_overrides: dict[str, dict[str, Any]] = {}
        self.sources: list[str] = []
        self.load()

    # ------------------------------------------------------------------ carga
    def load(self) -> "Config":
        self.data = self._load_yaml()
        self.sources = [str(self.path)]
        self._load_param_workbook()
        self._load_providers()
        self._load_product_overrides()
        return self

    def _load_yaml(self) -> dict[str, Any]:
        if not self.path.exists():
            raise FileNotFoundError(f"No se encontró el archivo de configuración: {self.path}")
        with open(self.path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ValueError("parameters.yaml debe contener un diccionario en la raíz")
        return data

    def _load_param_workbook(self) -> None:
        """Carga claves/valores editados en Excel (si el archivo existe)."""
        rel = self.get("data.params_file", "config/parametros.xlsx")
        path = Path(rel)
        if not path.is_absolute():
            path = self.base_dir / path
        if not path.exists():
            return
        try:
            import pandas as pd

            sheets = pd.read_excel(path, sheet_name=None)
        except Exception:
            return
        params = sheets.get("PARAMETROS") or sheets.get("PARÁMETROS")
        if params is not None and not params.empty:
            cols = {str(c).strip().lower(): c for c in params.columns}
            key_col = cols.get("parametro") or cols.get("parámetro") or params.columns[0]
            val_col = cols.get("valor")
            if val_col is None and len(params.columns) > 1:
                val_col = params.columns[1]
            if val_col is not None:
                for _, row in params.iterrows():
                    key = str(row.get(key_col, "")).strip()
                    if not key or key.startswith("#"):
                        continue
                    self.set(key, row.get(val_col))
        self.sources.append(str(path))

    def _load_providers(self) -> None:
        rel = self.get("data.provider_file", "config/proveedores.csv")
        path = Path(rel)
        if not path.is_absolute():
            path = self.base_dir / path
        self.providers = {}
        if path.exists():
            with open(path, "r", encoding="utf-8-sig", newline="") as fh:
                for row in csv.DictReader(fh):
                    name = (row.get("Proveedor") or "").strip()
                    if not name:
                        continue
                    descuentos = []
                    for idx in (1, 2):
                        pct = _to_float(row.get(f"Descuento_{idx}_Pct"), 0.0)
                        desde = _to_float(row.get(f"Descuento_{idx}_Desde"), 0.0)
                        if pct > 0 and desde > 0:
                            descuentos.append((desde, pct))
                    self.providers[name.upper()] = Provider(
                        nombre=name,
                        lead_time=_to_int(row.get("Lead_Time_Dias"), 30),
                        revision=_to_int(row.get("Dias_Revision"), 7),
                        minimo=_to_int(row.get("Minimo_Pedido"), 24),
                        multiplo=_to_int(row.get("Multiplo_Compra"), 24),
                        descuentos=descuentos,
                        activo=_to_bool(row.get("Activo"), True),
                        notas=(row.get("Notas") or "").strip(),
                    )
            self.sources.append(str(path))
        # Garantizar un proveedor por defecto
        default = self.providers.get("DEFAULT")
        if default is None:
            default = Provider(
                nombre="DEFAULT",
                lead_time=int(self.get("inventory.default_lead_time", 30)),
                revision=int(self.get("inventory.default_review_period", 7)),
                minimo=int(self.get("inventory.min_order_quantity", 24)),
                multiplo=int(self.get("inventory.default_multiplier", 24)),
            )
            self.providers["DEFAULT"] = default
        if "SIN ASIGNAR" not in self.providers:
            self.providers["SIN ASIGNAR"] = Provider(nombre="SIN ASIGNAR",
                                                    lead_time=default.lead_time,
                                                    revision=default.revision,
                                                    minimo=default.minimo,
                                                    multiplo=default.multiplo)

    def _load_product_overrides(self) -> None:
        rel = self.get("data.product_file", "config/productos.csv")
        path = Path(rel)
        if not path.is_absolute():
            path = self.base_dir / path
        self.product_overrides = {}
        if not path.exists():
            return
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                ean = str(row.get("EAN") or "").strip()
                if not ean:
                    continue
                clean = {}
                for key, value in row.items():
                    if value in (None, ""):
                        continue
                    clean[key.strip()] = value
                self.product_overrides[ean] = clean
        self.sources.append(str(path))

    # ------------------------------------------------------------------ acceso
    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, path: str, value: Any) -> None:
        node = self.data
        parts = path.split(".")
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value

    def section(self, name: str) -> dict[str, Any]:
        value = self.get(name, {})
        return value if isinstance(value, dict) else {}

    def provider(self, nombre: str | None) -> Provider:
        key = (nombre or "").strip().upper()
        if key in self.providers:
            return self.providers[key]
        default = self.providers.get("DEFAULT") or Provider(nombre="DEFAULT")
        return Provider(
            nombre=nombre or "SIN ASIGNAR",
            lead_time=default.lead_time,
            revision=default.revision,
            minimo=default.minimo,
            multiplo=default.multiplo,
            descuentos=list(default.descuentos),
            activo=True,
        )

    def product(self, ean: str) -> dict[str, Any]:
        return self.product_overrides.get(str(ean), {})

    # ------------------------------------------------------------------ utilidades
    def ensure_provider_file(self, suppliers: list[str]) -> list[str]:
        """Agrega al CSV los proveedores nuevos encontrados en el catálogo.

        No sobreescribe filas existentes: el usuario manda. Devuelve los agregados.
        """
        rel = self.get("data.provider_file", "config/proveedores.csv")
        path = Path(rel)
        if not path.is_absolute():
            path = self.base_dir / path
        existing = {p.upper() for p in self.providers}
        nuevos = [s for s in suppliers if s and s.strip().upper() not in existing]
        if not nuevos:
            return []
        path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not path.exists()
        with open(path, "a", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            if is_new:
                writer.writerow(["Proveedor", "Lead_Time_Dias", "Dias_Revision", "Minimo_Pedido",
                                 "Multiplo_Compra", "Descuento_1_Pct", "Descuento_1_Desde",
                                 "Descuento_2_Pct", "Descuento_2_Desde", "Activo", "Notas"])
            default = self.providers.get("DEFAULT") or Provider(nombre="DEFAULT")
            for supplier in nuevos:
                writer.writerow([supplier, default.lead_time, default.revision, default.minimo,
                                 default.multiplo, 0, 0, 0, 0, "SI", "Agregado automaticamente"])
        self._load_providers()
        return nuevos

    def z_value(self) -> float:
        """Valor z de la normal estándar para el nivel de servicio configurado."""
        level = _to_float(self.get("inventory.service_level", 0.95), 0.95)
        level = min(max(level, 0.50), 0.999)
        return round(NormalDist().inv_cdf(level), 3)

    def summary(self) -> dict[str, Any]:
        """Parámetros relevantes para la hoja PARÁMETROS del reporte."""
        keys = [
            ("forecasting.horizon_days", "Horizonte de pronóstico (días)"),
            ("forecasting.review_period_days", "Periodo de revisión (días)"),
            ("forecasting.backtest_months", "Meses de backtesting"),
            ("forecasting.min_train_weeks", "Semanas mínimas de entrenamiento"),
            ("analysis.window_days", "Ventana de análisis ABC/XYZ (días)"),
            ("analysis.active_days", "Días para considerar producto activo"),
            ("analysis.inactive_days", "Días sin venta para considerar inactivo"),
            ("inventory.default_lead_time", "Lead time por defecto (días)"),
            ("inventory.default_multiplier", "Múltiplo de compra por defecto"),
            ("inventory.min_order_quantity", "Pedido mínimo por defecto (unidades)"),
            ("inventory.service_level", "Nivel de servicio"),
            ("inventory.safety_buffer_days", "Buffer de seguridad (días)"),
            ("inventory.max_safety_days", "Tope del stock de seguridad (días)"),
            ("inventory.max_coverage_days", "Cobertura máxima permitida (días)"),
            ("inventory.oc_base", "Base de cálculo de la OC"),
            ("inventory.use_committed_in_oc", "Descontar comprometido en la OC"),
            ("inventory.lle_policy", "Política de stock por llegar"),
            ("inventory.lle_max_age_days", "Edad máxima del tránsito (días)"),
            ("inventory.min_margin_pct", "Margen mínimo aceptable (%)"),
            ("lifecycle.observation_days", "Días sin venta para observación"),
            ("lifecycle.obsolete_days", "Días sin venta para obsoleto"),
            ("lifecycle.liquidation_days", "Días para vencer para liquidación"),
            ("price.warning_pct", "Variación de precio: alerta amarilla (%)"),
            ("price.approval_pct", "Variación de precio: aprobación naranja (%)"),
            ("price.max_increase_pct", "Variación de precio: techo rojo (%)"),
            ("price.impact_threshold_usd", "Impacto anual que escala aprobación (USD)"),
            ("inventory.default_margin_pct", "Margen usado para estimar costos (%)"),
            ("inventory.unknown_stock_policy", "Política si no hay stock informado"),
            ("inventory.min_forecast_30d", "Demanda mínima a 30 días para producto C"),
            ("warehouse.selected", "Almacenes de venta/stock"),
            ("warehouse.purchase_extra", "Almacenes extra para compras"),
            ("warehouse.purchase_mode", "Modo de filtro de compras"),
            ("warehouse.stock_source", "Fuente de stock por almacén"),
            ("alerts.critical_stock_days", "Días para alerta de stock crítico"),
            ("alerts.low_stock_days", "Días para alerta de stock bajo"),
            ("alerts.excess_stock_days", "Días para alerta de exceso"),
            ("alerts.trend_change_pct", "Umbral de cambio de tendencia (%)"),
            ("alerts.big_purchase_usd", "Umbral de compra grande (USD)"),
        ]
        rows = []
        for key, label in keys:
            rows.append({"Parametro": key, "Descripcion": label, "Valor": self.get(key)})
        return {"parameters": rows, "z_value": self.z_value()}

    def copy(self) -> "Config":
        clone = Config.__new__(Config)
        clone.base_dir = self.base_dir
        clone.path = self.path
        clone.data = copy.deepcopy(self.data)
        clone.providers = copy.deepcopy(self.providers)
        clone.product_overrides = copy.deepcopy(self.product_overrides)
        clone.sources = list(self.sources)
        return clone
