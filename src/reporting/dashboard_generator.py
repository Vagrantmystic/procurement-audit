"""Dashboard ejecutivo autocontenido (HTML + SVG, sin dependencias externas)."""
from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

from reporting.context import ReportContext

PRIORITY_COLORS = {
    "CRÍTICA": "#C00000", "ALTA": "#C55A11", "MEDIA": "#FFC000",
    "BAJA": "#548235", "REVISAR": "#7030A0",
}
SEVERITY_COLORS = {"CRÍTICA": "#C00000", "ALTA": "#C55A11", "MEDIA": "#FFC000", "BAJA": "#548235"}
PALETTE = ["#1F3864", "#2F5597", "#4472C4", "#8EA9DB", "#70AD47", "#C55A11", "#FFC000", "#7030A0", "#C00000"]


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def _truncate(text: object, length: int = 40) -> str:
    value = str(text if text is not None else "")
    return value if len(value) <= length else value[: length - 1] + "…"


def _bar_chart(items: Sequence[tuple[str, float]], width: int = 560, bar_height: int = 22,
               gap: int = 9, label_width: int = 170, value_fmt: str = "${:,.0f}",
               colors: Sequence[str] | None = None) -> str:
    if not items:
        return "<p class='empty'>Sin datos</p>"
    maximum = max((abs(v) for _, v in items), default=0) or 1
    chart_width = width - label_width - 95
    height = len(items) * (bar_height + gap) + 8
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    for index, (label, value) in enumerate(items):
        y = 4 + index * (bar_height + gap)
        bar_w = max(2, abs(value) / maximum * chart_width)
        color = colors[index % len(colors)] if colors else PALETTE[index % len(PALETTE)]
        parts.append(f'<rect x="{label_width}" y="{y}" width="{bar_w:.1f}" height="{bar_height}" '
                     f'rx="3" fill="{color}" opacity="0.9"/>')
        parts.append(f'<text x="{label_width - 8}" y="{y + bar_height / 2 + 4}" text-anchor="end" '
                     f'font-size="11" fill="#333">{_esc(_truncate(label, 32))}</text>')
        parts.append(f'<text x="{label_width + bar_w + 6}" y="{y + bar_height / 2 + 4}" '
                     f'font-size="10.5" fill="#555">{_esc(value_fmt.format(value))}</text>')
    parts.append("</svg>")
    return "".join(parts)


