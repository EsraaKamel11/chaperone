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

    The middle and last assertions are deliberately the *same draft body* judged against two
    records: "We raised $10m." is a finding against the unreadable spelling and clean against
    the readable one. Only the record's spelling differs, which is the point -- the record
    decides which check applies and the draft never does.

    **What that establishes is one instance of non-widening, not the universal.** "The degraded
    path is narrower everywhere" is a claim about every input, and a single pair of inputs
    cannot carry it; the sign case was a live counterexample to it while this test passed. So
    this test pins the specific behaviour it exercises -- an unreadable value admits no
    representation variance -- and the universal is not claimed anywhere.
    """
    unreadable = Record(fields={"raise": "10,000,000 USD"})
    readable = Record(fields={"raise": "10000000"})
    assert validate_citations(_draft("We raised 10,000,000 USD.", ("raise",)), unreadable) == ()
    assert len(validate_citations(_draft("We raised $10m.", ("raise",)), unreadable)) == 1
    assert validate_citations(_draft("We raised $10m.", ("raise",)), readable) == ()


def test_the_sign_bearing_shapes_the_guard_refuses():
    """A debit read as a credit -- the failure design spec 4.5 is most emphatic about.

    `(?<!\\w)` treats "-" and "(" as boundary characters, which is right for a text value ("US"
    inside "(US)" is still the same US) and wrong for a value carrying a figure, because
    `_AMOUNT` reads those same characters as the sign.

    **This test enumerates measured shapes; it does not establish that no signed spelling gets
    through.** The first version of this guard was a one-character lookbehind, and it refused
    row 1 while accepting rows 2 to 5 -- `_AMOUNT` reads a sign through `\\s*[$....]?\\s*`, so
    every spelling with a space or a currency symbol between the minus and the digits evaded a
    guard that looked one character back. The rows below are the spellings that were measured;
    the guard now mirrors `_AMOUNT`'s own prefix rather than guessing a width, which is what
    makes the list extend rather than merely lengthen.

    The accepted half is why this is a tightening and not a blanket refusal: the same figure in
    plain prose, and a record value carrying its own minus, both still match.
    """
    unsigned = Record(fields={"f": "$5MM"})
    wide = Record(fields={"f": "10,000,000 USD"})
    for record, body in [
        (unsigned, "A loss of -$5MM."),                    # 1: minus abutting the value
        (unsigned, "A loss of - $5MM."),                   # 2: minus, space, then the value
        (wide, "We wrote off -$10,000,000 USD."),          # 3: minus, currency, then digits
        (wide, "We wrote off - $10,000,000 USD."),         # 4: minus, space, currency, digits
        (wide, "We wrote off (-$10,000,000 USD)."),        # 5: bracket, minus, currency, digits
        (wide, "We wrote off -10,000,000 USD."),           # 6: minus abutting the digits
    ]:
        findings = validate_citations(_draft(body, ("f",)), record)
        assert len(findings) == 1, f"{record.fields['f']!r} was evidenced by {body!r}"
    assert validate_citations(_draft("We raised $5MM.", ("f",)), unsigned) == ()
    assert validate_citations(_draft("We raised 10,000,000 USD.", ("f",)), wide) == ()
    carries_its_own = Record(fields={"f": "-$5MM"})
    assert validate_citations(_draft("A loss of -$5MM.", ("f",)), carries_its_own) == ()


def test_a_figure_bearing_value_in_parentheses_is_refused_a_pinned_false_block():
    """A limit created by the sign fix, pinned rather than left to be discovered.

    "(" is a sign character to `_AMOUNT`, so a figure-bearing value is refused when a
    parenthesis immediately precedes it -- including when the parenthesis is merely
    parenthetical and nothing negative is meant. This is the fail-closed direction: a spurious
    finding routed to a human, never an escape.

    The canonicalizer's actual behaviour here is narrower than "parentheses mean negative", and
    it was measured rather than assumed: `figures_in` reads "(10000000)" as **-10000000** but
    "(10,000,000 USD)" as **+10000000**, because the pattern only pairs a parenthesis with the
    figure when nothing but whitespace intervenes before the closing bracket. So the two paths
    genuinely disagree about the parenthesised form, and refusing it is what stops the degraded
    path from being the more permissive of the two.
    """
    record = Record(fields={"round": "10,000,000 USD"})
    assert len(validate_citations(_draft("The round (10,000,000 USD) closed.", ("round",)), record)) == 1
    # The bracket is read through whitespace, exactly as `_AMOUNT` reads it. This spelling was
    # found by measurement and named in a report a round before any test contained it -- in a
    # file whose stated discipline is "pin the measured shape", that gap was the finding.
    assert len(validate_citations(_draft("We wrote off ( 10,000,000 USD ).", ("round",)), record)) == 1
    assert validate_citations(_draft("The round of 10,000,000 USD closed.", ("round",)), record) == ()
    bracketed = Record(fields={"round": "(10,000,000 USD)"})
    assert validate_citations(_draft("The round (10,000,000 USD) closed.", ("round",)), bracketed) == ()


def test_a_hyphenated_number_is_refused_by_both_paths_alike():
    """A false block, and the measurement that makes it the *right* kind.

    "FY-2024", "COVID-19" and "10-15 seats" all put a hyphen in front of a figure, so the sign
    guard refuses them. That looks like a degraded-path-only cost until you ask what the
    canonicalizer makes of the same sentences, which is the question this whole round is about:

        figures_in("The FY-2024 plan is set.") = [-2024]
        figures_in("The COVID-19 response.")   = [-19]
        figures_in("We have 10-15 seats.")     = [-15, 10]

    `_AMOUNT` reads every one of them as **negative**, so the canonical path refuses a citation
    to "2024" on the same body for the same reason. The two paths agree, which is the property
    being chased -- a false block present on only one path would mean they had drifted again.

    Reading "FY-2024" as negative 2024 is a limitation of `figures_in` scanning prose, not a
    decision this module made; it is recorded here because this is where a reader meets it.
    """
    for value, body in [
        ("2024", "The FY-2024 plan is set."),
        ("19", "The COVID-19 response."),
        ("15 seats", "We have 10-15 seats."),
    ]:
        findings = validate_citations(_draft(body, ("f",)), Record(fields={"f": value}))
        assert len(findings) == 1, f"{value!r} in {body!r}"


def test_the_legitimate_matches_that_anchoring_must_not_cost():
    """The regression surface. Every row is a citation that genuinely appears and must validate.

    Anchoring and the sign guard both narrow the check, and the cheapest way to pass a narrowing
    test is to narrow too far -- a validator that refused everything would satisfy every
    rejection test in this file. This is the other side of that ledger, and it is why the guards
    are conditioned on the value carrying a digit: "(" and "-" are boundary characters for a
    text value and sign characters for a figure.

    **Measured, the unconditional guards cost exactly one row: "parenthesised text value".**
    An earlier version of this docstring claimed rows 4, 7 and 8, which was three times the real
    figure -- row 7's comma is separated from the value by a space and the separator guard also
    requires a preceding digit, and row 8's hyphen falls after the value where a lookbehind
    cannot reach. The number is small, the conditionality is still real, and M22 still dies
    here; the overstatement was in the claim, not the guard.
    """
    cases = [
        ("value at body start", "US", "US investors are interested."),
        ("value at body end, no trailing punctuation", "US", "The investor is in the US"),
        ("value at body end, trailing punctuation", "US", "The investor is in the US."),
        ("parenthesised text value", "US", "The investor (US) is interested."),
        ("double-quoted", "Series A", 'They called it "Series A" internally.'),
        ("single-quoted", "Series A", "They called it 'Series A' internally."),
        ("comma-adjacent", "US", "The investor, US based, is keen."),
        ("hyphen-adjacent", "US", "A US-based investor."),
        ("case-varied", "Series A", "this is a series a round"),
        ("value begins with a non-word character", "$5MM", "We raised $5MM."),
        ("value ends with a non-word character", "(pending)", "The status is (pending) today."),
        ("value begins and ends non-word", "(pending)", "Status: (pending)."),
        ("figure-bearing value in plain prose", "10,000,000 USD", "We raised 10,000,000 USD."),
        ("figure-bearing, trailing comma", "10,000,000 USD", "We raised 10,000,000 USD, a record."),
        ("figure-bearing, record carries the sign", "-$5MM", "A loss of -$5MM."),
    ]
    refused = [
        label for label, value, body in cases
        if validate_citations(_draft(body, ("f",)), Record(fields={"f": value})) != ()
    ]
    assert refused == [], f"legitimate citations refused: {refused}"
    assert len(cases) == 15


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

    The emptiness clause is load-bearing rather than defensive: `"" in body` is True, so
    `span is None or span in body` alone would wave through a span set to the empty string --
    the same `""`-shaped hole this module already had once, in the blank-value check. A span
    must therefore be absent or *substantive*: non-blank, and findable in the body.
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
        span = findings[0].span
        assert span is None or (span.strip() != "" and span in draft.body)


def test_a_separator_cannot_let_a_smaller_figure_evidence_a_larger_one():
    r"""The sign guard's other half: "." and "," shift a magnitude as "-" and "(" shift a sign.

    One mechanism, not two defects -- adjacency characters that change what a figure means were
    being treated as token boundaries. A record field valued "5 seats" or "3 board seats" is
    ordinary, and it needs no exotic draft to defeat: "1.5 seats" reads as corroboration while
    contradicting. Three shapes, all measured -- a decimal point truncating the value from the
    left, a thousands separator doing the same, and a separator extending the figure rightwards.

    The accepted half is the conditionality, and it is why the rule is `(?<!\d[.,])` rather than
    a blanket refusal of separators. A separator is only part of a number when a digit continues
    past it, and a full stop is a separator -- so the blanket form refuses any figure-bearing
    value that ends a sentence. Measured, it costs four rows of the regression surface above,
    including "We raised $5MM." and "We raised 10,000,000 USD."

    **This is a regression guard, not a limit.** It began as a test pinning the escape as
    accepted and was inverted when the escape was closed; if it fails, something has reopened
    the magnitude escape and the fix is upstream of it.
    """
    for value, body in [
        ("5 seats", "A ratio of 1.5 seats."),
        ("000 USD", "We raised 10,000 USD."),
        ("tranche 2", "In tranche 2,500,000 closed."),
    ]:
        findings = validate_citations(_draft(body, ("f",)), Record(fields={"f": value}))
        assert len(findings) == 1, f"{value!r} was evidenced by {body!r}"
    for value, body in [
        ("5 seats", "There are 5 seats."),
        ("tranche 2", "In tranche 2, we closed."),
        ("10,000,000 USD", "We raised 10,000,000 USD, a record."),
    ]:
        accepted = validate_citations(_draft(body, ("f",)), Record(fields={"f": value}))
        assert accepted == (), f"{value!r} genuinely appears in {body!r} and was refused"


# --- Below this line, and ONLY below it, are limits: behaviours asserted in executable form
# --- because they are known and unclosed, not because they are wanted. The three are named
# --- explicitly rather than counted, because this banner once said "three limits" while four
# --- tests followed it and one of them was a regression guard -- so the banner was instructing
# --- a maintainer to delete the guard standing between this module and a reopened escape.
# ---
# --- For a test below: if it fails, a limit has been closed. Delete it, do not repair it.
# ---   test_a_bare_digit_run_in_prose_can_satisfy_a_small_numeric_citation_a_known_limit
# ---   test_the_currency_symbol_is_not_part_of_the_canonical_value_a_stated_scope_limit
# ---   test_a_non_string_record_value_is_undefined_behaviour_outside_the_type_contract
# --- Every test ABOVE this line is a guard: if one fails, an escape has reopened. Fix upstream.


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
