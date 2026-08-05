from chaperone.policy.citations import validate_citations
from chaperone.policy.types import Draft, Message, Record, ViolationClass

RECORD = Record(fields={"round_size": "10000000", "stage": "Series A"})


def _draft(body: str, cited: tuple[str, ...]) -> Draft:
    return Draft(
        thread=(Message(role="investor", body="tell me about the round"),),
        body=body, cited_fields=cited, recipient_jurisdiction="US",
        recipient_domain="example.test", tool_name="send_message",
    )


def test_a_citation_to_a_real_field_whose_value_appears_is_accepted():
    assert validate_citations(_draft("The round is $10M.", ("round_size",)), RECORD) == ()


def test_a_citation_to_a_field_that_does_not_exist_is_rejected_with_its_index():
    findings = validate_citations(_draft("The round is $10M.", ("valuation",)), RECORD)
    assert len(findings) == 1
    assert findings[0].violation_class is ViolationClass.FIGURE_NOT_IN_RECORD
    assert "index 0" in findings[0].detail


def test_a_fabricated_claim_containing_a_field_name_is_rejected():
    """The substring trap: 'contains a field token' is not 'cites a real value'."""
    body = "I made this up entirely, no round_size involved."
    findings = validate_citations(_draft(body, ("round_size",)), RECORD)
    assert len(findings) == 1


def test_a_cited_value_written_in_a_different_representation_is_accepted():
    assert validate_citations(_draft("Raising $10m.", ("round_size",)), RECORD) == ()
    assert validate_citations(_draft("Raising 10,000,000.", ("round_size",)), RECORD) == ()


def test_a_non_numeric_field_matches_on_its_literal_text():
    assert validate_citations(_draft("This is a Series A.", ("stage",)), RECORD) == ()


def test_a_non_numeric_field_whose_text_is_absent_is_rejected():
    findings = validate_citations(_draft("This is a seed round.", ("stage",)), RECORD)
    assert len(findings) == 1


def test_the_offending_index_is_reported_for_the_second_citation():
    findings = validate_citations(_draft("Raising $10M.", ("round_size", "valuation")), RECORD)
    assert len(findings) == 1
    assert "index 1" in findings[0].detail


def test_every_bad_citation_is_reported_in_order_with_its_own_index():
    """One finding per bad citation, each naming its own index and the value that was missing.

    Three things above are invisible to the tests before it, and each is an under-report -- the
    escaping direction. No draft above carries two *bad* citations, so `break` where the
    missing-field branch writes `continue` reports the first and silently drops the rest. No
    draft above checks the index on either *value* path, so a constant "index 0" there passes
    unnoticed, and design spec 4.5 rejects "with the offending index". And nothing above reads
    the value out of a detail, so a message that named only the field would still look right.

    Index 1 fails through the canonical path and index 2 through the degraded one, so the
    rendering of both is pinned here. The expected strings are literals, written by hand and
    never recomputed through `normalize_money` -- a detail rebuilt the way the code builds it
    would agree with the code by construction.
    """
    findings = validate_citations(
        _draft("Raising $40M.", ("valuation", "round_size", "stage", "arr")), RECORD
    )
    assert [f.detail for f in findings] == [
        "index 0: field 'valuation' is not in the record",
        "index 1: field 'round_size' value 10000000 does not appear in the draft",
        "index 2: field 'stage' value 'Series A' does not appear in the draft",
        "index 3: field 'arr' is not in the record",
    ]


def test_every_rejection_path_carries_the_class_the_gate_dispatches_on():
    """All three rejections must classify identically, because the class is what routes.

    The class is asserted above on the missing-field path alone. The two value paths could
    carry any class at all and the suite would stay green -- and the class, not the wording, is
    what the boundary engine turns into a disposition and what the denial contract publishes as
    its category. Two paths naming one policy is the shape that drifts.

    The counts are asserted alongside the classes because reading `[0]` from three non-empty
    tuples proves only that something was reported, not that each draft was reported once.
    """
    drafts = [
        _draft("The round is $10M.", ("valuation",)),         # the field is not in the record
        _draft("I made this up entirely.", ("round_size",)),  # a numeric value that is absent
        _draft("This is a seed round.", ("stage",)),          # a textual value that is absent
    ]
    findings = [validate_citations(draft, RECORD) for draft in drafts]
    assert [len(f) for f in findings] == [1, 1, 1]
    assert [f[0].violation_class for f in findings] == [ViolationClass.FIGURE_NOT_IN_RECORD] * 3


def test_a_positive_figure_in_the_draft_cannot_cite_a_negative_record_value():
    """Sign is part of the value, and the direction that matters is a debit read as a credit.

    Design spec 4.5 names this case explicitly: a naive character-class strip removes the minus
    and silently turns one into the other. Every value in this file until now is positive, so a
    check that compared magnitudes would satisfy all of them.

    The second half is the other direction, and it is what keeps the first from being satisfied
    by a validator that simply rejects everything negative: the parenthesised accounting
    spelling is a *different representation of the same debit*, and it is accepted. Both sides
    are written in the same currency, so neither assertion turns on a symbol the module does
    not interpret.
    """
    record = Record(fields={"write_off": "-$2,500,000"})
    findings = validate_citations(_draft("We recorded $2.5M.", ("write_off",)), record)
    assert len(findings) == 1
    assert findings[0].detail == (
        "index 0: field 'write_off' value -2500000 does not appear in the draft"
    )
    accepted = validate_citations(_draft("We recorded ($2,500,000).", ("write_off",)), record)
    assert accepted == ()
