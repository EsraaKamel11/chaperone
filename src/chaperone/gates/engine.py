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

**Every disposition is derived from the finding classes, in `disposition_for` and nowhere else**
(design spec 4.7). `decide` hardcoded the futile disposition on three paths and nothing noticed,
because the test named for the rule counted occurrences of the futile set's name -- which a
hardcoded `Disposition` member does not touch. The one literal that remains is the clean return's
`Disposition.ALLOW`, and a test pins that site by name so a second one cannot appear quietly.

Three things this layer does NOT do, written down because their absence is otherwise invisible:

- **It runs no clock, and the split is worth stating exactly.** Design spec 3.4 has the checker
  lane race to deny; nothing here races anything. **The policy belongs to this layer** -- a deny
  is the fallback when no answer arrives, and `decide` is the only place that knows it.
  **The mechanism belongs to the transport**, because `checker.check` is a blocking call and a
  deadline consulted after it returns stops no hang, while a transport can bound its own wait.
  **Today neither half exists and no test holds either.** A hanging transport hangs the gate.
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

# Futile means: **no compliant redraft answers this**, so the refinement budget is skipped and the
# denial escalates (design spec 4.8). Membership is decided by that question and by nothing else.
#
# - `ADVISES_ON_MERITS` -- "is this a good deal?" has no compliant direct answer. The compliant
#   response changes the task rather than rewording it.
# - The four act-classes below -- no wording gives a message consent it never had, an approval
#   token, a tool outside the grant, or send budget that is already spent. Routed refinable, each
#   cost a redraft round and then stopped for deadlock with no alternative.
# - `OTHER` -- carried by **four** routes. The count is from grepping every construction site that
#   can produce this class, because the first version of this comment said "three" from memory and
#   undercounted. Three are plumbing failures, where the checker gave no answer the gate can use:
#   it was unavailable, it flagged for review, or it reported a violation and declined to name it.
#   The fourth is a considered judgement -- `_reject_unusable` refuses a violation with *no* class
#   and does not refuse one classed `OTHER`, so a verdict naming it is returned intact and becomes
#   a finding carrying the model's own confidence and span. A redraft has nothing to aim at in any
#   of the four. This membership is also what lets the first three derive their disposition here
#   rather than writing a literal at each call site (4.7), and it silently changed the fourth from
#   refinable to futile -- which a test now pins, because nothing covered that path.
#
# **Deliberately absent, and each is load-bearing.** `FIGURE_NOT_IN_RECORD` is recoverable -- a
# figure can be corrected or removed -- and moving it breaks Task 21's budget test, measured. The
# other two content-classes are recoverable by rewording: a forward-looking return can be stated as
# a recorded fact, and a negotiation can become a referral.
FUTILE_CLASSES = frozenset({
    ViolationClass.ADVISES_ON_MERITS,
    ViolationClass.NO_APPROVAL_TOKEN,
    ViolationClass.JURISDICTION_NOT_CONSENTED,
    ViolationClass.TOOL_OUTSIDE_GRANT,
    ViolationClass.SEND_CAP_EXCEEDED,
    ViolationClass.OTHER,
})


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
        unavailable = (Finding(ViolationClass.OTHER, f"checker unavailable: {exc}", None),)
        return Decision(False, unavailable, disposition_for(unavailable))

    if isinstance(result, FlagForReview):
        flagged = (Finding(ViolationClass.OTHER, f"flagged for review: {result.reason}", None),) + tripwire_findings
        return Decision(False, flagged, disposition_for(flagged))

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
            unnamed = (
                Finding(ViolationClass.OTHER, "checker reported a violation without naming a class", None),
            ) + tripwire_findings
            return Decision(False, unnamed, disposition_for(unnamed))
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
