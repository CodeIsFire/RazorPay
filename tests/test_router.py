import sqlite3
from pathlib import Path

from app.classify import classify_and_persist_from_db
from app.config import MAX_EXCEPTION_AGE_DAYS, MAX_RETRY_COUNT
from app.fixtures import generate_dataset
from app.funnel import compute_funnel
from app.load_fixtures import load_dataset_into_db
from app.router import (
    ACTION_MAP,
    MockPayoutExecutor,
    confirm_action,
    recheck_exception,
    route_exception,
    route_open_exceptions,
)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def _fresh_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())
    return conn


def _insert_exception(conn, *, key, cause, ledger_ref, amount_paise=100_000,
                       retry_count=0, status="open", created_days_ago=0):
    conn.execute(
        """INSERT INTO exceptions (exception_key, cause, ledger_ref, gateway_ref,
                                    amount_paise, detail, status, retry_count, created_at)
           VALUES (?, ?, ?, NULL, ?, 'test fixture', ?, ?,
                   datetime('now', ?))""",
        (key, cause, ledger_ref, amount_paise, status, retry_count,
         f"-{created_days_ago} days"),
    )
    conn.commit()


def _fetch_row_with_age(conn, key):
    return dict(conn.execute(
        """SELECT *, (julianday('now') - julianday(created_at)) AS age_days
           FROM exceptions WHERE exception_key=?""",
        (key,),
    ).fetchone())


# ---------------------------------------------------------------------------
# Action mapping
# ---------------------------------------------------------------------------

def test_action_map_covers_every_cause_with_a_valid_action_type():
    from app.classify import CAUSES

    valid_actions = {"retry_payout", "draft_dispute_note", "send_reminder", "flag_for_review"}
    assert set(ACTION_MAP.keys()) == set(CAUSES)
    assert set(ACTION_MAP.values()) <= valid_actions


# ---------------------------------------------------------------------------
# Dispatch behavior
# ---------------------------------------------------------------------------

def test_failed_payment_dispatches_retry_payout_and_stays_processing():
    conn = _fresh_db()
    _insert_exception(conn, key="failed_payment:LED-1:-", cause="failed_payment", ledger_ref="LED-1")
    row = _fetch_row_with_age(conn, "failed_payment:LED-1:-")

    result = route_exception(conn, row, MockPayoutExecutor())
    assert result["decision"] == "dispatched"
    assert result["action_type"] == "retry_payout"

    action = conn.execute("SELECT * FROM actions WHERE exception_key=?",
                           ("failed_payment:LED-1:-",)).fetchone()
    assert action["action_type"] == "retry_payout"
    assert action["status"] == "processing"  # never assumed 'processed' on dispatch
    assert action["attempt_number"] == 1
    assert action["idempotency_key"] == "failed_payment:LED-1:-:attempt1"

    exc = conn.execute("SELECT * FROM exceptions WHERE exception_key=?",
                        ("failed_payment:LED-1:-",)).fetchone()
    assert exc["status"] == "open"
    assert exc["retry_count"] == 1


def test_fee_mismatch_dispatches_dispute_note_and_completes_immediately_but_stays_open():
    conn = _fresh_db()
    _insert_exception(conn, key="fee_mismatch:LED-2:pout_x", cause="fee_mismatch", ledger_ref="LED-2")
    row = _fetch_row_with_age(conn, "fee_mismatch:LED-2:pout_x")

    result = route_exception(conn, row, MockPayoutExecutor())
    assert result["action_type"] == "draft_dispute_note"

    action = conn.execute("SELECT * FROM actions WHERE exception_key=?",
                           ("fee_mismatch:LED-2:pout_x",)).fetchone()
    assert action["status"] == "completed"

    exc = conn.execute("SELECT * FROM exceptions WHERE exception_key=?",
                        ("fee_mismatch:LED-2:pout_x",)).fetchone()
    # Sending the note doesn't resolve the underlying discrepancy by itself.
    assert exc["status"] == "open"


def test_duplicate_and_unexplained_are_flagged_not_acted_on():
    conn = _fresh_db()
    _insert_exception(conn, key="duplicate:LED-3:pout_y", cause="duplicate", ledger_ref="LED-3")
    row = _fetch_row_with_age(conn, "duplicate:LED-3:pout_y")
    result = route_exception(conn, row, MockPayoutExecutor())
    assert result["action_type"] == "flag_for_review"


# ---------------------------------------------------------------------------
# One-shot causes: flag_for_review/draft_dispute_note say nothing new on a
# second dispatch, so they're never redispatched -- only retry_payout
# (a real API call that can genuinely behave differently attempt to attempt)
# is bounded/repeatable. timing_lag and partial_payment are their own
# category: they move to 'pending' after one dispatch instead, since their
# resolution comes from independent evidence, not from redispatching.
# ---------------------------------------------------------------------------

def test_one_shot_action_is_not_redispatched_on_a_second_pass():
    conn = _fresh_db()
    key = "duplicate:LED-10:pout_z"
    _insert_exception(conn, key=key, cause="duplicate", ledger_ref="LED-10")

    row = _fetch_row_with_age(conn, key)
    first = route_exception(conn, row, MockPayoutExecutor())
    assert first["decision"] == "dispatched"

    row2 = _fetch_row_with_age(conn, key)
    second = route_exception(conn, row2, MockPayoutExecutor())
    assert second["decision"] == "skipped"
    assert second["reason"] == "already flagged for human review"

    exc = conn.execute("SELECT * FROM exceptions WHERE exception_key=?", (key,)).fetchone()
    assert exc["status"] == "open"
    assert exc["retry_count"] == 1  # never incremented by the skip

    action_count = conn.execute(
        "SELECT COUNT(*) FROM actions WHERE exception_key=?", (key,)
    ).fetchone()[0]
    assert action_count == 1  # no second action row


