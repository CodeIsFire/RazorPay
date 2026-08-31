"""Google Gemini client, for the cause classifier.

Third provider in this codebase and deliberately its own module, for the same
reason anthropic_client.py is: the wire formats have nothing in common. Groq is
OpenAI-shaped (`/chat/completions`, `Authorization: Bearer`,
`choices[].message.content`), Anthropic uses `/v1/messages` with `x-api-key`,
and Gemini uses `:generateContent` with `x-goog-api-key`, a `systemInstruction`
object, `contents[].parts[].text`, and `generationConfig`. An abstraction over
three shapes that different would leak all three back out at the call site.

Two things learned the hard way against the live API, both encoded below.

  THINKING TOKENS COUNT AGAINST maxOutputTokens. Gemini 2.5+ reports a
  `thoughtsTokenCount` -- a trivial "reply ok" spent 17 of them. Set the budget
  too low and the model spends it all reasoning and returns a candidate with no
  text at all, which looks exactly like a broken prompt. This codebase already
  hit the same trap with Groq's gpt-oss models; the note on
  RR_ASSISTANT_MAX_TOKENS in .env.example says so.

  A THROTTLED REQUEST RETURNS AN EMPTY-BODIED 404. Not a 429. During a burst of
  probes, `gemini-2.5-flash` -- which had answered seconds earlier and answered
  again seconds later -- returned 404 with zero bytes. A genuine missing model
  returns 404 with a JSON error body explaining itself (retired models say so
  by name). So the body, not the status, is what distinguishes "stop asking"
  from "ask again in a moment", and _is_transient() below keys off exactly that.
  Treating every 404 as fatal would have made this client fail permanently on
  what was a two-second blip.
"""

from __future__ import annotations

import time

import httpx

from app import config
from app.provider_errors import error_payload

TIMEOUT_SECONDS = 90

#: Transient failures get this many total attempts, with linear backoff. Small
#: on purpose: the classifier already falls back to the deterministic rules, so
#: a long retry ladder would only delay a result the caller can already handle.
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 1.5


class GeminiError(RuntimeError):
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self.payload = payload
        super().__init__(f"Gemini API error {status_code}: {payload}")


def is_configured() -> bool:
    return bool(config.GEMINI_API_KEY)


def _headers() -> dict:
    # Per call, not at import, so a key rotated in .env applies on the next
    # request. Mirrors groq_client._headers() and razorpayx_client._auth().
    return {"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"}


def _is_transient(resp: httpx.Response) -> bool:
    """Worth retrying? See the module docstring on empty-bodied 404s."""
    if resp.status_code in (429, 500, 502, 503, 504):
        return True
    # The distinguishing signal: a real "no such model" carries an explanatory
    # JSON error; a throttle carries nothing.
    return resp.status_code == 404 and not resp.content.strip()


def _retry_after(resp: httpx.Response) -> float | None:
    """The server's own suggested wait, from google.rpc.RetryInfo.

    Worth reading rather than guessing: a 429 here carries `retryDelay: "43s"`,
    and a fixed 1.5s backoff would burn all three attempts inside the window
    and report a permanent failure for something that resolves on its own. The
    quota that produces it is per-day, though (20 requests on the free tier),
    so honouring the delay only helps a per-minute limit -- a daily one is
    reported honestly rather than slept through.
    """
    try:
        details = resp.json().get("error", {}).get("details", [])
    except ValueError:
        return None
    for d in details:
        if d.get("@type", "").endswith("RetryInfo"):
            raw = str(d.get("retryDelay", "")).rstrip("s")
            try:
                return float(raw)
            except ValueError:
                return None
    return None



def _text(payload: dict) -> str:
    """Concatenated text parts of the first candidate.

    A candidate can carry several parts and taking only the first would
    truncate silently. Returns '' when the model produced no text at all --
    generate() turns that into an error rather than an empty answer.
    """
    candidates = payload.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts).strip()


def generate(
    *,
    system: str,
    prompt: str,
    model: str = "",
    max_output_tokens: int = 2048,
    temperature: float = 0.0,
) -> str:
    """One generateContent call. Returns the model's text.

    temperature defaults to 0: this sorts ledger rows into a fixed set of
    causes, and there is no version of that task improved by sampling variety.
    It also makes the scored comparison against deterministic rules
    reproducible, which it has to be to mean anything.

    Raises GeminiError on a non-transient failure and on a 200 that carries no
    text, matching groq_client.chat()'s contract -- surfacing an empty
    completion as an error so the caller falls back deliberately instead of
    recording a blank string as a real classification.
    """
    model = model or config.GEMINI_MODEL
    url = f"{config.GEMINI_BASE_URL.rstrip('/')}/models/{model}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            # Headroom for thinking tokens -- see the module docstring.
            "maxOutputTokens": max_output_tokens,
        },
    }

    last: httpx.Response | None = None
    for attempt in range(MAX_ATTEMPTS):
        resp = httpx.post(url, json=body, headers=_headers(), timeout=TIMEOUT_SECONDS)
        if resp.status_code < 400:
            data = resp.json()
            text = _text(data)
            if not text:
                usage = data.get("usageMetadata", {})
                raise GeminiError(502, {
                    "error": "model returned no text",
                    "finish_reason": (data.get("candidates") or [{}])[0].get("finishReason"),
                    # Surfaced because it is usually the actual cause: the
                    # budget went entirely on reasoning.
                    "thoughts_tokens": usage.get("thoughtsTokenCount"),
                })
            return text

        last = resp
        if not _is_transient(resp) or attempt == MAX_ATTEMPTS - 1:
            break
        suggested = _retry_after(resp)
        # Cap it: a daily quota reports tens of seconds and there is no point
        # holding a batch open for that -- the caller gets a clean failure and
        # falls back to the rules instead.
        wait = min(suggested, 10.0) if suggested is not None else BACKOFF_SECONDS * (attempt + 1)
        time.sleep(wait)

    assert last is not None
    raise GeminiError(last.status_code, error_payload(last))
