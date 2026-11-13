"""Selección y validación de almacenes de Profit.

Profit 2K12 no tiene una columna ``activo`` en ``saAlmacen``; la tabla trae
banderas operativas (``noventa``, ``nocompra``, ``materiales``, ``produccion``,
``alm_temp``). Por eso la selección se gobierna por configuración/JSON y se
valida contra los códigos existentes.

Roles:
* ``selected``: almacenes de venta y stock (los que se reportan).
* ``purchase_extra``: almacenes de recepción que deben incluirse en compras
  para no perder el costo real (por defecto ``REC``).
"""
from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from utils.config import Config


DEFAULT_SELECTED = ["CCS", "ZUL", "CONCCS", "CONZUL"]
DEFAULT_PURCHASE_EXTRA = ["REC"]
VALID_PURCHASE_MODES = {"selected_only", "selected_plus_extra"}


@dataclass
class WarehouseSelection:
    selected: tuple[str, ...]
    purchase_extra: tuple[str, ...]
    purchase_mode: str
    stock_source: str
    available: pd.DataFrame
    source: str

    @property
    def sales_codes(self) -> tuple[str, ...]:
        return self.selected

    @property
    def stock_codes(self) -> tuple[str, ...]:
        return self.selected

    @property
    def purchase_codes(self) -> tuple[str, ...]:
        codes = list(self.selected)
        if self.purchase_mode == "selected_plus_extra":
            codes.extend(self.purchase_extra)
        return tuple(dict.fromkeys(c.strip().upper() for c in codes if c))

    def labels(self, codes: Iterable[str] | None = None) -> list[str]:
        codes = [c.upper() for c in (codes or self.selected)]
        if self.available is None or self.available.empty:
            return codes
        lookup = dict(zip(self.available["Codigo"], self.available["Nombre"]))
        return [f"{c} - {lookup[c]}" if c in lookup else c for c in codes]

    def summary(self) -> dict[str, Any]:
        return {
            "selected": list(self.selected),
            "selected_labels": self.labels(self.selected),
            "purchase_codes": list(self.purchase_codes),
            "purchase_labels": self.labels(self.purchase_codes),
            "purchase_extra": list(self.purchase_extra),
            "purchase_mode": self.purchase_mode,
            "stock_source": self.stock_source,
            "source": self.source,
        }


