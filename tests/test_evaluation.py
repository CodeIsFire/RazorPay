"""The scoring maths, hand-checked.

Metric code that nobody verified is worse than no metric code: it produces a
number that looks authoritative and is wrong, and the whole point of this
harness is that the numbers can be trusted enough to publish. Every expected
value below is worked out by hand in the test itself.
"""

from __future__ import annotations

import pytest

from app.evaluation import CLEAN, score, stratified_sample, truth_from_ground_truth


def test_a_perfect_run():
    truth = {"a": "duplicate", "b": "timing_lag", "c": CLEAN}
    got = score(truth, dict(truth), classifier="t")
    assert got.accuracy == 1.0
    assert got.per_cause["duplicate"].precision == 1.0
    assert got.per_cause["duplicate"].recall == 1.0


def test_precision_and_recall_are_computed_per_cause():
    # Four rows truly `duplicate`; the classifier finds two of them and also
    # calls one `timing_lag` row a duplicate.
    #   duplicate: TP=2, FN=2, FP=1  -> precision 2/3, recall 2/4
    #   timing_lag: TP=0, FN=1       -> precision undefined, recall 0
    truth = {"a": "duplicate", "b": "duplicate", "c": "duplicate",
             "d": "duplicate", "e": "timing_lag"}
    predicted = {"a": "duplicate", "b": "duplicate", "c": CLEAN,
                 "d": CLEAN, "e": "duplicate"}
    got = score(truth, predicted, classifier="t")

    dup = got.per_cause["duplicate"]
    assert (dup.true_positives, dup.false_negatives, dup.false_positives) == (2, 2, 1)
    assert dup.precision == pytest.approx(2 / 3)
    assert dup.recall == pytest.approx(0.5)
    assert dup.f1 == pytest.approx(2 * (2 / 3) * 0.5 / ((2 / 3) + 0.5))

    lag = got.per_cause["timing_lag"]
    assert lag.recall == 0.0
    assert lag.precision is None, "never predicted, so precision is undefined not zero"


def test_a_never_predicted_cause_reports_none_not_zero():
    # Reporting 0.0 would read as "it got them all wrong" when the classifier
    # in fact made no claim at all. The distinction matters in a published
    # table that someone will interpret.
    truth = {"a": CLEAN}
    got = score(truth, {"a": CLEAN}, classifier="t")
    assert got.per_cause[CLEAN].precision == 1.0
    assert "chargeback" not in got.per_cause


def test_false_positives_are_priced_in_money():
    # Raising an exception on a row that actually reconciled costs an operator
    # an investigation. The unit this product measures that in is rupees.
    truth = {"a": CLEAN, "b": CLEAN}
    predicted = {"a": "failed_payment", "b": CLEAN}
    got = score(truth, predicted, classifier="t", amounts={"a": 250_000, "b": 999})
    assert got.per_cause["failed_payment"].false_positive_paise == 250_000
    assert got.per_cause[CLEAN].false_positive_paise == 0


def test_an_abstention_is_not_scored_as_a_wrong_answer():
    # It still misses recall -- the row went unlabelled -- but it must not be
    # charged as a false positive against any cause, or the metric punishes
    # exactly the caution the classifier was designed to show.
    truth = {"a": "duplicate", "b": "duplicate"}
    predicted = {"a": "duplicate", "b": "abstain"}
    got = score(truth, predicted, classifier="t", abstain_marker="abstain")

    assert got.abstained == 1
    assert got.correct == 1
    dup = got.per_cause["duplicate"]
    assert dup.false_negatives == 1
    assert dup.false_positives == 0
    assert got.per_cause["duplicate"].recall == pytest.approx(0.5)


def test_rows_the_classifier_said_nothing_about_still_count_as_misses():
    # Leaving a subject out of the predictions must not quietly exclude it
    # from recall -- a classifier that answers nothing would otherwise score
    # perfectly on everything it did answer.
    truth = {"a": "duplicate", "b": "duplicate"}
    got = score(truth, {"a": "duplicate"}, classifier="t")
    assert got.subjects == 2
    assert got.per_cause["duplicate"].recall == pytest.approx(0.5)


# --- ground-truth translation ----------------------------------------------

def test_clean_case_types_become_the_no_exception_label():
    gt = {"cases": [
        {"case_type": "exact_match", "ledger_ref": "L1", "gateway_refs": ["G1"]},
        {"case_type": "split_complete", "ledger_ref": "L2", "gateway_refs": []},
        {"case_type": "fee_mismatch", "ledger_ref": "L3", "gateway_refs": []},
    ]}
    truth = truth_from_ground_truth(gt)
    assert truth["L1"] == CLEAN
    assert truth["L2"] == CLEAN
    assert truth["L3"] == "fee_mismatch"


def test_an_orphan_with_no_ledger_side_is_keyed_on_its_gateway_ref():
    # unexplained rows have no ledger ref at all. Keying everything on
    # ledger_ref would silently drop exactly the category the evaluation most
    # needs to measure.
    gt = {"cases": [{"case_type": "unexplained", "ledger_ref": None, "gateway_refs": ["G9"]}]}
    assert truth_from_ground_truth(gt) == {"G9": "unexplained"}


def test_split_and_batch_partials_translate_to_partial_payment():
    gt = {"cases": [
        {"case_type": "split_partial", "ledger_ref": "L1", "gateway_refs": []},
        {"case_type": "batch_partial", "ledger_ref": "L2", "gateway_refs": []},
    ]}
    truth = truth_from_ground_truth(gt)
    assert truth == {"L1": "partial_payment", "L2": "partial_payment"}


# --- sampling ---------------------------------------------------------------

def test_sampling_spreads_across_causes_rather_than_taking_the_first_n():
    # The stress set is ordered by construction. A head() would spend the whole
    # budget on one category and never reach the cases that distinguish the
    # classifiers -- producing a sample that "shows" them agreeing purely
    # because it never asked a question they disagree on.
    truth = {f"a{i}": "fee_mismatch" for i in range(10)}
    truth.update({f"b{i}": "bank_charge" for i in range(10)})
    picked = stratified_sample(truth, 4)
    causes = {truth[s] for s in picked}
    assert len(picked) == 4
    assert causes == {"fee_mismatch", "bank_charge"}


def test_sampling_is_deterministic():
    truth = {f"a{i}": "fee_mismatch" for i in range(6)}
    truth.update({f"b{i}": "timing_lag" for i in range(6)})
    assert stratified_sample(truth, 5) == stratified_sample(truth, 5)


def test_no_limit_returns_everything():
    truth = {"a": "duplicate", "b": CLEAN}
    assert stratified_sample(truth, 0) == ["a", "b"]
    assert stratified_sample(truth, 99) == ["a", "b"]


def test_sampling_drains_a_small_cause_without_stalling():
    # One cause has a single member; round-robin must not spin forever once it
    # is exhausted.
    truth = {"a": "rare", "b": "common", "c": "common", "d": "common"}
    assert len(stratified_sample(truth, 3)) == 3
