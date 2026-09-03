"""The upload endpoints: template out, CSV in, and the replace it performs.

Uploading a source *replaces* it, and a replace deletes exceptions and
actions derived from the old rows -- including, potentially, actions that
already dispatched real money. So the tests that matter most here are the
ones about blast radius: what a replace of one source is forbidden from
touching, and whether the dry run's numbers are the same numbers the real
replace acts on.
"""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app

LEDGER_CSV = (
    "external_ref,amount_paise,occurred_at,counterparty,narration\n"
    "ORD-1,150000,2026-09-01T10:00:00+00:00,Acme Traders,Payout to Acme\n"
    "ORD-2,42050,2026-09-01T11:30:00+00:00,Bharat Logistics,Payout to Bharat\n"
)

BANK_CSV = (
    "external_ref,amount_paise,occurred_at,counterparty,narration,reference_id\n"
    "TXN-1,150000,2026-09-01T12:00:00+00:00,Acme Traders,NEFT DR Acme,ORD-1\n"
)


def _upload(client, source, text, *, dry_run=False):
    return client.post(
        f"/data/upload/{source}",
        params={"dry_run": str(dry_run).lower()},
        files={"file": (f"{source}.csv", text, "text/csv")},
    )


def _conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _seed(sql, params=()):
    conn = _conn()
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def _count(table, where="1=1", params=()):
    conn = _conn()
    n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]
    conn.close()
    return n


def _seed_transaction(source, ref, *, amount=150000, occurred_at="2026-08-01T10:00:00+00:00"):
    _seed(
        "INSERT INTO transactions (source, external_ref, reference_id, amount_paise, "
        "occurred_at) VALUES (?, ?, ?, ?, ?)",
        (source, ref, ref, amount, occurred_at),
    )


def _seed_exception(key, *, ledger_ref=None, gateway_ref=None, matched_source="gateway"):
    _seed(
        "INSERT INTO exceptions (exception_key, cause, ledger_ref, gateway_ref, "
        "matched_source, amount_paise, detail) "
        "VALUES (?, 'failed_payment', ?, ?, ?, 150000, 'seeded')",
        (key, ledger_ref, gateway_ref, matched_source),
    )


def _seed_action(key, *, gateway_payout_id=None, attempt=1):
    _seed(
        "INSERT INTO actions (exception_key, attempt_number, action_type, "
        "idempotency_key, status, gateway_payout_id) "
        "VALUES (?, ?, 'retry_payout', ?, 'queued', ?)",
        (key, attempt, f"{key}-{attempt}", gateway_payout_id),
    )


# --- templates -------------------------------------------------------------

