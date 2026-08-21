"""Action router: exception cause -> action, bounded by retry count and age
so it can never loop forever on a stuck exception.

One action type per cause:

  failed_payment  -> retry_payout        the only action that calls RazorpayX
  fee_mismatch    -> draft_dispute_note  a real money discrepancy; needs a human
  timing_lag      -> send_reminder       probably fine, just slow -- nudge, don't retry
  duplicate       -> flag_for_review     money already moved twice; too risky to auto-remediate
  unexplained     -> flag_for_review     unknown cause -- don't guess, flag

Only retry_payout can ever move an exception to 'resolved', and only once
its action is CONFIRMED processed -- never on dispatch. Every other action
type marks the exception 'in_progress' and stops there. That's a
deliberate scope boundary: this router does not pretend to auto-resolve a
fee dispute or a duplicate payout. A human closes those out; the system's
job is to surface them clearly and stop pestering once the bounds are hit.

Payout execution goes through a swappable PayoutExecutor so the mock used
here and the real razorpay-python calls wired in at M6 present the exact
same interface: create_payout() always returns "processing", never
"processed" -- matching real RazorpayX behavior, where a successful API
call only means the payout was accepted, not that money moved. Only
confirm_action() (called by a human/test now, by the webhook handler at
M6) may set a terminal outcome.
"""
import sqlite3
from typing import Protocol

from app.config import MAX_EXCEPTION_AGE_DAYS, MAX_RETRY_COUNT
from app.db import log_audit

ACTION_MAP = {
    "failed_payment": "retry_payout",
    "fee_mismatch": "draft_dispute_note",
    "timing_lag": "send_reminder",
    "duplicate": "flag_for_review",
    "unexplained": "flag_for_review",
}


class PayoutExecutor(Protocol):
    def create_payout(self, *, idempotency_key: str, amount_paise: int,
                       counterparty: str, purpose: str) -> dict: ...


class MockPayoutExecutor:
    """Stands in for the real RazorpayX Payouts API (wired in at M6).
    Deterministic and offline -- this sandbox can't reach RazorpayX's API
    anyway (see M6 notes), so the router's logic has to be provably correct
    without it. Always returns 'processing': the router must never treat a
    successful API call as a successful payout.
    """

    def create_payout(self, *, idempotency_key: str, amount_paise: int,
                       counterparty: str, purpose: str) -> dict:
        return {"gateway_payout_id": f"mock_pout_{idempotency_key}", "status": "processing"}


def _abandon(conn: sqlite3.Connection, exception_key: str, reason: str) -> None:
    conn.execute(
        "UPDATE exceptions SET status='abandoned', updated_at=datetime('now') WHERE exception_key=?",
        (exception_key,),
    )
    log_audit(conn, actor="router", subject_type="exception", subject_id=exception_key,
              event="action_skipped_bound_hit", detail=reason)


