from __future__ import annotations

import re

from chaperone.policy.canonical import CanonicalizationError, figures_in, normalize_money
from chaperone.policy.types import Draft, Finding, Record, ViolationClass


# Mirrors `_AMOUNT`'s own prefix -- `(?P<paren>\()?\s*(?P<sign>-)?\s*[$£€]?\s*` in
# canonical.py -- reversed onto the text preceding a candidate match, and requiring the paren
# or the minus actually to be present. It is applied by searching backwards rather than as a
# lookbehind because `re` lookbehinds must be fixed-width and this prefix is not: the first
# version of this guard was `(?<![-(])`, which read one character back and so refused
# "-$5MM" while accepting "- $5MM", "-$10,000,000 USD" and "(-$10,000,000 USD)".
#
# **Update this whenever `_AMOUNT` changes.** Two patterns deciding what a sign is will drift,
# and this guard exists because they already did once.
_SIGN_PREFIX = re.compile(r"(?:\(\s*-?|-)\s*[$£€]?\s*$")


def _appears_as_a_whole_token(value: str, body: str) -> bool:
    r"""True when `value` occurs in `body` as a whole token that the draft has not negated.

    Plain containment is finding E's own mistake with the value substituted for the field name.
    The spec's account of finding E is a validator that accepted a field token *anywhere* in a
    string; `value in body` accepts the value anywhere in the draft, so "US" matches inside
    "discussed" and "Series A" matches inside "Series Auction", and a draft that mentions
    neither evidences a citation to both. Containment is not a claim.

    A blank value returns False rather than True, and this function is the single place the
    accept/reject decision is taken. `"" in anything` is True, so an empty or whitespace-only
    field validated every citation to it -- and the guard has to live here rather than in the
    caller, because an empty needle defeats the anchoring too: `(?<!\w)(?!\w)` still matches
    between two spaces. A field with no value cannot evidence anything. (The caller strips the
    value a second time, but only to choose the wording of the finding; it decides nothing.)

    The bounds are lookarounds, not `\b`, and the reason is stronger than a missed match: `\b`
    **inverts** the property. Measured against "$5MM", `\b\$5MM\b` does not match the legitimate
    "We raised $5MM." -- the space and the "$" are both non-word, so there is no boundary
    between them -- and it *does* match the glued "x$5MM.", where "x" abutting "$" makes one.
    It accepts exactly what it should refuse. A negative lookaround for `\w` states the property
    directly: nothing may run into the value from either side, whatever the value starts with.

    **The adjacency guards.** `(?<!\w)` treats "-", "(", "." and "," as boundaries, which is
    right for a text value -- "US" inside "(US)" is still the same US -- and wrong for a value
    carrying a figure, because `_AMOUNT` reads those same characters as part of the number. Two
    kinds of damage follow, and they are one mechanism rather than two defects:

    - **sign.** "A loss of -$5MM." reproduced a record's "$5MM" as a whole token and evidenced
      it while stating the opposite. Design spec 4.5 is most emphatic about exactly this -- a
      debit silently turned into a credit.
    - **magnitude.** "A ratio of 1.5 seats." evidenced a record's "5 seats", and "In tranche
      2,500,000 closed." evidenced "tranche 2". The draft states a different number and reads
      as corroboration.

    So a needle carrying a digit also refuses `_AMOUNT`'s sign prefix in front of it, and
    refuses a separator on either side **when a digit continues past it**. Both conditions are
    measured, not assumed: making the guards unconditional costs exactly one legitimate row,
    "The investor (US) is interested."; refusing separators unconditionally costs four,
    including "We raised $5MM." and "We raised 10,000,000 USD." -- a full stop is a separator,
    so the blanket rule refuses any figure-bearing value that ends a sentence. Each condition
    has a mutant that dies to exactly one test.

    **What this is.** A list of refused shapes, arrived at by measurement, each with a test.
    It is not a proof that a signed or rescaled spelling cannot get through -- the sign guard
    was itself one character wide until measurement found four spellings that walked past it.
    It does not establish that the draft means what the record means. The residual is pinned in
    the tests, and what is untested is what has not yet been thought of.
    """
    needle = value.strip()
    if not needle:
        return False
    figure_bearing = any(character.isdigit() for character in needle)
    # "." and "," are the characters `_AMOUNT` reads as continuing a figure, but only when a
    # digit sits on the far side of them; those are fixed-width and stay in the pattern.
    before, after = (r"(?<!\d[.,])", r"(?![.,]\d)") if figure_bearing else ("", "")
    pattern = rf"(?<!\w){before}{re.escape(needle)}(?!\w){after}"
    for match in re.finditer(pattern, body, re.IGNORECASE):
        # The sign prefix is variable-width, so it is searched behind each candidate instead.
        if figure_bearing and _SIGN_PREFIX.search(body[: match.start()]):
            continue
        return True
    return False


