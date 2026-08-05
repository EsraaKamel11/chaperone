from __future__ import annotations

import re

from chaperone.policy.types import Draft, Finding, ViolationClass

TRIPWIRE_CLASSES = frozenset({
    ViolationClass.FORWARD_LOOKING_RETURN,
    ViolationClass.NEGOTIATES_TERMS,
    ViolationClass.ADVISES_ON_MERITS,
})

_HORIZON = r"(?:year|month|quarter|annually|per annum|p\.a\.)"

# The boundary belongs inside the alternation, because `%` is not a word character. `(?:%|x)\b`
# demanded a boundary immediately after `%`, and one exists there only when a word character
# follows -- so the pattern refused "20% annually" and accepted the glued "20%annually", inverting
# what it was for. That is the mechanism `_appears_as_a_whole_token` in citations.py already
# records for `\b\$5MM\b`; here it cost the whole `%` branch, measured identically on 3.11 and
# 3.13. `x` is a word character and keeps its boundary, which is what refuses "0x1F" and "12xy".
#
# The gap between a trigger and the token it needs excludes the sentence marks, not only the full
# stop. `.` inside a character class is a literal full stop, so `[^.]` traversed `!`, `?`, `;` and
# `:`, and the span -- which spec 4.7 quotes to a human verbatim while they decide whether a draft
# may go out -- then ran two sentences together: "Personally I am not permitted to say; the deal"
# was a real span for a real refusal to comment. Measured, excluding the four marks keeps every
# verdict in the suite and all three catches on drafts that wrap a violation across a line break.
# `\n` stays traversable on purpose: excluding it loses two of those three, which trades a
# misquotation for an escape. The paragraph-crossing span that remains is pinned as a limit test.
_PATTERNS: tuple[tuple[ViolationClass, re.Pattern[str]], ...] = (
    (ViolationClass.FORWARD_LOOKING_RETURN, re.compile(rf"(?i)\b\d+(?:\.\d+)?\s*(?:%|x\b)[^.!?;:]{{0,60}}\b{_HORIZON}")),
    (ViolationClass.FORWARD_LOOKING_RETURN, re.compile(r"(?i)\b(guarantee[sd]?|assure you|downside protection|no risk)\b")),
    (ViolationClass.NEGOTIATES_TERMS, re.compile(r"(?i)\b(instead of|come down on|meet you at|accept)\b[^.!?;:]{0,40}[$£€]?\s*\d")),
    (ViolationClass.NEGOTIATES_TERMS, re.compile(r"(?i)\b(ask them to|push them (?:on|to)|negotiate)\b")),
    (ViolationClass.ADVISES_ON_MERITS, re.compile(r"(?i)\b(honestly|in my (?:view|opinion)|personally|between us)\b[^.!?;:]{0,60}\b(deal|opportunity|investment|round)\b")),
    (ViolationClass.ADVISES_ON_MERITS, re.compile(r"(?i)\b(?:this|it) is an?\s+(?:\w+\s+){0,2}(strong|excellent|great|compelling|attractive)\b[^.!?;:]{0,20}\b(deal|opportunity|investment|round)\b")),
)


def evaluate_tripwires(draft: Draft) -> tuple[Finding, ...]:
    """Pure lexical detectors. A second disjunct beside the checker, never the detector itself."""
    seen: set[ViolationClass] = set()
    findings: list[Finding] = []
    for violation_class, pattern in _PATTERNS:
        if violation_class in seen:
            continue
        match = pattern.search(draft.body)
        if match:
            seen.add(violation_class)
            findings.append(Finding(violation_class, "tripwire", match.group(0)))
    return tuple(findings)
