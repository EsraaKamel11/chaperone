import ast
import dataclasses
import inspect

import pytest

from tools.coverage_map import detectors_for, uncovered_classes
from chaperone.policy.types import Family, ViolationClass

from chaperone.gates.engine import ACT_CLASSES, CONTENT_CLASSES
from chaperone.policy import act_classes as act_classes_module
from chaperone.policy.act_classes import ActContext, evaluate_act_classes
from chaperone.policy.types import Draft, Message, Record
from tools.coverage_map import ACT_COVERAGE, CHECKER_COVERAGE, TRIPWIRE_COVERAGE, main


def test_every_constraint_class_has_a_detector():
    assert uncovered_classes() == []


def test_every_act_class_has_a_pure_detector():
    for klass in ViolationClass:
        if klass.family is Family.ACT:
            assert "act_classes" in detectors_for(klass)


def test_every_content_class_has_both_a_checker_and_a_tripwire():
    for klass in ViolationClass:
        if klass.family is Family.CONTENT:
            detectors = detectors_for(klass)
            assert "checker" in detectors
            assert "tripwires" in detectors


def test_a_class_with_no_detector_is_reported(monkeypatch):
    import tools.coverage_map as cm
    monkeypatch.setattr(cm, "TRIPWIRE_COVERAGE", frozenset())
    assert ViolationClass.NEGOTIATES_TERMS in cm.uncovered_classes()


# --- Guards below. Each was watched failing against the mutant it names, applied to the shipped
# --- module rather than to a copy:
# ---   test_emptying_any_one_lane_reports_exactly_the_classes_that_lane_covered
# ---       -- `uncovered_classes` returning `[]`. The brief's test above is this one's tripwire
# ---          row with `in` for `==`; the act and checker lanes had no negative control at all.
# ---   test_the_declared_act_detector_is_bound_to_the_module_that_implements_it
# ---       -- the `SEND_CAP_EXCEEDED` branch deleted from `evaluate_act_classes`, which the map
# ---          alone does not notice: measured, `main()` still exits 0 with the act lane neutered
# ---          entirely and a send over the cap allowed.
# ---   test_the_coverage_tables_are_the_families_the_enum_declares
# ---       -- `ACT_COVERAGE | {ViolationClass.NEGOTIATES_TERMS}`. `uncovered_classes()` stays `[]`
# ---          and `main()` stays 0 under that mutant while `detectors_for` claims a pure detector
# ---          for a content class, so this assertion is the only one that sees it.
# ---   test_other_is_the_only_class_the_map_is_allowed_to_exempt
# ---       -- a tenth member `ESCALATION_REQUIRED = "escalation_required"` added to
# ---          `ViolationClass`: the map exits 0 over it, and only this test objects.
# ---   test_the_tool_exits_nonzero_and_names_the_class_when_one_is_uncovered
# ---       -- `return 1 if uncovered else 0` weakened to `return 0`, and the `print` removed.
# --- Every test above this banner is the brief's own.

RECORD = Record(fields={"round_size": "10000000"})
CONTEXT = ActContext(approval_token="tok", tier=2, consented_jurisdictions=frozenset({"US"}),
                     granted_tools=frozenset({"send_message"}), sent_count=0, send_cap=50)

# One input per declared act-class, each chosen to make the real `evaluate_act_classes` emit that
# class and no other. Overrides rather than whole fixtures, so a row names the single thing it
# changes and cannot pass by changing two.
_ACT_TRIGGERS = {
    ViolationClass.NO_APPROVAL_TOKEN: ({}, {"approval_token": None}),
    ViolationClass.JURISDICTION_NOT_CONSENTED: ({"recipient_jurisdiction": "DE"}, {}),
    ViolationClass.TOOL_OUTSIDE_GRANT: ({"tool_name": "wire_funds"}, {}),
    ViolationClass.FIGURE_NOT_IN_RECORD: ({"body": "The round is $8M."}, {}),
    ViolationClass.SEND_CAP_EXCEEDED: ({}, {"sent_count": 50}),
}


def _draft(**overrides) -> Draft:
    base = dict(thread=(Message(role="investor", body="?"),), body="Hello.", cited_fields=(),
                recipient_jurisdiction="US", recipient_domain="example.test", tool_name="send_message")
    base.update(overrides)
    return Draft(**base)


