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


# ---------------------------------------------------------------------------
# Composite payout body -- built straight from a ledger row's Tally payout
# instruction, replacing the fund_account_map side-car.
# ---------------------------------------------------------------------------

BANK_INSTRUCTION = {
    "counterparty": "Acme Traders", "currency": "INR",
    "payout_purpose": "vendor bill", "payout_mode": "NEFT",
    "fund_account_type": "bank_account", "fund_account_name": "Acme Traders",
    "fund_account_ifsc": "HDFC0000053", "fund_account_number": "50100112233445",
    "fund_account_vpa": None,
    "contact_type": "vendor", "contact_email": "accounts@acmetraders.co.in",
    "contact_mobile": "9490234307",
}

VPA_INSTRUCTION = {
    "counterparty": "Indus Hardware", "currency": "INR",
    "payout_purpose": "vendor bill", "payout_mode": "UPI",
    "fund_account_type": "vpa", "fund_account_name": "Indus Hardware",
    "fund_account_ifsc": None, "fund_account_number": None,
    "fund_account_vpa": "indushardware@ybl",
    "contact_type": "vendor", "contact_email": "accounts@indushardware.co.in",
    "contact_mobile": "9000000001",
}


def test_bank_account_composite_body_matches_razorpayx_shape():
    fa = rzpx.build_composite_fund_account(BANK_INSTRUCTION)
    assert fa["account_type"] == "bank_account"
    assert fa["bank_account"] == {
        "name": "Acme Traders",
        "ifsc": "HDFC0000053",
        "account_number": "50100112233445",
    }
    assert fa["contact"]["name"] == "Acme Traders"
    assert fa["contact"]["type"] == "vendor"
    assert fa["contact"]["email"] == "accounts@acmetraders.co.in"
    assert fa["contact"]["contact"] == "9490234307"
    # a bank_account body must not also carry a vpa object
    assert "vpa" not in fa


def test_vpa_composite_body_matches_razorpayx_shape():
    fa = rzpx.build_composite_fund_account(VPA_INSTRUCTION)
    assert fa["account_type"] == "vpa"
    assert fa["vpa"] == {"address": "indushardware@ybl"}
    # and must not carry bank_account -- RazorpayX rejects the wrong shape
    assert "bank_account" not in fa


def test_optional_contact_fields_are_omitted_not_nulled():
    # An explicit null is a validation error at RazorpayX; an absent key
    # simply means "not provided".
    sparse = dict(BANK_INSTRUCTION, contact_email=None, contact_mobile=None)
    contact = rzpx.build_composite_fund_account(sparse)["contact"]
    assert "email" not in contact
    assert "contact" not in contact
    assert contact["name"] == "Acme Traders"


def test_unsupported_fund_account_type_is_rejected():
    import pytest
    with pytest.raises(ValueError, match="unsupported fund_account_type"):
        rzpx.build_composite_fund_account(dict(BANK_INSTRUCTION, fund_account_type="card"))


def test_composite_payout_sends_row_mode_purpose_and_idempotency(monkeypatch):
    captured = {}

    class _Resp:
        status_code = 200
        def json(self): return {"id": "pout_x", "status": "queued"}

    def fake_post(url, json=None, headers=None, auth=None, timeout=None):
        captured["url"] = url
        captured["body"] = json
        captured["headers"] = headers
        return _Resp()

    monkeypatch.setattr(rzpx.httpx, "post", fake_post)
    monkeypatch.setattr(rzpx.config, "RAZORPAYX_ACCOUNT_NUMBER", "2323230051247439")

    out = rzpx.create_composite_payout(
        idempotency_key="failed_payment:LED-0052:-:attempt1",
        instruction=BANK_INSTRUCTION, amount_paise=2918500,
        reference_id="failed_payment:LED-0052:-:attempt1",
        narration="Reconcile Recover retry",
    )

    assert out == {"id": "pout_x", "status": "queued"}
    assert captured["url"].endswith("/payouts")
    body = captured["body"]
    assert body["account_number"] == "2323230051247439"
    assert body["amount"] == 2918500
    assert body["currency"] == "INR"
    # taken from the ledger row, not from a config default
    assert body["mode"] == "NEFT"
    assert body["purpose"] == "vendor bill"
    assert body["fund_account"]["account_type"] == "bank_account"
    assert captured["headers"]["X-Payout-Idempotency"]
    # RazorpayX caps both of these; the client must truncate, not error
    assert len(body["reference_id"]) <= 40
    assert len(body["narration"]) <= 30


def test_vpa_row_settles_over_upi():
    fa = rzpx.build_composite_fund_account(VPA_INSTRUCTION)
    assert fa["account_type"] == "vpa"
    assert VPA_INSTRUCTION["payout_mode"] == "UPI"


def test_idempotency_header_is_stable_for_an_identical_request():
    body = {"amount": 100, "mode": "NEFT", "fund_account": {"account_type": "vpa"}}
    a = rzpx.idempotency_header_for("k:attempt1", body)
    b = rzpx.idempotency_header_for("k:attempt1", dict(reversed(list(body.items()))))
    # key order must not matter -- the body is canonicalised before hashing
    assert a == b
    assert 4 <= len(a) <= 36
    assert re.fullmatch(r"[A-Za-z0-9_\- ]+", a)


def test_idempotency_header_changes_when_the_body_changes():
    # This is the whole point: RazorpayX 400s if a key it already saw
    # arrives with different content, and our internal attempt counter
    # resets whenever the local DB is rebuilt.
    base = {"amount": 100, "mode": "NEFT"}
    assert (rzpx.idempotency_header_for("k:attempt1", base)
            != rzpx.idempotency_header_for("k:attempt1", {"amount": 200, "mode": "NEFT"}))


def test_composite_payout_header_reflects_the_instruction(monkeypatch):
    seen = []

    class _Resp:
        status_code = 200
        def json(self): return {"id": "pout_x", "status": "queued"}

    monkeypatch.setattr(rzpx.httpx, "post",
                        lambda url, json=None, headers=None, auth=None, timeout=None:
                            (seen.append(headers["X-Payout-Idempotency"]), _Resp())[1])
    monkeypatch.setattr(rzpx.config, "RAZORPAYX_ACCOUNT_NUMBER", "1234567890")

    same_key = "failed_payment:LED-0052:-:attempt1"
    rzpx.create_composite_payout(idempotency_key=same_key,
                                  instruction=BANK_INSTRUCTION, amount_paise=1000)
    rzpx.create_composite_payout(idempotency_key=same_key,
                                  instruction=BANK_INSTRUCTION, amount_paise=1000)
    rzpx.create_composite_payout(idempotency_key=same_key,
                                  instruction=VPA_INSTRUCTION, amount_paise=1000)

    assert seen[0] == seen[1], "an identical redispatch must stay idempotent"
    assert seen[2] != seen[0], "a different payee must not reuse the key"
