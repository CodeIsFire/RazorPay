"""Action router: exception cause -> action.

One action type per cause:

  failed_payment  -> retry_payout        the only action that calls RazorpayX
  fee_mismatch    -> draft_dispute_note  a real money discrepancy; needs a human
                                          (small, fee-schedule-explained deltas
                                          never reach here -- classify.py
                                          auto-resolves those before routing)
  timing_lag      -> send_reminder       amount already matches, just landed
                                          late -- goes 'pending'; waits on
                                          recheck_exception(), not retried
  duplicate       -> flag_for_review     money already moved twice; too risky to auto-remediate
  unexplained     -> flag_for_review     unknown cause -- don't guess, flag
  refund_unmatched-> flag_for_review     a recognized refund, but still needs a human to confirm
                                          it against the ledger before it's called reconciled
  chargeback      -> draft_dispute_note  real money at risk with a bank-side response deadline;
                                          same reasoning as fee_mismatch, needs a human
  partial_payment -> send_reminder       real correlated evidence exists, just not all of it yet --
                                          same category as timing_lag, wait rather than retry

Only retry_payout is bounded by MAX_RETRY_COUNT/MAX_EXCEPTION_AGE_DAYS and
eligible for repeated dispatch -- it's the one action type that's a real API
call and can genuinely behave differently attempt to attempt. Every other
action type dispatches exactly once:

  - draft_dispute_note / flag_for_review: redispatching the identical note
    or flag produces nothing new -- the underlying situation only changes
    when a human acts on it (see resolve_exception). So after one dispatch
    the exception stays 'open' but is excluded from future
    route_open_exceptions() passes (retry_count >= 1 short-circuits it) --
    never retried, never abandoned by a bound that was never really about
    retrying in the first place.
  - send_reminder (timing_lag, partial_payment): moves to 'pending' after
    one dispatch instead, since neither resolves by redispatching either --
    timing_lag resolves via recheck_exception() reaffirming evidence that
    was already complete at classification time; partial_payment resolves
    via classify.py's independent graduation check once its group's pieces
    fully sum up. 'pending' keeps both out of route_open_exceptions'
    selection entirely, which matters more for partial_payment than it
    looks: that graduation check explicitly skips 'abandoned' rows, so a
    partial_payment abandoned by the retry bound could never auto-resolve
    even after its missing piece showed up.

Net effect: 'abandoned' only ever means one thing now -- a real payout was
retried up to the bound and never confirmed. Every other cause's terminal
state is a human decision (resolve_exception) or the classifier's own
graduation check, not the router giving up.

Payout execution goes through a swappable PayoutExecutor so the mock used
here and the real RazorpayX-backed one (app/live_executor.py) present the
exact same interface. A dispatch never claims success by itself -- only
confirm_action() (called by a human/test, or the real webhook handler) may
set a terminal outcome. If executor.create_payout() raises (network error,
a ledger row with no payout instruction, RazorpayX rejecting the request),
that's
reported as its own 'error' decision, distinct from a bound hit -- see the
try/except below.
"""
from __future__ import annotations

import sqlite3
from typing import Protocol

from app import config
from app.config import MAX_EXCEPTION_AGE_DAYS, MAX_RETRY_COUNT
from app.db import fetch_payout_instruction, log_audit

ACTION_MAP = {
    "failed_payment": "retry_payout",
    "fee_mismatch": "draft_dispute_note",
    "timing_lag": "send_reminder",
    "duplicate": "flag_for_review",
    "unexplained": "flag_for_review",
    "refund_unmatched": "flag_for_review",
    "chargeback": "draft_dispute_note",
    "partial_payment": "send_reminder",
}


class PayoutExecutor(Protocol):
    def create_payout(self, *, idempotency_key: str, amount_paise: int,
                       counterparty: str, purpose: str,
                       payout_instruction: dict | None = None) -> dict: ...


