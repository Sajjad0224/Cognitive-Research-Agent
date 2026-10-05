"""Accounts, explicit consent, and opaque bearer tokens. Standard library only."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from typing import Callable, Optional

from .events import utc_now

CONSENT_VERSION = "2026-09-v1"
CONSENT_TEXT = (
    "This platform records your investigation actions (questions, evidence you open, hypotheses, "
    "links, notes, conclusions, and timestamps) to evaluate how you reason. Scores describe observed "
    "behaviour in a case, not intelligence or any medical or psychological condition. "
    "Records are stored securely and are tied to your account."
)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD, MAX_PASSWORD = 8, 128
_DUMMY_SALT = b"\x00" * 16


class AuthError(Exception):
    pass


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT = timedelta(minutes=15)


class AuthStore:
    def __init__(self, path: str = ":memory:", clock: Optional[Callable[[], datetime]] = None,
                 token_ttl: timedelta = timedelta(hours=12), admin_emails: frozenset = frozenset()):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._clock = clock or utc_now
        self._ttl = token_ttl
        self._admin_emails = {self.normalize_email(e) for e in admin_emails}
        # In-memory login-attempt tracker (Phase 8 hardening): bounds brute-force guessing without
        # a new table. Keyed by normalised email; intentionally NOT persisted across a restart,
        # and intentionally not IP-based (this app has no reliable client IP at this layer) — an
        # attacker who can restart the process resets their own lockout, which is an accepted
        # trade-off for a single-process deployment, not a claim of complete protection.
        self._failures: dict = {}
        with self._lock, self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS users(
                    id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, salt BLOB NOT NULL,
                    pw_hash BLOB NOT NULL, consent_version TEXT NOT NULL,
                    consent_at TEXT NOT NULL, created_at TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS tokens(
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at TEXT NOT NULL);
            """)

    @staticmethod
    def normalize_email(email: str) -> str:
        e = (email or "").strip().lower()
        if len(e) > 254 or not _EMAIL.match(e):
            raise AuthError("invalid email address")
        return e

    def register(self, email: str, password: str, consent_version: str) -> dict:
        if consent_version != CONSENT_VERSION:
            raise AuthError("consent to the current data-tracking notice is required")
        email = self.normalize_email(email)
        if not isinstance(password, str) or not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
            raise AuthError(f"password must be {MIN_PASSWORD}-{MAX_PASSWORD} characters")
        salt = secrets.token_bytes(16)
        uid = "U-" + uuid.uuid4().hex[:12]
        now = self._clock().isoformat()
        is_admin = int(email in self._admin_emails)
        try:
            with self._lock, self._conn:
                self._conn.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,?)",
                                   (uid, email, salt, _hash(password, salt), consent_version, now, now, is_admin))
        except sqlite3.IntegrityError:
            raise AuthError("email already registered") from None
        return {"id": uid, "email": email, "consent_version": consent_version, "consent_at": now,
                "is_admin": bool(is_admin)}

    def _locked_until(self, email: str) -> Optional[datetime]:
        rec = self._failures.get(email)
        if not rec:
            return None
        count, last = rec
        if count < MAX_LOGIN_ATTEMPTS:
            return None
        until = last + LOGIN_LOCKOUT
        return until if until > self._clock() else None

    def login(self, email: str, password: str) -> tuple:
        try:
            email = self.normalize_email(email)
        except AuthError:
            email = ""
        with self._lock:
            locked = self._locked_until(email)
        if locked is not None:
            raise AuthError(f"too many failed attempts; try again after {locked.isoformat()}")
        with self._lock:
            row = self._conn.execute(
                "SELECT id, email, salt, pw_hash, is_admin FROM users WHERE email=?", (email,)).fetchone()
        pw = password if isinstance(password, str) else ""
        computed = _hash(pw, row[2] if row is not None else _DUMMY_SALT)  # always exactly one hash
        ok = row is not None and hmac.compare_digest(computed, row[3])
        if not ok:
            with self._lock:
                count, _ = self._failures.get(email, (0, self._clock()))
                self._failures[email] = (count + 1, self._clock())
            raise AuthError("invalid email or password")
        with self._lock:
            self._failures.pop(email, None)
        token = secrets.token_urlsafe(32)
        exp = (self._clock() + self._ttl).isoformat()
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO tokens VALUES (?,?,?)", (_token_hash(token), row[0], exp))
        return token, {"id": row[0], "email": row[1], "is_admin": bool(row[4])}

    def user_for_token(self, token: str) -> Optional[dict]:
        if not token:
            return None
        th = _token_hash(token)
        with self._lock:
            row = self._conn.execute(
                "SELECT u.id, u.email, t.expires_at, u.is_admin FROM tokens t JOIN users u ON u.id=t.user_id "
                "WHERE t.token_hash=?", (th,)).fetchone()
        if row is None:
            return None
        if datetime.fromisoformat(row[2]) <= self._clock():
            with self._lock, self._conn:
                self._conn.execute("DELETE FROM tokens WHERE token_hash=?", (th,))
            return None
        return {"id": row[0], "email": row[1], "is_admin": bool(row[3])}

    def logout(self, token: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM tokens WHERE token_hash=?", (_token_hash(token),))

    def raw_password_hash(self, email: str) -> Optional[bytes]:  # for tests/diagnostics only
        with self._lock:
            row = self._conn.execute("SELECT pw_hash FROM users WHERE email=?", (email,)).fetchone()
        return row[0] if row else None

    def user_count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def get_user(self, user_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, email, consent_version, consent_at, created_at, is_admin FROM users WHERE id=?",
                (user_id,)).fetchone()
        if row is None:
            return None
        return {"id": row[0], "email": row[1], "consent_version": row[2], "consent_at": row[3],
                "created_at": row[4], "is_admin": bool(row[5])}

    def delete_user(self, user_id: str) -> None:
        """Part of the account-deletion flow (Phase 8 privacy requirement): removes the account
        row and every bearer token for it. Investigation event data for this user is erased
        separately via EventStore.erase_user, which the caller (app.py) runs alongside this."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM tokens WHERE user_id=?", (user_id,))
            self._conn.execute("DELETE FROM users WHERE id=?", (user_id,))
