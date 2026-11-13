"""Configuración de logging con salida a consola y archivo."""
from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logging(level: str = "INFO", log_dir: str | Path | None = None, name: str = "compras") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    logger.propagate = False
    if logger.handlers:
        return logger

    formatter = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    if log_dir:
        try:
            path = Path(log_dir)
            path.mkdir(parents=True, exist_ok=True)
            from datetime import datetime

            file_handler = logging.FileHandler(
                path / f"ejecucion_{datetime.now():%Y%m%d_%H%M%S}.log", encoding="utf-8"
            )
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
            )
            logger.addHandler(file_handler)
        except OSError:
            pass
    return logger
