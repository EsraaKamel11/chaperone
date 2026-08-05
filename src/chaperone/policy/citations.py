from __future__ import annotations

import re

from chaperone.policy.canonical import CanonicalizationError, figures_in, normalize_money
from chaperone.policy.types import Draft, Finding, Record, ViolationClass


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

    **The sign guard.** `(?<!\w)` treats "-" and "(" as boundaries, which is right for a text
    value -- "US" inside "(US)" is still the same US -- and wrong for a value carrying a figure,
    because `_AMOUNT` reads those same characters as the sign. Without the guard, "A loss of
    -$5MM." reproduced a record's "$5MM" as a whole token and evidenced it while stating the
    opposite: the two halves of the module disagreed about what "-" means, and design spec 4.5
    is most emphatic about exactly this failure -- a debit silently turned into a credit. So a
    needle carrying a digit additionally refuses a sign character immediately in front of it.
    The guard is conditional because applying it to text values would cost "(US)" and
    "US-based", which are not sign-bearing at all.

    This is a set of enumerated defences, not a proof. It does not establish that the draft
    means what the record means -- see the limits pinned in the tests, which are the residual.
    """
    needle = value.strip()
    if not needle:
        return False
    # "-" and "(" are the two characters `_AMOUNT` can read as making a figure negative.
    sign_guard = r"(?<![-(])" if any(character.isdigit() for character in needle) else ""
    pattern = rf"(?<!\w){sign_guard}{re.escape(needle)}(?!\w)"
    return re.search(pattern, body, re.IGNORECASE) is not None


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
    a value carrying a figure is refused where the draft puts a sign character in front of it,
    so the draft cannot negate what the record states.

    That list is not a proof that the degraded path is narrower everywhere -- the sign case was
    a counterexample to exactly that claim until it was closed, and it was found by measurement
    rather than by the sweep. The residuals are pinned as executable limits in the tests: a bare
    digit run in prose can satisfy a small numeric citation, currency symbols are not part of a
    canonical value, and digits adjacent to "." or "," can still truncate or extend a magnitude.
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
