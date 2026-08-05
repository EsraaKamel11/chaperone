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
    """`Handoff(reason_category=...)` raising proves *one* field is required, not nine.

    Eight of the nine could carry a default and the one-argument construction above would still
    raise. Design spec 4.7 needs every field the reviewer reads, so every field is dropped in turn
    from a complete payload and each drop has to be refused on its own. The payload is built from
    the model's own field list, so a field added later is covered without editing this test --
    and `proposed_alternative: str | None` is the one a default would look most natural on.
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


def test_every_field_traces_to_the_draft_the_record_or_the_decision():
    """Self-containment stated as provenance rather than as an absent-phrase check.

    `test_the_handoff_is_readable_without_the_transcript` searches the blob for "as discussed",
    "see above" and "earlier in the conversation". Those are absent because nothing writes them,
    so the check holds no field to any source -- and `thread_excerpt` copies investor prose
    verbatim, so an investor who opens with "as discussed" fails it on a correct handoff. This
    pins each field to the argument it came from instead, which is what 4.7 actually asks: the
    reviewer cannot see the conversation, so nothing may be reachable only from there.
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
