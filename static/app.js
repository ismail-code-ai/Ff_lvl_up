/* =========================================================================
   FreeFire BR Bot Panel - accounts page logic
   ========================================================================= */
const state = {
  accounts: {},   // uid -> account object
  regions: [],
  filter: "all",
  search: "",
  user: null,
  openLogs: {},   // uid -> bool
};

const HARVEST_GOAL = 3000; // session harvest gauge target (UI metric)

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s)
  .replace(/&/g, "\u0026amp;").replace(/</g, "\u0026lt;").replace(/>/g, "\u0026gt;")
  .replace(/"/g, "\u0026quot;");
const fmt = (n) => {
  if (n === null || n === undefined || n === "") return "0";
  const num = Number(n);
  if (isNaN(num)) return String(n);
  return num.toLocaleString("en-US");
};
const initials = (a) => {
  const s = (a.name || a.uid || "?").trim();
  return (s[0] || "?").toUpperCase();
};

/* ------------------------- toast ------------------------- */
let toastTimer = null;
function toast(msg, kind) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show " + (kind || "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = "toast " + (kind || "")), 2600);
}

/* ------------------------- api ------------------------- */
async function api(path, opts) {
  const res = await fetch(path, Object.assign(
    { headers: { "Content-Type": "application/json" } }, opts));
  if (res.status === 401) { window.location.href = "/login"; throw new Error("unauth"); }
  let data = null;
  try { data = await res.json(); } catch (e) {}
  if (!res.ok) throw new Error((data && data.detail) || res.statusText);
  return data;
}

/* ------------------------- status helpers ------------------------- */
const STATUS_LABEL = {
  idle: "PAUSED", stopped: "PAUSED", logging_in: "LOGGING IN",
  ready: "ONLINE", matchmaking: "MATCHMAKING", in_game: "IN MATCH", error: "ERROR",
};
function statusClass(a) {
  if (a.in_match) return "in_game";
  return a.status || "idle";
}
function statusText(a) {
  const c = statusClass(a);
  return STATUS_LABEL[c] || c.toUpperCase();
}

/* ------------------------- modal ------------------------- */
let modalResolve = null;
function openConfirm(opts) {
  $("modalIcon").textContent = opts.icon || "\u26A0\uFE0F";
  $("modalIcon").className = "modal-icon" + (opts.danger ? " danger" : "");
  $("modalTitle").textContent = opts.title || "Are you sure?";
  $("modalText").innerHTML = opts.text || "";
  const ok = $("modalConfirm");
  ok.textContent = opts.confirm || "Confirm";
  ok.className = "btn" + (opts.danger ? " danger" : "");
  $("modalOverlay").classList.add("show");
  return new Promise((resolve) => { modalResolve = resolve; });
}
function closeConfirm(val) {
  $("modalOverlay").classList.remove("show");
  if (modalResolve) { modalResolve(val); modalResolve = null; }
}
$("modalCancel").addEventListener("click", () => closeConfirm(false));
$("modalConfirm").addEventListener("click", () => closeConfirm(true));
$("modalOverlay").addEventListener("click", (e) => {
  if (e.target === $("modalOverlay")) closeConfirm(false);
});

/* ------------------------- rendering ------------------------- */
function matchesFilter(a) {
  const f = state.filter;
  if (f === "all") return true;
  if (f === "match") return a.in_match || a.status === "in_game" || a.status === "matchmaking";
  if (f === "online") return a.status === "ready";
  if (f === "paused") return ["idle", "stopped", "error"].includes(a.status) && !a.in_match;
  return true;
}
function matchesSearch(a) {
  const q = state.search.trim().toLowerCase();
  if (!q) return true;
  return (a.uid || "").toLowerCase().includes(q) || (a.name || "").toLowerCase().includes(q);
}

