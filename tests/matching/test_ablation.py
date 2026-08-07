"""The matching ablation: hard exclusions against tuned weighted features, on one population.

Design spec 1: *"every comparison arm gets the same engineering effort. A rigged baseline converts
an argument into a demo, and a technical reader spots it immediately."* That constraint is what
most of this file is defending, and it is defended behaviourally -- a weight set to zero is a
rigged arm whatever the source text of the function says about it.
"""
from __future__ import annotations

import pytest

from chaperone.matching.ablation import (
    AblationError, inject_missingness, run_matching_ablation, weighted_feature_arm,
)
from chaperone.matching.filters import (
    Candidate, ELIGIBILITY_FIELDS, Eligibility, Mandate, classify,
)

MANDATE = Mandate(check_size_min="5000000", stage="Series A", sector="fintech",
                  geography="US", consented_jurisdictions=frozenset({"US"}))

# One violation per eligibility axis, so no arm can be scored on a population that exercises four
# of the five constraints it is supposed to encode.
VIOLATIONS = (
    {"jurisdiction": "DE"},
    {"check_size_max": "250000"},
    {"stage": "Series C"},
    {"sector": "biotech"},
    {"geography": "EU"},
)


def _c(cid: str, days: int, **overrides) -> Candidate:
    base = dict(id=cid, check_size_max="25000000", stage="Series A", sector="fintech",
                geography="US", jurisdiction="US", days_since_touch=days, prior_passes=0)
    base.update(overrides)
    return Candidate(**base)


def _pool() -> list[Candidate]:
    """Design spec 8.1's regime: at this N the relationally hot contacts are frequently the ones
    who do not fit the mandate. Twenty eligible parties nobody has spoken to in months, twenty
    ineligible ones touched this quarter, each breaking exactly one of the five constraints.
    """
    good = [_c(f"g{i}", 120 + 12 * i) for i in range(20)]
    bad = [_c(f"b{i}", 5 * i, **VIOLATIONS[i % 5]) for i in range(20)]
    return good + bad


def _truth(pool) -> dict[str, bool]:
    """Ground truth is a property of the *latent* record and never of the degraded one a filter
    sees. It is read off the id the generator assigned, not recomputed with `classify` -- scoring
    an arm against a label the arm's own predicate produced would compare the code with itself."""
    return {c.id: c.id.startswith("g") for c in pool}


def test_missingness_injection_is_seeded_and_reproducible():
    pool = _pool()
    assert inject_missingness(pool, rate=0.3, seed=7) == inject_missingness(pool, rate=0.3, seed=7)


def test_missingness_injection_actually_nulls_fields():
    pool = inject_missingness(_pool(), rate=1.0, seed=7)
    assert any(c.check_size_max is None or c.sector is None for c in pool)


def test_two_different_seeds_do_not_produce_the_same_injection():
    """Reproducibility alone is satisfied by a hardcoded `Random(7)` -- and by an identity function.
    The seed has to be the seed that was passed in."""
    assert inject_missingness(_pool(), rate=0.3, seed=7) != inject_missingness(_pool(), rate=0.3, seed=8)


def test_a_rate_of_zero_leaves_every_record_intact():
    """The other end. `rate` has to reach the draw, not merely be accepted as an argument."""
    pool = _pool()
    assert inject_missingness(pool, rate=0.0, seed=7) == pool


def test_injection_reaches_every_eligibility_axis_the_filters_read():
    """Task 22 declared `ELIGIBILITY_FIELDS` in filters.py *for this caller*, so that an axis added
    to `classify` cannot be left out of the population that exercises it. Asserted as an effect: at
    a rate of 1.0 every declared axis is null on every record. Retyping the tuple here -- or
    dropping one name from it, which is how Task 22's eight tests went blind to `geography` --
    leaves that axis populated and fails here."""
    injected = inject_missingness(_pool(), rate=1.0, seed=7)
    for field_name in ELIGIBILITY_FIELDS:
        assert all(getattr(c, field_name) is None for c in injected), field_name


@pytest.mark.parametrize("rate", [-0.1, 1.5, float("nan")], ids=["negative", "above-one", "nan"])
def test_a_rate_outside_the_unit_interval_is_refused_rather_than_clamped(rate):
    """`rng.random() < rate` reads 1.5 as 1.0 and -0.1 as 0.0 without complaint, so a caller who
    meant a percentage gets a silently different experiment than the one they asked for."""
    with pytest.raises(AblationError):
        inject_missingness(_pool(), rate=rate, seed=7)


