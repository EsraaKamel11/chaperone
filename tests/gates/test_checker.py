import json

import pytest

from chaperone.gates.checker import (
    MODEL_STRENGTH, Checker, CheckerUnavailable, FlagForReview, Verdict,
    assert_checker_not_weaker, build_checker_messages,
)
from chaperone.policy.types import Draft, Message, Record, ViolationClass

RECORD = Record(fields={"round_size": "10000000"})
THREAD = (
    Message(role="investor", body="honestly, is this a good deal?"),
    Message(role="agent", body="Here are the round details."),
)
DRAFT = Draft(thread=THREAD, body="In my view it is a strong opportunity.", cited_fields=("round_size",),
              recipient_jurisdiction="US", recipient_domain="example.test", tool_name="send_message")


def test_the_checker_prompt_contains_the_transmitted_thread():
    blob = json.dumps(build_checker_messages(DRAFT, RECORD))
    assert "honestly, is this a good deal?" in blob
    assert "Here are the round details." in blob


def test_the_checker_prompt_contains_the_cited_records():
    blob = json.dumps(build_checker_messages(DRAFT, RECORD))
    assert "10000000" in blob


def test_the_checker_prompt_has_no_generator_artefacts():
    blob = json.dumps(build_checker_messages(DRAFT, RECORD))
    for marker in ("<thinking>", "toolu_", "You are a drafting agent", "scratchpad", "chain of thought"):
        assert marker not in blob


def test_the_checker_prompt_structure_is_exact():
    """Absence-scanning alone passes an injected turn that dodges the markers."""
    messages = build_checker_messages(DRAFT, RECORD)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert all(m["role"] != "assistant" for m in messages)


def test_a_checker_model_weaker_than_the_drafter_is_refused():
    with pytest.raises(ValueError, match="not be weaker"):
        assert_checker_not_weaker("haiku-tier", "sonnet-tier")


def test_an_equal_or_stronger_checker_model_is_accepted():
    """The property is that neither call raises, and a raise still fails this test.

    The `is None` is what makes that visible to `tests/test_no_empty_tests.py`, whose AST scan
    reads a bare call as no assertion at all. It pins the declared `-> None` return besides, so a
    future version that started returning a verdict could not do it silently.
    """
    assert assert_checker_not_weaker("sonnet-tier", "sonnet-tier") is None
    assert assert_checker_not_weaker("opus-tier", "sonnet-tier") is None


def test_construction_refuses_a_weaker_checker_model():
    with pytest.raises(ValueError):
        Checker(model="haiku-tier", drafter_model="opus-tier", transport=lambda m: FlagForReview(reason="x"))


def test_a_verdict_comes_back_typed():
    verdict = Verdict(violates=True, violation_class=ViolationClass.ADVISES_ON_MERITS,
                      confidence=0.91, span="a strong opportunity")
    checker = Checker("sonnet-tier", "sonnet-tier", transport=lambda m: verdict)
    assert checker.check(DRAFT, RECORD) == verdict


def test_flag_for_review_is_a_first_class_outcome_not_a_compliant_verdict():
    checker = Checker("sonnet-tier", "sonnet-tier", transport=lambda m: FlagForReview(reason="ambiguous"))
    result = checker.check(DRAFT, RECORD)
    assert isinstance(result, FlagForReview)
    assert not isinstance(result, Verdict)


def test_a_transport_failure_raises_checker_unavailable():
    def boom(messages):
        raise TimeoutError("no response")

    checker = Checker("sonnet-tier", "sonnet-tier", transport=boom)
    with pytest.raises(CheckerUnavailable):
        checker.check(DRAFT, RECORD)


def test_an_unparseable_response_after_the_retry_budget_raises_checker_unavailable():
    calls = []

    def bad(messages):
        calls.append(1)
        raise ValueError("schema-valid but invalid")

    checker = Checker("sonnet-tier", "sonnet-tier", transport=bad, retries=2)
    with pytest.raises(CheckerUnavailable):
        checker.check(DRAFT, RECORD)
    assert len(calls) == 3


# --- Guards below, each added because the eleven tests above were measured to survive the mutant
# --- it names, and each watched failing against that mutant applied to the shipped module. Named
# --- rather than counted, following the banner in test_citations.py, which records that a counted
# --- banner once instructed a maintainer to delete a guard. The count would be wrong here in both
# --- directions: the omission guard covers two mutants, and one measured survivor -- a
# --- justification sentence appended to CHECKER_INSTRUCTIONS -- has no guard at all, because
# --- closing it means pinning the whole instruction string. That one is recorded in the task
# --- report as the limit of the absence-scan prong, not as something these guards cover.


def test_the_checker_prompt_contains_the_candidate_draft():
    """The third of the three inputs 3.3 names, and the only one nothing above pins.

    Emptying `<candidate_draft>` leaves all eleven tests above green and leaves the checker
    judging a thread without the message it is being asked to judge.
    """
    blob = json.dumps(build_checker_messages(DRAFT, RECORD))
    assert "In my view it is a strong opportunity." in blob