function cardHTML(a) {
  const cls = statusClass(a);
  const running = !!a.running;
  const base = a.base_exp != null ? a.base_exp : a.exp;
  const current = a.exp_current != null ? a.exp_current : a.exp;
  const gain = a.exp_gain || 0;
  const level = a.level != null ? a.level : "-";
  const nextLevel = (typeof a.level === "number") ? a.level + 1 : "-";
  const goal = Math.max(HARVEST_GOAL, Math.ceil(gain / 1000) * 1000);
  const pct = Math.min(100, goal ? (gain / goal) * 100 : 0);
  const logsOpen = !!state.openLogs[a.uid];

  return `
  <div class="card" data-uid="${esc(a.uid)}">
    <div class="card-top">
      <div class="avatar">${esc(initials(a))}</div>
      <div class="who">
        <div class="name">${esc(a.name || "Unknown")} <span class="region-badge">${esc(a.region || "ME")}</span></div>
        <div class="uid">UID: ${esc(a.uid)}</div>
      </div>
      <div class="status-pill ${cls}">${statusText(a)}</div>
    </div>

    <div class="stats">
      <div class="stat"><span class="k">Base EXP</span><span class="v">${fmt(base)}</span></div>
      <div class="stat"><span class="k">Current</span><span class="v">${fmt(current)}</span></div>
      <div class="stat"><span class="k">Harvested</span><span class="v pos">+${fmt(gain)}</span></div>
    </div>

    <div class="lvl-row">
      <span class="lvl">LVL${esc(level)}</span>
      <span class="arrow">&rarr;</span>
      <span class="lvl">LVL${esc(nextLevel)}</span>
      <span class="spacer"></span>
      <span class="perhr">${fmt(a.exp_per_hour)} EXP/hr</span>
    </div>
    <div class="progress"><div class="bar" style="width:${pct.toFixed(1)}%"></div></div>
    <div class="prog-meta"><span>${fmt(gain)} / ${fmt(goal)}</span><span>${pct.toFixed(1)}%</span></div>

    <div class="match-timer ${a.in_match ? "" : "hidden"}">
      <span class="lbl">&#9201; Match time</span>
      <span class="timer" data-start="${a.match_started_at || ""}">00:00</span>
      <span class="lbl">&middot; matches: ${fmt(a.total_matches)}</span>
    </div>
    <div class="match-timer ${(!a.in_match && a.total_matches) ? "" : "hidden"}">
      <span class="lbl">&#127942; Total matches played</span>
      <span>${fmt(a.total_matches)}</span>
    </div>

    <div class="actions">
      <button class="act ${running ? "stop" : "primary"}" data-act="${running ? "stop" : "start"}" title="${running ? "Stop" : "Start"}">
        ${running ? "&#9208;" : "&#9654;"}
      </button>
      <button class="act" data-act="refresh" title="Refresh profile">&#8635;</button>
      <button class="act" data-act="logs" title="Logs">&#128203;</button>
      <button class="act del" data-act="remove" title="Remove">&#128465;</button>
    </div>

    <div class="logs-panel ${logsOpen ? "" : "hidden"}" data-logs="${esc(a.uid)}">
      ${(a.logs || []).slice(-40).map((l) => `<div class="ln">${esc(l.msg)}</div>`).join("") || '<div class="ln">No logs yet.</div>'}
    </div>
  </div>`;
}

function render() {
  const list = Object.values(state.accounts)
    .filter(matchesFilter).filter(matchesSearch);

  // counts
  const all = Object.values(state.accounts);
  $("cAll").textContent = all.length;
  $("cMatch").textContent = all.filter((a) => a.in_match || a.status === "in_game" || a.status === "matchmaking").length;
  $("cOnline").textContent = all.filter((a) => a.status === "ready").length;
  $("cPaused").textContent = all.filter((a) => ["idle", "stopped", "error"].includes(a.status) && !a.in_match).length;

  const wrap = $("accounts");
  if (!list.length) {
    wrap.innerHTML = "";
    $("empty").style.display = "block";
    if (all.length && (state.search || state.filter !== "all")) {
      $("empty").querySelector("h3").textContent = "No matches";
      $("empty").querySelector("p").innerHTML = "Try a different search or filter.";
    } else {
      $("empty").querySelector("h3").textContent = "No accounts yet";
      $("empty").querySelector("p").innerHTML = "Add a FreeFire UID above to fetch its profile,<br/>then press play to start an auto BR match.";
    }
    updateGlobalStatus();
    return;
  }
  $("empty").style.display = "none";
  wrap.innerHTML = list.map(cardHTML).join("");
  updateGlobalStatus();
  updateTimers();
}

