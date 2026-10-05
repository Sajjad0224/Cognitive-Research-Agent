"""Read-only session sharing — a minimal, concrete hook toward the "multiplayer / team-based
investigations" future feature (spec section 27), without building out real-time collaboration.

An investigator can mint a share token for one of their own sessions. Anyone holding that token
can view the session's current workspace read-only, with no account of their own needed — useful
for "show a teammate what I've found so far" or an instructor reviewing a student's progress.
Sharing never grants write access: all mutating engine calls still require session ownership
(see app.py::owned), and this module never touches scoring or event data.
"""
from __future__ import annotations

import secrets
import sqlite3
import threading
from datetime import datetime
from typing import Callable, Optional

from .events import utc_now


class ShareStore:
    def __init__(self, path: str = ":memory:", clock: Optional[Callable[[], datetime]] = None):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._clock = clock or utc_now
        with self._lock, self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS shares(
                    token TEXT PRIMARY KEY, session_id TEXT NOT NULL, owner_id TEXT NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_shares_session ON shares(session_id);
            """)

    def create(self, session_id: str, owner_id: str) -> str:
        token = secrets.token_urlsafe(24)
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO shares VALUES (?,?,?,?)",
                               (token, session_id, owner_id, self._clock().isoformat()))
        return token

    def session_for_token(self, token: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute("SELECT session_id FROM shares WHERE token=?", (token,)).fetchone()
        return row[0] if row else None

    def revoke_all_for_session(self, session_id: str) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM shares WHERE session_id=?", (session_id,))
            return cur.rowcount

    def erase_user(self, owner_id: str) -> int:
        """Deletes every share this user created — part of the Phase 8 account-erasure flow."""
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM shares WHERE owner_id=?", (owner_id,))
            return cur.rowcount
