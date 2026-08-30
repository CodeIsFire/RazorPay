"""RazorpayX webhook receiver logic. Verifies the signature, extracts the
payout outcome, and calls router.confirm_action() -- the exact function
M5's manual /actions/{id}/confirm already exercises. This is the one place
payout.processed / payout.reversed become real, webhook-confirmed outcomes
instead of a human calling confirm_action by hand.

Signature scheme, per RazorpayX's docs: HMAC-SHA256 of the RAW request
body (not the parsed/re-serialized JSON -- that can reorder keys and
silently break verification), hex digest, compared to the
X-Razorpay-Signature header. Implemented by hand with stdlib hmac/hashlib
rather than pulling in the razorpay package for one function -- see
app/razorpayx_client.py's docstring for why this app doesn't depend on
that SDK.

Test mode only ever fires five events (payout.queued, payout.initiated,
payout.processed, payout.reversed, transaction.created) and, critically,
does NOT auto-advance a payout through its lifecycle -- someone has to
nudge it forward from the RazorpayX dashboard for this endpoint to ever
receive a payout.processed/reversed webhook at all. That's a demo-time
fact, not a bug here.
"""
import hashlib
import hmac
import sqlite3

from app.db import log_audit
from app.router import confirm_action


class WebhookVerificationError(Exception):
    pass


def verify_signature(raw_body: bytes, signature: str, secret: str) -> None:
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        raise WebhookVerificationError("signature mismatch")


# Only these two events ever change any state. Everything else RazorpayX
# might send (payout.queued, payout.initiated, transaction.created, ...)
# is logged for visibility and otherwise ignored -- there's nothing for
# this app to *do* with "your payout started processing".
EVENT_TO_OUTCOME = {
    "payout.processed": "processed",
    "payout.reversed": "reversed",
}


def _entity_ref(payload: dict) -> str:
    """The one id worth surfacing from a webhook payload -- the payout or
    transaction entity it is about. Falls back to whatever id is at the top
    level, then to an empty string. The full payload is not stored in the
    audit detail (it is a Python repr blob that renders as noise); it stays
    available in RazorpayX's own webhook logs."""
    body = payload.get("payload") or {}
    for key in ("payout", "transaction", "settlement", "refund"):
        entity = (body.get(key) or {}).get("entity") or {}
        if entity.get("id"):
            return entity["id"]
    return payload.get("id", "")


def handle_webhook(conn: sqlite3.Connection, payload: dict) -> dict:
    event = payload.get("event", "")
    ref = _entity_ref(payload)
    log_audit(conn, actor="webhook", subject_type="webhook_event", subject_id=event or "unknown",
              event="webhook_received", detail=f"{event} · {ref}" if ref else event)
    conn.commit()

    outcome = EVENT_TO_OUTCOME.get(event)
    if outcome is None:
        return {"handled": False, "event": event}

    try:
        payout_entity = payload["payload"]["payout"]["entity"]
        gateway_payout_id = payout_entity["id"]
    except (KeyError, TypeError) as e:
        raise ValueError(f"malformed webhook payload for event={event}: missing {e}")

    action = conn.execute(
        "SELECT * FROM actions WHERE gateway_payout_id=?", (gateway_payout_id,)
    ).fetchone()
    if action is None:
        # A webhook for a payout we have no record of dispatching -- log it
        # loudly rather than silently dropping it; could mean a payout was
        # created outside this app (dashboard, another process).
        log_audit(conn, actor="webhook", subject_type="webhook_event", subject_id=event,
                  event="webhook_unmatched",
                  detail=f"no action found for gateway_payout_id={gateway_payout_id}")
        conn.commit()
        return {"handled": False, "event": event, "reason": "no matching action"}

    confirm_action(conn, action["id"], outcome)
    return {"handled": True, "event": event, "action_id": action["id"], "outcome": outcome}