# --------------------------------------------------------------------------------------------
# The two arms, on the clean population
# --------------------------------------------------------------------------------------------


def test_the_id_prefix_truth_agrees_with_the_filters_on_every_clean_record():
    """Every number below is scored against `_truth`, so a fixture where the ids and the mandate
    disagree would report a contamination that measures the fixture. Held here once, on the clean
    pool only: a degraded record is *supposed* to disagree, which is the whole ablation."""
    for candidate in _pool():
        eligible = classify(candidate, MANDATE)[0] is Eligibility.ELIGIBLE
        assert eligible == candidate.id.startswith("g"), candidate.id


def test_on_clean_data_the_hard_filter_arm_admits_no_ineligible_candidate():
    """Named as tautological, and the mechanism is worth stating because it is stronger than the
    test: an arm built on `classify` can only ever surface a party that passed every exclusion, so
    its contamination stays at zero under injected missingness too -- nulling an axis routes a
    record to needs-verification, which is *out* of the ranked bucket rather than into it. What
    missingness buys this ablation is a non-trivial recall loss, not a contaminated hard arm."""
    pool = _pool()
    hard, _ = run_matching_ablation(pool, MANDATE, _truth(pool), k=10)
    assert hard.contamination == 0.0


def test_the_weighted_feature_arm_admits_ineligible_candidates_it_ranks_highly():
    """The failure mode the hard filters exist to prevent, reproduced on a population where the
    ineligible parties are the well-known ones. A penalty is not an exclusion: enough relationship
    signal outvotes it."""
    pool = _pool()
    _, weighted = run_matching_ablation(pool, MANDATE, _truth(pool), k=10)
    assert weighted.contamination > 0.0


def test_both_metrics_are_reported_for_both_arms():
    pool = _pool()
    for result in run_matching_ablation(pool, MANDATE, _truth(pool), k=10):
        assert result.contamination is not None
        assert result.recall_loss is not None


# --------------------------------------------------------------------------------------------
# Equal engineering effort, asserted two ways
#
# The first of these is the brief's, written verbatim. It scans the arm's source text for the five
# constraint names -- a substring check, which is a proxy in design spec 10's sense: a name in the
# source is not a penalty in the ranking. The second is what the first gestures at, stated as an
# effect. Neither replaces the other and both are kept; the report names the mutant each one
# catches alone.
# --------------------------------------------------------------------------------------------

# (violating override, unknown override) per constraint, for the behavioural companion.
CONSTRAINT_CASES = (
    ("check_size", {"check_size_max": "250000"}, {"check_size_max": None}),
    ("stage", {"stage": "Series C"}, {"stage": None}),
    ("sector", {"sector": "biotech"}, {"sector": None}),
    ("geography", {"geography": "EU"}, {"geography": None}),
    ("jurisdiction", {"jurisdiction": "DE"}, {"jurisdiction": None}),
)


def test_the_weighted_arm_is_tuned_not_a_strawman():
    """Equal engineering effort is binding. The weights encode every constraint the filters do."""
    import inspect
    from chaperone.matching import ablation
    source = inspect.getsource(ablation.weighted_feature_arm)
    for constraint in ("check_size", "stage", "sector", "geography", "jurisdiction"):
        assert constraint in source


@pytest.mark.parametrize(
    "constraint,violating,_unknown", CONSTRAINT_CASES, ids=[c[0] for c in CONSTRAINT_CASES]
)
def test_every_constraint_costs_the_weighted_arm_rank_and_not_only_a_mention(
    constraint, violating, _unknown
):
    """The behavioural form of the test above, and the only one of the two that can establish it.

    A weight of `0.0` on any axis leaves that axis's name in the source and the arm blind to it --
    which is a rigged baseline, the failure design spec 1 names. The violator is placed **first**
    in the input, so a zero penalty leaves Python's stable sort holding it at index 0 and the
    ordering assertion fails rather than passing on a tie.
    """
    compliant = _c("compliant", 30)
    violator = _c("violating", 30, **violating)
    ordered = weighted_feature_arm([violator, compliant], MANDATE, lambda c: 0.5, 2)
    assert [c.id for c in ordered] == ["compliant", "violating"], constraint