@pytest.mark.parametrize("source", ["ledger", "bank_statement"])
def test_template_downloads_as_a_csv_attachment(isolated_db, source):
    with TestClient(app) as client:
        resp = client.get(f"/data/templates/{source}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert f"{source}_template.csv" in resp.headers["content-disposition"]
    assert resp.text.splitlines()[0].startswith("external_ref,amount_paise,occurred_at")


def test_template_is_rejected_for_gateway(isolated_db):
    # gateway is RazorpayX's own record; there is no file to hand anyone.
    with TestClient(app) as client:
        resp = client.get("/data/templates/gateway")
    assert resp.status_code == 400


# --- summary ---------------------------------------------------------------

def test_summary_is_zero_before_anything_is_uploaded(isolated_db):
    with TestClient(app) as client:
        body = client.get("/data/summary").json()
    assert body == {
        "ledger": {"rows": 0, "updated_at": None},
        "bank_statement": {"rows": 0, "updated_at": None},
    }


def test_summary_counts_each_source_separately(isolated_db):
    with TestClient(app) as client:
        _upload(client, "ledger", LEDGER_CSV)
        body = client.get("/data/summary").json()
    assert body["ledger"]["rows"] == 2
    assert body["ledger"]["updated_at"] is not None
    assert body["bank_statement"] == {"rows": 0, "updated_at": None}


# --- dry run ---------------------------------------------------------------

def test_dry_run_reports_impact_without_writing(isolated_db):
    with TestClient(app) as client:
        _seed_transaction("ledger", "OLD-1")
        _seed_exception("exc-1", ledger_ref="OLD-1")
        _seed_action("exc-1")

        resp = _upload(client, "ledger", LEDGER_CSV, dry_run=True)

    assert resp.status_code == 200
    assert resp.json() == {
        "rows_to_load": 2,
        "rows_to_delete": 1,
        "exceptions_to_delete": 1,
        "actions_to_delete": 1,
        "live_payouts_affected": 0,
    }
    # Inert, like GET /pipeline/route/preview: nothing moved.
    assert _count("transactions") == 1
    assert _count("exceptions") == 1
    assert _count("actions") == 1


def test_dry_run_counts_dispatched_payouts_among_the_actions_it_would_delete(isolated_db):
    # An action with a gateway_payout_id already moved real money. Deleting
    # its record is not the same as deleting a queued attempt, and the
    # confirm panel has to be able to say so before the user commits.
    with TestClient(app) as client:
        _seed_transaction("ledger", "OLD-1")
        _seed_exception("exc-1", ledger_ref="OLD-1")
        _seed_action("exc-1", gateway_payout_id="pout_LIVE1")
        _seed_action("exc-1", attempt=2)

        body = _upload(client, "ledger", LEDGER_CSV, dry_run=True).json()

    assert body["actions_to_delete"] == 2
    assert body["live_payouts_affected"] == 1


def test_dry_run_numbers_match_what_the_real_replace_does(isolated_db):
    with TestClient(app) as client:
        _seed_transaction("ledger", "OLD-1")
        _seed_transaction("ledger", "OLD-2")
        _seed_exception("exc-1", ledger_ref="OLD-1")
        _seed_action("exc-1")

        preview = _upload(client, "ledger", LEDGER_CSV, dry_run=True).json()
        real = _upload(client, "ledger", LEDGER_CSV, dry_run=False).json()

    assert real == {
        "rows_loaded": preview["rows_to_load"],
        "rows_deleted": preview["rows_to_delete"],
        "exceptions_deleted": preview["exceptions_to_delete"],
        "actions_deleted": preview["actions_to_delete"],
    }


# --- replace + cascade -----------------------------------------------------

def test_upload_replaces_the_source_it_names(isolated_db):
    with TestClient(app) as client:
        _seed_transaction("ledger", "OLD-1")
        resp = _upload(client, "ledger", LEDGER_CSV)

    assert resp.status_code == 200
    conn = _conn()
    refs = [r[0] for r in conn.execute(
        "SELECT external_ref FROM transactions WHERE source='ledger' ORDER BY external_ref")]
    conn.close()
    assert refs == ["ORD-1", "ORD-2"]


def test_a_ledger_replace_leaves_gateway_rows_and_bank_rows_alone(isolated_db):
    with TestClient(app) as client:
        _seed_transaction("gateway", "PAY-1")
        _seed_transaction("bank_statement", "TXN-9")
        body = _upload(client, "ledger", LEDGER_CSV).json()

    assert body["rows_loaded"] == 2
    assert _count("transactions", "source='gateway'") == 1
    assert _count("transactions", "source='bank_statement'") == 1


def test_a_ledger_replace_clears_ledger_derived_exceptions_and_their_actions(isolated_db):
    with TestClient(app) as client:
        _seed_exception("exc-ledger", ledger_ref="OLD-1")
        _seed_action("exc-ledger")
        _upload(client, "ledger", LEDGER_CSV)

    assert _count("exceptions") == 0
    assert _count("actions") == 0


def test_a_ledger_replace_leaves_an_orphan_exception_alone(isolated_db):
    # A gateway-only orphan (ledger_ref IS NULL) says "money moved that the
    # ledger never asked for". Replacing the ledger doesn't explain it away.
    with TestClient(app) as client:
        _seed_exception("exc-orphan", gateway_ref="PAY-9")
        _seed_action("exc-orphan")
        body = _upload(client, "ledger", LEDGER_CSV).json()

    assert body["exceptions_deleted"] == 0
    assert _count("exceptions", "exception_key='exc-orphan'") == 1
    assert _count("actions") == 1


def test_a_bank_replace_leaves_gateway_matched_exceptions_alone(isolated_db):
    with TestClient(app) as client:
        _seed_exception("exc-gw", ledger_ref="OLD-1", matched_source="gateway")
        _seed_exception("exc-bank", ledger_ref="OLD-1", matched_source="bank_statement")
        _seed_action("exc-gw")
        _seed_action("exc-bank")
        body = _upload(client, "bank_statement", BANK_CSV).json()

    assert body["exceptions_deleted"] == 1
    assert _count("exceptions", "exception_key='exc-gw'") == 1
    assert _count("exceptions", "exception_key='exc-bank'") == 0
    assert _count("actions", "exception_key='exc-gw'") == 1
    assert _count("actions", "exception_key='exc-bank'") == 0


def test_the_replace_is_recorded_in_the_audit_log(isolated_db):
    with TestClient(app) as client:
        _upload(client, "ledger", LEDGER_CSV)
        entries = client.get("/audit").json()["entries"]

    replaced = [e for e in entries if e["event"] == "data_replaced"]
    assert len(replaced) == 1
    assert replaced[0]["subject_id"] == "ledger"
    assert json.loads(replaced[0]["detail"])["rows_loaded"] == 2


# --- rejection -------------------------------------------------------------

def test_an_invalid_file_is_rejected_with_every_error_and_writes_nothing(isolated_db):
    bad = (
        "external_ref,amount_paise,occurred_at\n"
        "ORD-1,oops,2026-09-01T10:00:00+00:00\n"
        "ORD-2,150000,not-a-date\n"
    )
    with TestClient(app) as client:
        _seed_transaction("ledger", "OLD-1")
        resp = _upload(client, "ledger", bad)

    assert resp.status_code == 422
    assert [(e["row_number"], e["column"]) for e in resp.json()["errors"]] == [
        (1, "amount_paise"), (2, "occurred_at")
    ]
    # All-or-nothing: the old rows are still there, untouched.
    assert _count("transactions", "external_ref='OLD-1'") == 1
    assert _count("transactions") == 1


def test_one_bad_row_among_good_ones_loads_nothing(isolated_db):
    mostly_good = LEDGER_CSV + "ORD-3,-1,2026-09-01T12:00:00+00:00,Crimson,Payout\n"
    with TestClient(app) as client:
        resp = _upload(client, "ledger", mostly_good)

    assert resp.status_code == 422
    assert _count("transactions") == 0


def test_a_dry_run_of_an_invalid_file_is_still_a_422(isolated_db):
    with TestClient(app) as client:
        resp = _upload(client, "ledger", "external_ref\nORD-1\n", dry_run=True)
    assert resp.status_code == 422


def test_a_file_that_is_not_utf8_text_is_rejected_not_crashed(isolated_db):
    # A CSV saved as latin-1 out of an older accounting package is a
    # user's problem to fix, not a 500 for them to interpret.
    with TestClient(app) as client:
        resp = client.post(
            "/data/upload/ledger",
            files={"file": ("ledger.csv", "external_ref\nOR\xd0-1\n".encode("latin-1"),
                             "text/csv")},
        )
    assert resp.status_code == 422
    assert resp.json()["errors"] == [
        {"row_number": 0, "column": "", "message": "file must be UTF-8 encoded text"}
    ]


def test_upload_is_rejected_for_gateway(isolated_db):
    with TestClient(app) as client:
        resp = _upload(client, "gateway", LEDGER_CSV)
    assert resp.status_code == 400


def test_a_file_over_the_row_limit_is_rejected(isolated_db, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_MAX_ROWS", 2)
    too_many = LEDGER_CSV + "ORD-3,900,2026-09-01T12:00:00+00:00,Crimson,Payout\n"
    with TestClient(app) as client:
        resp = _upload(client, "ledger", too_many)

    assert resp.status_code == 422
    assert resp.json()["errors"] == [
        {"row_number": 0, "column": "", "message": "file has more than 2 data rows"}
    ]
    assert _count("transactions") == 0


def test_a_file_over_the_byte_limit_is_rejected(isolated_db, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_MAX_BYTES", 50)
    with TestClient(app) as client:
        resp = _upload(client, "ledger", LEDGER_CSV)

    assert resp.status_code == 413
    assert _count("transactions") == 0


def test_upload_limits_have_defaults(isolated_db):
    assert config.UPLOAD_MAX_ROWS == 5000
    assert config.UPLOAD_MAX_BYTES == 2_000_000


# --- the point of all of it ------------------------------------------------

def test_uploaded_data_reconciles_through_the_existing_pipeline(isolated_db):
    """The feature only means anything if uploaded rows behave like
    generated ones -- same matcher, same classifier, no special casing.
    ORD-1 has a bank row that matches it; ORD-2 has none."""
    with TestClient(app) as client:
        _upload(client, "ledger", LEDGER_CSV)
        _upload(client, "bank_statement", BANK_CSV)
        resp = client.post("/pipeline/reconcile", params={"actual_source": "bank_statement"})

    assert resp.status_code == 200
    conn = _conn()
    exceptions = [dict(r) for r in conn.execute("SELECT * FROM exceptions")]
    conn.close()
    assert [e["ledger_ref"] for e in exceptions] == ["ORD-2"]
    assert exceptions[0]["matched_source"] == "bank_statement"