def route_exception(conn: sqlite3.Connection, exception_row: dict, executor: PayoutExecutor) -> dict:
    """Decides and takes (at most) one action for one exception. Every
    branch -- dispatch, or give up and say why -- writes to audit_log."""
    key = exception_row["exception_key"]
    status = exception_row["status"]
    if status in ("resolved", "abandoned"):
        return {"decision": "skipped", "reason": f"already {status}"}

    retry_count = exception_row["retry_count"]
    age_days = exception_row["age_days"]

    if retry_count >= MAX_RETRY_COUNT:
        _abandon(conn, key, f"max retry count reached ({retry_count}/{MAX_RETRY_COUNT})")
        return {"decision": "abandoned", "reason": "max_retries"}

    if age_days > MAX_EXCEPTION_AGE_DAYS:
        _abandon(conn, key, f"exception is {age_days:.1f}d old, exceeds the {MAX_EXCEPTION_AGE_DAYS}d limit")
        return {"decision": "abandoned", "reason": "max_age"}

    cause = exception_row["cause"]
    action_type = ACTION_MAP[cause]
    attempt_number = retry_count + 1
    idempotency_key = f"{key}:attempt{attempt_number}"

    if action_type == "retry_payout":
        result = executor.create_payout(
            idempotency_key=idempotency_key,
            amount_paise=exception_row["amount_paise"],
            counterparty=exception_row["ledger_ref"] or "unknown",
            purpose="failed_payment_retry",
        )
        action_status = result["status"]
        gateway_payout_id = result.get("gateway_payout_id")
    else:
        # Non-payout actions have nothing to wait on -- they complete the
        # moment they're dispatched, but that only means "the note/reminder
        # was sent", not "the underlying exception is resolved".
        action_status = "completed"
        gateway_payout_id = None

    conn.execute(
        """INSERT INTO actions
               (exception_key, attempt_number, action_type, idempotency_key,
                status, gateway_payout_id, detail)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (key, attempt_number, action_type, idempotency_key, action_status,
         gateway_payout_id, f"attempt {attempt_number} of {MAX_RETRY_COUNT}"),
    )
    conn.execute(
        "UPDATE exceptions SET retry_count=?, status='in_progress', updated_at=datetime('now') "
        "WHERE exception_key=?",
        (attempt_number, key),
    )
    log_audit(conn, actor="router", subject_type="exception", subject_id=key,
              event="action_dispatched",
              detail=f"action={action_type} attempt={attempt_number} idempotency_key={idempotency_key}")

    return {"decision": "dispatched", "action_type": action_type, "idempotency_key": idempotency_key}


def route_open_exceptions(conn: sqlite3.Connection, executor: PayoutExecutor | None = None) -> dict:
    """Runs route_exception over every exception still eligible for action.
    Safe to call repeatedly -- already-resolved/abandoned rows are skipped,
    and bound-hit rows get abandoned exactly once (the second call finds
    them already 'abandoned' and skips)."""
    executor = executor or MockPayoutExecutor()
    rows = conn.execute(
        """SELECT *, (julianday('now') - julianday(created_at)) AS age_days
           FROM exceptions WHERE status IN ('open', 'in_progress')"""
    ).fetchall()

    summary = {"dispatched": 0, "abandoned": 0, "skipped": 0}
    for row in rows:
        result = route_exception(conn, dict(row), executor)
        summary[result["decision"]] += 1
    conn.commit()
    return summary


def confirm_action(conn: sqlite3.Connection, action_id: int, outcome: str) -> None:
    """outcome: 'processed' or 'reversed'. This is what a webhook handler
    (mocked here via a direct call, real at M6 via payout.processed /
    payout.reversed) calls once RazorpayX confirms what actually happened.
    The router never assumes success from dispatch alone -- this is the
    only place a payout action reaches a terminal state."""
    if outcome not in ("processed", "reversed"):
        raise ValueError(f"invalid outcome: {outcome!r}")

    action = conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
    if action is None:
        raise ValueError(f"no such action: {action_id}")

    conn.execute("UPDATE actions SET status=?, updated_at=datetime('now') WHERE id=?",
                 (outcome, action_id))

    if action["action_type"] == "retry_payout":
        if outcome == "processed":
            conn.execute(
                "UPDATE exceptions SET status='resolved', updated_at=datetime('now') WHERE exception_key=?",
                (action["exception_key"],),
            )
            event = "payout_confirmed_processed"
        else:
            # Attempt failed. retry_count already reflects this attempt;
            # reopen so the next route_open_exceptions() pass can try
            # again, up to MAX_RETRY_COUNT.
            conn.execute(
                "UPDATE exceptions SET status='open', updated_at=datetime('now') WHERE exception_key=?",
                (action["exception_key"],),
            )
            event = "payout_confirmed_reversed"
        log_audit(conn, actor="router", subject_type="exception", subject_id=action["exception_key"],
                  event=event,
                  detail=f"action_id={action_id} idempotency_key={action['idempotency_key']}")

    conn.commit()
