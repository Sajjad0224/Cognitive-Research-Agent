import copy
import json
import re
import sqlite3

import pytest

from cra.case import CaseValidationError, load_case, parse_case
from cra.engine import InvestigationError, classify_intent
from cra.matcher import KeywordMatcher
from cra.report import build_report, render_text
from cra.scoring import compute_features, evaluate
from cra.state import reduce_events
from cra.textutil import has_keyword, normalize
from tests.scenarios import CASE_PATH, guesser, make_engine, strong_user


def raw():
    with open(CASE_PATH) as fh:
        return json.load(fh)


# ---------------- text + matcher ----------------
def test_keyword_prefix_boundary():
    n = normalize("Ken's token was taken; he deleted it")
    assert has_keyword(n, "ken") and has_keyword(n, "delete")
    assert not has_keyword(normalize("a token was taken"), "ken")


def test_matcher_levels_and_ambiguity():
    case = load_case(CASE_PATH)
    m = KeywordMatcher()
    assert m.match(case, "Ken intentionally deleted the data to hide errors") == "H1"
    assert m.match(case, "Ken accidentally deleted the files") == "H3"
    assert m.match(case, "The server migration accidentally deleted the data") == "H2"
    assert m.match(case, "Lina sabotaged the data") == "H4"
    assert m.match(case, "I have no idea") is None


# ---------------- case validation ----------------
def test_case_loads():
    c = load_case(CASE_PATH)
    assert len(c.evidence) == 10 and len(c.contradictions) == 2


@pytest.mark.parametrize("mutate,msg", [
    (lambda d: d["evidence"].append(copy.deepcopy(d["evidence"][0])), "duplicate evidence"),
    (lambda d: d["evidence"][3].__setitem__("supports", ["H9"]), "unknown hypothesis"),
    (lambda d: d["evidence"][3].__setitem__("relevance", 1.5), "within [0, 1]"),
    (lambda d: d["evidence"][3].__setitem__("unlock", {"keywords": []}), "needs unlock.keywords"),
    (lambda d: d["evidence"][5]["unlock"].__setitem__("requires", ["E-99"]), "unknown evidence"),
    (lambda d: d.__setitem__("required_evidence", ["E-99"]), "required_evidence unknown"),
    (lambda d: [h.__setitem__("level", "incorrect") for h in d["hypotheses"]], "'correct'"),
    (lambda d: d.__setitem__("scoring_weights", {"nope": {"link_accuracy": 1}}), "unknown skill"),
    (lambda d: d.__setitem__("scoring_weights", {"adaptability": {"bogus": 1}}), "unknown indicator"),
    (lambda d: d.__setitem__("contradictions", [{"id": "C", "evidence": ["E-01", "E-01"]}]), "two distinct"),
])
def test_case_validation_rejects(mutate, msg):
    d = raw()
    mutate(d)
    with pytest.raises(CaseValidationError, match=re.escape(msg)):
        parse_case(d)


def test_cyclic_requires_is_rejected():
    d = raw()
    for e in d["evidence"]:
        if e["id"] == "E-04":
            e["unlock"]["requires"] = ["E-09"]  # E-09 requires E-04 -> cycle
    with pytest.raises(CaseValidationError, match="undiscoverable"):
        parse_case(d)


def test_bool_not_accepted_as_number():
    d = raw()
    d["evidence"][3]["reliability"] = True
    with pytest.raises(CaseValidationError):
        parse_case(d)


# ---------------- event store ----------------
def test_events_are_immutable():
    eng, _, store = make_engine()
    s = eng.start_session("CASE-014", "u")
    with pytest.raises(sqlite3.DatabaseError):
        store._conn.execute("UPDATE events SET event_type='x'")
    with pytest.raises(sqlite3.DatabaseError):
        store._conn.execute("DELETE FROM events")
    assert len(store.read(s)) == 4  # started + 3 initial evidence
    with pytest.raises(ValueError):
        store.append(s, "CASE-014", "u", "bogus", {})


