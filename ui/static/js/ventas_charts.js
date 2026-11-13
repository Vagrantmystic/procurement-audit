const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const fmtMoney = (v, dec = 0) => v == null || isNaN(v) ? "—" :
  "$" + Number(v).toLocaleString("es-VE", { minimumFractionDigits: dec, maximumFractionDigits: dec });
const fmtNum = (v, dec = 0) => v == null || isNaN(v) ? "—" :
  Number(v).toLocaleString("es-VE", { minimumFractionDigits: dec, maximumFractionDigits: dec });
const fmtPct = (v, dec = 1) => v == null || isNaN(v) ? "—" : Number(v).toFixed(dec) + "%";
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const charts = {};
const state = { desde: "", hasta: "", almacenes: [], laboratorio: "", linea: "", categoria: "", cliente: "" };

function toast(msg, error = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast show" + (error ? " error" : "");
  setTimeout(() => el.className = "toast", 3500);
}
function overlay(show) { $("#overlay").classList.toggle("show", show); }

function query() {
  const p = new URLSearchParams({ desde: state.desde, hasta: state.hasta });
  if (state.almacenes.length) p.set("almacen", state.almacenes.join(","));
  if (state.laboratorio) p.set("laboratorio", state.laboratorio);
  if (state.linea) p.set("linea", state.linea);
  if (state.categoria) p.set("categoria", state.categoria);
  return p;
}

async function api(path) {
  const res = await fetch(path + (path.includes("?") ? "&" : "?") + query().toString());
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Error de consulta");
  return data;
}

function saveFilters() {
  try { sessionStorage.setItem("ventas_filters", JSON.stringify(state)); } catch (e) { /* noop */ }
}
function restoreFilters() {
  try {
    const raw = sessionStorage.getItem("ventas_filters");
    if (!raw) return;
    const saved = JSON.parse(raw);
    state.laboratorio = saved.laboratorio || "";
    state.linea = saved.linea || "";
    state.categoria = saved.categoria || "";
    if (Array.isArray(saved.almacenes)) state.almacenes = saved.almacenes;
    if (saved.desde) state.desde = saved.desde;
    if (saved.hasta) state.hasta = saved.hasta;
  } catch (e) { /* noop */ }
}

function setDefaultDates() {
  const today = new Date();
  const end = today.toISOString().slice(0, 10);
  const start = new Date(today.getTime() - 89 * 86400000).toISOString().slice(0, 10);
  state.desde = state.desde || start;
  state.hasta = state.hasta || end;
  $("#fechaDesde").value = state.desde;
  $("#fechaHasta").value = state.hasta;
}

async function loadWarehouses() {
  const box = $("#almacenFiltro");
  const summary = $("#almacenSummary");
  try {
    const res = await fetch("/ventas/api/almacenes");
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "No se pudieron cargar almacenes");
    const items = data.available || [];
    const selected = new Set(state.almacenes.length ? state.almacenes : (data.summary.selected || []));
    state.almacenes = [...selected];
    box.innerHTML = items.map((w) =>
      `<label><input type="checkbox" value="${esc(w.codigo)}" ${selected.has(w.codigo) ? "checked" : ""}>
        <span><strong>${esc(w.codigo)}</strong> · ${esc(w.nombre)}</span></label>`
    ).join("");
    if (summary) summary.textContent = selected.size ? `${selected.size} almacén(es)` : "Seleccionar almacenes";
  } catch (e) {
    box.innerHTML = `<span class="empty">Sin almacenes</span>`;
    toast(e.message, true);
  }
}

async function loadFiltros() {
  try {
    const data = await api("/ventas/api/filtros");
    const lab = $("#labFiltro"), linea = $("#lineaFiltro"), cat = $("#catFiltro");
    const labs = data.laboratorios || [], lineas = data.lineas_terapeuticas || [], cats = data.categorias || [];
    lab.innerHTML = `<option value="">Todos</option>` + labs.map((v) => `<option ${v === state.laboratorio ? "selected" : ""}>${esc(v)}</option>`).join("");
    linea.innerHTML = `<option value="">Todas</option>` + lineas.map((v) => `<option ${v === state.linea ? "selected" : ""}>${esc(v)}</option>`).join("");
    cat.innerHTML = `<option value="">Todas</option>` + cats.map((v) => `<option ${v === state.categoria ? "selected" : ""}>${esc(v)}</option>`).join("");
  } catch (e) { /* filtros opcionales */ }
}
function deltaHtml(value) {
  if (value == null) return `<span class="note">—</span>`;
  const up = value >= 0;
  return `<span class="${up ? "up" : "down"}">${up ? "▲" : "▼"} ${fmtPct(Math.abs(value))}</span>`;
}

