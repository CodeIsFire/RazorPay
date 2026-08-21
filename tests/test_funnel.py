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
    assert funnel["ingested"] == 20
    assert funnel["matched"] == 12
    assert funnel["exceptions"] == 8
    assert funnel["recovered"] == 0
    assert funnel["gateway_side_anomalies"] == 2
    assert funnel["amount_recovered_paise"] == 0
    assert funnel["match_rate"] == 0.6
    assert funnel["matched"] + funnel["exceptions"] + funnel["recovered"] == funnel["ingested"]
    conn.close()


def test_funnel_reflects_resolved_exceptions_as_recovered():
    ledger_rows, gateway_rows, gt = generate_dataset()
    conn = _fresh_db()
    load_dataset_into_db(conn, ledger_rows, gateway_rows)
    classify_and_persist_from_db(conn)

    # Simulate what M5 will eventually do: mark one exception resolved.
    row = conn.execute(
        "SELECT * FROM exceptions WHERE cause='failed_payment' LIMIT 1"
    ).fetchone()
    conn.execute("UPDATE exceptions SET status='resolved' WHERE id=?", (row["id"],))
    conn.commit()

    funnel = compute_funnel(conn)
    assert funnel["recovered"] == 1
    assert funnel["exceptions"] == 7
    assert funnel["matched"] == 12  # unaffected -- matched is decided at recon time
    assert funnel["amount_recovered_paise"] == row["amount_paise"]
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
        assert resp.json()["count"] == 10

        funnel_resp = client.get("/funnel")
        assert funnel_resp.status_code == 200
        funnel = funnel_resp.json()
        assert funnel["ingested"] == 20
        assert funnel["matched"] == 12
        assert funnel["exceptions"] == 8
        assert funnel["gateway_side_anomalies"] == 2

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
