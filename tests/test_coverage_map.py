import ast
import dataclasses
import inspect
from enum import Enum
from pathlib import Path

import pytest

from tools.coverage_map import detectors_for, uncovered_classes
from chaperone.policy.types import Family, ViolationClass

import tools.coverage_map as cm_module
from chaperone.gates.engine import ACT_CLASSES, CONTENT_CLASSES
from chaperone.policy import act_classes as act_classes_module
from chaperone.policy.act_classes import ActContext, evaluate_act_classes
from chaperone.policy.types import Draft, Message, Record
from tools.coverage_map import ACT_COVERAGE, CHECKER_COVERAGE, EXEMPT, TRIPWIRE_COVERAGE, main


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
# ---       -- `EXEMPT | {ViolationClass.NEGOTIATES_TERMS}`, the shape of silencing this tool for
# ---          a class rather than closing the gap it reports.
# ---   test_the_tool_exits_nonzero_and_names_the_class_when_one_is_uncovered
# ---       -- `return 1 if found else 0` weakened to `return 0`, and the `print` removed.
# ---   test_a_stand_in_registry_is_interchangeable_with_the_real_one
# ---       -- `_registry` built on a plain `str` mixin with no `family`, and built with mismatched
# ---          member names: the harness every guard below it rests on, measured not assumed.
# ---   test_a_run_that_classified_nothing_fails_rather_than_reporting_clean
# ---       -- `violations()` with its classified-nothing branch removed and nothing else changed:
# ---          both rows fail and no other test moves. Also watched against the module as it stood
# ---          before this round, where it had no such branch at all.
# ---   test_a_class_that_names_no_family_is_reported_rather_than_exempted
# ---       -- `uncovered_classes` restored to skipping a family with no `REQUIRED_DETECTORS`
# ---          entry, which is the hole verbatim: this test alone fails, and the tool exits 0.
# ---       Both assert through `main()` because the exit code is the property, and both hold the
# ---       tool end of design spec 10.4 rather than the suite end.
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


class _FamilyStr(str):
    """The mixin a stand-in registry is built on, carrying the **real** family derivation.

    `ViolationClass.family.fget` rather than a copy of its four lines: a second derivation in the
    tests would let the registry a stand-in expresses drift from the one the tool reads, which is
    the failure this whole file exists to catch.
    """

    family = property(ViolationClass.family.fget)


def _registry(name: str, members: dict[str, str]):
    """A stand-in for `ViolationClass`, for registries the real enum cannot be made to express.

    `uncovered_classes` resolves `ViolationClass` from the module globals at call time, so pointing
    that name at one of these drives the shipped code over a registry of choice -- no copy of the
    tool, and `main()` is the same `main()` CI runs.

    Members carry the real names and values, so they are found in the real coverage tables and in
    `EXEMPT`: an enum member's hash is its **name**'s and a str-mixin member's equality is its
    **value**'s, so a stand-in agreeing on both is interchangeable with the real member in a
    `frozenset`. That is measured in `test_a_stand_in_registry_is_interchangeable_with_the_real_one`
    below rather than assumed, because every test using this helper rests on it.
    """
    return Enum(name, members, type=_FamilyStr)


_REAL_MEMBERS = {klass.name: klass.value for klass in ViolationClass}


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
    - **named implies declared**, structurally: every `ViolationClass` member the module **names**
      is in `ACT_COVERAGE`. A branch added with no map entry fails here, which the behavioural
      direction structurally cannot see -- nobody writes the triggering input for a detector they
      have forgotten to declare.

    "Names", not "emits", and the distinction is the assertion's bound rather than a quibble: an
    `ast.Attribute` walk sees a member written down, and cannot see whether the branch around it
    ever runs. So a member named in dead code fails this too. That is the direction to fail in --
    a spurious failure sends someone to read one function, where the reverse lets a detector go
    undeclared -- but it is not a claim that the module emits exactly these.

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
    """`EXEMPT` is the map's disarm surface: every name in it is a class the map stops asking about.

    So it is pinned by equality against the one class that legitimately has none -- `OTHER`, which
    a finding carries when the checker gave no usable answer, where there is nothing to detect and
    a detector would be a category error. A second entry fails here, which is the same treatment
    CLAUDE.md gives `tools/static_audit.py`'s allowed list: if the map reports a class, close the
    gap rather than exempting the class.

    It used to be an exemption of the whole `UNCLASSIFIED` **family**, and this test used to assert
    that family had one member. That was the weaker statement it looks like: the family exemption
    was granted before any detector was consulted, so a member added with neither prefix inherited
    it and `python tools/coverage_map.py` exited 0 over a forgotten class. The exemption is now by
    identity and the family assertion below is no longer load-bearing -- it is kept because a
    second `UNCLASSIFIED` member is still worth a human looking at, and it now fails **beside** the
    tool rather than instead of it.
    """
    assert EXEMPT == frozenset({ViolationClass.OTHER})
    assert [klass for klass in ViolationClass if klass.family is Family.UNCLASSIFIED] == [ViolationClass.OTHER]


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


