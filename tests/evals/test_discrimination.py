"""Prediction 4's machinery, asserted; prediction 4's result, never.

Design spec 9.6: CI asserts invariants only, and a probabilistic rate is measured and reported. So
every assertion here is over a constructed input whose answer is known before the code runs. No test
in this file reads the corpus's AUC and compares it to anything.
"""
from __future__ import annotations

import json

import pytest

from chaperone.evals.corpus import CORPUS_PATH, LABELS_PATH, load_corpus, load_labels
from chaperone.evals.discrimination import (
    QUALITY_PASS_THRESHOLD,
    QUALITY_SCORES_PATH,
    DiscriminationError,
    auc,
    load_quality_scores,
    quality_means,
    bootstrap_ci,
    prediction_four_holds,
    run_discrimination,
    score_by_id,
    split_scores,
)

# Enough distinct values on both sides that the resampled AUC is not confined to a handful of
# levels, which is what makes the seed's effect observable at the 2.5th and 97.5th percentiles.
_POS = [0.50, 0.55, 0.60, 0.65, 0.70, 0.72]
_NEG = [0.45, 0.52, 0.58, 0.63, 0.68, 0.71]


def test_perfect_separation_scores_one():
    assert auc([0.9, 0.8, 0.95], [0.1, 0.2, 0.05]) == 1.0


def test_perfect_inversion_scores_zero():
    assert auc([0.1, 0.2], [0.9, 0.8]) == 0.0


def test_no_separation_scores_one_half():
    assert auc([0.5, 0.5], [0.5, 0.5]) == 0.5


def test_a_tie_counts_a_half_against_wins_and_losses_in_one_mixture():
    """Hand-computed, because 1.0, 0.0 and 0.5 all survive a broken tie rule.

    Two positives against three negatives, six pairs, counted by hand:

    - 0.5 vs 0.5 tie, 0.5; vs 0.6 loss, 0; vs 0.8 loss, 0.
    - 0.7 vs 0.5 win, 1; vs 0.6 win, 1; vs 0.8 loss, 0.

    2.5 of 6. Deleting the half-credit branch gives 2 of 6, and each of the three tests above still
    passes: the first two have no tie and the third is all ties, where 0 of 4 is not 0.5 only if the
    branch is gone entirely -- which is why that one is not enough either.
    """
    assert auc([0.5, 0.7], [0.5, 0.6, 0.8]) == pytest.approx(2.5 / 6)


def test_an_empty_side_is_refused_rather_than_reported_as_no_separation():
    """The vacuous pass this whole task exists to stop, at its narrowest point.

    An `auc` that answers 0.5 over an empty side answers 0.5 over an empty corpus, a bootstrap over
    it returns (0.5, 0.5), and a CI of (0.5, 0.5) contains 0.5 and excludes 0.75 -- so prediction 4
    is reported held having compared no draft to any other. Refusing here is what makes that chain
    unreachable, and it is refused on both sides because a corpus with no compliant row and one with
    no violating row are different defects with the same consequence.
    """
    with pytest.raises(DiscriminationError):
        auc([], [0.1, 0.2])
    with pytest.raises(DiscriminationError):
        auc([0.9, 0.8], [])


def test_the_ci_is_reproducible_under_its_seed():
    assert bootstrap_ci(_POS, _NEG) == bootstrap_ci(_POS, _NEG)


def test_two_seeds_do_not_produce_one_interval():
    """The test above passes against a function that ignores `seed` entirely.

    A `bootstrap_ci` that reaches for `random.Random(20260805)` and discards its parameter is
    reproducible, is wrong, and is invisible to a reproducibility check. Eight seeds over fixed
    inputs is deterministic in both directions: nothing here can flake, and a function that ignores
    the seed collapses all eight to one interval.
    """
    intervals = {bootstrap_ci(_POS, _NEG, seed=s) for s in range(1, 9)}
    assert len(intervals) > 1, f"eight seeds produced one interval, {intervals}"


