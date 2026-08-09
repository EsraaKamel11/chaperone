import dataclasses

import pytest
from pydantic import ValidationError

from chaperone.gates.checker import Checker, FlagForReview, Verdict
from chaperone.gates.engine import decide
from chaperone.gates.handoff import FIELD_NOT_IN_RECORD, Handoff, build_handoff
from chaperone.policy.act_classes import ActContext
from chaperone.policy.types import Decision, Disposition, Draft, Finding, Message, Record, ViolationClass

RECORD = Record(fields={"round_size": "10000000", "stage": "Series A"})
DRAFT = Draft(
    thread=(Message(role="investor", body="honestly, is this a good deal?"),),
    body="In my view it is a strong opportunity.", cited_fields=("round_size",),
    recipient_jurisdiction="US", recipient_domain="example.test", tool_name="send_message",
)
DECISION = Decision(
    allowed=False,
    findings=(Finding(ViolationClass.ADVISES_ON_MERITS, "checker confidence 0.91", "a strong opportunity"),),
    disposition=Disposition.REDIRECT_FUTILE,
)


def test_the_handoff_is_readable_without_the_transcript():
    handoff = build_handoff(DRAFT, RECORD, DECISION, alternative=None, rounds=0)
    blob = handoff.model_dump_json()
    for phrase in ("as discussed", "see above", "earlier in the conversation"):
        assert phrase not in blob.lower()
    assert handoff.blocked_body == DRAFT.body
    assert handoff.recipient_domain == "example.test"


def test_every_field_the_reviewer_reads_is_required():
    """A loose schema means the escalation arrives empty."""
    with pytest.raises(ValidationError):
        Handoff(reason_category="content:advises_on_merits")


def test_cited_field_values_are_resolved_not_referenced():
    handoff = build_handoff(DRAFT, RECORD, DECISION, alternative=None, rounds=0)
    assert handoff.cited_field_values == {"round_size": "10000000"}


def test_the_violating_span_is_carried_verbatim():
    handoff = build_handoff(DRAFT, RECORD, DECISION, alternative=None, rounds=0)
    assert handoff.violating_span == "a strong opportunity"


def test_a_futile_class_carries_a_deflection_at_zero_rounds():
    handoff = build_handoff(DRAFT, RECORD, DECISION, alternative="I cannot advise on merits, but here are the facts.", rounds=0)
    assert handoff.refinement_rounds == 0
    assert handoff.proposed_alternative is not None


# ---------------------------------------------------------------------------
# What the two tests above do NOT establish, and the tests that do.
# ---------------------------------------------------------------------------


def test_omitting_any_single_field_is_refused():
    """`Handoff(reason_category=...)` raising proves *one* field is required, not ten.

    Nine of the ten could carry a default and the one-argument construction above would still
    raise. Design spec 4.7 needs every field the reviewer reads, so every field is dropped in turn
    from a complete payload and each drop has to be refused on its own. The payload is built from
    the model's own field list, so a field added later is covered without editing this test --
    and the two `str | None` fields, `proposed_alternative` and `detector_outage`, are the ones a
    default would look most natural on.
    """
    complete = build_handoff(DRAFT, RECORD, DECISION, alternative="a deflection", rounds=1).model_dump()
    assert set(complete) == set(Handoff.model_fields), "the complete payload is missing a field"
    accepted = []
    for name in sorted(complete):
        try:
            Handoff(**{k: v for k, v in complete.items() if k != name})
        except ValidationError:
            continue
        accepted.append(name)
    assert accepted == [], f"fields that are optional and should not be: {accepted}"


def test_a_field_the_schema_does_not_declare_is_refused():
    """The other half of "every field is required": nothing undeclared may ride along.

    A misspelled field name is otherwise a silently ignored keyword sitting beside the empty field
    the reviewer needed -- the loose-schema failure this module is named for, arriving by addition
    instead of by omission.
    """
    complete = build_handoff(DRAFT, RECORD, DECISION, alternative=None, rounds=0).model_dump()
    with pytest.raises(ValidationError):
        Handoff(**complete, reviewer_note="ride along")


