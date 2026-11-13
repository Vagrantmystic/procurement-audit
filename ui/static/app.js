const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const fmtMoney = (v, dec = 0) => v == null || isNaN(v) ? "—" :
  "$" + Number(v).toLocaleString("es-VE", { minimumFractionDigits: dec, maximumFractionDigits: dec });
const fmtNum = (v, dec = 0) => v == null || isNaN(v) ? "—" :
  Number(v).toLocaleString("es-VE", { minimumFractionDigits: dec, maximumFractionDigits: dec });
const fmtPct = (v, dec = 1) => v == null || isNaN(v) ? "—" : Number(v).toFixed(dec) + "%";
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function badge(value) {
  const key = String(value || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  const cls = key.includes("critic") ? "critica" : key.includes("alta") ? "alta" :
    key.includes("media") ? "media" : key.includes("baja") ? "baja" : "revisar";
  return `<span class="badge ${cls}">${esc(value)}</span>`;
}

function kpi(label, value, note = "", tone = "") {
  return `<div class="kpi ${tone}"><div class="label">${esc(label)}</div>
    <div class="value">${value}</div><div class="note">${esc(note)}</div></div>`;
}

function table(headers, rows, aligns = []) {
  if (!rows || !rows.length) return `<p class="empty">Sin datos.</p>`;
  const th = headers.map((h, i) => `<th class="${aligns[i] === "num" ? "num" : ""}">${esc(h)}</th>`).join("");
  const tr = rows.map((r) => `<tr>${r.map((c, i) =>
    `<td class="${aligns[i] === "num" ? "num" : ""}">${c}</td>`).join("")}</tr>`).join("");
  return `<table class="table"><thead><tr>${th}</tr></thead><tbody>${tr}</tbody></table>`;
}

function showToast(msg, error = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast show" + (error ? " error" : "");
  setTimeout(() => el.className = "toast", 4200);
}

function overlay(show, title = "Procesando...", text = "Consultando Profit. No cierre esta pestaña.") {
  const el = $("#overlay");
  if (show) {
    $("#overlayTitle").textContent = title;
    $("#overlayText").textContent = text;
    el.classList.add("show");
  } else el.classList.remove("show");
}

function go(tab) {
  $$(".nav").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $$(".tab").forEach((s) => s.classList.toggle("active", s.id === `tab-${tab}`));
  const titles = {
    resumen: ["Centro de compras", "Profit 2K12 · solo lectura · datos en vivo"],
    auditoria: ["Auditoría de compras", "Compra vs venta, margen, quiebres y sobrecompra"],
    ordenes: ["Órdenes de compra", "Demanda, inventario y tránsito reales"],
    ml: ["Modelos ML / DL", "Validación temporal y comparación con el enfoque clásico"],
    config: ["Configuración", "Parámetros y conexión"],
  };
  $("#pageTitle").textContent = titles[tab][0];
  $("#pageSub").textContent = titles[tab][1];
  try { history.replaceState(null, "", "#" + tab); } catch (e) { /* noop */ }
}
window.go = go;

async function runJob(kind, url, body, title, text) {
  overlay(true, title, text);
  try {
    const res = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "No se pudo iniciar el proceso");
    pollJob(data.job, kind);
  } catch (err) {
    overlay(false);
    showToast(err.message, true);
  }
}

async function pollJob(id, kind) {
  try {
    const res = await fetch(`/api/job/${id}`);
    const job = await res.json();
    if (job.status === "running") {
      setTimeout(() => pollJob(id, kind), 1600);
      return;
    }
    overlay(false);
    if (job.status === "error") {
      showToast("Error: " + job.error, true);
      return;
    }
    $("#lastRun").textContent = `Última ejecución: ${job.finished}`;
    if (kind === "audit") renderAudit(job.result);
    if (kind === "orders") renderOrders(job.result);
    if (kind === "ml") renderML(job.result);
    showToast("Proceso completado.");
  } catch (err) {
    overlay(false);
    showToast(err.message, true);
  }
}