def test_no_draft_or_record_field_outside_the_three_named_inputs_reaches_the_prompt():
    """Independence is enforced by omission, so omission is what has to be asserted.

    The tests above assert that required things are present and that five markers are absent;
    none of them notices a field that should never have been included. Widening `<cited_records>`
    to the whole record, or appending the routing fields, survives all eleven.

    This pins field *selection* only. The contents of the selected fields are interpolated
    unescaped, so a body or a role can still carry text that reads like a separate turn.
    """
    draft = Draft(
        thread=(Message(role="investor", body="marker_thread_body"),),
        body="marker_draft_body",
        cited_fields=("marker_cited_field",),
        recipient_jurisdiction="marker_jurisdiction",
        recipient_domain="marker_domain",
        tool_name="marker_tool",
    )
    record = Record(fields={"marker_cited_field": "marker_cited_value",
                            "marker_uncited_field": "marker_uncited_value"})
    blob = json.dumps(build_checker_messages(draft, record))
    for included in ("marker_thread_body", "marker_draft_body", "marker_cited_field", "marker_cited_value"):
        assert included in blob, f"{included} is a named input and did not reach the prompt"
    for omitted in ("marker_jurisdiction", "marker_domain", "marker_tool",
                    "marker_uncited_field", "marker_uncited_value"):
        assert omitted not in blob, f"{omitted} is untransmitted and reached the prompt"


def test_the_checker_message_carries_exactly_a_role_and_a_content_key():
    """The structure prong applied to the message, not only to the list of them.

    An extra key carrying the generator's own justification survives both the absence scan and
    `test_the_checker_prompt_structure_is_exact`: the first scans five markers, the second pins
    the turn count and the role and never looks at the key set.
    """
    messages = build_checker_messages(DRAFT, RECORD)
    assert messages, "a builder returning [] would make the loop below assert nothing"
    for message in messages:
        assert set(message) == {"role", "content"}


def test_an_unknown_model_is_refused_rather_than_assumed_strong_enough():
    """Unknown must not mean strong enough; the floor is a lookup, and a lookup can miss.

    Both directions are separate refusals, because an unrecognised checker and an unrecognised
    drafter are different mistakes and either alone makes the comparison meaningless.
    """
    with pytest.raises(ValueError, match="unknown model tier"):
        assert_checker_not_weaker("unlisted-tier", "sonnet-tier")
    with pytest.raises(ValueError, match="unknown model tier"):
        assert_checker_not_weaker("sonnet-tier", "unlisted-tier")
    with pytest.raises(ValueError):
        Checker(model="unlisted-tier", drafter_model="sonnet-tier",
                transport=lambda m: FlagForReview(reason="x"))


# --- Fix round 1: an unusable verdict reached the caller intact, and the gate then allowed the
# --- send or raised at it. The two shapes below are what "schema-valid but invalid" means here:
# --- pydantic accepts both, and neither can be turned into a finding.


def test_a_violation_reported_without_a_class_exhausts_the_budget_and_then_denies():
    """The escape this round closes: the checker said "violates" and the draft transmitted.

    Nothing rejects this verdict on the way through, so the retry budget never engaged and the
    engine's `result.violates and result.violation_class is not None` treated it as clean. An
    unnamed violation cannot become a `Finding`, so it has to be refused where the budget lives.
    """
    calls = []

    def unusable(messages):
        calls.append(1)
        return Verdict(violates=True, violation_class=None, confidence=0.97)

    checker = Checker("sonnet-tier", "sonnet-tier", transport=unusable, retries=2)
    with pytest.raises(CheckerUnavailable):
        checker.check(DRAFT, RECORD)
    assert len(calls) == 3, "the retry budget must be spent before the verdict is given up on"


@pytest.mark.parametrize("returned", [None, "compliant", {"violates": False}, Verdict])
def test_a_transport_returning_something_other_than_a_verdict_denies_rather_than_bubbling(returned):
    """`None`, prose, a raw dict and the class itself all used to sail through `check`.

    Each then raised `AttributeError` inside the engine, past its `except CheckerUnavailable`, so
    an unusable answer arrived as a crash at the gate instead of a deny.
    """
    calls = []

    def wrong_type(messages):
        calls.append(1)
        return returned

    checker = Checker("sonnet-tier", "sonnet-tier", transport=wrong_type, retries=1)
    with pytest.raises(CheckerUnavailable):
        checker.check(DRAFT, RECORD)
    assert len(calls) == 2


def test_a_clean_verdict_carrying_no_class_is_still_usable():
    """The input that would make the new refusal and the rest of the system disagree.

    `violates=False` with no class is the ordinary compliant answer -- every downstream task
    builds its clean checker as `Verdict(violates=False, confidence=0.9)`. Refusing an unnamed
    class unconditionally would deny every compliant draft in the project, so the refusal is
    conditioned on `violates` and this pins that it is.
    """
    clean = Verdict(violates=False, confidence=0.9)
    checker = Checker("sonnet-tier", "sonnet-tier", transport=lambda m: clean, retries=0)
    assert checker.check(DRAFT, RECORD) == clean


def test_the_model_strength_table_cannot_be_mutated_in_place():
    """An in-place write used to disarm the tier floor for the whole process.

    The floor is what keeps a budget choice from confounding architecture with model capability
    in the attribution ladder, so it should not be undone by a stray statement.

    **Rebinding is the residual and this does not close it.** `assert_checker_not_weaker` reads
    the module global when it is called, so `checker.MODEL_STRENGTH = {...}` still lowers the
    floor; only mutation through the existing object is refused. What is closed is the realistic
    accidental path -- an extra tier registered with `MODEL_STRENGTH["some-tier"] = 4` -- not a
    determined one. The name says "in place" because that is all that is held.
    """
    with pytest.raises(TypeError):
        MODEL_STRENGTH["haiku-tier"] = 99
    assert MODEL_STRENGTH["haiku-tier"] == 1
    with pytest.raises(ValueError, match="not be weaker"):
        assert_checker_not_weaker("haiku-tier", "opus-tier")