def test_every_field_traces_to_the_draft_the_record_or_the_decision():
    """Self-containment stated as provenance rather than as an absent-phrase check.

    `test_the_handoff_is_readable_without_the_transcript` searches the blob for "as discussed",
    "see above" and "earlier in the conversation". Those are absent because nothing writes them,
    so the check holds no field to any source -- and `thread_excerpt` copies investor prose
    verbatim, so an investor who opens with "as discussed" fails it on a correct handoff. This
    pins each field to the argument it came from instead, which is what 4.7 actually asks: the
    reviewer cannot see the conversation, so nothing may be reachable only from there.

    **`detector_outage` needs a second decision, and the first version of this did not have one.**
    `DECISION` carries no outage, so `handoff.detector_outage == DECISION.outage` was `None == None`
    -- satisfied by `build_handoff` hardcoding `None` and never reading the decision at all. A
    vacuous assertion in a test named for provenance is worse than an absent one, so the field is
    traced from a decision that has an outage and the empty case is asserted beside it.
    """
    handoff = build_handoff(DRAFT, RECORD, DECISION, alternative="a deflection", rounds=2)
    assert handoff.reason_category == ViolationClass.ADVISES_ON_MERITS.value
    assert handoff.violating_span == DECISION.findings[0].span
    assert handoff.blocked_body == DRAFT.body
    assert handoff.recipient_domain == DRAFT.recipient_domain
    assert handoff.recipient_jurisdiction == DRAFT.recipient_jurisdiction
    assert handoff.cited_field_values == {"round_size": RECORD.fields["round_size"]}
    assert handoff.thread_excerpt == "[investor] honestly, is this a good deal?"
    assert handoff.proposed_alternative == "a deflection"
    assert handoff.refinement_rounds == 2
    assert handoff.detector_outage is None

    outaged = dataclasses.replace(DECISION, outage="transport down")
    assert build_handoff(DRAFT, RECORD, outaged, alternative="a deflection", rounds=2) \
        .detector_outage == outaged.outage


def test_the_category_is_the_class_value_and_not_the_member_name():
    """`f"{ViolationClass.ADVISES_ON_MERITS}"` is `"ViolationClass.ADVISES_ON_MERITS"` on 3.11+.

    `ViolationClass` subclasses `str`, so `==` against the value passes against a bare member too
    and the equality above asserts nothing about this. The runtime type and the formatted form are
    what tell them apart, and a handoff is serialized before a human reads it.
    """
    handoff = build_handoff(DRAFT, RECORD, DECISION, alternative=None, rounds=0)
    assert type(handoff.reason_category) is str
    assert f"{handoff.reason_category}" == "content:advises_on_merits"


def _flagging_checker() -> Checker:
    return Checker("sonnet-tier", "sonnet-tier", transport=lambda m: FlagForReview(reason="unclear"), retries=0)


CONTEXT = ActContext(approval_token="tok", tier=2, consented_jurisdictions=frozenset({"US"}),
                     granted_tools=frozenset({"send_message"}), sent_count=0, send_cap=50)


def test_a_span_carried_by_a_later_finding_still_reaches_the_reviewer():
    """`findings[0]` is not always the finding that holds the offending text.

    `decide` returns `(Finding(OTHER, "flagged for review: ...", None),) + tripwire_findings` on
    the flag-for-review route, so the first finding carries no span while the second quotes the
    draft exactly. Reading `findings[0].span or ""` sent the reviewer an empty span with the text
    sitting one element away -- 4.7's "arrives empty" in the field 4.7 names first.

    The category still comes from `findings[0]`, matching `denial_result`, so the two agree on
    what kind of failure this is and differ only in how hard they look for the quote.
    """
    decision = decide(
        Draft(thread=(Message(role="investor", body="?"),), body="Returns are guaranteed.",
              cited_fields=(), recipient_jurisdiction="US", recipient_domain="example.test",
              tool_name="send_message"),
        RECORD, CONTEXT, _flagging_checker(),
    )
    assert [f.span for f in decision.findings] == [None, "guaranteed"], "the fixture stopped reproducing the shape"
    handoff = build_handoff(DRAFT, RECORD, decision, alternative=None, rounds=0)
    assert handoff.violating_span == "guaranteed"
    assert handoff.reason_category == ViolationClass.OTHER.value