@pytest.mark.parametrize("table,covered", [
    ("ACT_COVERAGE", ACT_CLASSES),
    ("CHECKER_COVERAGE", CONTENT_CLASSES),
    ("TRIPWIRE_COVERAGE", CONTENT_CLASSES),
])
def test_emptying_any_one_lane_reports_exactly_the_classes_that_lane_covered(monkeypatch, table, covered):
    """The direction that hides. Design spec 10.4 says a class with no detector must fail a test,
    and the four tests above all read the map where a detector is *present* -- so a lane that
    silently stopped counting would leave every one of them green.

    Three rows because there are three lanes and the brief's negative control covers one of them.
    `ACT_COVERAGE` and `CHECKER_COVERAGE` had none, and they are the two hand-written tables:
    `TRIPWIRE_COVERAGE` **is** `TRIPWIRE_CLASSES`, the same object, so that row is the only one
    where the lane cannot drift from the detector behind it in the first place.

    Equality, not membership. `in` passes while a lane reports one class of the several it lost,
    and the expected set is the enum's own family membership rather than a second copy of the
    table under test -- so a row cannot agree with a mutant by being derived from it.
    """
    import tools.coverage_map as cm
    assert covered, "an empty expected set would make the equality below assert nothing"
    monkeypatch.setattr(cm, table, frozenset())
    assert set(cm.uncovered_classes()) == set(covered)


def test_the_declared_act_detector_is_bound_to_the_module_that_implements_it():
    """`ACT_COVERAGE` is a hand-written literal and `evaluate_act_classes` is a separate module, so
    the map's answer for the act lane is a claim about an implementation the map never reads.

    Measured, with `evaluate_act_classes` replaced by `lambda *_: ()`: a draft over the send cap is
    **allowed**, and `tools/coverage_map.py` still exits 0. The map cannot see a deleted detector.
    This is the binding that lets it, in the shape Task 9 already uses for `TRIPWIRE_CLASSES`
    against the pattern table -- both directions, because each is silent on its own:

    - **declared implies implemented**, behaviourally: every class in `ACT_COVERAGE` is emitted by
      the real predicate on an input differing from a compliant one in exactly one field. A
      deleted branch fails here.
    - **implemented implies declared**, structurally: every `ViolationClass` member the module
      constructs is in `ACT_COVERAGE`. A branch added with no map entry fails here, which the
      behavioural direction structurally cannot see -- nobody writes the triggering input for a
      detector they have forgotten to declare.

    The structural half is a walk over statically visible attribute access, in the manner of
    `tools/static_audit.py` and with the same bound: `getattr(ViolationClass, name)` would evade
    it. It makes accidental drift impossible, not deliberate drift impossible.

    `validate_citations` is deliberately not consulted. It emits `FIGURE_NOT_IN_RECORD` too, but
    the detector this lane is named for is `act_classes`, and binding the label to the module it
    names is the point.
    """
    assert set(_ACT_TRIGGERS) == set(ACT_COVERAGE), "a declared act-class with no triggering input"
    for klass, (draft_overrides, context_overrides) in _ACT_TRIGGERS.items():
        context = dataclasses.replace(CONTEXT, **context_overrides)
        findings = evaluate_act_classes(_draft(**draft_overrides), RECORD, context)
        assert [f.violation_class for f in findings] == [klass], f"{klass.value} is declared, not detected"

    constructed = {
        node.attr for node in ast.walk(ast.parse(inspect.getsource(act_classes_module)))
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
        and node.value.id == "ViolationClass"
    }
    assert constructed == {klass.name for klass in ACT_COVERAGE}


