"""Case model + strict validation. Cases are pure data (no code changes needed)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .skills import (DEFAULT_OUTCOME_WEIGHTS, DEFAULT_SKILL_WEIGHTS,
                     validate_outcome_weights, validate_skill_weights)

LEVELS = ("correct", "partial", "incorrect")


class CaseValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Evidence:
    id: str
    title: str
    type: str
    content: str
    reliability: float
    relevance: float
    directness: float
    supports: tuple
    contradicts: tuple
    red_herring: bool
    initial: bool
    unlock_keywords: tuple
    unlock_requires: tuple
    adaptive: Optional[dict]   # {"trigger": "stall"|"strong", "after": int} or None

    @property
    def is_relevant(self) -> bool:
        return self.relevance >= 0.5 and not self.red_herring

    def public(self) -> dict:
        """What the user may see. Hidden metadata (reliability, supports, ...) is never exposed."""
        return {"id": self.id, "title": self.title, "type": self.type, "content": self.content}


@dataclass(frozen=True)
class CaseHypothesis:
    id: str
    statement: str
    level: str
    match_keywords: tuple


@dataclass(frozen=True)
class Case:
    case_id: str
    title: str
    domain: str
    difficulty: str
    overview: str
    objective: str
    time_limit_minutes: Optional[int]
    evidence: dict          # id -> Evidence (insertion ordered)
    hypotheses: dict        # id -> CaseHypothesis
    contradictions: tuple   # ((id, frozenset({a, b})), ...)
    required_evidence: tuple
    assessed_skills: tuple
    skill_weights: dict
    outcome_weights: dict

    def briefing(self) -> dict:
        """Public case header. Never includes hypotheses or the hidden truth."""
        return {"case_id": self.case_id, "title": self.title, "domain": self.domain,
                "difficulty": self.difficulty, "overview": self.overview,
                "objective": self.objective, "time_limit_minutes": self.time_limit_minutes}


def _req(d: dict, key: str, typ, ctx: str):
    if key not in d:
        raise CaseValidationError(f"{ctx}: missing '{key}'")
    v = d[key]
    if not isinstance(v, typ) or (isinstance(v, bool) and typ is not bool):
        raise CaseValidationError(f"{ctx}: '{key}' has wrong type")
    return v


def _unit(d: dict, key: str, ctx: str) -> float:
    v = _req(d, key, (int, float), ctx)
    if not 0.0 <= float(v) <= 1.0:
        raise CaseValidationError(f"{ctx}: '{key}' must be within [0, 1]")
    return float(v)


def _str_list(d: dict, key: str, ctx: str, default=None) -> tuple:
    v = d.get(key, default)
    if v is None:
        raise CaseValidationError(f"{ctx}: missing '{key}'")
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        raise CaseValidationError(f"{ctx}: '{key}' must be a list of non-empty strings")
    return tuple(v)


def parse_case(data: dict) -> Case:
    if not isinstance(data, dict):
        raise CaseValidationError("case must be a JSON object")
    cid = _req(data, "case_id", str, "case")
    ctx = f"case {cid}"
    if not cid.strip():
        raise CaseValidationError("case_id must be non-empty")
    tl = data.get("time_limit_minutes")
    if tl is not None and (not isinstance(tl, int) or isinstance(tl, bool) or tl <= 0):
        raise CaseValidationError(f"{ctx}: time_limit_minutes must be a positive integer or null")

    # hypotheses
    hyps = {}
    for h in _req(data, "hypotheses", list, ctx):
        hid = _req(h, "id", str, f"{ctx} hypothesis")
        if hid in hyps:
            raise CaseValidationError(f"{ctx}: duplicate hypothesis id {hid}")
        level = _req(h, "level", str, f"{ctx} {hid}")
        if level not in LEVELS:
            raise CaseValidationError(f"{ctx} {hid}: level must be one of {LEVELS}")
        kws = _str_list(h, "match_keywords", f"{ctx} {hid}")
        hyps[hid] = CaseHypothesis(hid, _req(h, "statement", str, f"{ctx} {hid}"), level, kws)
    if not any(h.level == "correct" for h in hyps.values()):
        raise CaseValidationError(f"{ctx}: at least one hypothesis must have level 'correct'")

    # evidence
    evidence = {}
    for e in _req(data, "evidence", list, ctx):
        eid = _req(e, "id", str, f"{ctx} evidence")
        ectx = f"{ctx} {eid}"
        if eid in evidence:
            raise CaseValidationError(f"{ctx}: duplicate evidence id {eid}")
        sup = _str_list(e, "supports", ectx, [])
        con = _str_list(e, "contradicts", ectx, [])
        for hid in sup + con:
            if hid not in hyps:
                raise CaseValidationError(f"{ectx}: unknown hypothesis {hid}")
        if set(sup) & set(con):
            raise CaseValidationError(f"{ectx}: cannot both support and contradict the same hypothesis")
        initial = e.get("initial", False)
        if not isinstance(initial, bool):
            raise CaseValidationError(f"{ectx}: 'initial' must be boolean")
        rh = e.get("red_herring", False)
        if not isinstance(rh, bool):
            raise CaseValidationError(f"{ectx}: 'red_herring' must be boolean")
        unlock = e.get("unlock", {})
        if not isinstance(unlock, dict):
            raise CaseValidationError(f"{ectx}: 'unlock' must be an object")
        kws = _str_list(unlock, "keywords", ectx, [])
        req = _str_list(unlock, "requires", ectx, [])
        adaptive = e.get("adaptive")
        if adaptive is not None:
            if not isinstance(adaptive, dict):
                raise CaseValidationError(f"{ectx}: 'adaptive' must be an object")
            trig = adaptive.get("trigger")
            if trig not in ("stall", "strong"):
                raise CaseValidationError(f"{ectx}: adaptive.trigger must be 'stall' or 'strong'")
            after = adaptive.get("after")
            if not isinstance(after, int) or isinstance(after, bool) or after <= 0:
                raise CaseValidationError(f"{ectx}: adaptive.after must be a positive integer")
            adaptive = {"trigger": trig, "after": after}
        if not initial and not kws and adaptive is None:
            raise CaseValidationError(f"{ectx}: non-initial evidence needs unlock.keywords or an 'adaptive' trigger")
        evidence[eid] = Evidence(
            id=eid, title=_req(e, "title", str, ectx), type=_req(e, "type", str, ectx),
            content=_req(e, "content", str, ectx),
            reliability=_unit(e, "reliability", ectx), relevance=_unit(e, "relevance", ectx),
            directness=_unit(e, "directness", ectx), supports=sup, contradicts=con,
            red_herring=rh, initial=initial, unlock_keywords=kws, unlock_requires=req, adaptive=adaptive)
    if not evidence:
        raise CaseValidationError(f"{ctx}: needs at least one evidence item")
    if not any(e.initial for e in evidence.values()):
        raise CaseValidationError(f"{ctx}: needs at least one initial evidence item")
    for e in evidence.values():
        for r in e.unlock_requires:
            if r not in evidence:
                raise CaseValidationError(f"{ctx} {e.id}: unlock.requires unknown evidence {r}")
            if r == e.id:
                raise CaseValidationError(f"{ctx} {e.id}: cannot require itself")

    # reachability: every item must be discoverable (also catches require-cycles)
    reached = {e.id for e in evidence.values() if e.initial}
    changed = True
    while changed:
        changed = False
        for e in evidence.values():
            if e.id not in reached and all(r in reached for r in e.unlock_requires):
                reached.add(e.id)
                changed = True
    unreachable = set(evidence) - reached
    if unreachable:
        raise CaseValidationError(f"{ctx}: undiscoverable evidence (cyclic/unmet requires): {sorted(unreachable)}")

    # contradictions
    contradictions = []
    seen = set()
    for c in data.get("contradictions", []):
        cid2 = _req(c, "id", str, f"{ctx} contradiction")
        pair = _str_list(c, "evidence", f"{ctx} {cid2}")
        if len(pair) != 2 or pair[0] == pair[1] or any(p not in evidence for p in pair):
            raise CaseValidationError(f"{ctx} {cid2}: needs two distinct known evidence ids")
        fs = frozenset(pair)
        if cid2 in seen or any(fs == p for _, p in contradictions):
            raise CaseValidationError(f"{ctx}: duplicate contradiction {cid2}")
        seen.add(cid2)
        contradictions.append((cid2, fs))

    required = _str_list(data, "required_evidence", ctx, [])
    for r in required:
        if r not in evidence:
            raise CaseValidationError(f"{ctx}: required_evidence unknown id {r}")

    skills = _str_list(data, "assessed_skills", ctx, list(DEFAULT_SKILL_WEIGHTS))
    for s in skills:
        if s not in DEFAULT_SKILL_WEIGHTS:
            raise CaseValidationError(f"{ctx}: skill {s!r} is not measurable yet")
    sw = data.get("scoring_weights", {})
    ow = data.get("outcome_weights", dict(DEFAULT_OUTCOME_WEIGHTS))
    try:
        if not isinstance(sw, dict) or not isinstance(ow, dict):
            raise ValueError("scoring_weights/outcome_weights must be objects")
        validate_skill_weights(sw)
        validate_outcome_weights(ow)
    except ValueError as exc:
        raise CaseValidationError(f"{ctx}: {exc}") from None

    return Case(
        case_id=cid, title=_req(data, "title", str, ctx), domain=_req(data, "domain", str, ctx),
        difficulty=_req(data, "difficulty", str, ctx), overview=_req(data, "overview", str, ctx),
        objective=_req(data, "objective", str, ctx), time_limit_minutes=tl,
        evidence=evidence, hypotheses=hyps, contradictions=tuple(contradictions),
        required_evidence=required, assessed_skills=skills,
        skill_weights=dict(sw), outcome_weights=dict(ow))


def load_case(path) -> Case:
    with open(Path(path), encoding="utf-8") as fh:
        return parse_case(json.load(fh))
