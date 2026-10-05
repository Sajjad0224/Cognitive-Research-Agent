"""Immutable, append-only event store (SQLite). Updates and deletes are blocked by triggers."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

SESSION_STARTED = "session_started"
EVIDENCE_DISCOVERED = "evidence_discovered"
EVIDENCE_VIEWED = "evidence_viewed"
QUESTION_ASKED = "question_asked"
HYPOTHESIS_CREATED = "hypothesis_created"
HYPOTHESIS_UPDATED = "hypothesis_updated"
EVIDENCE_LINKED = "evidence_linked"
CONTRADICTION_FLAGGED = "contradiction_flagged"
RELEVANCE_MARKED = "relevance_marked"
NOTE_ADDED = "note_added"
CONCLUSION_SUBMITTED = "conclusion_submitted"

ALL_EVENT_TYPES = frozenset({
    SESSION_STARTED, EVIDENCE_DISCOVERED, EVIDENCE_VIEWED, QUESTION_ASKED,
    HYPOTHESIS_CREATED, HYPOTHESIS_UPDATED, EVIDENCE_LINKED, CONTRADICTION_FLAGGED,
    RELEVANCE_MARKED, NOTE_ADDED, CONCLUSION_SUBMITTED})


@dataclass(frozen=True)
class Event:
    seq: int
    event_id: str
    session_id: str
    case_id: str
    user_id: str
    ts: str
    event_type: str
    payload: dict


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id   TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    case_id    TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    ts         TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, seq);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are immutable'); END;
"""


class EventStore:
    def __init__(self, path: str = ":memory:", clock: Optional[Callable[[], datetime]] = None):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()
        self._clock = clock or utc_now

    def append(self, session_id: str, case_id: str, user_id: str,
               event_type: str, payload: Optional[dict] = None) -> Event:
        if event_type not in ALL_EVENT_TYPES:
            raise ValueError(f"unknown event type {event_type!r}")
        payload = payload or {}
        event_id = "EVT-" + uuid.uuid4().hex[:12]
        ts = self._clock().isoformat()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO events(event_id, session_id, case_id, user_id, ts, event_type, payload)"
                " VALUES (?,?,?,?,?,?,?)",
                (event_id, session_id, case_id, user_id, ts, event_type, json.dumps(payload)))
            seq = cur.lastrowid
        return Event(seq, event_id, session_id, case_id, user_id, ts, event_type, payload)

    def read(self, session_id: str) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, event_id, session_id, case_id, user_id, ts, event_type, payload"
                " FROM events WHERE session_id=? ORDER BY seq", (session_id,)).fetchall()
        return [Event(r[0], r[1], r[2], r[3], r[4], r[5], r[6], json.loads(r[7])) for r in rows]

    def sessions_for_user(self, user_id: str) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id FROM events WHERE user_id=? AND event_type=? ORDER BY seq",
                (user_id, SESSION_STARTED)).fetchall()
        return [r[0] for r in rows]

    def all_sessions(self) -> list:
        """Every session ever started, for admin analytics (Phase 7)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, case_id, user_id, ts FROM events WHERE event_type=? ORDER BY seq",
                (SESSION_STARTED,)).fetchall()
        return [{"session_id": r[0], "case_id": r[1], "user_id": r[2], "started_at": r[3]} for r in rows]

    def has_sessions_for_case(self, case_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM events WHERE case_id=? AND event_type=? LIMIT 1",
                (case_id, SESSION_STARTED)).fetchone()
        return row is not None

    def export_for_user(self, user_id: str) -> list:
        """Every event this user ever produced, across all sessions, oldest first. Used only by
        the privacy data-export endpoint (Phase 8) — a read, not a mutation, so it needs no
        exception to the immutability triggers above."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, event_id, session_id, case_id, user_id, ts, event_type, payload"
                " FROM events WHERE user_id=? ORDER BY seq", (user_id,)).fetchall()
        return [Event(r[0], r[1], r[2], r[3], r[4], r[5], r[6], json.loads(r[7])) for r in rows]

    def erase_user(self, user_id: str) -> int:
        """Permanently erases every event belonging to this user. This is the ONE deliberate,
        audited exception to the append-only design at the top of this file: the UPDATE/DELETE
        triggers exist to stop scores from being quietly altered mid-investigation, not to make a
        user's own right to erasure (Phase 8 privacy requirement) impossible. The trigger is
        dropped, the rows for this user_id are deleted, and the trigger is restored in the same
        transaction, so events for every OTHER user remain exactly as immutable as before and
        this user's data is identically unrecoverable afterwards. Returns the number of rows
        erased. There is no public API path to this method other than the privacy/account
        deletion endpoint — it is never used to "fix" a score or edit history."""
        with self._lock, self._conn:
            self._conn.execute("DROP TRIGGER IF EXISTS events_no_delete")
            cur = self._conn.execute("DELETE FROM events WHERE user_id=?", (user_id,))
            self._conn.executescript("""
                CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
                BEGIN SELECT RAISE(ABORT, 'events are immutable'); END;
            """)
            return cur.rowcount