def test_partial_payment_also_goes_pending_not_open_after_dispatch():
    """Same category as timing_lag: partial_payment's real resolution path
    is the classifier's independent graduation check
    (_resolve_completed_partial_payments), not redispatching send_reminder.
    Landing in 'pending' keeps it out of route_open_exceptions' selection
    entirely, so it can never get abandoned by the retry/age bound -- which
    matters because abandoned rows are excluded from that graduation check."""
    conn = _fresh_db()
    key = "partial_payment:split:LED-11:-"
    _insert_exception(conn, key=key, cause="partial_payment", ledger_ref="LED-11")

    row = _fetch_row_with_age(conn, key)
    result = route_exception(conn, row, MockPayoutExecutor())
    assert result["decision"] == "dispatched"
    assert result["action_type"] == "send_reminder"

    exc = conn.execute("SELECT * FROM exceptions WHERE exception_key=?", (key,)).fetchone()
    assert exc["status"] == "pending"


# ---------------------------------------------------------------------------
# timing_lag: goes 'pending' after one reminder, not 'in_progress' -- it's
# time-dependent, not stuck, so it shouldn't be re-nagged or abandoned by
# the generic retry/age bound. recheck_exception() is its only way out.
# ---------------------------------------------------------------------------

def test_timing_lag_goes_pending_not_in_progress_after_dispatch():
    conn = _fresh_db()
    _insert_exception(conn, key="timing_lag:LED-6:pout_z", cause="timing_lag", ledger_ref="LED-6")
    row = _fetch_row_with_age(conn, "timing_lag:LED-6:pout_z")

    result = route_exception(conn, row, MockPayoutExecutor())
    assert result["decision"] == "dispatched"
    assert result["action_type"] == "send_reminder"

    exc = conn.execute("SELECT * FROM exceptions WHERE exception_key=?",
                        ("timing_lag:LED-6:pout_z",)).fetchone()
    assert exc["status"] == "pending"


def test_pending_timing_lag_is_not_reselected_by_route_open_exceptions():
    conn = _fresh_db()
    _insert_exception(conn, key="timing_lag:LED-7:pout_z", cause="timing_lag", ledger_ref="LED-7")
    route_open_exceptions(conn)  # first pass: dispatches the reminder, goes pending

    summary = route_open_exceptions(conn)  # second pass: nothing left to do
    assert summary == {"dispatched": 0, "abandoned": 0, "skipped": 0, "error": 0}

    exc = conn.execute("SELECT status, retry_count FROM exceptions WHERE exception_key=?",
                        ("timing_lag:LED-7:pout_z",)).fetchone()
    assert exc["status"] == "pending"
    assert exc["retry_count"] == 1  # never got a second reminder dispatched


def test_recheck_resolves_a_pending_timing_lag_exception():
    conn = _fresh_db()
    key = "timing_lag:LED-8:pout_z"
    _insert_exception(conn, key=key, cause="timing_lag", ledger_ref="LED-8")
    route_open_exceptions(conn)  # -> pending

    recheck_exception(conn, key)

    exc = conn.execute("SELECT * FROM exceptions WHERE exception_key=?", (key,)).fetchone()
    assert exc["status"] == "resolved"

    audit = conn.execute(
        "SELECT * FROM audit_log WHERE event='timing_lag_recheck_resolved' AND subject_id=?", (key,)
    ).fetchone()
    assert audit is not None


def test_recheck_rejects_an_exception_that_is_not_pending():
    conn = _fresh_db()
    # timing_lag specifically (not duplicate) -- isolates the status guard
    # from the cause guard tested separately below.
    key = "timing_lag:LED-9:pout_z"
    _insert_exception(conn, key=key, cause="timing_lag", ledger_ref="LED-9")
    # Never routed, so it's still 'open', not 'pending'.

    try:
        recheck_exception(conn, key)
        assert False, "expected a ValueError"
    except ValueError as e:
        assert "not 'pending'" in str(e)


def test_recheck_rejects_a_pending_exception_whose_cause_isnt_timing_lag():
    """partial_payment also lands in 'pending' (see
    test_partial_payment_also_goes_pending_not_open_after_dispatch), but
    closing one out is a real judgment call (writing off a shortfall) --
    unlike timing_lag, where the evidence is already fully explained.
    recheck_exception()'s canned 'reaffirmed present, late but correct'
    message would be actively misleading here, so it's cause-gated."""
    conn = _fresh_db()
    key = "partial_payment:split:LED-12:-"
    _insert_exception(conn, key=key, cause="partial_payment", ledger_ref="LED-12", status="pending")

    try:
        recheck_exception(conn, key)
        assert False, "expected a ValueError"
    except ValueError as e:
        assert "timing_lag" in str(e)


def test_recheck_rejects_an_unknown_exception_key():
    conn = _fresh_db()
    try:
        recheck_exception(conn, "no-such-key")
        assert False, "expected a ValueError"
    except ValueError as e:
        assert "no such exception" in str(e)


