import pytest

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


# --- fix round 1: the presence check was an unanchored substring test on both paths ---


def test_a_blank_field_cannot_evidence_a_citation():
    """`"" in anything` is True, so a blank value validated every citation made to it.

    A single space is in almost every sentence, so the whitespace spelling fails open just as
    reliably as the empty one -- both are here because both were measured, not because one
    implies the other. A field with no value cannot evidence anything, so the safe answer is a
    finding, and the wording separates an upstream record bug from a fabricated draft: they are
    both refusals but they send a human to different places.
    """
    record = Record(fields={"note": "", "blank": "   "})
    body = "I made this up entirely."
    for index, field_name in enumerate(("note", "blank")):
        findings = validate_citations(_draft(body, (field_name,)), record)
        assert len(findings) == 1, f"{field_name!r} validated a citation it cannot evidence"
        assert findings[0].detail == f"index 0: field {field_name!r} has no value to cite"
    assert index == 1


def test_a_value_appearing_only_inside_an_unrelated_word_cannot_evidence_a_citation():
    """Finding E's own shape, one level down, and the reason this module exists.

    The spec's account of finding E is a validator that accepted a field token *anywhere* in a
    string. Replacing the token with the value and keeping the unanchored containment test
    reproduces the defect exactly: "US" is inside "discussed" and "Series A" is inside "Series
    Auction", so a draft that mentions neither evidences a citation to both.

    The last two assertions are not decoration. A validator that simply rejected everything
    would satisfy the first three, so the same values are also shown matching where they really
    do appear -- at a punctuation boundary and in different case. The fix has to land on
    "spurious finding" for genuine text, not on "reject everything".
    """
    record = Record(fields={"jurisdiction": "US", "stage": "Series A"})
    assert len(validate_citations(_draft("We discussed it at length.", ("jurisdiction",)), record)) == 1
    assert len(validate_citations(_draft("We ran a Series Auction.", ("stage",)), record)) == 1
    assert len(validate_citations(_draft("The seedling programme.", ("stage",)), record)) == 1
    assert validate_citations(_draft("The investor is in the US.", ("jurisdiction",)), record) == ()
    assert validate_citations(_draft("this is a series a round", ("stage",)), record) == ()


def test_an_unreadable_record_value_is_matched_by_its_text_alone_never_by_its_amount():
    """Degradation must narrow the check, never widen it.

    "10,000,000 USD" is a spelling `normalize_money` refuses, so the module never learns what
    the field is worth. It may then ask only whether the draft reproduces the record's own text;
    it must not accept a draft that states the same amount some other way, because it never
    established what the amount is.

    The middle and last assertions are the ordering, and they are deliberately the *same draft
    body* judged against two records: "We raised $10m." is a finding against the unreadable
    spelling and clean against the readable one. Only the record's spelling differs, which is
    the whole claim -- the record decides which check applies, the draft never does, and the
    degraded check is the narrower one. A fallback that widened it would let a record value the
    module cannot read evidence more than one it can.
    """
    unreadable = Record(fields={"raise": "10,000,000 USD"})
    readable = Record(fields={"raise": "10000000"})
    assert validate_citations(_draft("We raised 10,000,000 USD.", ("raise",)), unreadable) == ()
    assert len(validate_citations(_draft("We raised $10m.", ("raise",)), unreadable)) == 1
    assert validate_citations(_draft("We raised $10m.", ("raise",)), readable) == ()


