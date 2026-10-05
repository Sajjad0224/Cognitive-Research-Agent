"""Thin, swappable wrapper around the Anthropic Messages API.

Nothing in cra/scoring.py or cra/report.py ever imports this module: those stay
100% deterministic (see README, "Separation of responsibilities"). This module
only powers optional natural-language *quality* — phrasing of in-case answers,
matching free-text statements to authored hypotheses, and narrative flavour text
added on top of an already-computed report. If no API key is configured, or a
call fails, every caller in cra/llm_adapters.py falls back to the deterministic
Phase 1-3 behaviour, so the platform works fully with LLM_ENABLED=false.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Optional, Protocol

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-3-5-haiku-latest"


class LLMError(Exception):
    """Raised for any failure to get a usable completion (network, auth, parsing)."""


class LLMClient(Protocol):
    def complete(self, system: str, user: str, max_tokens: int = 300) -> str: ...


class AnthropicClient:
    """Real client. Never constructed unless an API key is available (see from_env)."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, timeout: float = 20.0):
        if not api_key:
            raise LLMError("no API key provided")
        self.api_key, self.model, self.timeout = api_key, model, timeout

    def complete(self, system: str, user: str, max_tokens: int = 300) -> str:
        body = json.dumps({
            "model": self.model, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": user}],
        }).encode("utf-8")
        req = urllib.request.Request(API_URL, data=body, method="POST", headers={
            "Content-Type": "application/json", "x-api-key": self.api_key,
            "anthropic-version": API_VERSION})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
        except urllib.error.URLError as exc:
            raise LLMError(f"request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LLMError(f"bad response: {exc}") from exc
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        if not text.strip():
            raise LLMError("empty completion")
        return text


def from_env() -> Optional[AnthropicClient]:
    """Returns a client iff ANTHROPIC_API_KEY is set and LLM_ENABLED != 'false'."""
    if os.environ.get("LLM_ENABLED", "true").lower() == "false":
        return None
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    return AnthropicClient(key, os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL))
