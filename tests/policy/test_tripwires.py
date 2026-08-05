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


def test_the_declared_classes_are_exactly_the_classes_the_pattern_table_carries():
    """Two layers, one policy. `TRIPWIRE_CLASSES` is a hand-written set and the pattern table is
    a separate literal, so they can drift apart in either direction and only this compares them.

    The direction that hides is a class declared with no pattern behind it: `TRIPWIRE_CLASSES` is
    what a coverage map reads to decide a content class has a deterministic detector, and the
    test above it here checks the *declared* set, so an act-class pattern -- or a declared class
    with nothing implementing it -- passes every other assertion in this file.

    Equality, not containment, because each direction is a different failure and both are silent.
    """
    from chaperone.policy.tripwires import _PATTERNS
    assert {violation_class for violation_class, _ in _PATTERNS} == TRIPWIRE_CLASSES


def test_no_span_reaches_across_a_sentence_boundary():
    r"""A span is a quotation. Design spec 4.7 hands it to a human verbatim in the redraft prompt,
    and that human is deciding whether a draft may go out, so a span that runs two sentences
    together tells them the draft said something it did not.

    The gap between a trigger and the token it needs was `[^.]`, and `.` inside a character class
    is a literal full stop -- so the gap traversed `!`, `?`, `;` and `:` freely. Each body below
    declines to advise or states a fact, and each was quoted as one continuous clause of advice:
    `'Personally I am not permitted to say; the deal'` was a real span from the real module.

    Excluding the four sentence marks was measured free: all twelve bodies above keep their
    verdict, and all three drafts that wrap a violation across a line break are still caught,
    because `\n` stays traversable. Excluding `\n` as well is *not* free -- it loses two of those
    three -- so it was not done, and what remains is pinned as a limit below.
    """
    for body in (
        "Honestly, no comment! Separately: the round is open",
        "Personally I am not permitted to say; the deal desk will follow up",
        "They accept EU investors; the round is $10M",
        "Honestly, I have no idea!\n\nThe round closes on Friday.",
    ):
        for finding in evaluate_tripwires(_draft(body)):
            crossed = sorted(set(finding.span) & set("!?;:"))
            assert not crossed, f"span {finding.span!r} crosses {crossed} in {body!r}"


# --- Below this line, and ONLY below it, are limits: behaviours asserted in executable form
# --- because they are known and unclosed, not because they are wanted. The one is named rather
# --- than counted, following the banner in test_citations.py, which records that a counted
# --- banner once instructed a maintainer to delete a guard.
# ---
# --- For a test below: if it fails, a limit has been closed. Delete it, do not repair it.
# ---   test_a_span_can_still_cross_a_paragraph_break_a_known_limit
# --- Every test ABOVE this line is a guard: if one fails, an escape or a misquotation has
# --- reopened. Fix upstream.


def test_a_span_can_still_cross_a_paragraph_break_a_known_limit():
    r"""What the sentence-mark fix leaves behind, in executable form rather than in a comment.

    `\n` is still traversable, so a trigger in one paragraph reaches a token in the next and the
    span carries the break. Closing it costs two of the three measured catches on drafts that
    soft-wrap a violation across a line -- `'We expect 20%\nannually.'` and `'Honestly, I think
    the\nround is worth taking.'` both go from a finding to nothing -- which trades a
    misquotation for an escape, the wrong direction. Closing it properly means matching across
    the break but quoting only the sentence, which changes what a span is.
    """
    body = "Our last fund returned 3x\n\nThe partnership closed its first year of operations"
    findings = evaluate_tripwires(_draft(body))
    assert [f.span for f in findings] == ["3x\n\nThe partnership closed its first year"]
