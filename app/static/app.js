// ChainSight dashboard (no build step: plain JS + Cytoscape.js)
const $ = (s, el = document) => el.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const short = (a) => (a && a.length > 16 ? `${a.slice(0, 8)}…${a.slice(-6)}` : a || "");
const fmt = (n) => Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: 2 });
const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString() : "-");

const TYPE_COLORS = {
  seed: "#f85149", source: "#a371f7", intermediary: "#6e7681", deposit: "#f0883e",
  exchange: "#3fb950", mixer: "#e6edf3", bridge: "#39c5cf",
};
const TYPE_NAMES = {
  seed: "Reported wallet", source: "Sender (victim side)", intermediary: "Intermediary",
  deposit: "Exchange deposit address", exchange: "Exchange wallet", mixer: "Mixer", bridge: "Bridge",
};

let CONFIG = null;
let current = null;
let pollTimer = null;
let cy = null;

const explorer = (chain, kind, id) => chain === "tron"
  ? `https://tronscan.org/#/${kind === "tx" ? "transaction" : "address"}/${id}`
  : `https://etherscan.io/${kind === "tx" ? "tx" : "address"}/${id}`;

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(msg);
  }
  return r.json();
}

async function init() {
  CONFIG = await api("/api/config");
  $("#modes").innerHTML = [
    `<span class="badge ${CONFIG.chains.tron}">Tron: ${CONFIG.chains.tron}</span>`,
    `<span class="badge ${CONFIG.chains.ethereum}">Ethereum: ${CONFIG.chains.ethereum}</span>`,
    `<span class="badge ${CONFIG.tinyfish}">TinyFish: ${CONFIG.tinyfish}</span>`,
  ].join("");
  $("#caseForm").addEventListener("submit", onSubmit);
  $("#demoBtn").addEventListener("click", () => {
    $("#chain").value = "tron";
    $("#address").value = CONFIG.demo_addresses.tron;
    $("#demo").checked = true;
    $("#caseForm").requestSubmit();
  });
  await refreshList();
  const id = location.hash.slice(1);
  if (id) openCase(id);
}

async function onSubmit(e) {
  e.preventDefault();
  $("#formError").textContent = "";
  const f = new FormData(e.target);
  const body = {
    chain: f.get("chain"), address: f.get("address").trim(),
    complaint_ref: f.get("complaint_ref"), p2p_url: f.get("p2p_url"), demo: f.get("demo") === "on",
  };
  try {
    const res = await api("/api/cases", { method: "POST", body: JSON.stringify(body) });
    await refreshList();
    location.hash = res.id;
  } catch (err) {
    $("#formError").textContent = err.message;
  }
}

async function refreshList() {
  const cases = await api("/api/cases");
  $("#caseList").innerHTML = cases.map((c) => `
    <li data-id="${esc(c.id)}" class="${current && current.id === c.id ? "active" : ""}">
      <div class="addr">${esc(short(c.address))} <span class="muted">${esc(c.chain)}</span></div>
      <div class="muted">${when(c.created_at)} · ${esc(c.status)}
        ${c.risk ? `<span class="badge ${esc(c.risk)}">${esc(c.risk)}</span>` : ""}
        ${c.demo ? `<span class="badge demo">demo</span>` : ""}</div>
    </li>`).join("") || `<li class="muted">No cases yet</li>`;
  document.querySelectorAll("#caseList li[data-id]").forEach((li) =>
    li.addEventListener("click", () => {
      if (location.hash.slice(1) === li.dataset.id) openCase(li.dataset.id);
      else location.hash = li.dataset.id;
    }));
}

async function openCase(id) {
  clearTimeout(pollTimer);
  $("#nodePanel").classList.add("hidden");
  const c = await api(`/api/cases/${id}`);
  current = c;
  if (c.status === "done" || c.status === "failed") {
    render(c);
    refreshList();
  } else {
    renderProgress(c);
    pollTimer = setTimeout(() => openCase(id), 800);
  }
}

function stepsHtml(c) {
  return `<div class="steps">${Object.values(c.steps).map((s) => `
    <div class="step ${esc(s.status)}">
      <div class="st">${esc(s.status)}</div><div>${esc(s.label)}</div>
      <div class="log">${s.log.slice(-4).map((l) => esc(l.msg)).join("<br>")}</div>
    </div>`).join("")}</div>`;
}