def validate_citations(draft: Draft, record: Record) -> tuple[Finding, ...]:
    """A citation is valid when the field exists AND its canonical value appears in the draft.

    Two checks, and which one a field gets is decided by whether `normalize_money` can read the
    value **recorded** for it -- never by anything in the draft.

    The canonical check is the one that admits representation variance: "$10M", "10m" and
    "10,000,000" are one value, so a drafter may write the amount however they like. The
    degraded check admits none. It requires the record's own text, whole-token and
    case-insensitive, because when canonicalization fails the module never learned what the
    field is worth and so cannot say that two spellings denote the same amount.

    **Degradation must narrow the check, never widen it**, because a fallback that widened it
    would let a record value the module cannot read evidence *more* than one it can. What is
    actually enforced toward that end is a list, not a universal: the record's text must appear
    as a whole token, not as a fragment of a longer word; a blank value evidences nothing; and
    a value carrying a figure is refused where the draft puts `_AMOUNT`'s sign prefix in front
    of it, or continues its digits past a separator.

    **That is a list of refused shapes, not a completeness claim.** Read it as "these spellings
    are refused", never as "the draft cannot negate or rescale the value" -- three sentences of
    that stronger kind have been written in this module and all three were false when written.
    The degraded path being narrower everywhere is likewise not established: the sign and
    magnitude cases were live counterexamples to it, both found by measurement rather than by
    the sweep, and the sign guard was a character wide until four spellings were measured
    walking past it. The residuals that *are* known are pinned as executable limits in the
    tests: a bare digit run in prose can satisfy a small numeric citation, and a currency symbol
    is not part of a canonical value.
    """
    findings: list[Finding] = []
    draft_figures = figures_in(draft.body)

    for index, field_name in enumerate(draft.cited_fields):
        value = record.get(field_name)
        if value is None:
            findings.append(Finding(
                ViolationClass.FIGURE_NOT_IN_RECORD,
                f"index {index}: field {field_name!r} is not in the record",
                None,
            ))
            continue

        try:
            canonical = normalize_money(value)
        except CanonicalizationError:
            if not _appears_as_a_whole_token(value, draft.body):
                # One decision, taken in one place. The branch below chooses only how to say
                # it: a blank record field and a fabricated citation are both refusals, but
                # they send a human to different places -- upstream data, or the draft.
                if value.strip():
                    detail = f"index {index}: field {field_name!r} value {value!r} does not appear in the draft"
                else:
                    detail = f"index {index}: field {field_name!r} has no value to cite"
                findings.append(Finding(ViolationClass.FIGURE_NOT_IN_RECORD, detail, None))
            continue

        if canonical not in draft_figures:
            findings.append(Finding(
                ViolationClass.FIGURE_NOT_IN_RECORD,
                f"index {index}: field {field_name!r} value {canonical} does not appear in the draft",
                None,
            ))

    return tuple(findings)
