import ast
import dataclasses
import inspect

import pytest

from chaperone.gates.checker import Checker, CheckerUnavailable, FlagForReview, Verdict
from chaperone.gates.engine import decide, denial_result, disposition_for
from chaperone.policy.act_classes import ActContext, evaluate_act_classes
from chaperone.policy.citations import validate_citations
from chaperone.policy.tripwires import evaluate_tripwires
from chaperone.policy.types import (
    Decision, Disposition, Draft, Finding, Message, Record, ViolationClass,
)

RECORD = Record(fields={"round_size": "10000000"})
CONTEXT = ActContext(approval_token="tok", tier=2, consented_jurisdictions=frozenset({"US"}),
                     granted_tools=frozenset({"send_message"}), sent_count=0, send_cap=50)


def _draft(body: str, **overrides) -> Draft:
    base = dict(thread=(Message(role="investor", body="?"),), body=body, cited_fields=(),
                recipient_jurisdiction="US", recipient_domain="example.test", tool_name="send_message")
    base.update(overrides)
    return Draft(**base)


def _checker(result):
    if isinstance(result, Exception):
        def transport(_):
            raise result
    else:
        def transport(_):
            return result
    return Checker("sonnet-tier", "sonnet-tier", transport=transport, retries=0)


CLEAN = Verdict(violates=False, confidence=0.9)


def test_a_clean_draft_with_a_clean_checker_is_allowed():
    decision = decide(_draft("The round is $10M."), RECORD, CONTEXT, _checker(CLEAN))
    assert decision.allowed is True
    assert decision.disposition is Disposition.ALLOW


def test_an_act_class_finding_blocks_without_consulting_the_checker():
    consulted = False

    def transport(_):
        nonlocal consulted
        consulted = True
        return CLEAN

    checker = Checker("sonnet-tier", "sonnet-tier", transport=transport, retries=0)
    decision = decide(_draft("hello", recipient_jurisdiction="DE"), RECORD, CONTEXT, checker)
    assert decision.allowed is False
    assert consulted is False


def test_a_checker_flag_blocks_even_when_no_tripwire_fires():
    verdict = Verdict(violates=True, violation_class=ViolationClass.ADVISES_ON_MERITS,
                      confidence=0.8, span="move quickly")
    decision = decide(_draft("Between us, I would move quickly on this one."), RECORD, CONTEXT, _checker(verdict))
    assert decision.allowed is False


def test_a_tripwire_blocks_even_when_the_checker_says_compliant():
    """The second disjunct. A checker false negative is caught for the detectable slice."""
    decision = decide(_draft("Returns are guaranteed."), RECORD, CONTEXT, _checker(CLEAN))
    assert decision.allowed is False
    assert ViolationClass.FORWARD_LOOKING_RETURN in [f.violation_class for f in decision.findings]


def test_checker_unavailability_fails_closed():
    decision = decide(_draft("The round is $10M."), RECORD, CONTEXT, _checker(TimeoutError("down")))
    assert decision.allowed is False
    assert decision.disposition is Disposition.REDIRECT_FUTILE


def test_flag_for_review_fails_closed_rather_than_passing():
    decision = decide(_draft("The round is $10M."), RECORD, CONTEXT, _checker(FlagForReview(reason="unclear")))
    assert decision.allowed is False


def test_advises_on_merits_is_futile_and_negotiates_terms_is_refinable():
    assert disposition_for((Finding(ViolationClass.ADVISES_ON_MERITS, "m", None),)) is Disposition.REDIRECT_FUTILE
    assert disposition_for((Finding(ViolationClass.NEGOTIATES_TERMS, "n", None),)) is Disposition.REDIRECT_REFINABLE


def test_a_futile_class_wins_when_mixed_with_a_refinable_one():
    findings = (Finding(ViolationClass.NEGOTIATES_TERMS, "n", None),
                Finding(ViolationClass.ADVISES_ON_MERITS, "m", None))
    assert disposition_for(findings) is Disposition.REDIRECT_FUTILE


def test_the_denial_result_is_categorized_and_not_retryable():
    decision = decide(_draft("Returns are guaranteed."), RECORD, CONTEXT, _checker(CLEAN))
    result = denial_result(decision)
    assert result["is_error"] is True
    assert result["is_retryable"] is False
    assert result["category"] == "content:forward_looking_return"


def test_the_denial_result_carries_the_violating_span_verbatim():
    decision = decide(_draft("Returns are guaranteed."), RECORD, CONTEXT, _checker(CLEAN))
    assert "guaranteed" in denial_result(decision)["span"]


def test_disposition_is_derived_from_category_in_one_place():
    """Not per call site. The mapping has exactly one definition."""
    import inspect
    from chaperone.gates import engine
    source = inspect.getsource(engine)
    assert source.count("FUTILE_CLASSES") == 2  # the definition and its single use


