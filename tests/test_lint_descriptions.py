"""The description linter, and both directions of every rule it enforces."""
from __future__ import annotations

from tools.lint_descriptions import CAPABILITY_WORDS, TOOL_DESCRIPTIONS, lint_description, main


def test_every_shipped_description_passes_the_linter():
    """A linter its own corpus fails is a rigged baseline, and a reader spots it immediately.

    Production change that breaks this: any shipped description that stops naming a sibling, stops
    saying what it is not for, or starts claiming a capability its tool was not granted.
    """
    names = list(TOOL_DESCRIPTIONS)
    for name, text in TOOL_DESCRIPTIONS.items():
        siblings = [n for n in names if n != name]
        assert lint_description(name, text, siblings) == [], name


def test_a_description_with_no_boundary_clause_naming_a_sibling_is_flagged():
    """Matched on the discriminating suffix, never on the shared phrase.

    Both boundary-clause messages begin "no boundary clause", so `any("boundary clause" in i)` --
    which is what this asserted -- is satisfied by **either** rule. Measured: deleting the sibling
    check left this green because the do-not-use check still fired on "Sends a message.", and
    deleting the do-not-use check left it green because the sibling check fired. Production change
    that breaks this now: deleting the sibling check specifically.
    """
    issues = lint_description("send_message", "Sends a message.", siblings=["send_reply"])
    assert any("naming a sibling tool" in i for i in issues)


def test_a_description_that_names_a_sibling_but_never_says_what_it_is_not_for_is_flagged():
    """The case neither test covered: one rule satisfied, the other not.

    Production change that breaks this: deleting the do-not-use check. The second assertion is what
    keeps the first honest -- it holds the sibling rule satisfied, so this cannot pass by tripping
    the rule it is not about.
    """
    issues = lint_description(
        "draft_message",
        "Composes a candidate outbound message from the mandate record and the prior thread. "
        "Use send_message to reach a recipient.",
        siblings=["send_message"],
    )
    assert any("what it is not for" in i for i in issues)
    assert not any("naming a sibling tool" in i for i in issues)


def test_a_capability_promised_as_a_gerund_is_flagged():
    """`Sending messages on your behalf` is a promise, and the indicative rule did not see it.

    Production change that breaks this: dropping the `-ing` forms from `CAPABILITY_WORDS`. They
    collide with nothing in the shipped set, which is why they can be added; `transmitted` cannot,
    for the reason `CAPABILITY_WORDS` records.
    """
    issues = lint_description(
        "draft_message",
        "Sending messages on your behalf whenever asked, from the record and the thread. "
        "Do not use this to transmit; use send_message.",
        siblings=["send_message"],
    )
    assert any("sending" in i for i in issues)


def test_a_description_that_is_too_short_is_flagged():
    issues = lint_description("x", "Does it.", siblings=[])
    assert any("too short" in i for i in issues)


def test_a_description_promising_a_capability_the_tool_lacks_is_flagged():
    issues = lint_description("draft_message", "Drafts and sends the message to the investor.",
                              siblings=[])
    assert any("sends" in i for i in issues)


def test_a_tool_the_capability_table_does_not_name_is_checked_against_every_capability_word():
    """Standing check 12. A `.get(name, ())` fallback checks nothing for the tool nobody thought of.

    Production change that breaks this: any lookup that yields an empty forbidden set for a tool
    with no entry. The permissive fallback is silent precisely where a new surface arrives, so the
    default has to be the whole vocabulary rather than none of it.
    """
    for word in CAPABILITY_WORDS:
        issues = lint_description("wire_funds", f"Reviews the wire and {word} the counterparty. "
                                                "Do not use this to draft; use draft_message.",
                                  siblings=["draft_message"])
        assert any(word in i for i in issues), f"{word} went unchecked for a tool with no entry"


def test_a_capability_word_is_forbidden_for_every_tool_that_was_not_granted_it():
    """A per-tool list of forbidden words leaves each tool unchecked for the words nobody listed.

    `read_policy` was listed against `writes` and `updates` alone, so it could have promised to
    send and the linter would have agreed. Production change that breaks this: going back to a
    per-tool forbidden list rather than a per-tool grant subtracted from one vocabulary.
    """
    issues = lint_description("read_policy",
                              "Returns the constraint set and sends a copy to the recipient. "
                              "Do not use this to compose; use draft_message.",
                              siblings=["draft_message"])
    assert any("sends" in i for i in issues)


def test_a_tool_granted_a_capability_may_state_it():
    """Non-vacuity for the grant: without it every description mentioning its own job is flagged."""
    issues = lint_description("send_message", TOOL_DESCRIPTIONS["send_message"],
                              siblings=["draft_message"])
    assert issues == []
    assert "transmits" in CAPABILITY_WORDS, "the granted word is not in the vocabulary being tested"


def test_a_capability_word_inside_a_longer_word_does_not_escape_the_check():
    """Direction one of the rule: a flagged promise must not slip.

    Production change that breaks this: matching on word boundaries, which reads `resends` as a
    word the vocabulary does not hold and lets the promise through. A spurious finding beats a
    missed one, so the match is deliberately a substring match.
    """
    issues = lint_description("draft_message",
                              "Drafts the message and resends it whenever delivery fails. "
                              "Do not use this to transmit; use send_message.",
                              siblings=["send_message"])
    assert any("sends" in i for i in issues)


def test_a_word_that_is_not_a_capability_word_is_not_flagged_for_containing_a_stem():
    """Direction two of the rule: a legitimate description must not be flagged.

    `transmitted thread`, `no send capability` and the identifier `send_message` all carry a
    capability stem and none of them promises a capability. That holds by rule rather than by luck
    of the letters: the vocabulary holds inflected verb forms only and never a bare stem.
    Production change that breaks this: adding `send` or `transmit` to `CAPABILITY_WORDS`.
    """
    issues = lint_description("draft_message", TOOL_DESCRIPTIONS["draft_message"],
                              siblings=["send_message"])
    assert issues == []
    assert "transmitted thread" in TOOL_DESCRIPTIONS["draft_message"]
    assert "no send capability" in TOOL_DESCRIPTIONS["draft_message"]


def test_the_linter_exits_nonzero_on_a_description_that_fails_and_zero_on_the_shipped_set():
    """The exit code is the property CI reads, so it is asserted rather than the issue list.

    Production change that breaks this: a `main` that prints findings and returns 0 anyway.
    """
    assert main({"x": "Does it."}) == 1
    assert main() == 0


def test_a_run_that_linted_no_description_is_reported_rather_than_reported_clean():
    """Standing check 1. A linter that examined nothing must not exit 0.

    The same rule `tools/static_audit.py` applies to an audit over no file and
    `tools/coverage_map.py` to a registry with no class: a renamed or emptied description set would
    otherwise disarm this tool in silence while CI stayed green.

    Production change that breaks this: returning `1 if issues else 0` over an empty set, which is
    zero, and is indistinguishable from three descriptions that passed.
    """
    assert main({}) == 1
