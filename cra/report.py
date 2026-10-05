"""Explainable report built from evaluate() output. Every score maps to observable events."""
from __future__ import annotations

from .case import Case
from .scoring import OUTCOME_SCORES, WEAK_RELIABILITY, behavior_metrics, evaluate

LABELS = {
    "information_coverage": "coverage of key evidence", "evidence_prioritization": "evidence prioritisation",
    "search_efficiency": "search efficiency", "question_quality": "question quality",
    "evidence_evaluation": "judging evidence relevance", "contradiction_detection": "contradiction detection",
    "alternative_hypotheses": "considering alternative hypotheses", "assumption_testing": "testing assumptions",
    "bias_resistance": "resisting confirmation bias", "link_accuracy": "linking evidence soundly",
    "information_synthesis": "synthesising multiple evidence items", "evidence_used": "evidence behind the conclusion",
    "uncertainty_handling": "calibrated confidence", "decision_quality": "decision quality",
    "logical_consistency": "logical consistency", "hypothesis_validation": "validating the final hypothesis",
    "solution_reliability": "solution reliability", "revision_after_contradiction": "revising after contradictions",
    "revision_rate": "revising hypotheses",
}

RECOMMENDATIONS = {
    "critical_thinking": "Practise cases with red herrings; before concluding, write one alternative explanation and the evidence that would refute it.",
    "analytical_reasoning": "Build an explicit evidence-to-hypothesis map and check each link supports or contradicts before relying on it.",
    "research_ability": "Plan questions before asking: list the unknowns, then ask one targeted question per unknown.",
    "hypothesis_testing": "Try cases with several competing hypotheses; deliberately seek evidence that would disprove your favourite.",
    "evidence_evaluation": "Rate each item's source reliability and directness, and mark weak or irrelevant items explicitly.",
    "decision_making": "State your confidence and cite the specific evidence behind your final decision.",
    "attention_to_detail": "Open every key document and compare dates, times and names across items to expose inconsistencies.",
    "adaptability": "When contradicting evidence appears, revise or retire the affected hypothesis and note why.",
}


def _explain(skill: str, result: dict) -> str:
    if result["score"] is None:
        return "Not enough observable behaviour to assess this skill in this case."
    rows = [b for b in result["breakdown"] if b["value"] is not None]
    strong = sorted((b for b in rows if b["value"] >= 0.75), key=lambda b: -b["value"] * b["effective_weight"])
    weak = sorted((b for b in rows if b["value"] <= 0.4), key=lambda b: b["value"])
    parts = []
    if result["data_coverage"] < 0.5:
        parts.append(f"Low confidence: only {round(result['data_coverage'] * 100)}% of this skill's indicators had data.")
    if strong:
        parts.append("Strengths: " + "; ".join(f"{LABELS[b['indicator']]} ({b['detail']})" for b in strong[:2]) + ".")
    if weak:
        parts.append("Weaknesses: " + "; ".join(f"{LABELS[b['indicator']]} ({b['detail']})" for b in weak[:2]) + ".")
    missing = [b for b in result["breakdown"] if b["value"] is None]
    if missing:
        parts.append("No data for: " + ", ".join(LABELS[b["indicator"]] for b in missing) + ".")
    return " ".join(parts) or "Mixed performance across indicators."


