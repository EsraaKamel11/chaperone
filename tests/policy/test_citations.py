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
