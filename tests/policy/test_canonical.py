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