def build_report(case: Case, events, matcher=None) -> dict:
    r = evaluate(case, events, matcher)
    st, F = r["state"], r["features"]
    level = r["conclusion_level"]
    status = {"correct": "solved", "partial": "partially_solved"}.get(level, "unsolved") if level else "unsubmitted"

    flags = []
    rel = F["solution_reliability"].value
    if level == "correct" and rel is not None and rel < WEAK_RELIABILITY:
        flags.append("Correct outcome, weak evidentiary reasoning.")
    if level == "unsupported":
        flags.append("Conclusion does not match any accepted explanation.")
    if st.conclusion and F["logical_consistency"].value is not None and F["logical_consistency"].value < 1.0 \
            and st.conclusion["cited"]:
        flags.append("Some cited evidence contradicts the stated conclusion.")

    skills = {}
    for name, res in r["skills"].items():
        skills[name] = {"score": res["score"], "data_coverage": res["data_coverage"],
                        "explanation": _explain(name, res), "breakdown": res["breakdown"]}

    scored = [(n, s["score"]) for n, s in skills.items() if s["score"] is not None]
    strengths = [f"{n.replace('_', ' ').title()} ({s})" for n, s in scored if s >= 75]
    weak_ind = sorted((b for s in skills.values() for b in s["breakdown"] if b["value"] is not None and b["value"] < 0.5),
                      key=lambda b: b["value"])
    seen, improvements = set(), []
    for b in weak_ind:
        if b["indicator"] not in seen:
            seen.add(b["indicator"])
            improvements.append(f"Improve {LABELS[b['indicator']]}: {b['detail']}.")

    missed = [f"Never opened key evidence {e}: {case.evidence[e].title}" for e in case.required_evidence
              if e not in st.viewed]
    unopened = [e for e in st.discovered if e not in st.viewed and case.evidence[e].is_relevant
                and e not in case.required_evidence]
    missed += [f"Discovered but never opened relevant evidence {e}: {case.evidence[e].title}" for e in unopened]
    flagged = {f["pair"] for f in st.flags}
    for cid, pair in case.contradictions:
        if all(x in st.discovered for x in pair) and pair not in flagged:
            a, b = sorted(pair)
            missed.append(f"Available contradiction between {a} and {b} was not flagged")

    decisions = []
    if st.hypotheses:
        h = min(st.hypotheses.values(), key=lambda h: h["created_seq"])
        decisions.append({"event_id": h["event_id"], "moment": f"First hypothesis: \"{h['statement']}\""})
    for l in st.links:
        if l["relation"] == "contradicts":
            decisions.append({"event_id": l["event_id"], "moment": f"First contradicting evidence recorded ({l['evidence_id']} vs {l['hypothesis_id']})"})
            break
    for h in st.hypotheses.values():
        for eid in h["update_event_ids"][:1]:
            decisions.append({"event_id": eid, "moment": f"Revised {h['id']}"})
    if st.conclusion:
        decisions.append({"event_id": st.conclusion["event_id"], "moment": "Final conclusion submitted"})

    weakest = sorted(scored, key=lambda t: t[1])[:2]
    return {
        "case_id": case.case_id, "title": case.title, "session_id": st.session_id, "user_id": st.user_id,
        "started_at": st.started_at, "completed_at": st.conclusion["ts"] if st.conclusion else None,
        "status": status, "conclusion_level": level, "performance": r["performance"],
        "solution_reliability": None if rel is None else round(rel, 3), "flags": flags,
        "skills": skills, "behavior": behavior_metrics(case, st, F),
        "strengths": strengths, "improvements": improvements, "missed_opportunities": missed,
        "key_decision_points": decisions,
        "recommended_development": [RECOMMENDATIONS[n] for n, _ in weakest],
        "notice": "Scores describe observed investigation behaviour in this case only; "
                  "they are not a diagnosis or measure of intelligence.",
    }


def render_text(rep: dict) -> str:
    L = [f"CASE PERFORMANCE REPORT - {rep['title']} ({rep['case_id']})", "=" * 60,
         f"Status: {rep['status']}   Final performance: {rep['performance']['final']}",
         f"  Outcome {rep['performance']['outcome']} | Reasoning {rep['performance']['reasoning']} | "
         f"Process {rep['performance']['process']} | Solution reliability {rep['solution_reliability']}"]
    for f in rep["flags"]:
        L.append(f"  ! {f}")
    L += ["", "COGNITIVE PROFILE"]
    for n, s in rep["skills"].items():
        shown = "n/a" if s["score"] is None else f"{s['score']}/100"
        L.append(f"- {n.replace('_', ' ').title()} - {shown}")
        L.append(f"    {s['explanation']}")
    L += ["", "INVESTIGATION BEHAVIOR"] + [f"- {k.replace('_', ' ')}: {v}" for k, v in rep["behavior"].items()]
    for title, key in (("STRENGTHS", "strengths"), ("AREAS FOR IMPROVEMENT", "improvements"),
                       ("MISSED OPPORTUNITIES", "missed_opportunities"),
                       ("RECOMMENDED DEVELOPMENT", "recommended_development")):
        L += ["", title] + ([f"- {x}" for x in rep[key]] or ["- none identified"])
    L += ["", "KEY DECISION POINTS"] + [f"- {d['moment']} [{d['event_id']}]" for d in rep["key_decision_points"]]
    L += ["", rep["notice"]]
    return "\n".join(L)
