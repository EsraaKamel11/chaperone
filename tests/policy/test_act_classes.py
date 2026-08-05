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


# Large enough that applying its multiplier overflows the decimal context.
_TOO_LARGE_TO_SCALE = "9" * 999_999 + "m"


def test_an_unrepresentable_figure_yields_findings_rather_than_raising():
    """A predicate that raises is not a predicate.

    An exception crossing this boundary is not a finding: an executor written to be forgiving
    can relabel it "transient, please retry" and retry the very act the predicate exists to
    stop. So the figure is skipped, and the rest of the body is still judged.

    Skipping it does drop a figure, which canonicalization otherwise refuses to do -- see the
    documented limit on multiplier spellings. This is the lesser of two escapes, not an
    acceptable outcome, and it is not forced: the bare digits here are representable, so
    truncating the unscalable multiplier, exactly as an unrecognised spelling already does,
    would close it. That widens what `figures_in` promises, so it is not done here.
    """
    draft = _draft(body=f"The round is {_TOO_LARGE_TO_SCALE}, up from $40M.")
    findings = evaluate_act_classes(draft, RECORD, _context())
    assert [f.violation_class for f in findings] == [ViolationClass.FIGURE_NOT_IN_RECORD]
    assert findings[0].detail == "40000000"


def test_findings_arrive_in_a_fixed_order():
    """Order is a contract, not an accident of how the predicates happen to be written."""
    draft = _draft(body="The round is $40M.", recipient_jurisdiction="DE", tool_name="wire_funds")
    context = _context(approval_token=None, sent_count=50, send_cap=50)
    assert [f.violation_class for f in evaluate_act_classes(draft, RECORD, context)] == [
        ViolationClass.NO_APPROVAL_TOKEN,
        ViolationClass.JURISDICTION_NOT_CONSENTED,
        ViolationClass.TOOL_OUTSIDE_GRANT,
        ViolationClass.FIGURE_NOT_IN_RECORD,
        ViolationClass.SEND_CAP_EXCEEDED,
    ]


def test_multiple_absent_figures_arrive_in_ascending_numeric_order():
    """Ascending by value, not by the order a set happens to yield nor by string comparison.

    Lexicographically these details sort ["2000000", "300000", "40000000", "9000000"], so an
    accidental string ordering fails this, and so does dropping the `sorted()` call.
    """
    draft = _draft(body="We raised $40M, then $2M, then $9M, then $300k.")
    findings = evaluate_act_classes(draft, RECORD, _context())
    assert [f.detail for f in findings] == ["300000", "2000000", "9000000", "40000000"]
