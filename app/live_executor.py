"""Real RazorpayX-backed PayoutExecutor -- same interface as router.py's
MockPayoutExecutor, swapped in once live credentials are configured (see
app/main.py's executor factory). The router doesn't know or care which one
it's talking to.

create_payout() returns RazorpayX's own status verbatim ('queued',
'processing', ...) rather than collapsing it into our internal vocabulary
-- see schema.sql's actions.status CHECK, which was widened at M6 to
accept RazorpayX's real states, not just the two the mock ever produces.

A real payout needs to know WHERE the money goes. That used to come from a
side-car: data/fund_account_map.json, a ledger_ref -> fund_account_id dict
populated by hand out of band, because the synthetic ledger had no bank
details to speak of. It doesn't need one any more --
`transactions` now carries the RazorpayX Tally batch-payout fields, so a
ledger row IS a payout instruction, and this executor sends it as a
composite payout (contact + fund account + payout in a single call).

Two consequences worth knowing:

  * Adding a payee is now a data change in the ledger, not an out-of-band
    provisioning step someone has to remember to run.
  * The fixtures' IFSC codes are real ones, verified against Razorpay's own
    directory -- RazorpayX validates IFSC on fund account creation, so
    invented codes would be rejected here (see app/fixtures.py:IFSC_CODES).

A row with no payout instruction fails loudly and specifically per-exception
(caught by router.route_exception, logged, no action taken) rather than
either crashing the whole batch or silently no-op'ing.
"""
from __future__ import annotations

from app import razorpayx_client as rzpx


class RazorpayXPayoutExecutor:
    def create_payout(self, *, idempotency_key: str, amount_paise: int,
                       counterparty: str, purpose: str,
                       payout_instruction: dict | None = None) -> dict:
        if not payout_instruction:
            raise ValueError(
                f"ledger row {counterparty!r} carries no payout instruction -- "
                f"it needs fund_account_type plus either an IFSC and account "
                f"number, or a VPA (see schema.sql's Tally payout columns). "
                f"A row ingested before those columns existed will look like this."
            )

        # Raw key, not pre-hashed: create_composite_payout() derives the
        # wire header from this plus the body it assembles, so a rebuilt
        # local DB replaying 'attempt1' can't collide with a differently
        # shaped request RazorpayX already saw under that key.
        result = rzpx.create_composite_payout(
            idempotency_key=idempotency_key,
            instruction=payout_instruction,
            amount_paise=amount_paise,
            reference_id=idempotency_key,
            narration="Reconcile Recover retry",
            queue_if_low_balance=True,
        )
        return {"gateway_payout_id": result["id"], "status": result["status"]}


class RazorpayXPayoutStatusFetcher:
    """Reads a payout's current status straight from RazorpayX, for
    router.sync_payout_statuses(). Kept separate from the executor because
    it is a read: it dispatches nothing and can never move money, so there is
    no reason for it to share the executor's write-shaped interface."""

    def fetch_payout_status(self, gateway_payout_id: str) -> str:
        return rzpx.fetch_payout(gateway_payout_id)["status"]