def test_the_coverage_tables_are_the_families_the_enum_declares():
    """The enum is the registry of constraint classes; the three tables are claims about it.

    Held to the registry rather than to each other, because the failure this closes is a table
    that drifts while every reading of it stays self-consistent. `uncovered_classes()` cannot see
    it: measured, `ACT_COVERAGE | {NEGOTIATES_TERMS}` leaves `uncovered_classes()` empty and
    `main()` at 0, while `detectors_for(NEGOTIATES_TERMS)` starts reporting `act_classes` -- a
    content class advertising a deterministic detector, which is the one claim this repository
    does not permit about a content class.

    `ACT_CLASSES` and `CONTENT_CLASSES` are derived from `ViolationClass.family` rather than
    written out, so this is a table against a derivation and not two literals agreeing.

    **Both content lanes covering the whole family is today's state, not a rule.** The checker's
    reach is a property of a model and a prompt, measured in the evals and not structurally
    enforceable here; the tripwire table's reach is measured in `tests/policy/test_tripwires.py`.
    What this asserts is that nothing is *declared* covered that the family does not contain, and
    that no member of the family is left out of a declaration.
    """
    assert ACT_COVERAGE == ACT_CLASSES
    assert CHECKER_COVERAGE == CONTENT_CLASSES
    assert TRIPWIRE_COVERAGE == CONTENT_CLASSES


def test_other_is_the_only_class_the_map_is_allowed_to_exempt():
    """`uncovered_classes` skips `Family.UNCLASSIFIED` by family, so the exemption is not `OTHER`'s
    alone -- it belongs to any member whose value carries neither prefix.

    Measured on the shipped module: a tenth member `ESCALATION_REQUIRED = "escalation_required"`
    added to `ViolationClass` is exempted and `main()` exits 0, while the same member spelled
    `content:escalation_required` is reported and exits 1. So "you cannot silently forget a class"
    holds for a class that names its family, and not for one that does not.

    Closing that inside `uncovered_classes` changes the map's contract and is not this task's to
    take, so this holds the enum end instead: a new unclassified member fails here even though the
    tool still exits 0. `OTHER` is the one member that legitimately has no detector -- it is the
    class carried when the checker gives no usable answer, and there is nothing to detect.
    """
    exempt = [klass for klass in ViolationClass if klass.family is Family.UNCLASSIFIED]
    assert exempt == [ViolationClass.OTHER]


def test_the_tool_exits_nonzero_and_names_the_class_when_one_is_uncovered(monkeypatch, capsys):
    """CI runs `python tools/coverage_map.py`, so the exit code is the enforcement, and nothing
    else in this file executes it. `uncovered_classes()` returning the right list is not the guard
    if `main()` prints it and exits 0 anyway.

    The printed line is asserted too: an exit code says a class is uncovered and not which, and
    10.4's promise is that you cannot silently forget a class -- a bare `1` in CI names nothing.
    """
    assert main() == 0
    assert capsys.readouterr().out == ""

    import tools.coverage_map as cm
    monkeypatch.setattr(cm, "CHECKER_COVERAGE", frozenset())
    assert cm.main() == 1
    printed = capsys.readouterr().out
    for klass in CONTENT_CLASSES:
        assert f"uncovered constraint class: {klass.value}" in printed


# --- Below this line, and ONLY below it, are limits: behaviours asserted in executable form
# --- because they are known and unclosed, not because they are wanted. The one is named rather
# --- than counted, following the banner in tests/policy/test_citations.py, which records that a
# --- counted banner once instructed a maintainer to delete a guard.
# ---
# --- For a test below: if it fails, a limit has been closed. Delete it, do not repair it.
# ---   test_a_class_outside_both_families_is_reported_covered_a_known_limit
# --- Every test ABOVE this line is a guard: if one fails, a class has lost its detector or a
# --- declaration has drifted from what implements it. Fix upstream.


def test_a_class_outside_both_families_is_reported_covered_a_known_limit():
    """The map answers "covered" for a class with no detector at all, and this is that in
    executable form rather than in a comment.

    `detectors_for(OTHER)` is empty and `uncovered_classes()` does not contain it, so the tool
    exits 0 over a class nothing detects. That is correct for `OTHER` -- it is what a finding
    carries when the checker gave no usable answer, and a detector for it would be a category
    error -- but the exemption is granted by family rather than by identity, so it extends to any
    member added with neither prefix. The guard above holds the enum end of that; this records
    what the tool itself does.

    Closing it means `uncovered_classes` distinguishing "unclassified and expected" from
    "unclassified and forgotten", which is a change to the map's contract: today the map has no
    notion of an expected exemption, and inventing one here would put a second allowlist beside
    the three coverage tables.
    """
    assert detectors_for(ViolationClass.OTHER) == []
    assert ViolationClass.OTHER not in uncovered_classes()
