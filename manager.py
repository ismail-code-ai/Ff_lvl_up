# -*- coding: utf-8 -*-
"""
Account manager for the FreeFire BR web panel.

Holds accounts (scoped per panel-user), performs login/profile fetch, and
controls the BR match session per account with live status reporting
(idle -> matchmaking -> in_game).
"""
import asyncio
import json
import os
import time
from typing import Dict, List, Optional, Callable, Awaitable

import botcore

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DATA_FILE = os.path.join(DATA_DIR, "accounts.json")

VALID_REGIONS = ["ME", "IND", "SG", "TH", "PH", "VN", "MY", "ID", "HK", "TW",
                 "BD", "PK", "CIS", "BR", "NA", "SAC", "EUROPE"]

# statuses
ST_IDLE = "idle"
ST_LOGGING_IN = "logging_in"
ST_READY = "ready"
ST_MATCHMAKING = "matchmaking"
ST_IN_GAME = "in_game"
ST_ERROR = "error"
ST_STOPPED = "stopped"


def _key(owner: str, uid: str) -> str:
    """Accounts are stored per owner; anonymous (owner == '') keeps legacy keys."""
    owner = (owner or "").strip().lower()
    uid = str(uid).strip()
    return f"{owner}::{uid}" if owner else uid


class AccountSession:
    def __init__(self, uid: str, password: str, region: str = "ME", owner: str = ""):
        self.uid = str(uid).strip()
        self.password = password
        self.region = (region or "ME").upper()
        self.owner = (owner or "").strip().lower()
        self.account_data: Optional[Dict] = None
        self.status = ST_IDLE
        self.error: Optional[str] = None
        self.logs: List[Dict] = []
        self.task: Optional[asyncio.Task] = None
        self.stop_event: Optional[asyncio.Event] = None
        self.last_update = time.time()

        # --- stats ---
        # exp gain is tracked as a running accumulator of positive deltas so it
        # stays correct even if the game resets exp on level-up.
        self.last_exp: Optional[int] = None
        self.base_exp: Optional[int] = None  # exp value when tracking started
        self.exp_gain: int = 0
        self.total_matches: int = 0
        self.session_started_at: float = time.time()
        # --- current match tracking (for the live timer + reconnect) ---
        self.match_started_at: Optional[float] = None
        self.match_info: Optional[Dict] = None
        self.in_match: bool = False

    def update_exp(self, new_exp):
        """Accumulate exp gain from the latest exp value."""
        try:
            new_exp = int(new_exp)
        except (TypeError, ValueError):
            return
        if new_exp < 0:
            return
        if self.last_exp is None:
            self.last_exp = new_exp
            return
        if new_exp >= self.last_exp:
            self.exp_gain += (new_exp - self.last_exp)
        else:
            # exp wrapped around (level-up reset) -> count the fresh value
            self.exp_gain += new_exp
        self.last_exp = new_exp

    def exp_per_hour(self) -> float:
        elapsed = max(1.0, time.time() - self.session_started_at)
        return round(self.exp_gain / (elapsed / 3600.0), 1)

    def public(self) -> Dict:
        ad = self.account_data or {}
        running = bool(self.task and not self.task.done())
        exp = ad.get("exp")
        current = None
        if exp is not None:
            try:
                current = int(exp)
            except (TypeError, ValueError):
                current = exp
        base = self.base_exp
        if base is None:
            base = exp
        return {
            "uid": self.uid,
            "name": ad.get("nickname") or "",
            "account_id": str(ad.get("account_id") or ""),
            "level": ad.get("level"),
            "exp": exp,
            "base_exp": base,
            "exp_gain": self.exp_gain,
            "exp_current": current,
            "exp_per_hour": self.exp_per_hour(),
            "total_matches": self.total_matches,
            "region": ad.get("region") or self.region,
            "status": self.status,
            "error": self.error,
            "running": running,
            "in_match": self.in_match,
            "match_started_at": self.match_started_at,
            "match_index": (self.match_info or {}).get("match_index"),
            "logs": self.logs[-60:],
        }


