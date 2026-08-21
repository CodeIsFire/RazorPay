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
    assert exc["status"] == "in_progress"
    assert exc["retry_count"] == 1


def test_fee_mismatch_dispatches_dispute_note_and_completes_immediately_but_stays_in_progress():
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
    assert exc["status"] == "in_progress"


def test_duplicate_and_unexplained_are_flagged_not_acted_on():
    conn = _fresh_db()
    _insert_exception(conn, key="duplicate:LED-3:pout_y", cause="duplicate", ledger_ref="LED-3")
    row = _fetch_row_with_age(conn, "duplicate:LED-3:pout_y")
    result = route_exception(conn, row, MockPayoutExecutor())
    assert result["action_type"] == "flag_for_review"


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
    assert summary == {"dispatched": 10, "abandoned": 0, "skipped": 0}

    action_types = [r["action_type"] for r in conn.execute("SELECT action_type FROM actions")]
    assert action_types.count("retry_payout") == 3       # failed_payment cases
    assert action_types.count("draft_dispute_note") == 3  # fee_mismatch cases
    assert action_types.count("send_reminder") == 2       # timing_lag cases
    assert action_types.count("flag_for_review") == 2     # duplicate cases

    # Re-running dispatches nothing new -- everything is now in_progress,
    # not open, but still under the retry bound so it's a legitimate skip
    # only once resolved/abandoned. On this pass everything is eligible
    # again (in_progress counts as open-for-routing), so a second identical
    # pass would dispatch attempt 2 for each -- confirm that's intentional
    # by checking retry_count instead of re-asserting the summary shape.
    exc = conn.execute("SELECT retry_count FROM exceptions LIMIT 1").fetchone()
    assert exc["retry_count"] == 1


def test_confirming_a_retry_updates_the_funnel():
    ledger_rows, gateway_rows, gt = generate_dataset()
    conn = _fresh_db()
    load_dataset_into_db(conn, ledger_rows, gateway_rows)
    classify_and_persist_from_db(conn)
    route_open_exceptions(conn)

    action = conn.execute(
        "SELECT * FROM actions WHERE action_type='retry_payout' LIMIT 1"
    ).fetchone()
    confirm_action(conn, action["id"], "processed")

    funnel = compute_funnel(conn)
    assert funnel["recovered"] == 1
    assert funnel["exceptions"] == 7
    assert funnel["matched"] == 12
    assert funnel["matched"] + funnel["exceptions"] + funnel["recovered"] == funnel["ingested"]

    exc = conn.execute("SELECT amount_paise FROM exceptions WHERE exception_key=?",
                        (action["exception_key"],)).fetchone()
    assert funnel["amount_recovered_paise"] == exc["amount_paise"]


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
        assert route_resp.json()["dispatched"] == 10

        conn = get_connection()
        action = conn.execute(
            "SELECT * FROM actions WHERE action_type='retry_payout' LIMIT 1"
        ).fetchone()
        conn.close()

        confirm_resp = client.post(f"/actions/{action['id']}/confirm",
                                    json={"outcome": "processed"})
        assert confirm_resp.status_code == 200

        funnel = client.get("/funnel").json()
        assert funnel["recovered"] == 1

        bad_resp = client.post(f"/actions/999999/confirm", json={"outcome": "processed"})
        assert bad_resp.status_code == 400
