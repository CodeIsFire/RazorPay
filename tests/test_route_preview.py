"""preview_route must answer "what would routing do" without doing any of it.

The whole point of the preview is that a human sees a number before real
payouts go out, so the tests that matter are the ones proving it is inert: no
writes, no executor, no network -- and an answer that actually matches the
routing pass it claims to predict.
"""
import sqlite3
from pathlib import Path

from app.config import MAX_EXCEPTION_AGE_DAYS, MAX_RETRY_COUNT
from app.router import MockPayoutExecutor, preview_route, route_open_exceptions

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"

TRACKED_TABLES = ("exceptions", "actions", "audit_log")


def _fresh_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())
    return conn


def _insert_exception(conn, *, key, cause, amount_paise=100_000, retry_count=0,
                       status="open", created_days_ago=0):
    conn.execute(
        """INSERT INTO exceptions
             (exception_key, cause, ledger_ref, matched_source, amount_paise,
              detail, status, retry_count, created_at, updated_at)
           VALUES (?, ?, ?, 'gateway', ?, 'test', ?, ?,
                   datetime('now', ?), datetime('now'))""",
        (key, cause, key, amount_paise, status, retry_count, f"-{created_days_ago} days"),
    )
    conn.commit()


def _snapshot(conn):
    """Everything preview_route is forbidden from changing."""
    return {
        table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
        for table in TRACKED_TABLES
    }


class ExplodingExecutor:
    """A preview that reaches an executor has already failed its contract."""

    def create_payout(self, **kwargs):
        raise AssertionError("preview_route must never dispatch a payout")


def test_preview_reports_what_routing_would_dispatch():
    conn = _fresh_db()
    _insert_exception(conn, key="EX-1", cause="failed_payment", amount_paise=250_000)
    _insert_exception(conn, key="EX-2", cause="failed_payment", amount_paise=125_000)

    preview = preview_route(conn)

    assert preview["would_dispatch"] == 2
    assert preview["value_paise"] == 375_000


def test_preview_changes_nothing():
    """The load-bearing property. Every table byte-identical afterwards --
    including audit_log, which route_exception writes to on every branch."""
    conn = _fresh_db()
    _insert_exception(conn, key="EX-1", cause="failed_payment")
    _insert_exception(conn, key="EX-2", cause="chargeback")
    # A row the router would ABANDON -- the branch that issues an UPDATE plus
    # an audit entry, so it is the one most likely to leak past a rollback.
    _insert_exception(conn, key="EX-3", cause="failed_payment", retry_count=MAX_RETRY_COUNT)

    before = _snapshot(conn)
    preview_route(conn)
    assert _snapshot(conn) == before

    # ...and still nothing after a second look, in case the savepoint left the
    # connection in a state where the next write behaves differently.
    preview_route(conn)
    assert _snapshot(conn) == before


def test_preview_never_touches_an_executor():
    conn = _fresh_db()
    _insert_exception(conn, key="EX-1", cause="failed_payment")

    # preview_route builds its own executor and must not accept an injected
    # one; this asserts the signature stays closed rather than growing a
    # parameter a caller could point at the live RazorpayX client.
    preview = preview_route(conn)
    assert preview["would_dispatch"] == 1


def test_preview_matches_the_routing_pass_it_predicts():
    """A preview that disagrees with the router is worse than no preview."""
    conn = _fresh_db()
    _insert_exception(conn, key="EX-1", cause="failed_payment", amount_paise=100_000)
    _insert_exception(conn, key="EX-2", cause="fee_mismatch", amount_paise=200_000)
    _insert_exception(conn, key="EX-3", cause="chargeback", amount_paise=300_000)
    _insert_exception(conn, key="EX-4", cause="failed_payment", retry_count=MAX_RETRY_COUNT)
    _insert_exception(conn, key="EX-5", cause="failed_payment",
                      created_days_ago=MAX_EXCEPTION_AGE_DAYS + 5)
    _insert_exception(conn, key="EX-6", cause="timing_lag", status="pending")

    preview = preview_route(conn)
    actual = route_open_exceptions(conn, executor=MockPayoutExecutor())

    assert preview["would_dispatch"] == actual["dispatched"]
    assert preview["would_skip"] == actual["skipped"]
    assert preview["would_abandon"] == actual["abandoned"]
    assert preview["would_error"] == actual["error"]


def test_preview_sees_the_in_flight_guard():
    """The reason this endpoint exists rather than a client-side count: an
    attempt still in flight is invisible to the frontend, and routing skips it
    rather than paying the payee twice."""
    conn = _fresh_db()
    _insert_exception(conn, key="EX-1", cause="failed_payment")

    assert preview_route(conn)["would_dispatch"] == 1

    route_open_exceptions(conn, executor=MockPayoutExecutor())

    # The dispatched attempt leaves the exception 'open' on purpose, so it is
    # still selected -- but it must now be skipped, not dispatched again.
    after = preview_route(conn)
    assert after["would_dispatch"] == 0
    assert after["would_skip"] == 1
    assert after["value_paise"] == 0


def test_preview_on_an_empty_backlog():
    conn = _fresh_db()
    preview = preview_route(conn)
    assert preview == {
        "would_dispatch": 0,
        "would_skip": 0,
        "would_abandon": 0,
        "would_error": 0,
        "value_paise": 0,
    }


def test_preview_does_not_deadlock_on_an_open_write_transaction():
    """Regression: the first implementation used sqlite3's backup() API, which
    needs a read lock on the source and therefore blocked forever when the
    caller had an uncommitted write open. It hung the test run rather than
    failing it. Plain SELECTs take no such lock."""
    conn = _fresh_db()
    _insert_exception(conn, key="EX-1", cause="failed_payment")

    conn.execute("INSERT INTO audit_log (actor, subject_type, subject_id, event) "
                 "VALUES ('test', 'exception', 'EX-1', 'before_preview')")
    assert conn.in_transaction  # the condition that used to deadlock

    preview_route(conn)

    conn.execute("INSERT INTO audit_log (actor, subject_type, subject_id, event) "
                 "VALUES ('test', 'exception', 'EX-1', 'after_preview')")
    conn.commit()

    events = [r["event"] for r in conn.execute("SELECT event FROM audit_log ORDER BY id")]
    assert events == ["before_preview", "after_preview"]


def test_preview_sees_the_callers_uncommitted_rows():
    """A copy built from reads reflects the caller's own open transaction, so
    the preview describes the database the route call will actually act on."""
    conn = _fresh_db()
    _insert_exception(conn, key="EX-1", cause="failed_payment", amount_paise=100_000)

    # Not committed -- backup() would have missed this entirely.
    conn.execute(
        """INSERT INTO exceptions
             (exception_key, cause, ledger_ref, matched_source, amount_paise,
              detail, status, retry_count, created_at, updated_at)
           VALUES ('EX-2', 'failed_payment', 'EX-2', 'gateway', 50000,
                   'uncommitted', 'open', 0, datetime('now'), datetime('now'))"""
    )

    preview = preview_route(conn)
    assert preview["would_dispatch"] == 2
    assert preview["value_paise"] == 150_000
