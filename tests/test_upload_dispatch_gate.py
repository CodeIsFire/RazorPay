"""Uploaded payout instructions never dispatch on production credentials.

The upload template now accepts fund accounts, which means a CSV someone
pastes together can name a destination for real money. This app has no
authentication, and switching from test to production keys is a one-line
.env edit -- so the only thing standing between a pasted file and a real
payout is this gate.

It is deliberately not overridable. "demo now, real later" means the
production path is a later, deliberate piece of work; a flag that lifted the
gate today would let real money move before IFSC validation, authentication
and encryption-at-rest exist, while looking like those questions had been
considered and answered.

The gate keys on transactions.origin, not on the key alone: fixture rows
must keep dispatching in every mode, or the demo breaks on production
credentials for no good reason.
"""
import sqlite3
from pathlib import Path

import pytest

from app import config
from app.router import _routable_rows, route_exception

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"

TEST_KEY = "rzp_test_ABC123"
LIVE_KEY = "rzp_live_ABC123"


class _RecordingExecutor:
    """Records what it was asked to dispatch. Never touches the network --
    reaching this at all is the failure these tests look for."""

    def __init__(self):
        self.calls = []

    def create_payout(self, *, idempotency_key, amount_paise, counterparty,
                       purpose, payout_instruction=None):
        self.calls.append(idempotency_key)
        return {"gateway_payout_id": f"pout_{len(self.calls)}", "status": "processing"}


def _db_with_dispatchable_row(origin: str):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute(
        """INSERT INTO transactions (source, external_ref, reference_id, amount_paise,
             occurred_at, counterparty, origin, fund_account_type, fund_account_name,
             fund_account_ifsc, fund_account_number, contact_type, payout_purpose, payout_mode)
           VALUES ('ledger','LED-1','LED-1',150000,'2026-09-01T10:00:00+00:00','Acme',?,
                   'bank_account','Acme Traders','HDFC0001234','50100123456789',
                   'vendor','vendor bill','NEFT')""",
        (origin,),
    )
    conn.execute(
        """INSERT INTO exceptions (exception_key, cause, ledger_ref, matched_source,
             amount_paise, detail, status)
           VALUES ('failed_payment:LED-1:-','failed_payment','LED-1','gateway',150000,'x','open')"""
    )
    conn.commit()
    return conn


def _route(conn, monkeypatch, key):
    monkeypatch.setattr(config, "RAZORPAYX_KEY_ID", key)
    # Via _routable_rows so the row carries age_days, exactly as a real
    # routing pass supplies it.
    row = dict(_routable_rows(conn)[0])
    executor = _RecordingExecutor()
    result = route_exception(conn, row, executor)
    conn.commit()
    return result, executor


def test_an_uploaded_row_is_refused_on_production_keys(monkeypatch):
    conn = _db_with_dispatchable_row("upload")
    result, executor = _route(conn, monkeypatch, LIVE_KEY)

    assert result["decision"] == "skipped"
    # The executor is never reached -- no request is built, let alone sent.
    assert executor.calls == []


def test_the_refusal_says_why_in_the_audit_log(monkeypatch):
    conn = _db_with_dispatchable_row("upload")
    _route(conn, monkeypatch, LIVE_KEY)

    events = [r[0] for r in conn.execute("SELECT event FROM audit_log")]
    details = " ".join(r[0] or "" for r in conn.execute("SELECT detail FROM audit_log"))
    assert "action_skipped_uploaded_on_live_key" in events
    assert "upload" in details


def test_an_uploaded_row_still_dispatches_on_test_keys(monkeypatch):
    # The demo path. Refusing here would make the feature useless.
    conn = _db_with_dispatchable_row("upload")
    result, executor = _route(conn, monkeypatch, TEST_KEY)

    assert result["decision"] == "dispatched"
    assert len(executor.calls) == 1


@pytest.mark.parametrize("key", [TEST_KEY, LIVE_KEY])
def test_a_generated_row_dispatches_in_either_mode(monkeypatch, key):
    # The gate is about provenance, not about the key. Fixture rows are
    # dispatchable on production credentials exactly as they were before.
    conn = _db_with_dispatchable_row("generated")
    result, executor = _route(conn, monkeypatch, key)

    assert result["decision"] == "dispatched"
    assert len(executor.calls) == 1


def test_the_gate_cannot_be_lifted_by_configuration(monkeypatch):
    """Pins the "no override" decision.

    If someone later adds an escape hatch, this test should fail and force
    the conversation rather than let the hatch land quietly.
    """
    conn = _db_with_dispatchable_row("upload")
    for name in ("RR_ALLOW_LIVE_UPLOADED_PAYOUTS", "ALLOW_LIVE_UPLOADED_PAYOUTS"):
        monkeypatch.setenv(name, "1")
    result, executor = _route(conn, monkeypatch, LIVE_KEY)

    assert result["decision"] == "skipped"
    assert executor.calls == []
