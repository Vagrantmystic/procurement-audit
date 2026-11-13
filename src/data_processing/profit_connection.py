"""Conexión de **solo lectura** a Profit 2K12 (SQL Server).

Reglas de seguridad:
* Solo se permiten sentencias SELECT / WITH.
* Se rechazan INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, EXEC, MERGE, TRUNCATE, GRANT.
* La conexión se abre con autocommit y no ejecuta escrituras.
* La contraseña nunca se registra en logs ni se imprime.
"""
from __future__ import annotations

import logging
import os
import re
import warnings
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "database.local.yaml"

FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|EXEC|EXECUTE|MERGE|TRUNCATE|GRANT|REVOKE|"
    r"BACKUP|RESTORE|DBCC|SHUTDOWN|KILL|sp_configure|xp_cmdshell)\b",
    re.IGNORECASE,
)


class ReadOnlyViolation(RuntimeError):
    """Se intentó ejecutar una sentencia no permitida."""


class ProfitConnection:
    def __init__(self, config_path: str | Path | None = None,
                 logger: logging.Logger | None = None):
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG
        self.logger = logger or logging.getLogger("compras")
        self.config = self._load_config()
        self._connection = None

    # ------------------------------------------------------------------ config
    def _load_config(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.config_path.exists():
            with open(self.config_path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        db = dict(data.get("database", {}))
        # Variables de entorno tienen prioridad (recomendado en producción)
        db["server"] = os.getenv("PROFIT_DB_SERVER", db.get("server"))
        db["database"] = os.getenv("PROFIT_DB_NAME", db.get("database"))
        db["username"] = os.getenv("PROFIT_DB_USER", db.get("username"))
        db["password"] = os.getenv("PROFIT_DB_PASSWORD", db.get("password"))
        db["driver"] = os.getenv("PROFIT_DB_DRIVER", db.get("driver", "{ODBC Driver 17 for SQL Server}"))
        db["port"] = int(os.getenv("PROFIT_DB_PORT", db.get("port", 1433)))
        if not all(db.get(k) for k in ("server", "database", "username", "password")):
            raise ValueError(
                "Faltan credenciales de Profit. Use config/database.local.yaml "
                "o las variables PROFIT_DB_SERVER/NAME/USER/PASSWORD."
            )
        data["database"] = db
        return data

    @property
    def driver(self) -> str:
        driver = str(self.config["database"]["driver"]).strip()
        if not driver.startswith("{"):
            driver = "{" + driver + "}"
        return driver

    # ------------------------------------------------------------------ conexión
    def connect(self):
        import pyodbc

        if self._connection is not None:
            return self._connection
        db = self.config["database"]
        connection_string = (
            f"DRIVER={self.driver};"
            f"SERVER={db['server']},{db.get('port', 1433)};"
            f"DATABASE={db['database']};"
            f"UID={db['username']};PWD={db['password']};"
            "TrustServerCertificate=yes;"
            f"Connection Timeout={int(db.get('timeout', 15))};"
            "ApplicationIntent=ReadOnly;"
        )
        self._connection = pyodbc.connect(connection_string, autocommit=True)
        try:
            cursor = self._connection.cursor()
            cursor.execute("SET NOCOUNT ON; SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;")
            cursor.close()
        except Exception:
            pass
        self.logger.info("Conectado a Profit %s / %s (solo lectura)", db["server"], db["database"])
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # ------------------------------------------------------------------ consultas
    @staticmethod
    def _validate(sql: str) -> str:
        text = (sql or "").strip()
        if not text:
            raise ReadOnlyViolation("Consulta vacía")
        if FORBIDDEN.search(text):
            raise ReadOnlyViolation("La consulta contiene una operación no permitida (solo SELECT/WITH)")
        first = text.split(None, 1)[0].upper()
        if first not in {"SELECT", "WITH"}:
            raise ReadOnlyViolation("Solo se permiten consultas SELECT o WITH")
        # Evitar múltiples sentencias
        if ";" in text.rstrip(";"):
            raise ReadOnlyViolation("No se permiten múltiples sentencias en una consulta")
        return text

    def query(self, sql: str, params: Iterable[Any] | None = None,
              chunksize: int | None = None) -> pd.DataFrame:
        sql = self._validate(sql)
        connection = self.connect()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            if chunksize:
                frames = pd.read_sql_query(sql, connection, params=params or [], chunksize=chunksize)
                return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            return pd.read_sql_query(sql, connection, params=params or [])

    def scalar(self, sql: str, params: Iterable[Any] | None = None) -> Any:
        df = self.query(sql, params)
        return df.iloc[0, 0] if not df.empty else None

    # ------------------------------------------------------------------ metadatos
    def tables(self, pattern: str | None = None) -> pd.DataFrame:
        sql = """
            SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_TYPE IN ('BASE TABLE', 'VIEW')
        """
        params: list[Any] = []
        if pattern:
            sql += " AND (TABLE_NAME LIKE ? OR TABLE_SCHEMA LIKE ?)"
            params = [f"%{pattern}%", f"%{pattern}%"]
        sql += " ORDER BY TABLE_NAME"
        return self.query(sql, params)

    def columns(self, table: str) -> pd.DataFrame:
        return self.query(
            """
            SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE, ORDINAL_POSITION
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = ?
            ORDER BY ORDINAL_POSITION
            """,
            [table],
        )

    def primary_keys(self, table: str) -> pd.DataFrame:
        return self.query(
            """
            SELECT c.COLUMN_NAME, tc.CONSTRAINT_TYPE
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            JOIN INFORMATION_SCHEMA.CONSTRAINT_COLUMN_USAGE c
              ON c.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
            WHERE tc.TABLE_NAME = ? AND tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
            """,
            [table],
        )