# ---------------- engine rules ----------------
def test_gating_and_no_leakage():
    eng, case, _ = make_engine()
    s = eng.start_session("CASE-014", "u")
    b = eng.briefing(s)
    assert {e["id"] for e in b["evidence"]} == {"E-01", "E-02", "E-03"}
    dumped = json.dumps(b)
    for secret in ("reliability", "supports", "contradicts", "red_herring", "level", "H1", "correct"):
        assert secret not in dumped
    with pytest.raises(InvestigationError):
        eng.view_evidence(s, "E-04")                       # undiscovered
    r = eng.ask(s, "Show me the badge swipes")            # E-06 requires E-05
    assert r["new_evidence"] == []
    r = eng.ask(s, "Interview Ken")
    assert [e["id"] for e in r["new_evidence"]] == ["E-05"]
    r = eng.ask(s, "Check the badge swipes")
    assert [e["id"] for e in r["new_evidence"]] == ["E-06"]
    assert all(k in e for e in r["new_evidence"] for k in ("id", "title", "type", "content"))
    assert set(r["new_evidence"][0]) == {"id", "title", "type", "content"}
    # token must not trigger "ken"
    r = eng.ask(s, "Was a token taken?")
    assert r["new_evidence"] == []


def test_action_validation():
    eng, _, _ = make_engine()
    s = eng.start_session("CASE-014", "u")
    with pytest.raises(InvestigationError):
        eng.ask(s, "   ")
    with pytest.raises(InvestigationError):
        eng.create_hypothesis(s, "x", 1.5)
    with pytest.raises(InvestigationError):
        eng.create_hypothesis(s, "x", True)
    h = eng.create_hypothesis(s, "Ken did it", 0.5)
    with pytest.raises(InvestigationError):
        eng.link_evidence(s, h, "E-04", "supports")        # undiscovered
    eng.link_evidence(s, h, "E-01", "supports")
    with pytest.raises(InvestigationError):
        eng.link_evidence(s, h, "E-01", "supports")        # duplicate
    with pytest.raises(InvestigationError):
        eng.link_evidence(s, h, "E-01", "sideways")
    with pytest.raises(InvestigationError):
        eng.link_evidence(s, "H-U99", "E-01", "supports")
    with pytest.raises(InvestigationError):
        eng.flag_contradiction(s, "E-01", "E-01")
    eng.flag_contradiction(s, "E-01", "E-02")
    with pytest.raises(InvestigationError):
        eng.flag_contradiction(s, "E-02", "E-01")          # order-insensitive duplicate
    with pytest.raises(InvestigationError):
        eng.update_hypothesis(s, h)
    with pytest.raises(InvestigationError):
        eng.update_hypothesis(s, h, status="weird")
    with pytest.raises(InvestigationError):
        eng.submit_conclusion(s, "x", 0.5, ["E-04"])
    with pytest.raises(InvestigationError):
        eng.start_session("NOPE", "u")
    with pytest.raises(InvestigationError):
        eng.state("S-unknown")


def test_session_closes_after_conclusion():
    eng, _, _ = make_engine()
    s = guesser(eng)
    for call in (lambda: eng.ask(s, "hello"), lambda: eng.create_hypothesis(s, "x"),
                 lambda: eng.submit_conclusion(s, "again", 0.5)):
        with pytest.raises(InvestigationError, match="closed"):
            call()


def test_intent_classification():
    assert classify_intent("Interview the CFO") == "interview"
    assert classify_intent("Show me all evidence") == "evidence_request"
    assert classify_intent("I think John altered the records") == "hypothesis"
    assert classify_intent("Is anything inconsistent here?") == "evidence_analysis"
    assert classify_intent("What happened between 8 PM and midnight?") == "timeline"
    assert classify_intent("Find the invoices") == "search"
    assert classify_intent("Who is Sam?") == "question"


# ---------------- evaluation ----------------
def test_strong_vs_guesser_process_matters():
    eng, case, store = make_engine()
    a, b = strong_user(eng), guesser(eng)
    ra, rb = build_report(case, store.read(a)), build_report(case, store.read(b))
    assert ra["status"] == rb["status"] == "solved"                # both reached the right answer
    assert ra["performance"]["outcome"] == rb["performance"]["outcome"] == 100.0
    assert ra["performance"]["final"] > rb["performance"]["final"] + 30
    assert ra["solution_reliability"] > 0.7 and rb["solution_reliability"] == 0.0
    assert "Correct outcome, weak evidentiary reasoning." in rb["flags"]
    assert not ra["flags"]
    assert ra["skills"]["critical_thinking"]["score"] > 70


def test_guesser_has_missing_data_not_fake_zeros():
    eng, case, store = make_engine()
    g = guesser(eng)
    rep = build_report(case, store.read(g))
    ct = rep["skills"]["critical_thinking"]
    by = {b["indicator"]: b for b in ct["breakdown"]}
    assert by["evidence_evaluation"]["value"] is None and by["evidence_evaluation"]["effective_weight"] == 0
    assert abs(sum(b["effective_weight"] for b in ct["breakdown"]) - 1.0) < 1e-9
    assert rep["skills"]["adaptability"]["score"] is None          # no data at all


