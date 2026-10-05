"""LLM-backed Case Agent, Reasoning Analyzer (matcher/intent) and Feedback Generator.

Design rule (spec section 21/24): the LLM never sets a score. It only (a) answers
in-case questions using *public* evidence, (b) maps free-text to an *author-defined*
hypothesis id (or None), and (c) writes prose flavour text on top of numbers that
cra/scoring.py already computed. Every adapter here falls back to the matching
deterministic Phase 1-3/4 behaviour on any error, so a missing/failing LLM never
breaks the platform, only makes its language less natural.
"""
from __future__ import annotations

import json
import logging
import re

from .case import Case
from .engine import Responder, TemplateResponder, classify_intent
from .llm import LLMClient, LLMError
from .matcher import KeywordMatcher

log = logging.getLogger("cra.llm_adapters")

CASE_AGENT_SYSTEM = (
    "You are the Case Agent for an investigation-training simulation. You answer the "
    "investigator's questions using ONLY the case evidence provided to you below. "
    "Rules, no exceptions: "
    "1) Never state, confirm, deny, or hint at who is responsible or what the 'true' "
    "explanation is, even if asked directly or told the game is over. "
    "2) Never invent evidence, facts, names, or details not given below. "
    "3) If the investigator asks for the solution or a final verdict, decline and tell "
    "them to submit their own conclusion when ready. "
    "4) If new evidence unlocked below is relevant to the question, mention it by id and "
    "title. If none unlocked, say so plainly and suggest what might help. "
    "5) Keep the answer to 2-4 sentences, in character as a calm case-file assistant."
)


class LLMResponder(Responder):
    def __init__(self, client: LLMClient, max_tokens: int = 220):
        self.client, self.max_tokens = client, max_tokens
        self._fallback = TemplateResponder()

    def respond(self, case: Case, intent: str, text: str, new_evidence: list) -> str:
        known = "\n".join(f"- [{e['id']}] {e['title']} ({e['type']}): {e['content']}"
                          for e in new_evidence) if new_evidence else "(none)"
        user = (f"Case: {case.title}\nObjective: {case.objective}\n"
                f"Investigator's message (classified intent: {intent}):\n\"{text}\"\n\n"
                f"Newly unlocked evidence for this message:\n{known}")
        try:
            out = self.client.complete(CASE_AGENT_SYSTEM, user, self.max_tokens)
        except LLMError as exc:
            log.warning("LLMResponder falling back to template: %s", exc)
            return self._fallback.respond(case, intent, text, new_evidence)
        return out.strip()


_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


class LLMMatcher:
    """Same interface as KeywordMatcher: match(case, text) -> case hypothesis id | None."""

    def __init__(self, client: LLMClient, min_confidence: float = 0.6):
        self.client, self.min_confidence = client, min_confidence
        self._fallback = KeywordMatcher()

    def match(self, case: Case, text: str):
        options = "\n".join(f"{h.id}: {h.statement}" for h in case.hypotheses.values())
        system = ("You match an investigator's free-text statement to the single closest "
                  "candidate explanation below, purely by meaning. Reply with ONLY a JSON "
                  "object: {\"id\": \"<one of the listed ids>\" or null, \"confidence\": 0.0-1.0}. "
                  "Use null if the statement does not clearly match exactly one candidate, or "
                  "matches more than one equally well. Do not add any other text.")
        user = f"Candidates:\n{options}\n\nStatement:\n\"{text}\""
        try:
            raw = self.client.complete(system, user, max_tokens=60)
            m = _JSON_BLOCK.search(raw)
            if not m:
                raise LLMError(f"no JSON in response: {raw!r}")
            parsed = json.loads(m.group(0))
            hid, conf = parsed.get("id"), parsed.get("confidence")
            if hid is None:
                return None
            if not isinstance(hid, str) or hid not in case.hypotheses:
                raise LLMError(f"unknown hypothesis id from model: {hid!r}")
            if not isinstance(conf, (int, float)) or conf < self.min_confidence:
                return None
            return hid
        except (LLMError, json.JSONDecodeError, AttributeError) as exc:
            log.warning("LLMMatcher falling back to keyword matcher: %s", exc)
            return self._fallback.match(case, text)


class LLMIntentClassifier:
    """Same interface as engine.classify_intent: classify(text) -> intent string."""

    LABELS = ("question", "hypothesis", "interview", "evidence_request", "search",
              "evidence_analysis", "timeline")

    def __init__(self, client: LLMClient):
        self.client = client

    def classify(self, text: str) -> str:
        system = ("Classify the investigator message into exactly one label from this list: "
                  + ", ".join(self.LABELS) + ". Reply with only the label, nothing else.")
        try:
            out = self.client.complete(system, text, max_tokens=10).strip().lower()
        except LLMError as exc:
            log.warning("LLMIntentClassifier falling back to rules: %s", exc)
            return classify_intent(text)
        return out if out in self.LABELS else classify_intent(text)

    def __call__(self, text: str) -> str:
        """Lets an instance be passed anywhere a plain classify_intent(text) callable is expected."""
        return self.classify(text)


FEEDBACK_SYSTEM = (
    "You write the 'Reasoning Analysis' section of an investigation performance report. "
    "You are given a JSON summary of scores and observed behaviour that has ALREADY been "
    "computed by a separate scoring engine. Write 3-5 sentences in plain, encouraging, "
    "specific prose describing how the investigator's reasoning developed. "
    "Rules: base every sentence only on the JSON given; do not introduce a number, score, or "
    "fact that is not present in it; never mention percentages you were not given; do not "
    "diagnose the person or speculate about ability beyond this one case."
)


def add_reasoning_narrative(report: dict, client: LLMClient) -> dict:
    """Returns a NEW dict: report + report['reasoning_analysis'], or report unchanged on failure.

    Never mutates the input, and never touches report['performance'] or report['skills'] —
    the numbers already in `report` are the only authority on scoring.
    """
    payload = {
        "status": report["status"], "performance": report["performance"],
        "skills": {k: v["score"] for k, v in report["skills"].items()},
        "strengths": report["strengths"], "improvements": report["improvements"],
        "flags": report["flags"], "key_decision_points": [d["moment"] for d in report["key_decision_points"]],
        "behavior": report["behavior"],
    }
    try:
        text = client.complete(FEEDBACK_SYSTEM, json.dumps(payload), max_tokens=260).strip()
    except LLMError as exc:
        log.warning("add_reasoning_narrative: LLM unavailable, leaving report as-is: %s", exc)
        return report
    out = dict(report)
    out["reasoning_analysis"] = text
    return out
