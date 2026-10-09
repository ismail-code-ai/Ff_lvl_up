/* =========================================================================
   FreeFire BR Bot Panel - dashboard page logic
   ========================================================================= */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s)
  .replace(/&/g, "\u0026amp;").replace(/</g, "\u0026lt;").replace(/>/g, "\u0026gt;").replace(/"/g, "\u0026quot;");
const fmt = (n) => {
  if (n === null || n === undefined || n === "") return "0";
  const num = Number(n);
  return isNaN(num) ? String(n) : num.toLocaleString("en-US");
};
const initials = (a) => ((a.name || a.uid || "?").trim()[0] || "?").toUpperCase();

function toast(msg, kind) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show " + (kind || "");
  setTimeout(() => (t.className = "toast " + (kind || "")), 2600);
}

async function api(path, opts) {
  const res = await fetch(path, Object.assign(
    { headers: { "Content-Type": "application/json" } }, opts));
  if (res.status === 401) { window.location.href = "/login"; throw new Error("unauth"); }
  let data = null;
  try { data = await res.json(); } catch (e) {}
  if (!res.ok) throw new Error((data && data.detail) || res.statusText);
  return data;
}

const STATUS_LABEL = {
  idle: "Paused", stopped: "Paused", logging_in: "Logging in",
  ready: "Online", matchmaking: "Matchmaking", in_game: "In Match", error: "Error",
};
function statusClass(a) {
  if (a.in_match) return "in_game";
  return a.status || "idle";
}
function statusText(a) { return STATUS_LABEL[statusClass(a)] || statusClass(a); }

function bar(label, value, total, cls) {
  const pct = total ? Math.min(100, (value / total) * 100) : 0;
  return `<div class="stat-bar ${cls || ""}">
    <div class="top"><span class="n">${esc(label)}</span><span>${fmt(value)}</span></div>
    <div class="track"><div class="fill" style="width:${pct.toFixed(1)}%"></div></div>
  </div>`;
}

function renderDash(d) {
  const accs = d.accounts || [];
  $("kAccounts").textContent = fmt(d.total_accounts);
  $("kExpGain").textContent = "+" + fmt(d.total_exp_gained);
  $("kMatches").textContent = fmt(d.total_matches);
  $("kExp").textContent = fmt(d.total_exp);
  $("kInMatch").textContent = fmt(d.in_match);
  $("kOnline").textContent = fmt(d.online);

  $("heroSub").innerHTML =
    `Avg level <b>${fmt(d.avg_level)}</b> &middot; Max level <b>${fmt(d.max_level)}</b> ` +
    `&middot; <b>${fmt(d.running)}</b> active session${d.running === 1 ? "" : "s"}`;

  const total = accs.length || 1;
  $("statusBars").innerHTML = [
    bar("In Match", d.in_match, total, "green"),
    bar("Matchmaking", d.matchmaking, total, "amber"),
    bar("Online / Ready", d.online, total, "green"),
    bar("Paused", d.paused, total, ""),
    bar("Errors", d.errors, total, "amber"),
  ].join("");

  const list = $("dashList");
  if (!accs.length) {
    list.innerHTML = "";
    $("dashEmpty").style.display = "block";
    return;
  }
  $("dashEmpty").style.display = "none";
  list.innerHTML = accs.map((a) => `
    <div class="dash-row">
      <div class="avatar">${esc(initials(a))}</div>
      <div class="info">
        <div class="nm">${esc(a.name || "Unknown")} <span class="region-badge">${esc(a.region || "ME")}</span></div>
        <div class="sub">UID ${esc(a.uid)} &middot; LVL ${esc(a.level != null ? a.level : "-")} &middot; ${statusText(a)}</div>
      </div>
      <div class="right">
        <div class="g">+${fmt(a.exp_gain)}</div>
        <div class="m">${fmt(a.total_matches)} matches</div>
      </div>
    </div>`).join("");
}

function updateGlobal() {
  const wrap = $("globalStatusWrap"), label = $("globalStatus");
  wrap.className = "global-status";
  label.textContent = "Live";
  wrap.classList.add("live");
}

async function load() {
  try {
    const me = await api("/api/auth/me");
    $("userTag").textContent = "Signed in as " + me.user.username;
  } catch (e) { return; }
  try {
    const d = await api("/api/dashboard");
    renderDash(d);
    $("loading").style.display = "none";
    $("content").style.display = "block";
    updateGlobal();
  } catch (e) {
    toast(e.message, "err");
  }
}

$("logoutBtn").addEventListener("click", async () => {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (e) {}
  window.location.href = "/login";
});

load();
setInterval(load, 5000);
