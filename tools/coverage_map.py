"""Every constraint class has a detector, checked by machine rather than remembered.

Design spec 10.4: *a test fails if a constraint class has no detector. You cannot silently forget a
class.* `ViolationClass` is the registry of classes; the three tables below are claims about which
detector answers for each; and `main()` is what CI runs, so **the exit code is the property**.

**The three tables are not equally strong, and which is which is the first thing to know:**

- `TRIPWIRE_COVERAGE` is **derived**. It *is* `TRIPWIRE_CLASSES` -- the same object, not a copy --
  which `tests/policy/test_tripwires.py` binds to the pattern table that implements it. It cannot
  drift from its detector, because there is nothing here to drift.
- `ACT_COVERAGE` is a **declaration, bound by test**. Nothing in this module reads
  `evaluate_act_classes`, so the map's answer for this lane is invariant under every possible
  change to that detector -- including deleting it.
  `tests/test_coverage_map.py::test_the_declared_act_detector_is_bound_to_the_module_that_implements_it`
  is what closes that, in both directions: every declared class fires from the real predicate, and
  every class the module names is declared.
- `CHECKER_COVERAGE` is a **declaration bound only to the enum**, and this is the asymmetry worth
  stating plainly. The checker's reach is a property of a model and a prompt, not of code, so there
  is no implementation here to bind it to; it is measured in the evals. What this table establishes
  is that these three classes are **declared** to have a checker, and that the declaration matches
  the content family exactly. It establishes nothing about what the checker catches, and a table
  saying `checker` is not evidence that one would.

**A run that classified nothing must never report clean**, which is the rule
`tools/static_audit.py::_files_to_audit` already enforces for an audit that examined no files --
and the same treatment, not a second convention: the "classified nothing" case produces a violation
line, so it travels the same path to the same exit code as any uncovered class. The failure is not
hypothetical. `family` reads the `act:`/`content:` prefix off a value and nothing else, so dropping
those prefixes leaves every class unclassified, and the previous version of this module skipped
every unclassified class and exited 0 -- reporting clean over a registry it had forgotten entirely.

**The exemption is by identity, never by family.** `EXEMPT` below names `OTHER` and only `OTHER`.
Exempting the `UNCLASSIFIED` *family* instead handed the same free pass to any member added with
neither prefix: measured, a tenth member `ESCALATION_REQUIRED = "escalation_required"` exited 0
while `content:escalation_required` exited 1, so 10.4's promise held only for a class that named
its family -- which is precisely the class nobody forgets.

**What this module is.** A registry, three declarations and an exit code. It runs no detector and
cannot observe one working. "Zero by construction" is an act-class claim and is not made here about
anything.
"""
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

# What each family must have before it counts as covered. A table rather than a branch per family,
# because the answer for a family **absent** from it has to be "uncovered": that is what turns an
# unclassified class from a silent skip into a reported one, and it is what a `Family` member added
# later inherits by default. Fail-closed in the label direction too -- rename a label in
# `detectors_for` without renaming it here and every class is reported, loudly.
REQUIRED_DETECTORS = {
    Family.ACT: frozenset({"act_classes"}),
    Family.CONTENT: frozenset({"checker", "tripwires"}),
}

# The classes that legitimately have no detector, named one by one rather than exempted by family.
# `OTHER` is what a finding carries when the checker gave no usable answer -- there is nothing to
# detect, and a detector for it would be a category error.
#
# **This is the disarm surface.** Every name added here is a class the map stops asking about, so
# adding one is how this tool would be silenced, and `test_other_is_the_only_class_the_map_is_
# allowed_to_exempt` fails on a second entry for that reason. CLAUDE.md's rule for
# `tools/static_audit.py`'s allowed list applies here unchanged: if the map reports a class, close
# the gap rather than exempting the class.
EXEMPT = frozenset({ViolationClass.OTHER})


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
    """Every registered class that is not exempt and lacks what its family requires.

    A class whose family has no entry in `REQUIRED_DETECTORS` is uncovered rather than skipped.
    That one condition is what closed the unclassified-member hole; the `continue` on
    `Family.UNCLASSIFIED` that it replaces **was** the hole.
    """
    uncovered = []
    for klass in ViolationClass:
        if klass in EXEMPT:
            continue
        required = REQUIRED_DETECTORS.get(klass.family)
        if required is None or not required <= set(detectors_for(klass)):
            uncovered.append(klass)
    return uncovered


def violations() -> list[str]:
    """Every reason this run is not clean, including having classified nothing.

    Separate from `main()` for the reason `tools/static_audit.py::audit_tree` is: a test can then
    read the real enforcement's own answer rather than a reconstruction of it.

    A registry with nothing to map yields a violation instead of an empty list. An empty registry
    and one holding nothing but exempt classes are the same situation -- the map asked about no
    class at all -- and reporting clean over either is forgetting every class at once.
    """
    classified = [klass for klass in ViolationClass if klass not in EXEMPT]
    if not classified:
        return [f"{ViolationClass.__name__}: no constraint class to map -- classified nothing"]
    return [f"uncovered constraint class: {klass.value}" for klass in uncovered_classes()]


def main() -> int:
    found = violations()
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
