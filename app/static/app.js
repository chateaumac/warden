/* Warden UI — unified settings enforcer and real-time content guard. */
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
  mainTab: "devices",    // devices | guard | inspector
  devices: [],
  profiles: [],
  channelRules: [],
  selectedId: null,
  view: "home",          // home | add | device
  events: [],
  discovery: null,
  discoveryPoll: null,
  guardState: {},
  inspectResult: null,
  inspectingId: null,
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

function mdLite(text) {
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
  return lines.map((l) => {
    const t = l.trim();
    if (t.startsWith("## ")) return `<h4>${esc(t.slice(3))}</h4>`;
    if (t.startsWith("> "))  return `<blockquote>${esc(t.slice(2))}</blockquote>`;
    if (/^\d+\.\s/.test(t)) return `<li>${esc(t.replace(/^\d+\.\s/, ""))}</li>`;
    if (t.startsWith("- ") || t.startsWith("* ")) return `<li>${esc(t.slice(2))}</li>`;
    if (!t) return "";
    return `<p>${esc(t)}</p>`;
  }).join("")
    .replace(/(<li>.*<\/li>)+/g, "<ol>$&</ol>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/\*([^*]+)\*/g, "<i>$1</i>");
}

/* ------------------------------------------------------------- lifecycle */

async function init() {
  $("#btn-add").addEventListener("click", () => showAdd());
  await loadProfiles();
  await loadChannelRules();
  await refresh();
  setInterval(refresh, 5000);
}

function switchMainTab(tab) {
  state.mainTab = tab;
  $$(".nav-tab").forEach(el => el.classList.remove("active"));
  const tabEl = $(`#tab-${tab}`);
  if (tabEl) tabEl.classList.add("active");
  
  const sidebar = $("#app-sidebar");
  if (tab === "devices") {
    sidebar.style.display = "block";
  } else {
    sidebar.style.display = "none";
  }
  
  renderMain();
}

async function loadProfiles() {
  try { state.profiles = await api("/api/profiles"); }
  catch (e) { toast("failed to load profiles: " + e.message, "down"); }
}

async function loadChannelRules() {
  try { state.channelRules = await api("/api/guard/rules"); }
  catch (e) { console.warn("failed to load rules", e); }
}

async function refresh() {
  try {
    state.devices = await api("/api/devices");
    if (state.selectedId && !deviceById(state.selectedId)) {
      state.selectedId = null;
      state.view = "home";
    }
    if (state.selectedId && state.mainTab === "devices" && state.view === "device") {
      try {
        state.guardState[state.selectedId] = await api(`/api/guard/devices/${state.selectedId}/state`);
      } catch {}
    }
  } catch (e) {
    console.warn("refresh failed", e);
  }
  renderSummary();
  renderSidebar();
  renderMain();
}

const deviceById = (id) => state.devices.find((d) => d.id === id);
const profileById = (id) => state.profiles.find((p) => p.id === id);

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
  if (state.mainTab === "guard") {
    renderGuardView(main);
    return;
  }
  if (state.mainTab === "inspector") {
    renderInspectorView(main);
    return;
  }

  // Devices tab
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
      <h3>Warden Governance &amp; Content Shield 🛡️</h3>
      <p class="hint">
        Warden protects your TVs on two distinct layers:
      </p>
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px; margin: 14px 0;">
        <div style="background:var(--bg2); padding:14px; border-radius:8px; border:1px solid var(--border);">
          <b>1. Device Sanitization &amp; Anti-ACR</b>
          <p class="hint" style="margin-top:6px;">
            Disables ACR (Samba TV), eliminates Google/Sony recommendation trackers, disables launcher ads, and forces OS-level Private DNS (DoT) via AdGuard/NextDNS.
          </p>
        </div>
        <div style="background:var(--bg2); padding:14px; border-radius:8px; border:1px solid var(--border);">
          <b>2. Real-Time Channel Guard</b>
          <p class="hint" style="margin-top:6px;">
            Zero-wear active media monitoring that detects restricted live streams (YouTube TV) and automatically skips past them or force-stops playback.
          </p>
        </div>
      </div>
    </div>
    <div class="card">
      <h3>Active Profiles</h3>
      <div class="hint">${state.profiles.map((p) =>
        `<div style="margin-bottom:8px"><b>${esc(p.name)}</b> <span class="chip">${esc(p.connector)}</span><br>${esc(p.description)}</div>`).join("")}
      </div>
    </div>`;
}

/* ------------------------------------------------------------ device view */

async function selectDevice(id) {
  state.selectedId = id;
  state.view = "device";
  state.mainTab = "devices";
  switchMainTab("devices");
  try {
    state.events = await api(`/api/devices/${id}/events?limit=40`);
    state.guardState[id] = await api(`/api/guard/devices/${id}/state`);
  } catch {}
  renderMain();
}

function renderDevice(main, dev) {
  const profile = profileById(dev.profile_id);
  const statusMeta = STATUS[dev.status] || STATUS.unknown;
  const gState = state.guardState[dev.id] || { state: "offline", is_snoozed: false, title: "", current_package: "" };

  const badgeCls = `badge-${gState.state || 'offline'}`;

  main.innerHTML = `
    <div class="device-header">
      <div>
        <h2>${esc(dev.name)}</h2>
        <div class="hint">${esc(dev.host)}:${dev.port} · ${esc(dev.location || "No location")}</div>
      </div>
      <div style="display:flex; gap:8px; align-items:center;">
        <button class="btn secondary" onclick="openLiveInspectorFor(${dev.id})">🔍 Live Inspect</button>
        <button class="btn ${dev.mode === 'enforce' ? 'primary' : 'secondary'}" onclick="toggleDeviceMode(${dev.id})">
          Mode: ${esc(dev.mode.toUpperCase())}
        </button>
        <button class="btn secondary" onclick="triggerAudit(${dev.id})">⚡ Audit Now</button>
        <button class="btn danger" onclick="deleteDeviceConfirm(${dev.id})">Delete</button>
      </div>
    </div>

    <!-- Live Channel Guard Card -->
    <div class="card" style="border-left: 4px solid var(--accent);">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div>
          <span class="guard-badge ${badgeCls}">${esc(gState.state)}</span>
          <span style="font-weight:600; margin-left:8px;">Channel Guard</span>
        </div>
        <div>
          ${gState.is_snoozed 
            ? `<button class="btn secondary sm" onclick="unsnoozeDevice(${dev.id})">⏰ Snoozed (${gState.snooze_remaining_s}s) - Resume</button>`
            : `<button class="btn secondary sm" onclick="snoozeDevicePrompt(${dev.id})">⏰ Snooze 30m</button>`}
        </div>
      </div>
      <div style="margin-top:12px; font-size:13px;">
        <div><b>Active App:</b> <code>${esc(gState.current_package || "None")}</code></div>
        <div><b>Current Playing:</b> ${esc(gState.title || "No active stream")} ${gState.subtitle ? `· <i>${esc(gState.subtitle)}</i>` : ""}</div>
        <div class="hint" style="margin-top:4px;">${esc(gState.status_detail || "")}</div>
        ${gState.last_action_name ? `<div style="margin-top:4px; color:var(--warn);">⚠️ Last Action: Enforced <b>${esc(gState.last_action_name)}</b> (${esc(gState.last_matched_rule)})</div>` : ""}
      </div>
    </div>

    <!-- Sanitization / Profile Card -->
    <div class="card">
      <h3>Sanitization &amp; Tracker Policy (${esc(profile ? profile.name : "No Profile")})</h3>
      <p class="hint">${esc(profile ? profile.description : "Assign a profile to enforce anti-tracking & ad blocking")}</p>
      
      <div style="margin-top:14px;">
        <table style="width:100%; border-collapse:collapse; font-size:13px;">
          <thead>
            <tr style="text-align:left; border-bottom:1px solid var(--border); color:var(--muted);">
              <th style="padding:6px 0;">Action / Tracker</th>
              <th style="padding:6px 0;">Status</th>
            </tr>
          </thead>
          <tbody>
            ${(dev.last_result || []).map(r => `
              <tr style="border-bottom:1px solid var(--border);">
                <td style="padding:8px 0;"><b>${esc(r.name)}</b><br><span class="hint">${esc(r.detail || r.observed || "")}</span></td>
                <td style="padding:8px 0;"><span class="chip">${esc(ACTION_STATE[r.status] || r.status)}</span></td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>

    <!-- Event History -->
    <div class="card">
      <h3>Recent Events</h3>
      <div style="max-height:220px; overflow-y:auto; font-size:12.5px;">
        ${state.events.map(e => `
          <div style="padding:6px 0; border-bottom:1px solid var(--border); display:flex; gap:8px;">
            <span class="hint">${esc(timeAgo(e.ts))}</span>
            <span class="chip">${esc(e.kind)}</span>
            <span>${esc(e.message)}</span>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

/* -------------------------------------------------------- channel guard view */

function renderGuardView(main) {
  main.innerHTML = `
    <div class="device-header">
      <div>
        <h2>Channel Guard &amp; Content Rules</h2>
        <div class="hint">Define restricted channels and programs with automatic skip or force-stop actions</div>
      </div>
      <div>
        <button class="btn primary" onclick="openAddRuleModal()">＋ Add Channel Rule</button>
      </div>
    </div>

    <div class="card">
      <table style="width:100%; border-collapse:collapse; font-size:13.5px;">
        <thead>
          <tr style="text-align:left; border-bottom:1px solid var(--border); color:var(--muted);">
            <th style="padding:8px 4px;">Status</th>
            <th style="padding:8px 4px;">Rule Name</th>
            <th style="padding:8px 4px;">Target Apps</th>
            <th style="padding:8px 4px;">Patterns</th>
            <th style="padding:8px 4px;">Action</th>
            <th style="padding:8px 4px; text-align:right;">Actions</th>
          </tr>
        </thead>
        <tbody>
          ${state.channelRules.map(r => `
            <tr style="border-bottom:1px solid var(--border);">
              <td style="padding:10px 4px;">
                <input type="checkbox" ${r.enabled ? "checked" : ""} onchange="toggleRuleEnabled(${r.id}, this.checked)">
              </td>
              <td style="padding:10px 4px;">
                <b>${esc(r.name)}</b>
                <div class="hint">${esc(r.description || "")}</div>
              </td>
              <td style="padding:10px 4px;">
                ${(r.target_packages || []).map(p => `<code>${esc(p.split('.').pop())}</code>`).join(", ")}
              </td>
              <td style="padding:10px 4px;">
                ${(r.patterns || []).map(p => `<span class="rule-pill">${esc(p)}</span>`).join("")}
              </td>
              <td style="padding:10px 4px;">
                <span class="chip">${esc(r.action)}</span>
              </td>
              <td style="padding:10px 4px; text-align:right;">
                <button class="btn secondary sm" onclick="openEditRuleModal(${r.id})">Edit</button>
                <button class="btn danger sm" onclick="deleteRuleConfirm(${r.id})">Delete</button>
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>

    <!-- Regex Tester Tool -->
    <div class="card">
      <h3>Pattern Tester</h3>
      <p class="hint">Test regular expressions against real channel metadata or UI dumps</p>
      <div style="display:flex; gap:10px; margin-top:10px;">
        <input type="text" id="test-pattern" placeholder="e.g. fox\\s*news|\\bFNC\\b" class="input" style="flex:1;">
        <input type="text" id="test-sample" placeholder="e.g. Live: Fox News Channel HD" class="input" style="flex:2;">
        <button class="btn secondary" onclick="runPatternTest()">Test Pattern</button>
      </div>
      <div id="test-result" style="margin-top:8px; font-family:var(--mono); font-size:12px;"></div>
    </div>
  `;
}

function openAddRuleModal() {
  openModal(`
    <h3>Add Channel Rule</h3>
    <form id="rule-form" onsubmit="handleRuleSubmit(event)">
      <label class="label">Rule Name</label>
      <input class="input block" name="name" placeholder="e.g. Skip Fox News" required>
      
      <label class="label" style="margin-top:10px;">Target Package(s)</label>
      <input class="input block" name="target_packages" value="com.google.android.youtube.tvunplugged" required>
      <div class="hint">Comma separated (e.g. com.google.android.youtube.tvunplugged for YouTube TV)</div>

      <label class="label" style="margin-top:10px;">Regex Patterns (one per line)</label>
      <textarea class="input block" name="patterns" rows="3" placeholder="fox\\s*news\n\\bFNC\\b" required></textarea>

      <label class="label" style="margin-top:10px;">Action</label>
      <select class="input block" name="action">
        <option value="auto_skip" selected>auto_skip (Advance channel / D-pad)</option>
        <option value="force_stop">force_stop (Kill streaming app)</option>
        <option value="back">back (Send BACK key twice)</option>
        <option value="home">home (Return to Google TV launcher)</option>
        <option value="mute">mute (Mute volume)</option>
      </select>

      <label class="label" style="margin-top:10px;">Description</label>
      <input class="input block" name="description" placeholder="Optional description">

      <div style="margin-top:16px; display:flex; justify-content:flex-end; gap:8px;">
        <button type="button" class="btn secondary" onclick="closeModal()">Cancel</button>
        <button type="submit" class="btn primary">Save Rule</button>
      </div>
    </form>
  `);
}

function openEditRuleModal(ruleId) {
  const r = state.channelRules.find(x => x.id === ruleId);
  if (!r) return;
  openModal(`
    <h3>Edit Channel Rule</h3>
    <form id="rule-form" onsubmit="handleRuleSubmit(event, ${ruleId})">
      <label class="label">Rule Name</label>
      <input class="input block" name="name" value="${esc(r.name)}" required>
      
      <label class="label" style="margin-top:10px;">Target Package(s)</label>
      <input class="input block" name="target_packages" value="${esc((r.target_packages || []).join(', '))}" required>

      <label class="label" style="margin-top:10px;">Regex Patterns (one per line)</label>
      <textarea class="input block" name="patterns" rows="3" required>${esc((r.patterns || []).join('\n'))}</textarea>

      <label class="label" style="margin-top:10px;">Action</label>
      <select class="input block" name="action">
        <option value="auto_skip" ${r.action === 'auto_skip' ? 'selected' : ''}>auto_skip (Advance channel / D-pad)</option>
        <option value="force_stop" ${r.action === 'force_stop' ? 'selected' : ''}>force_stop (Kill streaming app)</option>
        <option value="back" ${r.action === 'back' ? 'selected' : ''}>back (Send BACK key twice)</option>
        <option value="home" ${r.action === 'home' ? 'selected' : ''}>home (Return to Google TV launcher)</option>
        <option value="mute" ${r.action === 'mute' ? 'selected' : ''}>mute (Mute volume)</option>
      </select>

      <label class="label" style="margin-top:10px;">Description</label>
      <input class="input block" name="description" value="${esc(r.description || '')}">

      <div style="margin-top:16px; display:flex; justify-content:flex-end; gap:8px;">
        <button type="button" class="btn secondary" onclick="closeModal()">Cancel</button>
        <button type="submit" class="btn primary">Update Rule</button>
      </div>
    </form>
  `);
}

async function handleRuleSubmit(e, ruleId = null) {
  e.preventDefault();
  const form = e.target;
  const targetPkgs = form.target_packages.value.split(',').map(s => s.trim()).filter(Boolean);
  const patterns = form.patterns.value.split('\n').map(s => s.trim()).filter(Boolean);
  
  const payload = {
    name: form.name.value.trim(),
    target_packages: targetPkgs,
    patterns: patterns,
    action: form.action.value,
    description: form.description.value.trim(),
  };

  try {
    if (ruleId) {
      await api(`/api/guard/rules/${ruleId}`, { method: "PATCH", body: payload });
      toast("Rule updated");
    } else {
      await api("/api/guard/rules", { method: "POST", body: payload });
      toast("Rule created");
    }
    closeModal();
    await loadChannelRules();
    renderMain();
  } catch (err) {
    toast("Failed: " + err.message, "down");
  }
}

async function toggleRuleEnabled(id, enabled) {
  try {
    await api(`/api/guard/rules/${id}`, { method: "PATCH", body: { enabled } });
    await loadChannelRules();
    toast(`Rule ${enabled ? 'enabled' : 'disabled'}`);
  } catch (e) {
    toast(e.message, "down");
  }
}

async function deleteRuleConfirm(id) {
  if (!confirm("Are you sure you want to delete this rule?")) return;
  try {
    await api(`/api/guard/rules/${id}`, { method: "DELETE" });
    await loadChannelRules();
    renderMain();
    toast("Rule deleted");
  } catch (e) {
    toast(e.message, "down");
  }
}

async function runPatternTest() {
  const pattern = $("#test-pattern").value;
  const sample = $("#test-sample").value;
  const out = $("#test-result");
  if (!pattern || !sample) {
    out.innerHTML = `<span style="color:var(--warn)">Please enter both a pattern and sample text.</span>`;
    return;
  }
  try {
    const res = await api("/api/guard/test-pattern", { method: "POST", body: { pattern, sample_text: sample } });
    if (res.matched) {
      out.innerHTML = `<span style="color:var(--accent)">✓ Match Found: "${esc(res.matched_text)}"</span>`;
    } else {
      out.innerHTML = `<span style="color:var(--muted)">✗ No match</span>`;
    }
  } catch (e) {
    out.innerHTML = `<span style="color:var(--down)">Error: ${esc(e.message)}</span>`;
  }
}

/* ------------------------------------------------------- live inspector view */

function openLiveInspectorFor(deviceId) {
  state.inspectingId = deviceId;
  switchMainTab("inspector");
  runInspection();
}

function renderInspectorView(main) {
  const devOptions = state.devices.map(d => 
    `<option value="${d.id}" ${d.id === state.inspectingId ? 'selected' : ''}>${esc(d.name)} (${esc(d.host)})</option>`
  ).join("");

  const res = state.inspectResult;

  main.innerHTML = `
    <div class="device-header">
      <div>
        <h2>Live Diagnostic Payload Inspector</h2>
        <div class="hint">Inspect real-time media sessions and window state emitted by YouTube TV / Google TV</div>
      </div>
      <div style="display:flex; gap:8px;">
        <select id="inspect-select" class="input" style="width:240px;" onchange="state.inspectingId = Number(this.value)">
          <option value="">-- Select TV --</option>
          ${devOptions}
        </select>
        <button class="btn primary" onclick="runInspection()">🔍 Inspect TV Now</button>
      </div>
    </div>

    ${res ? `
      <!-- Parsed Diagnostics -->
      <div class="card">
        <h3>Inspection Result: ${esc(res.device_name || '')}</h3>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:10px;">
          <div>
            <div><b>Screen Interactive:</b> ${res.screen_on ? '<span style="color:var(--accent)">ON</span>' : '<span style="color:var(--idle)">OFF (Standby)</span>'}</div>
            <div><b>Foreground App:</b> <code>${esc(res.foreground_package || 'None')}</code></div>
            <div><b>Playback State:</b> ${esc(res.parsed_metadata?.playback_state || 'unknown')}</div>
          </div>
          <div>
            <div><b>Media Title:</b> <b>${esc(res.parsed_metadata?.title || 'None')}</b></div>
            <div><b>Subtitle / Artist:</b> ${esc(res.parsed_metadata?.subtitle || 'None')}</div>
            <div><b>Searchable Text:</b> <span class="hint">${esc(res.parsed_metadata?.full_text || '')}</span></div>
          </div>
        </div>

        ${res.matched_rule ? `
          <div style="margin-top:14px; padding:10px; background:var(--warn-dim); border:1px solid var(--warn); border-radius:6px;">
            ⚠️ <b>Rule Triggered:</b> ${esc(res.matched_rule.rule_name)} (Pattern: <code>${esc(res.matched_rule.pattern)}</code>)
            <br>Matched Text: <b>"${esc(res.matched_rule.matched_text)}"</b> &rarr; Action: <b>${esc(res.matched_rule.action)}</b>
          </div>
        ` : `<div style="margin-top:14px; color:var(--accent);">✓ No restricted rules matched current playback.</div>`}
      </div>

      <!-- Raw Payloads -->
      <div class="inspector-grid">
        <div class="card">
          <h4>Raw dumpsys media_session</h4>
          <div class="raw-dump-box">${esc(res.raw?.media_session || "No session data")}</div>
        </div>
        <div class="card">
          <h4>Raw dumpsys window focus</h4>
          <div class="raw-dump-box">${esc(res.raw?.window || "No window data")}</div>
        </div>
      </div>
    ` : `
      <div class="card empty-list">
        Select a TV above and click <b>Inspect TV Now</b> to query real-time dumpsys output.
      </div>
    `}
  `;
}

async function runInspection() {
  const select = $("#inspect-select");
  const devId = state.inspectingId || (select ? Number(select.value) : null);
  if (!devId) {
    toast("Please select a device to inspect", "warn");
    return;
  }
  state.inspectingId = devId;
  toast("Querying TV over ADB...", "ok");
  try {
    state.inspectResult = await api(`/api/guard/devices/${devId}/inspect`);
    if (!state.inspectResult.ok) {
      toast("Inspection failed: " + state.inspectResult.error, "down");
    }
    renderMain();
  } catch (e) {
    toast("Inspection error: " + e.message, "down");
  }
}

/* ------------------------------------------------------------- actions */

async function snoozeDevicePrompt(devId) {
  try {
    await api(`/api/guard/devices/${devId}/snooze`, { method: "POST", body: { duration_s: 1800 } });
    toast("Protection snoozed for 30 minutes");
    await refresh();
  } catch (e) {
    toast(e.message, "down");
  }
}

async function unsnoozeDevice(devId) {
  try {
    await api(`/api/guard/devices/${devId}/unsnooze`, { method: "POST" });
    toast("Protection resumed");
    await refresh();
  } catch (e) {
    toast(e.message, "down");
  }
}

async function toggleDeviceMode(devId) {
  const dev = deviceById(devId);
  if (!dev) return;
  const newMode = dev.mode === "enforce" ? "monitor" : "enforce";
  try {
    await api(`/api/devices/${devId}`, { method: "PATCH", body: { mode: newMode } });
    toast(`Mode set to ${newMode}`);
    await refresh();
  } catch (e) {
    toast(e.message, "down");
  }
}

async function triggerAudit(devId) {
  toast("Auditing device settings against profile...", "ok");
  try {
    await api(`/api/devices/${devId}/audit`, { method: "POST" });
    await refresh();
    toast("Audit complete");
  } catch (e) {
    toast("Audit failed: " + e.message, "down");
  }
}

async function deleteDeviceConfirm(devId) {
  if (!confirm("Are you sure you want to delete this device from Warden?")) return;
  try {
    await api(`/api/devices/${devId}`, { method: "DELETE" });
    state.selectedId = null;
    state.view = "home";
    await refresh();
    toast("Device deleted");
  } catch (e) {
    toast(e.message, "down");
  }
}

/* ------------------------------------------------------------ add device */

function showAdd() {
  state.view = "add";
  state.mainTab = "devices";
  switchMainTab("devices");
  renderMain();
  startDiscovery();
}

function renderAdd(main) {
  const profiles = state.profiles.map((p) =>
    `<option value="${p.id}">${esc(p.name)} (${esc(p.connector)})</option>`).join("");

  main.innerHTML = `
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <h3>Discovered Devices on LAN</h3>
        <button class="btn secondary sm" onclick="startDiscovery()">⟳ Scan LAN</button>
      </div>
      <div id="discovery-results" style="margin-top:12px;">
        <div class="hint">Scanning mDNS and subnet...</div>
      </div>
    </div>

    <div class="card">
      <h3>Add Device Manually</h3>
      <form id="manual-add-form" onsubmit="handleManualAdd(event)">
        <label class="label">Device Name</label>
        <input class="input block" name="name" placeholder="e.g. Living Room TCL" required>

        <label class="label" style="margin-top:10px;">Host IP</label>
        <input class="input block" name="host" placeholder="e.g. 10.10.40.50" required>

        <label class="label" style="margin-top:10px;">Port</label>
        <input class="input block" name="port" type="number" value="5555" required>

        <label class="label" style="margin-top:10px;">Profile</label>
        <select class="input block" name="profile_id">
          <option value="">-- Select Profile --</option>
          ${profiles}
        </select>

        <label class="label" style="margin-top:10px;">Location</label>
        <input class="input block" name="location" placeholder="e.g. Living Room">

        <div style="margin-top:16px; display:flex; gap:8px;">
          <button type="submit" class="btn primary">Add Device</button>
          <button type="button" class="btn secondary" onclick="selectDevice(null)">Cancel</button>
        </div>
      </form>
    </div>
  `;
}

async function startDiscovery() {
  try {
    const res = await api("/api/discovery/scan", { method: "POST", body: { mdns: true, duration_s: 4 } });
    pollDiscovery(res.scan_id);
  } catch (e) {
    $("#discovery-results").innerHTML = `<div class="hint">Discovery error: ${esc(e.message)}</div>`;
  }
}

async function pollDiscovery(scanId) {
  try {
    const res = await api(`/api/discovery/scan/${scanId}`);
    const results = res.results || [];
    const div = $("#discovery-results");
    if (!div) return;
    if (!results.length) {
      div.innerHTML = `<div class="hint">${res.active ? "Scanning..." : "No new devices discovered."}</div>`;
      if (res.active) setTimeout(() => pollDiscovery(scanId), 1500);
      return;
    }
    div.innerHTML = results.map(r => `
      <div style="display:flex; justify-content:space-between; align-items:center; padding:8px 0; border-bottom:1px solid var(--border);">
        <div>
          <b>${esc(r.name || r.host)}</b> &middot; <code>${esc(r.host)}:${r.port}</code>
          <div class="hint">${esc(r.suggested_profile_id || "Generic")} &middot; via ${esc(r.source)}</div>
        </div>
        <button class="btn primary sm" onclick="addDiscoveredDevice('${esc(r.host)}', ${r.port}, '${esc(r.name)}', '${esc(r.suggested_profile_id || '')}')">＋ Add</button>
      </div>
    `).join("");
    if (res.active) setTimeout(() => pollDiscovery(scanId), 1500);
  } catch {}
}

async function addDiscoveredDevice(host, port, name, profileId) {
  try {
    const dev = await api("/api/devices", {
      method: "POST",
      body: { host, port, name: name || host, profile_id: profileId || null, mode: "enforce" }
    });
    toast(`Added ${dev.name}`);
    await refresh();
    selectDevice(dev.id);
  } catch (e) {
    toast("Failed to add: " + e.message, "down");
  }
}

async function handleManualAdd(e) {
  e.preventDefault();
  const f = e.target;
  try {
    const dev = await api("/api/devices", {
      method: "POST",
      body: {
        name: f.name.value.trim(),
        host: f.host.value.trim(),
        port: Number(f.port.value),
        profile_id: f.profile_id.value || null,
        location: f.location.value.trim(),
        mode: "enforce",
      }
    });
    toast(`Added ${dev.name}`);
    await refresh();
    selectDevice(dev.id);
  } catch (err) {
    toast("Failed to add: " + err.message, "down");
  }
}

document.addEventListener("DOMContentLoaded", init);