function headHtml(c) {
  return `
    <div class="casehead">
      <div>
        <h1>Case ${esc(c.id)} ${c.complaint_ref ? `<span class="muted">· ${esc(c.complaint_ref)}</span>` : ""}</h1>
        <div class="mono"><a href="${explorer(c.chain, "addr", c.address)}" target="_blank" rel="noopener">${esc(c.address)}</a>
          <span class="badge">${esc(c.chain)}</span>
          <span class="badge ${c.demo ? "demo" : "live"}">on-chain: ${c.demo ? "demo dataset" : "live"}</span>
          ${c.tinyfish_mode ? `<span class="badge ${esc(c.tinyfish_mode)}">TinyFish: ${esc(c.tinyfish_mode)}</span>` : ""}</div>
      </div>
      ${c.status === "done" ? `<div class="row">
        <a href="/api/cases/${c.id}/report.pdf"><button class="primary">Download PDF report</button></a>
        <a href="/api/cases/${c.id}/report.json"><button>JSON</button></a></div>` : ""}
    </div>`;
}

function renderProgress(c) {
  $("#main").innerHTML = headHtml(c) + stepsHtml(c) +
    `<div class="banner info">Tracing on-chain and running TinyFish agents in parallel…</div>`;
}

function render(c) {
  if (c.status === "failed") {
    $("#main").innerHTML = headHtml(c) + stepsHtml(c) +
      `<div class="banner err"><b>Case failed:</b> ${esc(c.error)}</div>`;
    return;
  }
  const r = c.risk, v = (c.vasps || [])[0];
  const demoBanner = (c.demo || c.tinyfish_mode === "simulated")
    ? `<div class="banner warn"><b>Demo data.</b> ${c.demo ? "On-chain flows are synthetic." : ""}
       ${c.tinyfish_mode === "simulated" ? "TinyFish replies are simulated (set TINYFISH_API_KEY for live agents)." : ""}</div>` : "";
  const tfCalls = c.tinyfish_calls || [];
  $("#main").innerHTML = `
    ${headHtml(c)}
    ${demoBanner}
    <div class="banner info"><b>Human review required.</b> ${esc(r.disclaimer)}</div>
    ${stepsHtml(c)}
    <div class="kpis">
      <div class="kpi"><div class="k">Risk lead score</div><div class="v"><span class="badge ${esc(r.level)}">${esc(r.level)}</span> ${r.score}/100</div><div class="s">${esc(r.category)}</div></div>
      <div class="kpi"><div class="k">Nearest VASP</div><div class="v">${v ? esc(v.name) : "None found"}</div><div class="s">${v ? `${v.hops} hop(s) · ${esc(v.address_type)}` : "within hop limit"}</div></div>
      <div class="kpi"><div class="k">Amount reaching VASP</div><div class="v">${v ? `${fmt(v.amount_estimate)} ${esc(v.token)}` : "-"}</div><div class="s">${v && v.minutes_from_seed != null ? `${(v.minutes_from_seed / 60).toFixed(1)} h after leaving wallet` : ""}</div></div>
      <div class="kpi"><div class="k">Graph</div><div class="v">${c.graph.nodes.length} addr</div><div class="s">${c.graph.edges.length} flows · ${c.patterns.length} pattern hits</div></div>
      <div class="kpi"><div class="k">Time to report (measured)</div><div class="v">${(c.timings.total || 0).toFixed(1)} s</div><div class="s">${tfCalls.length} TinyFish call(s)</div></div>
    </div>

    <div class="card section">
      <h2>Fund flow</h2>
      <div id="graph"></div>
      <div class="legend">${Object.entries(TYPE_COLORS).map(([t, col]) => `<span><i style="background:${col}"></i>${TYPE_NAMES[t]}</span>`).join("")}
        <span>Edge width ∝ amount · dashed border = trace limit reached · click a node for details</span></div>
    </div>

    <div class="grid2">
      <div class="card">
        <h2>Suggested next actions</h2>
        <ul class="clean">${c.next_actions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>
        <h3 style="margin-top:14px">Why this score</h3>
        <table>${r.reasons.map((x) => `<tr><td style="width:50px">+${x.points}</td><td>${esc(x.reason)}</td></tr>`).join("")}</table>
      </div>
      <div class="card">
        <h2>Laundering patterns</h2>
        <table><tr><th>Pattern</th><th>Detail</th><th>Address</th></tr>
        ${c.patterns.map((p) => `<tr><td><span class="pill">${esc(p.type.replace("_", " "))}</span></td><td>${esc(p.detail)}</td>
          <td class="mono">${p.addresses.slice(0, 1).map(short).map(esc).join("")}${p.addresses.length > 1 ? ` <span class="muted">+${p.addresses.length - 1}</span>` : ""}</td></tr>`).join("") || `<tr><td colspan="3" class="muted">None detected</td></tr>`}
        </table>
      </div>
    </div>

    <div class="card section">
      <h2>VASPs reached &amp; law-enforcement contact <span class="muted">(TinyFish Web Agent)</span></h2>
      ${(c.vasps || []).map((vv) => vaspHtml(c, vv)).join("") || `<p class="muted">No exchange reached within the hop limit.</p>`}
    </div>

    <div class="grid2">
      <div class="card">
        <h2>Off-chain intelligence <span class="muted">(TinyFish Search + Fetch)</span></h2>
        ${intelHtml(c)}
      </div>
      <div class="card">
        <h2>Evidence trail</h2>
        <table><tr><th>#</th><th>Captured (UTC)</th><th>Page</th><th>SHA-256</th></tr>
        ${(c.evidence || []).map((e) => `<tr><td>${e.id}</td><td>${esc(e.captured_at.replace("T", " ").replace("+00:00", ""))}</td>
          <td><a href="/api/cases/${c.id}/evidence/${e.id}" target="_blank">${esc(e.purpose)}</a><div class="muted mono">${esc(e.url)}</div><div class="muted">${esc(e.method)}</div></td>
          <td class="mono">${esc(e.sha256.slice(0, 16))}…</td></tr>`).join("") || `<tr><td colspan="4" class="muted">No snapshots</td></tr>`}
        </table>
      </div>
    </div>

    ${c.freeze_request ? `<div class="card section">
      <h2>Draft freeze / disclosure request</h2>
      <p class="muted">Review and edit before sending. Attach the PDF report as supporting evidence.</p>
      <textarea class="letter" id="letter">${esc(c.freeze_request)}</textarea>
      <div class="row" style="margin-top:8px"><button id="copyLetter">Copy</button>
        <a href="/api/cases/${c.id}/freeze-request.txt" target="_blank"><button>Open as text</button></a></div>
    </div>` : ""}

    <div class="card section">
      <h2>TinyFish call log</h2>
      <table><tr><th>Kind</th><th>Target</th><th>Result</th><th>Duration</th></tr>
      ${tfCalls.map((t) => `<tr><td><span class="pill">${esc(t.kind)}</span></td><td class="mono">${esc(t.target)}</td>
        <td>${t.ok ? `<span class="pill ok">ok</span>` : `<span class="pill bad">failed</span> ${esc(t.error || "")}`}${t.attempt ? ` <span class="muted">retry ${t.attempt}</span>` : ""}</td>
        <td>${(t.ms / 1000).toFixed(1)} s</td></tr>`).join("")}
      </table>
    </div>`;

  const btn = $("#copyLetter");
  if (btn) btn.addEventListener("click", () => {
    navigator.clipboard.writeText($("#letter").value);
    btn.textContent = "Copied";
  });
  drawGraph(c);
}

