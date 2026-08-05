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