# ---------------------------------------------------------------------------
# Executor failures (network error, live executor misconfiguration, ...)
# must not crash the batch or count as a "chose not to" bound hit.
# ---------------------------------------------------------------------------

class _FailingExecutor:
    def create_payout(self, **kwargs):
        raise RuntimeError("simulated network failure")


def test_executor_failure_is_reported_as_error_not_dispatched_or_abandoned():
    conn = _fresh_db()
    key = "failed_payment:LED-8:-"
    _insert_exception(conn, key=key, cause="failed_payment", ledger_ref="LED-8")
    row = _fetch_row_with_age(conn, key)

    result = route_exception(conn, row, _FailingExecutor())
    assert result["decision"] == "error"

    # No attempt was actually made: no actions row, retry_count untouched,
    # exception stays open (not abandoned -- this wasn't a bound hit).
    assert conn.execute("SELECT COUNT(*) FROM actions WHERE exception_key=?",
                         (key,)).fetchone()[0] == 0
    exc = conn.execute("SELECT * FROM exceptions WHERE exception_key=?", (key,)).fetchone()
    assert exc["status"] == "open"
    assert exc["retry_count"] == 0

    audit = conn.execute(
        "SELECT * FROM audit_log WHERE event='action_dispatch_failed' AND subject_id=?", (key,)
    ).fetchone()
    assert audit is not None
    assert "simulated network failure" in audit["detail"]


def test_route_open_exceptions_summary_includes_error_count():
    conn = _fresh_db()
    _insert_exception(conn, key="failed_payment:LED-9:-", cause="failed_payment", ledger_ref="LED-9")
    summary = route_open_exceptions(conn, executor=_FailingExecutor())
    assert summary == {"dispatched": 0, "abandoned": 0, "skipped": 0, "error": 1}


def test_live_executor_raises_clear_error_when_row_has_no_payout_instruction():
    # A ledger row predating the Tally payout columns (or any row whose
    # fund account was never filled in) can't be dispatched. It must fail
    # loudly and name the row, so route_exception can log and skip just
    # that one -- see the 'error' decision, distinct from a bound hit.
    from app.live_executor import RazorpayXPayoutExecutor

    executor = RazorpayXPayoutExecutor()
    try:
        executor.create_payout(idempotency_key="k", amount_paise=1000,
                                counterparty="LED-0018", purpose="failed_payment_retry",
                                payout_instruction=None)
        assert False, "expected a ValueError"
    except ValueError as e:
        assert "LED-0018" in str(e)
        assert "fund_account_type" in str(e)


# ---------------------------------------------------------------------------
# Confirmation (mock stand-in for the M6 webhook)
# ---------------------------------------------------------------------------

def test_confirm_processed_resolves_the_exception():
    conn = _fresh_db()
    _insert_exception(conn, key="failed_payment:LED-4:-", cause="failed_payment", ledger_ref="LED-4")
    row = _fetch_row_with_age(conn, "failed_payment:LED-4:-")
    route_exception(conn, row, MockPayoutExecutor())
    conn.commit()

    action_id = conn.execute("SELECT id FROM actions WHERE exception_key=?",
                              ("failed_payment:LED-4:-",)).fetchone()["id"]
    confirm_action(conn, action_id, "processed")

    exc = conn.execute("SELECT * FROM exceptions WHERE exception_key=?",
                        ("failed_payment:LED-4:-",)).fetchone()
    assert exc["status"] == "resolved"
    action = conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
    assert action["status"] == "processed"

    audit = conn.execute(
        "SELECT * FROM audit_log WHERE event='payout_confirmed_processed'"
    ).fetchall()
    assert len(audit) == 1


def test_confirm_reversed_reopens_for_another_attempt():
    conn = _fresh_db()
    _insert_exception(conn, key="failed_payment:LED-5:-", cause="failed_payment", ledger_ref="LED-5")
    row = _fetch_row_with_age(conn, "failed_payment:LED-5:-")
    route_exception(conn, row, MockPayoutExecutor())
    conn.commit()

    action_id = conn.execute("SELECT id FROM actions WHERE exception_key=?",
                              ("failed_payment:LED-5:-",)).fetchone()["id"]
    confirm_action(conn, action_id, "reversed")

    exc = conn.execute("SELECT * FROM exceptions WHERE exception_key=?",
                        ("failed_payment:LED-5:-",)).fetchone()
    assert exc["status"] == "open"
    assert exc["retry_count"] == 1  # the failed attempt still counts

    # a second pass should dispatch attempt 2
    row2 = _fetch_row_with_age(conn, "failed_payment:LED-5:-")
    result2 = route_exception(conn, row2, MockPayoutExecutor())
    assert result2["decision"] == "dispatched"
    action2 = conn.execute(
        "SELECT * FROM actions WHERE exception_key=? ORDER BY id DESC LIMIT 1",
        ("failed_payment:LED-5:-",),
    ).fetchone()
    assert action2["attempt_number"] == 2


# ---------------------------------------------------------------------------
# Bounds: retry count and age
# ---------------------------------------------------------------------------