class DashboardGenerator:
    def __init__(self, context: ReportContext, output_path: str | Path,
                 excel_name: str = "", logger: logging.Logger | None = None):
        self.ctx = context
        self.path = Path(output_path)
        self.excel_name = excel_name
        self.logger = logger or logging.getLogger("compras")

    def generate(self) -> Path:
        ctx = self.ctx
        orders = ctx.order_bundle.orders
        alerts = ctx.alert_bundle.alerts
        metrics = ctx.forecast_bundle.metrics

        kpis = [
            ("Órdenes sugeridas", f"{len(orders):,}", "productos con compra"),
            ("Unidades", f"{orders['Cantidad_Sugerida'].sum():,.0f}" if not orders.empty else "0", "a comprar"),
            ("Inversión", f"${orders['Costo_Total_USD'].sum():,.0f}" if not orders.empty else "$0", "estimada USD"),
            ("Alertas críticas", f"{ctx.alert_bundle.summary.get('por_severidad', {}).get('CRÍTICA', 0):,}", "requieren acción"),
            ("Cobertura de stock", f"{ctx.stock_coverage_pct:.1f}%", "productos con inventario"),
            ("WAPE A/B", f"{metrics.get('wape_ab', 0):.1f}%", "error del pronóstico"),
            ("Excluidos EOL", f"{ctx.order_bundle.summary.get('excluidos_eol', 0):,}", "no comprar"),
            ("Precios por aprobar", f"{ctx.order_bundle.summary.get('aprobaciones', 0):,}", "revisar/negociar"),
        ]
        kpi_html = "".join(
            f'<div class="kpi"><div class="kpi-label">{_esc(label)}</div>'
            f'<div class="kpi-value">{_esc(value)}</div>'
            f'<div class="kpi-note">{_esc(note)}</div></div>'
            for label, value, note in kpis
        )

        priority_items = [(str(r["Prioridad"]), float(r["Inversión_USD"]))
                          for _, r in ctx.order_bundle.by_priority.iterrows()]
        priority_colors = [PRIORITY_COLORS.get(label, "#2F5597") for label, _ in priority_items]
        priority_chart = _bar_chart(priority_items, colors=priority_colors)

        top_products = orders.sort_values("Costo_Total_USD", ascending=False).head(10) if not orders.empty else orders
        product_items = [(f"{_truncate(r['Descripcion'], 34)}", float(r["Costo_Total_USD"]))
                         for _, r in top_products.iterrows()]
        product_chart = _bar_chart(product_items, colors=["#2F5597"])

        segment_items = [(str(r["Segmento"]), float(r["Valor_USD"]))
                         for _, r in ctx.segmentation.summary.iterrows()]
        segment_chart = _bar_chart(segment_items, colors=["#4472C4"])

        type_counts = alerts["Tipo"].value_counts().head(8) if not alerts.empty else pd.Series(dtype=int)
        alert_items = [(str(k), float(v)) for k, v in type_counts.items()]
        alert_chart = _bar_chart(alert_items, colors=["#C55A11"], value_fmt="{:.0f}")

        supplier_rows = "".join(
            f"<tr><td>{_esc(r['Proveedor'])}</td><td class='num'>{int(r['Órdenes'])}</td>"
            f"<td class='num'>{int(r['Unidades']):,}</td><td class='num'>${r['Inversión_USD']:,.0f}</td>"
            f"<td class='num'>{r['%_Inversión']:.1f}%</td></tr>"
            for _, r in ctx.order_bundle.by_supplier.head(8).iterrows()
        ) or "<tr><td colspan='5' class='empty'>Sin datos</td></tr>"

        top_alerts = alerts.head(12) if not alerts.empty else alerts
        alert_rows = "".join(
            f"<tr><td><span class='badge' style='background:{SEVERITY_COLORS.get(str(r['Severidad']), '#666')}'>"
            f"{_esc(r['Severidad'])}</span></td><td>{_esc(r['Tipo'])}</td>"
            f"<td>{_esc(_truncate(r['Producto'], 42))}</td>"
            f"<td>{_esc(_truncate(r['Detalle'], 110))}</td></tr>"
            for _, r in top_alerts.iterrows()
        ) or "<tr><td colspan='4' class='empty'>Sin alertas</td></tr>"

        policy_rows = "".join(
            f"<tr><td>{_esc(r['Grupo'])}</td><td>{_esc(r['Modelo'])}</td>"
            f"<td class='num'>{int(r['Productos'])}</td><td class='num'>{r['WAPE']:.1f}%</td>"
            f"<td class='num'>{r['Hit25']:.1f}%</td></tr>"
            for _, r in ctx.forecast_bundle.policy_table.iterrows()
        ) or "<tr><td colspan='5' class='empty'>Sin datos</td></tr>"

        eol = ctx.order_bundle.excluded
        eol_rows = "".join(
            f"<tr><td>{_esc(_truncate(r['Descripcion'], 36))}</td>"
            f"<td>{_esc(r['Estado_Ciclo_Vida'])}</td>"
            f"<td class='num'>{r['Stock_Actual']:,.0f}</td>"
            f"<td class='num'>${r['Valor_Stock']:,.0f}</td></tr>"
            for _, r in eol.head(6).iterrows()
        ) or "<tr><td colspan='4' class='empty'>Sin bloqueos EOL</td></tr>"
        price = ctx.order_bundle.price_alerts
        price_rows = "".join(
            f"<tr><td>{_esc(_truncate(r['Descripcion'], 36))}</td>"
            f"<td>{_esc(r['Precio_Estado'])}</td>"
            f"<td class='num'>{r['Var_Precio_Pct']:+.1f}%</td>"
            f"<td class='num'>${r['Impacto_USD']:,.0f}</td></tr>"
            for _, r in price.head(6).iterrows()
        ) or "<tr><td colspan='4' class='empty'>Sin alertas de precio</td></tr>"

        excel_line = (f"<p class='footnote'>Reporte completo: <strong>{_esc(self.excel_name)}</strong> "
                      f"(órdenes, EOL, control de precios, alertas, análisis y parámetros).</p>"
                      if self.excel_name else "")

        winfo = ctx.warehouse_info
        if winfo:
            selected = winfo.get("selected", [])
            purchase = winfo.get("purchase_codes", selected)
            warehouse_line = (
                f"Almacenes ventas/stock: <strong>{_esc(', '.join(selected) if isinstance(selected, (list, tuple)) else selected)}</strong> · "
                f"Compras: <strong>{_esc(', '.join(purchase) if isinstance(purchase, (list, tuple)) else purchase)}</strong>"
            )
        else:
            warehouse_line = "Almacenes: <strong>no aplica (modo Excel)</strong>"

        html_doc = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dashboard de compras · {_esc(ctx.company)}</title>