# --- Guards below. Named rather than counted, following the banner in test_citations.py, which
# --- records that a counted banner once instructed a maintainer to delete a guard. Each was
# --- watched failing against the mutant it names, applied to the shipped module:
# ---   test_a_violation_reported_without_a_class_denies_rather_than_allowing
# ---       -- the fail-open this task closed: `if result.violates and result.violation_class is
# ---          not None` produced no finding for a verdict that reported a violation and named
# ---          no class, and with no tripwire hit `decide` returned allowed.
# ---   test_the_futile_set_and_both_redirect_dispositions_are_named_only_in_disposition_for
# ---       -- the behavioural companion to the substring count above, and the guard on design
# ---          spec 4.7's single-site rule, which the substring count structurally cannot see.
# ---          Mutants: the reader moved into `decide` with the count unchanged at two, and a
# ---          fourth site hardcoding a disposition literal.
# ---   test_an_act_class_no_redraft_can_answer_escalates_instead_of_burning_the_budget
# ---   test_a_correctable_figure_stays_refinable_so_the_budget_is_not_skipped
# ---       -- the two sides of 4.8's routing: the four unrecoverable act-classes dropped back to
# ---          refinable, and the futile set widened to swallow the one that is recoverable.
# ---   test_every_unusable_checker_answer_is_futile_by_derivation_and_not_by_a_literal
# ---   test_a_verdict_that_names_the_unclassified_class_is_futile_too
# ---       -- any one of the FOUR routes carrying an unclassifiable class changing answer under
# ---          the derivation. Four, counted by grepping the construction sites: three plumbing
# ---          failures and one considered verdict. The first version of this list said three.
# ---   test_the_denial_result_renders_the_class_and_the_disposition_as_their_values
# ---       -- dropping `.value` from either field in `denial_result`.
# ---   test_a_refinable_denial_is_still_not_retryable
# ---       -- `"is_retryable": decision.disposition is Disposition.REDIRECT_REFINABLE`.
# ---   test_every_denial_in_the_battery_is_a_usable_denial_result
# ---   test_no_decision_in_the_battery_is_allowed_while_carrying_findings
# ---       -- a denial returned with an empty finding tuple, and an allow returned carrying one.
# ---
# --- Three more guards, listed apart because they were watched failing against something other
# --- than a mutant and the sentence above stays true only of the entries it covers:
# ---   test_an_unavailable_checker_names_the_outage_on_the_decision
# ---   test_a_judged_violation_carries_no_outage
# ---       -- watched failing against `Decision` carrying no `outage` field at all. The pair is
# ---          what stops the field from being set on every denial or on none.
# ---   test_every_battery_cell_exercises_the_mechanism_its_label_names
# ---       -- watched failing against the SHIPPED `_CHECKERS` fixture rather than a mutant, which
# ---          is the stronger provenance. The "violating" verdict quoted a span present in only
# ---          one of the five bodies, so `Checker.check` refused it everywhere else and all four
# ---          cells that reached a checker took the "down" route while wearing the other label.
# ---          Both battery tests stayed green throughout: their assertions are structural and
# ---          neither reads a span. The fixture is corrected below; this is what holds it.
# --- Every test above this banner is the brief's own.


class _RawChecker:
    """Hands `decide` exactly what it is given, without passing through `Checker.check`.

    Deliberately not a `Checker`. Task 10 made `Checker.check` refuse a verdict the gate cannot
    act on, spend the retry budget on it and then raise -- so no `Checker` can put one in front of
    `decide`, and a test that used one would be measuring the layer below instead of this one.
    The bypass is not hypothetical: Task 17 builds `Verdict`s straight from recorded JSON and
    never calls `check()` at all.
    """

    def __init__(self, result: object) -> None:
        self._result = result

    def check(self, draft: Draft, record: Record) -> object:
        return self._result


_BODIES = (
    "The round is $10M.",
    "hello",
    "Returns are guaranteed.",
    "Honestly, this is a strong deal.",
    "They would probably accept $8M instead of $10M.",
)
_CHECKERS = (
    ("clean", lambda: _checker(CLEAN)),
    ("flag", lambda: _checker(FlagForReview(reason="unclear"))),
    # `span=None`, which is the minimal fix and not the only one. A verdict's span must be a
    # verbatim substring of the draft body, and this one verdict is decided against all five bodies
    # -- so any single quoted span is absent from four of them, `Checker.check` refuses it, and the
    # cell routes through `CheckerUnavailable` while still being labelled "violating". Measured on
    # the `"accept $8M"` this replaces: all four cells that reach a checker took the "down" route.
    # A per-body span table would serve equally well and would cost a fixture rebuild; quoting
    # nothing costs nothing, because no battery test reads a span.
    # `test_every_battery_cell_exercises_the_mechanism_its_label_names` is what holds this.
    ("violating", lambda: _checker(Verdict(violates=True, violation_class=ViolationClass.NEGOTIATES_TERMS,
                                           confidence=0.7, span=None))),
    ("unnamed", lambda: _RawChecker(Verdict(violates=True, confidence=0.9))),
    ("down", lambda: _checker(TimeoutError("down"))),
)