def test_router_abandons_after_max_retries_and_logs_why():
    conn = _fresh_db()
    key = "failed_payment:LED-6:-"
    _insert_exception(conn, key=key, cause="failed_payment", ledger_ref="LED-6")

    # Simulate MAX_RETRY_COUNT dispatch+reversed cycles.
    for _ in range(MAX_RETRY_COUNT):
        row = _fetch_row_with_age(conn, key)
        result = route_exception(conn, row, MockPayoutExecutor())
        assert result["decision"] == "dispatched"
        action_id = conn.execute(
            "SELECT id FROM actions WHERE exception_key=? ORDER BY id DESC LIMIT 1", (key,)
        ).fetchone()["id"]
        confirm_action(conn, action_id, "reversed")

    # One more pass should hit the bound instead of dispatching a 4th attempt.
    row = _fetch_row_with_age(conn, key)
    result = route_exception(conn, row, MockPayoutExecutor())
    assert result["decision"] == "abandoned"
    assert result["reason"] == "max_retries"

    exc = conn.execute("SELECT * FROM exceptions WHERE exception_key=?", (key,)).fetchone()
    assert exc["status"] == "abandoned"
    assert exc["retry_count"] == MAX_RETRY_COUNT  # unchanged -- no attempt was made

    bound_audit = conn.execute(
        "SELECT * FROM audit_log WHERE event='action_skipped_bound_hit' AND subject_id=?", (key,)
    ).fetchall()
    assert len(bound_audit) == 1
    assert "max retry count" in bound_audit[0]["detail"]

    # Idempotent: routing again is a no-op, not a second abandonment.
    row = _fetch_row_with_age(conn, key)
    result_again = route_exception(conn, row, MockPayoutExecutor())
    assert result_again["decision"] == "skipped"
    bound_audit_after = conn.execute(
        "SELECT * FROM audit_log WHERE event='action_skipped_bound_hit' AND subject_id=?", (key,)
    ).fetchall()
    assert len(bound_audit_after) == 1  # still just one -- not logged again


def test_router_abandons_when_too_old_without_touching_retry_count():
    conn = _fresh_db()
    key = "failed_payment:LED-7:-"
    _insert_exception(conn, key=key, cause="failed_payment", ledger_ref="LED-7",
                       created_days_ago=MAX_EXCEPTION_AGE_DAYS + 1)

    row = _fetch_row_with_age(conn, key)
    assert row["age_days"] > MAX_EXCEPTION_AGE_DAYS

    result = route_exception(conn, row, MockPayoutExecutor())
    assert result["decision"] == "abandoned"
    assert result["reason"] == "max_age"

    exc = conn.execute("SELECT * FROM exceptions WHERE exception_key=?", (key,)).fetchone()
    assert exc["status"] == "abandoned"
    assert exc["retry_count"] == 0  # no attempt was actually made

    bound_audit = conn.execute(
        "SELECT * FROM audit_log WHERE event='action_skipped_bound_hit' AND subject_id=?", (key,)
    ).fetchone()
    assert "exceeds the" in bound_audit["detail"]


# ---------------------------------------------------------------------------
# End to end on the M1 fixture, through route_open_exceptions + funnel
# ---------------------------------------------------------------------------

def test_route_open_exceptions_dispatches_one_action_per_exception_on_first_pass():
    ledger_rows, gateway_rows, gt = generate_dataset()
    conn = _fresh_db()
    load_dataset_into_db(conn, ledger_rows, gateway_rows)
    classify_and_persist_from_db(conn)

    summary = route_open_exceptions(conn)
    # Only 24 are eligible: the 9 fee_mismatch cases already auto-resolved
    # at classification time (see classify.py) and are never 'open'.
    assert summary == {"dispatched": 24, "abandoned": 0, "skipped": 0, "error": 0}

    action_types = [r["action_type"] for r in conn.execute("SELECT action_type FROM actions")]
    assert action_types.count("retry_payout") == 9        # failed_payment cases
    assert action_types.count("draft_dispute_note") == 0  # all fee_mismatch cases auto-resolved
    assert action_types.count("send_reminder") == 6        # timing_lag cases
    assert action_types.count("flag_for_review") == 9      # duplicate (6) + unexplained (3)

    retry_exc = conn.execute(
        "SELECT retry_count FROM exceptions WHERE cause='failed_payment' LIMIT 1"
    ).fetchone()
    assert retry_exc["retry_count"] == 1

    pending = conn.execute(
        "SELECT status, retry_count FROM exceptions WHERE cause='timing_lag' LIMIT 1"
    ).fetchone()
    assert pending["status"] == "pending"
    assert pending["retry_count"] == 1

    # Second pass: nothing new is dispatched at all.
    #
    # This used to expect 9 fresh failed_payment attempts, on the reasoning
    # that retry_payout is the one bounded, retryable action type. That was
    # wrong, and expensively so: those 9 payouts had been dispatched and not
    # yet confirmed either way, so retrying them paid every one of those
    # payees a second time and produced a second payout webhook per
    # exception. An attempt that is merely unconfirmed has not failed.
    # Retrying now waits for confirm_action() -- see the in-flight guard in
    # route_exception and the tests at the end of this file.
    #
    # duplicate/unexplained are skipped for the older reason: they already
    # got their one flag, and redispatching the same flag says nothing new.
    # timing_lag moved to 'pending' on the first pass and isn't selected here.
    summary2 = route_open_exceptions(conn)
    assert summary2["dispatched"] == 0
    assert summary2["skipped"] == 18    # failed_payment (9) in flight + duplicate (6) + unexplained (3)

    retry_exc2 = conn.execute(
        "SELECT retry_count FROM exceptions WHERE cause='failed_payment' LIMIT 1"
    ).fetchone()
    assert retry_exc2["retry_count"] == 1  # unconfirmed attempt burns no retry budget

    flagged_exc = conn.execute(
        "SELECT status, retry_count FROM exceptions WHERE cause='duplicate' LIMIT 1"
    ).fetchone()
    assert flagged_exc["status"] == "open"
    assert flagged_exc["retry_count"] == 1  # never redispatched


