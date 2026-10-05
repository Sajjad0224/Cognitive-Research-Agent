"""Maps free-text statements to authored case hypotheses (deterministic; LLM-swappable)."""
from __future__ import annotations

from typing import Optional

from .case import Case
from .textutil import has_keyword, normalize


class KeywordMatcher:
    """Returns the case hypothesis id whose keywords best cover the text, or None.

    None is returned when nothing clears the threshold or the top two candidates tie
    (an ambiguous statement is never silently credited).
    """

    def __init__(self, min_hits: int = 2, min_ratio: float = 0.4):
        self.min_hits = min_hits
        self.min_ratio = min_ratio

    def match(self, case: Case, text: str) -> Optional[str]:
        norm = normalize(text)
        scored = []
        for h in case.hypotheses.values():
            if not h.match_keywords:
                continue
            hits = sum(1 for k in h.match_keywords if has_keyword(norm, k))
            ratio = hits / len(h.match_keywords)
            if hits >= self.min_hits and ratio >= self.min_ratio:
                scored.append((ratio, hits, h.id))
        if not scored:
            return None
        scored.sort(key=lambda t: (-t[0], -t[1]))
        if len(scored) > 1 and scored[0][0] == scored[1][0] and scored[0][1] == scored[1][1]:
            return None
        return scored[0][2]