def test_the_ci_brackets_the_point_estimate():
    low, high = bootstrap_ci(_POS, _NEG)
    assert low <= auc(_POS, _NEG) <= high


def test_the_ci_refuses_an_empty_side_in_its_own_right():
    """`auc` guards the point estimate; nothing has called it yet when the resampling starts.

    A `bootstrap_ci` delegating this check to `auc` raises `IndexError` out of `random.choice` on
    the first resample instead, which names the standard library rather than the empty denominator.
    """
    with pytest.raises(DiscriminationError):
        bootstrap_ci([], _NEG)
    with pytest.raises(DiscriminationError):
        bootstrap_ci(_POS, [])


def test_an_iteration_count_that_resamples_nothing_is_refused():
    """Zero iterations builds an empty sample list, and a percentile of nothing is not an interval."""
    with pytest.raises(DiscriminationError):
        bootstrap_ci(_POS, _NEG, iterations=0)


def _eval_corpus():
    return load_corpus(CORPUS_PATH, split="eval"), load_labels(LABELS_PATH)


def test_the_split_is_taken_on_the_label_and_never_on_the_score():
    """The one thing that would make an AUC self-fulfilling: splitting by the score being ranked.

    Every score here is above the pass threshold, so a split taken on the score puts all 80 rows on
    one side and the other side is empty. The label split is 50 and 30, which is what the labels
    hold and what no reading of any score can produce.
    """
    items, labels = _eval_corpus()
    positive, negative = split_scores(items, labels, lambda item: 0.9)
    expected_violating = sum(1 for item in items if labels[item.id].violating)
    assert (len(positive), len(negative)) == (expected_violating, len(items) - expected_violating)


def test_a_row_carrying_no_label_is_refused_rather_than_dropped_from_a_denominator():
    """A dropped row shrinks a denominator with nothing in the output saying it shrank."""
    items, labels = _eval_corpus()
    holed = {k: v for k, v in labels.items() if k != items[0].id}
    with pytest.raises(DiscriminationError, match=items[0].id):
        split_scores(items, holed, lambda item: 0.9)


def test_a_row_carrying_no_quality_score_is_refused_rather_than_defaulted():
    """The task's own instruction, held by a test: fail loudly, never substitute.

    A lookup written `judge_scores.get(item.id, 0.5)` returns the neutral score for every row the
    blind judge never scored, and a corpus of neutral scores is an AUC of exactly 0.5 -- the value
    prediction 4 predicts. The default that looks harmless is the one that manufactures the result.
    """
    items, _ = _eval_corpus()
    scores = {item.id: 0.9 for item in items[1:]}
    with pytest.raises(DiscriminationError, match=items[0].id):
        score_by_id(scores)(items[0])


def test_the_result_reports_a_two_by_two_table_over_every_row():
    items, labels = _eval_corpus()
    result = run_discrimination(items, labels, {item.id: 0.9 for item in items})
    assert set(result.table) == {
        "quality_pass_and_violating", "quality_pass_and_compliant",
        "quality_fail_and_violating", "quality_fail_and_compliant",
    }
    assert sum(result.table.values()) == len(items)


def test_each_cell_holds_the_count_it_names():
    """The sum above is satisfied by a table that puts every row in one cell.

    Scores are set by label so the diagonal is known before the code runs: every violating row
    passes, every compliant row fails, and the two off-diagonal cells are empty. The counts are read
    off the labels here and off a threshold comparison there, so this is not the code under test
    computed twice.
    """
    items, labels = _eval_corpus()
    scores = {item.id: (0.9 if labels[item.id].violating else 0.1) for item in items}
    result = run_discrimination(items, labels, scores)
    violating = sum(1 for item in items if labels[item.id].violating)
    assert result.table == {
        "quality_pass_and_violating": violating,
        "quality_pass_and_compliant": 0,
        "quality_fail_and_violating": 0,
        "quality_fail_and_compliant": len(items) - violating,
    }


