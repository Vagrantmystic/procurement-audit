"""Dimensión maestra de laboratorio real.

Profit no expone el laboratorio de forma consistente en ``saLinea``: esa tabla
es la línea terapéutica. La fuente canónica es
``docs/PRODUCTOS POR LABORATORIO.md`` (código, laboratorio/proveedor, descripción).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = PROJECT_ROOT / "docs" / "PRODUCTOS POR LABORATORIO.md"
CACHE_DIR = PROJECT_ROOT / "outputs"
REQUIRED = {"CODIGO", "LABORATORIO", "DESCRIPCION"}


def normalize_code(value: Any) -> str:
    """Normaliza un código a dígitos, conservando ceros iniciales."""
    text = str(value if value is not None else "").strip()
    if text.lower() in {"", "nan", "none", "nat"}:
        return ""
    text = re.sub(r"\.0+$", "", text)
    return re.sub(r"\D", "", text)


def _read_table(path: Path) -> pd.DataFrame:
    raw = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    if not raw:
        return pd.DataFrame(columns=["CODIGO", "LABORATORIO", "DESCRIPCION"])
    header = raw[0]
    if "\t" in header:
        df = pd.read_csv(path, sep="\t", dtype=str, on_bad_lines="skip")
    elif "|" in header:
        rows = []
        for line in raw:
            if "|" not in line or set(line.replace("|", "").strip()) <= {"-", ":", " "}:
                continue
            rows.append([c.strip() for c in line.strip().strip("|").split("|")])
        if not rows:
            return pd.DataFrame(columns=["CODIGO", "LABORATORIO", "DESCRIPCION"])
        df = pd.DataFrame(rows[1:], columns=rows[0])
    else:
        # Fallback: dos o más columnas separadas por espacios/tabulaciones.
        df = pd.read_csv(path, sep=r"\t+", dtype=str, engine="python", on_bad_lines="skip")
    df.columns = [str(c).strip().upper() for c in df.columns]
    if "PROVEEDOR" in df.columns and "LABORATORIO" not in df.columns:
        df = df.rename(columns={"PROVEEDOR": "LABORATORIO"})
    return df


def load_laboratorio_dim(path: str | Path | None = None,
                         use_cache: bool = True,
                         logger: logging.Logger | None = None) -> pd.DataFrame:
    """Carga y deduplica la dimensión de laboratorio.

    Devuelve columnas ``Codigo``, ``Laboratorio``, ``Descripcion`` y ``Fuente``.
    """
    logger = logger or logging.getLogger("compras")
    path = Path(path) if path else DEFAULT_PATH
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        logger.warning("No existe la dimensión de laboratorio: %s", path)
        return pd.DataFrame(columns=["Codigo", "Laboratorio", "Descripcion", "Fuente"])

    cache = CACHE_DIR / "dim_laboratorio.pkl"
    parquet = CACHE_DIR / "dim_laboratorio.parquet"
    mtime = path.stat().st_mtime
    if use_cache and parquet.exists() and parquet.stat().st_mtime >= mtime:
        try:
            cached = pd.read_parquet(parquet)
            cached.attrs["cache"] = "hit"
            return cached
        except Exception:
            pass
    if use_cache and cache.exists() and cache.stat().st_mtime >= mtime:
        try:
            cached = pd.read_pickle(cache)
            cached.attrs["cache"] = "hit"
            return cached
        except Exception:
            pass

    df = _read_table(path)
    if df.empty:
        return pd.DataFrame(columns=["Codigo", "Laboratorio", "Descripcion", "Fuente"])
    for col in ("CODIGO", "LABORATORIO", "DESCRIPCION"):
        if col not in df.columns:
            df[col] = ""
    df["Codigo"] = df["CODIGO"].map(normalize_code)
    df["Laboratorio"] = df["LABORATORIO"].fillna("").astype(str).str.strip().str.upper()
    df["Descripcion"] = df["DESCRIPCION"].fillna("").astype(str).str.strip()
    df = df[(df["Codigo"] != "") & (df["Laboratorio"] != "")].copy()

    # Si un código aparece con más de un laboratorio, conservar el más frecuente.
    counts = df.groupby(["Codigo", "Laboratorio"]).size().reset_index(name="_n")
    counts = counts.sort_values(["Codigo", "_n", "Laboratorio"], ascending=[True, False, True])
    winner = counts.drop_duplicates("Codigo", keep="first")[["Codigo", "Laboratorio"]]
    dup = counts.groupby("Codigo").size()
    conflicts = int((dup > 1).sum())
    df = df.merge(winner, on=["Codigo", "Laboratorio"], how="inner")
    df = df.drop_duplicates("Codigo", keep="first")[["Codigo", "Laboratorio", "Descripcion"]]
    df["Fuente"] = "PRODUCTOS POR LABORATORIO.md"
    df = df.sort_values(["Laboratorio", "Codigo"]).reset_index(drop=True)
    df.attrs["cache"] = "miss"
    df.attrs["conflicts"] = conflicts
    if conflicts:
        logger.warning("Dimensión laboratorio: %s códigos con más de un laboratorio; se usó el más frecuente.", conflicts)
    logger.info("Dimensión laboratorio: %s códigos, %s laboratorios", len(df), df["Laboratorio"].nunique())
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        df.to_pickle(cache)
        df.to_csv(CACHE_DIR / "dim_laboratorio.csv", index=False)
        try:
            df.to_parquet(parquet, index=False)
        except Exception:
            pass
    except Exception:
        pass
    return df


def attach_laboratorio(frame: pd.DataFrame, dim: pd.DataFrame) -> pd.DataFrame:
    """Agrega ``Laboratorio`` y renombra ``Linea`` a ``Linea_Terapeutica``."""
    out = frame.copy()
    if "Linea" in out.columns and "Linea_Terapeutica" not in out.columns:
        out = out.rename(columns={"Linea": "Linea_Terapeutica"})
    if "Linea_Terapeutica" not in out.columns:
        out["Linea_Terapeutica"] = "SIN LÍNEA"
    out["Linea_Terapeutica"] = out["Linea_Terapeutica"].fillna("SIN LÍNEA").astype(str).str.strip()
    out.loc[out["Linea_Terapeutica"] == "", "Linea_Terapeutica"] = "SIN LÍNEA"
    out["_Codigo_Dim"] = out["EAN"].map(normalize_code)
    lookup = dim.drop_duplicates("Codigo").set_index("Codigo")["Laboratorio"]
    out["Laboratorio"] = out["_Codigo_Dim"].map(lookup)
    out["_Lab_Matched"] = out["Laboratorio"].notna()
    out["Laboratorio"] = out["Laboratorio"].fillna("SIN_LABORATORIO").astype(str).str.upper()
    return out


def coverage_report(frame: pd.DataFrame) -> dict[str, float]:
    if frame.empty:
        return {"codigos_pct": 0.0, "venta_pct": 0.0, "codigos": 0, "codigos_match": 0}
    matched = frame["_Lab_Matched"] if "_Lab_Matched" in frame.columns else pd.Series(False, index=frame.index)
    codes = frame["EAN"].nunique()
    codes_match = frame.loc[matched, "EAN"].nunique()
    value_col = "Venta_USD" if "Venta_USD" in frame.columns else "Importe_USD"
    total = float(frame[value_col].sum()) if value_col in frame.columns else 0.0
    hit = float(frame.loc[matched, value_col].sum()) if value_col in frame.columns else 0.0
    return {
        "codigos_pct": codes_match / codes * 100 if codes else 0.0,
        "venta_pct": hit / total * 100 if total else 0.0,
        "codigos": int(codes),
        "codigos_match": int(codes_match),
    }
