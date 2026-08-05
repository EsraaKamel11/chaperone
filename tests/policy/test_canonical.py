from decimal import Decimal

import pytest
from hypothesis import given, strategies as st

from chaperone.policy.canonical import (
    CanonicalizationError, arg_digest, canonical_json, figures_in, normalize_money,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("$2.5M", Decimal("2500000")),
        ("2,500,000", Decimal("2500000")),
        ("2500000.00", Decimal("2500000.00")),
        ("$10m", Decimal("10000000")),
        ("1.5k", Decimal("1500")),
    ],
)
def test_the_same_amount_in_different_representations_normalizes_to_one_value(raw, expected):
    assert normalize_money(raw) == expected


def test_a_negative_amount_keeps_its_sign():
    assert normalize_money("-$500.00") == Decimal("-500.00")
    assert normalize_money("($500.00)") == Decimal("-500.00")
    assert normalize_money(-500) == Decimal("-500")


def test_unparseable_input_raises_rather_than_returning_zero():
    with pytest.raises(CanonicalizationError):
        normalize_money("about ten million")


def test_a_figure_in_a_draft_matches_its_record_value_across_representations():
    assert Decimal("2500000") in figures_in("we are raising $2.5M this round")


def test_canonical_json_is_key_order_independent():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_arg_digest_is_stable_and_does_not_contain_the_raw_value():
    digest = arg_digest({"recipient": "someone@example.test"})
    assert digest == arg_digest({"recipient": "someone@example.test"})
    assert "example.test" not in digest
    assert len(digest) == 64


@given(st.decimals(min_value=Decimal("-1e9"), max_value=Decimal("1e9"), places=2))
def test_normalization_round_trips_any_two_place_decimal(value):
    assert normalize_money(f"{value:.2f}") == value


def test_a_multiplier_suffix_is_never_taken_from_the_following_word():
    assert figures_in("the meeting is on 12 March") == {Decimal("12")}
    assert figures_in("10 mistakes were made") == {Decimal("10")}
    assert figures_in("we are raising $2.5M this round") == {Decimal("2500000")}
    assert figures_in("We are raising $10m.") == {Decimal("10000000")}
    assert normalize_money("1.5k") == Decimal("1500")


def test_an_unbalanced_parenthesis_is_unparseable_rather_than_negative():
    with pytest.raises(CanonicalizationError):
        normalize_money("(500")
    with pytest.raises(CanonicalizationError):
        normalize_money("500)")
    assert normalize_money("($500.00)") == Decimal("-500.00")
    assert normalize_money("-$500.00") == Decimal("-500.00")


def test_a_non_finite_amount_is_unparseable():
    for value in (float("nan"), float("inf"), float("-inf"), Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(CanonicalizationError):
            normalize_money(value)


def test_an_unsupported_type_raises_the_same_typed_error():
    for value in (None, ["500"], {"amount": 500}, object()):
        with pytest.raises(CanonicalizationError):
            normalize_money(value)


# Large enough that applying any multiplier pushes the result past the decimal context's Emax.
_BARE_DIGITS = "9" * 999_999
_TOO_LARGE_TO_SCALE = _BARE_DIGITS + "m"


def test_an_amount_too_large_to_scale_raises_the_same_typed_error():
    """Every caller guards with `except CanonicalizationError`, so nothing else may escape.

    Applying the multiplier is arithmetic, and arithmetic raises `decimal.Overflow`, which
    derives from `ArithmeticError` rather than `ValueError` -- so it is not a
    `CanonicalizationError` and would pass straight through every handler in the chain.
    Catching `DecimalException` covers `Overflow`, `InvalidOperation` and their siblings at
    once, leaving none to leak the next time this code is touched.

    This raises where `figures_in` falls back, and the asymmetry is the strict/lenient split:
    `normalize_money` reads a value handed over as a record field and must never guess a
    magnitude, so it refuses. Scanning prose for candidates is the lenient half.
    """
    with pytest.raises(CanonicalizationError):
        normalize_money(_TOO_LARGE_TO_SCALE)


def test_an_amount_too_large_to_scale_keeps_its_bare_digits():
    """The multiplier is dropped, never the figure.

    An unrecognised spelling and a multiplier whose product overflows are the same situation --
    the multiplier cannot be applied -- so they take the same answer. The pattern already
    truncates `$5MM` to 5 before `normalize_money` is reached; this is that rule arriving one
    step later, where the failure is arithmetic rather than textual. The figure survives, fails
    to match the record, and routes the draft to a human.
    """
    assert figures_in(f"we raised $2.5M against a cap of {_TOO_LARGE_TO_SCALE}") == {
        Decimal("2500000"),
        Decimal(_BARE_DIGITS),
    }


def test_documented_limit_a_bare_digit_run_in_prose_is_treated_as_a_figure():
    """A documented boundary, not a defect.

    `figures_in` is a candidate extractor for currency-marked drafts, not a general numeric
    parser. It cannot tell a sum from a count, so an unadorned numeral in prose arrives as a
    candidate figure and every caller must expect to see one. Nobody should later mistake it
    for a money detector.
    """
    assert figures_in("I have 3 questions") == {Decimal("3")}


def test_documented_limit_comma_grouping_is_not_validated():
    """A documented boundary, not a defect.

    Commas are stripped, never checked, so a malformed thousands grouping normalizes to a
    value instead of raising. Canonicalization answers "what number is this", not "is this
    well-formed".
    """
    assert normalize_money("2,5,0,0") == Decimal("2500")
    assert normalize_money("1,23") == Decimal("123")


def test_documented_limit_an_unrecognised_multiplier_spelling_truncates_to_the_digits():
    """A documented boundary, not a defect.

    The multiplier alphabet is `k`, `m` and `b` only -- the spellings a drafter actually
    emits. `MM`, `bn` and `MB` are not recognised, so such a figure truncates to its bare
    digits instead of scaling. Truncation is the side to fail on: a truncated figure usually
    misses the record and produces a spurious finding that routes the draft to a human.
    Usually, not always -- the act-class predicate pools every record field value into one
    set, so a record holding any field valued "5" passes a draft writing "$5MM". Dropping the
    figure escapes against every record, not merely a colliding one. Never drop a figure.
    """
    assert figures_in("$5MM") == {Decimal("5")}
    assert figures_in("$20bn") == {Decimal("20")}
    assert figures_in("a 5MB attachment") == {Decimal("5")}
