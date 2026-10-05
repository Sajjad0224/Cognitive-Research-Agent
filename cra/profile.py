"""Cross-case User Skill Profile (spec section 17). Deterministic and recomputed on demand from
the same event stream everything else uses — there is no separate mutable "profile" table, so a
profile can never drift out of sync with the sessions it summarises."""
from __future__ import annotations

from statistics import mean
from typing import Optional

from .events import EventStore
from .report import build_report
from .skills import DEFAULT_SKILL_WEIGHTS
from .state import reduce_events

TREND_MARGIN = 5.0  # points; smaller swings are reported as "steady" rather than noise


def _trend(scores_in_order: list) -> Optional[str]:
    if len(scores_in_order) < 2:
        return None
    mid = len(scores_in_order) // 2
    first, second = scores_in_order[:mid] or scores_in_order[:1], scores_in_order[mid:]
    delta = mean(second) - mean(first)
    if delta > TREND_MARGIN:
        return "improving"
    if delta < -TREND_MARGIN:
        return "needs_focus"
    return "steady"


def _patterns(entries: list) -> list:
    """A few simple, explicitly-labelled behavioural notes. Deliberately conservative: each one
    only fires on a clear, repeated signal across at least 2 completed cases, and only ever
    describes observed behaviour, never a trait, ability, or diagnosis."""
    closed = [e for e in entries if e["status"] != "unsubmitted"]
    if len(closed) < 2:
        return []
    out = []
    ratios = [e["behavior"]["time_to_first_hypothesis_s"] / e["behavior"]["time_to_conclusion_s"]
             for e in closed
             if e["behavior"]["time_to_first_hypothesis_s"] and e["behavior"]["time_to_conclusion_s"]]
    if len(ratios) >= 2 and mean(ratios) < 0.35:
        out.append("Often forms an initial hypothesis early in the investigation.")
    rep_ratio = [e["behavior"]["repeated_questions"] / e["behavior"]["questions_asked"]
                for e in closed if e["behavior"]["questions_asked"]]
    if rep_ratio and mean(rep_ratio) > 0.2:
        out.append("Sometimes repeats questions that already returned an answer.")
    weak_solved = sum(1 for e in closed if e["status"] == "solved" and (e["solution_reliability"] or 0) < 0.35)
    if weak_solved >= 2:
        out.append("Sometimes reaches the right answer without fully testing it (correct guesses over strong reasoning).")
    invalid = [e["behavior"]["invalid_contradiction_flags"] for e in closed]
    if invalid and mean(invalid) >= 1:
        out.append("Occasionally flags items as contradictory that the case does not treat as a real contradiction.")
    return out


def build_user_profile(cases: dict, store: EventStore, user_id: str, matcher=None) -> dict:
    entries, attempted = [], 0
    for sid in store.sessions_for_user(user_id):
        events = store.read(sid)
        st = reduce_events(events)
        attempted += 1
        if not st.closed or st.case_id not in cases:
            continue
        entries.append(build_report(cases[st.case_id], events, matcher))
    entries.sort(key=lambda e: e["completed_at"] or "")

    skills = {}
    for name in DEFAULT_SKILL_WEIGHTS:
        series = [e["skills"][name]["score"] for e in entries
                 if name in e["skills"] and e["skills"][name]["score"] is not None]
        if series:
            skills[name] = {"average": round(mean(series), 1), "trend": _trend(series),
                            "cases_assessed": len(series), "history": series}

    history = [{"session_id": e["session_id"], "case_id": e["case_id"], "title": e["title"],
               "status": e["status"], "final_score": e["performance"]["final"],
               "completed_at": e["completed_at"]} for e in entries]

    return {
        "user_id": user_id, "cases_attempted": attempted, "cases_completed": len(entries),
        "skills": skills, "behavioral_patterns": _patterns(entries), "history": history,
        "notice": "Aggregated from this account's completed cases only. Describes observed "
                  "investigation behaviour, not intelligence, personality, or any diagnosis.",
    }