def test_evaluation_is_deterministic_and_replayable():
    eng, case, store = make_engine()
    s = strong_user(eng)
    ev = store.read(s)
    r1, r2 = build_report(case, ev), build_report(case, ev)
    assert r1 == r2
    assert reduce_events(ev).conclusion["cited"] == ["E-04", "E-06", "E-09", "E-10"]


def test_scores_within_bounds_and_features_complete():
    eng, case, store = make_engine()
    for s in (strong_user(eng), guesser(eng)):
        out = evaluate(case, store.read(s))
        for f in out["features"].values():
            assert f.value is None or 0.0 <= f.value <= 1.0
        for sk in out["skills"].values():
            assert sk["score"] is None or 0.0 <= sk["score"] <= 100.0
        assert 0.0 <= out["performance"]["final"] <= 100.0


def test_wrong_and_partial_conclusions():
    eng, case, store = make_engine()
    s = eng.start_session("CASE-014", "u")
    eng.ask(s, "audit log")
    eng.submit_conclusion(s, "The server migration accidentally deleted the data", 0.9, ["E-04"])
    rep = build_report(case, store.read(s))
    assert rep["status"] == "unsolved" and rep["performance"]["outcome"] == 0.0
    assert any("contradicts" in f for f in rep["flags"])
    s2 = eng.start_session("CASE-014", "u2")
    eng.submit_conclusion(s2, "Ken accidentally deleted the files", 0.5, [])
    assert build_report(case, store.read(s2))["status"] == "partially_solved"
    s3 = eng.start_session("CASE-014", "u3")
    eng.submit_conclusion(s3, "Aliens did it", 0.5, [])
    r3 = build_report(case, store.read(s3))
    assert r3["status"] == "unsolved" and "Conclusion does not match any accepted explanation." in r3["flags"]


def test_unsubmitted_session_reports_cleanly():
    eng, case, store = make_engine()
    s = eng.start_session("CASE-014", "u")
    rep = build_report(case, store.read(s))
    assert rep["status"] == "unsubmitted" and rep["performance"]["outcome"] == 0.0
    assert "unsubmitted" in render_text(rep)


def test_confirmation_bias_and_bad_links_penalised():
    eng, case, store = make_engine()
    s = eng.start_session("CASE-014", "u")
    eng.ask(s, "audit log")
    eng.ask(s, "Interview Ken")
    h = eng.create_hypothesis(s, "Ken intentionally deleted the data to hide errors")
    eng.link_evidence(s, h, "E-04", "supports")
    eng.link_evidence(s, h, "E-02", "supports")     # red herring is not support for H1
    eng.link_evidence(s, h, "E-05", "supports")     # statement contradicts H1 -> wrong direction
    st = reduce_events(store.read(s))
    f = compute_features(case, st, KeywordMatcher())
    assert f["bias_resistance"].value == 0.0
    assert abs(f["link_accuracy"].value - 1 / 3) < 1e-9


def test_case_specific_weights_change_scores():
    eng, case, store = make_engine()
    s = strong_user(eng)
    d = raw()
    d["scoring_weights"] = {"critical_thinking": {"bias_resistance": 1}}
    d["outcome_weights"] = {"outcome": 1, "reasoning": 0, "process": 0}
    case2 = parse_case(d)
    ev = store.read(s)
    r_default, r_custom = build_report(case, ev), build_report(case2, ev)
    bias = compute_features(case, reduce_events(ev), KeywordMatcher())["bias_resistance"].value
    assert r_custom["skills"]["critical_thinking"]["score"] == round(bias * 100, 1)
    assert r_custom["performance"]["final"] == 100.0
    assert r_default["skills"]["critical_thinking"]["score"] != r_custom["skills"]["critical_thinking"]["score"]


def test_report_text_renders():
    eng, case, store = make_engine()
    text = render_text(build_report(case, store.read(strong_user(eng))))
    for section in ("COGNITIVE PROFILE", "INVESTIGATION BEHAVIOR", "STRENGTHS", "KEY DECISION POINTS"):
        assert section in text


