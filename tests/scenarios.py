"""Reusable scripted investigations (used by tests and the demo)."""
from datetime import datetime, timedelta, timezone

from cra.engine import InvestigationEngine
from cra.events import EventStore
from cra.case import load_case

CASE_PATH = "cases/missing_research_data.json"


def make_engine():
    t = {"now": datetime(2026, 1, 1, tzinfo=timezone.utc)}

    def clock():
        t["now"] += timedelta(seconds=10)
        return t["now"]

    case = load_case(CASE_PATH)
    store = EventStore(clock=clock)
    return InvestigationEngine(store, {case.case_id: case}), case, store


def strong_user(eng, user="strong"):
    s = eng.start_session("CASE-014", user)
    for e in ("E-01", "E-02", "E-03"):
        eng.view_evidence(s, e)
    eng.mark_relevance(s, "E-02", False)
    eng.mark_relevance(s, "E-03", False)
    h2 = eng.create_hypothesis(s, "The server migration accidentally deleted the data", 0.5)
    eng.ask(s, "Who accessed the server? Show me the audit log")
    eng.view_evidence(s, "E-04")
    eng.flag_contradiction(s, "E-02", "E-04")
    eng.link_evidence(s, h2, "E-04", "contradicts")
    eng.update_hypothesis(s, h2, status="abandoned")
    h1 = eng.create_hypothesis(s, "Ken intentionally deleted the data to hide errors", 0.6)
    eng.link_evidence(s, h1, "E-04", "supports")
    eng.ask(s, "Interview the postdoc Ken Chen")
    eng.view_evidence(s, "E-05")
    eng.ask(s, "Who was in the lab? Check badge swipes")
    eng.view_evidence(s, "E-06")
    eng.flag_contradiction(s, "E-05", "E-06")
    eng.link_evidence(s, h1, "E-06", "supports")
    eng.link_evidence(s, h1, "E-05", "contradicts")
    eng.update_hypothesis(s, h1, confidence=0.75)
    eng.ask(s, "Is there a backup snapshot?")
    eng.view_evidence(s, "E-09")
    eng.ask(s, "What was his motive? Any messages?")
    eng.view_evidence(s, "E-10")
    eng.link_evidence(s, h1, "E-10", "supports")
    eng.submit_conclusion(s, "Ken intentionally deleted the data to hide errors in the outlier trials",
                          0.9, ["E-04", "E-06", "E-09", "E-10"])
    return s


def guesser(eng, user="guesser"):
    s = eng.start_session("CASE-014", user)
    eng.submit_conclusion(s, "Ken intentionally deleted the data to hide errors", 0.95, [])
    return s