function filesRow(data) {
  const links = [];
  if (data.excel) links.push(`<a class="btn small" href="/download/${encodeURIComponent(data.excel)}">Descargar Excel</a>`);
  if (data.dashboard) links.push(`<a class="btn small" href="/files/${encodeURIComponent(data.dashboard)}" target="_blank">Abrir dashboard</a>`);
  return links.length ? `<div class="actions">${links.join("")}</div>` : "";
}

function renderAudit(data) {
  const m = data.metrics || {};
  const kpis = [
    kpi("Compra de mercancía", fmtMoney(m.compra_usd), `${data.year}`),
    kpi("Venta de mercancía", fmtMoney(m.venta_usd), `${data.year}`),
    kpi("Margen bruto", fmtPct(m.margen_pct), fmtMoney(m.margen_usd), "good"),
    kpi("Cobertura", `${fmtNum(m.cobertura_dias)} días`, "stock + tránsito", "amber"),
    kpi("Quiebres reales", fmtNum(m.quiebres), `${fmtMoney(m.venta_quiebre_usd)} venta`, "warn"),
    kpi("Sobrecompra", fmtNum(m.sobrecompra), `${fmtMoney(m.capital_exceso_usd)} exceso`, "warn"),
    kpi("Sin venta", fmtNum(m.sin_venta), `${fmtMoney(m.sin_venta_stock_usd)} inmovilizado`),
    kpi("Venta inmediata en riesgo", fmtMoney(m.venta_en_riesgo_usd), "faltante de lead time", "warn"),
  ].join("");

  const findings = (data.findings || []).map((f) => `<li>${esc(f)}</li>`).join("");
  const topMissing = table(
    ["Estado", "Producto", "Stock", "Tránsito", "Venta 2026", "Faltante USD"],
    (data.top_missing || []).map((r) => [badge(r.Estado), esc(r.Descripcion), fmtNum(r.Stock_Actual),
      fmtNum(r.Stock_Transito), fmtMoney(r.Venta_USD), fmtMoney(r.Faltante_USD)]),
    ["", "", "num", "num", "num", "num"]);
  const topOver = table(
    ["Producto", "Stock", "Tránsito", "Cobertura (d)", "Exceso USD"],
    (data.top_over || []).map((r) => [esc(r.Descripcion), fmtNum(r.Stock_Actual), fmtNum(r.Stock_Transito),
      fmtNum(r.Cobertura_Disponible_Dias), fmtMoney(r.Exceso_USD)]),
    ["", "num", "num", "num", "num"]);
  const suppliers = table(
    ["Proveedor", "Compra", "Share", "SKU"],
    (data.suppliers || []).map((r) => [esc(r.Proveedor), fmtMoney(r.Compra_USD), fmtPct(r.Share_Pct), fmtNum(r.Productos)]),
    ["", "num", "num", "num"]);

  $("#auditResult").innerHTML = `
    <div class="kpis">${kpis}</div>
    <article class="card"><h2>Veredicto</h2><ul class="findings">${findings}</ul>${filesRow(data)}</article>
    <div class="grid two">
      <article class="card"><h2>Quiebres y faltantes prioritarios</h2>${topMissing}</article>
      <article class="card"><h2>Capital inmovilizado (top)</h2>${topOver}</article>
    </div>
    <article class="card"><h2>Proveedores principales</h2>${suppliers}</article>`;
  $("#resumenCards").innerHTML = `
    <article class="card"><h3>Auditoría ${data.year}</h3>
      <div class="kpi good"><div class="label">Margen bruto</div><div class="value">${fmtPct(m.margen_pct)}</div></div>
      <p>Compra ${fmtMoney(m.compra_usd)} · venta ${fmtMoney(m.venta_usd)} · ${fmtNum(m.quiebres)} quiebres.</p>
      <button class="btn small" onclick="go('auditoria')">Ver auditoría</button></article>
    <article class="card"><h3>Estado</h3><p>${fmtNum(m.sobrecompra)} SKU sobrecomprados · ${fmtNum(m.sin_venta)} sin venta.</p>
      <p>Capital en exceso: <strong>${fmtMoney(m.capital_exceso_usd)}</strong>.</p></article>`;
  $("#resumenFindings").hidden = false;
  $("#lastFindings").innerHTML = findings;
}

