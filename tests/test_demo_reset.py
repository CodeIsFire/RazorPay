"""POST /demo/reset -- put the app back to its out-of-the-box demo state.

This is the counterpart to the upload feature: uploading replaces a source
with your own data, and there was previously no way back short of deleting
the database file. The tests that matter are that it is a genuine reset (no
residue of whatever was there before) and that it lands somewhere useful --
seeded AND reconciled, because seeded-but-unreconciled looks broken on every
tab.
"""
import sqlite3

from fastapi.testclient import TestClient

from app import config
from app.main import app


def _conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _count(table, where="1=1"):
    conn = _conn()
    n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
    conn.close()
    return n


def _seed_foreign_data(client):
    """Whatever a user had before hitting reset -- here, an upload."""
    csv = (
        "external_ref,amount_paise,occurred_at,counterparty,narration\n"
        "MINE-1,150000,2026-09-01T10:00:00+00:00,My Vendor,My own row\n"
    )
    resp = client.post("/data/upload/ledger", params={"dry_run": "false"},
                        files={"file": ("ledger.csv", csv, "text/csv")})
    assert resp.status_code == 200


def test_reset_seeds_all_three_sources(isolated_db):
    with TestClient(app) as client:
        body = client.post("/demo/reset").json()

    assert body["ledger"] > 0
    assert body["gateway"] > 0
    assert body["bank_statement"] > 0
    for source in ("ledger", "gateway", "bank_statement"):
        assert _count("transactions", f"source='{source}'") == body[source]


def test_reset_replaces_uploaded_data_rather_than_adding_to_it(isolated_db):
    # The whole point: an upload put foreign rows in, reset takes them out.
    with TestClient(app) as client:
        _seed_foreign_data(client)
        assert _count("transactions", "external_ref='MINE-1'") == 1
        client.post("/demo/reset")

    assert _count("transactions", "external_ref='MINE-1'") == 0


def test_reset_clears_exceptions_and_actions_from_the_previous_data(isolated_db):
    with TestClient(app) as client:
        _seed_foreign_data(client)
        client.post("/pipeline/reconcile")
        stale = _count("exceptions")
        assert stale > 0

        client.post("/demo/reset")

    # Exceptions exist again, but they are the demo's -- none reference the
    # uploaded row that no longer exists.
    assert _count("exceptions", "ledger_ref='MINE-1'") == 0


def test_reset_leaves_the_data_reconciled_not_just_loaded(isolated_db):
    # Seeded-but-unreconciled reads as a broken app: 0% match rate, empty
    # backlog, nothing on any tab. Reset has to land on a working dashboard.
    with TestClient(app) as client:
        body = client.post("/demo/reset").json()
        funnel = client.get("/funnel").json()

    assert body["exceptions"] > 0
    assert _count("exceptions") == body["exceptions"]
    assert funnel["matched"] > 0
    assert funnel["match_rate"] > 0


def test_reset_reconciles_against_both_actual_sources(isolated_db):
    # Two passes, not one -- the bank statement half is exactly what the Run
    # menu cannot currently trigger, so reset must not skip it.
    with TestClient(app) as client:
        client.post("/demo/reset")

    assert _count("exceptions", "matched_source='gateway'") > 0
    assert _count("exceptions", "matched_source='bank_statement'") > 0


def test_reset_is_recorded_in_the_audit_log(isolated_db):
    with TestClient(app) as client:
        client.post("/demo/reset")
        entries = client.get("/audit").json()["entries"]

    reset_events = [e for e in entries if e["event"] == "demo_data_reset"]
    assert len(reset_events) == 1


def test_reset_is_repeatable(isolated_db):
    # Deterministic fixtures + a full clear means running it twice lands in
    # the same place, rather than piling a second copy on top.
    with TestClient(app) as client:
        first = client.post("/demo/reset").json()
        second = client.post("/demo/reset").json()

    assert first == second
    assert _count("transactions") == first["ledger"] + first["gateway"] + first["bank_statement"]