function updateGlobalStatus() {
  const all = Object.values(state.accounts);
  const running = all.filter((a) => a.running).length;
  const inMatch = all.filter((a) => a.in_match).length;
  const errs = all.filter((a) => a.status === "error").length;
  const wrap = $("globalStatusWrap");
  const label = $("globalStatus");
  wrap.className = "global-status";
  if (errs) { wrap.classList.add("err"); label.textContent = errs + " error" + (errs > 1 ? "s" : ""); }
  else if (inMatch) { wrap.classList.add("live"); label.textContent = inMatch + " in match"; }
  else if (running) { wrap.classList.add("busy"); label.textContent = running + " active"; }
  else { label.textContent = all.length ? "Idle" : "No accounts"; }
}

/* ------------------------- live timers ------------------------- */
function fmtClock(sec) {
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const p = (x) => String(x).padStart(2, "0");
  return h > 0 ? `${p(h)}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`;
}
function updateTimers() {
  const now = Date.now() / 1000;
  document.querySelectorAll(".timer[data-start]").forEach((el) => {
    const start = parseFloat(el.getAttribute("data-start"));
    if (!start) { el.textContent = "00:00"; return; }
    el.textContent = fmtClock(now - start);
  });
}
setInterval(updateTimers, 1000);

/* ------------------------- actions ------------------------- */
async function doStart(uid) {
  try {
    const r = await api(`/api/accounts/${uid}/start`, { method: "POST" });
    if (r.account) state.accounts[uid] = r.account;
    toast("Match started", "ok"); render();
  } catch (e) { toast(e.message, "err"); }
}
async function doStop(uid) {
  const a = state.accounts[uid] || {};
  const ok = await openConfirm({
    icon: "\u23F8\uFE0F", danger: true,
    title: "Stop match?",
    text: `Stop the BR session for <b>${esc(a.name || uid)}</b>?<br/>The bot will leave the match and stop pinging.`,
    confirm: "Stop match",
  });
  if (!ok) return;
  try {
    const r = await api(`/api/accounts/${uid}/stop`, { method: "POST" });
    if (r.account) state.accounts[uid] = r.account;
    toast("Match stopped", "ok"); render();
  } catch (e) { toast(e.message, "err"); }
}
async function doRefresh(uid) {
  toast("Refreshing profile...");
  try {
    const r = await api(`/api/accounts/${uid}/login`, { method: "POST" });
    if (r.account) state.accounts[uid] = r.account;
    toast("Profile refreshed", "ok"); render();
  } catch (e) { toast(e.message, "err"); }
}
async function doRemove(uid) {
  const a = state.accounts[uid] || {};
  const ok = await openConfirm({
    icon: "\uD83D\uDDD1\uFE0F", danger: true,
    title: "Remove account?",
    text: `Remove <b>${esc(a.name || uid)}</b> (UID ${esc(uid)}) from the panel?<br/>This cannot be undone.`,
    confirm: "Remove",
  });
  if (!ok) return;
  try {
    await api(`/api/accounts/${uid}`, { method: "DELETE" });
    delete state.accounts[uid];
    toast("Account removed", "ok"); render();
  } catch (e) { toast(e.message, "err"); }
}