def test_a_finding_never_reports_a_span_that_is_not_in_the_draft():
    """A span is a span *of the draft*, and today there is never one to report.

    Asserting `span is None` would kill the mutant that puts the field name there, but it would
    also fail the day someone reports a genuine offending span -- which is the improvement the
    bare-digit limit below actually needs, so a test forbidding it would be an obstacle rather
    than a guard. This asserts instead the property that makes a span a span: whatever is
    reported must be findable in the body the reader is being pointed at.

    It is not idle. The denial contract publishes `span or ""` to the caller, so a span naming
    something absent from the draft sends a reader hunting for text that was never there. All
    three rejection paths are covered, and none of the three field names appears in the body.
    """
    cases = [
        (Record(fields={"round_size": "10000000"}), ("round_size",)),  # the canonical path
        (Record(fields={"stage": "Series A"}), ("stage",)),            # the degraded path
        (Record(fields={"stage": "Series A"}), ("valuation",)),        # the field is missing
    ]
    for record, cited in cases:
        draft = _draft("I made this up entirely.", cited)
        findings = validate_citations(draft, record)
        assert len(findings) == 1, "a case that reports nothing proves nothing about spans"
        assert findings[0].span is None or findings[0].span in draft.body


# --- three limits, asserted in executable form rather than implied. None is a property to
# --- preserve: if a later task closes one, its test fails, and that failure is the
# --- notification. Delete it, do not repair it.


def test_a_bare_digit_run_in_prose_can_satisfy_a_small_numeric_citation_a_known_limit():
    """The residual after anchoring, and it has no cheap fix.

    `figures_in` is a candidate extractor over prose, not a money parser, so a bare digit run is
    a candidate: "3 questions" offers the figure 3, and a citation to a field worth 3 is
    satisfied by a sentence that never mentions that field. Anchoring closed the textual half of
    exactly this shape ("US" inside "discussed"); the numeric half has no equivalent, because by
    the time the comparison happens both sides are decimals and there is no sentence left to
    anchor to. Closing it needs the citation to carry a span, which is a design change.

    The leniency is right where it comes from: `evaluate_act_classes` asks "is every figure in
    this draft in the record?", where a spare candidate costs only a spurious finding. This
    module asks the converse, and on the converse the same leniency costs an escape. Small
    integers -- counts, seats, headcounts -- are the population at risk.
    """
    record = Record(fields={"board_seats": "3"})
    draft = _draft("I have 3 questions about the round.", ("board_seats",))
    assert validate_citations(draft, record) == ()


def test_the_currency_symbol_is_not_part_of_the_canonical_value_a_stated_scope_limit():
    """Design spec 4.5's own declared boundary, in executable form.

    The spec says locale-general money handling "is not in scope for this artifact and is stated
    as such rather than implied" -- and stating it as such is what this test is. `normalize_money`
    reads the digits and drops the symbol, so equal magnitudes in two currencies compare equal
    and a sterling record value is evidenced by a dollar figure in the draft.
    """
    record = Record(fields={"round_size": "£10,000,000"})
    assert validate_citations(_draft("Raising $10M.", ("round_size",)), record) == ()


def test_a_non_string_record_value_is_undefined_behaviour_outside_the_type_contract():
    """Pinned so a change is visible, not endorsed. `Record.fields` is `Mapping[str, str]`.

    Python does not enforce that annotation, and what happens is decided by which path the value
    takes -- which is why two people probing this reached different answers. Measured, the whole
    matrix is: `int`, `float`, `bool` and `Decimal` are accepted by `normalize_money`, reach the
    canonical path and are judged as though written as digits; `bytes` survives to produce a
    (meaningless) finding; and everything else -- `list`, `tuple`, `dict`, a bare object, a
    non-finite float -- reaches `.strip()` and raises `AttributeError`.

    That raise is the part worth naming: **a predicate that raises is not a predicate.** No
    finding is produced at all, and a forgiving executor can relabel the exception "transient,
    please retry" and retry the very act the predicate exists to stop.
    """
    numeric = Record(fields={"seats": 3})  # type: ignore[dict-item]
    assert validate_citations(_draft("There are 3 seats.", ("seats",)), numeric) == ()
    ill_typed = Record(fields={"seats": ["3"]})  # type: ignore[dict-item]
    with pytest.raises(AttributeError):
        validate_citations(_draft("There are 3 seats.", ("seats",)), ill_typed)
