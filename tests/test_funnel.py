import sqlite3
from pathlib import Path

from app.classify import classify_and_persist_from_db
from app.fixtures import generate_dataset
from app.funnel import compute_funnel
from app.load_fixtures import load_dataset_into_db

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def _fresh_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())
    return conn


# ---------------------------------------------------------------------------
# Pure DB-level correctness
# ---------------------------------------------------------------------------

def test_funnel_matches_ground_truth_before_any_recovery():
    ledger_rows, gateway_rows, gt = generate_dataset()
    conn = _fresh_db()
    load_dataset_into_db(conn, ledger_rows, gateway_rows)
    classify_and_persist_from_db(conn)

    funnel = compute_funnel(conn)
    assert funnel["ingested"] == 60
    assert funnel["matched"] == 36
    # 9 fee_mismatch cases auto-resolve immediately at classification time
    # (delta within the known fee tolerance -- see classify.py), so they're
    # already "recovered" before anyone routes or confirms anything.
    assert funnel["exceptions"] == 15
    assert funnel["recovered"] == 9
    assert funnel["gateway_side_anomalies"] == 9
    assert funnel["amount_recovered_paise"] > 0
    assert funnel["match_rate"] == 0.6
    assert funnel["matched"] + funnel["exceptions"] + funnel["recovered"] == funnel["ingested"]
    conn.close()


def test_funnel_reflects_resolved_exceptions_as_recovered():
    ledger_rows, gateway_rows, gt = generate_dataset()
    conn = _fresh_db()
    load_dataset_into_db(conn, ledger_rows, gateway_rows)
    classify_and_persist_from_db(conn)
    baseline = compute_funnel(conn)  # 9 fee_mismatch already auto-resolved

    # Simulate what M5 will eventually do: mark one more exception resolved.
    row = conn.execute(
        "SELECT * FROM exceptions WHERE cause='failed_payment' LIMIT 1"
    ).fetchone()
    conn.execute("UPDATE exceptions SET status='resolved' WHERE id=?", (row["id"],))
    conn.commit()

    funnel = compute_funnel(conn)
    assert funnel["recovered"] == baseline["recovered"] + 1
    assert funnel["exceptions"] == baseline["exceptions"] - 1
    assert funnel["matched"] == 36  # unaffected -- matched is decided at recon time
    assert funnel["amount_recovered_paise"] == baseline["amount_recovered_paise"] + row["amount_paise"]
    assert funnel["matched"] + funnel["exceptions"] + funnel["recovered"] == funnel["ingested"]
    conn.close()


def test_funnel_handles_empty_db_without_dividing_by_zero():
    conn = _fresh_db()
    funnel = compute_funnel(conn)
    assert funnel["ingested"] == 0
    assert funnel["matched"] == 0
    assert funnel["match_rate"] is None
    conn.close()


# ---------------------------------------------------------------------------
# API level: pipeline trigger -> funnel -> audit, through real HTTP calls
# ---------------------------------------------------------------------------

def test_pipeline_funnel_and_audit_endpoints(isolated_db):
    from fastapi.testclient import TestClient

    from app.db import get_connection
    from app.main import app

    with TestClient(app) as client:
        ledger_rows, gateway_rows, gt = generate_dataset()
        conn = get_connection()
        load_dataset_into_db(conn, ledger_rows, gateway_rows)
        conn.close()

        resp = client.post("/pipeline/reconcile")
        assert resp.status_code == 200
        assert resp.json()["count"] == 33

        funnel_resp = client.get("/funnel")
        assert funnel_resp.status_code == 200
        funnel = funnel_resp.json()
        assert funnel["ingested"] == 60
        assert funnel["matched"] == 36
        assert funnel["exceptions"] == 15  # 9 fee_mismatch already auto-resolved
        assert funnel["recovered"] == 9
        assert funnel["gateway_side_anomalies"] == 9

        # Idempotent: re-running finds nothing new.
        resp2 = client.post("/pipeline/reconcile")
        assert resp2.json()["count"] == 0

        audit_resp = client.get("/audit")
        assert audit_resp.status_code == 200
        audit = audit_resp.json()
        assert audit["count"] > 0
        events = {e["event"] for e in audit["entries"]}
        assert "exception_created" in events
        assert "reconciliation_complete" in events

        scoped_resp = client.get("/audit", params={"subject_type": "exception", "limit": 5})
        assert scoped_resp.status_code == 200
        assert len(scoped_resp.json()["entries"]) <= 5
        assert all(e["subject_type"] == "exception" for e in scoped_resp.json()["entries"])


def test_a_ref_with_both_resolved_and_open_exceptions_counts_once():
    """Regression: one ledger row can raise several exceptions with different
    causes. Counting the open and recovered ref sets independently put such a
    row in both, and since matched = ingested - exceptions - recovered it was
    then subtracted twice -- understating how many rows actually reconciled
    while the matched+exceptions+recovered identity still summed correctly."""
    conn = _fresh_db()
    conn.execute(
        """INSERT INTO transactions (source, external_ref, reference_id, amount_paise,
                                     currency, occurred_at)
           VALUES ('ledger', 'LED-1', 'LED-1', 1000, 'INR', '2026-08-20T10:00:00+00:00')"""
    )
    # Same ledger row, two causes: one closed, one still open.
    conn.execute(
        """INSERT INTO exceptions (exception_key, cause, ledger_ref, amount_paise, status)
           VALUES ('fee_mismatch:ledger:LED-1:-', 'fee_mismatch', 'LED-1', 400, 'resolved')"""
    )
    conn.execute(
        """INSERT INTO exceptions (exception_key, cause, ledger_ref, amount_paise, status)
           VALUES ('timing_lag:ledger:LED-1:-', 'timing_lag', 'LED-1', 600, 'open')"""
    )
    conn.commit()

    f = compute_funnel(conn)
    # The row is not reconciled, so it is an exception and NOT also a recovery.
    assert f["exceptions"] == 1
    assert f["recovered"] == 0
    assert f["matched"] == 0
    assert f["matched"] + f["exceptions"] + f["recovered"] == f["ingested"] == 1
    # Money is tracked separately from rows: the fee really was recovered.
    assert f["amount_recovered_paise"] == 400
    conn.close()


def test_ref_moves_to_recovered_once_nothing_is_left_open():
    conn = _fresh_db()
    conn.execute(
        """INSERT INTO transactions (source, external_ref, reference_id, amount_paise,
                                     currency, occurred_at)
           VALUES ('ledger', 'LED-1', 'LED-1', 1000, 'INR', '2026-08-20T10:00:00+00:00')"""
    )
    conn.execute(
        """INSERT INTO exceptions (exception_key, cause, ledger_ref, amount_paise, status)
           VALUES ('fee_mismatch:ledger:LED-1:-', 'fee_mismatch', 'LED-1', 400, 'resolved')"""
    )
    conn.execute(
        """INSERT INTO exceptions (exception_key, cause, ledger_ref, amount_paise, status)
           VALUES ('timing_lag:ledger:LED-1:-', 'timing_lag', 'LED-1', 600, 'open')"""
    )
    conn.commit()
    assert compute_funnel(conn)["recovered"] == 0

    conn.execute("UPDATE exceptions SET status='resolved' WHERE cause='timing_lag'")
    conn.commit()
    f = compute_funnel(conn)
    assert f["recovered"] == 1
    assert f["exceptions"] == 0
    assert f["matched"] + f["exceptions"] + f["recovered"] == f["ingested"]
    conn.close()
