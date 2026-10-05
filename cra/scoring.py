"""Deterministic multi-factor evaluation. No LLM is involved in any number produced here.

events -> state -> features (value in [0,1] or None, with detail + supporting event ids)
       -> skill scores (weighted, renormalised over indicators that have data)
       -> reasoning / process / outcome -> final performance
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .case import Case
from .matcher import KeywordMatcher
from .skills import (DEFAULT_OUTCOME_WEIGHTS, DEFAULT_SKILL_WEIGHTS, FEATURE_NAMES)
from .state import InvestigationState, reduce_events

OUTCOME_SCORES = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0, "unsupported": 0.0}
WEAK_RELIABILITY = 0.35


@dataclass
class Feature:
    value: Optional[float]
    detail: str
    event_ids: list = field(default_factory=list)


def _f(value, detail, ids=()):
    if value is not None:
        value = max(0.0, min(1.0, float(value)))
    return Feature(value, detail, list(ids))


def _na(detail):
    return Feature(None, detail, [])


def link_is_valid(case: Case, evidence_id: str, case_hyp_id: str, relation: str) -> bool:
    ev = case.evidence[evidence_id]
    return case_hyp_id in (ev.supports if relation == "supports" else ev.contradicts)


def conclusion_level(case: Case, state: InvestigationState, matcher) -> tuple:
    """(case_hypothesis_id | None, level) where level in OUTCOME_SCORES."""
    if state.conclusion is None:
        return None, None
    hid = matcher.match(case, state.conclusion["text"])
    return hid, (case.hypotheses[hid].level if hid else "unsupported")


def compute_features(case: Case, state: InvestigationState, matcher) -> dict:
    F = {}
    n_hyp = len(state.hypotheses)
    mapping = {hid: matcher.match(case, h["statement"]) for hid, h in state.hypotheses.items()}
    concl = state.conclusion
    c_hid, c_level = conclusion_level(case, state, matcher)

    # --- research process ---
    req = case.required_evidence
    if req:
        got = [r for r in req if r in state.viewed]
        F["information_coverage"] = _f(len(got) / len(req), f"viewed {len(got)}/{len(req)} key evidence items")
    else:
        F["information_coverage"] = _na("case defines no required evidence")

    chosen = [e for e in state.viewed if state.discovered_via.get(e) != "initial"]
    if chosen:
        first = chosen[:5]
        rel = [e for e in first if case.evidence[e].is_relevant]
        F["evidence_prioritization"] = _f(len(rel) / len(first),
            f"{len(rel)}/{len(first)} of the first investigator-discovered items opened were relevant")
    else:
        F["evidence_prioritization"] = _na("no discovered evidence was opened")

    nq = len(state.questions)
    if nq:
        prod = [q for q in state.questions if q["new_evidence"]]
        F["question_quality"] = _f(len(prod) / nq, f"{len(prod)}/{nq} questions uncovered new evidence "
                                   f"({sum(q['repeated'] for q in state.questions)} repeated)",
                                   [q["event_id"] for q in prod])
        found = {e for q in state.questions for e in q["new_evidence"] if case.evidence[e].is_relevant}
        F["search_efficiency"] = _f(len(found) / nq, f"{len(found)} relevant items found through {nq} questions")
    else:
        F["question_quality"] = _na("no questions asked")
        F["search_efficiency"] = _na("no questions asked")

    # --- evidence evaluation ---
    if state.marks:
        ok = [e for e, m in state.marks.items() if m["relevant"] == case.evidence[e].is_relevant]
        F["evidence_evaluation"] = _f(len(ok) / len(state.marks),
                                      f"{len(ok)}/{len(state.marks)} relevance judgements were correct",
                                      [m["event_id"] for m in state.marks.values()])
    else:
        F["evidence_evaluation"] = _na("no relevance judgements made")

    # --- contradictions ---
    detectable = [(cid, pair) for cid, pair in case.contradictions if all(x in state.discovered for x in pair)]
    if detectable:
        valid_flags = [f for f in state.flags if any(f["pair"] == p for _, p in case.contradictions)]
        found = {f["pair"] for f in valid_flags}
        recall = sum(1 for _, p in detectable if p in found) / len(detectable)
        precision = (len(valid_flags) / len(state.flags)) if state.flags else 1.0
        F["contradiction_detection"] = _f(recall * precision,
            f"identified {sum(1 for _, p in detectable if p in found)}/{len(detectable)} contradictions; "
            f"{len(state.flags) - len(valid_flags)} flag(s) were not real contradictions",
            [f["event_id"] for f in state.flags])
    else:
        F["contradiction_detection"] = _na("no contradictions were discoverable yet")

    # --- hypotheses ---
    F["alternative_hypotheses"] = _f(min(n_hyp, 3) / 3, f"formed {n_hyp} hypothesis(es); 3+ earns full credit",
                                     [h["event_id"] for h in state.hypotheses.values()])
    if n_hyp:
        tested = [hid for hid, h in state.hypotheses.items()
                  if h["update_seqs"] or any(l["hypothesis_id"] == hid and l["relation"] == "contradicts"
                                             for l in state.links)]
        F["assumption_testing"] = _f(len(tested) / n_hyp,
                                     f"{len(tested)}/{n_hyp} hypotheses were challenged (contradicting evidence or revision)")
        F["information_synthesis"] = _f(
            sum(1 for hid in state.hypotheses
                if len({l["evidence_id"] for l in state.links if l["hypothesis_id"] == hid}) >= 2) / n_hyp,
            "share of hypotheses built on 2+ distinct evidence items")
        F["revision_rate"] = _f(sum(1 for h in state.hypotheses.values() if h["update_seqs"]) / n_hyp,
                                "share of hypotheses that were revised")
    else:
        for k, d in (("assumption_testing", "no hypotheses"), ("information_synthesis", "no hypotheses"),
                     ("revision_rate", "no hypotheses")):
            F[k] = _na(d)

    nl = len(state.links)
    if nl >= 2:
        sup = sum(1 for l in state.links if l["relation"] == "supports")
        F["bias_resistance"] = _f(1 - max(0.0, sup / nl - 0.5) * 2,
                                  f"{sup}/{nl} evidence links were confirming (supporting) rather than testing")
    else:
        F["bias_resistance"] = _na("fewer than 2 evidence links")

    evaluable = [(l, mapping.get(l["hypothesis_id"])) for l in state.links]
    evaluable = [(l, h) for l, h in evaluable if h]
    if evaluable:
        good = [l for l, h in evaluable if link_is_valid(case, l["evidence_id"], h, l["relation"])]
        F["link_accuracy"] = _f(len(good) / len(evaluable), f"{len(good)}/{len(evaluable)} evidence links were sound",
                                [l["event_id"] for l, _ in evaluable])
    else:
        F["link_accuracy"] = _na("no evaluable evidence links")

    contra_hyps = {}
    for l in state.links:
        if l["relation"] == "contradicts":
            contra_hyps.setdefault(l["hypothesis_id"], min(l["seq"], contra_hyps.get(l["hypothesis_id"], 10**18)))
    if contra_hyps:
        revised = [hid for hid, seq in contra_hyps.items()
                   if any(u > seq for u in state.hypotheses[hid]["update_seqs"])]
        F["revision_after_contradiction"] = _f(len(revised) / len(contra_hyps),
            f"revised {len(revised)}/{len(contra_hyps)} hypotheses after recording contradicting evidence")
    else:
        F["revision_after_contradiction"] = _na("no contradicting evidence was recorded against any hypothesis")

    # --- conclusion-dependent ---
    if concl is None:
        for k in ("evidence_used", "uncertainty_handling", "decision_quality", "logical_consistency",
                  "hypothesis_validation", "solution_reliability"):
            F[k] = _na("no conclusion submitted")
    else:
        oscore = OUTCOME_SCORES[c_level]
        F["decision_quality"] = _f(oscore, f"conclusion assessed as '{c_level}'", [concl["event_id"]])
        F["uncertainty_handling"] = _f(1 - abs(concl["confidence"] - oscore),
                                       f"stated confidence {concl['confidence']:.2f} vs outcome {oscore:.2f}",
                                       [concl["event_id"]])
        cited = concl["cited"]
        if not cited:
            F["evidence_used"] = _f(0.0, "conclusion cited no evidence", [concl["event_id"]])
            F["logical_consistency"] = _f(0.0, "conclusion cited no evidence", [concl["event_id"]])
        elif c_hid is None:
            F["evidence_used"] = _f(0.0, "conclusion matches no accepted explanation", [concl["event_id"]])
            F["logical_consistency"] = _f(0.0, "conclusion matches no accepted explanation", [concl["event_id"]])
        else:
            sup = [c for c in cited if c_hid in case.evidence[c].supports]
            con = [c for c in cited if c_hid in case.evidence[c].contradicts]
            F["evidence_used"] = _f(len(sup) / len(cited), f"{len(sup)}/{len(cited)} cited items support the conclusion",
                                    [concl["event_id"]])
            F["logical_consistency"] = _f(1 - len(con) / len(cited),
                                          f"{len(con)}/{len(cited)} cited items contradict the conclusion",
                                          [concl["event_id"]])
        if c_hid is None:
            F["hypothesis_validation"] = _f(0.0, "no tested hypothesis matches the conclusion")
        else:
            mine = [hid for hid, m in mapping.items() if m == c_hid]
            has_support = any(l["hypothesis_id"] in mine and l["relation"] == "supports"
                              and link_is_valid(case, l["evidence_id"], c_hid, "supports") for l in state.links)
            alts = n_hyp >= 2 or any(l["relation"] == "contradicts" for l in state.links)
            F["hypothesis_validation"] = _f(0.5 * has_support + 0.5 * alts,
                f"{'validated' if has_support else 'no validated'} support for the concluded hypothesis; "
                f"{'alternatives/contradictions considered' if alts else 'no alternatives considered'}")
        cov = F["information_coverage"].value
        F["solution_reliability"] = _f(
            (1.0 if cov is None else cov) * F["logical_consistency"].value * F["hypothesis_validation"].value,
            "coverage x logical consistency x hypothesis validation", [concl["event_id"]])

    assert set(F) == set(FEATURE_NAMES), set(F) ^ set(FEATURE_NAMES)
    return F


def _weighted(items):
    """items: [(value|None, weight)] -> renormalised mean or None."""
    avail = [(v, w) for v, w in items if v is not None and w > 0]
    tot = sum(w for _, w in avail)
    return None if tot == 0 else sum(v * w for v, w in avail) / tot


def compute_skills(case: Case, F: dict) -> dict:
    out = {}
    for skill in case.assessed_skills:
        weights = case.skill_weights.get(skill, DEFAULT_SKILL_WEIGHTS[skill])
        avail_total = sum(w for i, w in weights.items() if F[i].value is not None and w > 0)
        breakdown = []
        for ind, w in weights.items():
            f = F[ind]
            eff = (w / avail_total) if (f.value is not None and w > 0 and avail_total) else 0.0
            breakdown.append({"indicator": ind, "value": f.value, "configured_weight": w,
                              "effective_weight": eff, "detail": f.detail or "insufficient data",
                              "event_ids": f.event_ids})
        score = _weighted([(F[i].value, w) for i, w in weights.items()])
        total_cfg = sum(w for w in weights.values() if w > 0)
        out[skill] = {"score": None if score is None else round(score * 100, 1),
                      "data_coverage": round(avail_total / total_cfg, 3) if total_cfg else 0.0,
                      "breakdown": breakdown}
    return out


def compute_performance(case: Case, F: dict, c_level) -> dict:
    outcome = OUTCOME_SCORES[c_level] if c_level else 0.0
    reasoning = _weighted([(F[k].value, 0.2) for k in
                           ("evidence_used", "logical_consistency", "assumption_testing",
                            "contradiction_detection", "alternative_hypotheses")])
    process = _weighted([(F[k].value, 0.25) for k in
                         ("question_quality", "search_efficiency", "evidence_prioritization", "information_coverage")])
    ow = case.outcome_weights or DEFAULT_OUTCOME_WEIGHTS
    final = _weighted([(outcome, ow["outcome"]), (reasoning, ow["reasoning"]), (process, ow["process"])])
    r = lambda x: None if x is None else round(x * 100, 1)
    return {"outcome": r(outcome), "reasoning": r(reasoning), "process": r(process), "final": r(final)}


def behavior_metrics(case: Case, state: InvestigationState, F: dict) -> dict:
    def secs(a, b):
        return None if not a or not b else round((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds(), 3)
    first_h = min((h["created_ts"] for h in state.hypotheses.values()), default=None)
    valid = sum(1 for f in state.flags if any(f["pair"] == p for _, p in case.contradictions))
    return {
        "questions_asked": len(state.questions),
        "repeated_questions": sum(q["repeated"] for q in state.questions),
        "evidence_reviewed": f"{len(state.viewed)}/{len(case.evidence)}",
        "evidence_discovered": len(state.discovered),
        "hypotheses_created": len(state.hypotheses),
        "hypotheses_abandoned": sum(1 for h in state.hypotheses.values() if h["status"] == "abandoned"),
        "contradictions_identified": valid,
        "invalid_contradiction_flags": len(state.flags) - valid,
        "evidence_links": len(state.links),
        "time_to_first_hypothesis_s": secs(state.started_at, first_h),
        "time_to_conclusion_s": secs(state.started_at, state.conclusion["ts"] if state.conclusion else None),
    }


def evaluate(case: Case, events, matcher=None) -> dict:
    """Pure function of (case, events). Re-running it on the same events is always identical."""
    matcher = matcher or KeywordMatcher()
    state = reduce_events(events)
    F = compute_features(case, state, matcher)
    c_hid, c_level = conclusion_level(case, state, matcher)
    skills = compute_skills(case, F)
    perf = compute_performance(case, F, c_level)
    return {"state": state, "features": F, "skills": skills, "performance": perf,
            "conclusion_hypothesis": c_hid, "conclusion_level": c_level, "matcher": matcher}
