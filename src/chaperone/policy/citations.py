from __future__ import annotations

import re

from chaperone.policy.canonical import CanonicalizationError, figures_in, normalize_money
from chaperone.policy.types import Draft, Finding, Record, ViolationClass


def _appears_as_a_whole_token(value: str, body: str) -> bool:
    r"""True when `value` occurs in `body` bounded by non-word characters on both sides.

    Plain containment is finding E's own mistake with the value substituted for the field name.
    The spec's account of finding E is a validator that accepted a field token *anywhere* in a
    string; `value in body` accepts the value anywhere in the draft, so "US" matches inside
    "discussed" and "Series A" matches inside "Series Auction", and a draft that mentions
    neither evidences a citation to both. Containment is not a claim.

    A blank value returns False rather than True, and this is the only place that decision is
    made. `"" in anything` is True, so an empty or whitespace-only field validated every
    citation to it -- and the guard has to live here rather than in the caller, because an empty
    needle defeats the anchoring too: `(?<!\w)(?!\w)` still matches between two spaces. A field
    with no value cannot evidence anything.

    The bounds are lookarounds, not `\b`. `\b` is defined relative to the character beside it,
    so a value that begins or ends with a non-word character -- "$5MM", "(pending)" -- would
    demand a word character exactly where there is none and could never match. A negative
    lookahead for `\w` states the property directly: nothing may run into the value from either
    side, whatever the value happens to start with.
    """
    needle = value.strip()
    if not needle:
        return False
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", body, re.IGNORECASE) is not None


def validate_citations(draft: Draft, record: Record) -> tuple[Finding, ...]:
    """A citation is valid when the field exists AND its canonical value appears in the draft.

    Two checks, and which one a field gets is decided by whether `normalize_money` can read the
    value **recorded** for it -- never by anything in the draft.

    The canonical check is the one that admits representation variance: "$10M", "10m" and
    "10,000,000" are one value, so a drafter may write the amount however they like. The
    degraded check admits none. It requires the record's own text, whole-token and
    case-insensitive, because when canonicalization fails the module never learned what the
    field is worth and so cannot say that two spellings denote the same amount.

    **The degraded path is therefore the narrower of the two, and that ordering is the point.**
    It accepts only a draft that reproduces the record's text; the canonical path accepts that
    and also every restatement of the amount. Degradation must narrow the check -- a fallback
    that widened it would let a record value the module cannot read evidence *more* than one it
    can, which is the direction that turns an unreadable record into an escape.
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
