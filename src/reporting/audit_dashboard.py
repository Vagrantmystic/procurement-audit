"""Dashboard HTML de la auditoría de compras (autocontenido)."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from audit.purchase_audit import AuditResult
from reporting.dashboard_generator import _bar_chart, _esc, _truncate

STATE_COLORS = {
    "QUIEBRE": "#C00000", "CRÍTICO": "#C55A11", "FALTANTE": "#FFC000",
    "SOBRECOMPRA": "#7030A0", "SIN VENTA": "#7F7F7F",
    "SIN COMPRA 2026": "#2F5597", "OK": "#548235",
}


class AuditDashboardGenerator:
    def __init__(self, audit: AuditResult, output_path: str | Path,
                 excel_name: str = "", logger: logging.Logger | None = None):
        self.audit = audit
        self.path = Path(output_path)
        self.excel_name = excel_name
        self.logger = logger or logging.getLogger("compras")

    def generate(self) -> Path:
        a = self.audit
        m = a.metrics
        cards = [
            ("Compra de mercancía", f"${m['compra_usd']:,.0f}", "2026"),
            ("Venta de mercancía", f"${m['venta_usd']:,.0f}", "2026"),
            ("Margen bruto", f"{m['margen_pct']:.1f}%", f"${m['margen_usd']:,.0f}"),
            ("Cobertura", f"{m['cobertura_dias']:.0f} días", "stock + tránsito"),
            ("Quiebres reales", f"{m['quiebres']:,}", f"${m['venta_quiebre_usd']:,.0f} venta"),
            ("Sobrecompra", f"{m['sobrecompra']:,}", f"${m['capital_exceso_usd']:,.0f} exceso"),
        ]
        kpi_html = "".join(
            f'<div class="kpi"><div class="kpi-label">{_esc(l)}</div>'
            f'<div class="kpi-value">{_esc(v)}</div>'
            f'<div class="kpi-note">{_esc(n)}</div></div>' for l, v, n in cards)

        monthly = a.monthly.copy()
        compra_items = [(str(r["Mes"]), float(r["Compra_USD"])) for _, r in monthly.iterrows()]
        venta_items = [(str(r["Mes"]), float(r["Venta_USD"])) for _, r in monthly.iterrows()]
        compra_chart = _bar_chart(compra_items, colors=["#2F5597"])
        venta_chart = _bar_chart(venta_items, colors=["#70AD47"])

        status = a.products["Estado"].value_counts()
        status_items = [(str(k), float(v)) for k, v in status.items()]
        status_chart = _bar_chart(status_items, colors=[STATE_COLORS.get(k, "#2F5597") for k, _ in status_items],
                                  value_fmt="{:.0f}")

        sup = a.suppliers.head(10)
        sup_items = [(str(r["Proveedor"]), float(r["Compra_USD"])) for _, r in sup.iterrows()]
        sup_chart = _bar_chart(sup_items, colors=["#1F3864"])

        over = a.products[a.products["Estado"] == "SOBRECOMPRA"].sort_values("Exceso_USD", ascending=False).head(10)
        over_items = [(str(r["Descripcion"]), float(r["Exceso_USD"])) for _, r in over.iterrows()]
        over_chart = _bar_chart(over_items, colors=["#7030A0"])

        miss = a.products[a.products["Estado"].isin(["QUIEBRE", "CRÍTICO", "FALTANTE"])] \
            .sort_values("Venta_USD", ascending=False).head(12)
        miss_rows = "".join(
            f"<tr><td><span class='badge' style='background:{STATE_COLORS.get(str(r['Estado']), '#666')}'>"
            f"{_esc(r['Estado'])}</span></td><td>{_esc(_truncate(r['Descripcion'], 48))}</td>"
            f"<td class='num'>{int(r['Stock_Actual'])}</td><td class='num'>{int(r['Stock_Transito'])}</td>"
            f"<td class='num'>${r['Venta_USD']:,.0f}</td><td class='num'>${r['Faltante_USD']:,.0f}</td></tr>"
            for _, r in miss.iterrows()) or "<tr><td colspan='6' class='empty'>Sin datos</td></tr>"

        over_rows = "".join(
            f"<tr><td>{_esc(_truncate(r['Descripcion'], 48))}</td>"
            f"<td class='num'>{int(r['Stock_Actual']):,}</td><td class='num'>{int(r['Stock_Transito']):,}</td>"
            f"<td class='num'>{r['Cobertura_Disponible_Dias']:,.0f}</td>"
            f"<td class='num'>${r['Exceso_USD']:,.0f}</td></tr>"
            for _, r in over.iterrows()) or "<tr><td colspan='5' class='empty'>Sin datos</td></tr>"

        sup_rows = "".join(
            f"<tr><td>{_esc(_truncate(r['Proveedor'], 44))}</td>"
            f"<td class='num'>${r['Compra_USD']:,.0f}</td><td class='num'>{r['Share_Pct']:.1f}%</td>"
            f"<td class='num'>{int(r['Productos'])}</td></tr>"
            for _, r in sup.iterrows()) or "<tr><td colspan='4' class='empty'>Sin datos</td></tr>"

        findings = "".join(f"<li>{_esc(text)}</li>" for text in a.findings)
        winfo = getattr(a, "warehouse_info", {}) or {}
        wh_line = ", ".join(winfo.get("selected", [])) or "No informado"
        excel_line = (f"<p class='footnote'>Reporte completo: <strong>{_esc(self.excel_name)}</strong> "
                      f"(productos, quiebres, sobrecompra, proveedores, precios y metodología).</p>"
                      if self.excel_name else "")

        html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Auditoría de compras {a.year} · Droguería Emmanuelle</title>
<style>
 :root {{ --navy:#1F3864; --blue:#2F5597; --bg:#F3F5F9; --card:#fff; --muted:#667; }}
 * {{ box-sizing:border-box; }}
 body {{ margin:0; background:var(--bg); color:#222; font-family:"Segoe UI",Arial,sans-serif; font-size:14px; }}
 .wrap {{ max-width:1240px; margin:0 auto; padding:22px; }}
 header {{ background:linear-gradient(135deg,var(--navy),var(--blue)); color:#fff; border-radius:16px;
           padding:26px 30px; box-shadow:0 8px 24px rgba(31,56,100,.18); }}
 header h1 {{ margin:0; font-size:24px; }} header p {{ margin:8px 0 0; opacity:.9; font-size:13px; }}
 .kpis {{ display:grid; grid-template-columns:repeat(6,1fr); gap:12px; margin:18px 0; }}
 .kpi {{ background:var(--card); border-radius:12px; padding:14px 16px; box-shadow:0 3px 10px rgba(20,40,80,.06);
         border-left:4px solid var(--blue); }}
 .kpi-label {{ font-size:11px; text-transform:uppercase; letter-spacing:.5px; color:var(--muted); }}
 .kpi-value {{ font-size:22px; font-weight:700; color:var(--navy); margin:6px 0 2px; }}
 .kpi-note {{ font-size:11px; color:var(--muted); }}
 .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:16px; }}
 .card {{ background:var(--card); border-radius:14px; padding:18px 20px; box-shadow:0 3px 12px rgba(20,40,80,.06); }}
 .card h2 {{ margin:0 0 14px; font-size:15px; color:var(--navy); }}
 .findings {{ margin:0; padding-left:20px; }} .findings li {{ margin-bottom:8px; line-height:1.45; }}
 table {{ width:100%; border-collapse:collapse; font-size:12.5px; }}
 th {{ text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.4px; color:var(--muted);
       border-bottom:2px solid #E3E8F0; padding:8px 6px; }}
 td {{ padding:8px 6px; border-bottom:1px solid #EEF1F6; vertical-align:top; }}
 td.num, th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
 tr:hover td {{ background:#FAFBFD; }}
 .badge {{ color:#fff; border-radius:20px; padding:2px 8px; font-size:10px; font-weight:700; }}
 .empty {{ color:var(--muted); font-style:italic; }}
 .footnote {{ color:var(--muted); font-size:12px; margin-top:8px; }}
 footer {{ color:var(--muted); font-size:11.5px; text-align:center; padding:20px 8px 8px; }}
 @media(max-width:1000px) {{ .kpis {{ grid-template-columns:repeat(3,1fr); }} .grid {{ grid-template-columns:1fr; }} }}
 @media(max-width:620px) {{ .kpis {{ grid-template-columns:repeat(2,1fr); }} }}
</style></head><body><div class="wrap">
<header><h1>Auditoría de compras {a.year}: ¿compramos bien?</h1>
<p>Droguería Emmanuelle · Corte {a.as_of:%d/%m/%Y} · Almacenes: {_esc(wh_line)} · Fuente Profit 2K12 · Solo lectura</p></header>
<section class="kpis">{kpi_html}</section>
<section class="card" style="margin-bottom:16px"><h2>Veredicto y hallazgos</h2><ul class="findings">{findings}</ul></section>
<section class="grid">
  <div class="card"><h2>Compra mensual (USD)</h2>{compra_chart}</div>
  <div class="card"><h2>Venta mensual (USD)</h2>{venta_chart}</div>
</section>
<section class="grid">
  <div class="card"><h2>Estado del portafolio</h2>{status_chart}</div>
  <div class="card"><h2>Top proveedores por compra</h2>{sup_chart}</div>
</section>
<section class="grid">
  <div class="card"><h2>Top exceso de inventario</h2>{over_chart}</div>
  <div class="card"><h2>Proveedores principales</h2>
    <table><thead><tr><th>Proveedor</th><th class="num">Compra</th><th class="num">Share</th><th class="num">SKU</th></tr></thead>
    <tbody>{sup_rows}</tbody></table></div>
</section>
<section class="card" style="margin-bottom:16px"><h2>Quiebres y faltantes prioritarios</h2>
<table><thead><tr><th>Estado</th><th>Producto</th><th class="num">Stock</th><th class="num">Tránsito</th>
<th class="num">Venta 2026</th><th class="num">Faltante USD</th></tr></thead><tbody>{miss_rows}</tbody></table></section>
<section class="card" style="margin-bottom:16px"><h2>Capital inmovilizado (top)</h2>
<table><thead><tr><th>Producto</th><th class="num">Stock</th><th class="num">Tránsito</th>
<th class="num">Cobertura (d)</th><th class="num">Exceso USD</th></tr></thead><tbody>{over_rows}</tbody></table></section>
{excel_line}
<footer>Sistema de Compras · Auditoría generada desde Profit 2K12 · Los importes se convierten a USD con la tasa del documento.</footer>
</div></body></html>"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(html, encoding="utf-8")
        self.logger.info("Dashboard de auditoría generado: %s", self.path)
        return self.path
