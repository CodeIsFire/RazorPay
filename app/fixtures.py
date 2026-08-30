"""Deterministic synthetic data for the reconciliation engine.

generate_dataset() produces two lists of row-dicts shaped exactly like the
`transactions` table (source='ledger' vs source='gateway'), plus a
ground_truth structure describing what SHOULD happen to each case. That
ground truth is the whole point: M2 and M3 assert against it directly
instead of eyeballing counts, so "the matcher looks right" becomes "the
matcher provably matches N cases and misses exactly these M for these
reasons."

Six case types, matching all five exception causes from the architecture
doc plus the trivial exact-match case:

  exact_match     ledger + gateway agree on reference_id, amount, timing
  fee_mismatch    same reference_id, gateway amount = ledger amount - fee
  duplicate       one ledger entry, two gateway entries (a retried payout)
  timing_lag      same reference_id + amount, gateway far outside the
                  matcher's time window (simulates T+3 "deemed success")
  failed_payment  ledger entry with no gateway counterpart at all
  unexplained     gateway entry with no ledger counterpart at all -- the
                  mirror image of failed_payment; a stray transaction the
                  ledger never expected (see app/classify.py's CAUSES)

Everything is seeded and anchored to a fixed date, so re-running this
produces byte-identical output — required for the tests to stay meaningful.

generate_bank_statement(ledger_rows) is the equivalent generator for a
third source (source='bank_statement'), reconciled against the same
ledger rows this function produces -- see its own docstring below.

generate_split_and_batch_cases() covers split-payment and batch-settlement
matching -- see its own docstring for why it's a separate, self-contained
dataset rather than more CASE_PLAN entries here.
"""
import hashlib
import json
import os
import random
from datetime import datetime, timedelta, timezone

# Both knobs below default to the historical values, so with no environment set
# every generator still emits byte-identical output and the tests that assert
# exact case counts keep meaning what they meant. They exist so a demo database
# can be reseeded with a genuinely different dataset without editing source.
#
# One offset shifts every RNG stream together. The four seeds stay distinct
# constants rather than one shared value because each generator is deliberately
# independent of the others' streams -- see generate_bank_statement's docstring
# on why call order must never matter.
_SEED_OFFSET = int(os.getenv("RR_FIXTURE_SEED_OFFSET", "0"))
SEED = 42 + _SEED_OFFSET
BANK_SEED = 4242 + _SEED_OFFSET
SPLIT_BATCH_SEED = 9001 + _SEED_OFFSET
SETTLED_BANK_SEED = 777 + _SEED_OFFSET

# Every timestamp in the dataset hangs off this. Worth overriding when reseeding:
# the exception age buckets are measured against "now", so a dataset anchored
# weeks in the past lands entirely in "over 7d" and the age breakdown stops
# saying anything. ISO 8601, e.g. RR_FIXTURE_ANCHOR=2026-08-24T09:00:00+00:00
ANCHOR = datetime.fromisoformat(
    os.getenv("RR_FIXTURE_ANCHOR", "2026-08-10T09:00:00+00:00")
)

VENDORS = [
    "Acme Traders", "Bharat Logistics", "Crimson Supplies", "Delta Freight",
    "Everest Textiles", "Falcon Components", "Ganges Packaging",
    "Horizon Electricals", "Indus Hardware", "Jupiter Chemicals",
    "Konkan Foods", "Lotus Stationery", "Malabar Spices", "Nilgiri Tea Co",
    "Orion Fabrics", "Pallavi Prints", "Quartz Metals", "Ridgeline Tools",
    "Sundar Furnishings", "Tricolor Paints",
]

