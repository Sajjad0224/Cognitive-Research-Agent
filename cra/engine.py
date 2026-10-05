"""Investigation engine: validates every user action against case rules, emits events."""
from __future__ import annotations

import functools
import re
import threading
import uuid
from typing import Optional, Protocol

from . import events as ev
from .case import Case
from .events import EventStore
from .state import InvestigationState, reduce_events
from .textutil import has_keyword, normalize

RELATIONS = ("supports", "contradicts")
HYPOTHESIS_STATUSES = ("active", "abandoned", "supported", "refuted")
MAX_TEXT = 5000


class InvestigationError(Exception):
    """Raised for invalid or disallowed user actions."""


class NotFoundError(InvestigationError):
    """Unknown session or case."""


def _locked(fn):
    """Serialise mutating actions so concurrent requests cannot race on stale state."""
    @functools.wraps(fn)
    def wrapper(self, *a, **k):
        with self._lock:
            return fn(self, *a, **k)
    return wrapper


def classify_intent(text: str) -> str:
    t = normalize(text)
    if re.search(r"\b(i think|i believe|i suspect|my hypothesis|hypothesis)\b", t):
        return "hypothesis"
    if re.match(r"(interview|question|talk to|speak to|speak with)\b", t):
        return "interview"
    if re.match(r"(show|list|give me|pull up|get me|open)\b", t):
        return "evidence_request"
    if re.match(r"(search|find|look for|look up)\b", t):
        return "search"
    if re.search(r"\b(inconsisten\w*|contradict\w*|discrepan\w*)\b", t):
        return "evidence_analysis"
    if re.search(r"\b(timeline|what happened|between)\b", t):
        return "timeline"
    return "question"


class Responder(Protocol):
    def respond(self, case: Case, intent: str, text: str, new_evidence: list) -> str: ...


class TemplateResponder:
    """Leak-proof default: only announces evidence that was legitimately unlocked."""

    def respond(self, case: Case, intent: str, text: str, new_evidence: list) -> str:
        if not new_evidence:
            return "Nothing new turns up from that line of inquiry. Try a different angle or review your current evidence."
        lines = [f"- [{e['id']}] {e['title']}" for e in new_evidence]
        return "New evidence is now available:\n" + "\n".join(lines)