def test_confirming_a_retry_updates_the_funnel():
    ledger_rows, gateway_rows, gt = generate_dataset()
    conn = _fresh_db()
    load_dataset_into_db(conn, ledger_rows, gateway_rows)
    classify_and_persist_from_db(conn)
    baseline = compute_funnel(conn)  # 9 fee_mismatch already auto-resolved
    route_open_exceptions(conn)

    action = conn.execute(
        "SELECT * FROM actions WHERE action_type='retry_payout' LIMIT 1"
    ).fetchone()
    confirm_action(conn, action["id"], "processed")

    funnel = compute_funnel(conn)
    assert funnel["recovered"] == baseline["recovered"] + 1
    assert funnel["exceptions"] == baseline["exceptions"] - 1
    assert funnel["matched"] == 36
    assert funnel["matched"] + funnel["exceptions"] + funnel["recovered"] == funnel["ingested"]

    exc = conn.execute("SELECT amount_paise FROM exceptions WHERE exception_key=?",
                        (action["exception_key"],)).fetchone()
    assert funnel["amount_recovered_paise"] == baseline["amount_recovered_paise"] + exc["amount_paise"]


# ---------------------------------------------------------------------------
# API level
# ---------------------------------------------------------------------------

def test_route_and_confirm_via_api(isolated_db):
    from fastapi.testclient import TestClient

    from app.db import get_connection
    from app.main import app

    with TestClient(app) as client:
        ledger_rows, gateway_rows, gt = generate_dataset()
        conn = get_connection()
        load_dataset_into_db(conn, ledger_rows, gateway_rows)
        conn.close()

        client.post("/pipeline/reconcile")
        route_resp = client.post("/pipeline/route")
        assert route_resp.status_code == 200
        assert route_resp.json()["dispatched"] == 24  # 9 fee_mismatch already auto-resolved

        conn = get_connection()
        action = conn.execute(
            "SELECT * FROM actions WHERE action_type='retry_payout' LIMIT 1"
        ).fetchone()
        conn.close()

        confirm_resp = client.post(f"/actions/{action['id']}/confirm",
                                    json={"outcome": "processed"})
        assert confirm_resp.status_code == 200

        funnel = client.get("/funnel").json()
        assert funnel["recovered"] == 10  # 9 fee_mismatch auto-resolved + this one confirmed

        bad_resp = client.post(f"/actions/999999/confirm", json={"outcome": "processed"})
        assert bad_resp.status_code == 400


# ---------------------------------------------------------------------------
# The payout instruction reaches the executor from the ledger row itself,
# with no side-car provisioning step in between.
# ---------------------------------------------------------------------------

class _CapturingExecutor:
    def __init__(self):
        self.calls = []

    def create_payout(self, *, idempotency_key, amount_paise, counterparty,
                       purpose, payout_instruction=None):
        self.calls.append({
            "counterparty": counterparty,
            "payout_instruction": payout_instruction,
        })
        return {"gateway_payout_id": "pout_captured", "status": "processing"}


def _insert_ledger_row(conn, *, ref, **overrides):
    row = {
        "external_ref": ref, "counterparty": "Acme Traders", "amount_paise": 100_000,
        "payout_purpose": "vendor bill", "payout_mode": "NEFT",
        "fund_account_type": "bank_account", "fund_account_name": "Acme Traders",
        "fund_account_ifsc": "HDFC0000053", "fund_account_number": "50100112233445",
        "contact_type": "vendor", "contact_email": "accounts@acmetraders.co.in",
        "contact_mobile": "9490234307",
    }
    row.update(overrides)
    cols = ", ".join(row)
    conn.execute(
        f"""INSERT INTO transactions (source, reference_id, currency, occurred_at, {cols})
            VALUES ('ledger', ?, 'INR', datetime('now'), {', '.join('?' * len(row))})""",
        (ref, *row.values()),
    )
    conn.commit()


def test_router_hands_the_ledger_rows_own_payout_instruction_to_the_executor():
    conn = _fresh_db()
    _insert_ledger_row(conn, ref="LED-0052")
    _insert_exception(conn, key="failed_payment:LED-0052:-", cause="failed_payment",
                       ledger_ref="LED-0052")

    executor = _CapturingExecutor()
    route_exception(conn, _fetch_row_with_age(conn, "failed_payment:LED-0052:-"), executor)

    assert len(executor.calls) == 1
    instruction = executor.calls[0]["payout_instruction"]
    assert instruction is not None
    assert instruction["fund_account_ifsc"] == "HDFC0000053"
    assert instruction["fund_account_number"] == "50100112233445"
    assert instruction["payout_mode"] == "NEFT"
    assert instruction["contact_email"] == "accounts@acmetraders.co.in"


def test_router_passes_none_when_the_ledger_row_has_no_instruction():
    # The mock ignores it; the live executor turns it into a specific,
    # per-exception error rather than a failed batch.
    conn = _fresh_db()
    _insert_ledger_row(conn, ref="LED-0099", fund_account_type=None,
                       fund_account_ifsc=None, fund_account_number=None)
    _insert_exception(conn, key="failed_payment:LED-0099:-", cause="failed_payment",
                       ledger_ref="LED-0099")

    executor = _CapturingExecutor()
    route_exception(conn, _fetch_row_with_age(conn, "failed_payment:LED-0099:-"), executor)
    assert executor.calls[0]["payout_instruction"] is None


