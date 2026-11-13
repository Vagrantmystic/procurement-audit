"""Render de PDF para el módulo de Ventas.

Intenta WeasyPrint (Linux/servidor con Pango) y cae a Chromium/Edge headless
en Windows, que imprime el mismo HTML con las imágenes Base64.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

LOGGER = logging.getLogger("compras")


def render_pdf(html: str, base_url: str | Path | None = None) -> bytes:
    try:
        from weasyprint import HTML  # type: ignore

        return HTML(string=html, base_url=str(base_url) if base_url else None).write_pdf()
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("WeasyPrint no disponible (%s); se usa Chromium/Edge.", exc)
    return _render_with_chromium(html)


def _render_with_chromium(html: str) -> bytes:
    edge = _find_browser()
    if not edge:
        raise RuntimeError("No hay WeasyPrint ni Chromium/Edge para generar el PDF.")
    with tempfile.TemporaryDirectory(prefix="ventas_pdf_") as tmp:
        tmp_path = Path(tmp)
        html_path = tmp_path / "export.html"
        pdf_path = tmp_path / "export.pdf"
        html_path.write_text(html, encoding="utf-8")
        cmd = [
            str(edge), "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}",
            html_path.resolve().as_uri(),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if not pdf_path.exists() or pdf_path.stat().st_size == 0:
            raise RuntimeError(
                "No se pudo generar el PDF con Chromium/Edge. "
                f"Salida: {(result.stderr or result.stdout)[-500:]}"
            )
        return pdf_path.read_bytes()


def _find_browser() -> Path | None:
    candidates = [
        shutil.which("msedge"),
        shutil.which("microsoft-edge"),
        shutil.which("chrome"),
        shutil.which("google-chrome"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists():
            return path
    return None
