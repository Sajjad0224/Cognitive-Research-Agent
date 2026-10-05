"""Tiny deterministic text helpers shared by the engine and the matcher."""
import re

_TOKEN = re.compile(r"[a-z0-9']+")


def normalize(text: str) -> str:
    return " ".join(_TOKEN.findall(text.lower()))


def has_keyword(norm_text: str, keyword: str) -> bool:
    """True if `keyword` (single or multi word) occurs starting at a token boundary.

    Prefix matching lets "delete" match "deleted" and "accident" match "accidentally",
    while "ken" does not match "token" or "taken".
    """
    k = normalize(keyword)
    if not k:
        return False
    return (" " + k) in (" " + norm_text)
