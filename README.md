# FreeFire BR Bot — Web Panel

Converted the original `lvl.py` CLI bot into a **web control panel** (FastAPI + WebSocket)
with a modern dark UI. Add multiple accounts (UID / password / region), auto-fetch each
account's details, and start/stop BR matches with live status (`Matchmaking` → `In Game`).

---

## 0. Round 4 — new UI, Dashboard & Login/Register

This round adds three things on top of the working bot:

### 0.1 Mobile-first UI redesign
The panel was rebuilt to look like the reference mobile design (dark purple/blue theme):

* **Header** with brand + live global status ("2 in match").
* **Search bar** (`Search UID or nickname...`) and **filter tabs**
  `All / Match / Online / Paused` with live counts.
* **Player cards** showing: avatar (initial), nickname, **region badge**, UID,
  status pill (`IN MATCH / MATCHMAKING / ONLINE / PAUSED / ERROR`),
  **Base EXP / Current / Harvested** stats, **LVLx → LVLx+1** range,
  a **progress bar** (session harvest gauge) with `x / goal` + `%`,
  **EXP/hr**, a **live match timer** and total matches, plus action buttons
  (▶/⏸ start-stop, ⟳ refresh, 📋 logs, 🗑 remove).
* **Bottom navigation**: Accounts / Dashboard / Logout.
* A **confirm/cancel modal** is used for Stop and Remove.

### 0.2 Dashboard page (`/dashboard`)
A dedicated overview page with aggregate KPIs computed per user:

* **Total accounts added**, **Total EXP gained**, **Total matches played**,
  **Total current EXP**, **In match now**, **Online / ready**.
* Average & max level, active session count.
* A **status breakdown** (In Match / Matchmaking / Online / Paused / Errors) as bars.
* An **account details** list (avatar, name, region, UID, level, status, +EXP, matches).

### 0.3 Login / Register system
* `users.py` — a small auth store: **PBKDF2-HMAC-SHA256** password hashing
  (120k iterations + per-user salt), opaque session tokens with a 7-day expiry,
  persisted to `data/users.json`.
* Endpoints: `POST /api/auth/register`, `POST /api/auth/login`,
  `POST /api/auth/logout`, `GET /api/auth/me`.
* **Cookie session** (`ff_session`, HttpOnly, SameSite=Lax). `/` and `/dashboard`
  redirect to `/login` when not signed in; all `/api/*` account routes require auth.
* **Per-user scoping**: every account is stored under the owner's username, so each
  panel user only sees and controls their **own** UIDs. The WebSocket stream is
  filtered by owner too.

---

## 1. What was fixed (auto BR match)

The original code kept a single **global** `ACCOUNT_REGION` (default `"ME"`) and used it
inside `functional_br()` when sending the start-match packet:

```python
# OLD (bug) — always used the global region, ignoring the account's real region
async def send_start_match():
    current_region = ACCOUNT_REGION
    await start_game_battle_royale(current_region, client_version, writer, ...)
```

If the account's real region (returned by `MajorLogin`) did **not** match the global
`ACCOUNT_REGION`, the start-match packet was built with the **wrong region prefix**
(`0319` = ME, `0314` = IND/BD, `0315` = others) and the match server silently ignored it —
so **auto BR never started**.

### Fix
* `send_start_match()` now uses the **per-account region** (`account_region`), which comes
  from the account's own login response.
* `version_config()`, `process_account_uid_pass()` and the keep-alive are all
  **region-parameterised** instead of relying on the global.
* Each account now runs in its **own session/task**, so multiple IDs with **different
  regions** work at the same time.
* Added a persistent session: on drop it **auto re-logs-in and reconnects** (auto BR)
  until you press **Stop**.

---

## 1b. Match-entry fix (`[SEND-FAIL] HELLO`)

After matchmaking succeeds, the bot sends a UDP **HELLO** to the match server. On **Windows**
the log showed:

```
[+] Match found [BR] | UID: ... | size=988
[+] Match #1 [BR] injected -> 162.128.249.148:10015
[MATCH #1] [SEND-FAIL] HELLO          <-- empty error message
```

The **empty** error message is the giveaway: `loop.sock_sendto()` raised
`NotImplementedError` (which has no text). That happens because Windows' default asyncio loop
is `ProactorEventLoop`, and it does **not** implement `sock_sendto` / `sock_recvfrom`
(they are only implemented on `SelectorEventLoop`). Result: the match HELLO never went out,
so the bot found a match but **never entered it**.

### Fix
* **Force a `SelectorEventLoop` on Windows** at import time (`botcore.py`) and before the
  loop is created (`server.py`), and run uvicorn with `loop="asyncio"`.
* **Loop-agnostic UDP helpers** (`udp_sendto` / `udp_recvfrom`): if a loop still doesn't
  support native UDP, they transparently fall back to blocking socket I/O in a worker thread,
  so the match works on *any* platform / event loop.
* **HELLO retries** (up to 5 attempts) with the real error text logged, so a transient send
  failure no longer kills the match.
* `decode_packet()` no longer crashes with `KeyError` on layouts that omit a field code
  (safe defaults) — this previously caused server packets to be silently dropped.
* **In-match keep-alive (ping)** is now visible in the log: `[MATCH #n] [PING] keep-alive #k`.
* **Match end → auto restart**: `cmd 103` (`RUDP_MATCH_END`) / `cmd 107` (`RUDP_DEAD`) end the
  match; if the server goes silent for 60 s the match is also treated as finished. The bot then
  **automatically starts a new BR match** (and re-logs-in if needed).

