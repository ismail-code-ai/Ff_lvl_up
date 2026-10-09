# TODO - FreeFire BR Bot Web Panel

## Round 4 - UI redesign + dashboard + login/register

### A. Auth system (login / register)
- [x] users.py: user store, pbkdf2 password hashing, session tokens
- [x] Persist users + sessions to data/users.json
- [x] server.py: /api/auth/register, /api/auth/login, /api/auth/logout, /api/auth/me
- [x] Cookie session + require_user guard; redirect / -> /login when not authed
- [x] Scope accounts per user (owner field) + filter WebSocket by owner

### B. Dashboard page
- [x] manager.dashboard(owner): totals (accounts, exp gained, matches, in-match, online, paused, errors, avg level)
- [x] GET /api/dashboard endpoint
- [x] dashboard.html + dashboard.js (stat cards + per-account table)

### C. UI redesign (match reference images)
- [x] style.css: mobile-first dark purple/blue theme
- [x] index.html: header, search, filter tabs (All/Match/Online/Paused), player cards
- [x] Player card: avatar, name, UID, region badge, status, Base EXP / Current / Harvested, LVL range, progress bar, EXP/hr
- [x] Bottom nav (Accounts / Dashboard / Logout)
- [x] login.html + auth.js (login/register toggle)

### D. Tests + deliver
- [ ] Auth flow test (register/login/session guard)
- [ ] Dashboard aggregate test
- [ ] Server boots; pages + endpoints 200
- [ ] Repackage ff_web_panel.zip + README
