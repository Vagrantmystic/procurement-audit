"""Utilidades comunes: normalización de columnas, EAN, números y redondeos."""
from __future__ import annotations

import math
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- texto
def strip_accents(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(value)) if not unicodedata.combining(c))


def normalize_key(value: Any) -> str:
    """'Código Artículo' -> 'codigo_articulo'."""
    text = strip_accents(str(value)).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def rename_columns(df: pd.DataFrame, aliases: dict[str, Iterable[str]]) -> pd.DataFrame:
    """Renombra columnas usando alias insensibles a mayúsculas/acentos/espacios."""
    lookup: dict[str, str] = {}
    for canonical, names in aliases.items():
        lookup[normalize_key(canonical)] = canonical
        for name in names:
            lookup[normalize_key(name)] = canonical
    mapping = {}
    used: set[str] = set()
    for original in df.columns:
        key = normalize_key(original)
        canonical = lookup.get(key)
        if canonical and canonical not in used:
            mapping[original] = canonical
            used.add(canonical)
        else:
            mapping[original] = str(original).strip()
    return df.rename(columns=mapping)


# --------------------------------------------------------------------------- EAN
def to_ean(series: pd.Series) -> pd.Series:
    """Convierte códigos de barras a texto estable (sin '.0' ni notación científica)."""

    def _one(value: Any) -> str:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return ""
        if isinstance(value, (int, np.integer)):
            return str(int(value))
        if isinstance(value, (float, np.floating)):
            return str(int(round(float(value))))
        text = str(value).strip()
        if not text or text.lower() in {"nan", "none"}:
            return ""
        text = re.sub(r"\.0+$", "", text)
        text = text.replace(" ", "")
        # Notación científica generada por Excel: 7.59102E+12
        if re.fullmatch(r"[0-9.]+[eE]\+?[0-9]+", text):
            try:
                return str(int(float(text)))
            except ValueError:
                return text
        return text

    return series.map(_one).astype(str)


# --------------------------------------------------------------------------- números
def to_number(series: pd.Series) -> pd.Series:
    """Convierte texto numérico tolerando símbolos, separadores y comas decimales."""

    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")

    def _one(value: Any) -> float:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return np.nan
        if isinstance(value, (int, float, np.integer, np.floating)):
            return float(value)
        text = str(value).strip()
        if not text or text.lower() in {"nan", "none", "-"}:
            return np.nan
        negative = text.startswith("(") and text.endswith(")")
        text = text.replace("$", "").replace("%", "").replace(" ", "").replace("\u00a0", "")
        text = text.replace("(", "").replace(")", "")
        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        elif "," in text:
            parts = text.split(",")
            if len(parts) == 2 and len(parts[1]) in (1, 2):
                text = text.replace(",", ".")
            else:
                text = text.replace(",", "")
        try:
            value_out = float(text)
        except ValueError:
            return np.nan
        return -value_out if negative else value_out

    return series.map(_one).astype(float)


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    try:
        if denominator in (0, None) or (isinstance(denominator, float) and math.isnan(denominator)):
            return default
        return numerator / denominator
    except (TypeError, ZeroDivisionError):
        return default


# --------------------------------------------------------------------------- cantidades
def round_to_multiple(quantity: float, multiple: float, mode: str = "up") -> int:
    """Redondea una cantidad al múltiplo comercial indicado.

    mode='up'  -> nunca pedir menos de lo necesario (recomendado para disponibilidad)
    mode='near'-> múltiplo más cercano
    mode='down'-> múltiplo inferior (para respetar topes de cobertura)
    """
    if quantity is None or (isinstance(quantity, float) and math.isnan(quantity)):
        return 0
    multiple = int(multiple) if multiple and multiple > 0 else 1
    if quantity <= 0:
        return 0
    if mode == "near":
        return int(multiple * max(1, round(quantity / multiple)))
    if mode == "down":
        return int(multiple * math.floor(quantity / multiple))
    return int(multiple * max(1, math.ceil(quantity / multiple)))


def percent_change(actual: float, previous: float) -> float | None:
    if previous is None or previous == 0 or (isinstance(previous, float) and math.isnan(previous)):
        return None
    return (actual - previous) / previous * 100.0


# --------------------------------------------------------------------------- fechas
def ensure_datetime(series: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    try:
        return pd.to_datetime(series, errors="coerce", dayfirst=True)
    except (TypeError, ValueError):
        return pd.to_datetime(series, errors="coerce")


def week_end(dates: pd.Series | pd.DatetimeIndex) -> pd.Series:
    """Etiqueta de semana terminada en domingo."""
    return dates.dt.to_period("W-SUN")


def as_date(value: Any) -> pd.Timestamp:
    return pd.Timestamp(value).normalize()


# --------------------------------------------------------------------------- archivos
def ensure_dir(path: str | Path) -> Path:
    out = Path(path)
    out.mkdir(parents=True, exist_ok=True)
    return out


def find_input_file(base_dir: Path, configured: str | None, patterns: list[str]) -> Path | None:
    """Busca un archivo configurado o el primero que coincida con los patrones."""
    if configured:
        candidate = Path(configured)
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        if candidate.exists():
            return candidate
    for pattern in patterns:
        matches = sorted(base_dir.glob(pattern))
        if matches:
            return matches[0]
    return None


def df_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """DataFrame -> registros con NaN convertido a None (para openpyxl)."""
    if df is None or df.empty:
        return []
    clean = df.replace({np.nan: None})
    return clean.to_dict("records")


def fmt_units(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"{value:,.0f}".replace(",", ".")


def fmt_money(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"${value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