function renderOrders(data) {
  const s = data.summary || {};
  const kpis = [
    kpi("Órdenes sugeridas", fmtNum(s.ordenes), "líneas"),
    kpi("Unidades", fmtNum(s.unidades), "a comprar"),
    kpi("Inversión", fmtMoney(s.inversion), "estimada USD"),
    kpi("Críticas", fmtNum(s.criticas), "requieren acción", "warn"),
    kpi("Excluidos EOL", fmtNum(s.excluidos_eol), "no comprar", "warn"),
    kpi("LLE pendientes", fmtNum(s.lle_pendientes), "bloquean OC", "warn"),
    kpi("Precios por aprobar", fmtNum(s.aprobaciones), "negociar/revisar", "amber"),
  ].join("");
  const priority = table(
    ["Prioridad", "Órdenes", "Unidades", "Inversión"],
    (data.by_priority || []).map((r) => [badge(r.Prioridad), fmtNum(r.n), fmtNum(r.u), fmtMoney(r.c)]),
    ["", "num", "num", "num"]);
  const top = table(
    ["Prioridad", "Producto", "Proveedor", "Stock", "Cantidad", "Costo", "Cobertura (d)", "Precio"],
    (data.top || []).map((r) => [badge(r.Prioridad), esc(r.Descripcion), esc(r.Proveedor),
      r.Stock_Actual == null ? "—" : fmtNum(r.Stock_Actual), fmtNum(r.Cantidad_Sugerida),
      fmtMoney(r.Costo_Total_USD), r.Dias_Cobertura == null ? "—" : fmtNum(r.Dias_Cobertura),
      esc(r.Precio_Estado || "—")]),
    ["", "", "", "num", "num", "num", "num", "center"]);
  const excluded = table(
    ["Producto", "Estado", "Motivo", "Stock", "Valor"],
    (data.excluded || []).map((r) => [esc(r.Descripcion), esc(r.Estado_Ciclo_Vida),
      esc(r.Motivo_Bloqueo), fmtNum(r.Stock_Actual), fmtMoney(r.Valor_Stock)]),
    ["", "center", "", "num", "num"]);
  const price = table(
    ["Producto", "Estado", "Var %", "Impacto", "Aprobador", "Bloquea"],
    (data.price_alerts || []).map((r) => [esc(r.Descripcion), esc(r.Precio_Estado),
      fmtPct(r.Var_Precio_Pct), fmtMoney(r.Impacto_USD), esc(r.Aprobador),
      r.Bloqueada_OC ? "Sí" : "No"]),
    ["", "center", "num", "num", "", "center"]);
  const wape = data.metrics ? `<p class="hint">WAPE A/B ${fmtPct(data.metrics.wape_ab)} · validación cruzada ${fmtPct(data.metrics.wape_ab_cv)}</p>` : "";
  $("#ordersResult").innerHTML = `
    <div class="kpis">${kpis}</div>
    <article class="card"><h2>Resumen por prioridad</h2>${priority}${wape}${filesRow(data)}</article>
    <div class="grid two">
      <article class="card"><h2>Excluidos por ciclo de vida</h2>${excluded}</article>
      <article class="card"><h2>Control de precios</h2>${price}</article>
    </div>
    <article class="card"><h2>Primeras 15 órdenes</h2>${top}</article>`;
}

