from __future__ import annotations

import re

from chaperone.policy.types import Draft, Finding, ViolationClass

TRIPWIRE_CLASSES = frozenset({
    ViolationClass.FORWARD_LOOKING_RETURN,
    ViolationClass.NEGOTIATES_TERMS,
    ViolationClass.ADVISES_ON_MERITS,
})

_HORIZON = r"(?:year|month|quarter|annually|per annum|p\.a\.)"
_PATTERNS: tuple[tuple[ViolationClass, re.Pattern[str]], ...] = (
    (ViolationClass.FORWARD_LOOKING_RETURN, re.compile(rf"(?i)\b\d+(?:\.\d+)?\s*(?:%|x)\b[^.]{{0,60}}\b{_HORIZON}")),
    (ViolationClass.FORWARD_LOOKING_RETURN, re.compile(r"(?i)\b(guarantee[sd]?|assure you|downside protection|no risk)\b")),
    (ViolationClass.NEGOTIATES_TERMS, re.compile(r"(?i)\b(instead of|come down on|meet you at|accept)\b[^.]{0,40}[$£€]?\s*\d")),
    (ViolationClass.NEGOTIATES_TERMS, re.compile(r"(?i)\b(ask them to|push them (?:on|to)|negotiate)\b")),
    (ViolationClass.ADVISES_ON_MERITS, re.compile(r"(?i)\b(honestly|in my (?:view|opinion)|personally|between us)\b[^.]{0,60}\b(deal|opportunity|investment|round)\b")),
    (ViolationClass.ADVISES_ON_MERITS, re.compile(r"(?i)\b(?:this|it) is an?\s+(?:\w+\s+){0,2}(strong|excellent|great|compelling|attractive)\b[^.]{0,20}\b(deal|opportunity|investment|round)\b")),
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