def test_router_survives_an_exception_whose_ledger_row_is_missing_entirely():
    conn = _fresh_db()
    _insert_exception(conn, key="failed_payment:LED-GONE:-", cause="failed_payment",
                       ledger_ref="LED-GONE")

    executor = _CapturingExecutor()
    result = route_exception(conn, _fetch_row_with_age(conn, "failed_payment:LED-GONE:-"), executor)
    assert result["decision"] == "dispatched"
    assert executor.calls[0]["payout_instruction"] is None


# ---------------------------------------------------------------------------
# Re-running /pipeline/route must never duplicate a payout that is merely
# unconfirmed. Dispatch leaves the exception 'open' (dispatch != success),
# which is also what route_open_exceptions selects -- so without an in-flight
# guard a second route pass pays the same payee twice and produces a second
# payout webhook for the same exception.
# ---------------------------------------------------------------------------

def test_repeated_route_passes_do_not_redispatch_an_in_flight_payout():
    conn = _fresh_db()
    key = "failed_payment:LED-0052:-"
    _insert_exception(conn, key=key, cause="failed_payment", ledger_ref="LED-0052")

    first = route_open_exceptions(conn, MockPayoutExecutor())
    assert first["dispatched"] == 1

    for _ in range(3):
        again = route_open_exceptions(conn, MockPayoutExecutor())
        assert again == {"dispatched": 0, "abandoned": 0, "skipped": 1, "error": 0}

    assert conn.execute("SELECT COUNT(*) FROM actions WHERE exception_key=?",
                         (key,)).fetchone()[0] == 1
    # the unconfirmed attempt must not have burned the retry budget either
    assert conn.execute("SELECT retry_count FROM exceptions WHERE exception_key=?",
                         (key,)).fetchone()[0] == 1


def test_in_flight_skip_is_written_to_the_audit_log():
    conn = _fresh_db()
    key = "failed_payment:LED-0052:-"
    _insert_exception(conn, key=key, cause="failed_payment", ledger_ref="LED-0052")
    route_open_exceptions(conn, MockPayoutExecutor())
    route_open_exceptions(conn, MockPayoutExecutor())

    entry = conn.execute(
        "SELECT * FROM audit_log WHERE event='action_skipped_in_flight' AND subject_id=?",
        (key,),
    ).fetchone()
    assert entry is not None
    assert "waiting on a payout webhook" in entry["detail"]


def test_retry_resumes_once_a_webhook_confirms_the_attempt_actually_failed():
    conn = _fresh_db()
    key = "failed_payment:LED-0052:-"
    _insert_exception(conn, key=key, cause="failed_payment", ledger_ref="LED-0052")

    route_open_exceptions(conn, MockPayoutExecutor())
    action_id = conn.execute("SELECT id FROM actions WHERE exception_key=?", (key,)).fetchone()[0]

    # blocked while in flight...
    assert route_open_exceptions(conn, MockPayoutExecutor())["skipped"] == 1
    # ...and unblocked by a real confirmed failure, which is the only thing
    # that makes a retry legitimate.
    confirm_action(conn, action_id, "reversed")
    assert route_open_exceptions(conn, MockPayoutExecutor())["dispatched"] == 1

    attempts = [r["attempt_number"] for r in
                conn.execute("SELECT attempt_number FROM actions WHERE exception_key=? "
                              "ORDER BY attempt_number", (key,))]
    assert attempts == [1, 2]


def test_a_processed_payout_is_never_redispatched():
    conn = _fresh_db()
    key = "failed_payment:LED-0052:-"
    _insert_exception(conn, key=key, cause="failed_payment", ledger_ref="LED-0052")
    route_open_exceptions(conn, MockPayoutExecutor())
    action_id = conn.execute("SELECT id FROM actions WHERE exception_key=?", (key,)).fetchone()[0]

    confirm_action(conn, action_id, "processed")
    assert route_open_exceptions(conn, MockPayoutExecutor()) == {
        "dispatched": 0, "abandoned": 0, "skipped": 0, "error": 0
    }
    assert conn.execute("SELECT status FROM exceptions WHERE exception_key=?",
                         (key,)).fetchone()[0] == "resolved"


def test_the_in_flight_guard_is_scoped_to_its_own_exception():
    # One exception waiting on a webhook must not stall an unrelated one.
    conn = _fresh_db()
    _insert_exception(conn, key="failed_payment:LED-A:-", cause="failed_payment", ledger_ref="LED-A")
    route_open_exceptions(conn, MockPayoutExecutor())

    _insert_exception(conn, key="failed_payment:LED-B:-", cause="failed_payment", ledger_ref="LED-B")
    summary = route_open_exceptions(conn, MockPayoutExecutor())
    assert summary["dispatched"] == 1   # LED-B goes out
    assert summary["skipped"] == 1      # LED-A still waiting


# ---------------------------------------------------------------------------
# Payout status sync -- the fallback for when a webhook never arrives.
# Webhooks stay authoritative; this only ever touches in-flight actions.
# ---------------------------------------------------------------------------

class _FakeFetcher:
    def __init__(self, statuses):
        self.statuses = statuses
        self.asked = []

    def fetch_payout_status(self, gateway_payout_id):
        self.asked.append(gateway_payout_id)
        value = self.statuses[gateway_payout_id]
        if isinstance(value, Exception):
            raise value
        return value