class MockPayoutExecutor:
    """Stands in for the real RazorpayX Payouts API (wired in at M6).
    Deterministic and offline -- this sandbox can't reach RazorpayX's API
    anyway (see M6 notes), so the router's logic has to be provably correct
    without it. Always returns 'processing': the router must never treat a
    successful API call as a successful payout.
    """

    def create_payout(self, *, idempotency_key: str, amount_paise: int,
                       counterparty: str, purpose: str,
                       payout_instruction: dict | None = None) -> dict:
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
    if status == "pending":
        # timing_lag/partial_payment: resolved by independent evidence
        # (recheck_exception or classify.py's graduation check), not by
        # anything this router could dispatch again.
        return {"decision": "skipped", "reason": "pending -- waiting on independent evidence"}

    cause = exception_row["cause"]
    action_type = ACTION_MAP[cause]
    retry_count = exception_row["retry_count"]
    age_days = exception_row["age_days"]

    if action_type == "retry_payout":
        if retry_count >= MAX_RETRY_COUNT:
            _abandon(conn, key, f"max retry count reached ({retry_count}/{MAX_RETRY_COUNT})")
            return {"decision": "abandoned", "reason": "max_retries"}
        if age_days > MAX_EXCEPTION_AGE_DAYS:
            _abandon(conn, key, f"exception is {age_days:.1f}d old, exceeds the {MAX_EXCEPTION_AGE_DAYS}d limit")
            return {"decision": "abandoned", "reason": "max_age"}

        # Never dispatch a second payout while the first is still in flight.
        #
        # A dispatched retry_payout leaves the exception 'open' on purpose --
        # dispatch is not success, and only confirm_action() (the webhook) may
        # call it either way. But 'open' is also exactly what
        # route_open_exceptions() selects, so without this guard a second
        # /pipeline/route call would retry an attempt that hasn't failed --
        # and hasn't succeeded either, it simply hasn't been confirmed yet.
        # That is a duplicate disbursement to a real payee, plus a duplicate
        # payout.processed webhook for the same underlying exception.
        #
        # Retry is only ever legitimate after RazorpayX confirms a failure,
        # which confirm_action('reversed') already models by reopening the
        # exception -- by which point the attempt below is no longer in
        # 'queued'/'processing' and this guard lets it through.
        in_flight = conn.execute(
            """SELECT attempt_number, gateway_payout_id, status FROM actions
               WHERE exception_key = ? AND action_type = 'retry_payout'
                 AND status IN ('queued', 'processing')
               ORDER BY attempt_number DESC LIMIT 1""",
            (key,),
        ).fetchone()
        if in_flight is not None:
            reason = (f"attempt {in_flight['attempt_number']} is still "
                      f"{in_flight['status']} ({in_flight['gateway_payout_id']}) -- "
                      f"waiting on a payout webhook before retrying")
            log_audit(conn, actor="router", subject_type="exception", subject_id=key,
                      event="action_skipped_in_flight", detail=reason)
            conn.commit()
            return {"decision": "skipped", "reason": reason}
    elif retry_count >= 1:
        # One-shot action (flag_for_review/draft_dispute_note) already
        # dispatched once -- redispatching the identical note/flag says
        # nothing new. Not a bound hit (nothing failed), just nothing left
        # for automation to do; a human closes it out via resolve_exception.
        return {"decision": "skipped", "reason": "already flagged for human review"}

    attempt_number = retry_count + 1
    idempotency_key = f"{key}:attempt{attempt_number}"

    if action_type == "retry_payout":
        try:
            # Where the money goes comes from the ledger row itself -- the
            # Tally payout columns on `transactions` -- so a live payout
            # needs no out-of-band payee provisioning. None here is not
            # fatal on its own: the mock ignores it, and the live executor
            # raises a specific error that the except below turns into one
            # logged, skipped exception rather than a failed batch.
            result = executor.create_payout(
                idempotency_key=idempotency_key,
                amount_paise=exception_row["amount_paise"],
                counterparty=exception_row["ledger_ref"] or "unknown",
                purpose="failed_payment_retry",
                payout_instruction=fetch_payout_instruction(
                    conn, exception_row["ledger_ref"] or ""
                ),
            )
        except Exception as e:
            # A live executor can fail for reasons that have nothing to do
            # with the exception itself (network, missing fund-account
            # config, RazorpayX rejecting the request) -- one bad exception
            # shouldn't crash the whole batch, and this attempt was never
            # actually dispatched, so no actions row and no retry_count
            # increment. Distinct from a bound hit: this is "couldn't try",
            # not "chose not to".
            log_audit(conn, actor="router", subject_type="exception", subject_id=key,
                      event="action_dispatch_failed",
                      detail=f"action={action_type} attempt={attempt_number} error={e}")
            conn.commit()
            return {"decision": "error", "detail": str(e)}
        action_status = result["status"]
        gateway_payout_id = result.get("gateway_payout_id")
    else:
        # Non-payout actions have nothing to wait on -- they complete the
        # moment they're dispatched, but that only means "the note/reminder
        # was sent", not "the underlying exception is resolved".
        action_status = "completed"
        gateway_payout_id = None

    cur = conn.execute(
        """INSERT INTO actions
               (exception_key, attempt_number, action_type, idempotency_key,
                status, gateway_payout_id, detail)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (key, attempt_number, action_type, idempotency_key, action_status,
         gateway_payout_id, f"attempt {attempt_number} of {MAX_RETRY_COUNT}"),
    )
    # timing_lag/partial_payment are genuinely time-dependent, not stuck:
    # the evidence either already matches (just landed late) or will
    # complete on its own as more pieces arrive. 'pending' says so
    # explicitly, and (by design) isn't selected by route_open_exceptions'
    # WHERE clause below, so neither gets re-nagged with another dispatch
    # or hits the retry/age bound. Every other cause stays 'open' -- either
    # eligible for another retry_payout attempt, or (for the one-shot
    # actions) simply done with what automation can contribute, waiting on
    # a human via resolve_exception.
    next_status = "pending" if cause in ("timing_lag", "partial_payment") else "open"
    conn.execute(
        "UPDATE exceptions SET retry_count=?, status=?, updated_at=datetime('now') "
        "WHERE exception_key=?",
        (attempt_number, next_status, key),
    )
    log_audit(conn, actor="router", subject_type="exception", subject_id=key,
              event="action_dispatched",
              detail=f"action={action_type} attempt={attempt_number} idempotency_key={idempotency_key}")

    # A live executor's create_payout can return an already-terminal status
    # instead of "processing" -- RazorpayX replays an idempotent request
    # (same idempotency_key sent before) by returning the payout's current
    # state rather than erroring, which can be 'processed'/'reversed' if
    # that payout was already resolved since the original attempt. Without
    # this, the exception would sit at 'open' forever: nothing else
    # ever calls confirm_action() for it, since the webhook already fired
    # (once, back when this payout was genuinely dispatched) and won't
    # fire again for the same payout id.
    if action_type == "retry_payout" and action_status in ("processed", "reversed"):
        confirm_action(conn, cur.lastrowid, action_status)

    return {"decision": "dispatched", "action_type": action_type, "idempotency_key": idempotency_key}


def route_open_exceptions(conn: sqlite3.Connection, executor: PayoutExecutor | None = None) -> dict:
    """Runs route_exception over every exception still eligible for action.
    Safe to call repeatedly -- already-resolved/abandoned/pending rows are
    skipped, bound-hit retry_payout rows get abandoned exactly once, and a
    one-shot cause that already got its single flag dispatched is skipped
    rather than redispatched (see route_exception)."""
    executor = executor or MockPayoutExecutor()
    rows = conn.execute(
        """SELECT *, (julianday('now') - julianday(created_at)) AS age_days
           FROM exceptions WHERE status = 'open'"""
    ).fetchall()

    summary = {"dispatched": 0, "abandoned": 0, "skipped": 0, "error": 0}
    for row in rows:
        result = route_exception(conn, dict(row), executor)
        summary[result["decision"]] = summary.get(result["decision"], 0) + 1
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


def resolve_exception(conn: sqlite3.Connection, exception_key: str, note: str = "") -> None:
    """The human-in-the-loop close-out for the three action types that
    route_exception never resolves on its own (draft_dispute_note,
    send_reminder, flag_for_review -- see route_exception's docstring).
    Those actions surface an exception and stop pestering once bounds are
    hit, but nothing before this marked the underlying situation actually
    handled. This is that: a human confirms the fee dispute / duplicate /
    unexplained transaction was dealt with outside the system, and this
    records that decision as a terminal state -- same 'resolved' status a
    successful payout reaches, so it stops showing as open regardless of
    which cause it started as."""
    exc = conn.execute("SELECT * FROM exceptions WHERE exception_key=?", (exception_key,)).fetchone()
    if exc is None:
        raise ValueError(f"no such exception: {exception_key}")
    if exc["status"] == "resolved":
        return  # idempotent: already resolved, nothing to do

    conn.execute(
        "UPDATE exceptions SET status='resolved', updated_at=datetime('now') WHERE exception_key=?",
        (exception_key,),
    )
    log_audit(conn, actor="human", subject_type="exception", subject_id=exception_key,
              event="exception_manually_resolved", detail=note or "resolved outside the system")
    conn.commit()


def recheck_exception(conn: sqlite3.Connection, exception_key: str) -> None:
    """Closes out a 'pending' timing_lag exception. Unlike resolve_exception,
    this isn't an override; the evidence that justifies resolving it was
    already established at classification time (the gateway transaction
    exists and its amount matches the ledger row -- only the timing was
    off), so a recheck is just reaffirming that nothing has changed, not a
    judgment call. Callable by a human from the dashboard now; the natural
    next step is a scheduler calling this automatically after some grace
    period.

    partial_payment also reaches 'pending' (see route_exception) but is
    deliberately rejected here: closing one out for real IS a judgment call
    (writing off a shortfall, or accepting an incomplete group as settled),
    and this function's canned 'reaffirmed present, late but correct'
    message would misrepresent that decision. A human closes those out via
    resolve_exception with a note instead."""
    exc = conn.execute("SELECT * FROM exceptions WHERE exception_key=?", (exception_key,)).fetchone()
    if exc is None:
        raise ValueError(f"no such exception: {exception_key}")
    if exc["cause"] != "timing_lag":
        raise ValueError(
            f"recheck only applies to timing_lag exceptions, got cause={exc['cause']!r} "
            f"for {exception_key} -- use resolve_exception instead"
        )
    if exc["status"] != "pending":
        raise ValueError(f"exception {exception_key} is {exc['status']!r}, not 'pending' -- nothing to recheck")

    conn.execute(
        "UPDATE exceptions SET status='resolved', updated_at=datetime('now') WHERE exception_key=?",
        (exception_key,),
    )
    log_audit(conn, actor="router", subject_type="exception", subject_id=exception_key,
              event="timing_lag_recheck_resolved",
              detail="Gateway transaction reaffirmed present with matching amount -- late but correct.")
    conn.commit()


# RazorpayX payout lifecycle: queued -> processing -> processed / reversed /
# failed / cancelled / rejected. The first two mean "still in flight"; the
# rest are terminal.
IN_FLIGHT_PAYOUT_STATUSES = ("queued", "processing")

# confirm_action() only speaks 'processed' and 'reversed', because those are
# the only two outcomes a webhook ever delivers. Every other terminal state
# means the same thing operationally -- the money did not reach the payee and
# the exception should reopen for another attempt -- so they map onto
# 'reversed' rather than growing a parallel vocabulary.
TERMINAL_PAYOUT_OUTCOME = {
    "processed": "processed",
    "reversed": "reversed",
    "failed": "reversed",
    "cancelled": "reversed",
    "rejected": "reversed",
}


class PayoutStatusFetcher(Protocol):
    def fetch_payout_status(self, gateway_payout_id: str) -> str: ...


def sync_payout_statuses(conn: sqlite3.Connection, fetcher: PayoutStatusFetcher) -> dict:
    """Ask RazorpayX what actually happened to every payout we still think is
    in flight, and apply any terminal answer.

    Webhooks are the primary path and stay authoritative -- this changes
    nothing a webhook already recorded, because only in-flight actions are
    even looked at. It exists because webhook delivery is not guaranteed: a
    tunnel that was down, a restart mid-delivery, or a payout advanced while
    nothing was listening all leave an action stuck at 'processing' forever,
    and nothing else in the app would ever revisit it.

    Deliberately does NOT discover payouts we have no action row for. A payout
    created by an earlier run (or from the RazorpayX dashboard directly) has
    no exception to resolve here, and inventing one would fabricate
    reconciliation state -- handle_webhook logs those as 'webhook_unmatched'
    for the same reason.
    """
    # In-flight payouts, PLUS recently-settled ones -- because 'processed' is
    # not actually the end of the story. A beneficiary bank can return funds
    # days after settlement, and RazorpayX moves the payout to 'reversed'. The
    # original version of this query watched only the in-flight statuses, so
    # once an action reached 'processed' it was never re-read: four real
    # reversals sat undetected while the dashboard kept counting them as
    # recovered money.
    #
    # Bounded by a window rather than watching every settled payout forever,
    # since the sweep costs one API call per row on every pass and would
    # otherwise grow without limit as payouts settle. Reversals arrive within
    # the settlement window; anything older is genuinely finished.
    #
    # 'reversed' is deliberately NOT re-read. It is terminal in the direction
    # that matters -- the money came back, and a fresh attempt would be a new
    # payout with a new id -- so polling it could only ever flap a settled
    # failure back to success.
    watched = IN_FLIGHT_PAYOUT_STATUSES + ("processed",)
    placeholders = ",".join("?" for _ in watched)
    rows = conn.execute(
        f"""SELECT id, gateway_payout_id, exception_key, status FROM actions
            WHERE action_type='retry_payout'
              AND gateway_payout_id IS NOT NULL
              AND status IN ({placeholders})
              AND (status != 'processed'
                   OR updated_at >= datetime('now', ?))""",
        (*watched, f"-{config.SYNC_REVERSAL_WINDOW_DAYS} days"),
    ).fetchall()

    summary = {"checked": len(rows), "confirmed": 0, "still_in_flight": 0, "error": 0}
    for row in rows:
        try:
            remote_status = fetcher.fetch_payout_status(row["gateway_payout_id"])
        except Exception as e:
            # One unreachable payout must not abandon the rest of the sweep.
            log_audit(conn, actor="router", subject_type="action", subject_id=str(row["id"]),
                      event="payout_sync_failed",
                      detail=f"payout_id={row['gateway_payout_id']} error={e}")
            summary["error"] += 1
            continue

        outcome = TERMINAL_PAYOUT_OUTCOME.get(remote_status)
        if outcome is None:
            summary["still_in_flight"] += 1
            continue

        # Nothing changed since we last looked. Now that settled payouts are
        # re-read for late reversals, most rows in a sweep are in exactly the
        # state we already recorded -- without this guard each one would be
        # re-confirmed and re-audited every pass, writing a fresh audit row
        # every 5 seconds per settled payout.
        if outcome == row["status"]:
            continue

        log_audit(conn, actor="router", subject_type="exception",
                  subject_id=row["exception_key"], event="payout_synced_from_api",
                  detail=f"payout_id={row['gateway_payout_id']} razorpayx_status={remote_status} "
                         f"-> {outcome} (was {row['status']}; no webhook had been received)")
        confirm_action(conn, row["id"], outcome)
        summary["confirmed"] += 1

    conn.commit()
    return summary
