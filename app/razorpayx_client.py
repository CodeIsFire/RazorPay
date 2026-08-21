"""Thin, direct REST client for the RazorpayX APIs this app needs:
Contacts, Fund Accounts, and Payouts.

Deliberately NOT built on the official `razorpay` PyPI package: as of this
writing, that SDK's resource classes cover Payments-side resources plus
FundAccount, but not Payout or Contact (confirmed by reading its resources/
__init__.py and constants/url.py on GitHub) -- so there is no
client.payout.create() to call. Rather than reach into the SDK's
undocumented generic request() method and hope, this talks to the
well-documented REST endpoints directly over HTTPS Basic Auth, using httpx
(already a dependency for the test client). Every field name and
constraint below is taken from RazorpayX's published API reference, not
guessed:

  Contacts        POST /v1/contacts        https://razorpay.com/docs/api/x/contacts/create/
  Fund Accounts   POST /v1/fund_accounts   https://razorpay.com/docs/api/x/fund-accounts/create/bank-account/
  Payouts         POST /v1/payouts         https://razorpay.com/docs/api/x/payouts/create/bank-account/

Payouts require an `X-Payout-Idempotency` header on every request (mandatory
since March 2025), 4-36 chars, letters/digits/hyphen/underscore/space only.
Our internal idempotency keys use ':' as a separator and can run longer
than 36 chars, so sanitize_idempotency_key() hashes them into RazorpayX's
allowed shape -- deterministically, so retries of the same internal key
still produce the same header value.

This module has no network access from inside this sandbox (see M6 commit
notes) and is therefore verified here only against a faked HTTP layer --
see tests/test_razorpayx_client.py. Real verification happens wherever
this runs with an actual route to api.razorpay.com.
"""
import hashlib

import httpx

from app import config

BASE_URL = "https://api.razorpay.com/v1"
TIMEOUT_SECONDS = 15


class RazorpayXError(RuntimeError):
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self.payload = payload
        super().__init__(f"RazorpayX API error {status_code}: {payload}")


def sanitize_idempotency_key(raw_key: str) -> str:
    """RazorpayX's X-Payout-Idempotency header allows only 4-36 chars of
    letters/digits/hyphen/underscore/space. Our internal keys (e.g.
    'failed_payment:LED-0018:-:attempt1') use ':' and can exceed 36 chars,
    so hash rather than mangle -- a deterministic hash means a retried
    dispatch with the same raw_key reliably reuses the same header value,
    which is what makes RazorpayX's own idempotency protection meaningful
    here rather than accidental.
    """
    return hashlib.sha256(raw_key.encode()).hexdigest()[:32]


def _auth() -> tuple[str, str]:
    return (config.RAZORPAYX_KEY_ID, config.RAZORPAYX_KEY_SECRET)


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        try:
            payload = resp.json()
        except ValueError:
            payload = {"raw": resp.text}
        raise RazorpayXError(resp.status_code, payload)


def create_contact(*, name: str, contact_type: str = "vendor",
                    reference_id: str | None = None, email: str | None = None,
                    phone: str | None = None) -> dict:
    body = {"name": name, "type": contact_type}
    if reference_id:
        body["reference_id"] = reference_id[:40]
    if email:
        body["email"] = email
    if phone:
        body["contact"] = phone
    resp = httpx.post(f"{BASE_URL}/contacts", json=body, auth=_auth(), timeout=TIMEOUT_SECONDS)
    _raise_for_status(resp)
    return resp.json()


def create_fund_account_bank(*, contact_id: str, account_holder_name: str,
                              ifsc: str, account_number: str) -> dict:
    body = {
        "contact_id": contact_id,
        "account_type": "bank_account",
        "bank_account": {
            "name": account_holder_name,
            "ifsc": ifsc,
            "account_number": account_number,
        },
    }
    resp = httpx.post(f"{BASE_URL}/fund_accounts", json=body, auth=_auth(), timeout=TIMEOUT_SECONDS)
    _raise_for_status(resp)
    return resp.json()


def create_payout(*, idempotency_key: str, fund_account_id: str, amount_paise: int,
                   purpose: str = "payout", mode: str | None = None,
                   reference_id: str | None = None, narration: str | None = None,
                   queue_if_low_balance: bool = True) -> dict:
    body = {
        "account_number": config.RAZORPAYX_ACCOUNT_NUMBER,
        "fund_account_id": fund_account_id,
        "amount": amount_paise,
        "currency": "INR",
        "mode": mode or config.RAZORPAYX_PAYOUT_MODE,
        "purpose": purpose,
        "queue_if_low_balance": queue_if_low_balance,
    }
    if reference_id:
        body["reference_id"] = reference_id[:40]
    if narration:
        body["narration"] = narration[:30]

    headers = {"X-Payout-Idempotency": idempotency_key}
    resp = httpx.post(f"{BASE_URL}/payouts", json=body, headers=headers,
                       auth=_auth(), timeout=TIMEOUT_SECONDS)
    _raise_for_status(resp)
    return resp.json()


def fetch_payout(payout_id: str) -> dict:
    resp = httpx.get(f"{BASE_URL}/payouts/{payout_id}", auth=_auth(), timeout=TIMEOUT_SECONDS)
    _raise_for_status(resp)
    return resp.json()
