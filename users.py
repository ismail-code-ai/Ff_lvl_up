# -*- coding: utf-8 -*-
"""
User accounts + sessions for the FreeFire BR Bot web panel.

A tiny self-contained auth store (no external deps):
  * passwords hashed with PBKDF2-HMAC-SHA256 (120k iterations + per-user salt)
  * opaque random session tokens kept server-side with an expiry
  * everything persisted to data/users.json so logins survive a restart
"""
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Dict, List, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
USERS_FILE = os.path.join(DATA_DIR, "users.json")

PBKDF2_ITERATIONS = 120_000
SESSION_TTL = 60 * 60 * 24 * 7  # 7 days

USERNAME_MIN = 3
USERNAME_MAX = 24
PASSWORD_MIN = 4


def _hash_password(password: str, salt: Optional[str] = None) -> Dict[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return {"salt": salt, "hash": dk.hex()}


def _verify_password(password: str, salt: str, expected_hash: str) -> bool:
    try:
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    except Exception:
        return False
    return hmac.compare_digest(dk.hex(), expected_hash)


class AuthError(Exception):
    """Raised for user-facing auth problems (bad credentials, taken username...)."""


class UserStore:
    def __init__(self, path: str = USERS_FILE):
        self.path = path
        self.users: Dict[str, Dict] = {}      # username(lower) -> user record
        self.sessions: Dict[str, Dict] = {}   # token -> {username, expires}
        self._load()

    # ---------------- persistence ----------------
    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.users = data.get("users", {}) or {}
            self.sessions = data.get("sessions", {}) or {}
        except Exception:
            self.users = {}
            self.sessions = {}
        self._purge_expired()

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"users": self.users, "sessions": self.sessions}, f, indent=2)
        except Exception:
            pass

    def _purge_expired(self):
        now = time.time()
        expired = [t for t, s in self.sessions.items() if s.get("expires", 0) < now]
        for t in expired:
            self.sessions.pop(t, None)
        if expired:
            self._save()

    # ---------------- users ----------------
    def register(self, username: str, password: str) -> Dict:
        username = (username or "").strip()
        password = password or ""
        if len(username) < USERNAME_MIN:
            raise AuthError(f"Username must be at least {USERNAME_MIN} characters")
        if len(username) > USERNAME_MAX:
            raise AuthError(f"Username must be at most {USERNAME_MAX} characters")
        if not all(c.isalnum() or c in "._-" for c in username):
            raise AuthError("Username may only contain letters, numbers, . _ -")
        if len(password) < PASSWORD_MIN:
            raise AuthError(f"Password must be at least {PASSWORD_MIN} characters")

        key = username.lower()
        if key in self.users:
            raise AuthError("That username is already taken")

        rec = _hash_password(password)
        self.users[key] = {
            "username": username,
            "salt": rec["salt"],
            "hash": rec["hash"],
            "created": time.time(),
        }
        self._save()
        return {"username": username}

    def authenticate(self, username: str, password: str) -> Dict:
        key = (username or "").strip().lower()
        user = self.users.get(key)
        if not user:
            raise AuthError("Invalid username or password")
        if not _verify_password(password or "", user.get("salt", ""), user.get("hash", "")):
            raise AuthError("Invalid username or password")
        return {"username": user.get("username", username)}

    # ---------------- sessions ----------------
    def create_session(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        self.sessions[token] = {"username": username, "expires": time.time() + SESSION_TTL}
        self._save()
        return token

    def get_user_by_token(self, token: Optional[str]) -> Optional[Dict]:
        if not token:
            return None
        sess = self.sessions.get(token)
        if not sess:
            return None
        if sess.get("expires", 0) < time.time():
            self.sessions.pop(token, None)
            self._save()
            return None
        key = str(sess.get("username", "")).lower()
        user = self.users.get(key)
        if not user:
            return None
        return {"username": user.get("username", sess.get("username"))}

    def destroy_session(self, token: Optional[str]):
        if token and token in self.sessions:
            self.sessions.pop(token, None)
            self._save()

    def count_users(self) -> int:
        return len(self.users)
