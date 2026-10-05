"""Event-sourced investigation state: a pure function of the event stream."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import events as ev


@dataclass
class InvestigationState:
    session_id: str = ""
    case_id: str = ""
    user_id: str = ""
    started_at: Optional[str] = None
    closed: bool = False
    discovered: list = field(default_factory=list)       # ordered evidence ids
    discovered_via: dict = field(default_factory=dict)   # eid -> "initial" | "question"
    viewed: list = field(default_factory=list)           # first-view order
    view_counts: dict = field(default_factory=dict)
    marks: dict = field(default_factory=dict)            # eid -> {"relevant", "event_id"}
    questions: list = field(default_factory=list)
    hypotheses: dict = field(default_factory=dict)
    links: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    conclusion: Optional[dict] = None

    def link_exists(self, hid: str, eid: str, relation: str) -> bool:
        return any(l["hypothesis_id"] == hid and l["evidence_id"] == eid and l["relation"] == relation
                   for l in self.links)

    def flag_exists(self, a: str, b: str) -> bool:
        return any(f["pair"] == frozenset((a, b)) for f in self.flags)


def _apply(s: InvestigationState, e: ev.Event) -> None:
    p = e.payload
    t = e.event_type
    if t == ev.SESSION_STARTED:
        s.session_id, s.case_id, s.user_id, s.started_at = e.session_id, e.case_id, e.user_id, e.ts
    elif t == ev.EVIDENCE_DISCOVERED:
        if p["evidence_id"] not in s.discovered:
            s.discovered.append(p["evidence_id"])
            s.discovered_via[p["evidence_id"]] = p["via"]
    elif t == ev.EVIDENCE_VIEWED:
        eid = p["evidence_id"]
        if eid not in s.viewed:
            s.viewed.append(eid)
        s.view_counts[eid] = s.view_counts.get(eid, 0) + 1
    elif t == ev.QUESTION_ASKED:
        s.questions.append({"event_id": e.event_id, "seq": e.seq, "ts": e.ts, "text": p["text"],
                            "intent": p["intent"], "new_evidence": list(p["new_evidence"]),
                            "repeated": bool(p["repeated"])})
    elif t == ev.HYPOTHESIS_CREATED:
        s.hypotheses[p["hypothesis_id"]] = {
            "id": p["hypothesis_id"], "statement": p["statement"], "confidence": p["confidence"],
            "status": "active", "created_seq": e.seq, "created_ts": e.ts,
            "event_id": e.event_id, "update_seqs": [], "update_event_ids": []}
    elif t == ev.HYPOTHESIS_UPDATED:
        h = s.hypotheses[p["hypothesis_id"]]
        for k in ("statement", "confidence", "status"):
            if k in p:
                h[k] = p[k]
        h["update_seqs"].append(e.seq)
        h["update_event_ids"].append(e.event_id)
    elif t == ev.EVIDENCE_LINKED:
        s.links.append({"event_id": e.event_id, "seq": e.seq, "hypothesis_id": p["hypothesis_id"],
                        "evidence_id": p["evidence_id"], "relation": p["relation"]})
    elif t == ev.CONTRADICTION_FLAGGED:
        s.flags.append({"event_id": e.event_id, "seq": e.seq,
                        "pair": frozenset((p["evidence_a"], p["evidence_b"]))})
    elif t == ev.RELEVANCE_MARKED:
        s.marks[p["evidence_id"]] = {"relevant": bool(p["relevant"]), "event_id": e.event_id}
    elif t == ev.NOTE_ADDED:
        s.notes.append({"event_id": e.event_id, "ts": e.ts, "text": p["text"]})
    elif t == ev.CONCLUSION_SUBMITTED:
        s.conclusion = {"event_id": e.event_id, "seq": e.seq, "ts": e.ts, "text": p["text"],
                        "confidence": p["confidence"], "cited": list(p["cited_evidence"])}
        s.closed = True


def reduce_events(events) -> InvestigationState:
    s = InvestigationState()
    for e in events:
        _apply(s, e)
    return s
