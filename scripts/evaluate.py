"""Scores the reconciliation pipeline on a dataset it has never seen.

    python -m scripts.evaluate                 # seed offset 0 -- the fixture the rules were written from
    python -m scripts.evaluate --seed 7        # held out: same generators, different RNG stream
    python -m scripts.evaluate --seed 7 --json report.json

The point of --seed is generalisation. Every rule in classify.py was written
while looking at the seed-0 fixture, so scoring against seed 0 measures nothing
except that the rules still do what their author intended. A different offset
produces different amounts, timings, vendors and case orderings from the same
generators, which is the closest thing to unseen data a synthetic corpus can
offer -- and it is the honest number to quote.

IMPORT ORDER IS LOAD-BEARING. app/fixtures.py computes SEED, BANK_SEED,
SPLIT_BATCH_SEED and ANCHOR at module scope from the environment (fixtures.py:50-63),
so the env has to be set before that module is first imported anywhere. Hence
the deferred imports inside main() rather than at the top of this file: a
top-level `from app.fixtures import ...` would freeze the default seed before
--seed was ever read, and the script would silently score seed 0 while printing
whatever number was asked for. That failure is invisible in the output, which
is exactly why it is worth the ugliness.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path




def _score_ai_column(
    *, ledger_rows, gateway_rows, truth, predicted, amounts, limit,
):
    """Runs the LLM over the same subjects and returns its report, or None.

    Split out of main() because it owns a policy main() does not: which subjects
    to sample under a quota, when to stop at a wall, and when a column is too
    incomplete to be worth printing at all. Leaving it inline made main() a
    ~195-line function holding both CLI orchestration and that policy at one
    indent level, so changing either meant re-reading both.

    Imports are local for the same reason main()'s are -- see the module
    docstring on why app.fixtures must not be imported before the seed env is set.
    """
    from app.ai_classifier import ABSTAIN, active_model, propose
    from app.evaluation import CLEAN, format_report, score, stratified_sample

    # The LLM sees the same subjects the rules did, described with the
    # fields classify.py never reads -- narration, both counterparty
    # spellings, the raw refs. Sending only what the rules use would
    # guarantee it could do no better, which would make the comparison
    # meaningless in the other direction.
    by_ref: dict[str, dict] = {r["external_ref"]: r for r in ledger_rows + gateway_rows}
    rule_by_subject = dict(predicted)

    # Indexed once rather than rescanned per subject. The counterpart lookup
    # below was a linear scan over gateway_rows inside the per-subject loop
    # -- O(n*m), which is 27x27 on the stress set and grows with any larger
    # held-out fixture this script is pointed at. by_ref two lines up
    # already does the same trick for the subject itself.
    by_reference_id: dict[str, list[dict]] = {}
    by_counterparty: dict[str, list[dict]] = {}
    for g in gateway_rows:
        if g.get("reference_id"):
            by_reference_id.setdefault(g["reference_id"], []).append(g)
        if g.get("counterparty"):
            by_counterparty.setdefault(g["counterparty"], []).append(g)

    subjects = stratified_sample(truth, limit)
    if len(subjects) < len(truth):
        print(f"\nLLM sampling {len(subjects)} of {len(truth)} subjects, "
              f"stratified across {len(set(truth[s] for s in subjects))} causes "
              f"(free-tier quota is 20 calls/day).")

    ai_predicted: dict[str, str] = {}
    fallbacks = 0
    ai_started = time.perf_counter()

    for subject in subjects:
        row = by_ref.get(subject, {})
        # The counterpart is whichever side shares this row's reference id.
        candidates = (
            by_reference_id.get(row.get("reference_id") or "", [])
            + by_counterparty.get(row.get("counterparty") or "", [])
        )
        other = next((g for g in candidates if g["external_ref"] != subject), {})
        context = {
            "ledger_reference": row.get("reference_id") or row.get("external_ref"),
            "gateway_reference": other.get("reference_id") or other.get("external_ref"),
            "amount_paise": row.get("amount_paise"),
            "counterparty_ledger": row.get("counterparty"),
            "counterparty_other": other.get("counterparty"),
            "narration": row.get("narration") or other.get("narration"),
        }
        rule_cause = rule_by_subject.get(subject, CLEAN)
        proposal = propose(context, rule_cause=rule_cause)

        # Checked before anything is recorded. A daily quota is not a
        # transient blip and no retry ladder gets past it, so stop at the
        # wall rather than grinding the remaining rows through it -- and
        # do not count this row, which was never scored.
        if proposal.fell_back and "429" in proposal.error:
            print(f"\nstopped after {len(ai_predicted)} of {len(subjects)} rows: "
                  f"provider quota exhausted ({proposal.error}).")
            break

        if proposal.fell_back:
            fallbacks += 1
        # "matched" is the model's way of saying "no exception belongs
        # here", which is what CLEAN means in the truth vocabulary.
        ai_predicted[subject] = CLEAN if proposal.cause == "matched" else proposal.cause

    ai_elapsed = time.perf_counter() - ai_started
    answered = len(ai_predicted) - fallbacks

    if answered <= 0:
        # Every row fell back, so this column would be the rules column
        # copied -- identical numbers that look like a finding and are an
        # artifact. Refusing to print it is the whole point: a scored
        # comparison nobody can distinguish from a no-op is worse than no
        # comparison at all.
        print(f"\nLLM ({active_model()}): no column produced. "
              f"All {len(ai_predicted)} attempted rows fell back to the rules "
              f"-- scoring them would just reprint the rules above.")
        return None
    else:
        ai_report = score(
            {s: truth[s] for s in ai_predicted}, ai_predicted,
            classifier=f"LLM ({active_model()})",
            amounts=amounts,
            elapsed_seconds=ai_elapsed,
            abstain_marker=ABSTAIN,
        )
        print()
        print(format_report(ai_report))
        if len(ai_predicted) < len(truth):
            # Said out loud every time, because two accuracy figures
            # printed one above the other invite being read as a
            # head-to-head even when they were measured on different rows.
            print(f"\nNOT comparable with the rules column above: the model was scored on "
                  f"{len(ai_predicted)} subjects, the rules on {len(truth)}.")
        if fallbacks:
            # Stated plainly: these rows are the rules' answers, not the
            # model's, and folding them in silently would inflate it
            # toward the rules' score.
            print(f"\n{fallbacks} of {len(ai_predicted)} scored rows fell back to the "
                  f"rules and carry the rules' answer, not the model's.")
        return ai_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seed", type=int, default=0,
        help="RR_FIXTURE_SEED_OFFSET. 0 is the fixture the rules were authored against; "
             "anything else is held out.",
    )
    parser.add_argument(
        "--anchor", default=None,
        help="RR_FIXTURE_ANCHOR, ISO 8601. Age buckets are measured against now, so a "
             "dataset anchored weeks back lands entirely in the oldest bucket.",
    )
    parser.add_argument("--json", dest="json_path", default=None, help="Also write the report here.")
    parser.add_argument(
        "--ai", action="store_true",
        help="Also run the LLM classifier over the same rows and score it through the identical "
             "path, so neither classifier gets a bespoke metric. Costs one API call per subject.",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Score at most N subjects with the LLM, sampled evenly across true causes. "
             "Exists because the free Gemini tier allows 20 generateContent calls per day and "
             "the stress set has 27 subjects. The rules column is always scored in full -- it "
             "costs nothing -- so the two columns are explicitly NOT comparable when this is set.",
    )
    parser.add_argument(
        "--stress", action="store_true",
        help="Score the adversarial set instead: boundary and language cases the rules were "
             "not written against. This is the number worth quoting -- see app/stress_fixtures.py "
             "for why a reseeded standard fixture is not.",
    )
    args = parser.parse_args()

    # Before any app.fixtures import. See the module docstring.
    os.environ["RR_FIXTURE_SEED_OFFSET"] = str(args.seed)
    if args.anchor:
        os.environ["RR_FIXTURE_ANCHOR"] = args.anchor

    from app.classify import classify
    from app.evaluation import (
        EvaluationReport,
        format_report,
        predictions_from_exceptions,
        score,
        stratified_sample,
        truth_from_ground_truth,
    )
    from app.fixtures import generate_dataset
    from app.reconcile import reconcile

    if args.stress:
        from app.stress_fixtures import generate_stress_cases
        ledger_rows, gateway_rows, ground_truth = generate_stress_cases()
    else:
        ledger_rows, gateway_rows, ground_truth = generate_dataset()

    truth = truth_from_ground_truth(ground_truth)
    amounts = {row["external_ref"]: row["amount_paise"] for row in ledger_rows}
    for row in gateway_rows:
        amounts.setdefault(row["external_ref"], row["amount_paise"])

    started = time.perf_counter()
    result = reconcile(ledger_rows, gateway_rows)
    exceptions = classify(result)
    elapsed = time.perf_counter() - started

    predicted = predictions_from_exceptions(exceptions, set(truth))
    report: EvaluationReport = score(
        truth, predicted,
        classifier="rules (classify.py)",
        amounts=amounts,
        elapsed_seconds=elapsed,
    )

    if args.stress:
        header = "STRESS SET -- boundary + language cases the rules were not written against"
    elif args.seed != 0:
        header = (f"seed offset {args.seed} -- reseeded, but CASE_PLAN is fixed, so this varies "
                  f"values not structure. NOT a generalisation claim.")
    else:
        header = "seed offset 0 -- the fixture the rules were AUTHORED AGAINST"
    print(header)
    print(f"rows        {len(ledger_rows)} ledger + {len(gateway_rows)} gateway")
    print()
    print(format_report(report))

    reports = [report]

    if args.ai:
        ai_report = _score_ai_column(
            ledger_rows=ledger_rows,
            gateway_rows=gateway_rows,
            truth=truth,
            predicted=predicted,
            amounts=amounts,
            limit=args.limit,
        )
        if ai_report is not None:
            reports.append(ai_report)

    if args.json_path:
        payload = {
            "seed_offset": args.seed,
            "anchor": ground_truth.get("anchor"),
            "ledger_rows": len(ledger_rows),
            "gateway_rows": len(gateway_rows),
            "reports": [r.as_dict() for r in reports],
        }
        Path(args.json_path).write_text(json.dumps(payload, indent=2))
        print(f"\nwrote {args.json_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
