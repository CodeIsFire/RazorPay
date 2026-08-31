"""Measures the pipeline against the fixture's own ground truth.

Why this exists: `tests/test_classify.py` and `tests/test_reconcile.py` already
compare against `ground_truth["cases"]`, but they assert exact set equality on
the *same* fixture the rules in classify.py were hand-written from. That is
100% accuracy by construction and says nothing about whether the rules
generalise. Quoting it as a score would be the cherry-picked match this
project's own honesty rules exist to prevent.

This module scores the same comparison as *rates* rather than assertions, so it
can be pointed at a dataset the rules have never seen -- `RR_FIXTURE_SEED_OFFSET`
(app/fixtures.py:50) shifts every RNG stream, which is exactly the held-out
mechanism, and it was already built for reseeding demo databases.

It is deliberately classifier-agnostic: `score()` takes predictions as a plain
{subject -> cause} mapping, so the deterministic rules and an LLM can be run
through the identical scoring path and compared without either getting a
home-field advantage from its own bespoke metric code.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The pseudo-cause for "this row reconciled cleanly, no exception is correct
# here". It has to be a real key in the matrix rather than an absence, because
# raising an exception on a row that actually matched is a false positive with
# a real cost -- an operator opens it, reads it, and finds nothing wrong. A
# scorer that only looked at rows carrying exceptions could never see that.
CLEAN = "none"

# Ground-truth case types that mean "no exception should be raised".
_CLEAN_CASE_TYPES = frozenset({"exact_match", "split_complete", "batch_complete"})

# Ground-truth case types whose label is not the classifier's own vocabulary.
# split_partial/batch_partial both surface as partial_payment; keeping the
# translation here rather than in the fixture means the fixture stays a
# description of the data and this stays a description of the scoring.
_CASE_TYPE_TO_CAUSE = {
    "split_partial": "partial_payment",
    "batch_partial": "partial_payment",
}


@dataclass
class CauseScore:
    cause: str
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    #: Rows truly of this cause. TP + FN.
    support: int = 0
    #: Money on rows this cause was wrongly raised against. The operator-time
    #: cost of a false positive, in the only unit this product measures.
    false_positive_paise: int = 0

    @property
    def precision(self) -> float | None:
        denom = self.true_positives + self.false_positives
        # None, not 0.0. A cause that was never predicted has undefined
        # precision, and reporting that as zero would read as "it got them all
        # wrong" when it in fact made no claim at all.
        return None if denom == 0 else self.true_positives / denom

    @property
    def recall(self) -> float | None:
        denom = self.true_positives + self.false_negatives
        return None if denom == 0 else self.true_positives / denom

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if p is None or r is None or p + r == 0:
            return None
        return 2 * p * r / (p + r)


@dataclass
class EvaluationReport:
    #: Label for whoever produced the predictions, e.g. "rules" or a model id.
    classifier: str
    per_cause: dict[str, CauseScore] = field(default_factory=dict)
    #: confusion[true_cause][predicted_cause] = count
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    subjects: int = 0
    correct: int = 0
    #: Subjects the classifier declined to label. Counted separately from
    #: wrong answers on purpose: abstaining is the honest move when a model is
    #: unsure, and scoring it as a wrong guess would punish the behaviour this
    #: pipeline actually wants.
    abstained: int = 0
    elapsed_seconds: float = 0.0

    @property
    def accuracy(self) -> float | None:
        return None if self.subjects == 0 else self.correct / self.subjects

    @property
    def rows_per_second(self) -> float | None:
        if self.elapsed_seconds <= 0:
            return None
        return self.subjects / self.elapsed_seconds

    def as_dict(self) -> dict:
        """JSON-safe, for the API and for committing a run alongside a claim."""
        return {
            "classifier": self.classifier,
            "subjects": self.subjects,
            "correct": self.correct,
            "abstained": self.abstained,
            "accuracy": self.accuracy,
            "elapsed_seconds": round(self.elapsed_seconds, 4),
            "rows_per_second": self.rows_per_second,
            "per_cause": {
                cause: {
                    "support": s.support,
                    "true_positives": s.true_positives,
                    "false_positives": s.false_positives,
                    "false_negatives": s.false_negatives,
                    "precision": s.precision,
                    "recall": s.recall,
                    "f1": s.f1,
                    "false_positive_paise": s.false_positive_paise,
                }
                for cause, s in sorted(self.per_cause.items())
            },
            "confusion": self.confusion,
        }


def truth_from_ground_truth(ground_truth: dict) -> dict[str, str]:
    """{subject -> true cause} from a fixture's ground truth.

    The subject key is the ledger ref where there is one and the gateway ref
    otherwise, because `unexplained` rows are orphans with no ledger side at
    all -- keying everything on ledger_ref would silently drop exactly the
    category this evaluation most needs to measure.
    """
    truth: dict[str, str] = {}
    for case in ground_truth.get("cases", []):
        case_type = case["case_type"]
        cause = _CASE_TYPE_TO_CAUSE.get(case_type, case_type)
        if case_type in _CLEAN_CASE_TYPES:
            cause = CLEAN

        subject = case.get("ledger_ref")
        if not subject:
            refs = case.get("gateway_refs") or case.get("bank_refs") or []
            if not refs:
                continue
            subject = refs[0]
        truth[subject] = cause
    return truth


def predictions_from_exceptions(exceptions, subjects: set[str]) -> dict[str, str]:
    """{subject -> predicted cause} from classify()'s output.

    Every subject the truth knows about gets an entry: one the classifier
    raised nothing for is a prediction of CLEAN, not a missing row. Leaving it
    out would quietly exclude the pipeline's misses from recall.
    """
    predicted = {subject: CLEAN for subject in subjects}
    for exc in exceptions:
        subject = exc.ledger_ref or exc.gateway_ref
        if subject is None:
            continue
        # A ledger row can carry more than one exception across sources (the
        # gateway pass and the bank pass each raise their own). First writer
        # wins so the score is stable; the duplicate is not a second subject.
        if predicted.get(subject, CLEAN) == CLEAN:
            predicted[subject] = exc.cause
    return predicted


def score(
    truth: dict[str, str],
    predicted: dict[str, str],
    *,
    classifier: str,
    amounts: dict[str, int] | None = None,
    elapsed_seconds: float = 0.0,
    abstain_marker: str | None = None,
) -> EvaluationReport:
    """Per-cause precision/recall/F1 plus a confusion matrix.

    `amounts` maps subject -> amount_paise and is only used to price false
    positives. `abstain_marker` names the value a classifier uses to say "I
    don't know"; those subjects are counted but excluded from every cause's
    precision, since an abstention is not a claim.
    """
    amounts = amounts or {}
    causes = sorted(set(truth.values()) | {v for v in predicted.values() if v != abstain_marker})

    report = EvaluationReport(classifier=classifier, elapsed_seconds=elapsed_seconds)
    report.per_cause = {c: CauseScore(cause=c) for c in causes}
    report.confusion = {t: {p: 0 for p in causes} for t in causes}

    for subject, actual in truth.items():
        guess = predicted.get(subject, CLEAN)
        report.subjects += 1

        if abstain_marker is not None and guess == abstain_marker:
            report.abstained += 1
            # Still a miss for the true cause's recall -- the row went
            # unlabelled, and recall asks how much of the truth was found, not
            # how much was found or politely declined.
            report.per_cause[actual].false_negatives += 1
            report.per_cause[actual].support += 1
            continue

        report.per_cause[actual].support += 1
        if actual in report.confusion and guess in report.confusion[actual]:
            report.confusion[actual][guess] += 1

        if guess == actual:
            report.correct += 1
            report.per_cause[actual].true_positives += 1
        else:
            report.per_cause[actual].false_negatives += 1
            if guess in report.per_cause:
                report.per_cause[guess].false_positives += 1
                report.per_cause[guess].false_positive_paise += amounts.get(subject, 0)

    return report


def stratified_sample(truth: dict[str, str], limit: int) -> list[str]:
    """Up to `limit` subjects, spread evenly across true causes.

    Needed because scoring an LLM costs one API call per subject and the free
    Gemini tier allows 20 per day, against a 27-subject stress set. Taking the
    first N instead would be worse than useless here: the stress set is ordered
    by construction, so a head() spends the entire budget on amount-boundary
    cases and never reaches the language ones -- which are the only cases the
    model is there to be tested on. The sample would then "show" the model
    matching the rules exactly, purely because it never saw a case that
    distinguishes them.

    Round-robin over causes sorted by name, so the selection is deterministic
    and a published number can be reproduced.
    """
    if limit <= 0 or limit >= len(truth):
        return sorted(truth)

    by_cause: dict[str, list[str]] = {}
    for subject in sorted(truth):
        by_cause.setdefault(truth[subject], []).append(subject)

    picked: list[str] = []
    while len(picked) < limit:
        took_any = False
        for cause in sorted(by_cause):
            if by_cause[cause] and len(picked) < limit:
                picked.append(by_cause[cause].pop(0))
                took_any = True
        if not took_any:
            break
    return sorted(picked)


def format_report(report: EvaluationReport) -> str:
    """A fixed-width table. Printed in the terminal and read off in the demo,
    so it has to survive being screenshotted -- no colour, no unicode drawing."""
    lines: list[str] = []
    acc = report.accuracy
    rps = report.rows_per_second
    lines.append(f"classifier : {report.classifier}")
    lines.append(f"subjects   : {report.subjects}")
    lines.append(f"accuracy   : {'n/a' if acc is None else f'{acc:.1%}'}")
    if report.abstained:
        lines.append(f"abstained  : {report.abstained}")
    lines.append(
        f"throughput : {'n/a' if rps is None else f'{rps:,.0f} rows/s'}"
        f"  ({report.elapsed_seconds:.3f}s)"
    )
    lines.append("")
    lines.append(f"{'cause':<20}{'n':>5}{'prec':>8}{'recall':>8}{'F1':>8}{'FP cost':>14}")
    lines.append("-" * 63)

    def pct(v: float | None) -> str:
        return "  --  " if v is None else f"{v:6.1%}"

    for cause, s in sorted(report.per_cause.items()):
        fp_cost = f"₹{s.false_positive_paise / 100:,.0f}" if s.false_positive_paise else "-"
        lines.append(
            f"{cause:<20}{s.support:>5}{pct(s.precision):>8}{pct(s.recall):>8}"
            f"{pct(s.f1):>8}{fp_cost:>14}"
        )
    return "\n".join(lines)