def _battery_cells() -> list[tuple[tuple[str, str, str], Draft, Decision]]:
    """Every combination of body, checker answer and jurisdiction: its key, its draft, its decision.

    A battery, not a proof: it is the cross product of five bodies chosen to reach each branch,
    five checker answers and both jurisdictions. It says nothing about a body nobody thought of.

    **The key is a tuple and the draft comes back with the decision**, both so that a test can ask
    which checker answer a cell was built from without parsing a formatted string, and so that
    asking what the act lane does to a cell needs no second construction of the draft -- a second
    one would be free to drift from this one, and the label test below would then be asserting
    about a draft the battery never decided.
    """
    cells = []
    for body in _BODIES:
        for name, make in _CHECKERS:
            for juris in ("US", "DE"):
                draft = _draft(body, recipient_jurisdiction=juris)
                cells.append(((body, name, juris), draft, decide(draft, RECORD, CONTEXT, make())))
    return cells


def _battery() -> dict[tuple[str, str, str], Decision]:
    """The battery keyed by cell, for the tests that read only the decisions."""
    return {key: decision for key, _, decision in _battery_cells()}


def _checker_contribution(draft: Draft, decision: Decision) -> tuple[Finding, ...]:
    """The findings the checker contributed, with the tripwire findings stripped off the end.

    `decide` writes `checker_findings + tripwire_findings` on every route that consults a checker and
    gets an answer back, so the tripwire findings are a suffix and whatever precedes them is what the
    checker added.

    **This restates `decide`'s composition, which is the same coupling shape the act-clean filter in
    the label test carries, and it is the lesser case.** There the drift is silent in one direction;
    here it is loud in both. `decide` composing findings some other way makes the suffix stop matching
    and the whole tuple come back, so a cell reports a mechanism its label does not name and the test
    fails rather than quietly measuring the wrong thing. Checking the suffix instead of assuming it is
    what buys that: the length arithmetic alone would slice a tuple it no longer describes.

    The non-matching branch is also **defensive rather than reached**. The unavailable route appends
    no tripwires and would land there, but no caller brings it: `_mechanism_shown` answers on `outage`
    before calling this, and the class loop calls it only for `"violating"` cells.
    """
    tripwires = evaluate_tripwires(draft)
    if tripwires and decision.findings[-len(tripwires):] == tripwires:
        return decision.findings[:-len(tripwires)]
    return decision.findings


def _mechanism_shown(draft: Draft, decision: Decision) -> str:
    """Which `_CHECKERS` answer this decision shows it acted on, read off the decision's own effects.

    The return value is compared against the cell's label, so a cell whose label names one mechanism
    and whose decision shows another fails rather than being counted.

    **"Off effects" and not "off effects alone".** Nothing here spies an invocation or reconstructs
    what the answer should have been, which is the part that matters. But `_checker_contribution`
    re-runs `evaluate_tripwires(draft)` to find the suffix to strip, so one policy predicate is
    evaluated rather than read off the decision, and the summary line does not claim otherwise.

    **Only one of the three plumbing routes is structural, and it is the one this task added.**
    `outage` names the unavailable route as a field. The flag route and the violation-without-a-class
    route both produce `OTHER` and differ only in the prose `decide` writes into `Finding.detail`, so
    they are told apart by that prose -- a substring match over a sentence in another module, which is
    the weakest discriminator here and is named as such rather than left to be discovered. An
    unrecognised shape returns a string no label equals, so it fails loudly instead of being
    attributed to whichever branch happened to be last.
    """
    if decision.outage is not None:
        return "down"
    contributed = _checker_contribution(draft, decision)
    if not contributed:
        return "clean"
    first = contributed[0]
    if first.violation_class is not ViolationClass.OTHER:
        return "violating"
    detail = first.detail or ""
    if "flagged for review" in detail:
        return "flag"
    if "without naming a class" in detail:
        return "unnamed"
    return f"unrecognised({first.violation_class.value}: {detail!r})"