function renderML(data) {
  const metrics = data.metrics || {};
  const rows = Object.entries(metrics).filter(([k]) => k !== "mejor").map(([k, v]) => [
    k, fmtPct(v.wape), fmtPct(v.bias), fmtPct(v.hit25), fmtNum(v.mae, 2)]);
  const best = metrics.mejor || "—";
  const bestWape = metrics[best] ? metrics[best].wape : null;
  const ma8 = metrics["MA8 × 4"] ? metrics["MA8 × 4"].wape : null;
  const delta = (bestWape != null && ma8) ? (ma8 - bestWape) : null;
  const byVol = table(
    ["Tramo (u/mes)", "SKU", "WAPE ML", "WAPE DL", "WAPE MA4", "WAPE MA8", "WAPE híbrido"],
    (data.by_volume || []).map((r) => [esc(r.Tramo), fmtNum(r.Productos), fmtPct(r.WAPE_ML),
      fmtPct(r.WAPE_DL), fmtPct(r.WAPE_MA4), fmtPct(r.WAPE_MA8), fmtPct(r.WAPE_Hibrido)]),
    ["", "num", "num", "num", "num", "num", "num"]);
  const imp = table(
    ["Variable", "Importancia"],
    (data.feature_importance || []).map((r) => [esc(r.Feature), r.Importancia == null ? "—" : fmtNum(r.Importancia, 3)]),
    ["", "num"]);
  $("#mlResult").innerHTML = `
    <div class="kpis">
      ${kpi("Mejor modelo", esc(best), fmtPct(bestWape) + " WAPE", "good")}
      ${kpi("MA8 (referencia)", fmtPct(ma8), "media móvil 8 semanas")}
      ${kpi("Diferencia vs MA8", delta == null ? "—" : (delta >= 0 ? "−" : "+") + fmtNum(Math.abs(delta), 1) + " pp", "puntos de WAPE", delta >= 0 ? "good" : "warn")}
      ${kpi("Tiempo de entrenamiento", fmtNum(data.elapsed, 1) + " s", "ML + red neuronal")}
    </div>
    <article class="card"><h2>Comparación de modelos</h2>
      ${table(["Modelo", "WAPE", "Sesgo", "Hit ±25%", "MAE"], rows, ["", "num", "num", "num", "num"])}
      <p class="hint">La media móvil es un baseline muy fuerte en demanda intermitente. El modelo híbrido usa la red neuronal solo en SKU de volumen medio/alto; en el resto mantiene MA8. En este portafolio la mejora es marginal: la mayor ganancia vendrá de registrar quiebres y lead times reales.</p>
    </article>
    <article class="card"><h2>Desempeño por volumen</h2>${byVol}</article>
    <article class="card"><h2>Variables más relevantes</h2>${imp}</article>`;
}

function loadConfig() {
  fetch("/api/config").then((r) => r.json()).then((c) => {
    $("#configTable").innerHTML = `<tbody>
      <tr><td>Nivel de servicio</td><td>${c.service_level}</td></tr>
      <tr><td>Lead time por defecto</td><td>${c.lead_time} días</td></tr>
      <tr><td>Horizonte de pronóstico</td><td>${c.horizon} días</td></tr>
      <tr><td>Cobertura objetivo auditoría</td><td>${c.target_coverage} días</td></tr>
      <tr><td>Base de la OC</td><td>${c.oc_base} (LLE no suma)</td></tr>
      <tr><td>Política de tránsito</td><td>${c.lle_policy}</td></tr>
      <tr><td>Techo de aumento de costo</td><td>${c.price_max}%</td></tr>
      <tr><td>Fuente</td><td>Profit 2K12 (solo lectura)</td></tr></tbody>`;
    $("#cfgServer").textContent = c.server || "—";
    $("#cfgDatabase").textContent = c.database || "—";
  }).catch(() => {});
}

