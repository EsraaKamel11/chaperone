import json

import pytest

from chaperone.gates.checker import (
    Checker, CheckerUnavailable, FlagForReview, Verdict,
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


# --- Four guards below, one per mutant that the eleven tests above were measured to survive. ---
# --- Each was watched failing against that mutant applied to the shipped module. ---


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
    for message in build_checker_messages(DRAFT, RECORD):
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
