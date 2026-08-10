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
    blob = json.dumps(build_checker_messages(draft=DRAFT, record=RECORD))
    assert "honestly, is this a good deal?" in blob
    assert "Here are the round details." in blob


def test_the_checker_prompt_contains_the_cited_records():
    blob = json.dumps(build_checker_messages(draft=DRAFT, record=RECORD))
    assert "10000000" in blob


def test_the_checker_prompt_has_no_generator_artefacts():
    blob = json.dumps(build_checker_messages(draft=DRAFT, record=RECORD))
    for marker in ("<thinking>", "toolu_", "You are a drafting agent", "scratchpad", "chain of thought"):
        assert marker not in blob


def test_the_checker_prompt_structure_is_exact():
    """Absence-scanning alone passes an injected turn that dodges the markers."""
    messages = build_checker_messages(draft=DRAFT, record=RECORD)
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
    blob = json.dumps(build_checker_messages(draft=DRAFT, record=RECORD))
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
    blob = json.dumps(build_checker_messages(draft=draft, record=record))
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
    messages = build_checker_messages(draft=DRAFT, record=RECORD)
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


# --- The two closures below bind the pair nothing bound. Neither is a mutant guard and neither is
# --- covered by the banner above: they hold couplings that no behavioural test can see, because
# --- both failure modes leave every assertion in this file green. The instruction text and the
# --- content half of `ViolationClass` are a pair a single edit can separate, and
# --- `build_checker_messages` is a function whose parameter list a later caller can widen
# --- positionally. Neither closes the survivor the banner above names -- a justification sentence
# --- appended to `CHECKER_INSTRUCTIONS` still survives, since closing that means pinning the whole
# --- instruction string, and the instruction string is frozen under 160 recorded verdicts rather
# --- than asserted.


def test_checker_instructions_name_every_content_class_and_nothing_else():
    """Coverage is derived from the enum; the phrases are hand-chosen; the count is pinned twice.

    The instruction text itself is frozen (the recorded verdicts were produced under these
    exact bytes), so the map is checked against it, never composed into it.

    **The last two assertions are a pair of literals and not an equality**, which the wording here
    used to obscure. Nothing reads a number out of the instruction and compares it to `len()`; what
    holds "no other class is described as checkable" is that a fourth content class reddens the key
    comparison and the length pin together, while an instruction that renamed its own count reddens
    the substring pin. Composing the number into the frozen string is the one repair not available.
    """
    from chaperone.gates.checker import CHECKER_INSTRUCTIONS, CONTENT_CLASS_PHRASES
    from chaperone.policy.types import Family, ViolationClass

    content = {m for m in ViolationClass if m.family is Family.CONTENT}
    assert set(CONTENT_CLASS_PHRASES) == content
    for member, phrase in CONTENT_CLASS_PHRASES.items():
        assert phrase in CHECKER_INSTRUCTIONS, member
    assert "three" in CHECKER_INSTRUCTIONS
    assert len(CONTENT_CLASS_PHRASES) == 3


def test_build_checker_messages_is_keyword_only():
    """Independence enforced by the signature: a positional channel cannot exist.

    The name check is a vacuity tripwire, not decoration: `parameters` is a mapping, and a builder
    that took no arguments at all would run the loop zero times and pass. Same shape as
    `test_the_checker_message_carries_exactly_a_role_and_a_content_key` above, for the same reason.
    """
    import inspect
    from chaperone.gates.checker import build_checker_messages

    params = inspect.signature(build_checker_messages).parameters
    assert set(params) == {"draft", "record"}, params
    for name, param in params.items():
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, name


# --- The two rules the gate applies to a well-formed verdict, extracted as predicates so that one
# --- of them can be enforced twice. `unusable_reason` is shared rather than moved: `_reject_unusable`
# --- keeps calling it, and a transport binding can raise from the same predicate rather than
# --- reimplementing it. `span_absent_reason` is gate-side only -- at the transport seam the draft is
# --- out of scope, so nothing there can even evaluate it.


