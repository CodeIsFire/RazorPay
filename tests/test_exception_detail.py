"""fetch_exception_detail(): pulls every raw transaction row correlated to
an exception, not just the refs it stores -- see the duplicate case below
for why that distinction matters (the original match is never stored on
the exception row itself)."""
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def _fresh_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())
    return conn


def _txn(source, ref, amount, reference_id=None, occurred_at="2026-01-01T10:00:00+00:00",
         counterparty="Vendor A", narration="", raw_json="{}", settlement_batch_id=None):
    return {
        "source": source, "external_ref": ref, "reference_id": reference_id,
        "amount_paise": amount, "currency": "INR", "counterparty": counterparty,
        "narration": narration, "occurred_at": occurred_at, "raw_json": raw_json,
        "settlement_batch_id": settlement_batch_id,
    }


def _insert_exception(conn, **kwargs):
    defaults = {
        "exception_key": "x", "cause": "unexplained", "ledger_ref": None,
        "gateway_ref": None, "matched_source": "gateway", "amount_paise": 0,
        "detail": "",
    }
    defaults.update(kwargs)
    conn.execute(
        """INSERT INTO exceptions (exception_key, cause, ledger_ref, gateway_ref,
               matched_source, amount_paise, detail)
           VALUES (:exception_key, :cause, :ledger_ref, :gateway_ref,
               :matched_source, :amount_paise, :detail)""",
        defaults,
    )
    conn.commit()


def test_returns_none_for_an_unknown_exception_key():
    from app.db import fetch_exception_detail
    conn = _fresh_db()
    assert fetch_exception_detail(conn, "no-such-key") is None
    conn.close()


def test_fee_mismatch_returns_one_ledger_row_and_one_gateway_row():
    from app.db import fetch_exception_detail
    from app.load_fixtures import load_transactions

    conn = _fresh_db()
    load_transactions(conn, [
        _txn("ledger", "L1", 100_000, reference_id="L1"),
        _txn("gateway", "G1", 98_500, reference_id="L1"),
    ])
    conn.commit()
    _insert_exception(conn, exception_key="fee_mismatch:L1:G1", cause="fee_mismatch",
                       ledger_ref="L1", gateway_ref="G1", amount_paise=100_000)

    detail = fetch_exception_detail(conn, "fee_mismatch:L1:G1")
    assert detail["exception"]["exception_key"] == "fee_mismatch:L1:G1"
    assert [t["external_ref"] for t in detail["ledger_transactions"]] == ["L1"]
    assert [t["external_ref"] for t in detail["actual_transactions"]] == ["G1"]
    conn.close()


def test_duplicate_surfaces_the_original_match_even_though_it_isnt_stored_on_the_exception():
    """The exception row only ever records the surplus gateway_ref -- the
    original matched transaction has to come from correlating reference_id,
    not from any field on the exception itself."""
    from app.db import fetch_exception_detail
    from app.load_fixtures import load_transactions

    conn = _fresh_db()
    load_transactions(conn, [
        _txn("ledger", "L1", 100_000, reference_id="L1"),
        _txn("gateway", "G1", 100_000, reference_id="L1"),  # the original match
        _txn("gateway", "G2", 100_000, reference_id="L1"),  # the surplus/duplicate
    ])
    conn.commit()
    _insert_exception(conn, exception_key="duplicate:L1:G2", cause="duplicate",
                       ledger_ref="L1", gateway_ref="G2", amount_paise=100_000)

    detail = fetch_exception_detail(conn, "duplicate:L1:G2")
    actual_refs = {t["external_ref"] for t in detail["actual_transactions"]}
    assert actual_refs == {"G1", "G2"}
    conn.close()


def test_batch_partial_payment_splits_comma_joined_ledger_refs():
    from app.db import fetch_exception_detail
    from app.load_fixtures import load_transactions

    conn = _fresh_db()
    load_transactions(conn, [
        _txn("ledger", "L1", 60_000, reference_id="L1", settlement_batch_id="B1"),
        _txn("ledger", "L2", 40_000, reference_id="L2", settlement_batch_id="B1"),
        _txn("gateway", "G1", 60_000, reference_id="B1"),
    ])
    conn.commit()
    _insert_exception(conn, exception_key="partial_payment:batch:B1:-", cause="partial_payment",
                       ledger_ref="L1,L2", gateway_ref="G1", amount_paise=100_000)

    detail = fetch_exception_detail(conn, "partial_payment:batch:B1:-")
    assert {t["external_ref"] for t in detail["ledger_transactions"]} == {"L1", "L2"}
    assert {t["external_ref"] for t in detail["actual_transactions"]} == {"G1"}
    conn.close()


def test_unexplained_has_no_ledger_transactions_and_does_not_error():
    from app.db import fetch_exception_detail
    from app.load_fixtures import load_transactions

    conn = _fresh_db()
    load_transactions(conn, [_txn("gateway", "G1", 5_000, reference_id="NOTHING")])
    conn.commit()
    _insert_exception(conn, exception_key="unexplained:-:G1", cause="unexplained",
                       ledger_ref=None, gateway_ref="G1", amount_paise=5_000)

    detail = fetch_exception_detail(conn, "unexplained:-:G1")
    assert detail["ledger_transactions"] == []
    assert [t["external_ref"] for t in detail["actual_transactions"]] == ["G1"]
    conn.close()


# ---------------------------------------------------------------------------
# API level
# ---------------------------------------------------------------------------

def test_detail_endpoint_returns_404_for_unknown_key(isolated_db):
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/exceptions/no-such-key/detail")
        assert resp.status_code == 404


def test_detail_endpoint_returns_the_full_picture(isolated_db):
    from fastapi.testclient import TestClient

    from app.db import get_connection
    from app.load_fixtures import load_transactions
    from app.main import app

    with TestClient(app) as client:
        conn = get_connection()
        load_transactions(conn, [
            _txn("ledger", "L1", 100_000, reference_id="L1"),
            _txn("gateway", "G1", 98_500, reference_id="L1"),
        ])
        conn.commit()
        _insert_exception(conn, exception_key="fee_mismatch:L1:G1", cause="fee_mismatch",
                           ledger_ref="L1", gateway_ref="G1", amount_paise=100_000)
        conn.close()

        resp = client.get("/exceptions/fee_mismatch:L1:G1/detail")
        assert resp.status_code == 200
        body = resp.json()
        assert body["exception"]["cause"] == "fee_mismatch"
        assert len(body["ledger_transactions"]) == 1
        assert len(body["actual_transactions"]) == 1
