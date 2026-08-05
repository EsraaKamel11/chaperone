import pytest

from chaperone.policy.tripwires import TRIPWIRE_CLASSES, evaluate_tripwires
from chaperone.policy.types import Draft, Message, ViolationClass


def _draft(body: str) -> Draft:
    return Draft(thread=(Message(role="investor", body="?"),), body=body, cited_fields=(),
                 recipient_jurisdiction="US", recipient_domain="example.test", tool_name="send_message")


@pytest.mark.parametrize("body", [
    "You should expect 20% annually over five years.",
    "This will return 3x within 24 months.",
])
def test_a_percentage_or_multiple_with_a_horizon_trips_forward_looking_return(body):
    classes = [f.violation_class for f in evaluate_tripwires(_draft(body))]
    assert ViolationClass.FORWARD_LOOKING_RETURN in classes


@pytest.mark.parametrize("body", [
    "Returns are guaranteed.",
    "I can assure you of the downside protection.",
])
def test_guarantee_language_trips_forward_looking_return(body):
    classes = [f.violation_class for f in evaluate_tripwires(_draft(body))]
    assert ViolationClass.FORWARD_LOOKING_RETURN in classes


@pytest.mark.parametrize("body", [
    "They would probably accept $8M instead of $10M.",
    "I could ask them to come down on the valuation.",
])
def test_price_movement_language_trips_negotiates_terms(body):
    classes = [f.violation_class for f in evaluate_tripwires(_draft(body))]
    assert ViolationClass.NEGOTIATES_TERMS in classes


@pytest.mark.parametrize("body", [
    "Honestly, this is a strong deal.",
    "In my view it is an excellent opportunity.",
])
def test_merit_adjectives_predicated_on_the_deal_trip_advises_on_merits(body):
    classes = [f.violation_class for f in evaluate_tripwires(_draft(body))]
    assert ViolationClass.ADVISES_ON_MERITS in classes


@pytest.mark.parametrize("body", [
    "The round is $10M and closes on Friday.",
    "Their last fund was an excellent vintage for the sector.",
    "I have attached the memo.",
])
def test_compliant_drafts_do_not_trip(body):
    assert evaluate_tripwires(_draft(body)) == ()


def test_a_paraphrase_that_evades_the_patterns_is_not_caught():
    """Documented, not a bug. Tripwires are a second disjunct, never the detector."""
    body = "Between us, I would move quickly on this one."
    assert evaluate_tripwires(_draft(body)) == ()


def test_tripwires_only_cover_content_classes():
    assert all(c.family.value == "content" for c in TRIPWIRE_CLASSES)


def test_tripwires_are_deterministic():
    draft = _draft("Returns are guaranteed.")
    assert evaluate_tripwires(draft) == evaluate_tripwires(draft)
