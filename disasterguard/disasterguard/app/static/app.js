const $ = (s) => document.querySelector(s);
const SEV = {1: "Low", 2: "Moderate", 3: "High", 4: "Critical"};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

async function api(path, body) {
  const r = await fetch("/api" + path, body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

document.querySelectorAll("#tabs button").forEach((b) => b.onclick = () => {
  document.querySelectorAll("#tabs button,.tab").forEach((e) => e.classList.remove("active"));
  b.classList.add("active"); $("#" + b.dataset.tab).classList.add("active");
  refresh();
});

async function refreshStatus() {
  try {
    const {llm} = await api("/status");
    const el = $("#llm");
    if (llm.online && llm.model_installed) { el.textContent = "🟢 Gemma online · " + llm.model; el.className = "pill on"; }
    else if (llm.online) { el.textContent = "🟠 model missing: ollama pull " + llm.model; el.className = "pill off"; }
    else { el.textContent = "🟠 AI offline · rule-based mode"; el.className = "pill off"; }
  } catch { $("#llm").textContent = "server unreachable"; }
}

async function refreshDash() {
  const [s, inc, logs] = await Promise.all([api("/stats"), api("/incidents"), api("/logs")]);
  $("#cards").innerHTML = [
    ["Open incidents", s.open], ["Critical", s.critical_open], ["People affected", s.people_affected], ["Resolved", s.resolved],
  ].map(([k, v]) => `<div class="card"><b>${v}</b><span>${k}</span></div>`).join("");
  $("#incidents").innerHTML = inc.length ? inc.map((i) => `
    <div class="inc s${i.severity} ${i.status}">
      <div class="top"><div><b>#${i.id} ${esc(i.type.replace("_", " "))}</b> · ${esc(i.location || "unknown location")}
        <span class="tag">${SEV[i.severity]}</span><span class="tag">${i.people} people</span><span class="tag">${esc(i.engine)}</span></div>
        ${i.status === "open" ? `<button class="small" onclick="resolveInc(${i.id})">Mark resolved</button>` : `<span class="tag">resolved</span>`}</div>
      <div class="muted">${esc(i.summary)}</div>
      <details><summary class="muted">Action plan</summary><pre>${esc(i.plan)}</pre></details>
    </div>`).join("") : `<p class="muted">No incidents yet. Use "Report incident".</p>`;
  $("#logs").innerHTML = logs.map((l) => `<div>${esc(l.ts)} · <b>${esc(l.agent)}</b> (${esc(l.engine)}) — ${esc(l.message)}</div>`).join("");
}

async function resolveInc(id) { await api(`/incidents/${id}/resolve`, {}); refresh(); }

async function refreshRes() {
  const r = await api("/resources");
  $("#resList").innerHTML = r.map((x) => `<div class="card" style="margin-bottom:8px"><b style="font-size:16px">${esc(x.name)}</b>
    <span>${x.available} / ${x.total} available · base: ${esc(x.base)}</span>
    <div class="bar"><i style="width:${(100 * x.available / x.total).toFixed(0)}%"></i></div></div>`).join("");
}

function refresh() {
  refreshStatus();
  const t = document.querySelector(".tab.active").id;
  if (t === "dash") refreshDash();
  if (t === "resources") refreshRes();
}

$("#reportBtn").onclick = async () => {
  const text = $("#reportText").value.trim(); if (!text) return;
  const b = $("#reportBtn"); b.disabled = true; b.textContent = "Agents working…";
  try {
    const r = await api("/incidents", {text});
    $("#reportOut").innerHTML = `<div class="out"><h2>Incident #${r.id}: ${esc(r.type.replace("_", " "))} · ${SEV[r.severity]} (${esc(r.engine)})</h2>
      <p class="muted">${esc(r.location || "unknown location")} · ${r.people} people · needs: ${esc(r.needs.join(", "))}</p>
      <b>Allocated</b><ul>${r.allocations.map((a) => `<li>${a.qty} × ${esc(a.resource)} (from ${esc(a.base)})</li>`).join("") || "<li>none</li>"}</ul>
      ${r.shortfalls.length ? `<b style="color:var(--s4)">Shortfalls</b><ul>${r.shortfalls.map((s) => `<li>${s.missing} × ${esc(s.kind)}</li>`).join("")}</ul>` : ""}
      <b>Action plan (${esc(r.plan_engine)})</b><pre>${esc(r.plan)}</pre></div>`;
    $("#reportText").value = "";
  } catch (e) { $("#reportOut").textContent = "Error: " + e.message; }
  b.disabled = false; b.textContent = "Submit to agents";
};

function addMsg(cls, text, extra) {
  const d = document.createElement("div"); d.className = "msg " + cls; d.textContent = text;
  if (extra) { const s = document.createElement("small"); s.textContent = extra; d.appendChild(s); }
  $("#chat").appendChild(d); $("#chat").scrollTop = 1e9;
}
async function ask() {
  const q = $("#q").value.trim(); if (!q) return;
  $("#q").value = ""; addMsg("u", q); $("#askBtn").disabled = true;
  try { const r = await api("/advise", {question: q}); addMsg("b", r.answer, "Sources: " + (r.sources.join("; ") || "none") + " · " + r.engine); }
  catch (e) { addMsg("b", "Error: " + e.message); }
  $("#askBtn").disabled = false;
}
$("#askBtn").onclick = ask;
$("#q").onkeydown = (e) => { if (e.key === "Enter") ask(); };

$("#srBtn").onclick = async () => {
  $("#srOut").textContent = "Generating…";
  try { $("#srOut").textContent = (await api("/sitrep")).markdown; } catch (e) { $("#srOut").textContent = "Error: " + e.message; }
};
$("#srCopy").onclick = () => navigator.clipboard && navigator.clipboard.writeText($("#srOut").textContent);

refresh(); setInterval(() => { if (document.hidden) return; refresh(); }, 15000);