@pytest.mark.parametrize(
    "constraint,violating,unknown", CONSTRAINT_CASES, ids=[c[0] for c in CONSTRAINT_CASES]
)
def test_an_unknown_value_costs_the_weighted_arm_less_than_a_violation_and_more_than_nothing(
    constraint, violating, unknown
):
    """The softening this baseline exists to represent: *a missing value costs a small amount
    rather than excluding.* Pricing an unknown at the violation's rate would turn the weighted arm
    into a slow hard filter and the ablation into a comparison of one architecture with itself;
    pricing it at nothing would make an absent record indistinguishable from a compliant one."""
    ordered = weighted_feature_arm(
        [_c("violating", 30, **violating), _c("unknown", 30, **unknown), _c("compliant", 30)],
        MANDATE, lambda c: 0.5, 3,
    )
    assert [c.id for c in ordered] == ["compliant", "unknown", "violating"], constraint


def test_a_cheque_size_the_arm_cannot_read_is_priced_as_unknown_and_not_as_compliant():
    """`classify` files an uncanonicalizable cheque size under *missing*, not under *passes*. The
    weighted arm's `except CanonicalizationError` branch is the one place it could disagree, and a
    permissive branch there readmits every party with a corrupt record at full score."""
    ordered = weighted_feature_arm(
        [_c("corrupt", 30, check_size_max="about five million"), _c("compliant", 30)],
        MANDATE, lambda c: 0.5, 2,
    )
    assert [c.id for c in ordered] == ["compliant", "corrupt"]


# --------------------------------------------------------------------------------------------
# Denominators
#
# `PREREGISTRATION.md` item 5: a rate over an empty denominator is reported as absent, never as
# zero. Both of this module's rates had a way to breach it, and one of them was breached by the
# brief's own headline test -- see the reproductions in the task report.
# --------------------------------------------------------------------------------------------


def _every_eligible_record_holed() -> list[Candidate]:
    """Every eligible party has a hole in one axis, so the hard arm's shortlist comes back empty."""
    good = [_c(f"g{i}", 120 + 12 * i, sector=None) for i in range(20)]
    bad = [_c(f"b{i}", 5 * i, **VIOLATIONS[i % 5]) for i in range(20)]
    return good + bad


def test_an_arm_that_surfaced_nobody_reports_an_absent_contamination_and_not_a_perfect_one():
    """The defect this project has now found six times: a clean number over nothing examined.

    `contamination = ... if top_k else 0.0` reports the best possible score for a shortlist with
    no rows in it, and it is not a hypothetical -- the brief's own `rate=0.5, seed=11` draw drove
    `n_top_k` to 0, so `hard.contamination <= weighted.contamination` was satisfied by an arm that
    had surfaced nobody. An empty shortlist is a loud outcome here, and the count that makes it
    loud is reported next to it.
    """
    pool = _every_eligible_record_holed()
    hard, weighted = run_matching_ablation(pool, MANDATE, _truth(pool), k=10)
    assert hard.n_top_k == 0
    assert hard.contamination is None
    assert weighted.contamination is not None


def test_a_recall_loss_is_taken_over_what_was_reachable_and_not_over_what_the_arm_returned():
    """The same defect aimed at the other rate, and the more dangerous of the two.

    `min(n_eligible, len(top_k))` divides by however many rows the arm chose to return, so an arm
    that surfaced one eligible party out of twenty reachable scores a recall loss of zero -- a
    perfect result, awarded for returning almost nothing. Recall loss is precisely what the hard
    arm is supposed to pay in this ablation, so a denominator that shrinks with the shortlist
    erases the comparison's whole subject. The denominator is `min(n_eligible, k)`.

    The labels here are the only ones in this file not read off the id prefix, and they are
    deliberately at odds with `classify`: nineteen of the twenty rows carry a hole and land in
    needs-verification, and all twenty are labelled eligible because ground truth is a property of
    the *latent* record and a hole is an observation defect rather than a fact about the party.
    That is the same rule `_truth` states, applied to a population that is already degraded -- it
    is not the fixture-validity check being skipped, which is scoped to clean records for exactly
    this reason.
    """
    pool = [_c("g0", 30)] + [_c(f"g{i}", 120 + 12 * i, sector=None) for i in range(1, 20)]
    hard, _ = run_matching_ablation(pool, MANDATE, {c.id: True for c in pool}, k=10)
    assert (hard.n_top_k, hard.n_eligible, hard.n_reachable) == (1, 20, 10)
    assert hard.recall_loss == 0.9