<style>
  :root {{ --navy:#1F3864; --blue:#2F5597; --bg:#F3F5F9; --card:#FFFFFF; --text:#222; --muted:#667; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
         font-family:"Segoe UI", Arial, sans-serif; font-size:14px; }}
  .wrap {{ max-width:1240px; margin:0 auto; padding:22px; }}
  header {{ background:linear-gradient(135deg, var(--navy), var(--blue)); color:#fff;
            border-radius:16px; padding:26px 30px; box-shadow:0 8px 24px rgba(31,56,100,.18); }}
  header h1 {{ margin:0; font-size:24px; letter-spacing:.2px; }}
  header p {{ margin:8px 0 0; opacity:.9; font-size:13px; }}
  .kpis {{ display:grid; grid-template-columns:repeat(6,1fr); gap:12px; margin:18px 0; }}
  .kpi {{ background:var(--card); border-radius:12px; padding:14px 16px;
          box-shadow:0 3px 10px rgba(20,40,80,.06); border-left:4px solid var(--blue); }}
  .kpi-label {{ font-size:11px; text-transform:uppercase; letter-spacing:.5px; color:var(--muted); }}
  .kpi-value {{ font-size:22px; font-weight:700; color:var(--navy); margin:6px 0 2px; }}
  .kpi-note {{ font-size:11px; color:var(--muted); }}
  .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:16px; }}
  .card {{ background:var(--card); border-radius:14px; padding:18px 20px;
           box-shadow:0 3px 12px rgba(20,40,80,.06); }}
  .card h2 {{ margin:0 0 14px; font-size:15px; color:var(--navy); }}
  table {{ width:100%; border-collapse:collapse; font-size:12.5px; }}
  th {{ text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.4px;
        color:var(--muted); border-bottom:2px solid #E3E8F0; padding:8px 6px; }}
  td {{ padding:8px 6px; border-bottom:1px solid #EEF1F6; vertical-align:top; }}
  td.num, th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  tr:hover td {{ background:#FAFBFD; }}
  .badge {{ color:#fff; border-radius:20px; padding:2px 8px; font-size:10px; font-weight:700; }}
  .empty {{ color:var(--muted); font-style:italic; }}
  .footnote {{ color:var(--muted); font-size:12px; margin-top:8px; }}
  footer {{ color:var(--muted); font-size:11.5px; text-align:center; padding:20px 8px 8px; }}
  @media (max-width:1000px) {{ .kpis {{ grid-template-columns:repeat(3,1fr); }} .grid {{ grid-template-columns:1fr; }} }}
  @media (max-width:620px) {{ .kpis {{ grid-template-columns:repeat(2,1fr); }} header h1 {{ font-size:19px; }} }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Dashboard ejecutivo de compras</h1>
    <p>{_esc(ctx.company)} · Corte de datos {ctx.as_of:%d/%m/%Y} · Generado {ctx.generated_at:%d/%m/%Y %H:%M} · USD</p>
  </header>

  <section class="kpis">{kpi_html}</section>

  <section class="grid">
    <div class="card"><h2>Inversión por prioridad</h2>{priority_chart}</div>
    <div class="card"><h2>Top 10 productos por inversión</h2>{product_chart}</div>
  </section>

  <section class="grid">
    <div class="card"><h2>Valor de ventas por segmento ABC/XYZ</h2>{segment_chart}</div>
    <div class="card"><h2>Alertas por tipo</h2>{alert_chart}</div>
  </section>

  <section class="grid">
    <div class="card"><h2>Proveedores principales</h2>
      <table><thead><tr><th>Proveedor</th><th class="num">Órdenes</th><th class="num">Unidades</th>
      <th class="num">Inversión</th><th class="num">%</th></tr></thead><tbody>{supplier_rows}</tbody></table>
    </div>
    <div class="card"><h2>Política de pronóstico</h2>
      <table><thead><tr><th>Clase</th><th>Modelo</th><th class="num">Productos</th>
      <th class="num">WAPE</th><th class="num">Hit ±25%</th></tr></thead><tbody>{policy_rows}</tbody></table>
      <p class="footnote">WAPE = error absoluto / venta real. La selección se valida dejando un mes fuera.</p>
    </div>
  </section>

  <section class="grid">
    <div class="card"><h2>Bloqueos EOL / obsoletos</h2>
      <table><thead><tr><th>Producto</th><th>Estado</th><th class="num">Stock</th>
      <th class="num">Valor</th></tr></thead><tbody>{eol_rows}</tbody></table>
    </div>
    <div class="card"><h2>Precios que requieren revisión</h2>
      <table><thead><tr><th>Producto</th><th>Estado</th><th class="num">Var.</th>
      <th class="num">Impacto</th></tr></thead><tbody>{price_rows}</tbody></table>
    </div>
  </section>

  <section class="card" style="margin-bottom:16px">
    <h2>Alertas prioritarias</h2>
    <table><thead><tr><th>Severidad</th><th>Tipo</th><th>Producto</th><th>Detalle</th></tr></thead>
    <tbody>{alert_rows}</tbody></table>
  </section>

  {excel_line}
  <footer>{warehouse_line}<br>Sistema de Predicción y Optimización de Compras · Droguería Emmanuelle ·
  Los costos de productos sin catálogo son estimados desde el precio de venta.</footer>
</div>
</body>
</html>"""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(html_doc, encoding="utf-8")
        self.logger.info("Dashboard HTML generado: %s", self.path)
        return self.path
