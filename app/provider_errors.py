"""Shared error-body handling for the LLM provider clients.

The three provider clients speak genuinely different protocols -- Groq is
OpenAI-shaped, Anthropic uses /v1/messages, Gemini uses :generateContent -- and
their request building, auth headers and response parsing are rightly separate.
But "a non-2xx arrived, get something loggable out of its body" does not depend
on the wire format at all, and writing it per client is how the copies drift.

They already had: two of them truncated the raw-text fallback and two did not,
and only one special-cased an empty body. That divergence was accidental, and
it is exactly the kind that makes an outage read differently depending on which
provider was configured. One implementation, one behaviour.

Kept deliberately small. It does not own the exception type, the status check,
or retry policy -- those differ per provider and belong with the client that
knows them.
"""

from __future__ import annotations

import httpx

#: Enough of an unparseable body to identify the failure in a log without
#: pasting an entire HTML error page into an audit trail.
RAW_BODY_CHARS = 400


def error_payload(resp: httpx.Response) -> dict:
    """A JSON-ish dict describing a failed response, whatever it actually sent.

    Providers return JSON errors on a good day and an HTML gateway page or
    nothing at all on a bad one. The empty case is called out by name rather
    than left as an empty string: an empty body is itself diagnostic -- it is
    how at least one of these APIs signals throttling, as opposed to the JSON
    error it sends for a genuine not-found.
    """
    try:
        data = resp.json()
        # A provider may answer with a bare array or string. Callers index this
        # like a dict, so normalise rather than hand back something that
        # AttributeErrors two frames later.
        return data if isinstance(data, dict) else {"raw": data}
    except ValueError:
        text = resp.text[:RAW_BODY_CHARS]
        return {"raw": text or "(empty body)"}