def test_a_stand_in_registry_is_interchangeable_with_the_real_one():
    """Every test below rests on this, so it is measured rather than assumed.

    An `Enum` member hashes as its **name** and a `str`-mixin member compares as its **value**, so
    a stand-in agreeing on both is found in a `frozenset` of real members and vice versa. Get
    either wrong and the tests below would report every class uncovered for the wrong reason --
    passing while measuring nothing about the guard they are named for.
    """
    stand_in = _registry("Same", _REAL_MEMBERS)
    assert stand_in.ADVISES_ON_MERITS in CHECKER_COVERAGE
    assert stand_in.NO_APPROVAL_TOKEN in ACT_COVERAGE
    assert stand_in.OTHER in frozenset({ViolationClass.OTHER})
    assert [klass.family for klass in stand_in] == [klass.family for klass in ViolationClass]


@pytest.mark.parametrize("label,members", [
    ("an empty registry", {}),
    ("nothing but the exempt class", {"OTHER": "other"}),
])
def test_a_run_that_classified_nothing_fails_rather_than_reporting_clean(monkeypatch, capsys, label, members):
    """Design spec 10.4 exists so you cannot silently forget a class, and a run that classified nothing has
    forgotten every class at once.

    This is the third time this project has met the shape: `tools/static_audit.py::_files_to_audit`
    records the first two -- a missing package root and a root with no Python files, both of which
    used to report clean -- and its answer is the one reused here rather than a second convention
    invented beside it: the "examined nothing" case produces a **violation line**, so it travels
    the same path to the same exit code as any other finding.

    Both rows are reachable without a stand-in registry, which is why they are guards and not
    curiosities: dropping the `act:`/`content:` prefixes from `ViolationClass`'s values disarms the
    whole map, because the prefix is the only thing `family` reads -- and the second row is what a
    registry pruned back to its plumbing class looks like.
    """
    import tools.coverage_map as cm
    monkeypatch.setattr(cm, "ViolationClass", _registry("Nothing", members))
    assert cm.main() == 1, f"{label}: the map reported clean over a registry it classified nothing in"
    assert "classified nothing" in capsys.readouterr().out


def test_a_class_that_names_no_family_is_reported_rather_than_exempted(monkeypatch, capsys):
    """The exemption belongs to `OTHER`, not to every class that fails to name a family.

    `uncovered_classes` used to skip on `klass.family is Family.UNCLASSIFIED`, so a member added
    with neither prefix inherited `OTHER`'s free pass: measured on the real enum, a tenth member
    `ESCALATION_REQUIRED = "escalation_required"` left `python tools/coverage_map.py` at exit 0
    while the same member spelled `content:escalation_required` exited 1. Design spec 10.4's
    sentence is about the tool, and CI runs the tool, so a guard living only in the suite left the
    promise depending on which entry point someone used.

    Asserted through `main()` -- the exit code is the property -- and the printed line is asserted
    too, because an exit code says a class was forgotten and not which one.
    """
    import tools.coverage_map as cm
    extended = _registry("Extended", {**_REAL_MEMBERS, "ESCALATION_REQUIRED": "escalation_required"})
    monkeypatch.setattr(cm, "ViolationClass", extended)
    assert cm.main() == 1
    assert "uncovered constraint class: escalation_required" in capsys.readouterr().out

    unchanged = _registry("Unchanged", _REAL_MEMBERS)
    monkeypatch.setattr(cm, "ViolationClass", unchanged)
    assert cm.main() == 0, "the real registry must still report clean, or the guard above is trivial"


# --- Below this line, and ONLY below it, are limits: behaviours asserted in executable form
# --- because they are known and unclosed, not because they are wanted. The one is named rather
# --- than counted, following the banner in tests/policy/test_citations.py, which records that a
# --- counted banner once instructed a maintainer to delete a guard.
# ---
# --- For a test below: if it fails, a limit has been closed. Delete it, do not repair it.
# ---   test_the_tool_reads_no_act_or_checker_detector_a_known_limit
# ---
# --- One limit was deleted here rather than repaired, per that rule:
# ---   test_a_class_outside_both_families_is_reported_covered_a_known_limit recorded that the map
# ---   exempted the whole UNCLASSIFIED family, so a member added with neither prefix was reported
# ---   covered. `EXEMPT` closed it -- the exemption is by identity now -- and the behaviour it
# ---   pinned is asserted from the other side by the two guards above.
# ---
# --- Every test ABOVE this line is a guard: if one fails, a class has lost its detector or a
# --- declaration has drifted from what implements it. Fix upstream.


def test_the_tool_reads_no_act_or_checker_detector_a_known_limit():
    """The tool's answer is invariant under **every** change to the act and checker detectors,
    because it imports neither. Deleting `evaluate_act_classes` outright leaves
    `python tools/coverage_map.py` at exit 0 -- measured, along with the send it then allows.

    Asserted as the import set rather than by mutating a detector, because that is the structural
    fact that produces the limit and it holds without touching another module. `TRIPWIRE_COVERAGE`
    is the exception and the reason this is worth stating: it **is** `TRIPWIRE_CLASSES`, so that
    one lane genuinely cannot drift, and the asymmetry between the three tables is invisible from
    the outside.

    Closing it means the map running detectors rather than declaring them, which is a different
    tool: a detector needs a draft, a record and an `ActContext` to run at all, and inventing
    fixtures inside a coverage map would make the map's answer depend on how good they were.
    `test_the_declared_act_detector_is_bound_to_the_module_that_implements_it` is where that lives
    instead, in the suite where fixtures belong -- so the binding exists, and this records only
    that it is not in the tool.
    """
    source = ast.parse(Path(cm_module.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(source):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert {name for name in imported if name.split(".")[0] == "chaperone"} == {
        "chaperone.policy.tripwires", "chaperone.policy.types"
    }
