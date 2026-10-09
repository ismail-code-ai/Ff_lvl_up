# -*- coding: utf-8 -*-
"""Round 4 tests: auth (register/login/logout/session guard), per-user account
scoping, and the dashboard aggregate endpoint. Uses FastAPI TestClient with a
mocked botcore login so nothing touches the network."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server
import manager as manager_mod
import users as users_mod
from fastapi.testclient import TestClient

TMP = tempfile.mkdtemp()
manager_mod.DATA_DIR = TMP
manager_mod.DATA_FILE = os.path.join(TMP, "accounts.json")
server.manager.accounts = {}
server.users.path = os.path.join(TMP, "users.json")
server.users.users = {}
server.users.sessions = {}


async def fake_process(uid, password, region=None):
    return {
        "account_id": uid,
        "nickname": f"Player_{uid}",
        "region": (region or "ME").upper(),
        "level": 10,
        "exp": 1000,
        "functional_addrs": "1.2.3.4:10000",
        "informational_addrs": None,
        "client_version": "1.0",
        "token": "tok",
        "server_time": 0,
        "aes_ak": b"0" * 16,
        "iv_i": b"0" * 16,
    }


import botcore
botcore.process_account_uid_pass = fake_process

client = TestClient(server.app)
passed = 0
failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}")


print("== A. Auth flow ==")
# unauthenticated guards
r = client.get("/api/accounts", follow_redirects=False)
check("GET /api/accounts without auth -> 401", r.status_code == 401)

r = client.get("/", follow_redirects=False)
check("GET / without auth -> redirect to /login",
      r.status_code in (302, 307) and "/login" in r.headers.get("location", ""))

r = client.get("/login", follow_redirects=False)
check("GET /login without auth -> 200", r.status_code == 200 and "auth-card" in r.text)

r = client.get("/api/auth/me")
check("GET /api/auth/me without auth -> 401", r.status_code == 401)

# register
r = client.post("/api/auth/register", json={"username": "alice", "password": "secret1"})
check("register alice -> 200", r.status_code == 200 and r.json().get("ok"))
check("register sets session cookie", "ff_session" in client.cookies)

r = client.get("/api/auth/me")
check("GET /api/auth/me after register -> alice", r.status_code == 200 and r.json()["user"]["username"] == "alice")

r = client.get("/", follow_redirects=False)
check("GET / after auth -> 200 index", r.status_code == 200 and "FreeFire BR Bot" in r.text)

# duplicate + validation
r = client.post("/api/auth/register", json={"username": "alice", "password": "secret1"})
check("duplicate username -> 400", r.status_code == 400)
r = client.post("/api/auth/register", json={"username": "ab", "password": "secret1"})
check("short username -> 400", r.status_code == 400)
r = client.post("/api/auth/register", json={"username": "bobby", "password": "x"})
check("short password -> 400", r.status_code == 400)

print("== B. Per-user scoping ==")
r = client.post("/api/accounts", json={"uid": "111111", "password": "p", "region": "ME"})
check("alice add account -> 200", r.status_code == 200 and r.json()["account"]["uid"] == "111111")
check("account has nickname", r.json()["account"]["name"] == "Player_111111")

r = client.get("/api/accounts")
check("alice sees 1 account", len(r.json()["accounts"]) == 1)

# simulate exp gain on alice's account
sess = server.manager.accounts["alice::111111"]
sess.update_exp(1500)  # +500
sess.total_matches = 3
sess.in_match = True
sess.match_started_at = 1000.0

r = client.get("/api/dashboard")
d = r.json()
check("alice dashboard total_accounts == 1", d["total_accounts"] == 1)
check("alice dashboard total_exp_gained == 500", d["total_exp_gained"] == 500)
check("alice dashboard total_matches == 3", d["total_matches"] == 3)
check("alice dashboard in_match == 1", d["in_match"] == 1)
check("alice dashboard has accounts list", len(d["accounts"]) == 1)

# second user
client2 = TestClient(server.app)
r = client2.post("/api/auth/register", json={"username": "bob", "password": "secret2"})
check("register bob -> 200", r.status_code == 200)
r = client2.get("/api/accounts")
check("bob sees 0 accounts (scoped)", r.json()["accounts"] == [])
r = client2.get("/api/dashboard")
check("bob dashboard total_accounts == 0", r.json()["total_accounts"] == 0)

r = client2.post("/api/accounts/111111/start")
check("bob cannot start alice's account -> 404", r.status_code == 404)

print("== C. Login / logout ==")
client3 = TestClient(server.app)
r = client3.post("/api/auth/login", json={"username": "alice", "password": "wrong"})
check("login wrong password -> 401", r.status_code == 401)
r = client3.post("/api/auth/login", json={"username": "alice", "password": "secret1"})
check("login correct -> 200", r.status_code == 200)
r = client3.get("/api/accounts")
check("login session sees alice's account", len(r.json()["accounts"]) == 1)

r = client3.post("/api/auth/logout")
check("logout -> 200", r.status_code == 200)
r = client3.get("/api/auth/me")
check("me after logout -> 401", r.status_code == 401)

print("== D. Persistence ==")
check("users.json written", os.path.exists(server.users.path))
check("accounts.json written", os.path.exists(manager_mod.DATA_FILE))

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
