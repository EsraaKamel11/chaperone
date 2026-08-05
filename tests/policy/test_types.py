import pytest
from chaperone.policy.types import (
    Decision, Disposition, Draft, Family, Finding, Message, Record, ViolationClass,
)


def test_act_classes_and_content_classes_report_their_family():
    assert ViolationClass.JURISDICTION_NOT_CONSENTED.family is Family.ACT
    assert ViolationClass.FIGURE_NOT_IN_RECORD.family is Family.ACT
    assert ViolationClass.ADVISES_ON_MERITS.family is Family.CONTENT
    assert ViolationClass.NEGOTIATES_TERMS.family is Family.CONTENT


def test_the_open_enum_carries_an_other_member_outside_both_families():
    assert ViolationClass.OTHER.family is Family.UNCLASSIFIED


def test_a_finding_on_other_requires_detail_text():
    with pytest.raises(ValueError, match="detail"):
        Finding(violation_class=ViolationClass.OTHER, detail=None, span=None)


def test_types_are_frozen_so_a_decision_cannot_be_edited_after_construction():
    decision = Decision(allowed=True, findings=(), disposition=Disposition.ALLOW)
    with pytest.raises(AttributeError):
        decision.allowed = False


def test_a_record_returns_none_for_an_absent_field_rather_than_a_default():
    record = Record(fields={"round_size": "10000000"})
    assert record.get("round_size") == "10000000"
    assert record.get("valuation") is None


def test_a_draft_carries_its_transmitted_thread():
    draft = Draft(
        thread=(Message(role="investor", body="is this a good deal?"),),
        body="Here is the memo.",
        cited_fields=("round_size",),
        recipient_jurisdiction="US",
        recipient_domain="example.test",
        tool_name="send_message",
    )
    assert len(draft.thread) == 1
    assert draft.thread[0].role == "investor"


def test_a_finding_outside_other_is_accepted_without_detail_text():
    finding = Finding(
        violation_class=ViolationClass.FIGURE_NOT_IN_RECORD,
        detail=None,
        span="a $40m valuation",
    )
    assert finding.violation_class is ViolationClass.FIGURE_NOT_IN_RECORD
    assert finding.detail is None
    assert finding.span == "a $40m valuation"


# Transcribed from the task brief's implementation block, not from the module under test: these
# wire values are the contract the downstream tasks read, and the act:/content: prefixes are what
# ViolationClass.family derives the family from.
@pytest.mark.parametrize(
    "member, expected_value, expected_family",
    [
        (ViolationClass.NO_APPROVAL_TOKEN, "act:no_approval_token", Family.ACT),
        (ViolationClass.JURISDICTION_NOT_CONSENTED, "act:jurisdiction_not_consented", Family.ACT),
        (ViolationClass.TOOL_OUTSIDE_GRANT, "act:tool_outside_grant", Family.ACT),
        (ViolationClass.FIGURE_NOT_IN_RECORD, "act:figure_not_in_record", Family.ACT),
        (ViolationClass.SEND_CAP_EXCEEDED, "act:send_cap_exceeded", Family.ACT),
        (ViolationClass.ADVISES_ON_MERITS, "content:advises_on_merits", Family.CONTENT),
        (ViolationClass.NEGOTIATES_TERMS, "content:negotiates_terms", Family.CONTENT),
        (ViolationClass.FORWARD_LOOKING_RETURN, "content:forward_looking_return", Family.CONTENT),
        (ViolationClass.OTHER, "other", Family.UNCLASSIFIED),
    ],
)
def test_every_violation_class_pins_its_value_and_the_family_that_value_yields(
    member, expected_value, expected_family
):
    assert member.value == expected_value
    assert member.family is expected_family