def test_both_denominators_travel_with_the_result():
    """A rate quoted without its denominator invites the wrong one, as `ArmResult` already says."""
    items, labels = _eval_corpus()
    result = run_discrimination(items, labels, {item.id: 0.9 for item in items})
    violating = sum(1 for item in items if labels[item.id].violating)
    assert (result.n_violating, result.n_compliant) == (violating, len(items) - violating)


def test_a_score_exactly_at_the_threshold_counts_as_a_pass():
    """The boundary the threshold names. `>` in place of `>=` moves all 80 rows to the fail row."""
    items, labels = _eval_corpus()
    scores = {item.id: QUALITY_PASS_THRESHOLD for item in items}
    result = run_discrimination(items, labels, scores)
    assert result.table["quality_fail_and_violating"] == 0
    assert result.table["quality_fail_and_compliant"] == 0


def test_an_interval_containing_one_half_and_excluding_three_quarters_holds():
    assert prediction_four_holds(0.45, 0.55) is True


def test_an_interval_excluding_one_half_fails_the_prediction():
    assert prediction_four_holds(0.80, 0.95) is False


def test_an_interval_below_one_half_fails_it_too_rather_than_passing_by_symmetry():
    """A judge that ranks compliant drafts *above* violating ones has not confirmed the null.

    The pre-registration names containment of 0.5, not distance from it, so an inverted separation
    is a failed prediction in the other direction and must be reported as one. A predicate written
    `abs(midpoint - 0.5) < 0.25` reports this interval as holding.
    """
    assert prediction_four_holds(0.20, 0.30) is False


def test_an_interval_containing_both_landmarks_fails_the_prediction():
    """The conjunct the separating case cannot reach.

    An interval of (0.40, 0.80) contains 0.5, so a predicate that dropped "and excludes 0.75"
    reports it as holding. Every other case in this file either contains both landmarks or neither,
    so this input is the only one that tells the two predicates apart.
    """
    assert prediction_four_holds(0.40, 0.80) is False


def test_three_quarters_at_the_endpoint_is_contained_rather_than_excluded():
    """"Excludes 0.75" read strictly: an interval reaching it has not excluded it."""
    assert prediction_four_holds(0.50, 0.75) is False


def test_the_prediction_flag_records_a_failed_null_rather_than_reframing_it():
    """A failed null is reported as a failed prediction, never quietly reframed."""
    items, labels = _eval_corpus()
    separating = {item.id: (0.95 if labels[item.id].violating else 0.05) for item in items}
    result = run_discrimination(items, labels, separating)
    assert result.prediction_held is False
    assert result.auc > 0.75


def test_the_prediction_flag_is_computed_rather_than_pinned_to_a_failure():
    """The test above passes against `prediction_held = False`, which reports every run as failed.

    Constant scores rank nothing above anything, so the AUC is 0.5 and every resample of it is too:
    the interval is (0.5, 0.5), which contains 0.5 and excludes 0.75. That is the one shape the flag
    must report as held, and it is the shape a hardcoded `False` gets wrong.
    """
    items, labels = _eval_corpus()
    result = run_discrimination(items, labels, {item.id: 0.42 for item in items})
    assert result.prediction_held is True


def _write(tmp_path, *rows):
    path = tmp_path / "scores.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return path


def test_the_shipped_scores_cover_every_corpus_row_and_no_other():
    """Set equality in both directions, over the whole corpus rather than one split.

    A scores file short of a row makes `run_discrimination` refuse rather than measure, which is the
    loud direction; a file carrying a row the corpus does not have is silent, because nothing looks
    the other way. So this is the only place the second direction is checked.
    """
    scores = load_quality_scores(QUALITY_SCORES_PATH)
    corpus_ids = {item.id for item in load_corpus(CORPUS_PATH)}
    assert set(scores) == corpus_ids
    assert len(scores) == len(corpus_ids)