class WarehouseFilter:
    def __init__(self, config: Config, connection=None,
                 logger: logging.Logger | None = None):
        self.config = config
        self.connection = connection
        self.logger = logger or logging.getLogger("compras")
        self.selection_file = self._selection_path()
        self.available = self._load_available()
        self.selection = self._load_selection()

    # ------------------------------------------------------------------ fuentes
    def _selection_path(self) -> Path:
        rel = self.config.get("warehouse.selection_file", "config/warehouse_selection.json")
        path = Path(rel)
        if not path.is_absolute():
            path = self.config.base_dir / path
        return path

    def _load_available(self) -> pd.DataFrame:
        if self.connection is None:
            return pd.DataFrame(columns=["Codigo", "Nombre", "Sucursal", "Noventa",
                                         "NoCompra", "Materiales", "Produccion", "AlmTemp"])
        try:
            df = self.connection.query("""
                SELECT co_alma, des_alma, co_sucur, noventa, nocompra, materiales,
                       produccion, alm_temp
                FROM saAlmacen
                ORDER BY co_alma
            """).rename(columns={
                "co_alma": "Codigo", "des_alma": "Nombre", "co_sucur": "Sucursal",
                "noventa": "Noventa", "nocompra": "NoCompra", "materiales": "Materiales",
                "produccion": "Produccion", "alm_temp": "AlmTemp",
            })
            for col in ("Codigo", "Nombre", "Sucursal"):
                df[col] = df[col].fillna("").astype(str).str.strip()
            for col in ("Noventa", "NoCompra", "Materiales", "Produccion", "AlmTemp"):
                df[col] = df[col].fillna(False).astype(bool)
            df["Codigo"] = df["Codigo"].str.upper()
            return df
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("No se pudieron cargar almacenes de Profit: %s", exc)
            return pd.DataFrame(columns=["Codigo", "Nombre", "Sucursal", "Noventa",
                                         "NoCompra", "Materiales", "Produccion", "AlmTemp"])

    def _load_selection(self) -> WarehouseSelection:
        cfg_selected = _codes(self.config.get("warehouse.selected", []))
        cfg_extra = _codes(self.config.get("warehouse.purchase_extra", []))
        default = _codes(self.config.get("warehouse.default_when_empty", []))
        mode = str(self.config.get("warehouse.purchase_mode", "selected_plus_extra")).lower()
        stock_source = str(self.config.get("warehouse.stock_source", "saStockAlmacen"))
        allow_empty = bool(self.config.get("warehouse.allow_empty", False))
        source = "config/parameters.yaml"

        selected = cfg_selected
        extra = cfg_extra
        if self.selection_file.exists():
            try:
                data = json.loads(self.selection_file.read_text(encoding="utf-8"))
                selected = _codes(data.get("selected", selected)) or selected
                extra = _codes(data.get("purchase_extra", extra)) or extra
                mode = str(data.get("purchase_mode", mode)).lower()
                source = str(self.selection_file)
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("Selección de almacenes inválida (%s): %s", self.selection_file, exc)

        available_codes = set(self.available["Codigo"]) if not self.available.empty else set()
        if available_codes:
            valid = [c for c in selected if c in available_codes]
            invalid = [c for c in selected if c not in available_codes]
            if invalid:
                self.logger.warning("Almacenes inexistentes ignorados: %s", ", ".join(invalid))
            selected = valid
        selected = selected or default or list(DEFAULT_SELECTED)
        if available_codes:
            selected = [c for c in selected if c in available_codes]
        if not selected and not allow_empty:
            selected = [c for c in DEFAULT_SELECTED if not available_codes or c in available_codes]
            if not selected:
                raise ValueError("La selección de almacenes quedó vacía. Configure warehouse.selected.")
        if not selected and allow_empty:
            selected = ["*"]

        if mode not in VALID_PURCHASE_MODES:
            self.logger.warning("warehouse.purchase_mode inválido '%s'; se usa selected_plus_extra", mode)
            mode = "selected_plus_extra"
        extra = [c for c in extra or DEFAULT_PURCHASE_EXTRA if not available_codes or c in available_codes]
        if mode == "selected_only":
            extra = []
        if not extra and mode == "selected_plus_extra" and "REC" in available_codes:
            extra = ["REC"]
        return WarehouseSelection(
            selected=tuple(dict.fromkeys(selected)),
            purchase_extra=tuple(dict.fromkeys(extra)),
            purchase_mode=mode,
            stock_source=stock_source,
            available=self.available,
            source=source,
        )

    # ------------------------------------------------------------------ API
    def available_payload(self) -> list[dict[str, Any]]:
        if self.available.empty:
            return []
        return [
            {
                "codigo": r.Codigo, "nombre": r.Nombre, "sucursal": r.Sucursal,
                "noventa": bool(r.Noventa), "nocompra": bool(r.NoCompra),
                "materiales": bool(r.Materiales), "produccion": bool(r.Produccion),
                "alm_temp": bool(r.AlmTemp),
                "seleccionado": r.Codigo in self.selection.selected,
                "compra": r.Codigo in self.selection.purchase_codes,
            }
            for r in self.available.itertuples(index=False)
        ]

    def save(self, selected: Iterable[str], purchase_extra: Iterable[str] | None = None,
             purchase_mode: str | None = None) -> WarehouseSelection:
        codes = _codes(selected)
        available_codes = set(self.available["Codigo"]) if not self.available.empty else set()
        if available_codes:
            invalid = [c for c in codes if c not in available_codes]
            if invalid:
                raise ValueError(f"Almacenes inexistentes: {', '.join(invalid)}")
        if not codes and not bool(self.config.get("warehouse.allow_empty", False)):
            raise ValueError("Debe seleccionar al menos un almacén.")
        mode = (purchase_mode or self.selection.purchase_mode).lower()
        if mode not in VALID_PURCHASE_MODES:
            raise ValueError(f"Modo de compras inválido: {mode}")
        extra = _codes(purchase_extra) if purchase_extra is not None else list(self.selection.purchase_extra)
        if available_codes:
            extra = [c for c in extra if c in available_codes]
        payload = {
            "selected": codes,
            "purchase_extra": extra,
            "purchase_mode": mode,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.selection_file.parent.mkdir(parents=True, exist_ok=True)
        self.selection_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.selection = self._load_selection()
        self.logger.info("Selección de almacenes guardada: %s", ", ".join(codes))
        return self.selection

    def with_selection(self, selected: Iterable[str] | None = None,
                       purchase_extra: Iterable[str] | None = None,
                       purchase_mode: str | None = None) -> "WarehouseFilter":
        """Copia el filtro con otra selección (para endpoints con ?almacen=...)."""
        clone = copy.copy(self)
        codes = _codes(selected) if selected else list(self.selection.selected)
        available_codes = set(self.available["Codigo"]) if not self.available.empty else set()
        if available_codes:
            codes = [c for c in codes if c in available_codes]
        if not codes:
            codes = list(self.selection.selected)
        extra = _codes(purchase_extra) if purchase_extra is not None else list(self.selection.purchase_extra)
        mode = (purchase_mode or self.selection.purchase_mode).lower()
        if mode not in VALID_PURCHASE_MODES:
            mode = self.selection.purchase_mode
        clone.selection = WarehouseSelection(
            selected=tuple(dict.fromkeys(codes)),
            purchase_extra=tuple(dict.fromkeys(extra)),
            purchase_mode=mode,
            stock_source=self.selection.stock_source,
            available=self.available,
            source="request",
        )
        return clone


def _codes(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return list(dict.fromkeys(str(v).strip().upper() for v in value if str(v).strip()))


def sql_placeholders(codes: Iterable[str]) -> str:
    codes = list(codes)
    return ",".join("?" for _ in codes) if codes else "NULL"
