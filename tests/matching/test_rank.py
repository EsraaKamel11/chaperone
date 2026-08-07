"""Relationship scoring, and the re-rank that happens inside the filtered set rather than as it.

The brief's six tests assert *membership* of the two buckets and never assert an *order*, so
replacing `sorted(...)` with `list(...)` leaves all six green while the ranking stops ranking. The
two ordering tests below are what close that, in both of its directions: the relationship term
orders the set, and the embedding term is able to overturn that order without ever reaching outside
the set it was handed.
"""
from __future__ import annotations

import pytest

from chaperone.matching.filters import Candidate, Eligibility, Mandate
from chaperone.matching.rank import rank
from chaperone.matching.relationship import relationship_score

MANDATE = Mandate(check_size_min="5000000", stage="Series A", sector="fintech",
                  geography="US", consented_jurisdictions=frozenset({"US"}))


def _c(cid, **overrides) -> Candidate:
    base = dict(id=cid, check_size_max="25000000", stage="Series A", sector="fintech",
                geography="US", jurisdiction="US", days_since_touch=30, prior_passes=0)
    base.update(overrides)
    return Candidate(**base)


def test_a_recent_touch_outranks_a_stale_one_at_equal_similarity():
    assert relationship_score(_c("a", days_since_touch=7)) > relationship_score(_c("b", days_since_touch=400))


def test_prior_passes_lower_the_relationship_score():
    assert relationship_score(_c("a", prior_passes=3)) < relationship_score(_c("b", prior_passes=0))


def test_a_never_touched_candidate_scores_lowest_without_erroring():
    assert relationship_score(_c("a", days_since_touch=None)) == 0.0


@pytest.mark.parametrize(
    "overrides",
    [
        {"days_since_touch": -100000},   # a touch date ahead of the clock
        {"days_since_touch": 0},
        {"days_since_touch": 10**9},
        {"days_since_touch": None},
        {"prior_passes": -50},           # a pass count below zero inverts the penalty term
        {"prior_passes": 10**6},
        {"days_since_touch": -100000, "prior_passes": -50},
    ],
    ids=["future-touch", "today", "ancient", "never", "negative-passes", "many-passes", "both-corrupt"],
)
def test_no_touchpoint_record_however_corrupt_scores_outside_the_unit_interval(overrides):
    """`max(0.0, 1.0 - d/365)` has no upper bound and `min(0.9, 0.2 * n)` has no lower one, so a
    negative day count or a negative pass count produced a score above 1 -- a corrupt ledger row
    sorting above every real one. Filtering runs first, so this is ranking determinism and not a
    readmission of an excluded party."""
    assert 0.0 <= relationship_score(_c("a", **overrides)) <= 1.0


def test_a_touch_date_ahead_of_the_clock_does_not_outrank_a_touch_today():
    assert relationship_score(_c("a", days_since_touch=-100000)) <= relationship_score(_c("b", days_since_touch=0))


def test_ineligible_candidates_are_absent_from_the_ranking_not_ranked_last():
    candidates = [_c("good"), _c("tiny", check_size_max="250000")]
    ranked, _ = rank(candidates, MANDATE, embed_score=lambda c: 1.0)
    assert [c.id for c in ranked] == ["good"]


def test_needs_verification_candidates_go_to_their_own_bucket():
    candidates = [_c("good"), _c("unknown", check_size_max=None)]
    ranked, needs = rank(candidates, MANDATE, embed_score=lambda c: 1.0)
    assert [c.id for c in ranked] == ["good"]
    assert [c.id for c in needs] == ["unknown"]


def test_embeddings_reorder_inside_the_filtered_set_and_never_retrieve():
    """A high similarity score cannot readmit an excluded candidate."""
    candidates = [_c("eligible-low"), _c("ineligible-high", check_size_max="250000")]
    ranked, _ = rank(candidates, MANDATE, embed_score=lambda c: 0.0 if c.id.startswith("eligible") else 1.0)
    assert [c.id for c in ranked] == ["eligible-low"]


def test_within_the_filtered_set_the_stronger_relationship_ranks_first():
    candidates = [_c("stale", days_since_touch=400), _c("fresh", days_since_touch=7)]
    ranked, _ = rank(candidates, MANDATE, lambda c: 0.5)
    assert [c.id for c in ranked] == ["fresh", "stale"]


def test_within_the_filtered_set_a_higher_embedding_score_can_overturn_the_relationship_order():
    """The embedding term is a re-rank with real weight, not a tie-break that never moves anything.
    Zeroing `EMBEDDING_WEIGHT` leaves every membership test green and fails only here."""
    candidates = [_c("closer-relationship", days_since_touch=7), _c("closer-embedding", days_since_touch=90)]
    ranked, _ = rank(candidates, MANDATE, lambda c: 1.0 if c.id == "closer-embedding" else 0.0)
    assert [c.id for c in ranked] == ["closer-embedding", "closer-relationship"]


def test_no_candidate_the_filters_did_not_exclude_disappears_from_both_buckets():
    """The other direction of design spec 8.3: an eligible party dropped in silence is the failure
    that makes teams soften hard filters into weights. `embed_score` is passed positionally here
    because the ablation arm calls it that way."""
    candidates = [_c("ok-1"), _c("ok-2", days_since_touch=200),
                  _c("unknown-1", sector=None), _c("unknown-2", jurisdiction=None)]
    ranked, needs = rank(candidates, MANDATE, lambda c: 0.5)
    assert len(ranked) + len(needs) == len(candidates)
    assert {c.id for c in ranked} | {c.id for c in needs} == {c.id for c in candidates}


def test_an_excluded_candidate_appears_in_neither_bucket_rather_than_in_the_unverified_one():
    """Ineligible and needs-verification are distinct states downstream: one is refused, the other
    is routed to a human. Collapsing the exclusion into the verification bucket would put a party
    the mandate excludes in front of someone to check."""
    candidates = [_c("ok"), _c("excluded", jurisdiction="DE")]
    ranked, needs = rank(candidates, MANDATE, lambda c: 0.5)
    assert "excluded" not in {c.id for c in ranked} | {c.id for c in needs}


def test_an_empty_population_yields_two_empty_buckets_and_no_default_result():
    assert rank([], MANDATE, lambda c: 1.0) == ([], [])


def test_every_eligibility_state_is_routed_to_a_named_destination():
    """A state added to `Eligibility` and not routed would be dropped from both returned buckets in
    silence. This is the declaration a new member has to be added to."""
    from chaperone.matching.rank import ROUTED_STATES

    assert ROUTED_STATES == frozenset(Eligibility)