function renderResumen(data) {
  const k = data.kpis || {}, d = data.variacion_pct || {};
  $("#kpiVenta").textContent = fmtMoney(k.venta_usd);
  $("#kpiVentaDelta").innerHTML = `${deltaHtml(d.venta_usd)} vs. período anterior`;
  $("#kpiMargen").textContent = fmtPct(k.margen_pct);
  $("#kpiMargenDelta").innerHTML = `${deltaHtml(d.margen_pct)} · costo cubierto ${fmtPct(k.cobertura_costo_pct)}`;
  $("#kpiTicket").textContent = fmtMoney(k.ticket_promedio);
  $("#kpiTicketDelta").innerHTML = `${deltaHtml(d.ticket_promedio)} · ${fmtNum(k.facturas)} facturas`;
  $("#kpiClientes").textContent = fmtNum(k.clientes);
  $("#kpiClientesDelta").innerHTML = `${deltaHtml(d.clientes)} vs. anterior`;
  $("#kpiSku").textContent = fmtNum(k.sku);
  $("#kpiSkuDelta").innerHTML = `${deltaHtml(d.sku)} vs. anterior`;
  const top = data.top_laboratorio || {};
  $("#kpiLab").textContent = top.nombre || "—";
  $("#kpiLabVenta").textContent = top.venta_usd ? fmtMoney(top.venta_usd) : "—";

  chartLine("chartSerie", (data.serie_diaria || []).map((r) => r.fecha), (data.serie_diaria || []).map((r) => r.Venta_USD));
  renderHeatmap(data.heatmap || []);
}

function chartLine(id, labels, values) {
  if (!window.Chart) return fallback(id);
  destroy(id);
  const ctx = document.getElementById(id);
  charts[id] = new Chart(ctx, {
    type: "line",
    data: { labels, datasets: [{ label: "Venta USD", data: values, borderColor: "#2563eb",
      backgroundColor: "rgba(37,99,235,.12)", fill: true, tension: .25, pointRadius: 2 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
      scales: { y: { ticks: { callback: (v) => "$" + Number(v).toLocaleString("es-VE") } } } },
  });
}

function barChart(id, labels, values, color = "#2F5597") {
  if (!window.Chart) return fallback(id);
  destroy(id);
  charts[id] = new Chart(document.getElementById(id), {
    type: "bar",
    data: { labels, datasets: [{ data: values, backgroundColor: color, borderRadius: 3 }] },
    options: { indexAxis: "y", responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { x: { ticks: { callback: (v) => "$" + Number(v).toLocaleString("es-VE") } } } },
  });
}

function doughnut(id, labels, values) {
  if (!window.Chart) return fallback(id);
  destroy(id);
  const colors = ["#1F3864", "#2F5597", "#4472C4", "#8EA9DB", "#70AD47", "#C55A11", "#FFC000", "#7030A0", "#C00000"];
  charts[id] = new Chart(document.getElementById(id), {
    type: "doughnut",
    data: { labels, datasets: [{ data: values, backgroundColor: colors }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "right" } } },
  });
}

function destroy(id) { if (charts[id]) { charts[id].destroy(); delete charts[id]; } }
function fallback(id) { const c = document.getElementById(id); if (c) c.parentElement.innerHTML = "<p class='empty'>Chart.js no disponible; revise la conexión.</p>"; }

function renderHeatmap(rows) {
  const box = $("#heatmap");
  if (!rows.length) { box.innerHTML = "<p class='empty'>Sin datos.</p>"; return; }
  const days = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"];
  const months = [...new Set(rows.map((r) => r.Mes))].sort();
  box.style.gridTemplateColumns = `42px repeat(${months.length}, 1fr)`;
  const map = new Map(rows.map((r) => [`${r.Dia}|${r.Mes}`, r.Venta_USD]));
  const max = Math.max(...rows.map((r) => r.Venta_USD), 1);
  let html = `<div></div>` + months.map((m) => `<div class="hm-month">${esc(m.slice(2))}</div>`).join("");
  days.forEach((day) => {
    html += `<div class="hm-label">${day}</div>`;
    months.forEach((m) => {
      const v = map.get(`${day}|${m}`) || 0;
      const alpha = Math.max(.08, v / max);
      html += `<div class="hm-cell" title="${esc(day)} ${esc(m)}: ${fmtMoney(v)}" style="background:rgba(37,99,235,${alpha.toFixed(2)})"></div>`;
    });
  });
  box.innerHTML = html;
}

function renderLabs(data) {
  const items = (data.items || []).slice(0, 10);
  barChart("chartLabs", items.map((r) => r.Laboratorio), items.map((r) => r.Venta_USD), "#2F5597");
  doughnut("chartMix", items.map((r) => r.Laboratorio), items.map((r) => r.Venta_USD));
}

function renderLineas(data) {
  const items = (data.items || []).slice(0, 10);
  barChart("chartLineas", items.map((r) => r.Linea_Terapeutica), items.map((r) => r.Venta_USD), "#7030A0");
}

function renderProducts(data) {
  const items = (data.items || []).slice(0, 10);
  barChart("chartProducts", items.map((r) => r.Descripcion), items.map((r) => r.Venta_USD), "#70AD47");
}

function renderClients(data) {
  const items = (data.items || []).slice(0, 10);
  barChart("chartClients", items.map((r) => r.Cliente), items.map((r) => r.Venta_USD), "#C55A11");
  const body = $("#tablaClientes tbody");
  body.innerHTML = (data.items || []).map((r) => `<tr class="clickable" data-cod="${esc(r.CodCliente)}">
    <td>${esc(r.Cliente)}</td><td class="num">${fmtMoney(r.Venta_USD)}</td>
    <td class="num">${fmtPct(r.Margen_Pct)}</td><td class="num">${fmtNum(r.Facturas)}</td>
    <td class="num">${fmtNum(r.SKU)}</td><td>${esc(r.Ultima_Venta || "—")}</td></tr>`).join("")
    || `<tr><td colspan="6" class="empty">Sin clientes.</td></tr>`;
  $$("#tablaClientes tbody tr.clickable").forEach((tr) => tr.addEventListener("click", () => openCliente(tr.dataset.cod)));
}

async function exportarClientePDF() {
  const cod = state.cliente;
  if (!cod) { toast("Seleccione un cliente primero.", true); return; }
  const images = [];
  if (charts.chartDrill) {
    try { images.push({ label: "Evolución mensual", data: charts.chartDrill.toBase64Image("image/png", 1) }); } catch (e) { /* omitir */ }
  }
  overlay(true);
  try {
    const res = await fetch(`/ventas/api/cliente/${encodeURIComponent(cod)}/pdf?` + query().toString(), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ charts: images }),
    });
    if (!res.ok) {
      let detail = "No se pudo generar el PDF del cliente";
      try { const err = await res.json(); detail = err.error || detail; } catch (e) { /* noop */ }
      throw new Error(detail);
    }
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `cliente_${cod}_${state.desde}_${state.hasta}.pdf`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 3000);
    toast("PDF del cliente generado.");
  } catch (e) {
    toast(e.message, true);
  } finally {
    overlay(false);
  }
}

