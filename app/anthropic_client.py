"""Anthropic Messages API client, for the cause classifier.

Deliberately separate from groq_client.py rather than a shared abstraction over
both. They speak different protocols -- Groq is OpenAI-shaped
(`/chat/completions`, `Authorization: Bearer`, `choices[].message.content`),
Anthropic is not (`/v1/messages`, `x-api-key`, `content[].text`, and a
`system` prompt that is a top-level field rather than a turn in `messages`).
A wrapper hiding that would have to leak it back out at every call site, and
the assistant has no reason to change providers just because the classifier
gained one.

Base URL is configurable because this is pointed at a gateway rather than at
Anthropic directly. Everything else about the request is the documented
Messages API, so the same code works against api.anthropic.com unchanged --
which matters: a reviewer with their own Anthropic key must be able to
reproduce the classifier's numbers without access to anyone's gateway account.
"""

from __future__ import annotations

import httpx

from app import config
from app.provider_errors import error_payload

TIMEOUT_SECONDS = 60

# Pinned. The Messages API is versioned by header, and an unpinned client
# silently changes response shape the day the provider ships a new default.
API_VERSION = "2023-06-01"


class AnthropicError(RuntimeError):
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self.payload = payload
        super().__init__(f"Anthropic API error {status_code}: {payload}")


def is_configured() -> bool:
    """No key means the classifier is off, exactly as an absent GROQ_API_KEY
    turns the assistant off. Callers branch on this rather than catching an
    error, so 'not configured' never reads as 'the model failed'."""
    return bool(config.ANTHROPIC_API_KEY)


def _headers() -> dict:
    # Resolved per call, not at import -- mirrors groq_client._headers() and
    # razorpayx_client._auth(), so a key rotated in .env takes effect on the
    # next request rather than the next restart.
    return {
        "x-api-key": config.ANTHROPIC_API_KEY,
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    }


def _text(resp_json: dict) -> str:
    """Concatenated text blocks. The Messages API returns a list of content
    blocks, and a response can legitimately carry more than one -- taking only
    the first would truncate without any sign that it had."""
    blocks = resp_json.get("content") or []
    parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
    return "".join(parts).strip()


def complete(
    *,
    system: str,
    messages: list[dict],
    model: str = "",
    max_tokens: int = 1024,
    temperature: float = 0.0,
) -> str:
    """One Messages call. Returns the assistant's text.

    temperature defaults to 0: this classifies ledger rows into a fixed set of
    causes, and there is no version of that task where sampling variety is a
    feature. It also makes the evaluation reproducible, which a scored
    comparison against deterministic rules has to be to mean anything.

    Raises AnthropicError on any non-2xx and on a 200 carrying no text, the
    same contract groq_client.chat() offers -- surfacing an empty completion as
    an error rather than returning "" so the caller can fall back deliberately
    instead of recording a blank answer as a real one.
    """
    body = {
        "model": model or config.ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": messages,
    }
    url = f"{config.ANTHROPIC_BASE_URL.rstrip('/')}/v1/messages"
    resp = httpx.post(url, json=body, headers=_headers(), timeout=TIMEOUT_SECONDS)

    if resp.status_code >= 400:
        raise AnthropicError(resp.status_code, error_payload(resp))

    data = resp.json()
    text = _text(data)
    if not text:
        raise AnthropicError(502, {
            "error": "model returned no text",
            "stop_reason": data.get("stop_reason"),
        })
    return text