---

## 1c. Round 3 — stats, stop-confirm & reconnect-if-in-match

### a) Extra account stats
Each account card now shows, besides **Name / UID / Level / Exp / Region**:

* **Exp Gain** — a running accumulator of the exp gained while the panel is tracking the
  account (handles level-up resets safely).
* **Total Matches** — how many BR matches the bot has completed for that account.
* **Match Time** — a **live `HH:MM:SS` timer** that ticks while the account is inside a match
  (and resets to `—` when it is not).

These are tracked per session in `manager.AccountSession` (`exp_gain`, `total_matches`,
`match_started_at`, `match_info`, `in_match`), exposed through `public()` and persisted to
`data/accounts.json` so they survive a restart. `functional_br()` reports match lifecycle
through a new `match_cb("start" | "end", info)` callback.

### b) Stop confirmation popup
Pressing **Stop Match** no longer stops immediately. A styled **confirm / cancel** modal
opens (`Stop the match?` → *Cancel* / *Stop Match*). Only **Stop Match** calls the stop API.
The same modal is reused for **Remove account** and closes on `Esc` / backdrop click.

### c) Reconnect if already in a match (no duplicate match)
When you press **Start Match**, the bot first checks whether that ID is already inside a match:

* **Already running & in a match** → it does **not** start a new match; it keeps the existing
  match alive and keeps sending pings (`[~] … already in match #n - reconnecting & sending pings`).
* **Not running, but a recent match is remembered** (e.g. after a panel restart or a Stop) →
  it calls the new `botcore.reconnect_match()`, which re-opens the UDP socket to the match
  server, sends **HELLO**, answers the server and resumes **keep-alive pings**. If the server
  answers, the bot stays in that match until it ends; if the match is gone (no reply) it
  **falls back to starting a fresh match**.
* **Otherwise** → a normal new BR match is started.

---

## 2. Features

| Feature | Description |
|---|---|
| Add account | Enter **UID**, **Password**, **Region** → account is saved and its profile is fetched automatically |
| Multiple IDs | Add as many accounts as you want; each runs independently |
| Auto details | On add, logs in with UID/password and shows **Name, UID, Level, Exp, Exp Gain, Region, Total Matches, Match Time** |
| Live match timer | `HH:MM:SS` timer that ticks while the account is in a match |
| Start Match | Green **Start Match** button → begins BR matchmaking (or **reconnects** if already in a match) |
| Stop Match | Red button → **confirm/cancel popup** → stops the session |
| Live status | Top bar + per-card badge: `Logging in` → `Ready` → **`Matchmaking`** → **`In Game`** |
| Refresh / Remove | Re-fetch details, or delete the account (with confirm popup) |
| Live logs | Per-account console streamed over WebSocket |

---

## 3. Project layout

```
ff_web/
├── botcore.py          # protocol + login + match engine (from lvl.py, region fixed)
├── manager.py          # account manager, login, match control, status
├── server.py           # FastAPI app (REST + WebSocket + static)
├── message_ids.py      # message id → name map
├── thunderFF_pb2.py    # protobuf definitions
├── static/
│   ├── index.html      # panel UI
│   ├── style.css       # dark theme
│   └── app.js          # REST + WebSocket client
├── data/accounts.json  # persisted accounts (auto-created)
├── requirements.txt
├── run.sh
├── test_flow.py        # offline flow test (mocked network)
└── test_round3.py      # reconnect_match (real mock UDP) + stats/reconnect tests
```

---

## 4. Install & run

```bash
cd ff_web
pip install -r requirements.txt

# start the panel
python3 -m uvicorn server:app --host 0.0.0.0 --port 8000
# or
./run.sh
```

Then open **http://localhost:8000** in your browser.

---

## 5. API (optional, for automation)

| Method | Path | Body | Purpose |
|---|---|---|---|
| POST | `/api/auth/register` | `{username,password}` | create user + session |
| POST | `/api/auth/login` | `{username,password}` | log in + session |
| POST | `/api/auth/logout` | - | destroy session |
| GET | `/api/auth/me` | - | current signed-in user |
| GET | `/api/dashboard` | - | aggregate stats for the user |
| GET | `/api/accounts` | – | list accounts + status |
| POST | `/api/accounts` | `{uid,password,region}` | add + fetch details |
| POST | `/api/accounts/{uid}/login` | – | re-fetch details |
| POST | `/api/accounts/{uid}/start` | – | start BR match |
| POST | `/api/accounts/{uid}/stop` | – | stop BR match |
| DELETE | `/api/accounts/{uid}` | – | remove account |
| WS | `/ws` | – | live status/log stream |

---

## 6. Notes

* The account's **real region** (from the login response) is authoritative; the region you
  pick in the form is used as a fallback and for the version/region API call.
* If login fails, the card shows an `Error` badge with the reason (wrong UID/password or
  server error).
* Make sure `PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python` is set (the run script does it)
  so the `protobuf_decoder` parser works.
* **First run**: open the panel, click **Register** on the login screen to create a panel
  user, then sign in. Panel users are stored in `data/users.json` and each user only sees
  their own accounts.
* Sessions are kept in a `ff_session` cookie (7-day expiry). Use **Logout** in the bottom nav.