function loadWarehouses() {
  const box = $("#warehouseList");
  if (!box) return;
  fetch("/api/warehouses").then((r) => r.json()).then((data) => {
    if (data.error) throw new Error(data.error);
    const items = data.available || [];
    if (!items.length) {
      box.innerHTML = `<span class="empty">No se pudieron cargar almacenes desde Profit.</span>`;
      return;
    }
    box.innerHTML = items.map((w) => {
      const flags = [
        w.noventa ? "noventa" : "", w.nocompra ? "no compra" : "",
        w.alm_temp ? "temporal" : "", w.produccion ? "producción" : "",
      ].filter(Boolean).map((f) => `<span class="wh-flag">${esc(f)}</span>`).join("");
      return `<label class="wh-item"><input type="checkbox" value="${esc(w.codigo)}" ${w.seleccionado ? "checked" : ""}>
        <span><span class="wh-name">${esc(w.codigo)} · ${esc(w.nombre)}</span>
        <span class="wh-meta">Sucursal ${esc(w.sucursal || "—")} ${flags}</span></span></label>`;
    }).join("");
    const mode = data.summary && data.summary.purchase_mode;
    if (mode) $("#warehousePurchaseMode").value = mode;
    const summary = data.summary || {};
    $("#warehouseStatus").textContent = summary.selected_labels
      ? `Ventas/stock: ${summary.selected_labels.join(", ")} · Compras: ${(summary.purchase_labels || []).join(", ")}`
      : "Seleccione al menos un almacén.";
  }).catch((e) => {
    box.innerHTML = `<span class="empty">Error: ${esc(e.message)}</span>`;
  });
}

function saveWarehouses() {
  const selected = $$("#warehouseList input:checked").map((el) => el.value);
  if (!selected.length) { showToast("Seleccione al menos un almacén.", true); return; }
  const status = $("#warehouseStatus");
  status.textContent = "Guardando...";
  fetch("/api/warehouses", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selected, purchase_mode: $("#warehousePurchaseMode").value }),
  }).then(async (r) => {
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || "No se pudo guardar");
    showToast("Almacenes actualizados. Se aplican en la próxima ejecución.");
    const s = data.summary || {};
    status.textContent = `Ventas/stock: ${(s.selected_labels || []).join(", ")} · Compras: ${(s.purchase_labels || []).join(", ")}`;
    loadWarehouses();
  }).catch((e) => { status.textContent = e.message; showToast(e.message, true); });
}

function checkHealth() {
  fetch("/api/health").then((r) => r.json()).then((h) => {
    const dot = $("#dbStatus .dot"), text = $("#dbText");
    if (h.ok) {
      dot.className = "dot ok";
      text.textContent = `Profit · ${h.database}`;
      text.title = `${h.server} · ${h.user}`;
    } else {
      dot.className = "dot bad";
      text.textContent = "Sin conexión a Profit";
      text.title = h.error || "";
    }
  }).catch(() => {
    $("#dbStatus .dot").className = "dot bad";
    $("#dbText").textContent = "Servidor no disponible";
  });
}

function loadLast() {
  fetch("/api/last").then((r) => r.json()).then((data) => {
    if (data.audit) renderAudit(data.audit);
    if (data.orders) renderOrders(data.orders);
    if (data.ml) renderML(data.ml);
    const generated = (data.orders && data.orders.generated) || (data.audit && data.audit.generated) || (data.ml && data.ml.generated);
    if (generated) $("#lastRun").textContent = `Última ejecución: ${generated}`;
  }).catch(() => {});
}

$$(".nav").forEach((b) => b.addEventListener("click", () => go(b.dataset.tab)));

$("#auditForm").addEventListener("submit", (e) => {
  e.preventDefault();
  runJob("audit", "/api/audit", {
    year: Number($("#auditYear").value),
    as_of: $("#auditAsOf").value.trim() || null,
  }, "Auditando compras...", "Cruzando compras, ventas, stock y tránsito de Profit.");
});

$("#ordersForm").addEventListener("submit", (e) => {
  e.preventDefault();
  runJob("orders", "/api/orders", {
    as_of: $("#ordersAsOf").value.trim() || null,
    service_level: $("#ordersService").value,
    lead_time: $("#ordersLead").value,
  }, "Generando órdenes...", "Pronosticando demanda y calculando cobertura con stock real.");
});

$("#mlBtn").addEventListener("click", () => {
  runJob("ml", "/api/ml", {}, "Entrenando modelos...",
    "Comparando media móvil, gradient boosting y red neuronal.");
});

const saveWarehousesBtn = $("#saveWarehouses");
if (saveWarehousesBtn) saveWarehousesBtn.addEventListener("click", saveWarehouses);

go(location.hash ? location.hash.slice(1) : "resumen");
checkHealth();
loadConfig();
loadWarehouses();
loadLast();