def _engine_functions(matches) -> set[str]:
    """The name of every function in `engine` containing a node `matches` accepts.

    Structural, in the manner of tools/static_audit.py, and a denylist over a statically visible
    tree rather than a sandbox: `getattr(Disposition, "REDIRECT_FUTILE")` or a shadowing
    redefinition would walk past this, exactly as `tools/static_audit.py` records for its own
    checks. The job is to make accidental drift impossible and deliberate drift conspicuous.
    """
    from chaperone.gates import engine

    found = set()
    for node in ast.walk(ast.parse(inspect.getsource(engine))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(matches(inner) for inner in ast.walk(node)):
                found.add(node.name)
    return found


def _functions_reading(name: str) -> set[str]:
    """Every function whose body reads the global `name`.

    A mention in a docstring or a comment produces no `ast.Name` node and so cannot satisfy this,
    and a second reader produces one wherever in the module it is written.
    """
    return _engine_functions(lambda n: isinstance(n, ast.Name) and n.id == name)


def _is_disposition(member: str):
    return (
        lambda n: isinstance(n, ast.Attribute)
        and n.attr == member
        and isinstance(n.value, ast.Name)
        and n.value.id == "Disposition"
    )


def _functions_naming_disposition(member: str) -> set[str]:
    """Every function whose body writes `Disposition.<member>` as a literal."""
    return _engine_functions(_is_disposition(member))


def _disposition_literals(member: str) -> int:
    """How many times `Disposition.<member>` is written anywhere in the module.

    The count is needed beside the function attribution, not instead of it. Attribution alone is
    per-function, so a *second* literal added inside a function that already names that member
    changes no set and slips through -- measured, as mutant `M6`, against the first version of
    this helper, which had only the attribution.
    """
    from chaperone.gates import engine

    return sum(1 for n in ast.walk(ast.parse(inspect.getsource(engine))) if _is_disposition(member)(n))


def test_a_violation_reported_without_a_class_denies_rather_than_allowing():
    """Design spec 4.3: a schema-valid but invalid verdict maps to deny. It used to map to allow.

    `Verdict(violates=True, violation_class=None)` is schema-valid -- the field is
    `ViolationClass | None` -- and unusable: the checker has said a violation occurred and
    declined to name it. Task 10 closed this at the checker, so the second half below pins that
    layer too and the two cannot drift. This half pins the shape at *this* layer, which reads
    `.violates` off whatever it is handed regardless of who produced it.
    """
    unnamed = Verdict(violates=True, confidence=0.97)
    decision = decide(_draft("The round is $10M."), RECORD, CONTEXT, _RawChecker(unnamed))
    assert decision.allowed is False
    assert decision.disposition is Disposition.REDIRECT_FUTILE

    through_checker = decide(_draft("The round is $10M."), RECORD, CONTEXT, _checker(unnamed))
    assert through_checker.allowed is False
    assert "unavailable" in through_checker.findings[0].detail


def test_the_futile_set_and_both_redirect_dispositions_are_named_only_in_disposition_for():
    """The behavioural companion to the substring count above, and it holds more than that count.

    Three things, each a different way the same drift returns:

    1. The futile set has exactly one module-level definition, and exactly one function reads it.
    2. Neither redirect disposition is written as a literal outside `disposition_for`. This is the
       half the substring count above **cannot** see: it counts occurrences of the set's name,
       which stay at two however many times `Disposition.REDIRECT_FUTILE` is hardcoded elsewhere,
       so the test named for design spec 4.7's single-site rule did not test it. `decide` used to
       hardcode the futile disposition on three paths and both instruments passed.
    3. `Disposition.ALLOW` is written exactly twice -- `disposition_for`, which derives it, and
       `decide`'s clean return. That second site is the one remaining per-call-site disposition
       literal in the module, and it is pinned **by name and by count** rather than carved out,
       so a third site fails this test instead of hiding behind an exemption.

    The counts sit beside the attribution because attribution alone is per-function: a second
    literal inside a function that already names that member changes no set. That gap was real
    and was measured, not reasoned about -- see `_disposition_literals`.

    What it does not hold is in `_engine_functions`' docstring: this is a walk over statically
    visible attribute access, not a sandbox.
    """
    from chaperone.gates import engine

    tree = ast.parse(inspect.getsource(engine))
    definitions = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "FUTILE_CLASSES" for target in node.targets)
    ]
    assert len(definitions) == 1
    assert _functions_reading("FUTILE_CLASSES") == {"disposition_for"}
    assert _functions_naming_disposition("REDIRECT_FUTILE") == {"disposition_for"}
    assert _functions_naming_disposition("REDIRECT_REFINABLE") == {"disposition_for"}
    assert _functions_naming_disposition("ALLOW") == {"disposition_for", "decide"}
    assert (_disposition_literals("REDIRECT_FUTILE"), _disposition_literals("REDIRECT_REFINABLE"),
            _disposition_literals("ALLOW")) == (1, 1, 2)


def test_the_destination_is_derived_in_one_place():
    """Where a redirect routes to is a function of the disposition, and of nothing at a call site.

    `disposition_for` decides *whether* a draft is redirected; this decides *where*, so a caller
    that wants to route one has a function to ask instead of a queue name to type. The two redirect
    members answer with the same destination -- futile and refinable differ in whether a redraft
    could help, not in who reads the result -- and that is asserted rather than assumed, because a
    reader could reasonably expect two queues.

    **The `None` clause covers exactly one member, and says so.** `Disposition` has three members
    and two of them are the redirects, so "everything else routes nowhere" is `ALLOW` alone. It is
    named rather than written as a comprehension over a set difference, which would read as a sweep
    over a large space and be one assertion either way. The membership assertion above it is what
    makes the claim survive a fourth member: adding one fails here, where the routing decision for
    it has to be taken, rather than passing quietly as "not a redirect".

    The destination is compared against the literal string. `destination_for` derives it from a
    module constant, and asserting against that constant would hold through a rename that silently
    moved every queued escalation to a queue no reader is watching.
    """
    from chaperone.gates.engine import destination_for

    assert set(Disposition) == {
        Disposition.ALLOW, Disposition.REDIRECT_REFINABLE, Disposition.REDIRECT_FUTILE,
    }, "a Disposition member was added; decide where it routes rather than letting this pass"

    routed = {member: destination_for(member) for member in Disposition}
    assert routed[Disposition.REDIRECT_FUTILE] == "human-review"
    assert routed[Disposition.REDIRECT_REFINABLE] == "human-review"
    assert routed[Disposition.ALLOW] is None


