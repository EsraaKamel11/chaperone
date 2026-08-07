"""Hard exclusions and the null-field policy of design spec 8.3.

The brief's own tests exercise one nulled field (`check_size_max`) and never touch `geography` at
all, so deleting the `geography` row from `classify`'s field loop leaves every one of them green --
an ineligible party surfaced in silence. The parametrized rows below close that in both directions:
each of the five eligibility fields is exercised nulled, naming itself, and mismatched, excluding.
"""
from __future__ import annotations

import pytest

from chaperone.matching.filters import (
    ELIGIBILITY_FIELDS,
    Candidate,
    Eligibility,
    Mandate,
    classify,
)
from chaperone.policy.canonical import CanonicalizationError

MANDATE = Mandate(check_size_min="5000000", stage="Series A", sector="fintech",
                  geography="US", consented_jurisdictions=frozenset({"US", "UK"}))


def _candidate(**overrides) -> Candidate:
    base = dict(id="c1", check_size_max="25000000", stage="Series A", sector="fintech",
                geography="US", jurisdiction="US", days_since_touch=30, prior_passes=0)
    base.update(overrides)
    return Candidate(**base)


# A mismatching value per eligibility axis, each one wrong on that axis alone. Hand-written rather
# than derived from the mandate, so a mandate field renamed in `classify` cannot quietly rename the
# thing this table tests.
_WRONG_VALUES = {
    "check_size_max": "250000",
    "stage": "Series C",
    "sector": "biotech",
    "geography": "EU",
    "jurisdiction": "DE",
}


def test_a_fully_matching_candidate_is_eligible():
    assert classify(_candidate(), MANDATE) == (Eligibility.ELIGIBLE, ())


def test_a_cheque_size_an_order_of_magnitude_too_small_is_excluded_not_downranked():
    eligibility, _ = classify(_candidate(check_size_max="250000"), MANDATE)
    assert eligibility is Eligibility.INELIGIBLE


def test_a_cheque_size_exactly_at_the_floor_is_eligible():
    """The residual has a direction: `<=` here drops an eligible party rather than admitting one."""
    assert classify(_candidate(check_size_max="5000000"), MANDATE)[0] is Eligibility.ELIGIBLE


def test_a_non_consented_jurisdiction_is_excluded_using_the_same_predicate_as_the_boundary():
    eligibility, _ = classify(_candidate(jurisdiction="DE"), MANDATE)
    assert eligibility is Eligibility.INELIGIBLE


def test_a_wrong_stage_or_sector_is_excluded():
    assert classify(_candidate(stage="Series C"), MANDATE)[0] is Eligibility.INELIGIBLE
    assert classify(_candidate(sector="biotech"), MANDATE)[0] is Eligibility.INELIGIBLE


@pytest.mark.parametrize("field_name", sorted(_WRONG_VALUES))
def test_each_eligibility_axis_excludes_on_its_own_when_it_mismatches(field_name):
    """Every axis, not the three the brief happened to write out. Delete any one row from the
    field loop and this fails on that row while the rest stay green."""
    eligibility, missing = classify(_candidate(**{field_name: _WRONG_VALUES[field_name]}), MANDATE)
    assert eligibility is Eligibility.INELIGIBLE
    assert missing == ()


def test_a_null_eligibility_field_lands_in_needs_verification_with_the_field_named():
    """Not 'null passes' - that readmits the ineligible. Not 'null fails' - that drops the eligible."""
    eligibility, missing = classify(_candidate(check_size_max=None), MANDATE)
    assert eligibility is Eligibility.NEEDS_VERIFICATION
    assert missing == ("check_size_max",)


@pytest.mark.parametrize("field_name", sorted(_WRONG_VALUES))
def test_each_nulled_eligibility_field_lands_in_needs_verification_naming_itself(field_name):
    """Every field name here is a string `tools/build_candidates.py` and the ablation's
    missingness injection compare on, so the set is pinned as well as the behaviour."""
    eligibility, missing = classify(_candidate(**{field_name: None}), MANDATE)
    assert eligibility is Eligibility.NEEDS_VERIFICATION
    assert missing == (field_name,)


def test_several_null_fields_are_all_named():
    _, missing = classify(_candidate(check_size_max=None, sector=None), MANDATE)
    assert set(missing) == {"check_size_max", "sector"}


def test_every_eligibility_field_nulled_names_every_one_of_them_in_a_stable_order():
    """A candidate about which nothing is known is the 'examined nothing, reported eligible' input.
    It must be needs-verification, and the report must name all five without repeating one."""
    blank = _candidate(**{name: None for name in ELIGIBILITY_FIELDS})
    eligibility, missing = classify(blank, MANDATE)
    assert eligibility is Eligibility.NEEDS_VERIFICATION
    assert missing == ("jurisdiction", "check_size_max", "stage", "sector", "geography")


def test_the_declared_eligibility_fields_are_the_ones_classify_can_name():
    """`ELIGIBILITY_FIELDS` is what a caller injects missingness over. A field present there but
    unread by `classify` is a hole; a field `classify` reads but does not declare is undiscoverable."""
    named = set()
    for field_name in ELIGIBILITY_FIELDS:
        named.update(classify(_candidate(**{field_name: None}), MANDATE)[1])
    assert named == set(ELIGIBILITY_FIELDS)


def test_a_definite_exclusion_beats_a_missing_field():
    """A candidate known ineligible on one axis is not resurrected by uncertainty on another."""
    eligibility, _ = classify(_candidate(jurisdiction="DE", check_size_max=None), MANDATE)
    assert eligibility is Eligibility.INELIGIBLE


def test_an_unparseable_cheque_size_is_needs_verification_not_ineligible():
    eligibility, missing = classify(_candidate(check_size_max="a lot"), MANDATE)
    assert eligibility is Eligibility.NEEDS_VERIFICATION
    assert "check_size_max" in missing


def test_a_mandate_whose_floor_cannot_be_read_raises_rather_than_naming_the_candidates_field():
    """A mandate that cannot be canonicalized is not a candidate's uncertainty. Handling the floor
    parse in the candidate's `except` names `check_size_max` on a candidate whose cheque size is
    perfectly readable, and quietly routes the entire population to needs-verification."""
    unreadable = Mandate(check_size_min="a lot", stage="Series A", sector="fintech",
                         geography="US", consented_jurisdictions=frozenset({"US"}))
    with pytest.raises(CanonicalizationError):
        classify(_candidate(), unreadable)


def test_a_missing_touch_date_is_not_an_eligibility_gap():
    """`days_since_touch` feeds the ranking, not the gate. Adding it to the eligibility loop would
    drop an eligible party into needs-verification for a field no exclusion consults."""
    assert classify(_candidate(days_since_touch=None), MANDATE) == (Eligibility.ELIGIBLE, ())
