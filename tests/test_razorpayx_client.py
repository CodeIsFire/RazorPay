"""These test the REST client logic against a faked HTTP layer --
httpx.post/httpx.get are monkeypatched, nothing touches the network. This
sandbox has no route to api.razorpay.com at all (see M6 notes), so this is
the ceiling of what can be verified here; real verification happens
wherever this runs with actual network access.
"""
import re

import httpx
import pytest

from app import razorpayx_client as rzpx


def _fake_response(status_code, json_body):
    return httpx.Response(status_code, json=json_body,
                           request=httpx.Request("POST", "https://api.razorpay.com/v1/x"))


def test_sanitize_idempotency_key_is_charset_safe_and_length_bounded():
    raw = "failed_payment:LED-0018:-:attempt1"
    key = rzpx.sanitize_idempotency_key(raw)
    assert 4 <= len(key) <= 36
    assert re.fullmatch(r"[A-Za-z0-9]+", key)


def test_sanitize_idempotency_key_is_deterministic():
    raw = "failed_payment:LED-0018:-:attempt1"
    assert rzpx.sanitize_idempotency_key(raw) == rzpx.sanitize_idempotency_key(raw)
    assert rzpx.sanitize_idempotency_key(raw) != rzpx.sanitize_idempotency_key(raw + "x")


def test_create_contact_success(monkeypatch):
    captured = {}

    def fake_post(url, json=None, auth=None, headers=None, timeout=None):
        captured.update(url=url, json=json, auth=auth)
        return _fake_response(200, {"id": "cont_123", "name": json["name"]})

    monkeypatch.setattr(rzpx.httpx, "post", fake_post)
    result = rzpx.create_contact(name="Acme Traders", reference_id="LED-0018")

    assert result["id"] == "cont_123"
    assert captured["url"] == f"{rzpx.BASE_URL}/contacts"
    assert captured["json"] == {"name": "Acme Traders", "type": "vendor", "reference_id": "LED-0018"}


def test_create_fund_account_bank_success(monkeypatch):
    def fake_post(url, json=None, auth=None, headers=None, timeout=None):
        assert json["account_type"] == "bank_account"
        assert json["bank_account"] == {
            "name": "Acme", "ifsc": "HDFC0000053", "account_number": "765432123456789",
        }
        return _fake_response(200, {"id": "fa_123"})

    monkeypatch.setattr(rzpx.httpx, "post", fake_post)
    result = rzpx.create_fund_account_bank(
        contact_id="cont_123", account_holder_name="Acme",
        ifsc="HDFC0000053", account_number="765432123456789",
    )
    assert result["id"] == "fa_123"


def test_create_payout_sends_idempotency_header_and_passes_through_status(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, auth=None, timeout=None):
        captured.update(url=url, json=json, headers=headers)
        return _fake_response(200, {"id": "pout_123", "status": "queued"})

    monkeypatch.setattr(rzpx.httpx, "post", fake_post)
    result = rzpx.create_payout(idempotency_key="abc123", fund_account_id="fa_123",
                                 amount_paise=50_000, reference_id="LED-0018")

    assert result["status"] == "queued"  # passed through verbatim, not coerced
    assert captured["headers"]["X-Payout-Idempotency"] == "abc123"
    assert captured["json"]["fund_account_id"] == "fa_123"
    assert captured["json"]["mode"] == "IMPS"
    assert captured["json"]["amount"] == 50_000


def test_create_payout_raises_typed_error_on_http_error(monkeypatch):
    def fake_post(url, json=None, headers=None, auth=None, timeout=None):
        return _fake_response(400, {"error": {"description": "bad request"}})

    monkeypatch.setattr(rzpx.httpx, "post", fake_post)
    with pytest.raises(rzpx.RazorpayXError) as exc_info:
        rzpx.create_payout(idempotency_key="abc", fund_account_id="fa_1", amount_paise=1_000)
    assert exc_info.value.status_code == 400


def test_fetch_payout_success(monkeypatch):
    def fake_get(url, auth=None, timeout=None):
        assert url.endswith("/payouts/pout_123")
        return _fake_response(200, {"id": "pout_123", "status": "processed"})

    monkeypatch.setattr(rzpx.httpx, "get", fake_get)
    result = rzpx.fetch_payout("pout_123")
    assert result["status"] == "processed"
