from __future__ import annotations

from chaperone.gates.checker import Checker, CheckerUnavailable, FlagForReview, Verdict
from chaperone.policy.act_classes import ActContext, evaluate_act_classes
from chaperone.policy.citations import validate_citations
from chaperone.policy.tripwires import evaluate_tripwires
from chaperone.policy.types import Decision, Disposition, Draft, Finding, Record, ViolationClass

FUTILE_CLASSES = frozenset({ViolationClass.ADVISES_ON_MERITS})


def disposition_for(findings: tuple[Finding, ...]) -> Disposition:
    if not findings:
        return Disposition.ALLOW
    if any(f.violation_class in FUTILE_CLASSES for f in findings):
        return Disposition.REDIRECT_FUTILE
    return Disposition.REDIRECT_REFINABLE


def decide(draft: Draft, record: Record, context: ActContext, checker: Checker) -> Decision:
    act_findings = evaluate_act_classes(draft, record, context) + validate_citations(draft, record)
    if act_findings:
        return Decision(False, act_findings, disposition_for(act_findings))

    tripwire_findings = evaluate_tripwires(draft)

    try:
        result = checker.check(draft, record)
    except CheckerUnavailable as exc:
        return Decision(
            False,
            (Finding(ViolationClass.OTHER, f"checker unavailable: {exc}", None),),
            Disposition.REDIRECT_FUTILE,
        )

    if isinstance(result, FlagForReview):
        return Decision(
            False,
            (Finding(ViolationClass.OTHER, f"flagged for review: {result.reason}", None),) + tripwire_findings,
            Disposition.REDIRECT_FUTILE,
        )

    checker_findings: tuple[Finding, ...] = ()
    if result.violates and result.violation_class is not None:
        checker_findings = (Finding(result.violation_class, f"checker confidence {result.confidence}", result.span),)

    findings = checker_findings + tripwire_findings
    if findings:
        return Decision(False, findings, disposition_for(findings))
    return Decision(True, (), Disposition.ALLOW)


def denial_result(decision: Decision) -> dict:
    """The categorized tool result. Delivered as the result, never raised."""
    primary = decision.findings[0]
    return {
        "is_error": True,
        "is_retryable": False,
        "category": primary.violation_class.value,
        "detail": primary.detail,
        "span": primary.span or "",
        "disposition": decision.disposition.value,
    }