$("accounts").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-act]");
  if (!btn) return;
  const card = e.target.closest(".card");
  if (!card) return;
  const uid = card.getAttribute("data-uid");
  const act = btn.getAttribute("data-act");
  if (act === "start") doStart(uid);
  else if (act === "stop") doStop(uid);
  else if (act === "refresh") doRefresh(uid);
  else if (act === "remove") doRemove(uid);
  else if (act === "logs") {
    state.openLogs[uid] = !state.openLogs[uid];
    const panel = card.querySelector(`.logs-panel[data-logs="${uid}"]`);
    if (panel) panel.classList.toggle("hidden", !state.openLogs[uid]);
  }
});

/* ------------------------- add account ------------------------- */
$("addHead").addEventListener("click", () => $("addPanel").classList.toggle("open"));
$("addForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const uid = $("uid").value.trim();
  const password = $("password").value;
  const region = $("region").value;
  if (!uid || !password) { toast("UID and password required", "err"); return; }
  const btn = $("addBtn");
  btn.disabled = true; btn.textContent = "Fetching profile...";
  try {
    const r = await api("/api/accounts", {
      method: "POST",
      body: JSON.stringify({ uid, password, region }),
    });
    if (r.account) state.accounts[r.account.uid] = r.account;
    $("uid").value = ""; $("password").value = "";
    toast("Account added", "ok");
    render();
  } catch (err) {
    toast(err.message, "err");
  } finally {
    btn.disabled = false; btn.textContent = "Add & Fetch Profile";
  }
});

/* ------------------------- search / tabs ------------------------- */
$("search").addEventListener("input", (e) => { state.search = e.target.value; render(); });
$("tabs").addEventListener("click", (e) => {
  const t = e.target.closest(".tab");
  if (!t) return;
  state.filter = t.getAttribute("data-filter");
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
  render();
});

/* ------------------------- logout ------------------------- */
$("logoutBtn").addEventListener("click", async () => {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (e) {}
  window.location.href = "/login";
});

/* ------------------------- websocket ------------------------- */
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => {
    let msg = null;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (msg.type === "snapshot") {
      state.accounts = {};
      (msg.accounts || []).forEach((a) => (state.accounts[a.uid] = a));
      render();
    } else if (msg.type === "account" && msg.account) {
      state.accounts[msg.account.uid] = msg.account;
      render();
    } else if (msg.type === "status" && msg.uid) {
      const a = state.accounts[msg.uid];
      if (a) { a.status = msg.status; render(); }
    } else if (msg.type === "removed" && msg.uid) {
      delete state.accounts[msg.uid]; render();
    } else if (msg.type === "log" && msg.uid) {
      const a = state.accounts[msg.uid];
      if (a) {
        a.logs = (a.logs || []).concat([{ t: msg.t, msg: msg.msg }]).slice(-60);
        if (state.openLogs[msg.uid]) {
          const panel = document.querySelector(`.logs-panel[data-logs="${msg.uid}"]`);
          if (panel) {
            const ln = document.createElement("div");
            ln.className = "ln"; ln.textContent = msg.msg;
            panel.appendChild(ln);
            panel.scrollTop = panel.scrollHeight;
          }
        }
      }
    }
  };
  ws.onclose = () => setTimeout(connectWS, 2500);
  ws.onerror = () => { try { ws.close(); } catch (e) {} };
}

/* ------------------------- init ------------------------- */
async function init() {
  try {
    const me = await api("/api/auth/me");
    state.user = me.user;
    $("userTag").textContent = "Signed in as " + me.user.username;
  } catch (e) { return; }

  try {
    const r = await api("/api/regions");
    state.regions = r.regions || [];
    const sel = $("region");
    sel.innerHTML = state.regions.map((x) => `<option value="${esc(x)}">${esc(x)}</option>`).join("");
    sel.value = "ME";
  } catch (e) {}

  try {
    const r = await api("/api/accounts");
    state.accounts = {};
    (r.accounts || []).forEach((a) => (state.accounts[a.uid] = a));
    render();
  } catch (e) {}

  connectWS();
}
init();
