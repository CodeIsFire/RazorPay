"""Cases the reconciliation rules were NOT written against.

Why this module exists, stated plainly because the number it produces is the
one worth quoting:

`app/fixtures.py` builds each case to trip exactly one rule -- its fee_mismatch
generator picks a bank charge deliberately "bigger than KNOWN_FEE_TOLERANCE_PAISE"
so the rule that looks for exactly that will fire. The generator and
`app/classify.py` are two encodings of one specification, so the rules score
100% on it. Changing RR_FIXTURE_SEED_OFFSET moves vendor names, amounts and
timing jitter, but CASE_PLAN (fixtures.py:219) is a fixed list of case types and
counts -- the seed varies values, and the rules do not depend on values. 100%
on a reseeded fixture is therefore still 100% by construction, and quoting it as
a generalisation result would be false.

These cases are built from the other direction: from how reconciliation
actually fails in production, without reference to what classify.py checks.
They fall into two groups. The per-cause table in app/evaluation.py already
separates them -- fee_mismatch and timing_lag are the arithmetic ones,
bank_charge and the clean-match misses are the semantic ones -- so the split is
readable from the report without a second scoring axis.

  ARITHMETIC BOUNDARIES (1-4) sit within a paise or a minute of a threshold.
  Deterministic rules should win these outright -- a comparison against a
  constant is precisely what code is good at, and a language model asked to
  decide whether 101 > 100 is being used for the wrong job.

  SEMANTIC CASES (5-7) are ones where the evidence is language. The matcher's
  counterparty fallback is exact string equality (reconcile.py:111), so
  "Acme Traders" and "Acme Traders Pvt Ltd" are different payees to it; a
  reference id with a letter O typed for a zero is a different payment; and a
  bank service-charge line is an unexplained orphan. A human reconciler
  resolves all three in seconds by reading them. No tolerance constant can.

The truth labels for 5-7 are what a human reconciler would say, decided before
any classifier was run against them -- not reverse-engineered from what a model
happened to get right. That distinction is what keeps this a test rather than a
demonstration.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta

from app.config import FUZZY_AMOUNT_TOLERANCE_PAISE, FUZZY_TIME_WINDOW_HOURS

STRESS_SEED = 31337
ANCHOR = datetime.fromisoformat("2026-08-20T09:00:00+00:00")

#: Payee names chosen so the alias case is realistic rather than contrived --
#: the suffix forms below are how the same vendor appears on a bank statement
#: versus an internal ledger.
_VENDOR = "Meridian Textiles"
_VENDOR_LEGAL = "Meridian Textiles Pvt Ltd"


def _row(source, ref, reference_id, amount_paise, counterparty, narration, when, extra=None):
    return {
        "source": source,
        "external_ref": ref,
        "reference_id": reference_id,
        "amount_paise": amount_paise,
        "currency": "INR",
        "counterparty": counterparty,
        "narration": narration,
        "occurred_at": when.isoformat(),
        "raw_json": json.dumps(extra or {}),
    }


def generate_stress_cases() -> tuple[list[dict], list[dict], dict]:
    """(ledger_rows, gateway_rows, ground_truth), same contract as fixtures.py.

    Deterministic: one seed, no wall-clock reads, so a reported score can be
    reproduced exactly by anyone who clones the repo.
    """
    rng = random.Random(STRESS_SEED)
    ledger: list[dict] = []
    gateway: list[dict] = []
    cases: list[dict] = []

    def add(case_type: str, led: dict | None, gws: list[dict], note: str):
        if led is not None:
            ledger.append(led)
        gateway.extend(gws)
        cases.append({
            "case_type": case_type,
            "ledger_ref": led["external_ref"] if led else None,
            "gateway_refs": [g["external_ref"] for g in gws],
            "note": note,
        })

    def amount() -> int:
        return rng.randrange(50_000, 900_000)

    n = 0

    def ids(prefix="STR"):
        nonlocal n
        n += 1
        return f"{prefix}-{n:04d}", f"pout_stress{n:04d}"

    # ---- 1-2. Amount boundary. The rule is `<= FUZZY_AMOUNT_TOLERANCE_PAISE`,
    # so a delta exactly at the tolerance must still match, and one paise past
    # it must not. Off-by-one on an inclusive bound is the classic way a
    # reconciler silently starts raising or swallowing exceptions.
    for delta, case_type, note in (
        (FUZZY_AMOUNT_TOLERANCE_PAISE, "exact_match",
         "delta exactly at the inclusive tolerance -- must still match"),
        (FUZZY_AMOUNT_TOLERANCE_PAISE + 1, "fee_mismatch",
         "one paise past the tolerance -- must not match"),
    ):
        for _ in range(4):
            led_id, gw_id = ids()
            amt = amount()
            when = ANCHOR - timedelta(hours=rng.randint(2, 20))
            led = _row("ledger", led_id, led_id, amt, _VENDOR, f"Payout {led_id}", when)
            gw = _row("gateway", gw_id, led_id, amt - delta, _VENDOR,
                      f"UPI/{led_id}", when + timedelta(minutes=rng.randint(1, 90)),
                      {"kind": "payout", "delta_paise": delta})
            add(case_type, led, [gw], note)

    # ---- 3-4. Time boundary, same reasoning on the other axis.
    for hours, case_type, note in (
        (FUZZY_TIME_WINDOW_HOURS, "exact_match",
         "exactly at the window edge -- must still match"),
        (FUZZY_TIME_WINDOW_HOURS + 1, "timing_lag",
         "one hour past the window -- must not match"),
    ):
        for _ in range(4):
            led_id, gw_id = ids()
            amt = amount()
            when = ANCHOR - timedelta(days=4)
            led = _row("ledger", led_id, led_id, amt, _VENDOR, f"Payout {led_id}", when)
            gw = _row("gateway", gw_id, led_id, amt, _VENDOR, f"NEFT/{led_id}",
                      when + timedelta(hours=hours), {"kind": "payout"})
            add(case_type, led, [gw], note)

    # ---- 5. Counterparty alias. The gateway row carries no reference_id, so
    # the matcher falls back to comparing counterparty strings for equality
    # (reconcile.py:111). The same vendor written with its legal suffix is a
    # different string and therefore, to the rules, a different payee -- the
    # ledger row is reported as a failed payment that never failed.
    for _ in range(4):
        led_id, gw_id = ids()
        amt = amount()
        when = ANCHOR - timedelta(hours=rng.randint(3, 30))
        led = _row("ledger", led_id, led_id, amt, _VENDOR, f"Payout {led_id}", when)
        gw = _row("gateway", gw_id, None, amt, _VENDOR_LEGAL,
                  f"NEFT DR {_VENDOR_LEGAL}", when + timedelta(minutes=rng.randint(5, 200)),
                  {"kind": "payout"})
        add("exact_match", led, [gw],
            "same payee, legal-suffix alias; no reference_id so the match falls to "
            "exact string comparison of counterparty")

    # ---- 6. Reference transcription error. A capital O typed for a zero is
    # the oldest error in bank ops. Byte comparison sees an unrelated id; a
    # reader sees the same payment, and the amount and payee corroborate it.
    for _ in range(4):
        led_id, gw_id = ids()
        amt = amount()
        when = ANCHOR - timedelta(hours=rng.randint(3, 30))
        typo = led_id.replace("0", "O", 1)
        led = _row("ledger", led_id, led_id, amt, _VENDOR, f"Payout {led_id}", when)
        gw = _row("gateway", gw_id, typo, amt, _VENDOR, f"IMPS/{typo}",
                  when + timedelta(minutes=rng.randint(5, 120)), {"kind": "payout"})
        add("exact_match", led, [gw],
            "reference id transcribed with a letter O for a zero; amount and payee "
            "both corroborate that it is the same payment")

    # ---- 7. Bank service charge. An orphan with no ledger side, which the
    # rules can only call `unexplained` -- correct as far as they can see. But
    # it is not an unreconciled payout at all, and its narration says so. The
    # cost of getting this wrong is an operator investigating the bank's own
    # monthly fee as if it were missing money.
    for _ in range(3):
        _, gw_id = ids()
        gw = _row("gateway", gw_id, None, rng.randrange(2_000, 9_000), "HDFC Bank",
                  "Bank service charge - monthly maintenance",
                  ANCHOR - timedelta(days=rng.randint(1, 5)),
                  {"kind": "bank_fee"})
        add("bank_charge", None, [gw],
            "not a payout at all; the narration identifies it as the bank's own fee")

    ground_truth = {
        "seed": STRESS_SEED,
        "anchor": ANCHOR.isoformat(),
        "cases": cases,
        "summary": {ct: sum(1 for c in cases if c["case_type"] == ct)
                    for ct in {c["case_type"] for c in cases}},
        "totals": {"ledger_rows": len(ledger), "gateway_rows": len(gateway)},
    }
    return ledger, gateway, ground_truth