def test_a_verdict_that_names_a_class_without_quoting_anything_still_yields_a_span():
    """The third route, and the one the `OTHER`-prepending shape does not describe.

    `Verdict.span` defaults to `None` and `_reject_unusable` refuses only a violation with no
    *class*, so a checker may name `content:negotiates_terms` and quote nothing. That finding is
    built first, and `checker_findings + tripwire_findings` then puts it ahead of a tripwire that
    did quote the draft. Counted by grepping `+ tripwire_findings`, not recalled.
    """
    verdict = Verdict(violates=True, violation_class=ViolationClass.NEGOTIATES_TERMS, confidence=0.7, span=None)
    checker = Checker("sonnet-tier", "sonnet-tier", transport=lambda m: verdict, retries=0)
    decision = decide(
        Draft(thread=(Message(role="investor", body="?"),), body="Returns are guaranteed.",
              cited_fields=(), recipient_jurisdiction="US", recipient_domain="example.test",
              tool_name="send_message"),
        RECORD, CONTEXT, checker,
    )
    assert [f.span for f in decision.findings] == [None, "guaranteed"], "the fixture stopped reproducing the shape"
    handoff = build_handoff(DRAFT, RECORD, decision, alternative=None, rounds=0)
    assert handoff.violating_span == "guaranteed"
    assert handoff.reason_category == ViolationClass.NEGOTIATES_TERMS.value


#: `DRAFT` with its citation dropped, and the three tests below cannot use `DRAFT` itself.
#:
#: `DRAFT` cites `round_size` and its body quotes no figure, so `validate_citations` denies it
#: `act:figure_not_in_record` and `decide` returns on the act lane -- **before any checker is
#: consulted**. A test about what a checker answer becomes has to reach one, so the citation goes.
#: Measured rather than reasoned: the first version of these tests decided over `DRAFT` and read
#: back `act:figure_not_in_record` from all three.
UNCITED_DRAFT = dataclasses.replace(DRAFT, cited_fields=())


def _unavailable_checker() -> Checker:
    """A transport that never answers. `Checker.check` turns that into `CheckerUnavailable`."""
    def transport(_):
        raise TimeoutError("transport down")

    return Checker("sonnet-tier", "sonnet-tier", transport=transport, retries=0)


def _judging_checker(klass: ViolationClass) -> Checker:
    """A checker that answered, and answered "violates". The span is quoted from `DRAFT.body`,
    because `Checker.check` refuses a span the body does not contain verbatim."""
    verdict = Verdict(violates=True, violation_class=klass, confidence=0.9,
                      span="a strong opportunity")
    return Checker("sonnet-tier", "sonnet-tier", transport=lambda m: verdict, retries=0)


def test_the_handoff_names_the_outage_when_no_detector_answered():
    """Design spec 4.7's field for the one thing the payload could not say.

    The gate denies for two unlike reasons -- a detector judged the draft, or no detector answered
    and the gate failed closed -- and both arrived as the same denial. `Finding.detail` spelled the
    difference out in prose, and `Handoff` carries no `detail`, so the distinction reached the
    reviewer nowhere at all.

    Built through `decide` rather than from a hand-assembled `Decision`, so what is pinned is the
    predicate's effect. The message is asserted rather than mere presence: a boolean would tell the
    reviewer that something was down without telling them what, and `str` of the exception is the
    only thing that carries it.
    """
    decision = decide(UNCITED_DRAFT, RECORD, CONTEXT, _unavailable_checker())
    handoff = build_handoff(UNCITED_DRAFT, RECORD, decision, alternative=None, rounds=0)
    assert "transport down" in handoff.detector_outage


def test_a_judged_violation_hands_off_with_no_outage():
    """The other value, and the one that keeps the field from meaning "denied".

    A detector that answered and found a violation is not an outage. Without this the field could be
    populated on every denial and the test above would still pass, while the reviewer read every
    block as downtime -- which is the same undistinguished payload, relabelled.
    """
    decision = decide(UNCITED_DRAFT, RECORD, CONTEXT, _judging_checker(ViolationClass.ADVISES_ON_MERITS))
    handoff = build_handoff(UNCITED_DRAFT, RECORD, decision, alternative=None, rounds=0)
    assert handoff.reason_category == ViolationClass.ADVISES_ON_MERITS.value
    assert handoff.detector_outage is None


