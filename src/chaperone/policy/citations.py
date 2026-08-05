from __future__ import annotations

from chaperone.policy.canonical import CanonicalizationError, figures_in, normalize_money
from chaperone.policy.types import Draft, Finding, Record, ViolationClass


def validate_citations(draft: Draft, record: Record) -> tuple[Finding, ...]:
    """A citation is valid when the field exists AND its canonical value appears in the draft."""
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
            if value.lower() not in draft.body.lower():
                findings.append(Finding(
                    ViolationClass.FIGURE_NOT_IN_RECORD,
                    f"index {index}: field {field_name!r} value {value!r} does not appear in the draft",
                    None,
                ))
            continue

        if canonical not in draft_figures:
            findings.append(Finding(
                ViolationClass.FIGURE_NOT_IN_RECORD,
                f"index {index}: field {field_name!r} value {canonical} does not appear in the draft",
                None,
            ))

    return tuple(findings)