def test_a_population_with_nobody_eligible_reports_an_absent_recall_loss_and_not_a_total_one():
    """`min(n_eligible, k) or 1` invents a denominator of one where there is none, and answers
    1.0 -- *you missed everyone* -- about a population that held nobody to miss. Absent, and the
    contamination beside it is still a real number, which is what tells the two apart."""
    pool = [_c(f"b{i}", 5 * i, **VIOLATIONS[i % 5]) for i in range(20)]
    hard, weighted = run_matching_ablation(pool, MANDATE, _truth(pool), k=10)
    assert (hard.n_eligible, hard.n_reachable) == (0, 0)
    assert hard.recall_loss is None
    assert weighted.recall_loss is None
    assert weighted.contamination == 1.0


def test_a_candidate_carrying_no_ground_truth_label_is_refused_rather_than_scored_around():
    """A truth mapping short of a row narrows both denominators precisely on the rows nobody
    labelled, and the table then describes the labelled part while reading as the whole. `truth[id]`
    alone raises `KeyError('g3')`, which names the standard library rather than the defect."""
    pool = _pool()
    truth = {c.id: c.id.startswith("g") for c in pool if c.id != "g3"}
    with pytest.raises(AblationError, match="ground-truth"):
        run_matching_ablation(pool, MANDATE, truth, k=10)


# --------------------------------------------------------------------------------------------
# The trade
# --------------------------------------------------------------------------------------------


def _holed_pool() -> list[Candidate]:
    """The other regime, and the one the trade lives in: the parties the firm knows are eligible,
    and seventeen of their twenty records have a hole in one axis. Four ineligible parties are
    warm enough to compete; the remaining sixteen are cold.

    Constructed rather than drawn, so the trade below is an invariant of a fixed population and not
    an observation about one lucky seed. Design spec 9.6: rates are measured and reported, and only
    invariants are asserted.
    """
    clean = [_c(f"g{i}", 10 + 10 * i) for i in range(3)]
    holed = [_c(f"g{i}", 10 + 10 * i, sector=None) for i in range(3, 20)]
    warm_bad = [_c(f"b{i}", 5 * i, **VIOLATIONS[4]) for i in range(4)]
    cold_bad = [_c(f"b{i}", 200 + 10 * i, **VIOLATIONS[i % 5]) for i in range(4, 20)]
    return clean + holed + warm_bad + cold_bad


def test_under_missingness_the_hard_filter_arm_trades_recall_for_contamination():
    """Both directions of the trade, strictly, on one population where the mechanism is legible:
    the hard arm routes the seventeen holed records to needs-verification and surfaces the three
    clean ones; the weighted arm charges them 0.05 apiece, surfaces nine of them, and pays for it
    by admitting one ineligible party the hard arm excluded outright.

    The inequalities are strict. `<=` and `>=` are both satisfied by two arms that behaved
    identically, which is the one outcome that would mean the ablation showed nothing.
    """
    pool = _holed_pool()
    hard, weighted = run_matching_ablation(pool, MANDATE, _truth(pool), k=10)
    assert (hard.n_top_k, weighted.n_top_k) == (3, 10)
    assert hard.contamination < weighted.contamination
    assert hard.recall_loss > weighted.recall_loss


@pytest.mark.parametrize("seed", [3, 7, 11, 101, 4242])
@pytest.mark.parametrize("rate", [0.1, 0.3, 0.5, 0.9])
def test_no_injected_draw_lets_the_hard_filter_arm_admit_an_ineligible_party(seed, rate):
    """The invariant across draws, as against the rates, which the report measures instead.

    Nulling an axis moves a record to needs-verification, which is out of the ranked bucket rather
    than into it, so no amount of missingness can make this arm surface a party the mandate
    excludes. Absent when the shortlist is empty, and zero when it is not -- never a number in
    between, and never a zero standing in for the absence.
    """
    pool = inject_missingness(_pool(), rate=rate, seed=seed)
    hard, _ = run_matching_ablation(pool, MANDATE, _truth(pool), k=10)
    assert hard.contamination in (None, 0.0)


@pytest.mark.parametrize("seed", [3, 7, 11, 101, 4242])
@pytest.mark.parametrize("rate", [0.1, 0.3, 0.5, 0.9])
def test_every_reported_rate_is_absent_exactly_when_its_own_denominator_is_empty(seed, rate):
    """The correspondence itself, held across the draws that produce empty shortlists and the ones
    that do not. A rate present over an empty denominator is the fail-open defect; a rate absent
    over a populated one would silently drop a measurement that was available."""
    pool = inject_missingness(_pool(), rate=rate, seed=seed)
    for result in run_matching_ablation(pool, MANDATE, _truth(pool), k=10):
        assert (result.contamination is None) == (result.n_top_k == 0), result
        assert (result.recall_loss is None) == (result.n_reachable == 0), result