@pytest.mark.parametrize("klass,draft_overrides,context_overrides", [
    (ViolationClass.NO_APPROVAL_TOKEN, {}, {"approval_token": None}),
    (ViolationClass.JURISDICTION_NOT_CONSENTED, {"recipient_jurisdiction": "DE"}, {}),
    (ViolationClass.TOOL_OUTSIDE_GRANT, {"tool_name": "wire_funds"}, {}),
    (ViolationClass.SEND_CAP_EXCEEDED, {}, {"sent_count": 50}),
])
def test_an_act_class_no_redraft_can_answer_escalates_instead_of_burning_the_budget(
    klass, draft_overrides, context_overrides
):
    """Design spec 4.8: some denials are unrecoverable, and those skip the refinement budget.

    No redraft gives a message consent it never had, an approval token, a tool outside the grant
    or send budget that is spent. Routed refinable, each of these cost a redraft round and then
    stopped for `deadlock` with no alternative -- measured through Task 21's `refine` transcribed
    verbatim -- where 4.8 asks for an escalation at round zero.

    Asserted end to end through `decide` rather than on `disposition_for` alone, so it is the
    effect that is pinned; the finding list is compared exactly so each case is the class it says
    and not some other act-class firing alongside.
    """
    context = dataclasses.replace(CONTEXT, **context_overrides)
    decision = decide(_draft("hello", **draft_overrides), RECORD, context, _checker(CLEAN))
    assert [f.violation_class for f in decision.findings] == [klass]
    assert decision.disposition is Disposition.REDIRECT_FUTILE
    assert denial_result(decision)["disposition"] == "redirect_futile"


def test_a_correctable_figure_stays_refinable_so_the_budget_is_not_skipped():
    """The over-rejection side of the same edit, and it is the binding constraint on it.

    A figure can be corrected or removed, so `act:figure_not_in_record` is recoverable and must
    keep its budget. It is also the one act-class that cannot move: with it futile, Task 21's
    `test_the_budget_is_a_backstop_and_its_exhaustion_is_distinguishable` escalates at round zero
    and fails -- measured, not predicted. Pinning it from this side means the futile set cannot
    be widened by reflex.
    """
    decision = decide(_draft("The round is $8M."), RECORD, CONTEXT, _checker(CLEAN))
    assert [f.violation_class for f in decision.findings] == [ViolationClass.FIGURE_NOT_IN_RECORD]
    assert decision.disposition is Disposition.REDIRECT_REFINABLE
    assert disposition_for((Finding(ViolationClass.FIGURE_NOT_IN_RECORD, "f", None),)) is Disposition.REDIRECT_REFINABLE


def test_every_unusable_checker_answer_is_futile_by_derivation_and_not_by_a_literal():
    """All three checker-failure routes derive their disposition, and all three still escalate.

    Behaviour-preserving by construction: `OTHER` joining the futile set is exactly what makes
    `disposition_for` return what the three literals used to say. Asserted end to end on all
    three routes so the derivation cannot quietly change the answer on one of them, which is the
    failure mode a refactor from literals to a lookup actually has.
    """
    unavailable = decide(_draft("The round is $10M."), RECORD, CONTEXT, _checker(TimeoutError("down")))
    flagged = decide(_draft("The round is $10M."), RECORD, CONTEXT, _checker(FlagForReview(reason="unclear")))
    unnamed = decide(_draft("The round is $10M."), RECORD, CONTEXT, _RawChecker(Verdict(violates=True, confidence=0.9)))
    for decision in (unavailable, flagged, unnamed):
        assert decision.allowed is False
        assert decision.disposition is Disposition.REDIRECT_FUTILE
    assert disposition_for((Finding(ViolationClass.OTHER, "o", None),)) is Disposition.REDIRECT_FUTILE