def _dispatch_one(conn, key="failed_payment:LED-1:-", ledger_ref="LED-1"):
    from app.router import route_open_exceptions
    _insert_exception(conn, key=key, cause="failed_payment", ledger_ref=ledger_ref)
    route_open_exceptions(conn, MockPayoutExecutor())
    return conn.execute("SELECT id, gateway_payout_id FROM actions WHERE exception_key=?",
                         (key,)).fetchone()


def test_sync_confirms_a_payout_that_processed_without_a_webhook():
    from app.router import sync_payout_statuses

    conn = _fresh_db()
    action = _dispatch_one(conn)
    summary = sync_payout_statuses(conn, _FakeFetcher({action["gateway_payout_id"]: "processed"}))

    assert summary == {"checked": 1, "confirmed": 1, "still_in_flight": 0, "error": 0}
    assert conn.execute("SELECT status FROM actions WHERE id=?", (action["id"],)).fetchone()[0] == "processed"
    assert conn.execute("SELECT status FROM exceptions WHERE exception_key=?",
                         ("failed_payment:LED-1:-",)).fetchone()[0] == "resolved"


def test_sync_leaves_a_still_in_flight_payout_alone():
    from app.router import sync_payout_statuses

    conn = _fresh_db()
    action = _dispatch_one(conn)
    summary = sync_payout_statuses(conn, _FakeFetcher({action["gateway_payout_id"]: "processing"}))

    assert summary["still_in_flight"] == 1 and summary["confirmed"] == 0
    assert conn.execute("SELECT status FROM exceptions WHERE exception_key=?",
                         ("failed_payment:LED-1:-",)).fetchone()[0] == "open"


def test_sync_treats_every_terminal_failure_as_a_reversal():
    # RazorpayX can end a payout as failed/cancelled/rejected as well as
    # reversed; operationally they all mean the money didn't land, so the
    # exception reopens for another attempt rather than being resolved.
    from app.router import sync_payout_statuses

    for remote in ("reversed", "failed", "cancelled", "rejected"):
        conn = _fresh_db()
        action = _dispatch_one(conn)
        sync_payout_statuses(conn, _FakeFetcher({action["gateway_payout_id"]: remote}))
        assert conn.execute("SELECT status FROM exceptions WHERE exception_key=?",
                             ("failed_payment:LED-1:-",)).fetchone()[0] == "open", remote
        assert conn.execute("SELECT status FROM actions WHERE id=?",
                             (action["id"],)).fetchone()[0] == "reversed", remote


def test_sync_catches_a_reversal_that_lands_after_settlement():
    # 'processed' is not the end of the story: a beneficiary bank can return
    # funds days later and RazorpayX flips the payout to 'reversed'. This used
    # to be invisible -- the sweep watched only in-flight statuses, so a settled
    # action was never re-read and returned money kept counting as recovered.
    from app.router import sync_payout_statuses

    conn = _fresh_db()
    action = _dispatch_one(conn)
    confirm_action(conn, action["id"], "processed")
    assert conn.execute("SELECT status FROM exceptions WHERE exception_key=?",
                         ("failed_payment:LED-1:-",)).fetchone()[0] == "resolved"

    fetcher = _FakeFetcher({action["gateway_payout_id"]: "reversed"})
    summary = sync_payout_statuses(conn, fetcher)

    assert summary["checked"] == 1 and summary["confirmed"] == 1
    assert conn.execute("SELECT status FROM actions WHERE id=?",
                         (action["id"],)).fetchone()[0] == "reversed"
    # ...and the money goes back into the backlog rather than staying "recovered".
    assert conn.execute("SELECT status FROM exceptions WHERE exception_key=?",
                         ("failed_payment:LED-1:-",)).fetchone()[0] == "open"


def test_sync_never_flaps_a_reversal_back_to_processed():
    # The invariant the old "never revisit a settled action" rule was really
    # protecting. 'reversed' is terminal in the direction that matters -- the
    # money came back, and a retry would be a new payout with a new id -- so it
    # is never re-read and cannot be undone by a later poll.
    from app.router import sync_payout_statuses

    conn = _fresh_db()
    action = _dispatch_one(conn)
    confirm_action(conn, action["id"], "reversed")

    fetcher = _FakeFetcher({action["gateway_payout_id"]: "processed"})
    summary = sync_payout_statuses(conn, fetcher)

    assert summary["checked"] == 0
    assert fetcher.asked == []
    assert conn.execute("SELECT status FROM actions WHERE id=?",
                         (action["id"],)).fetchone()[0] == "reversed"


def test_sync_does_not_reconfirm_an_unchanged_settled_payout():
    # Settled payouts are re-read every pass now, so without a no-op guard each
    # one would be re-confirmed and re-audited every 5 seconds.
    from app.router import sync_payout_statuses

    conn = _fresh_db()
    action = _dispatch_one(conn)
    confirm_action(conn, action["id"], "processed")
    before = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]

    fetcher = _FakeFetcher({action["gateway_payout_id"]: "processed"})
    summary = sync_payout_statuses(conn, fetcher)

    assert summary["checked"] == 1        # it was looked at
    assert summary["confirmed"] == 0      # but nothing changed
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == before


