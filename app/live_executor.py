"""Real RazorpayX-backed PayoutExecutor -- same interface as router.py's
MockPayoutExecutor, swapped in once live credentials are configured (see
app/main.py's executor factory). The router doesn't know or care which one
it's talking to.

create_payout() returns RazorpayX's own status verbatim ('queued',
'processing', ...) rather than collapsing it into our internal vocabulary
-- see schema.sql's actions.status CHECK, which was widened at M6 to
accept RazorpayX's real states, not just the two the mock ever produces.

A real payout needs to know WHERE the money goes, and the synthetic
ledger (M1) has no real bank details -- it's fixture data. fund_account_map
is a simple counterparty(=ledger_ref) -> fund_account_id dict, loaded from
JSON at data/fund_account_map.json. See scripts/setup_test_payee.py for
creating one real test-mode Contact + Fund Account and populating that
file. Until it's populated, this executor fails loudly and specifically
per-exception (caught by router.route_exception, logged, no action taken)
rather than either crashing the whole batch or silently no-op'ing.
"""
import json
from pathlib import Path

from app import config, razorpayx_client as rzpx


def load_fund_account_map() -> dict:
    path = Path(config.RAZORPAYX_FUND_ACCOUNT_MAP_PATH)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


class RazorpayXPayoutExecutor:
    def __init__(self, fund_account_map: dict | None = None):
        self.fund_account_map = fund_account_map if fund_account_map is not None else load_fund_account_map()

    def create_payout(self, *, idempotency_key: str, amount_paise: int,
                       counterparty: str, purpose: str) -> dict:
        fund_account_id = self.fund_account_map.get(counterparty)
        if not fund_account_id:
            raise ValueError(
                f"no fund_account_id configured for counterparty={counterparty!r} in "
                f"{config.RAZORPAYX_FUND_ACCOUNT_MAP_PATH} -- run scripts/setup_test_payee.py "
                f"or add an entry manually before retrying this exception"
            )

        header_key = rzpx.sanitize_idempotency_key(idempotency_key)
        result = rzpx.create_payout(
            idempotency_key=header_key,
            fund_account_id=fund_account_id,
            amount_paise=amount_paise,
            purpose="payout",
            reference_id=idempotency_key,
            narration="Reconcile-Recover retry",
            queue_if_low_balance=True,
        )
        return {"gateway_payout_id": result["id"], "status": result["status"]}
