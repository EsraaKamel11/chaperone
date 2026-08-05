"""Where the two families meet, and the shape a denial leaves in.

Act-classes are deterministic predicates over the arguments, so `decide` is the place their
answer becomes binding. Content-classes have two independent detectors -- an independent checker
and a lexical tripwire table -- and `decide` takes the disjunction, because either alone has a
false-negative rate that is measured rather than assumed. **Neither the disjunction nor any part
of it is a guarantee about a content-class.** A wrong verdict on one is a measured escape; what
this module fixes is that the gate exists, that the model never decides whether it does, and that
every way of not getting a usable answer ends in a denial.

**A denial is returned, never raised.** Design spec 3.4: a defensively-written executor wraps
handler invocation in a catch-all, so a deny implemented as an exception arrives at the agent
relabelled "transient -- please retry", which is the one label it must never carry. Everything
here that refuses returns a `Decision`, and `denial_result` turns one into a tool result carrying
`is_error` and a category.

**Retryability and redraftability are two axes.** `is_retryable` is false on every denial. The
invitation to redraft is carried by `disposition` alone, and reading it across into
`is_retryable` is the tidy-looking edit that reopens 3.4.

Three things this layer does NOT do, written down because their absence is otherwise invisible:

- **It runs no clock.** Design spec 3.4 has the checker lane race to deny; nothing here races
  anything. `checker.check` is a blocking call, so a deadline consulted after it returns stops no
  hang -- the mechanism has to live in the transport, which can bound its own wait. The *policy*
  is this module's (a deny is the fallback, and only `decide` knows that), the mechanism is not,
  and today **no test holds either half**.
- **It does not rank findings.** `denial_result` reports `findings[0]`, and act-classes are
  evaluated first and returned early -- so a draft that trips an act-class and a tripwire is
  categorized by the act-class and the tripwire is never evaluated. Nothing escapes; the category
  is narrower than the draft. Pinned as a limit test.
- **It does not judge whether the checker was right.** A verdict it can act on is acted on. One it
  cannot act on -- a violation with no class -- is a denial, not a discard.
"""
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
    if result.violates:
        if result.violation_class is None:
            # Design spec 4.3 maps a schema-valid but invalid verdict to deny. The condition this
            # replaces -- `if result.violates and result.violation_class is not None` -- built no
            # finding for one, so with no tripwire hit the checker said "violates" and the draft
            # was sent. Task 10 closed it at the checker, and this is the same close one layer up,
            # because `decide` reads `.violates` off whatever it is handed: Task 17 builds
            # `Verdict`s straight from recorded JSON and never calls `check()` at all. Futile
            # rather than refinable for the reason the two branches above are: no redraft answers
            # a checker that cannot say what is wrong.
            return Decision(
                False,
                (Finding(ViolationClass.OTHER, "checker reported a violation without naming a class", None),)
                + tripwire_findings,
                Disposition.REDIRECT_FUTILE,
            )
        checker_findings = (Finding(result.violation_class, f"checker confidence {result.confidence}", result.span),)

    findings = checker_findings + tripwire_findings
    if findings:
        return Decision(False, findings, disposition_for(findings))
    return Decision(True, (), Disposition.ALLOW)


def denial_result(decision: Decision) -> dict:
    """The categorized tool result. Delivered as the result, never raised.

    Categorized, not merely failed: design spec 4.7 says a failure that does not say what kind of
    failure it is forces the agent to guess. `category` is the class's **value** -- both enums
    subclass `str`, so equality, concatenation and `json.dumps` give the value either way and only
    formatting differs, `f"{ViolationClass.ADVISES_ON_MERITS}"` being
    `"ViolationClass.ADVISES_ON_MERITS"` on 3.11 and later.

    `is_retryable` is false unconditionally. Whether a redraft could help is `disposition`, a
    separate field, derived from the class in `disposition_for` and nowhere else.

    **It requires a finding.** On `Decision(False, (), ...)` this raises `IndexError`, which at
    this boundary is exactly the shape 3.4 warns about -- so nothing in this module returns a
    denial without one, and a battery test holds that. A caller assembling a `Decision` by hand
    owns the same obligation.
    """
    primary = decision.findings[0]
    return {
        "is_error": True,
        "is_retryable": False,
        "category": primary.violation_class.value,
        "detail": primary.detail,
        "span": primary.span or "",
        "disposition": decision.disposition.value,
    }