async function openCliente(cod) {
  if (!cod) return;
  state.cliente = cod;
  overlay(true);
  try {
    const data = await api(`/ventas/api/cliente/${encodeURIComponent(cod)}`);
    state.cliente = cod;
    const f = data.cliente;
    if (!f) throw new Error("Cliente sin ventas en el período");
    $("#drillTitle").textContent = `${f.cliente} · ${f.codigo}`;
    $("#drillFicha").innerHTML = `
      <div><small>Venta neta</small><b>${fmtMoney(f.venta_usd)}</b></div>
      <div><small>Margen</small><b>${fmtPct(f.margen_pct)}</b></div>
      <div><small>Facturas</small><b>${fmtNum(f.facturas)}</b></div>
      <div><small>Ticket promedio</small><b>${fmtMoney(f.ticket_promedio)}</b></div>`;
    $("#tablaDrillLabs tbody").innerHTML = (data.items || []).map((r) => `<tr><td>${esc(r.Laboratorio)}</td>
      <td class="num">${fmtMoney(r.Venta_USD)}</td><td class="num">${fmtPct(r.Margen_Pct)}</td>
      <td class="num">${fmtNum(r.Unidades)}</td><td>${esc(r.Ultima_Venta || "—")}</td></tr>`).join("")
      || `<tr><td colspan="5" class="empty">Sin laboratorios.</td></tr>`;
    $("#tablaDrillProd tbody").innerHTML = (data.productos || []).map((r) => `<tr><td>${esc(r.Descripcion)}
      <br><small>${esc(r.Laboratorio || "")} · ${esc(r.Linea_Terapeutica || "")}</small></td>
      <td class="num">${fmtMoney(r.Venta_USD)}</td><td class="num">${fmtNum(r.Unidades)}</td></tr>`).join("")
      || `<tr><td colspan="3" class="empty">Sin productos.</td></tr>`;
    const serie = data.serie_mensual || [];
    if (window.Chart) {
      destroy("chartDrill");
      charts.chartDrill = new Chart(document.getElementById("chartDrill"), {
        type: "bar",
        data: { labels: serie.map((r) => r.Mes), datasets: [{ label: "Venta USD", data: serie.map((r) => r.Venta_USD), backgroundColor: "#7030A0" }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } },
      });
    }
    const section = $("#drilldown");
    section.classList.add("show");
    section.scrollIntoView({ behavior: "smooth", block: "start" });
    saveFilters();
  } catch (e) {
    toast(e.message, true);
  } finally {
    overlay(false);
  }
}

