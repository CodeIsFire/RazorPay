import hashlib
import hmac
import json
import sqlite3
from pathlib import Path

import pytest

from app.classify import classify_and_persist_from_db
from app.fixtures import generate_dataset
from app.load_fixtures import load_dataset_into_db
from app.router import MockPayoutExecutor, route_open_exceptions
from app.webhooks import WebhookVerificationError, handle_webhook, verify_signature

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def _fresh_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())
    return conn


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _payout_webhook_payload(event: str, gateway_payout_id: str) -> dict:
    return {
        "entity": "event",
        "event": event,
        "contains": ["payout"],
        "payload": {"payout": {"entity": {"id": gateway_payout_id, "status": event.split(".")[1]}}},
        "created_at": 1700000000,
    }


def _dispatch_one_failed_payment(conn) -> dict:
    ledger_rows, gateway_rows, gt = generate_dataset()
    load_dataset_into_db(conn, ledger_rows, gateway_rows)
    classify_and_persist_from_db(conn)
    route_open_exceptions(conn, executor=MockPayoutExecutor())
    action = conn.execute(
        "SELECT * FROM actions WHERE action_type='retry_payout' LIMIT 1"
    ).fetchone()
    return dict(action)


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

def test_verify_signature_accepts_a_correctly_signed_body():
    body = b'{"event": "payout.processed"}'
    secret = "whsec_test"
    verify_signature(body, _sign(body, secret), secret)  # should not raise


def test_verify_signature_rejects_tampered_body():
    body = b'{"event": "payout.processed"}'
    secret = "whsec_test"
    sig = _sign(body, secret)
    with pytest.raises(WebhookVerificationError):
        verify_signature(b'{"event": "payout.reversed"}', sig, secret)


def test_verify_signature_rejects_wrong_secret():
    body = b'{"event": "payout.processed"}'
    sig = _sign(body, "whsec_test")
    with pytest.raises(WebhookVerificationError):
        verify_signature(body, sig, "whsec_wrong")


def test_verify_signature_rejects_missing_signature():
    body = b'{"event": "payout.processed"}'
    with pytest.raises(WebhookVerificationError):
        verify_signature(body, "", "whsec_test")


# ---------------------------------------------------------------------------
# handle_webhook logic
# ---------------------------------------------------------------------------

def test_handle_webhook_processed_resolves_the_exception():
    conn = _fresh_db()
    action = _dispatch_one_failed_payment(conn)

    result = handle_webhook(conn, _payout_webhook_payload("payout.processed", action["gateway_payout_id"]))
    assert result == {"handled": True, "event": "payout.processed",
                       "action_id": action["id"], "outcome": "processed"}

    exc = conn.execute("SELECT * FROM exceptions WHERE exception_key=?",
                        (action["exception_key"],)).fetchone()
    assert exc["status"] == "resolved"


def test_handle_webhook_reversed_reopens_the_exception():
    conn = _fresh_db()
    action = _dispatch_one_failed_payment(conn)

    result = handle_webhook(conn, _payout_webhook_payload("payout.reversed", action["gateway_payout_id"]))
    assert result["outcome"] == "reversed"

    exc = conn.execute("SELECT * FROM exceptions WHERE exception_key=?",
                        (action["exception_key"],)).fetchone()
    assert exc["status"] == "open"


def test_handle_webhook_ignores_uninteresting_events():
    conn = _fresh_db()
    result = handle_webhook(conn, {"event": "payout.queued", "payload": {}})
    assert result == {"handled": False, "event": "payout.queued"}


def test_handle_webhook_logs_unmatched_payout_without_crashing():
    conn = _fresh_db()
    result = handle_webhook(conn, _payout_webhook_payload("payout.processed", "pout_never_dispatched"))
    assert result == {"handled": False, "event": "payout.processed", "reason": "no matching action"}

    audit = conn.execute("SELECT * FROM audit_log WHERE event='webhook_unmatched'").fetchall()
    assert len(audit) == 1


def test_handle_webhook_raises_on_malformed_payload():
    conn = _fresh_db()
    with pytest.raises(ValueError):
        handle_webhook(conn, {"event": "payout.processed", "payload": {}})


# ---------------------------------------------------------------------------
# API level: the real endpoint, real signature, real DB state change
# ---------------------------------------------------------------------------

def test_webhook_endpoint_end_to_end(isolated_db, monkeypatch):
    from fastapi.testclient import TestClient

    from app import config
    from app.db import get_connection
    from app.main import app

    monkeypatch.setattr(config, "RAZORPAYX_WEBHOOK_SECRET", "whsec_test")

    with TestClient(app) as client:
        conn = get_connection()
        ledger_rows, gateway_rows, gt = generate_dataset()
        load_dataset_into_db(conn, ledger_rows, gateway_rows)
        conn.close()

        client.post("/pipeline/reconcile")
        route_resp = client.post("/pipeline/route")
        assert route_resp.json()["dispatched"] == 10  # mock executor -- no live creds set

        conn = get_connection()
        action = conn.execute(
            "SELECT * FROM actions WHERE action_type='retry_payout' LIMIT 1"
        ).fetchone()
        conn.close()

        body = json.dumps(_payout_webhook_payload("payout.processed", action["gateway_payout_id"])).encode()
        sig = _sign(body, "whsec_test")

        resp = client.post("/webhooks/razorpayx", content=body,
                            headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"})
        assert resp.status_code == 200
        assert resp.json()["handled"] is True

        funnel = client.get("/funnel").json()
        assert funnel["recovered"] == 1

        bad_resp = client.post("/webhooks/razorpayx", content=body,
                                headers={"X-Razorpay-Signature": "deadbeef"})
        assert bad_resp.status_code == 400


def test_webhook_endpoint_503_when_secret_not_configured(isolated_db, monkeypatch):
    from fastapi.testclient import TestClient

    from app import config
    from app.main import app

    monkeypatch.setattr(config, "RAZORPAYX_WEBHOOK_SECRET", "")

    with TestClient(app) as client:
        resp = client.post("/webhooks/razorpayx", content=b"{}",
                            headers={"X-Razorpay-Signature": "whatever"})
        assert resp.status_code == 503


def test_integration_status_reports_mock_when_unconfigured(isolated_db):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        status = client.get("/integration/status").json()
        assert status["executor"] == "mock"
        assert status["account_number_configured"] is False