def test_sync_stops_watching_settled_payouts_outside_the_reversal_window(monkeypatch):
    # The window bounds the per-pass cost: one API call per watched payout, so
    # watching every settled payout forever would grow unboundedly.
    from app import config
    from app.router import sync_payout_statuses

    conn = _fresh_db()
    action = _dispatch_one(conn)
    confirm_action(conn, action["id"], "processed")
    conn.execute("UPDATE actions SET updated_at = datetime('now', '-30 days') WHERE id=?",
                 (action["id"],))
    conn.commit()

    monkeypatch.setattr(config, "SYNC_REVERSAL_WINDOW_DAYS", 7)
    fetcher = _FakeFetcher({action["gateway_payout_id"]: "reversed"})
    assert sync_payout_statuses(conn, fetcher)["checked"] == 0
    assert fetcher.asked == []

    # Widen the window and the same payout comes back into view.
    monkeypatch.setattr(config, "SYNC_REVERSAL_WINDOW_DAYS", 60)
    assert sync_payout_statuses(conn, _FakeFetcher(
        {action["gateway_payout_id"]: "reversed"}))["confirmed"] == 1


def test_one_unreachable_payout_does_not_abandon_the_rest_of_the_sweep():
    from app.router import sync_payout_statuses

    conn = _fresh_db()
    a = _dispatch_one(conn, key="failed_payment:LED-1:-", ledger_ref="LED-1")
    b = _dispatch_one(conn, key="failed_payment:LED-2:-", ledger_ref="LED-2")

    summary = sync_payout_statuses(conn, _FakeFetcher({
        a["gateway_payout_id"]: RuntimeError("network is down"),
        b["gateway_payout_id"]: "processed",
    }))
    assert summary == {"checked": 2, "confirmed": 1, "still_in_flight": 0, "error": 1}
    assert conn.execute("SELECT status FROM exceptions WHERE exception_key=?",
                         ("failed_payment:LED-2:-",)).fetchone()[0] == "resolved"
    assert conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE event='payout_sync_failed'").fetchone()[0] == 1


def test_sync_records_that_it_acted_instead_of_a_webhook():
    from app.router import sync_payout_statuses

    conn = _fresh_db()
    action = _dispatch_one(conn)
    sync_payout_statuses(conn, _FakeFetcher({action["gateway_payout_id"]: "processed"}))

    entry = conn.execute(
        "SELECT detail FROM audit_log WHERE event='payout_synced_from_api'").fetchone()
    assert entry is not None
    assert "no webhook had been received" in entry["detail"]


# ---------------------------------------------------------------------------
# Scheduled payout sync (app.main._sync_payouts_on_a_timer)
# ---------------------------------------------------------------------------

def test_sync_timer_skips_quietly_when_credentials_are_absent(monkeypatch):
    """The loop runs on every install, configured or not. Without credentials
    it must do nothing rather than raise on a timer nobody asked to fail."""
    import asyncio

    from app import config, main

    monkeypatch.setattr(config, "RAZORPAYX_KEY_ID", "")
    monkeypatch.setattr(config, "RAZORPAYX_KEY_SECRET", "")

    called = []
    monkeypatch.setattr(main, "sync_payout_statuses",
                        lambda *a, **k: called.append(1) or {})

    async def run():
        task = asyncio.create_task(main._sync_payouts_on_a_timer(0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert called == []


def test_sync_timer_calls_the_sync_and_survives_a_failing_pass(monkeypatch):
    """One bad pass must not end the loop -- a transient network blip should
    not silence status syncing for the life of the process."""
    import asyncio

    from app import config, main

    monkeypatch.setattr(config, "RAZORPAYX_KEY_ID", "k")
    monkeypatch.setattr(config, "RAZORPAYX_KEY_SECRET", "s")

    calls = []

    def flaky(conn, fetcher):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("transient upstream failure")
        return {"checked": 1, "confirmed": 1, "still_in_flight": 0, "error": 0}

    monkeypatch.setattr(main, "sync_payout_statuses", flaky)
    monkeypatch.setattr(main, "RazorpayXPayoutStatusFetcher", lambda: object())

    async def run():
        task = asyncio.create_task(main._sync_payouts_on_a_timer(0.01))
        await asyncio.sleep(0.08)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    # Kept going after the exception on pass 1.
    assert len(calls) >= 2


def test_sync_timer_does_not_block_the_event_loop(monkeypatch):
    """The RazorpayX client is synchronous httpx and issues one request per
    in-flight payout. Run inline, a slow sweep would stall every dashboard
    request for its whole duration, so it has to go to a thread."""
    import asyncio
    import time

    from app import config, main

    monkeypatch.setattr(config, "RAZORPAYX_KEY_ID", "k")
    monkeypatch.setattr(config, "RAZORPAYX_KEY_SECRET", "s")

    def slow(conn, fetcher):
        time.sleep(0.12)          # stands in for a multi-payout sweep
        return {"checked": 1, "confirmed": 0, "still_in_flight": 1, "error": 0}

    monkeypatch.setattr(main, "sync_payout_statuses", slow)
    monkeypatch.setattr(main, "RazorpayXPayoutStatusFetcher", lambda: object())

    ticks = []

    async def run():
        async def heartbeat():
            # If the sync blocked the loop, these stop while it runs.
            for _ in range(12):
                await asyncio.sleep(0.01)
                ticks.append(1)

        task = asyncio.create_task(main._sync_payouts_on_a_timer(0.01))
        await heartbeat()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    # A blocking sync would swallow most of the 0.12s window; the loop stayed live.
    assert len(ticks) >= 10
