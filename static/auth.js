/* FreeFire BR Bot - login / register page logic */
const $ = (id) => document.getElementById(id);
let mode = "login";

function toast(msg, kind) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show " + (kind || "");
  setTimeout(() => (t.className = "toast " + (kind || "")), 2600);
}

function setMode(next) {
  mode = next;
  const isLogin = next === "login";
  $("tabLogin").classList.toggle("active", isLogin);
  $("tabRegister").classList.toggle("active", !isLogin);
  $("confirmField").style.display = isLogin ? "none" : "block";
  $("submitBtn").textContent = isLogin ? "Login" : "Create account";
  $("password").setAttribute("autocomplete", isLogin ? "current-password" : "new-password");
  $("authFoot").innerHTML = isLogin
    ? 'No account yet? Tap <b>Register</b> above.'
    : 'Already registered? Tap <b>Login</b> above.';
  $("authMsg").textContent = "";
  $("authMsg").className = "auth-msg";
}

function showMsg(text, ok) {
  const m = $("authMsg");
  m.textContent = text;
  m.className = "auth-msg" + (ok ? " ok" : "");
}

async function submit(e) {
  e.preventDefault();
  const username = $("username").value.trim();
  const password = $("password").value;
  const confirm = $("confirm").value;

  if (!username || !password) { showMsg("Please fill in username and password."); return; }
  if (mode === "register" && password !== confirm) { showMsg("Passwords do not match."); return; }
  let setupToken = "";
  if (mode === "register") {
    setupToken = window.prompt("Enter the server setup token (FF_SETUP_TOKEN):") || "";
    if (!setupToken) { showMsg("Setup token is required for initial registration."); return; }
  }

  const btn = $("submitBtn");
  btn.disabled = true;
  const label = mode === "login" ? "Login" : "Create account";
  btn.textContent = "Please wait...";
  try {
    const res = await fetch("/api/auth/" + (mode === "login" ? "login" : "register"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, setup_token: setupToken }),
    });
    let data = null;
    try { data = await res.json(); } catch (err) {}
    if (!res.ok) throw new Error((data && data.detail) || "Something went wrong");
    showMsg(mode === "login" ? "Welcome back!" : "Account created!", true);
    toast(mode === "login" ? "Logged in" : "Registered", "ok");
    setTimeout(() => (window.location.href = "/"), 450);
  } catch (err) {
    showMsg(err.message || "Failed");
    btn.disabled = false;
    btn.textContent = label;
  }
}

$("tabLogin").addEventListener("click", () => setMode("login"));
$("tabRegister").addEventListener("click", () => setMode("register"));
$("authForm").addEventListener("submit", submit);
setMode("login");