async function loadAll() {
  overlay(true);
  try {
    const resumen = await api("/ventas/api/resumen");
    const [labs, lineas, products, clients] = await Promise.all([
      api("/ventas/api/laboratorios"), api("/ventas/api/lineas"),
      api("/ventas/api/productos"), api("/ventas/api/clientes"),
    ]);
    renderResumen(resumen);
    renderLabs(labs);
    renderLineas(lineas);
    renderProducts(products);
    renderClients(clients);
    const href = "/ventas/api/export?" + query().toString();
    $("#exportar").href = href;
    $("#pageSub").textContent = `Del ${resumen.desde} al ${resumen.hasta} · ${resumen.dias} días · ${state.almacenes.length} almacén(es)`;
    saveFilters();
    if (window.VENTAS_VIEW === "laboratorios") $("#sectionLabs").scrollIntoView({ block: "start" });
    if (window.VENTAS_VIEW === "productos") $("#sectionProducts").scrollIntoView({ block: "start" });
    if (window.VENTAS_VIEW === "clientes") $("#sectionClients").scrollIntoView({ block: "start" });
    if (window.VENTAS_VIEW === "drilldown" && window.VENTAS_CLIENTE) openCliente(window.VENTAS_CLIENTE);
  } catch (e) {
    toast(e.message, true);
  } finally {
    overlay(false);
  }
}

function applyFilters() {
  state.desde = $("#fechaDesde").value;
  state.hasta = $("#fechaHasta").value;
  state.almacenes = [...$$("#almacenFiltro input:checked")].map((el) => el.value);
  state.laboratorio = $("#labFiltro").value;
  state.linea = $("#lineaFiltro").value;
  state.categoria = $("#catFiltro").value;
  const summary = $("#almacenSummary");
  if (summary) summary.textContent = state.almacenes.length ? `${state.almacenes.length} almacén(es)` : "Seleccionar almacenes";
  $("#almacenDetails").removeAttribute("open");
  if (!state.desde || !state.hasta) { toast("Seleccione fechas.", true); return; }
  loadAll();
  loadFiltros();
}

async function exportarPDF() {
  const specs = [
    ["chartSerie", "Venta neta diaria"],
    ["chartMix", "Composición por laboratorio"],
    ["chartLabs", "Top laboratorios"],
    ["chartLineas", "Top líneas terapéuticas"],
    ["chartProducts", "Top productos"],
    ["chartClients", "Top clientes"],
  ];
  const images = [];
  specs.forEach(([id, label]) => {
    const chart = charts[id];
    if (!chart) return;
    try { images.push({ label, data: chart.toBase64Image("image/png", 1) }); } catch (e) { /* omitir */ }
  });
  overlay(true);
  try {
    const res = await fetch("/ventas/api/export/pdf?" + query().toString(), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        charts: images,
        kpis: {
          venta: $("#kpiVenta").textContent, margen: $("#kpiMargen").textContent,
          ticket: $("#kpiTicket").textContent, clientes: $("#kpiClientes").textContent,
          sku: $("#kpiSku").textContent, laboratorio: $("#kpiLab").textContent,
        },
      }),
    });
    if (!res.ok) {
      let detail = "No se pudo generar el PDF";
      try { const err = await res.json(); detail = err.error || detail; } catch (e) { /* noop */ }
      throw new Error(detail);
    }
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `ventas_${state.desde}_${state.hasta}.pdf`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 3000);
    toast("PDF generado.");
  } catch (e) {
    toast(e.message, true);
  } finally {
    overlay(false);
  }
}

function checkHealth() {
  fetch("/api/health").then((r) => r.json()).then((h) => {
    $("#dbDot").className = "dot " + (h.ok ? "ok" : "");
    $("#dbText").textContent = h.ok ? `Profit · ${h.database}` : "Sin conexión";
  }).catch(() => { $("#dbText").textContent = "Servidor no disponible"; });
}

$("#aplicarFiltros").addEventListener("click", applyFilters);
$("#exportarPdf").addEventListener("click", exportarPDF);
$("#exportarClientePdf").addEventListener("click", exportarClientePDF);
$("#labFiltro").addEventListener("change", applyFilters);
$("#lineaFiltro").addEventListener("change", applyFilters);
$("#catFiltro").addEventListener("change", applyFilters);
$("#cerrarDrill").addEventListener("click", () => $("#drilldown").classList.remove("show"));

restoreFilters();
setDefaultDates();
checkHealth();
loadWarehouses().then(async () => {
  await loadFiltros();
  if (window.VENTAS_CLIENTE) state.cliente = window.VENTAS_CLIENTE;
  await loadAll();
});
