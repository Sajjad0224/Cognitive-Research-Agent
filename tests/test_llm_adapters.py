"""Adapter tests use an in-process fake client — no network calls, fully offline/deterministic."""
import json

import pytest

from cra.case import load_case
from cra.engine import InvestigationEngine
from cra.events import EventStore
from cra.llm import LLMError
from cra.llm_adapters import (LLMIntentClassifier, LLMMatcher, LLMResponder,
                              add_reasoning_narrative)
from cra.report import build_report
from tests.scenarios import CASE_PATH, strong_user


class FakeClient:
    """script: list of (system_substring, reply) checked in order; raises on exhaustion."""
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def complete(self, system, user, max_tokens=300):
        self.calls.append((system, user, max_tokens))
        if not self.script:
            raise AssertionError("FakeClient called more times than scripted")
        expect_sub, reply = self.script.pop(0)
        assert expect_sub in system, f"unexpected call, system={system[:80]!r}"
        if isinstance(reply, Exception):
            raise reply
        return reply


class BrokenClient:
    def complete(self, *a, **k):
        raise LLMError("simulated outage")


@pytest.fixture
def case():
    return load_case(CASE_PATH)


def test_responder_uses_llm_and_falls_back(case):
    client = FakeClient([("Case Agent", "Ken's badge swipe places him in the building that night.")])
    r = LLMResponder(client)
    out = r.respond(case, "interview", "Interview Ken", [case.evidence["E-05"].public()])
    assert "badge swipe" in out
    r2 = LLMResponder(BrokenClient())
    out2 = r2.respond(case, "interview", "Interview Ken", [])
    assert "Nothing new" in out2  # deterministic TemplateResponder fallback


def test_matcher_maps_to_case_hypothesis_and_rejects_low_confidence(case):
    client = FakeClient([("match", json.dumps({"id": "H1", "confidence": 0.92}))])
    assert LLMMatcher(client).match(case, "he wiped it on purpose to hide bad results") == "H1"
    low = FakeClient([("match", json.dumps({"id": "H1", "confidence": 0.2}))])
    assert LLMMatcher(low, min_confidence=0.6).match(case, "vague statement") is None
    null = FakeClient([("match", json.dumps({"id": None, "confidence": 0.0}))])
    assert LLMMatcher(null).match(case, "no idea") is None


def test_matcher_never_trusts_a_hallucinated_id(case):
    hallu = FakeClient([("match", json.dumps({"id": "H999", "confidence": 0.99}))])
    assert LLMMatcher(hallu).match(case, "something") is None


def test_matcher_falls_back_to_keywords_on_bad_json_or_error(case):
    garbage = FakeClient([("match", "not json at all")])
    assert LLMMatcher(garbage).match(case, "Ken intentionally deleted the data to hide errors") == "H1"
    assert LLMMatcher(BrokenClient()).match(case, "Ken intentionally deleted the data to hide errors") == "H1"


def test_intent_classifier_validates_label_and_is_callable(case):
    client = FakeClient([("Classify", "interview"), ("Classify", "interview"), ("Classify", "not-a-real-label")])
    clf = LLMIntentClassifier(client)
    assert clf.classify("Talk to Ken") == "interview"
    assert clf("Talk to Ken") == "interview"        # __call__ alias used by the engine
    assert clf.classify("???") == "question"        # invalid label -> deterministic fallback
    assert LLMIntentClassifier(BrokenClient()).classify("Talk to Ken") == "interview"  # rule fallback


def test_engine_accepts_llm_style_components_end_to_end(case):
    responses = iter(["Ken's badge places him on site.", "supported", "supported"])
    class Resp:
        def respond(self, case, intent, text, new_evidence):
            return next(responses)
    clf = FakeClient([("Classify", "interview")])
    eng = InvestigationEngine(EventStore(), {case.case_id: case}, responder=Resp(),
                              intent_classifier=LLMIntentClassifier(clf))
    s = eng.start_session(case.case_id, "u")
    r = eng.ask(s, "Interview Ken about that night")
    assert r["intent"] == "interview" and r["response"] == "Ken's badge places him on site."


def test_reasoning_narrative_is_additive_and_never_touches_scores():
    eng = InvestigationEngine(EventStore(), {load_case(CASE_PATH).case_id: load_case(CASE_PATH)})
    case = load_case(CASE_PATH)
    store = EventStore()
    eng2 = InvestigationEngine(store, {case.case_id: case})
    s = strong_user(eng2)
    base = build_report(case, store.read(s))
    client = FakeClient([("Reasoning Analysis",
                         "The investigator built a clear evidence trail and revised course once the "
                         "server-migration theory was contradicted, before converging on a well-supported conclusion.")])
    enriched = add_reasoning_narrative(base, client)
    assert enriched is not base and "reasoning_analysis" in enriched
    assert "reasoning_analysis" not in base                          # original untouched
    stripped = {k: v for k, v in enriched.items() if k != "reasoning_analysis"}
    assert stripped == base                                          # every score/field identical
    payload = json.loads(client.calls[0][1])
    assert "score" not in json.dumps(payload) or all(
        payload["skills"][k] == base["skills"][k]["score"] for k in base["skills"])

    unchanged = add_reasoning_narrative(base, BrokenClient())
    assert unchanged == base                                         # failure -> unchanged, no crash
