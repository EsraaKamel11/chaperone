from __future__ import annotations

import sys

from chaperone.policy.tripwires import TRIPWIRE_CLASSES
from chaperone.policy.types import Family, ViolationClass

ACT_COVERAGE = frozenset({
    ViolationClass.NO_APPROVAL_TOKEN, ViolationClass.JURISDICTION_NOT_CONSENTED,
    ViolationClass.TOOL_OUTSIDE_GRANT, ViolationClass.FIGURE_NOT_IN_RECORD,
    ViolationClass.SEND_CAP_EXCEEDED,
})
CHECKER_COVERAGE = frozenset({
    ViolationClass.ADVISES_ON_MERITS, ViolationClass.NEGOTIATES_TERMS,
    ViolationClass.FORWARD_LOOKING_RETURN,
})
TRIPWIRE_COVERAGE = TRIPWIRE_CLASSES


def detectors_for(violation_class: ViolationClass) -> list[str]:
    detectors = []
    if violation_class in ACT_COVERAGE:
        detectors.append("act_classes")
    if violation_class in CHECKER_COVERAGE:
        detectors.append("checker")
    if violation_class in TRIPWIRE_COVERAGE:
        detectors.append("tripwires")
    return detectors


def uncovered_classes() -> list[ViolationClass]:
    uncovered = []
    for klass in ViolationClass:
        if klass.family is Family.UNCLASSIFIED:
            continue
        detectors = detectors_for(klass)
        if klass.family is Family.ACT and "act_classes" not in detectors:
            uncovered.append(klass)
        if klass.family is Family.CONTENT and not {"checker", "tripwires"} <= set(detectors):
            uncovered.append(klass)
    return uncovered


def main() -> int:
    uncovered = uncovered_classes()
    for klass in uncovered:
        print(f"uncovered constraint class: {klass.value}")
    return 1 if uncovered else 0


if __name__ == "__main__":
    sys.exit(main())