def test_a_verdict_that_names_the_unclassified_class_is_futile_too():
    """The fourth route that carries `OTHER`, and the one I missed by enumerating from memory.

    `_reject_unusable` refuses a violation with **no** class. It does not refuse one classed
    `OTHER`, which pydantic accepts, so the real `Checker` returns such a verdict intact and it
    becomes a finding through the ordinary `checker_findings` path -- carrying the model's own
    confidence and span, which is what distinguishes this route from the three plumbing failures
    above and is why the span is asserted here rather than the detail.

    Futile is right on meaning: a category of `other` gives a redraft nothing to aim at. But this
    was not a decision anyone took -- widening the futile set to let the other three derive their
    disposition changed this path from `redirect_refinable` to `redirect_futile` as a side
    effect, and no test covered it. It does now.

    **The span is quoted from the body, and it did not have to be when this was written.** It read
    `"unclear bit"` against a body that never contained those words, which `Checker.check` now
    refuses as unusable -- so the fixture, not the property, is what changed. Every assertion below
    is the one that stood here before; only the two spellings of the span moved, and they moved to
    the one shape a real checker could have produced.
    """
    verdict = Verdict(violates=True, violation_class=ViolationClass.OTHER, confidence=0.8,
                      span="round is $10M")
    decision = decide(_draft("The round is $10M."), RECORD, CONTEXT, _checker(verdict))
    assert decision.allowed is False
    assert [f.violation_class for f in decision.findings] == [ViolationClass.OTHER]
    assert decision.disposition is Disposition.REDIRECT_FUTILE
    assert denial_result(decision)["span"] == "round is $10M"


def test_an_unavailable_checker_names_the_outage_on_the_decision():
    """A denial for downtime and a denial for a judgement are otherwise the same shape.

    Both are `allowed is False` carrying `OTHER` and a futile disposition, and the only thing that
    told them apart was prose inside `Finding.detail` -- which `Handoff` does not carry at all, so
    a human reviewing an escalation could not tell whether a checker had judged the draft or had
    been down. The difference is carried as a field because it is the reviewer's, not a grep's.
    """
    decision = decide(_draft("The round is $10M."), RECORD, CONTEXT, _checker(TimeoutError("down")))
    assert decision.allowed is False
    assert decision.outage is not None and "down" in decision.outage


def test_an_outage_the_transport_never_explained_still_says_something_actionable():
    """`str(TimeoutError())` is `""`, so the field could be non-`None` and falsy at the same time.

    `Checker.check` raises `CheckerUnavailable(str(last))`, and an exception raised with no message
    stringifies to the empty string -- so a real transport failure produced `outage=""`. Every test
    here asks `is not None` and would not have noticed. A consumer asking the natural question, `if
    decision.outage:`, reads an empty outage as a judgement, which is the direction that looks like a
    clean denial and so is never questioned. The empty string is the value a reviewer can act on
    least, so it is the one value this field must never carry.
    """
    decision = decide(_draft("The round is $10M."), RECORD, CONTEXT, _checker(TimeoutError()))
    assert decision.allowed is False
    assert decision.outage, "a falsy outage reads as no outage to any consumer testing truthiness"
    assert "unavailable" in decision.outage


def test_a_judged_violation_carries_no_outage():
    """The other direction, and the one that stops the field from just meaning "denied".

    A checker that answered, and answered "violates", is the case the outage field must stay empty
    on. Without this half, `outage=str(exc)` moved onto any denial branch would still pass the test
    above while telling the reviewer every block was downtime.
    """
    verdict = Verdict(violates=True, violation_class=ViolationClass.ADVISES_ON_MERITS,
                      confidence=0.9, span="a strong deal")
    decision = decide(_draft("Honestly, this is a strong deal."), RECORD, CONTEXT, _checker(verdict))
    assert decision.allowed is False
    assert decision.outage is None


def test_the_denial_result_renders_the_class_and_the_disposition_as_their_values():
    """`f"{ViolationClass.ADVISES_ON_MERITS}"` is `"ViolationClass.ADVISES_ON_MERITS"`.

    Measured identically on 3.11.15 and 3.13.9. Both enums subclass `str`, so equality, `in`,
    concatenation and `json.dumps` every one of them yield the value whether or not `.value` was
    taken -- which means `result["category"] == "content:forward_looking_return"` above passes
    against a bare member and asserts nothing about this. The runtime type and the formatted form
    are the two things that tell them apart, and a tool result is formatted and serialized by
    whoever receives it.
    """
    result = denial_result(decide(_draft("Returns are guaranteed."), RECORD, CONTEXT, _checker(CLEAN)))
    assert type(result["category"]) is str
    assert f"{result['category']}" == "content:forward_looking_return"
    assert type(result["disposition"]) is str
    assert f"{result['disposition']}" == "redirect_refinable"


def test_a_refinable_denial_is_still_not_retryable():
    """Two axes, and only one of them is `is_retryable`.

    A refinable denial invites a redraft, and that affordance is carried by `disposition` alone.
    `is_retryable` stays false on every denial, because design spec 3.4's failure is an executor
    reading a deny as "transient -- please retry the forbidden send". Reading the redraft
    affordance across into `is_retryable` is the tidy-looking edit that reopens it.
    """
    decision = decide(_draft("Returns are guaranteed."), RECORD, CONTEXT, _checker(CLEAN))
    result = denial_result(decision)
    assert result["disposition"] == Disposition.REDIRECT_REFINABLE.value
    assert result["is_retryable"] is False