class InvestigationEngine:
    def __init__(self, store: EventStore, cases: dict, responder: Optional[Responder] = None,
                intent_classifier=None):
        self.store = store
        self.cases = dict(cases)
        self.responder = responder or TemplateResponder()
        self.intent_classifier = intent_classifier or classify_intent
        self._lock = threading.RLock()
        self._cases_lock = threading.RLock()

    # ---- case catalog management (used by the admin API, Phase 7) ---------
    def add_case(self, case: Case) -> None:
        with self._cases_lock:
            self.cases[case.case_id] = case

    def remove_case(self, case_id: str) -> None:
        with self._cases_lock:
            self.cases.pop(case_id, None)

    def has_case(self, case_id: str) -> bool:
        with self._cases_lock:
            return case_id in self.cases

    # ---- helpers -------------------------------------------------------
    def _emit(self, st: InvestigationState, event_type: str, payload: dict):
        return self.store.append(st.session_id, st.case_id, st.user_id, event_type, payload)

    def state(self, session_id: str) -> InvestigationState:
        events = self.store.read(session_id)
        if not events:
            raise NotFoundError(f"unknown session {session_id!r}")
        return reduce_events(events)

    def _open(self, session_id: str):
        st = self.state(session_id)
        if st.closed:
            raise InvestigationError("this investigation is closed (a conclusion was submitted)")
        return st, self.cases[st.case_id]

    @staticmethod
    def _text(value, what: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise InvestigationError(f"{what} must be non-empty text")
        if len(value) > MAX_TEXT:
            raise InvestigationError(f"{what} is too long (max {MAX_TEXT} characters)")
        return value.strip()

    @staticmethod
    def _confidence(value) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
            raise InvestigationError("confidence must be a number between 0 and 1")
        return float(value)

    @staticmethod
    def _need_discovered(st: InvestigationState, eid: str) -> None:
        if eid not in st.discovered:
            raise InvestigationError(f"evidence {eid!r} has not been discovered")

    @staticmethod
    def _need_hypothesis(st: InvestigationState, hid: str) -> None:
        if hid not in st.hypotheses:
            raise InvestigationError(f"unknown hypothesis {hid!r}")

    # ---- session -------------------------------------------------------
    @_locked
    def start_session(self, case_id: str, user_id: str) -> str:
        if case_id not in self.cases:
            raise NotFoundError(f"unknown case {case_id!r}")
        if not isinstance(user_id, str) or not user_id.strip():
            raise InvestigationError("user_id must be non-empty")
        case = self.cases[case_id]
        sid = "S-" + uuid.uuid4().hex[:12]
        self.store.append(sid, case_id, user_id, ev.SESSION_STARTED, {})
        for e in case.evidence.values():
            if e.initial:
                self.store.append(sid, case_id, user_id, ev.EVIDENCE_DISCOVERED,
                                  {"evidence_id": e.id, "via": "initial"})
        return sid

    def briefing(self, session_id: str) -> dict:
        st = self.state(session_id)
        case = self.cases[st.case_id]
        out = case.briefing()
        out["evidence"] = [case.evidence[i].public() for i in st.discovered]
        return out

    # ---- evidence ------------------------------------------------------
    @_locked
    def view_evidence(self, session_id: str, evidence_id: str) -> dict:
        st, case = self._open(session_id)
        self._need_discovered(st, evidence_id)
        self._emit(st, ev.EVIDENCE_VIEWED, {"evidence_id": evidence_id})
        return case.evidence[evidence_id].public()

    @_locked
    def mark_relevance(self, session_id: str, evidence_id: str, relevant: bool) -> None:
        st, _ = self._open(session_id)
        self._need_discovered(st, evidence_id)
        if not isinstance(relevant, bool):
            raise InvestigationError("relevant must be true or false")
        self._emit(st, ev.RELEVANCE_MARKED, {"evidence_id": evidence_id, "relevant": relevant})

    # ---- questions -----------------------------------------------------
    @staticmethod
    def _adaptive_ready(case: Case, st: InvestigationState) -> list:
        """Case-authored 'hint' (stall) / 'twist' (strong) evidence whose trigger condition is
        met. Deliberately simple, rule-based heuristics over already-recorded events — see
        README "Adaptive difficulty" for what this does and does not cover. The underlying case
        truth (hypothesis levels, required evidence) is never touched by this."""
        ready = []
        for e in case.evidence.values():
            if e.id in st.discovered or e.adaptive is None:
                continue
            if not all(r in st.discovered for r in e.unlock_requires):
                continue
            trig, after = e.adaptive["trigger"], e.adaptive["after"]
            if trig == "stall":
                if len(st.questions) >= after and all(not q["new_evidence"] for q in st.questions[-after:]):
                    ready.append(e)
            elif trig == "strong":
                if len(st.links) >= after and len(st.hypotheses) >= 2:
                    ready.append(e)
        return ready

    @_locked
    def ask(self, session_id: str, text: str) -> dict:
        st, case = self._open(session_id)
        text = self._text(text, "question")
        norm = normalize(text)
        intent = self.intent_classifier(text)
        repeated = any(normalize(q["text"]) == norm for q in st.questions)
        newly = []
        for e in case.evidence.values():
            if e.id in st.discovered or e.initial:
                continue
            if not all(r in st.discovered for r in e.unlock_requires):
                continue
            if any(has_keyword(norm, k) for k in e.unlock_keywords):
                newly.append(e)
        adaptive = [e for e in self._adaptive_ready(case, st) if e.id not in {x.id for x in newly}]
        for e in newly:
            self._emit(st, ev.EVIDENCE_DISCOVERED, {"evidence_id": e.id, "via": "question"})
        for e in adaptive:
            via = "adaptive_hint" if e.adaptive["trigger"] == "stall" else "adaptive_twist"
            self._emit(st, ev.EVIDENCE_DISCOVERED, {"evidence_id": e.id, "via": via})
        # Adaptive unlocks are recorded as their own events (via, above) and are NOT folded into
        # this question's own "new_evidence" tally, so question_quality/search_efficiency scoring
        # in cra/scoring.py still reflects only what the investigator's own questions surfaced.
        public = [e.public() for e in newly]
        self._emit(st, ev.QUESTION_ASKED, {"text": text, "intent": intent, "repeated": repeated,
                                           "new_evidence": [e.id for e in newly]})
        return {"intent": intent, "response": self.responder.respond(case, intent, text, public),
                "new_evidence": public,
                "adaptive_evidence": [e.public() for e in adaptive]}

    # ---- hypotheses ----------------------------------------------------
    @_locked
    def create_hypothesis(self, session_id: str, statement: str, confidence: float = 0.5) -> str:
        st, _ = self._open(session_id)
        statement = self._text(statement, "hypothesis")
        conf = self._confidence(confidence)
        hid = f"H-U{len(st.hypotheses) + 1}"
        self._emit(st, ev.HYPOTHESIS_CREATED, {"hypothesis_id": hid, "statement": statement, "confidence": conf})
        return hid

    @_locked
    def update_hypothesis(self, session_id: str, hypothesis_id: str, *, statement=None,
                          confidence=None, status=None) -> None:
        st, _ = self._open(session_id)
        self._need_hypothesis(st, hypothesis_id)
        payload = {"hypothesis_id": hypothesis_id}
        if statement is not None:
            payload["statement"] = self._text(statement, "hypothesis")
        if confidence is not None:
            payload["confidence"] = self._confidence(confidence)
        if status is not None:
            if status not in HYPOTHESIS_STATUSES:
                raise InvestigationError(f"status must be one of {HYPOTHESIS_STATUSES}")
            payload["status"] = status
        if len(payload) == 1:
            raise InvestigationError("nothing to update")
        self._emit(st, ev.HYPOTHESIS_UPDATED, payload)

    @_locked
    def link_evidence(self, session_id: str, hypothesis_id: str, evidence_id: str, relation: str) -> None:
        st, _ = self._open(session_id)
        self._need_hypothesis(st, hypothesis_id)
        self._need_discovered(st, evidence_id)
        if relation not in RELATIONS:
            raise InvestigationError(f"relation must be one of {RELATIONS}")
        if st.link_exists(hypothesis_id, evidence_id, relation):
            raise InvestigationError("that link already exists")
        self._emit(st, ev.EVIDENCE_LINKED, {"hypothesis_id": hypothesis_id,
                                            "evidence_id": evidence_id, "relation": relation})

    @_locked
    def flag_contradiction(self, session_id: str, evidence_a: str, evidence_b: str) -> None:
        st, _ = self._open(session_id)
        if evidence_a == evidence_b:
            raise InvestigationError("a contradiction needs two different evidence items")
        self._need_discovered(st, evidence_a)
        self._need_discovered(st, evidence_b)
        if st.flag_exists(evidence_a, evidence_b):
            raise InvestigationError("that contradiction was already flagged")
        self._emit(st, ev.CONTRADICTION_FLAGGED, {"evidence_a": evidence_a, "evidence_b": evidence_b})

    @_locked
    def add_note(self, session_id: str, text: str) -> None:
        st, _ = self._open(session_id)
        self._emit(st, ev.NOTE_ADDED, {"text": self._text(text, "note")})

    # ---- conclusion ----------------------------------------------------
    @_locked
    def submit_conclusion(self, session_id: str, text: str, confidence: float,
                          cited_evidence: Optional[list] = None) -> None:
        st, _ = self._open(session_id)
        text = self._text(text, "conclusion")
        conf = self._confidence(confidence)
        cited = cited_evidence or []
        if not isinstance(cited, list) or not all(isinstance(c, str) for c in cited):
            raise InvestigationError("cited_evidence must be a list of evidence ids")
        cited = list(dict.fromkeys(cited))  # de-duplicate, keep order
        for c in cited:
            self._need_discovered(st, c)
        self._emit(st, ev.CONCLUSION_SUBMITTED, {"text": text, "confidence": conf, "cited_evidence": cited})
