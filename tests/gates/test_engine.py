import ast
import inspect

from chaperone.gates.checker import Checker, CheckerUnavailable, FlagForReview, Verdict
from chaperone.gates.engine import decide, denial_result, disposition_for
from chaperone.policy.act_classes import ActContext
from chaperone.policy.tripwires import evaluate_tripwires
from chaperone.policy.types import Disposition, Draft, Finding, Message, Record, ViolationClass

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
# ---   test_the_futile_set_is_read_in_exactly_one_function_body_and_it_is_disposition_for
# ---       -- the behavioural companion to the substring count above; see its docstring for
# ---          what it holds and, more importantly, for what it does not.
# ---   test_the_denial_result_renders_the_class_and_the_disposition_as_their_values
# ---       -- dropping `.value` from either field in `denial_result`.
# ---   test_a_refinable_denial_is_still_not_retryable
# ---       -- `"is_retryable": decision.disposition is Disposition.REDIRECT_REFINABLE`.
# ---   test_every_denial_in_the_battery_is_a_usable_denial_result
# ---   test_no_decision_in_the_battery_is_allowed_while_carrying_findings
# ---       -- a denial returned with an empty finding tuple, and an allow returned carrying one.
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
    ("violating", lambda: _checker(Verdict(violates=True, violation_class=ViolationClass.NEGOTIATES_TERMS,
                                           confidence=0.7, span="accept $8M"))),
    ("unnamed", lambda: _RawChecker(Verdict(violates=True, confidence=0.9))),
    ("down", lambda: _checker(TimeoutError("down"))),
)


def _battery() -> dict[str, object]:
    """Every combination of body, checker answer and jurisdiction, decided.

    A battery, not a proof: it is the cross product of five bodies chosen to reach each branch,
    five checker answers and both jurisdictions. It says nothing about a body nobody thought of.
    """
    return {
        f"{body!r}/{name}/{juris}": decide(_draft(body, recipient_jurisdiction=juris), RECORD, CONTEXT, make())
        for body in _BODIES
        for name, make in _CHECKERS
        for juris in ("US", "DE")
    }


def _functions_reading(name: str) -> set[str]:
    """The name of every function in `engine` whose body reads `name`, by walking the AST.

    Structural, in the manner of tools/static_audit.py. A mention in a docstring or a comment
    produces no `ast.Name` node and so cannot satisfy this, and a second reader produces one
    wherever in the module it is written.
    """
    from chaperone.gates import engine

    readers = set()
    for node in ast.walk(ast.parse(inspect.getsource(engine))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(isinstance(inner, ast.Name) and inner.id == name for inner in ast.walk(node)):
                readers.add(node.name)
    return readers


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


def test_the_futile_set_is_read_in_exactly_one_function_body_and_it_is_disposition_for():
    """The behavioural companion to the substring count above, and it holds strictly less.

    What it holds: the futile set has exactly one module-level definition, and exactly one
    function reads it -- `disposition_for`. A second reader anywhere in the module fails this,
    and so does `disposition_for` ceasing to read it, whatever the substring count says.

    **What it does not hold, stated because the test name would otherwise overclaim.** Design
    spec 4.7 says retryable-as-redraft versus hard-handoff is derived in one place and never at
    each call site. `decide` still writes `Disposition.REDIRECT_FUTILE` as a literal on three
    paths -- checker unavailable, flagged for review, and a violation reported without a class --
    and those are per-call-site disposition decisions that reference no constant, so this walk
    passes straight over them. It sees who reads the set, not who decides a disposition.
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

    This body is Task 14's own fixture for `content:negotiates_terms`, so
    `test_each_content_class_blocks_through_the_tripwire_alone` will pass its `allowed is False`
    assertion on the act class and never reach the tripwire it is named for.
    """
    body = "They would probably accept $8M instead of $10M."
    assert ViolationClass.NEGOTIATES_TERMS in [f.violation_class for f in evaluate_tripwires(_draft(body))]

    decision = decide(_draft(body), RECORD, CONTEXT, _checker(CLEAN))
    assert decision.allowed is False
    assert [f.violation_class for f in decision.findings] == [ViolationClass.FIGURE_NOT_IN_RECORD]
    assert denial_result(decision)["category"] == "act:figure_not_in_record"