# ---------------------------------------------------------------------------
# Vendor payout profiles -- the fund account + contact half of RazorpayX's
# Tally batch-payout template.
#
# Keyed off the vendor NAME rather than a position in VENDORS, so vendors
# this module invents outside that list (generate_split_and_batch_cases()'s
# "Split Batch Vendor") get a stable profile too, and so adding a vendor
# never renumbers anybody else's bank details.
#
# The RNG is seeded from a SHA-256 of the name, not from the module's main
# SEED stream and never from hash() (which is salted per interpreter run).
# Two consequences, both deliberate: profiles are byte-identical across runs
# and machines, and drawing them consumes none of generate_dataset()'s own
# random stream -- so adding this data left every existing ledger id, amount
# and timestamp exactly where it was.
# ---------------------------------------------------------------------------

PROFILE_SEED = 9001

# REAL IFSC codes, every one verified to resolve against Razorpay's own
# directory (https://ifsc.razorpay.com/<ifsc>). This matters beyond realism:
# RazorpayX validates the IFSC when a fund account is created, so a
# format-valid-but-invented code (the earlier `PREFIX + random digits`
# approach) got a 404 from that directory and would be rejected outright by
# the composite payout in app/live_executor.py. Fixture bank details have to
# be *dispatchable*, not merely well-shaped.
#
# Account numbers below stay synthetic -- those aren't validated against the
# bank, only the IFSC is.
IFSC_CODES = [
    "SBIN0007105", "HDFC0000053", "ICIC0000001", "UTIB0000005",
    "KKBK0000958", "PUNB0234500", "BARB0VJKARN", "CNRB0000123",
    "IDFB0040101", "YESB0000001", "SBIN0000300", "HDFC0000240",
    "ICIC0000104", "UTIB0000009", "KKBK0000261", "IDIB000M082",
    "MAHB0000001",
]

UPI_HANDLES = ["okhdfcbank", "okaxis", "ybl", "paytm", "ibl"]

# (city, state, PIN prefix) triples that actually agree with each other --
# a Bangalore address with a Maharashtra PIN would be the kind of detail
# that makes synthetic data obviously synthetic.
CITIES = [
    ("Bengaluru", "Karnataka", "5600"),
    ("Mumbai", "Maharashtra", "4000"),
    ("Pune", "Maharashtra", "4110"),
    ("Chennai", "Tamil Nadu", "6000"),
    ("Coimbatore", "Tamil Nadu", "6410"),
    ("Hyderabad", "Telangana", "5000"),
    ("Ahmedabad", "Gujarat", "3800"),
    ("Surat", "Gujarat", "3950"),
    ("Jaipur", "Rajasthan", "3020"),
    ("Kolkata", "West Bengal", "7000"),
    ("Kochi", "Kerala", "6820"),
    ("Indore", "Madhya Pradesh", "4520"),
    ("Lucknow", "Uttar Pradesh", "2260"),
    ("Ludhiana", "Punjab", "1410"),
    ("New Delhi", "Delhi", "1100"),
]

STREETS = [
    "Industrial Estate", "Trade Centre", "Commerce House", "Market Road",
    "Export Park", "Business Bay", "Warehouse Lane", "Godown Road",
]

# Ledger transaction_type -> the template's payout purpose. Our ledger rows
# are vendor disbursements, so 'vendor bill' is the honest default; the other
# purposes appear only if the ledger ever carries those transaction types.
PURPOSE_BY_TXN_TYPE = {
    "payment": "vendor bill",
    "refund": "refund",
    "chargeback": "refund",
}

_PROFILE_CACHE: dict[str, dict] = {}