def test_every_denial_in_the_battery_is_a_usable_denial_result():
    """A denial `decide` produces can always be turned into a categorized result, never a raise.

    `denial_result` indexes `findings[0]`, so a denial carrying no finding would raise
    `IndexError` -- and design spec 3.4's whole point is that a raise at this boundary is what a
    defensive executor relabels as retryable. This shows `decide` produces no such denial over
    the battery. It does not show `denial_result` is safe on a `Decision` assembled by hand
    elsewhere: on `Decision(False, (), ...)` it still raises, and nothing here changes that.
    """
    decisions = _battery()
    denied = {label: d for label, d in decisions.items() if not d.allowed}
    assert len(denied) > 1, "a battery with no denials would make the loop below assert nothing"
    categories = {klass.value for klass in ViolationClass}
    dispositions = {disposition.value for disposition in Disposition}
    for label, decision in denied.items():
        assert decision.findings, f"{label}: a denial with no finding makes denial_result raise"
        result = denial_result(decision)
        assert result["is_error"] is True, label
        assert result["is_retryable"] is False, label
        assert result["category"] in categories, label
        assert result["disposition"] in dispositions, label
        assert result["disposition"] != Disposition.ALLOW.value, label


def test_no_decision_in_the_battery_is_allowed_while_carrying_findings():
    """Allowed, empty findings and the ALLOW disposition move together, in both directions.

    An allow carrying a finding is a gate that found something and sent anyway; a denial carrying
    the ALLOW disposition is one the gateway logs as `denied` rather than `redirected` and that
    Task 21 would try to refine on nothing. Both directions are asserted because only one of them
    is the escape, and the other is how the escape gets introduced.
    """
    decisions = _battery()
    allowed = {label for label, d in decisions.items() if d.allowed}
    assert allowed, "a battery with no allows would leave the biconditional half-exercised"
    assert len(allowed) < len(decisions), "a battery with no denials would too"
    for label, decision in decisions.items():
        assert (decision.allowed is True) == (decision.findings == ()), label
        assert (decision.allowed is True) == (decision.disposition is Disposition.ALLOW), label