def test_low_data_is_flagged_and_prioritisation_ignores_initial_items():
    eng, case, store = make_engine()
    g = build_report(case, store.read(guesser(eng)))
    ct = g["skills"]["critical_thinking"]
    assert ct["data_coverage"] == 0.2 and "Low confidence" in ct["explanation"]
    st = reduce_events(store.read(strong_user(eng)))
    f = compute_features(case, st, KeywordMatcher())
    assert f["evidence_prioritization"].value == 1.0   # E-01..E-03 were case-provided, not chosen
    s2 = eng.start_session("CASE-014", "u")
    eng.view_evidence(s2, "E-02")
    f2 = compute_features(case, reduce_events(store.read(s2)), KeywordMatcher())
    assert f2["evidence_prioritization"].value is None


def test_concurrent_duplicate_links_only_one_succeeds():
    import threading
    eng, _, _ = make_engine()
    s = eng.start_session("CASE-014", "u")
    h = eng.create_hypothesis(s, "Ken did it")
    results = []

    def go():
        try:
            eng.link_evidence(s, h, "E-01", "supports")
            results.append("ok")
        except InvestigationError:
            results.append("dup")

    ts = [threading.Thread(target=go) for _ in range(16)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert results.count("ok") == 1 and results.count("dup") == 15


# ---------------- adaptive difficulty (Phase 7) ----------------
def _adapt_engine():
    from cra.case import load_case
    from cra.engine import InvestigationEngine
    from cra.events import EventStore
    case = load_case("cases/adaptive_demo_case.json")
    store = EventStore()
    return InvestigationEngine(store, {case.case_id: case}), case, store


def test_adaptive_hint_unlocks_after_a_stall():
    eng, case, _ = _adapt_engine()
    s = eng.start_session(case.case_id, "u")
    r = eng.ask(s, "irrelevant question one")
    assert r["adaptive_evidence"] == []
    r = eng.ask(s, "irrelevant question two")
    assert r["adaptive_evidence"] == []          # only 1 prior stalled question recorded so far
    r = eng.ask(s, "irrelevant question three")  # now 2 prior stalled questions -> hint unlocks
    assert [e["id"] for e in r["adaptive_evidence"]] == ["A-04"]
    st = eng.state(s)
    assert st.discovered_via["A-04"] == "adaptive_hint"
    r = eng.ask(s, "irrelevant question four")   # already unlocked, not repeated
    assert r["adaptive_evidence"] == []


def test_adaptive_twist_unlocks_when_investigator_is_testing_hard():
    eng, case, _ = _adapt_engine()
    s = eng.start_session(case.case_id, "u")
    h1 = eng.create_hypothesis(s, "Heater started the fire")
    h2 = eng.create_hypothesis(s, "Arson by an employee")
    eng.link_evidence(s, h1, "A-01", "supports")
    eng.link_evidence(s, h2, "A-02", "contradicts")       # 2 links + 2 hypotheses -> twist ready
    r = eng.ask(s, "any new leads?")
    assert [e["id"] for e in r["adaptive_evidence"]] == ["A-05"]


def test_adaptive_evidence_does_not_count_toward_the_triggering_questions_own_tally():
    eng, case, store = _adapt_engine()
    s = eng.start_session(case.case_id, "u")
    eng.ask(s, "q1"); eng.ask(s, "q2"); eng.ask(s, "q3")
    st = reduce_events(store.read(s))
    assert st.questions[-1]["new_evidence"] == []          # the hint isn't folded into the question
    assert "A-04" in st.discovered


def test_adaptive_respects_unlock_requires_and_case_validation():
    d = json.loads(open("cases/adaptive_demo_case.json").read())
    d["evidence"][3]["adaptive"] = {"trigger": "bogus", "after": 2}
    with pytest.raises(CaseValidationError, match="trigger must be"):
        parse_case(d)
    d = json.loads(open("cases/adaptive_demo_case.json").read())
    d["evidence"][3]["adaptive"]["after"] = 0
    with pytest.raises(CaseValidationError, match="positive integer"):
        parse_case(d)
    d = json.loads(open("cases/adaptive_demo_case.json").read())
    del d["evidence"][3]["adaptive"]
    with pytest.raises(CaseValidationError, match="unlock.keywords or an 'adaptive'"):
        parse_case(d)


def test_adaptive_case_scores_normally_through_the_full_pipeline():
    eng, case, store = _adapt_engine()
    s = eng.start_session(case.case_id, "u")
    eng.ask(s, "maintenance log for the heater")
    eng.view_evidence(s, "A-03")
    eng.submit_conclusion(s, "A faulty space heater left running overnight started the fire",
                          0.85, ["A-03"])
    rep = build_report(case, store.read(s))
    assert rep["status"] == "solved" and rep["performance"]["outcome"] == 100.0
