from chaperone.policy.act_classes import ActContext, evaluate_act_classes
from chaperone.policy.types import Draft, Message, Record, ViolationClass


def _draft(**overrides) -> Draft:
    base = dict(
        thread=(Message(role="investor", body="hello"),),
        body="The round is $10M.",
        cited_fields=("round_size",),
        recipient_jurisdiction="US",
        recipient_domain="example.test",
        tool_name="send_message",
    )
    base.update(overrides)
    return Draft(**base)


def _context(**overrides) -> ActContext:
    base = dict(
        approval_token="tok",
        tier=2,
        consented_jurisdictions=frozenset({"US"}),
        granted_tools=frozenset({"send_message"}),
        sent_count=0,
        send_cap=50,
    )
    base.update(overrides)
    return ActContext(**base)


RECORD = Record(fields={"round_size": "10000000"})


def test_a_compliant_draft_produces_no_findings():
    assert evaluate_act_classes(_draft(), RECORD, _context()) == ()


def test_a_tier_two_send_without_an_approval_token_is_a_finding():
    findings = evaluate_act_classes(_draft(), RECORD, _context(approval_token=None))
    assert [f.violation_class for f in findings] == [ViolationClass.NO_APPROVAL_TOKEN]


def test_a_non_consented_jurisdiction_is_a_finding():
    findings = evaluate_act_classes(_draft(recipient_jurisdiction="DE"), RECORD, _context())
    assert ViolationClass.JURISDICTION_NOT_CONSENTED in [f.violation_class for f in findings]


def test_a_tool_outside_the_grant_is_a_finding():
    findings = evaluate_act_classes(_draft(tool_name="wire_funds"), RECORD, _context())
    assert ViolationClass.TOOL_OUTSIDE_GRANT in [f.violation_class for f in findings]


def test_a_figure_absent_from_the_record_is_a_finding_naming_the_figure():
    findings = evaluate_act_classes(_draft(body="The round is $40M."), RECORD, _context())
    matching = [f for f in findings if f.violation_class is ViolationClass.FIGURE_NOT_IN_RECORD]
    assert len(matching) == 1
    assert "40000000" in matching[0].detail


def test_a_figure_written_differently_from_the_record_still_matches():
    findings = evaluate_act_classes(_draft(body="We are raising $10m."), RECORD, _context())
    assert findings == ()


def test_the_cap_predicate_is_pure_over_draft_and_count():
    at_cap = evaluate_act_classes(_draft(), RECORD, _context(sent_count=50, send_cap=50))
    assert ViolationClass.SEND_CAP_EXCEEDED in [f.violation_class for f in at_cap]
    under_cap = evaluate_act_classes(_draft(), RECORD, _context(sent_count=49, send_cap=50))
    assert ViolationClass.SEND_CAP_EXCEEDED not in [f.violation_class for f in under_cap]


def test_the_same_inputs_always_produce_the_same_findings():
    args = (_draft(recipient_jurisdiction="DE"), RECORD, _context(approval_token=None))
    assert evaluate_act_classes(*args) == evaluate_act_classes(*args)