def test_unusable_reason_names_the_missing_class_and_passes_flags_through():
    """Production change that breaks this: deleting the class check from `unusable_reason`.

    The clean verdict and the review flag are the two shapes the rule must *not* claim, and each is
    asserted separately: a predicate that returned a reason unconditionally would deny every
    compliant draft in the project, and one that read `.violates` off a `FlagForReview` would raise
    instead of answering.
    """
    from chaperone.gates.checker import FlagForReview, Verdict, unusable_reason

    bad = Verdict(violates=True, violation_class=None, confidence=0.9, span=None)
    assert unusable_reason(bad) is not None
    ok = Verdict(violates=False, violation_class=None, confidence=0.9, span=None)
    assert unusable_reason(ok) is None
    assert unusable_reason(FlagForReview(reason="unclear")) is None


def test_span_absent_reason_refuses_a_span_not_in_the_body():
    """Production change that breaks this: widening the rule from `in` to anything looser.

    The first assertion is the one that keeps the rule usable -- a quoted span that *is* in the body
    is the ordinary case and must not be refused -- and the third holds the shape a `FlagForReview`
    has no field for. Verbatim is the whole property: a rule that matched case-insensitively, or on
    a normalized form, would let the checker quote a sentence the draft does not contain, and the
    handoff renders that span to a human as the offending words.
    """
    from chaperone.gates.checker import FlagForReview, Verdict, span_absent_reason

    v = Verdict(violates=True, violation_class=ViolationClass.ADVISES_ON_MERITS,
                confidence=0.9, span="great deal")
    assert span_absent_reason(v, "this is a great deal, honestly") is None
    assert span_absent_reason(v, "no such words here") is not None
    assert span_absent_reason(FlagForReview(reason="x"), "anything") is None


def test_a_verdict_quoting_a_span_the_draft_does_not_contain_exhausts_the_budget_and_then_denies():
    """A predicate nothing calls governs nothing, so the enforcement is asserted, not the rule.

    The two tests above hold `span_absent_reason` as a function. Neither of them would notice
    `Checker.check` never calling it, and an unenforced span rule is exactly the shape this project
    keeps meeting: the checker quotes words the draft does not contain, the verdict is returned
    intact, and `build_handoff` renders that span to a human as the offending text.

    Production change that breaks this: deleting the `span_absent_reason` call from `Checker.check`.
    The call count is the second half and catches a different edit -- hoisting the check above the
    loop, or into `Checker.__init__`, raises the same error after one attempt, and a `pytest.raises`
    alone cannot tell that from a rule applied where the budget lives.
    """
    calls = []

    def quotes_an_absent_span(messages):
        calls.append(1)
        return Verdict(violates=True, violation_class=ViolationClass.ADVISES_ON_MERITS,
                       confidence=0.9, span="a bargain at twice the price")

    checker = Checker("sonnet-tier", "sonnet-tier", transport=quotes_an_absent_span, retries=2)
    with pytest.raises(CheckerUnavailable, match="verbatim substring"):
        checker.check(DRAFT, RECORD)
    assert len(calls) == 3, "the span rule must be applied where the retry budget is spent"


def test_a_transport_that_declares_terminal_failure_is_not_retried():
    """The retry budget is for *availability*, and a transport can say the question is closed.

    `CheckerUnavailable` raised by the transport is a declaration, not a symptom: the replay in
    `testing/recorded.py` raises it for a recorded unavailability, and retrying that asks the same
    settled question two more times. Everything else the loop catches is a candidate for another
    attempt, so the escape is narrowed to this one type rather than to the loop as a whole.

    **The call count is the killer and the `match=` is not.** Measured before the clause existed:
    with the loop swallowing this, `str(last)` is the terminal exception's own message, so the
    re-wrapped `CheckerUnavailable` carries the identical text and the `match=` passes over three
    calls exactly as it passes over one. Production change that breaks this: deleting the
    `except CheckerUnavailable: raise` clause above the bare except.
    """
    calls = []

    def declares_terminal(messages):
        calls.append(1)
        raise CheckerUnavailable("declared terminal by the transport")

    checker = Checker("sonnet-tier", "sonnet-tier", declares_terminal, retries=2)
    with pytest.raises(CheckerUnavailable, match="declared terminal"):
        checker.check(DRAFT, RECORD)
    assert len(calls) == 1
