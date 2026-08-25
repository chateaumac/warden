/* Warden UI — vanilla JS single page app. */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const STATUS = {
  compliant:    { label: "Compliant",     cls: "ok" },
  drifted:      { label: "Drifted",       cls: "warn" },
  unreachable:  { label: "Unreachable",   cls: "down" },
  unauthorized: { label: "Needs auth",    cls: "auth" },
  error:        { label: "Error",         cls: "down" },
  unknown:      { label: "Never audited", cls: "idle" },
};

const ACTION_STATE = {
  compliant:   "✓ compliant",
  fixed:       "⟳ fixed",
  drifted:     "✗ drifted",
  error:       "! error",
  na:          "not installed",
  skipped:     "skipped",
  disabled:    "off",
  unsupported: "unsupported",
  pending:     "—",
};

const state = {
  devices: [],
  profiles: [],
  selectedId: null,
  view: "home",          // home | add | device
  events: [],
  discovery: null,
  discoveryPoll: null,
};

/* ------------------------------------------------------------------ api */

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = typeof data.detail === "string" ? data.detail : `${res.status} ${res.statusText}`;
    throw new Error(msg);
  }
  return data;
}

/* ---------------------------------------------------------------- utils */

function toast(msg, type = "ok") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 4500);
}

function openModal(html) {
  closeModal();
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = `<div class="modal">${html}</div>`;
  overlay.addEventListener("click", (e) => { if (e.target === overlay) closeModal(); });
  $("#modal-root").appendChild(overlay);
  return overlay;
}
function closeModal() { $("#modal-root").innerHTML = ""; }