class AccountManager:
    def __init__(self):
        self.accounts: Dict[str, AccountSession] = {}
        self._listeners: List[Callable[[Dict], Awaitable[None]]] = []
        self._load()

    # ---------------- persistence ----------------
    def _load(self):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for a in data:
                uid = str(a.get("uid", "")).strip()
                if not uid:
                    continue
                owner = str(a.get("owner", "")).strip().lower()
                s = AccountSession(uid, a.get("password", ""), a.get("region", "ME"), owner)
                # restore stats / last-known match (so we can reconnect after a restart)
                try:
                    if a.get("last_exp") is not None:
                        s.last_exp = int(a.get("last_exp"))
                    if a.get("base_exp") is not None:
                        s.base_exp = int(a.get("base_exp"))
                    s.exp_gain = int(a.get("exp_gain") or 0)
                    s.total_matches = int(a.get("total_matches") or 0)
                    if a.get("match_info"):
                        s.match_info = a.get("match_info")
                        s.match_started_at = s.match_info.get("started_at")
                except Exception:
                    pass
                self.accounts[_key(owner, uid)] = s
        except Exception:
            pass

    def _save(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            payload = [
                {
                    "owner": s.owner,
                    "uid": s.uid,
                    "password": s.password,
                    "region": s.region,
                    "last_exp": s.last_exp,
                    "base_exp": s.base_exp,
                    "exp_gain": s.exp_gain,
                    "total_matches": s.total_matches,
                    "match_info": s.match_info,
                }
                for s in self.accounts.values()
            ]
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass

    # ---------------- listeners / broadcast ----------------
    def add_listener(self, cb):
        self._listeners.append(cb)

    def remove_listener(self, cb):
        try:
            self._listeners.remove(cb)
        except ValueError:
            pass

    async def broadcast(self, msg: Dict):
        for cb in list(self._listeners):
            try:
                await cb(msg)
            except Exception:
                pass

    async def _emit_account(self, session: AccountSession):
        await self.broadcast({"type": "account", "owner": session.owner, "account": session.public()})

    def _log(self, session: AccountSession, msg: str):
        entry = {"t": time.time(), "msg": msg}
        session.logs.append(entry)
        if len(session.logs) > 300:
            session.logs = session.logs[-300:]
        # avoid spamming the socket with the very chatty debug lines
        if "[DEBUG]" in msg:
            return
        try:
            asyncio.get_running_loop().create_task(
                self.broadcast({"type": "log", "owner": session.owner, "uid": session.uid,
                                "msg": msg, "t": entry["t"]})
            )
        except Exception:
            pass

    async def _set_status(self, session: AccountSession, status: str):
        if session.status != status:
            session.status = status
            session.last_update = time.time()
            await self.broadcast({"type": "status", "owner": session.owner,
                                  "uid": session.uid, "status": status})

    # ---------------- account CRUD ----------------
    def _owned(self, owner: str) -> List[AccountSession]:
        owner = (owner or "").strip().lower()
        return [s for s in self.accounts.values() if s.owner == owner]

    def list_public(self, owner: str = "") -> List[Dict]:
        return [s.public() for s in self._owned(owner)]

    def get(self, owner: str, uid: str) -> Optional[AccountSession]:
        return self.accounts.get(_key(owner, uid))

    def dashboard(self, owner: str = "") -> Dict:
        accs = self._owned(owner)
        levels = []
        total_exp = 0
        for s in accs:
            ad = s.account_data or {}
            lv = ad.get("level")
            if isinstance(lv, (int, float)) and lv > 0:
                levels.append(int(lv))
            try:
                total_exp += int(ad.get("exp") or 0)
            except (TypeError, ValueError):
                pass
        in_match = sum(1 for s in accs if s.in_match or s.status == ST_IN_GAME)
        matchmaking = sum(1 for s in accs if s.status == ST_MATCHMAKING)
        online = sum(1 for s in accs if s.status == ST_READY)
        paused = sum(1 for s in accs if s.status in (ST_IDLE, ST_STOPPED))
        errors = sum(1 for s in accs if s.status == ST_ERROR)
        running = sum(1 for s in accs if s.task and not s.task.done())
        return {
            "total_accounts": len(accs),
            "total_exp_gained": sum(s.exp_gain for s in accs),
            "total_matches": sum(s.total_matches for s in accs),
            "total_exp": total_exp,
            "in_match": in_match,
            "matchmaking": matchmaking,
            "online": online,
            "paused": paused,
            "errors": errors,
            "running": running,
            "avg_level": round(sum(levels) / len(levels), 1) if levels else 0,
            "max_level": max(levels) if levels else 0,
            "accounts": [s.public() for s in accs],
        }

    async def add_account(self, owner: str, uid: str, password: str, region: str = "ME") -> Dict:
        uid = str(uid).strip()
        if not uid:
            raise ValueError("UID is required")
        if not password:
            raise ValueError("Password is required")
        region = (region or "ME").upper()
        if region not in VALID_REGIONS:
            region = "ME"

        key = _key(owner, uid)
        session = self.accounts.get(key)
        if session is None:
            session = AccountSession(uid, password, region, owner)
            self.accounts[key] = session
        else:
            session.password = password
            session.region = region
        self._save()

        await self._emit_account(session)
        # fetch details (login) right away
        await self.login_account(owner, uid)
        return session.public()

    async def remove_account(self, owner: str, uid: str):
        key = _key(owner, uid)
        session = self.accounts.get(key)
        if not session:
            return
        await self.stop_match(owner, uid)
        self.accounts.pop(key, None)
        self._save()
        await self.broadcast({"type": "removed", "owner": session.owner, "uid": session.uid})

    async def login_account(self, owner: str, uid: str) -> Dict:
        session = self.accounts.get(_key(owner, uid))
        if not session:
            raise ValueError("Account not found")
        if session.task and not session.task.done():
            # already running a session
            return session.public()

        session.error = None
        await self._set_status(session, ST_LOGGING_IN)
        self._log(session, f"[*] Fetching profile for UID {session.uid} ...")
        try:
            data = await botcore.process_account_uid_pass(
                session.uid, session.password, session.region
            )
        except Exception as e:
            data = None
            self._log(session, f"[-] Login error: {e}")

        if not data:
            session.error = "Login failed (wrong UID/password or server error)"
            await self._set_status(session, ST_ERROR)
            self._log(session, f"[-] Login failed for UID {session.uid}")
            await self._emit_account(session)
            return session.public()

        session.account_data = data
        session.error = None
        session.update_exp(data.get("exp"))
        self._save()
        await self._set_status(session, ST_READY)
        self._log(
            session,
            f"[+] Login OK | {data.get('nickname')} | UID {data.get('account_id')} "
            f"| Lvl {data.get('level')} | Exp {data.get('exp')} | Region {data.get('region')}",
        )
        await self._emit_account(session)
        return session.public()

    # ---------------- match control ----------------
    def _match_info_recent(self, info: Optional[Dict]) -> bool:
        """A stored match is only worth reconnecting to if it is still recent."""
        try:
            started = float((info or {}).get("started_at") or 0)
        except (TypeError, ValueError):
            return False
        if started <= 0:
            return False
        return (time.time() - started) < botcore.MAX_MATCH_DURATION

    async def start_match(self, owner: str, uid: str) -> Dict:
        session = self.accounts.get(_key(owner, uid))
        if not session:
            raise ValueError("Account not found")

        # 1) The session is already running.
        if session.task and not session.task.done():
            if session.in_match:
                # Already inside a match -> do NOT start a new one, just make sure
                # we keep the existing match alive (reconnect + pings).
                idx = (session.match_info or {}).get("match_index", "?")
                self._log(
                    session,
                    f"[~] UID {session.uid} is already in match #{idx} - "
                    f"reconnecting & sending pings (no new match started).",
                )
                await self._emit_account(session)
                return session.public()
            # matchmaking / logging in -> a session is already active
            self._log(
                session,
                f"[i] UID {session.uid} session already active ({session.status}).",
            )
            return session.public()

        # 2) Not running, but we remember a recent match -> try to reconnect to it.
        if session.match_info and self._match_info_recent(session.match_info):
            session.stop_event = asyncio.Event()
            session.task = asyncio.create_task(self._run_reconnect(session))
            await self._emit_account(session)
            return session.public()

        # 3) Fresh start: login if needed then run a normal BR session.
        if not session.account_data:
            await self.login_account(owner, uid)
            if not session.account_data:
                return session.public()

        session.stop_event = asyncio.Event()
        session.task = asyncio.create_task(self._run_session(session))
        await self._emit_account(session)
        return session.public()

    async def _run_reconnect(self, session: AccountSession):
        """Try to reconnect to the account's last known match. If the match is
        gone, fall back to starting a normal BR session."""
        info = dict(session.match_info or {})
        self._log(session, f"[~] Reconnecting UID {session.uid} to existing match ...")
        await self._set_status(session, ST_MATCHMAKING)
        await self._emit_account(session)

        async def status_cb(st):
            await self._set_status(session, st)
            await self._emit_account(session)

        def log_cb(msg):
            self._log(session, msg)

        try:
            result = await botcore.reconnect_match(
                info, status_cb=status_cb, log_cb=log_cb, stop_event=session.stop_event
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._log(session, f"[-] Reconnect error: {e}")
            result = "no-match"

        if session.stop_event and session.stop_event.is_set():
            session.in_match = False
            session.match_started_at = None
            await self._set_status(session, ST_READY if session.account_data else ST_IDLE)
            await self._emit_account(session)
            return

        if result == "reconnected":
            # the match we reconnected to finished -> count it and start a new one
            session.total_matches += 1
            self._log(
                session,
                f"[+] Reconnected match finished. Total matches: {session.total_matches}",
            )
        else:
            self._log(session, "[i] No live match to reconnect to - starting a fresh match.")

        # clear the (now stale) match and continue with a normal persistent session
        session.in_match = False
        session.match_started_at = None
        session.match_info = None
        try:
            await botcore.refresh_account_profile(session.account_data)
            session.update_exp((session.account_data or {}).get("exp"))
        except Exception:
            pass
        self._save()
        await self._emit_account(session)

        await self._run_session(session)

    async def stop_match(self, owner: str, uid: str) -> Dict:
        session = self.accounts.get(_key(owner, uid))
        if not session:
            raise ValueError("Account not found")
        if session.stop_event:
            session.stop_event.set()
        if session.task and not session.task.done():
            session.task.cancel()
            try:
                await session.task
            except (asyncio.CancelledError, Exception):
                pass
        session.task = None
        # stop the live timer, but keep match_info so that pressing Start again
        # can reconnect to the same match if it is still live on the server.
        session.in_match = False
        session.match_started_at = None
        self._save()
        await self._set_status(session, ST_READY if session.account_data else ST_IDLE)
        await self._emit_account(session)
        return session.public()

    async def _run_session(self, session: AccountSession):
        """Persistent session: keeps playing BR matches, auto re-logs in on drop,
        until the user presses Stop."""
        while True:
            if session.stop_event and session.stop_event.is_set():
                break
            try:
                retry = await self._run_once(session)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                session.error = str(e)
                self._log(session, f"[-] Session error: {e}")
                retry = True

            if session.stop_event and session.stop_event.is_set():
                break
            if not retry:
                break

            # Session dropped on its own -> reconnect automatically (auto BR)
            self._log(session, "[!] Session dropped. Reconnecting in 3s...")
            await self._set_status(session, ST_LOGGING_IN)
            await asyncio.sleep(3)
            try:
                data = await botcore.process_account_uid_pass(
                    session.uid, session.password, session.region
                )
                if data:
                    session.account_data = data
                    session.error = None
                    await self._emit_account(session)
            except Exception:
                pass

        await self._set_status(session, ST_READY if session.account_data else ST_IDLE)
        await self._emit_account(session)

    async def _run_once(self, session: AccountSession):
        ad = session.account_data
        reg = ad.get("region") or session.region
        acc_id = str(ad.get("account_id"))
        func_addr = ad.get("functional_addrs")
        info_addr = ad.get("informational_addrs")

        info_task = None
        exp_task = None
        try:
            if not func_addr or ":" not in str(func_addr):
                session.error = "No match server address returned by login"
                await self._set_status(session, ST_ERROR)
                self._log(session, f"[-] {session.error} (functional_addrs missing)")
                await self._emit_account(session)
                return False

            tcp_online = await botcore.build_tcp_startup_packet(
                ad["account_id"], ad["token"], ad["server_time"],
                ad["aes_ak"], ad["iv_i"], region=reg, typ="OnLine",
            )
            tcp_chat = await botcore.build_tcp_startup_packet(
                ad["account_id"], ad["token"], ad["server_time"],
                ad["aes_ak"], ad["iv_i"], region=reg, typ="ChaT",
            )

            if info_addr and ":" in str(info_addr):
                info_task = asyncio.create_task(
                    botcore.informational(
                        info_addr, tcp_chat, ad["aes_ak"], ad["iv_i"],
                        region=reg, account_id=acc_id,
                    )
                )

            exp_task = asyncio.create_task(self._exp_refresher(session))

            async def status_cb(st):
                if st == "connected":
                    st = ST_MATCHMAKING
                await self._set_status(session, st)
                await self._emit_account(session)

            def log_cb(msg):
                self._log(session, msg)

            async def match_cb(event, info):
                if event == "start":
                    session.match_info = dict(info or {})
                    session.in_match = True
                    session.match_started_at = (info or {}).get("started_at") or time.time()
                    self._log(
                        session,
                        f"[+] Match #{(info or {}).get('match_index')} started "
                        f"at {(info or {}).get('server_ip_port')} - timer running.",
                    )
                    self._save()
                    await self._emit_account(session)
                elif event == "end":
                    session.in_match = False
                    session.match_started_at = None
                    session.match_info = None
                    session.total_matches += 1
                    try:
                        await botcore.refresh_account_profile(session.account_data)
                        session.update_exp((session.account_data or {}).get("exp"))
                    except Exception:
                        pass
                    self._log(
                        session,
                        f"[+] Match finished. Total matches: {session.total_matches} "
                        f"| Exp gain: {session.exp_gain}",
                    )
                    self._save()
                    await self._emit_account(session)

            await botcore.functional_br(
                func_addr,
                tcp_online,
                reg,
                ad["client_version"],
                ad["aes_ak"],
                ad["iv_i"],
                account_id=acc_id,
                account_data=ad,
                status_cb=status_cb,
                log_cb=log_cb,
                stop_event=session.stop_event,
                single_match=False,
                match_cb=match_cb,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            session.error = str(e)
            self._log(session, f"[-] Session error: {e}")
        finally:
            for t in (info_task, exp_task):
                if t and not t.done():
                    t.cancel()
            for t in (info_task, exp_task):
                if t:
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
        return True

    async def _exp_refresher(self, session: AccountSession):
        while True:
            await asyncio.sleep(120)
            try:
                await botcore.refresh_account_profile(session.account_data)
                session.update_exp((session.account_data or {}).get("exp"))
                await self._emit_account(session)
            except Exception:
                pass

    async def shutdown(self):
        for key in list(self.accounts.keys()):
            s = self.accounts.get(key)
            if not s:
                continue
            try:
                await self.stop_match(s.owner, s.uid)
            except Exception:
                pass