def _slug(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _profile_rng(vendor: str) -> random.Random:
    digest = hashlib.sha256(f"{PROFILE_SEED}:{vendor}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def vendor_profile(vendor: str) -> dict:
    """The fund account + contact fields for one vendor, stable forever for
    a given name. A deterministic minority of vendors are paid to a UPI VPA
    rather than a bank account, so both branches of the template are exercised --
    and the two are mutually exclusive: a 'vpa' profile carries no IFSC or
    account number, and a 'bank_account' profile carries no VPA."""
    cached = _PROFILE_CACHE.get(vendor)
    if cached is not None:
        return cached

    rng = _profile_rng(vendor)
    slug = _slug(vendor)
    city, state, pin_prefix = rng.choice(CITIES)

    is_vpa = rng.random() < 0.2
    if is_vpa:
        fund_account = {
            "fund_account_type": "vpa",
            "fund_account_ifsc": None,
            "fund_account_number": None,
            "fund_account_vpa": f"{slug}@{rng.choice(UPI_HANDLES)}",
            # A VPA fund account can only be paid over UPI.
            "payout_mode": "UPI",
        }
    else:
        fund_account = {
            "fund_account_type": "bank_account",
            "fund_account_ifsc": rng.choice(IFSC_CODES),
            "fund_account_number": "".join(rng.choice("0123456789") for _ in range(rng.randint(11, 16))),
            "fund_account_vpa": None,
            # NEFT/IMPS only: RTGS has a 2,00,000 rupee floor and every
            # amount this module generates is well under it.
            "payout_mode": rng.choice(["NEFT", "IMPS"]),
        }

    profile = {
        **fund_account,
        "fund_account_name": vendor,
        # Every counterparty here is somebody we pay for goods or services.
        "contact_type": "vendor",
        "contact_email": f"accounts@{slug}.co.in",
        "contact_mobile": "".join(["9"] + [rng.choice("0123456789") for _ in range(9)]),
        "contact_address": f"{rng.randint(1, 240)} {rng.choice(STREETS)}",
        "contact_city": city,
        "contact_state": state,
        "contact_zipcode": pin_prefix + f"{rng.randint(1, 99):02d}",
    }
    _PROFILE_CACHE[vendor] = profile
    return profile


def payout_fields(vendor: str, ledger_ref: str, transaction_type: str = "payment") -> dict:
    """The full 15-field payout half of a ledger row: the vendor's stable
    profile plus the two fields that belong to this particular payout."""
    return {
        **vendor_profile(vendor),
        "payout_purpose": PURPOSE_BY_TXN_TYPE.get(transaction_type, "payout"),
        "notes": f"Tally voucher {ledger_ref}",
    }

CASE_PLAN = [
    ("exact_match", 30),
    ("fee_mismatch", 9),
    ("duplicate", 6),
    ("timing_lag", 6),
    ("failed_payment", 9),
    ("unexplained", 3),
]


def _rupees_to_paise(rupees: float) -> int:
    return int(round(rupees * 100))


def _fake_payout_id(rng: random.Random) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "pout_" + "".join(rng.choice(alphabet) for _ in range(14))


def _fake_utr(rng: random.Random) -> str:
    return "".join(rng.choice("0123456789") for _ in range(12))


def _fake_bank_txn_id(rng: random.Random) -> str:
    return "BANKTXN" + "".join(rng.choice("0123456789") for _ in range(10))


# ---------------------------------------------------------------------------
# Bank statement -- a third, independent view of the SAME ledger payouts
# generate_dataset() produced (the actual bank account RazorpayX debits,
# not RazorpayX's own API record). Deliberately a different case mix from
# the gateway fixture: a bank statement's realistic failure modes are
# settlement-file lag and the bank's own debit charges, not duplicate
# payouts -- plus a couple of cases the gateway fixture has no equivalent
# for at all (refund_unmatched/chargeback), to exercise the two causes
# app/classify.py only ever produces from a non-'payment' transaction_type.
# ---------------------------------------------------------------------------


# One entry per ledger row -- must sum to exactly len(ledger_rows) (60).
BANK_CASE_PLAN = [
    ("exact_match", 42),
    ("timing_lag", 8),
    ("fee_mismatch", 5),
    ("failed_payment", 5),
]

# Bank-only rows with no fresh ledger row of their own: unexplained is a
# stray debit (e.g. a bank service charge); refund_unmatched/chargeback
# each reference an ledger_ref that already cleared (an exact_match case),
# simulating a reversal the bank sees before the ledger is updated.
BANK_EXTRA_PLAN = [
    ("unexplained", 2),
    ("refund_unmatched", 3),
    ("chargeback", 2),
]


def generate_bank_statement(ledger_rows: list[dict]):
    """Returns (bank_rows, ground_truth) -- synthetic source='bank_statement'
    transactions rows reconciled against `ledger_rows` (generate_dataset()'s
    output), plus a ground truth in the same shape/spirit as
    generate_dataset()'s so app/classify.py's bank_statement path can be
    asserted against directly. Deterministic under BANK_SEED, independent
    of generate_dataset()'s own RNG stream so call order never matters."""
    rng = random.Random(BANK_SEED)

    plan_types = []
    for case_type, count in BANK_CASE_PLAN:
        plan_types.extend([case_type] * count)
    if len(plan_types) != len(ledger_rows):
        raise ValueError(
            f"BANK_CASE_PLAN covers {len(plan_types)} rows, but "
            f"ledger_rows has {len(ledger_rows)} -- keep them in sync."
        )
    rng.shuffle(plan_types)  # ledger position shouldn't predict bank fate

    bank_rows = []
    cases = []
    exact_match_refs = []

    for led, case_type in zip(ledger_rows, plan_types):
        ledger_id = led["external_ref"]
        vendor = led["counterparty"]
        amount_paise = led["amount_paise"]
        expected_at = datetime.fromisoformat(led["occurred_at"])
        case = {"case_type": case_type, "ledger_ref": ledger_id, "bank_refs": []}

        if case_type == "exact_match":
            bank_id = _fake_bank_txn_id(rng)
            bank_time = expected_at + timedelta(days=rng.randint(1, 2))
            bank_rows.append({
                "source": "bank_statement", "external_ref": bank_id,
                "reference_id": ledger_id, "amount_paise": amount_paise,
                "currency": "INR", "counterparty": vendor,
                "narration": f"NEFT DR {vendor}", "occurred_at": bank_time.isoformat(),
                "raw_json": json.dumps({"kind": "bank_statement_line", "utr": _fake_utr(rng)}),
            })
            case["bank_refs"] = [bank_id]
            exact_match_refs.append(ledger_id)

        elif case_type == "timing_lag":
            # Settlement-file lag: the bank's own batch cycle runs days
            # behind RazorpayX's real-time API status, further out than
            # the exact_match case above on purpose.
            bank_id = _fake_bank_txn_id(rng)
            bank_time = expected_at + timedelta(days=rng.randint(4, 6))
            bank_rows.append({
                "source": "bank_statement", "external_ref": bank_id,
                "reference_id": ledger_id, "amount_paise": amount_paise,
                "currency": "INR", "counterparty": vendor,
                "narration": f"NEFT DR {vendor}", "occurred_at": bank_time.isoformat(),
                "raw_json": json.dumps({"kind": "bank_statement_line", "utr": _fake_utr(rng)}),
            })
            case["bank_refs"] = [bank_id]

        elif case_type == "fee_mismatch":
            # A bank debit charge, not a RazorpayX platform fee -- bigger
            # than KNOWN_FEE_TOLERANCE_PAISE's RazorpayX-specific range on
            # purpose, so it can never be waved through by the (gateway-only)
            # auto-resolve path; see classify.py.
            bank_id = _fake_bank_txn_id(rng)
            bank_time = expected_at + timedelta(days=rng.randint(1, 2))
            bank_charge_paise = _rupees_to_paise(rng.choice([15, 25, 40]))
            bank_rows.append({
                "source": "bank_statement", "external_ref": bank_id,
                "reference_id": ledger_id, "amount_paise": amount_paise - bank_charge_paise,
                "currency": "INR", "counterparty": vendor,
                "narration": f"NEFT DR {vendor} (incl. bank charges)",
                "occurred_at": bank_time.isoformat(),
                "raw_json": json.dumps({"kind": "bank_statement_line", "bank_charge_paise": bank_charge_paise}),
            })
            case["bank_refs"] = [bank_id]
            case["bank_charge_paise"] = bank_charge_paise

        elif case_type == "failed_payment":
            pass  # bank hasn't reflected this debit yet

        cases.append(case)

    extra_seq = 0
    for case_type, count in BANK_EXTRA_PLAN:
        for _ in range(count):
            extra_seq += 1
            bank_id = _fake_bank_txn_id(rng)
            bank_time = ANCHOR + timedelta(days=rng.randint(10, 20))

            if case_type == "unexplained":
                bank_rows.append({
                    "source": "bank_statement", "external_ref": bank_id,
                    "reference_id": f"BANK-ORPHAN-{extra_seq:02d}",
                    "amount_paise": _rupees_to_paise(rng.randint(50, 500)),
                    "currency": "INR", "counterparty": "Bank",
                    "narration": "Bank service charge", "occurred_at": bank_time.isoformat(),
                    "raw_json": json.dumps({"kind": "bank_statement_line", "note": "bank charge"}),
                })
                cases.append({"case_type": "unexplained", "ledger_ref": None, "bank_refs": [bank_id]})
            else:
                # refund_unmatched / chargeback: reverses an already-cleared
                # exact_match ledger row, deterministically picked from what
                # we've seen so far.
                original = exact_match_refs[extra_seq % len(exact_match_refs)]
                bank_rows.append({
                    "source": "bank_statement", "external_ref": bank_id,
                    "reference_id": None,
                    "amount_paise": _rupees_to_paise(rng.randint(1_000, 20_000)),
                    "currency": "INR", "counterparty": "Bank",
                    "transaction_type": "refund" if case_type == "refund_unmatched" else "chargeback",
                    "original_ref": original,
                    "narration": f"{'Reversal' if case_type == 'refund_unmatched' else 'Chargeback'} re {original}",
                    "occurred_at": bank_time.isoformat(),
                    "raw_json": json.dumps({"kind": "bank_statement_line", "original_ref": original}),
                })
                cases.append({"case_type": case_type, "ledger_ref": original, "bank_refs": [bank_id]})

    ground_truth = {
        "seed": BANK_SEED,
        "summary": {**dict(BANK_CASE_PLAN), **dict(BANK_EXTRA_PLAN)},
        "cases": cases,
        "totals": {"bank_rows": len(bank_rows)},
    }
    return bank_rows, ground_truth


def generate_dataset():
    rng = random.Random(SEED)
    ledger_rows = []
    gateway_rows = []
    cases = []

    seq = 0
    vendor_cursor = 0
    time_cursor = ANCHOR

    for case_type, count in CASE_PLAN:
        for _ in range(count):
            seq += 1
            ledger_id = f"LED-{seq:04d}"
            vendor = VENDORS[vendor_cursor % len(VENDORS)]
            vendor_cursor += 1
            amount_rupees = rng.randint(1_000, 50_000)
            amount_paise = _rupees_to_paise(amount_rupees)
            expected_at = time_cursor
            time_cursor += timedelta(hours=rng.randint(2, 6))

            if case_type != "unexplained":
                ledger_rows.append({
                    "source": "ledger",
                    "external_ref": ledger_id,
                    "reference_id": ledger_id,
                    "amount_paise": amount_paise,
                    "currency": "INR",
                    "counterparty": vendor,
                    "narration": f"Payout to {vendor}",
                    "occurred_at": expected_at.isoformat(),
                    **payout_fields(vendor, ledger_id),
                    "raw_json": json.dumps({
                        "kind": "ledger_entry",
                        "order_id": ledger_id,
                        "vendor": vendor,
                        "expected_amount_inr": amount_rupees,
                    }),
                })

            case = {
                "case_type": case_type,
                "ledger_ref": ledger_id if case_type != "unexplained" else None,
                "gateway_refs": [],
            }

            if case_type == "exact_match":
                gw_id = _fake_payout_id(rng)
                gw_time = expected_at + timedelta(minutes=rng.randint(5, 90))
                gateway_rows.append({
                    "source": "gateway",
                    "external_ref": gw_id,
                    "reference_id": ledger_id,
                    "amount_paise": amount_paise,
                    "currency": "INR",
                    "counterparty": vendor,
                    "narration": f"Payout to {vendor}",
                    "occurred_at": gw_time.isoformat(),
                    "raw_json": json.dumps({
                        "kind": "razorpayx_transaction", "id": gw_id,
                        "reference_id": ledger_id, "status": "processed",
                        "utr": _fake_utr(rng), "fees": 0, "tax": 0,
                    }),
                })
                case["gateway_refs"] = [gw_id]

            elif case_type == "fee_mismatch":
                gw_id = _fake_payout_id(rng)
                gw_time = expected_at + timedelta(minutes=rng.randint(5, 90))
                fee_paise = _rupees_to_paise(rng.choice([2, 3, 5, 9, 10]))
                net_amount = amount_paise - fee_paise
                gateway_rows.append({
                    "source": "gateway",
                    "external_ref": gw_id,
                    "reference_id": ledger_id,
                    "amount_paise": net_amount,
                    "currency": "INR",
                    "counterparty": vendor,
                    "narration": f"Payout to {vendor}",
                    "occurred_at": gw_time.isoformat(),
                    "raw_json": json.dumps({
                        "kind": "razorpayx_transaction", "id": gw_id,
                        "reference_id": ledger_id, "status": "processed",
                        "utr": _fake_utr(rng), "fees": fee_paise, "tax": 0,
                    }),
                })
                case["gateway_refs"] = [gw_id]
                case["fee_paise"] = fee_paise

            elif case_type == "duplicate":
                refs = []
                for _ in range(2):
                    gw_id = _fake_payout_id(rng)
                    gw_time = expected_at + timedelta(minutes=rng.randint(5, 90))
                    gateway_rows.append({
                        "source": "gateway",
                        "external_ref": gw_id,
                        "reference_id": ledger_id,
                        "amount_paise": amount_paise,
                        "currency": "INR",
                        "counterparty": vendor,
                        "narration": f"Payout to {vendor}",
                        "occurred_at": gw_time.isoformat(),
                        "raw_json": json.dumps({
                            "kind": "razorpayx_transaction", "id": gw_id,
                            "reference_id": ledger_id, "status": "processed",
                            "utr": _fake_utr(rng), "fees": 0, "tax": 0,
                        }),
                    })
                    refs.append(gw_id)
                case["gateway_refs"] = refs

            elif case_type == "timing_lag":
                gw_id = _fake_payout_id(rng)
                gw_time = expected_at + timedelta(days=rng.randint(5, 8))
                gateway_rows.append({
                    "source": "gateway",
                    "external_ref": gw_id,
                    "reference_id": ledger_id,
                    "amount_paise": amount_paise,
                    "currency": "INR",
                    "counterparty": vendor,
                    "narration": f"Payout to {vendor}",
                    "occurred_at": gw_time.isoformat(),
                    "raw_json": json.dumps({
                        "kind": "razorpayx_transaction", "id": gw_id,
                        "reference_id": ledger_id, "status": "processed",
                        "utr": _fake_utr(rng), "fees": 0, "tax": 0,
                    }),
                })
                case["gateway_refs"] = [gw_id]

            elif case_type == "failed_payment":
                pass  # ledger row stands alone, no gateway counterpart

            elif case_type == "unexplained":
                # No ledger row at all -- a stray gateway transaction whose
                # reference_id correlates to nothing this ledger ever
                # expected. Uses its own reference namespace so it can never
                # collide with a real LED-#### ref.
                orphan_ref = f"ORPHAN-{seq:04d}"
                gw_id = _fake_payout_id(rng)
                gw_time = expected_at + timedelta(minutes=rng.randint(5, 90))
                gateway_rows.append({
                    "source": "gateway",
                    "external_ref": gw_id,
                    "reference_id": orphan_ref,
                    "amount_paise": amount_paise,
                    "currency": "INR",
                    "counterparty": vendor,
                    "narration": f"Payout to {vendor}",
                    "occurred_at": gw_time.isoformat(),
                    "raw_json": json.dumps({
                        "kind": "razorpayx_transaction", "id": gw_id,
                        "reference_id": orphan_ref, "status": "processed",
                        "utr": _fake_utr(rng), "fees": 0, "tax": 0,
                    }),
                })
                case["gateway_refs"] = [gw_id]

            cases.append(case)

    summary = {}
    for case_type, count in CASE_PLAN:
        summary[case_type] = count

    ground_truth = {
        "seed": SEED,
        "anchor": ANCHOR.isoformat(),
        "summary": summary,
        "cases": cases,
        "totals": {
            "ledger_rows": len(ledger_rows),
            "gateway_rows": len(gateway_rows),
        },
    }
    return ledger_rows, gateway_rows, ground_truth


# ---------------------------------------------------------------------------
# Split-payment / batch-settlement matching -- a small, self-contained
# dataset kept deliberately separate from generate_dataset()'s 60-row
# fixture (whose exact per-cause counts are asserted across several other
# test files) rather than folded into CASE_PLAN, so this can never perturb
# those. Four cases, one of each shape reconcile.py's split/batch passes
# recognize:
#
#   split_complete   1 ledger row, disbursed across 3 gateway rows that
#                    fully sum to it -> reconcile.py's split_matches
#   split_partial    1 ledger row, 2 of an eventual 3 pieces present so
#                    far, short of the full amount -> partial_groups
#   batch_complete   2 ledger rows sharing a settlement_batch_id, fully
#                    settled by 1 gateway row -> batch_matches
#   batch_partial    2 ledger rows sharing a settlement_batch_id, only
#                    one's share reflected so far -> partial_groups
# ---------------------------------------------------------------------------



def generate_split_and_batch_cases():
    """Returns (ledger_rows, gateway_rows, ground_truth) -- see the module
    docstring and the comment block above for why this is separate from
    generate_dataset()."""
    rng = random.Random(SPLIT_BATCH_SEED)
    ledger_rows = []
    gateway_rows = []
    cases = []

    def _ledger(ref, amount, batch_id=None, vendor="Split Batch Vendor"):
        return {
            "source": "ledger", "external_ref": ref, "reference_id": ref,
            "amount_paise": amount, "currency": "INR", "counterparty": vendor,
            "narration": f"Payout to {vendor}", "occurred_at": ANCHOR.isoformat(),
            **payout_fields(vendor, ref),
            "raw_json": json.dumps({"kind": "ledger_entry", "order_id": ref}),
            "settlement_batch_id": batch_id,
        }

    def _gateway(ref, amount, reference_id, minutes_after=30, vendor="Split Batch Vendor"):
        gw_time = ANCHOR + timedelta(minutes=minutes_after)
        return {
            "source": "gateway", "external_ref": ref, "reference_id": reference_id,
            "amount_paise": amount, "currency": "INR", "counterparty": vendor,
            "narration": f"Payout to {vendor}", "occurred_at": gw_time.isoformat(),
            "raw_json": json.dumps({
                "kind": "razorpayx_transaction", "id": ref, "reference_id": reference_id,
                "status": "processed", "utr": _fake_utr(rng), "fees": 0, "tax": 0,
            }),
        }

    # split_complete: 90,000 disbursed as 50k + 30k + 10k.
    ledger_rows.append(_ledger("SPLIT-001", 90_000))
    gateway_rows += [
        _gateway("SG-001", 50_000, "SPLIT-001", minutes_after=20),
        _gateway("SG-002", 30_000, "SPLIT-001", minutes_after=40),
        _gateway("SG-003", 10_000, "SPLIT-001", minutes_after=60),
    ]
    cases.append({"case_type": "split_complete", "ledger_ref": "SPLIT-001",
                  "gateway_refs": ["SG-001", "SG-002", "SG-003"]})

    # split_partial: 90,000 expected, only 40k + 20k = 60k has arrived.
    ledger_rows.append(_ledger("SPLIT-002", 90_000))
    gateway_rows += [
        _gateway("SG-004", 40_000, "SPLIT-002", minutes_after=20),
        _gateway("SG-005", 20_000, "SPLIT-002", minutes_after=40),
    ]
    cases.append({"case_type": "split_partial", "ledger_ref": "SPLIT-002",
                  "gateway_refs": ["SG-004", "SG-005"]})

    # batch_complete: two ledger rows (55k + 45k) settled by one 100k gateway row.
    ledger_rows += [
        _ledger("BATCH-001-A", 55_000, batch_id="SETTLE-BATCH-001"),
        _ledger("BATCH-001-B", 45_000, batch_id="SETTLE-BATCH-001"),
    ]
    gateway_rows.append(_gateway("SG-006", 100_000, "SETTLE-BATCH-001", minutes_after=45))
    cases.append({"case_type": "batch_complete", "ledger_ref": "BATCH-001-A,BATCH-001-B",
                  "gateway_refs": ["SG-006"]})

    # batch_partial: two ledger rows (70k + 30k), only the first share (70k) has landed.
    ledger_rows += [
        _ledger("BATCH-002-A", 70_000, batch_id="SETTLE-BATCH-002"),
        _ledger("BATCH-002-B", 30_000, batch_id="SETTLE-BATCH-002"),
    ]
    gateway_rows.append(_gateway("SG-007", 70_000, "SETTLE-BATCH-002", minutes_after=45))
    cases.append({"case_type": "batch_partial", "ledger_ref": "BATCH-002-A,BATCH-002-B",
                  "gateway_refs": ["SG-007"]})

    ground_truth = {
        "seed": SPLIT_BATCH_SEED,
        "anchor": ANCHOR.isoformat(),
        "cases": cases,
        "totals": {"ledger_rows": len(ledger_rows), "gateway_rows": len(gateway_rows)},
    }
    return ledger_rows, gateway_rows, ground_truth


# Bank-statement lines for ledger rows that generate_bank_statement() does not
# cover. That function is deliberately strict -- BANK_CASE_PLAN must account
# for exactly the rows handed to it -- so the split/batch dataset, which is
# generated separately, has no bank-side view at all.
#
# Without this, loading split/batch rows and then reconciling against
# 'bank_statement' reports six failed_payments: the ledger says the money left,
# and the bank statement has no record of it. That reads as a real finding but
# is purely an artefact of the fixture being incomplete on the bank side, and
# it would double the bank pass's failed_payment count with noise.
#
# These are plain settled debits: same reference_id and amount, a day or two
# later, so they reconcile cleanly and leave the split/batch cases to be judged
# on the gateway pass where their actual point lies.


def generate_settled_bank_lines(ledger_rows: list[dict]) -> list[dict]:
    """One clean, exactly-matching bank_statement row per ledger row."""
    rng = random.Random(SETTLED_BANK_SEED)
    rows = []
    for led in ledger_rows:
        vendor = led.get("counterparty") or "Unknown"
        occurred = datetime.fromisoformat(led["occurred_at"]) + timedelta(days=rng.randint(1, 2))
        rows.append({
            "source": "bank_statement",
            "external_ref": _fake_bank_txn_id(rng),
            "reference_id": led["external_ref"],
            "amount_paise": led["amount_paise"],
            "currency": "INR",
            "counterparty": vendor,
            "narration": f"NEFT DR {vendor}",
            "occurred_at": occurred.isoformat(),
            "raw_json": json.dumps({"kind": "bank_statement_line", "utr": _fake_utr(rng)}),
        })
    return rows