def test_every_battery_cell_exercises_the_mechanism_its_label_names():
    """The labels in `_CHECKERS` were decoration until `Decision.outage` existed to check them.

    A cell labelled `"violating"` is meant to exercise the checker-finding path and one labelled
    `"down"` the fail-closed path. Nothing held them to it. `Checker.check` refuses a span the draft
    body does not contain verbatim, and the `"violating"` verdict quoted `"accept $8M"` -- a
    substring of exactly one of the five bodies -- so on every other body that verdict was unusable
    and the cell routed through `CheckerUnavailable` instead. Measured before the fixture was
    corrected: **all four `"violating"` cells that reach a checker at all were taking the `"down"`
    route**, and both battery tests above stayed green throughout, because their assertions are
    structural and neither reads a span. `outage` is the first field that can see the difference.

    Two directions, and only one of them may be filtered:

    - **Over every cell, unfiltered**: an outage marks a `"down"` cell and nothing else. This is the
      direction that catches a mislabelled cell, so it is asserted over the whole battery. A filter
      here could exclude the very cells carrying the defect and pass.
    - **Over act-clean cells only**: a `"down"` cell carries an outage. `decide` returns on the act
      lane before consulting any checker, so on the cells the act lane denies -- every `DE` cell and
      every cell whose body names a figure the record does not hold -- no checker label names
      anything at all. The filter is `decide`'s own short-circuit condition, evaluated with the same
      two policy predicates, rather than a guess at which cells it covers. **`validate_citations` is
      the second of those two and fires on no cell here**, because `_draft` builds every body with
      `cited_fields=()`; it is called for parity with `decide` rather than because it contributes,
      and dropping it as dead would silently narrow the filter the first time a cell cites anything.

    The coverage assertion between them is what stops that filter from excluding everything: a label
    no act-clean cell carries is a mechanism this test would otherwise silently stop checking.

    **All five labels, not only the outage dimension.** The first version of this test asserted the
    outage direction alone, which held `"down"` to something and the other four labels to nothing but
    "not the outage route". Demonstrated, not argued -- and the numbers are the ones that were
    actually measured, an earlier draft of this paragraph having claimed more than the measurement
    showed. Flipping the `"violating"` verdict to `violates=False` turns **2 of its 10 cells into
    allows, 2 of the 4 that reach a checker**, and the outage-only version of this test stayed green
    through it. The other 8 stay denials for reasons that have nothing to do with the checker: 5 `DE`
    cells on `act:jurisdiction_not_consented`, the `$8M` `US` cell on `act:figure_not_in_record`, and
    2 `US` cells on tripwires -- `Returns are guaranteed.` and `Honestly, this is a strong deal.`
    Two escaping cells were enough, and that is the point: a fixture stops exercising the mechanism
    its label names without the battery changing shape. So each label is now compared against the
    mechanism its cell actually shows, by `_mechanism_shown`.

    **Scope, stated rather than hedged.** The universal is over cells that reach a checker. Thirty of
    the fifty reach none -- all twenty-five `DE` cells, denied `act:jurisdiction_not_consented`, and
    the five cells whose body names `$8M` against a record holding only `$10M`, denied
    `act:figure_not_in_record`. On those, no checker label names anything, and asserting a mechanism
    would be asserting about a call that never happened.
    """
    cells = _battery_cells()
    for (body, name, juris), _, decision in cells:
        if decision.outage is not None:
            assert name == "down", (
                f"{body!r}/{name}/{juris}: labelled as a checker that answered, and denied for a "
                "detector outage instead -- the cell exercises a mechanism its label does not name"
            )

    # The filter **restates** `decide`'s short-circuit (`engine.py`, first statement of `decide`)
    # rather than deriving from it, so a third act predicate added there would leave this copy stale.
    # No invocation spy is permitted here, and nothing else observable separates "the checker was
    # consulted" from "the act lane answered first", so this is the least-bad option rather than a
    # good one. **The drift is asymmetric and only one side is silent.** A predicate `decide` has and
    # this does not lets act-denied cells into the assertions below, where a `"down"` cell carrying no
    # outage fails loudly. A predicate this has and `decide` does not merely drops cells, and the
    # coverage assertion notices only if it drops every cell carrying some label -- that direction is
    # the quiet one.
    reachable = [
        (name, f"{body!r}/{name}/{juris}", draft, decision)
        for (body, name, juris), draft, decision in cells
        if not (evaluate_act_classes(draft, RECORD, CONTEXT) + validate_citations(draft, RECORD))
    ]
    assert {name for name, _, _, _ in reachable} == {name for name, _ in _CHECKERS}, (
        "a checker answer that no act-clean cell carries is a label the battery asserts nothing "
        f"about: {sorted({name for name, _ in _CHECKERS} - {name for name, _, _, _ in reachable})}"
    )
    for name, cell, draft, decision in reachable:
        assert _mechanism_shown(draft, decision) == name, (
            f"{cell}: the decision shows the {_mechanism_shown(draft, decision)!r} mechanism, so "
            f"the cell does not exercise the {name!r} one its label names"
        )

    # The `"violating"` label names a verdict this file writes, so the class it carries is pinned
    # here too: `_mechanism_shown` answers `"violating"` for any class outside `OTHER`, and the
    # fixture naming a different one would satisfy it while the battery stopped covering the class
    # the label was chosen for.
    for name, cell, draft, decision in reachable:
        if name == "violating":
            assert [f.violation_class for f in _checker_contribution(draft, decision)] == [
                ViolationClass.NEGOTIATES_TERMS
            ], f"{cell}: the violating verdict no longer reaches the findings as the class it names"


# --- Below this line, and ONLY below it, are limits: behaviours asserted in executable form
# --- because they are known and unclosed, not because they are wanted. The one is named rather
# --- than counted, following the banner in test_citations.py.
# ---
# --- For a test below: if it fails, a limit has been closed. Delete it, do not repair it.
# ---   test_an_act_finding_short_circuits_before_the_tripwires_run_a_known_limit
# --- Every test ABOVE this line is a guard: if one fails, an escape has reopened. Fix upstream.


def test_an_act_finding_short_circuits_before_the_tripwires_run_a_known_limit():
    """`decide` returns on act findings, so a draft tripping both reports only the act class.

    Nothing escapes -- the draft is denied either way -- but the denial is categorized by the
    class that happened to be evaluated first, and design spec 4.7 gives the category a job: an
    agent that is told `act:figure_not_in_record` will correct the figure and re-send the same
    negotiation. The checker is not consulted either, which is deliberate and is what the brief's
    second test pins.

    This body was Task 14's own fixture for `content:negotiates_terms`, and it reached exactly the
    outcome above: `test_each_content_class_blocks_through_the_tripwire_alone` passed its
    `allowed is False` assertion on the act class, failed its class assertion, and would never
    have reached the tripwire it is named for even had it passed both. Task 14 fixed the fixture
    rather than the short-circuit -- its record now carries the $8M, so the act lane is clean
    there and the tripwire is what denies. **The limit is unchanged**, and it is this file's own
    record, which backs `$10M` alone, that still demonstrates it below.
    """
    body = "They would probably accept $8M instead of $10M."
    assert ViolationClass.NEGOTIATES_TERMS in [f.violation_class for f in evaluate_tripwires(_draft(body))]

    decision = decide(_draft(body), RECORD, CONTEXT, _checker(CLEAN))
    assert decision.allowed is False
    assert [f.violation_class for f in decision.findings] == [ViolationClass.FIGURE_NOT_IN_RECORD]
    assert denial_result(decision)["category"] == "act:figure_not_in_record"
