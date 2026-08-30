"""Thin REST client for the two Groq endpoints the assistant needs.

Deliberately NOT built on the `groq` PyPI package. Groq serves an
OpenAI-compatible surface, so a chat completion is one POST with a Bearer
header and there is nothing an SDK would do for us that json+httpx doesn't --
and httpx is already pinned in requirements.txt, so this adds no dependency.
Same reasoning as app/razorpayx_client.py, and the structure here deliberately
mirrors that module: module-level functions rather than a client object, one
shared _raise_for_status, and auth read lazily per call so a config change
isn't frozen at import time.

  Chat        POST /openai/v1/chat/completions
  Guard       same endpoint, with the classifier model

Two things about this vendor that shape the code below, both established by
probing the live API rather than assumed:

1. The gpt-oss models are REASONING models. They spend completion tokens on
   hidden reasoning before emitting any content, and that spend counts against
   max_tokens. A max_tokens of 10 came back with finish_reason='length' and
   content='' -- a successful HTTP 200 carrying nothing. chat() therefore
   treats empty content as a real failure mode with its own message, instead
   of returning "" and letting a blank bubble appear in the UI.

2. llama-prompt-guard-2 is a classifier, not a chat model. It returns the
   probability of a prompt injection as a bare numeric string in the normal
   message.content slot ("0.00038..." / "0.99945..."), and bills
   total_tokens: 0 -- so screening every message is free against the account's
   tokens/minute ceiling. It costs one request, not one token.

No network from tests: httpx.post is monkeypatched, see tests/test_groq_client.py.
"""
from __future__ import annotations

import httpx

from app import config

BASE_URL = "https://api.groq.com/openai/v1"
TIMEOUT_SECONDS = 30

# Small, fixed classifier. Not configurable: the cutoff in config is calibrated
# against this specific model's output scale, so swapping one without the other
# would silently change what gets blocked.
GUARD_MODEL = "meta-llama/llama-prompt-guard-2-86m"


class GroqError(RuntimeError):
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self.payload = payload
        super().__init__(f"Groq API error {status_code}: {payload}")


def _headers() -> dict:
    # Resolved per call, not at import: mirrors razorpayx_client._auth().
    return {
        "Authorization": f"Bearer {config.GROQ_API_KEY}",
        "Content-Type": "application/json",
    }


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        try:
            payload = resp.json()
        except ValueError:
            payload = {"raw": resp.text}
        raise GroqError(resp.status_code, payload)


def _content(resp_json: dict) -> str:
    """First choice's message content, or '' when the model produced none."""
    choices = resp_json.get("choices") or []
    if not choices:
        return ""
    return (choices[0].get("message", {}).get("content") or "").strip()


def chat(messages: list[dict], *, model: str = "", max_tokens: int = 0) -> str:
    """One chat completion. `messages` is already-built OpenAI-shaped turns.

    Raises GroqError on any non-2xx, and on a 200 that carries no content --
    which is what a reasoning model returns when max_tokens ran out before it
    finished thinking. Surfacing that as an error is deliberate: the caller can
    say something useful, where returning "" would render an empty reply.
    """
    body = {
        "model": model or config.GROQ_MODEL,
        "messages": messages,
        "max_tokens": max_tokens or config.ASSISTANT_MAX_TOKENS,
        # Low but not zero. This answers questions about fixed numbers, so
        # near-deterministic phrasing is what we want; 0 makes repeated
        # questions read like a stuck record.
        "temperature": 0.2,
    }
    resp = httpx.post(f"{BASE_URL}/chat/completions", json=body,
                      headers=_headers(), timeout=TIMEOUT_SECONDS)
    _raise_for_status(resp)
    data = resp.json()
    text = _content(data)
    if not text:
        finish = (data.get("choices") or [{}])[0].get("finish_reason")
        raise GroqError(502, {"error": "model returned no content", "finish_reason": finish})
    return text


def injection_score(text: str) -> float:
    """P(prompt injection) for `text`, from llama-prompt-guard-2.

    Returns 0.0 rather than raising if the classifier is unavailable or its
    output isn't parseable: the guard is defence in depth, and the system
    prompt's own constraints are the primary control. Failing closed here would
    take the whole assistant down whenever the classifier hiccups, which trades
    a small risk for a certain outage.
    """
    body = {"model": GUARD_MODEL, "messages": [{"role": "user", "content": text}]}
    try:
        resp = httpx.post(f"{BASE_URL}/chat/completions", json=body,
                          headers=_headers(), timeout=TIMEOUT_SECONDS)
        _raise_for_status(resp)
        return float(_content(resp.json()))
    except (GroqError, httpx.HTTPError, ValueError, TypeError):
        return 0.0