function vaspHtml(c, v) {
  const ct = (c.contacts || {})[v.entity] || {};
  const list = (xs) => (xs && xs.length ? `<ul class="clean">${xs.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<span class="muted">-</span>`);
  return `
    <div style="padding:10px 0;border-bottom:1px solid var(--border)">
      <h3>${esc(v.name)} ${ct.jurisdiction ? `<span class="muted">· ${esc(ct.jurisdiction)}</span>` : ""}
        ${ct.manual_review ? `<span class="pill warn">manual review</span>`
          : ct.simulated ? `<span class="pill warn">simulated agent reply</span>` : `<span class="pill ok">found by agent</span>`} ${ct.cached ? `<span class="pill">cached</span>` : ""}</h3>
      <div class="grid2" style="margin:0">
        <div>
          <div><span class="muted">${v.address_type === "deposit" ? "Deposit address (freeze target)" : "Exchange wallet"}:</span>
            <a class="mono" href="${explorer(c.chain, "addr", v.address)}" target="_blank" rel="noopener">${esc(v.address)}</a></div>
          <div><span class="muted">Path:</span> ${v.hops} hop(s) · ≈${fmt(v.amount_estimate)} ${esc(v.token)} · arrived ${when(v.arrived_ts)}</div>
          <div class="muted">Label source: ${esc(v.label_source)}</div>
        </div>
        <div>
          <div><span class="muted">Request portal:</span> ${ct.portal_url ? `<a href="${esc(ct.portal_url)}" target="_blank" rel="noopener">${esc(ct.portal_url)}</a>` : "-"}</div>
          <div><span class="muted">Contact email:</span> ${esc(ct.contact_email || "not published")}</div>
          ${ct.streaming_url ? `<div><a href="${esc(ct.streaming_url)}" target="_blank" rel="noopener">Watch the agent's browser session</a></div>` : ""}
          ${ct.error ? `<div class="error">${esc(ct.error)}</div>` : ""}
        </div>
      </div>
      <div class="grid2" style="margin:8px 0 0">
        <div><span class="muted">Process</span>${list(ct.process_steps)}</div>
        <div><span class="muted">Required documents</span>${list(ct.required_documents)}${ct.notes ? `<div class="muted">${esc(ct.notes)}</div>` : ""}</div>
      </div>
    </div>`;
}

function intelHtml(c) {
  const i = c.intel || {};
  const ms = (i.mentions || []).map((m) => `
    <div class="mention">
      ${m.confirmed ? `<span class="pill ok">wallet confirmed on page</span>` : `<span class="pill">unconfirmed</span>`}
      ${Object.keys(m.typology || {}).map((t) => `<span class="pill">${esc(t.replace("_", " "))}</span>`).join("")}
      <div><a href="${esc(m.url)}" target="_blank" rel="noopener">${esc(m.title || m.url)}</a> <span class="muted">${esc(m.site || "")}</span></div>
      <div class="muted">${esc(m.context || "")}</div>
    </div>`).join("");
  const p2p = i.p2p ? `<h3 style="margin-top:12px">P2P seller profile</h3>${i.p2p.error
    ? `<div class="error">${esc(i.p2p.error)} (manual review)</div>`
    : `<pre class="mono">${esc(JSON.stringify(i.p2p.profile, null, 2))}</pre>`}` : "";
  return `${ms || `<p class="muted">No public mentions found.</p>`}
    <div style="margin-top:10px"><span class="muted">Sanctions / abuse lists:</span>
      ${i.sanctions ? `<span class="pill bad">${esc(i.sanctions.list)}: ${esc(i.sanctions.entity)}</span>` : `<span class="pill ok">no match</span>`}</div>
    ${(i.errors || []).length ? `<div class="error">${i.errors.map(esc).join("<br>")}</div>` : ""}
    ${p2p}`;
}

function drawGraph(c) {
  const byHop = {};
  c.graph.nodes.forEach((n) => { const h = n.hop ?? 0; (byHop[h] = byHop[h] || []).push(n); });
  const pos = {};
  Object.entries(byHop).forEach(([h, ns]) => {
    ns.sort((a, b) => (a.type === "seed" ? -1 : 0) - (b.type === "seed" ? -1 : 0));
    ns.forEach((n, i) => { pos[n.id] = { x: Number(h) * 170, y: (i - (ns.length - 1) / 2) * 80 }; });
  });
  const maxAmt = Math.max(1, ...c.graph.edges.map((e) => e.amount));
  const onPath = new Set();
  const v = (c.vasps || [])[0];
  if (v) v.path.slice(1).forEach((t, i) => onPath.add(`${v.path[i]}>${t}`));

  cy = cytoscape({
    container: $("#graph"),
    elements: [
      ...c.graph.nodes.map((n) => ({ data: { id: n.id, ...n,
        name: n.type === "seed" ? "Reported" : (n.label ? n.label.replace(/\s*\(.*\)/, "") : short(n.id)),
        color: TYPE_COLORS[n.type] || "#6e7681" }, position: pos[n.id] })),
      ...c.graph.edges.map((e) => ({ data: { id: `${e.source}>${e.target}`, ...e,
        w: 1 + 7 * (e.amount / maxAmt), lbl: `${fmt(e.amount)} ${e.token}`,
        hl: onPath.has(`${e.source}>${e.target}`) ? 1 : 0 } })),
    ],
    layout: { name: "preset", fit: true, padding: 30 },
    wheelSensitivity: 0.2,
    style: [
      { selector: "node", style: { "background-color": "data(color)", label: "data(name)", color: "#c9d1d9",
        "font-size": 9, "text-valign": "bottom", "text-margin-y": 4, width: 22, height: 22,
        "text-wrap": "ellipsis", "text-max-width": 120 } },
      { selector: "node[type='seed']", style: { width: 32, height: 32, "border-width": 3, "border-color": "#fff" } },
      { selector: "node[type='deposit'], node[type='exchange']", style: { width: 30, height: 30, shape: "round-rectangle" } },
      { selector: "node[?frontier]", style: { "border-width": 2, "border-style": "dashed", "border-color": "#d29922" } },
      { selector: "edge", style: { width: "data(w)", "line-color": "#3d4757", "target-arrow-color": "#3d4757",
        "target-arrow-shape": "triangle", "curve-style": "bezier", label: "data(lbl)", "font-size": 8,
        color: "#8b949e", "text-rotation": "autorotate", "text-background-color": "#0d1117", "text-background-opacity": 1 } },
      { selector: "edge[hl = 1]", style: { "line-color": "#f0883e", "target-arrow-color": "#f0883e" } },
      { selector: ":selected", style: { "border-width": 3, "border-color": "#4f8cff" } },
    ],
  });
  cy.on("tap", "node", (evt) => showNode(c, evt.target.data()));
}

function showNode(c, n) {
  const ins = c.graph.edges.filter((e) => e.target === n.id);
  const outs = c.graph.edges.filter((e) => e.source === n.id);
  const row = (e, other) => `<tr><td class="mono">${esc(short(other))}</td><td>${fmt(e.amount)} ${esc(e.token)}</td><td>${when(e.first_ts)}</td>
    <td>${e.tx_hashes.slice(0, 2).map((h) => `<a href="${explorer(c.chain, "tx", h)}" target="_blank" rel="noopener">tx</a>`).join(" ")}</td></tr>`;
  const p = $("#nodePanel");
  p.innerHTML = `<button class="close">×</button>
    <h3>${esc(TYPE_NAMES[n.type] || n.type)}</h3>
    <div class="mono"><a href="${explorer(c.chain, "addr", n.id)}" target="_blank" rel="noopener">${esc(n.id)}</a></div>
    ${n.label ? `<div>${esc(n.label)} <span class="muted">(${esc(n.label_source || "")})</span></div>` : ""}
    <div style="margin:6px 0">Hop ${n.hop ?? "-"} ${(n.patterns || []).map((x) => `<span class="pill">${esc(x.replace("_", " "))}</span>`).join("")}
      ${n.sanctioned ? `<span class="pill bad">sanctioned: ${esc(n.sanctioned.list)}</span>` : ""}
      ${n.frontier ? `<span class="pill warn">trace limit reached</span>` : ""}
      ${n.omitted_branches ? `<span class="pill warn">${n.omitted_branches} smaller branch(es) not followed</span>` : ""}</div>
    ${ins.length ? `<h3>Received</h3><table>${ins.map((e) => row(e, e.source)).join("")}</table>` : ""}
    ${outs.length ? `<h3 style="margin-top:8px">Sent</h3><table>${outs.map((e) => row(e, e.target)).join("")}</table>` : ""}`;
  p.classList.remove("hidden");
  $(".close", p).addEventListener("click", () => p.classList.add("hidden"));
}

window.addEventListener("hashchange", () => { const id = location.hash.slice(1); if (id) openCase(id); });
init().catch((e) => { $("#main").innerHTML = `<div class="banner err">${esc(e.message)}</div>`; });