function timeAgo(iso) {
  if (!iso) return "never";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/* Minimal markdown for profile how-tos (escape first, then format). */
function mdLite(text) {
  // hard-wrapped continuation lines belong to the block above them
  const lines = [];
  for (const raw of (text || "").split("\n")) {
    const trimmed = raw.trim();
    const startsBlock = /^(#{2,}\s|>\s?|\d+\.\s|[-*]\s)/.test(trimmed) || trimmed === "";
    if (!startsBlock && lines.length && lines[lines.length - 1].trim() !== "") {
      lines[lines.length - 1] += " " + trimmed;
    } else {
      lines.push(raw);
    }
  }
  let html = "", list = null, quote = [];
  const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
  const flushQuote = () => {
    if (quote.length) { html += `<blockquote>${quote.join("<br>")}</blockquote>`; quote = []; }
  };
  for (let line of lines) {
    line = esc(line)
      .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
      .replace(/\*(.+?)\*/g, "<i>$1</i>")
      .replace(/`(.+?)`/g, "<code>$1</code>");
    if (/^&gt;\s?/.test(line)) { closeList(); quote.push(line.replace(/^&gt;\s?/, "")); continue; }
    flushQuote();
    const heading = line.match(/^(#{2,})\s+(.*)$/);
    if (heading) { closeList(); const lvl = Math.min(heading[1].length, 4); html += `<h${lvl}>${heading[2]}</h${lvl}>`; }
    else if (/^\d+\.\s+/.test(line)) {
      if (list !== "ol") { closeList(); html += "<ol>"; list = "ol"; }
      html += `<li>${line.replace(/^\d+\.\s+/, "")}</li>`;
    } else if (/^[-*]\s+/.test(line)) {
      if (list !== "ul") { closeList(); html += "<ul>"; list = "ul"; }
      html += `<li>${line.replace(/^[-*]\s+/, "")}</li>`;
    } else if (line.trim() === "") { closeList(); }
    else { closeList(); html += `<p>${line}</p>`; }
  }
  closeList();
  flushQuote();
  return `<div class="howto">${html}</div>`;
}

const profileById = (id) => state.profiles.find((p) => p.id === id) || null;
const deviceById = (id) => state.devices.find((d) => d.id === id) || null;

function actionEnabled(device, action) {
  const o = device.action_overrides || {};
  return o[action.id] !== undefined ? o[action.id] : (action.default !== false);
}

function lastResultFor(device, actionId) {
  return (device.last_result || []).find((r) => r.action_id === actionId) || null;
}

/* -------------------------------------------------------------- refresh */

async function refresh(initial = false) {
  try {
    state.devices = await api("/api/devices");
  } catch (e) {
    if (initial) toast(`Failed to load devices: ${e.message}`, "error");
    return;
  }
  renderSummary();
  renderSidebar();
  if (state.view === "device") {
    const dev = deviceById(state.selectedId);
    if (!dev) { state.view = "home"; renderMain(); return; }
    // don't clobber the form while the user is typing in it
    const active = document.activeElement;
    if (!active || !$("#main").contains(active) ||
        !["INPUT", "SELECT", "TEXTAREA"].includes(active.tagName)) {
      await loadEvents();
      renderMain();
    }
  }
}

async function loadEvents() {
  if (state.selectedId == null) return;
  try { state.events = await api(`/api/devices/${state.selectedId}/events?limit=60`); }
  catch { state.events = []; }
}

/* --------------------------------------------------------------- render */

function renderSummary() {
  const counts = {};
  for (const d of state.devices) {
    const key = d.enabled ? d.status : "paused";
    counts[key] = (counts[key] || 0) + 1;
  }
  const order = ["compliant", "drifted", "unauthorized", "unreachable", "error", "unknown", "paused"];
  $("#summary").innerHTML = order
    .filter((k) => counts[k])
    .map((k) => {
      const meta = STATUS[k] || { label: "Paused", cls: "idle" };
      return `<span class="sum"><span class="dot ${meta.cls}"></span>${counts[k]} ${esc(meta.label.toLowerCase())}</span>`;
    })
    .join("") || `<span class="sum muted">no devices yet</span>`;
}

function renderSidebar() {
  const list = $("#device-list");
  if (!state.devices.length) {
    list.innerHTML = `<div class="empty-list">No devices yet.<br>Hit <b>＋ Add device</b> to scan your LAN.</div>`;
    return;
  }
  list.innerHTML = state.devices.map((d) => {
    const meta = STATUS[d.status] || STATUS.unknown;
    const profile = profileById(d.profile_id);
    return `
      <div class="device-item ${d.id === state.selectedId && state.view === "device" ? "active" : ""} ${d.enabled ? "" : "paused"}" data-id="${d.id}">
        <span class="dot ${d.enabled ? meta.cls : "idle"}"></span>
        <div>
          <div class="di-name">${esc(d.name)}</div>
          <div class="di-sub">${esc(d.location || d.host)}${profile ? " · " + esc(profile.name) : ""}</div>
        </div>
        <div class="di-status">${d.enabled ? esc(meta.label) : "paused"}<br>${esc(timeAgo(d.last_audit))}</div>
      </div>`;
  }).join("");
  $$(".device-item", list).forEach((el) =>
    el.addEventListener("click", () => selectDevice(Number(el.dataset.id))));
}

function renderMain() {
  const main = $("#main");
  if (state.view === "add") { renderAdd(main); return; }
  if (state.view === "device") {
    const dev = deviceById(state.selectedId);
    if (dev) { renderDevice(main, dev); return; }
  }
  renderHome(main);
}

function renderHome(main) {
  main.innerHTML = `
    <div class="card">
      <h3>Welcome to Warden 🛡️</h3>
      <p class="hint">
        Warden keeps your devices' settings the way <i>you</i> set them. It connects to
        Android TVs (and anything else with ADB or SSH), audits ad &amp; telemetry
        settings against a profile, and re-applies them when a firmware update
        quietly flips them back.
      </p>
      <ol class="hint">
        <li><b>＋ Add device</b> — scan the LAN (mDNS + optional subnet sweep) or add by IP.</li>
        <li><b>Connect</b> — pair with the device; the UI walks you through enabling ADB.</li>
        <li><b>Audit</b> — see which settings have drifted from the profile.</li>
        <li>Flip the device to <b>Enforce</b> mode and Warden re-sanitizes it automatically.</li>
      </ol>
    </div>
    <div class="card">
      <h3>Profiles</h3>
      <div class="hint">${state.profiles.map((p) =>
        `<div style="margin-bottom:8px"><b>${esc(p.name)}</b> <span class="chip">${esc(p.connector)}</span><br>${esc(p.description)}</div>`).join("")}
      </div>
    </div>`;
}

/* ------------------------------------------------------------ add view */

function showAdd() {
  state.view = "add";
  renderSidebar();
  renderMain();
  refreshDiscovery();
}

function renderAdd(main) {
  const disc = state.discovery;
  const adbProfiles = state.profiles.filter((p) => p.connector === "adb");
  const profileOptions = (selected) => state.profiles.map((p) =>
    `<option value="${esc(p.id)}" ${p.id === selected ? "selected" : ""}>${esc(p.name)}</option>`).join("");

  let resultsHtml = "";
  if (disc) {
    if (disc.error) resultsHtml = `<p class="hint" style="color:var(--down)">Scan failed: ${esc(disc.error)}</p>`;
    else if (!disc.results.length) {
      resultsHtml = disc.scanning
        ? `<p class="hint"><span class="spinner"></span> Scanning the network…</p>`
        : (disc.finished_at ? `<p class="hint">Nothing found. Devices on other VLANs won't answer mDNS — try a subnet sweep.</p>` : "");
    } else {
      resultsHtml = `
        <table class="disc">
          <tr><th></th><th>Device</th><th>Address</th><th>Seen via</th><th>Profile</th><th></th></tr>
          ${disc.results.map((r, i) => `
            <tr>
              <td><span class="dot ${r.port_open ? "ok" : "idle"}" title="${r.port_open ? "ADB port 5555 open" : "ADB port closed — enable debugging first"}"></span></td>
              <td><b>${esc(r.name || "(unnamed)")}</b><br><span class="muted">${esc(r.model || "")}</span></td>
              <td class="mono">${esc(r.host)}</td>
              <td>${r.mdns_types.map((t) => `<span class="chip">${esc(t.replace("._tcp.local.", "").replace(/^_/, ""))}</span>`).join(" ")}
                  ${!r.mdns_types.length ? '<span class="chip">port sweep</span>' : ""}</td>
              <td><select data-disc-profile="${i}">${profileOptions(r.suggested_profile)}</select></td>
              <td>${r.already_added
                  ? `<span class="muted">added ✓</span>`
                  : `<button class="btn small primary" data-disc-add="${i}">Add</button>`}</td>
            </tr>`).join("")}
        </table>`;
    }
  }

  main.innerHTML = `
    <div class="card">
      <h3>Discover devices</h3>
      <div class="scan-bar">
        <button class="btn primary" id="btn-scan" ${disc?.scanning ? "disabled" : ""}>
          ${disc?.scanning ? '<span class="spinner"></span> Scanning…' : "🔍 Scan (mDNS)"}
        </button>
        <span class="muted">or sweep a subnet for open ADB ports:</span>
        <input type="text" id="scan-subnet" placeholder="10.10.20.0/24"
               value="${esc(disc?.default_subnet || "")}">
        <button class="btn" id="btn-sweep" ${disc?.scanning ? "disabled" : ""}>Sweep</button>
      </div>
      <p class="hint" style="margin-bottom:0">
        mDNS finds Chromecasts / Android TVs that announce themselves on this L2 segment.
        The sweep probes every host in a subnet for TCP 5555 — use it across VLANs.
      </p>
    </div>
    <div class="card" id="disc-results"><h3>Results</h3>${resultsHtml || '<p class="hint">Run a scan to find devices.</p>'}</div>
    <div class="card">
      <h3>Add manually</h3>
      <div class="form-grid">
        <label class="fld"><b>Host / IP</b><input type="text" id="add-host" placeholder="10.10.20.31"></label>
        <label class="fld"><b>Name</b><input type="text" id="add-name" placeholder="Living room TV"></label>
        <label class="fld"><b>Connector</b>
          <select id="add-connector">
            <option value="adb">ADB (Android TV)</option>
            <option value="ssh">SSH</option>
          </select>
        </label>
        <label class="fld"><b>Port</b><input type="number" id="add-port" placeholder="(profile default)"></label>
        <label class="fld full"><b>Profile</b><select id="add-profile">${profileOptions(adbProfiles[0]?.id)}</select></label>
        <label class="fld full" id="add-config-wrap" style="display:none"><b>Connector config (JSON)</b>
          <textarea id="add-config" placeholder='{"username": "root", "password": "..."}'></textarea>
        </label>
      </div>
      <button class="btn primary" id="btn-add-manual">Add device</button>
    </div>`;

  $("#btn-scan").addEventListener("click", () => startScan({ mdns: true }));
  $("#btn-sweep").addEventListener("click", () => {
    const subnet = $("#scan-subnet").value.trim();
    if (!subnet) { toast("Enter a subnet in CIDR form first", "warn"); return; }
    startScan({ mdns: false, subnet });
  });
  $("#add-connector").addEventListener("change", (e) => {
    $("#add-config-wrap").style.display = e.target.value === "ssh" ? "" : "none";
  });
  $("#btn-add-manual").addEventListener("click", addManual);
  $$("[data-disc-add]").forEach((btn) => btn.addEventListener("click", () => {
    const i = Number(btn.dataset.discAdd);
    const row = state.discovery.results[i];
    const profileId = $(`[data-disc-profile="${i}"]`).value;
    addDiscovered(row, profileId);
  }));
}

async function startScan(opts) {
  try {
    state.discovery = await api("/api/discovery/scan", { method: "POST", body: opts });
  } catch (e) { toast(`Scan failed: ${e.message}`, "error"); return; }
  renderMain();
  pollDiscovery();
}

function pollDiscovery() {
  clearInterval(state.discoveryPoll);
  state.discoveryPoll = setInterval(async () => {
    await refreshDiscovery();
    if (!state.discovery?.scanning) clearInterval(state.discoveryPoll);
  }, 1500);
}

async function refreshDiscovery() {
  try { state.discovery = await api("/api/discovery"); } catch { return; }
  if (state.view === "add") renderMain();
}

async function addDiscovered(row, profileId) {
  try {
    const device = await api("/api/devices", { method: "POST", body: {
      host: row.host, name: row.name || row.host,
      profile_id: profileId || null,
    }});
    toast(`Added ${device.name}`);
    await refresh();
    selectDevice(device.id);
  } catch (e) { toast(e.message, "error"); }
}

async function addManual() {
  const host = $("#add-host").value.trim();
  if (!host) { toast("Host is required", "warn"); return; }
  const body = {
    host,
    name: $("#add-name").value.trim() || null,
    connector: $("#add-connector").value,
    profile_id: $("#add-profile").value || null,
  };
  const port = $("#add-port").value.trim();
  if (port) body.port = Number(port);
  const configRaw = $("#add-config").value.trim();
  if (configRaw && body.connector === "ssh") {
    try { body.config = JSON.parse(configRaw); }
    catch { toast("Connector config is not valid JSON", "error"); return; }
  }
  try {
    const device = await api("/api/devices", { method: "POST", body });
    toast(`Added ${device.name}`);
    await refresh();
    selectDevice(device.id);
  } catch (e) { toast(e.message, "error"); }
}

/* --------------------------------------------------------- device view */

async function selectDevice(id) {
  state.selectedId = id;
  state.view = "device";
  await loadEvents();
  renderSidebar();
  renderMain();
}

function renderDevice(main, d) {
  const profile = profileById(d.profile_id);
  const meta = STATUS[d.status] || STATUS.unknown;
  const ident = [d.identity?.manufacturer, d.identity?.model, d.identity?.os]
    .filter(Boolean).join(" · ");
  const needsSetup = ["unknown", "unauthorized", "unreachable"].includes(d.status);

  const profileOptions = state.profiles.map((p) =>
    `<option value="${esc(p.id)}" ${p.id === d.profile_id ? "selected" : ""}>${esc(p.name)}</option>`).join("");

  const varsHtml = (profile?.vars || []).map((v) => `
    <label class="fld"><b>${esc(v.label || v.name)}</b>
      <input type="text" data-var="${esc(v.name)}" value="${esc((d.vars || {})[v.name] || "")}"
             placeholder="${esc(v.name)}">
      <span>${esc(v.description || "")}</span>
    </label>`).join("");

  const actionsHtml = (profile?.actions || []).map((a) => {
    const r = lastResultFor(d, a.id);
    const stateKey = r ? r.status : "pending";
    const target = a.type === "package_disable" ? a.package
      : a.type === "setting" ? `${a.namespace}/${a.key} = ${a.value}` : "shell";
    return `
      <div class="action-row">
        <input type="checkbox" data-action="${esc(a.id)}" ${actionEnabled(d, a) ? "checked" : ""}>
        <div>
          <div class="ar-name">${esc(a.name || a.id)}</div>
          <div class="ar-desc">${esc(a.description || "")}</div>
          <div class="ar-meta">${esc(target)}</div>
        </div>
        <div class="ar-state">
          <span class="state-tag ${esc(stateKey)}">${esc(ACTION_STATE[stateKey] || stateKey)}</span>
          ${r && (r.detail || r.observed) ? `<div class="ar-detail">${esc(r.detail || `observed: ${r.observed}`)}</div>` : ""}
        </div>
      </div>`;
  }).join("");

  const eventsHtml = state.events.map((e) => `
    <div class="event-row ${esc(e.level)}">
      <span class="ev-ts" title="${esc(e.ts)}">${esc(timeAgo(e.ts))}</span>
      <span>${esc(e.message)}</span>
    </div>`).join("") || `<p class="hint">No events yet.</p>`;

  main.innerHTML = `
    <div class="detail-head">
      <div>
        <h2>${esc(d.name)}</h2>
        ${d.location ? `<div class="sub">📍 ${esc(d.location)}</div>` : ""}
        <div class="sub">${esc(d.host)}:${d.port} · ${esc(d.connector)}${ident ? " · " + esc(ident) : ""}</div>
        <div style="margin-top:8px" class="row-gap">
          <span class="pill ${meta.cls}"><span class="dot ${meta.cls}"></span>${esc(meta.label)}</span>
          ${d.status_detail ? `<span class="muted" style="font-size:12px">${esc(d.status_detail)}</span>` : ""}
          <span class="muted" style="font-size:12px">audited ${esc(timeAgo(d.last_audit))}</span>
        </div>
      </div>
      <div class="head-actions">
        <button class="btn" id="btn-edit">✎ Edit</button>
        <button class="btn" id="btn-connect">🔌 Connect</button>
        <button class="btn" id="btn-audit">🔎 Audit now</button>
        <button class="btn warn" id="btn-enforce">⚡ Enforce now</button>
        <button class="btn danger" id="btn-delete">✕</button>
      </div>
    </div>

    <div class="card">
      <div class="row-gap">
        <span class="muted">Mode</span>
        <span class="seg">
          <button id="mode-monitor" class="${d.mode === "monitor" ? "active" : ""}">Monitor</button>
          <button id="mode-enforce" class="${d.mode === "enforce" ? "active warn-seg" : ""}">Enforce</button>
        </span>
        <span class="muted" style="font-size:12px">
          ${d.mode === "enforce"
            ? "Drift is re-applied automatically on every scheduled audit."
            : "Drift is only reported — flip to Enforce to auto-fix."}
        </span>
        <span style="margin-left:auto" class="row-gap">
          <span class="muted">Profile</span>
          <select id="sel-profile" style="width:auto">${profileOptions}</select>
          <button class="btn small ${d.enabled ? "" : "primary"}" id="btn-pause">${d.enabled ? "⏸ Pause" : "▶ Resume"}</button>
        </span>
      </div>
    </div>

    ${needsSetup && profile ? `
    <div class="card" style="border-color:var(--auth)">
      <details class="howto-box" ${d.status !== "compliant" ? "open" : ""}>
        <summary>📖 Setup: enable debugging on this device</summary>
        ${mdLite(profile.howto)}
        <p class="hint">Then hit <b>Connect</b> above and accept the dialog on the device's screen.</p>
      </details>
    </div>` : ""}

    ${varsHtml ? `
    <div class="card">
      <h3>Device variables</h3>
      ${varsHtml}
      <button class="btn small" id="btn-save-vars">Save variables</button>
    </div>` : ""}

    <div class="card">
      <h3>Actions ${profile ? `<span class="chip">${esc(profile.name)}</span>` : ""}</h3>
      ${actionsHtml || '<p class="hint">Assign a profile to manage actions on this device.</p>'}
    </div>

    <div class="card">
      <h3>Event log</h3>
      ${eventsHtml}
    </div>`;

  $("#btn-edit").addEventListener("click", () => editDevice(d));
  $("#btn-connect").addEventListener("click", () => connectDevice(d));
  $("#btn-audit").addEventListener("click", () => runOp(d.id, "audit", "Audit"));
  $("#btn-enforce").addEventListener("click", () => runOp(d.id, "enforce", "Enforce"));
  $("#btn-delete").addEventListener("click", () => deleteDevice(d));
  $("#mode-monitor").addEventListener("click", () => patchDevice(d.id, { mode: "monitor" }));
  $("#mode-enforce").addEventListener("click", () => patchDevice(d.id, { mode: "enforce" },
    "Enforce mode on — drift will be re-applied automatically"));
  $("#btn-pause").addEventListener("click", () => patchDevice(d.id, { enabled: !d.enabled }));
  $("#sel-profile").addEventListener("change", (e) => patchDevice(d.id, { profile_id: e.target.value }));
  const saveVarsBtn = $("#btn-save-vars");
  if (saveVarsBtn) saveVarsBtn.addEventListener("click", () => {
    const vars = { ...(d.vars || {}) };
    $$("[data-var]", main).forEach((input) => { vars[input.dataset.var] = input.value.trim(); });
    patchDevice(d.id, { vars }, "Variables saved");
  });
  $$("[data-action]", main).forEach((cb) => cb.addEventListener("change", () => {
    const overrides = { ...(d.action_overrides || {}), [cb.dataset.action]: cb.checked };
    patchDevice(d.id, { action_overrides: overrides });
  }));
}

function editDevice(d) {
  openModal(`
    <h3>Edit device</h3>
    <label class="fld"><b>Name</b>
      <input type="text" id="edit-name" value="${esc(d.name)}" placeholder="e.g. Regan's Office TV"></label>
    <label class="fld"><b>Location</b>
      <input type="text" id="edit-location" value="${esc(d.location || "")}" placeholder="e.g. Regan's Office">
      <span>Where the device physically lives — shown in the sidebar.</span>
    </label>
    <div class="row-gap" style="margin-top:12px">
      <button class="btn primary" id="edit-save">Save</button>
      <button class="btn ghost" id="edit-cancel">Cancel</button>
    </div>`);
  $("#edit-cancel").addEventListener("click", closeModal);
  $("#edit-name").focus();
  $("#edit-save").addEventListener("click", async () => {
    const name = $("#edit-name").value.trim();
    const location = $("#edit-location").value.trim();
    if (!name) { toast("Name can't be empty", "warn"); return; }
    closeModal();
    await patchDevice(d.id, { name, location }, "Device updated");
  });
}

async function patchDevice(id, fields, msg) {
  try {
    const updated = await api(`/api/devices/${id}`, { method: "PATCH", body: fields });
    const i = state.devices.findIndex((x) => x.id === id);
    if (i >= 0) state.devices[i] = updated;
    if (msg) toast(msg);
    renderSummary(); renderSidebar(); renderMain();
  } catch (e) { toast(e.message, "error"); }
}

async function runOp(id, op, label) {
  const btn = $(`#btn-${op}`);
  if (btn) { btn.disabled = true; btn.innerHTML = `<span class="spinner"></span> ${label}…`; }
  try {
    const result = await api(`/api/devices/${id}/${op}`, { method: "POST", body: {} });
    if (result.error) toast(`${label}: ${result.error}`, "warn");
    else {
      const fixedNote = result.fixed ? ` — re-applied ${result.fixed} setting(s)` : "";
      toast(`${label} finished: ${result.status_detail || result.status}${fixedNote}`,
            result.status === "compliant" ? "ok" : "warn");
    }
  } catch (e) { toast(`${label} failed: ${e.message}`, "error"); }
  await refresh();
  await loadEvents();
  renderMain();
}

async function connectDevice(d) {
  const profile = profileById(d.profile_id);
  const overlay = openModal(`
    <h3>🔌 Connecting to ${esc(d.name)}</h3>
    <p><span class="spinner"></span> Reaching ${esc(d.host)}:${d.port}…</p>
    <p class="hint"><b>Now watch the device's screen.</b> When the
      <i>"Allow USB debugging?"</i> dialog appears, tick
      <b>Always allow from this computer</b> and accept. Warden waits up to 30&nbsp;seconds.</p>
  `);
  let result;
  try {
    result = await api(`/api/devices/${d.id}/connect`, { method: "POST", body: { timeout_s: 30 } });
  } catch (e) {
    result = { ok: false, status: "error", error: e.message };
  }
  if (!$("#modal-root").contains(overlay)) { await refresh(); return; } // user closed it
  const modal = $(".modal", overlay);
  if (result.ok) {
    const ident = [result.identity?.manufacturer, result.identity?.model, result.identity?.os]
      .filter(Boolean).join(" · ");
    modal.innerHTML = `
      <h3>✅ Connected</h3>
      <p>${esc(d.name)} authorized Warden${ident ? ` — <b>${esc(ident)}</b>` : ""}.</p>
      ${result.audit ? `<p class="hint">First audit: <b>${esc(result.audit.status_detail || result.audit.status)}</b></p>` : ""}
      <button class="btn primary" onclick="document.getElementById('modal-root').innerHTML=''">Done</button>`;
  } else {
    const showHowto = result.status === "unreachable" && profile;
    modal.innerHTML = `
      <h3>${result.status === "unauthorized" ? "🔒 Not authorized yet" : "⚠️ Connection failed"}</h3>
      <p class="hint">${esc(result.error || "")}</p>
      ${showHowto ? mdLite(profile.howto) : ""}
      <div class="row-gap" style="margin-top:10px">
        <button class="btn primary" id="modal-retry">Try again</button>
        <button class="btn ghost" onclick="document.getElementById('modal-root').innerHTML=''">Close</button>
      </div>`;
    const retry = $("#modal-retry", modal);
    if (retry) retry.addEventListener("click", () => connectDevice(d));
  }
  await refresh();
}

async function deleteDevice(d) {
  if (!confirm(`Remove ${d.name} (${d.host}) from Warden?\nNothing is changed on the device itself.`)) return;
  try {
    await api(`/api/devices/${d.id}`, { method: "DELETE" });
    toast(`Removed ${d.name}`);
    state.view = "home";
    state.selectedId = null;
    await refresh();
    renderMain();
  } catch (e) { toast(e.message, "error"); }
}

/* ------------------------------------------------------------------ init */

async function init() {
  $("#btn-add").addEventListener("click", showAdd);
  try { state.profiles = await api("/api/profiles"); }
  catch (e) { toast(`Failed to load profiles: ${e.message}`, "error"); }
  await refresh(true);
  renderMain();
  setInterval(refresh, 5000);
}

init();