def test_the_reason_category_cannot_tell_the_two_apart_and_the_outage_field_can():
    """Why the field is needed rather than derivable from what the payload already carried.

    `other` is carried by **four** routes in `decide`: three plumbing failures and one considered
    verdict -- `_reject_unusable` refuses a violation with *no* class and does not refuse one classed
    `other`, so a checker may name it and mean it. An escalation whose `reason_category` is `other`
    could have come from any of the four, and nothing in the payload said which. The pair below is
    the one this field resolves: an unavailable detector and a detector that judged.

    **It resolves that one pair and no other.** With no outage, an `other` escalation is still any of
    the remaining three -- flagged for review, a violation with no class, or a considered `other`
    verdict -- and `Finding.detail` is what would tell those apart. The earlier wording here offered
    "down, or judged" as if it were exhaustive, which made a null read as *judged*.

    **Not the only field that differs here, and the other one is not a substitute.**
    `violating_span` differs too, because the judging verdict quotes the body while the unavailable
    branch has nothing to quote. Reading it that way would be wrong:
    `test_no_finding_carrying_a_span_leaves_the_span_empty_rather_than_guessing` builds a denial no
    detector was unavailable for and it arrives with an empty span, so an empty span does not mean
    an outage. The category cannot separate the two and the span must not be asked to.
    """
    down = build_handoff(UNCITED_DRAFT, RECORD,
                         decide(UNCITED_DRAFT, RECORD, CONTEXT, _unavailable_checker()),
                         alternative=None, rounds=0)
    judged = build_handoff(
        UNCITED_DRAFT, RECORD,
        decide(UNCITED_DRAFT, RECORD, CONTEXT, _judging_checker(ViolationClass.OTHER)),
        alternative=None, rounds=0)
    assert down.reason_category == judged.reason_category == ViolationClass.OTHER.value
    assert down.detector_outage is not None
    assert judged.detector_outage is None


def test_no_finding_carrying_a_span_leaves_the_span_empty_rather_than_guessing():
    """The other direction of the same edit: a decision with no span anywhere invents none."""
    decision = Decision(
        allowed=False,
        findings=(Finding(ViolationClass.NO_APPROVAL_TOKEN, "tier 2 requires an approval token", None),),
        disposition=Disposition.REDIRECT_FUTILE,
    )
    assert build_handoff(DRAFT, RECORD, decision, alternative=None, rounds=0).violating_span == ""


def test_a_cited_field_absent_from_the_record_is_named_rather_than_dropped():
    """The fabricated citation is the one the reviewer most needs to see, and it vanished.

    `if (value := record.get(name)) is not None` drops exactly the field that produced
    `act:figure_not_in_record`, so the escalation reached a human showing only the citations that
    resolved. `Handoff` carries no `detail`, so with the field dropped there was nothing left in
    the payload naming it at all.
    """
    draft = dataclasses.replace(DRAFT, cited_fields=("round_size", "valuation"))
    handoff = build_handoff(draft, RECORD, DECISION, alternative=None, rounds=0)
    assert set(handoff.cited_field_values) == {"round_size", "valuation"}
    assert handoff.cited_field_values["round_size"] == "10000000"
    assert handoff.cited_field_values["valuation"] == FIELD_NOT_IN_RECORD


def test_a_denial_carrying_no_finding_has_no_handoff():
    """A stated limit, mirroring `denial_result`'s.

    `build_handoff` indexes `findings[0]`, so a `Decision(False, (), ...)` assembled by hand
    raises `IndexError`. `decide` produces no such denial -- a battery test in test_engine.py
    holds that -- and this pins the boundary rather than papering over it with an empty handoff,
    which is the one outcome 4.7 rules out.
    """
    empty = Decision(allowed=False, findings=(), disposition=Disposition.REDIRECT_FUTILE)
    with pytest.raises(IndexError):
        build_handoff(DRAFT, RECORD, empty, alternative=None, rounds=0)