def test_the_shipped_scores_are_not_one_repeated_value():
    """**The guard against measuring nothing at all**, and the defect this task was handed to fix.

    Every caller of `score_quality` in this repository passes a constant transport, so a quality
    score set derived from one would rank every draft equal to every other: AUC exactly 0.5, an
    interval of (0.5, 0.5), and prediction 4 reported held having discriminated nothing. The
    pre-registered null would pass by construction rather than by evidence.

    Non-constancy is an invariant of the file and is not the result: it says the instrument has a
    scale, and says nothing about which side of it any label falls on. The distribution's shape and
    its overlap with the labels are measured and reported, never asserted.
    """
    scores = load_quality_scores(QUALITY_SCORES_PATH)
    assert len(set(quality_means(scores).values())) > 1
    for dimension in ("grounding", "fluency", "fit"):
        assert len({getattr(s, dimension) for s in scores.values()}) > 1, f"{dimension} is constant"


def test_every_shipped_score_lies_inside_the_range_the_rubric_states():
    for item_id, score in load_quality_scores(QUALITY_SCORES_PATH).items():
        for dimension in ("grounding", "fluency", "fit"):
            assert 0.0 <= getattr(score, dimension) <= 1.0, f"{item_id}.{dimension}"


def test_a_score_outside_the_stated_range_is_refused_here(tmp_path):
    """`judge.py` accepts an out-of-range score as value fidelity and names where it would matter.

    Its docstring: *"where it would matter is 9.4's discrimination table rather than any
    permission: an inflated score corrupts an AUC, and authorizes nothing."* This is 9.4, so the
    refusal is here. A single 2.0 outranks every honest score in the corpus and moves the AUC on
    its own.
    """
    with pytest.raises(DiscriminationError, match="c0000"):
        load_quality_scores(_write(tmp_path, {"id": "c0000", "grounding": 2.0, "fluency": 0.5, "fit": 0.5}))


def test_a_repeated_id_is_refused_rather_than_resolved_by_last_write_wins(tmp_path):
    """Set equality against the corpus cannot see a duplicate: two lines for one id collapse to one
    key and the sets still match, so the coverage test above is only true of a file this refused."""
    with pytest.raises(DiscriminationError, match="c0000"):
        load_quality_scores(_write(
            tmp_path,
            {"id": "c0000", "grounding": 0.5, "fluency": 0.5, "fit": 0.5},
            {"id": "c0000", "grounding": 0.9, "fluency": 0.9, "fit": 0.9},
        ))


def test_a_row_missing_a_dimension_is_refused_rather_than_scored_on_the_rest(tmp_path):
    """Three dimensions divided by a literal 3. A row short one of them is not a lower score."""
    with pytest.raises(DiscriminationError, match="c0000"):
        load_quality_scores(_write(tmp_path, {"id": "c0000", "grounding": 0.5, "fluency": 0.5}))


def test_a_file_holding_no_score_is_refused_rather_than_read_as_an_unscored_corpus(tmp_path):
    """The rule `load_corpus` and `load_labels` already apply: nothing loaded is never a clean load."""
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(DiscriminationError):
        load_quality_scores(empty)


def test_the_ranked_score_is_the_judges_own_mean_and_not_a_second_average():
    """Two layers must not drift: the ranking averages what `QualityScores.mean()` averages.

    `judge.py` notes that `mean()` divides by a literal 3, so a fourth dimension is a silent
    arithmetic error. A mean re-implemented here would be a second place to make that error, and
    the two would disagree about what a quality score is while both looked correct.
    """
    scores = load_quality_scores(QUALITY_SCORES_PATH)
    means = quality_means(scores)
    assert means == {item_id: score.mean() for item_id, score in scores.items()}
    sample = scores["c0000"]
    assert means["c0000"] == (sample.grounding + sample.fluency + sample.fit) / 3
