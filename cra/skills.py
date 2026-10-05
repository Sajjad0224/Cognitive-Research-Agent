"""Skill / indicator definitions. Single source of truth for scoring weights."""

FEATURE_NAMES = (
    "information_coverage", "evidence_prioritization", "search_efficiency",
    "question_quality", "evidence_evaluation", "contradiction_detection",
    "alternative_hypotheses", "assumption_testing", "bias_resistance",
    "link_accuracy", "information_synthesis", "evidence_used",
    "uncertainty_handling", "decision_quality", "logical_consistency",
    "hypothesis_validation", "solution_reliability",
    "revision_after_contradiction", "revision_rate",
)

DEFAULT_SKILL_WEIGHTS = {
    "critical_thinking": {
        "evidence_evaluation": 0.25, "contradiction_detection": 0.20,
        "alternative_hypotheses": 0.20, "assumption_testing": 0.20,
        "bias_resistance": 0.15,
    },
    "analytical_reasoning": {
        "link_accuracy": 0.40, "information_synthesis": 0.30,
        "contradiction_detection": 0.30,
    },
    "research_ability": {
        "question_quality": 0.30, "search_efficiency": 0.20,
        "evidence_prioritization": 0.20, "information_coverage": 0.30,
    },
    "hypothesis_testing": {
        "assumption_testing": 0.40, "alternative_hypotheses": 0.30,
        "bias_resistance": 0.30,
    },
    "evidence_evaluation": {
        "evidence_evaluation": 0.50, "link_accuracy": 0.30,
        "evidence_prioritization": 0.20,
    },
    "decision_making": {
        "decision_quality": 0.35, "evidence_used": 0.25,
        "uncertainty_handling": 0.25, "solution_reliability": 0.15,
    },
    "attention_to_detail": {
        "information_coverage": 0.40, "contradiction_detection": 0.30,
        "link_accuracy": 0.30,
    },
    "adaptability": {
        "revision_after_contradiction": 0.60, "revision_rate": 0.40,
    },
}

DEFAULT_OUTCOME_WEIGHTS = {"outcome": 0.40, "reasoning": 0.35, "process": 0.25}

# Skills named in the spec that cannot be measured from MVP signals. They are
# only assessable in cases that define their own indicators (future phase).
UNMEASURED_SKILLS = ("leadership", "communication", "time_management", "strategic_thinking")


def validate_skill_weights(skill_weights: dict) -> None:
    for skill, weights in skill_weights.items():
        if skill not in DEFAULT_SKILL_WEIGHTS:
            raise ValueError(f"unknown skill in scoring_weights: {skill!r}")
        if not isinstance(weights, dict) or not weights:
            raise ValueError(f"weights for {skill!r} must be a non-empty object")
        for ind, w in weights.items():
            if ind not in FEATURE_NAMES:
                raise ValueError(f"unknown indicator {ind!r} for skill {skill!r}")
            if not isinstance(w, (int, float)) or isinstance(w, bool) or w < 0:
                raise ValueError(f"weight for {skill}.{ind} must be a number >= 0")
        if sum(weights.values()) <= 0:
            raise ValueError(f"weights for {skill!r} must sum to > 0")


def validate_outcome_weights(ow: dict) -> None:
    if set(ow) != set(DEFAULT_OUTCOME_WEIGHTS):
        raise ValueError(f"outcome_weights must have exactly keys {sorted(DEFAULT_OUTCOME_WEIGHTS)}")
    for k, w in ow.items():
        if not isinstance(w, (int, float)) or isinstance(w, bool) or w < 0:
            raise ValueError(f"outcome_weights.{k} must be a number >= 0")
    if sum(ow.values()) <= 0:
        raise ValueError("outcome_weights must sum to > 0")
