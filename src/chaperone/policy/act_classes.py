from __future__ import annotations

from dataclasses import dataclass

from chaperone.policy.canonical import CanonicalizationError, figures_in, normalize_money
from chaperone.policy.eligibility import jurisdiction_consented
from chaperone.policy.types import Draft, Finding, Record, ViolationClass


@dataclass(frozen=True)
class ActContext:
    approval_token: str | None
    tier: int
    consented_jurisdictions: frozenset[str]
    granted_tools: frozenset[str]
    sent_count: int
    send_cap: int


def evaluate_act_classes(draft: Draft, record: Record, context: ActContext) -> tuple[Finding, ...]:
    """Pure. No I/O, no clock, no LLM client. The count arrives as an argument."""
    findings: list[Finding] = []

    if context.tier >= 2 and context.approval_token is None:
        findings.append(Finding(ViolationClass.NO_APPROVAL_TOKEN, f"tier {context.tier} requires an approval token", None))

    # The predicate is shared with matching's filters rather than restated there; see
    # `chaperone.policy.eligibility`. The finding stays here, because the act-class this lane is
    # named for is this module's.
    if not jurisdiction_consented(draft.recipient_jurisdiction, context.consented_jurisdictions):
        findings.append(Finding(ViolationClass.JURISDICTION_NOT_CONSENTED, draft.recipient_jurisdiction, None))

    if draft.tool_name is not None and draft.tool_name not in context.granted_tools:
        findings.append(Finding(ViolationClass.TOOL_OUTSIDE_GRANT, draft.tool_name, None))

    record_values = set()
    for value in record.fields.values():
        try:
            record_values.add(normalize_money(value))
        except CanonicalizationError:
            continue
    for figure in sorted(figures_in(draft.body)):
        if figure not in record_values:
            findings.append(Finding(ViolationClass.FIGURE_NOT_IN_RECORD, str(figure), None))

    if context.sent_count >= context.send_cap:
        findings.append(
            Finding(ViolationClass.SEND_CAP_EXCEEDED, f"{context.sent_count}/{context.send_cap}", None)
        )

    return tuple(findings)
