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

**Where a redirect goes is derived here too, in `destination_for`, and it is a separate question.**
`disposition_for` answers whether a draft is redirected; `destination_for` answers which queue reads
it, so a caller wanting to route one asks a function rather than typing a name. This module still
holds no queue and puts nothing in one: `gates/queues.py` is the queue and the executor chokepoint
is what routes to it.

Three things this layer does NOT do, written down because their absence is otherwise invisible:

- **It runs no clock, and the split is worth stating exactly.** Design spec 3.4 has the checker
  lane race to deny; nothing here races anything. **The policy belongs to this layer** -- a deny
  is the fallback when no answer arrives, and `decide` is the only place that knows it.
  **The mechanism belongs to the transport**, because `checker.check` is a blocking call and a
  deadline consulted after it returns stops no hang, while a transport can bound its own wait.
  **Both halves now exist and `tests/gates/test_deadline.py` holds them together.** The policy
  half was always the `CheckerUnavailable` branch in `decide`; the mechanism half is
  `gates/deadline.py`, whose wrapper raises exactly that at an expired wait. The bound is opt-in
  at the transport seam -- an unwrapped transport still hangs the gate -- and it bounds the
  gate's wait, not the transport's execution: the abandoned in-flight call survives the deny.
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
from chaperone.policy.types import Decision, Disposition, Draft, Family, Finding, Record, ViolationClass

# The two families, enumerated by derivation rather than by hand. `ViolationClass.family` reads the
# prefix off the value, so a member added to the enum joins the right set here with no edit, and a
# member that joins neither -- anything whose value carries no `act:` or `content:` prefix -- joins
# neither set. `OTHER` is the one such member today.
#
# Derived rather than written out because these are what `tools/coverage_map.py`'s declared coverage
# tables are held against, and a hand-written set on this side would make that a comparison of two
# literals: both would have to be edited to add a class, and the test would pass while nothing
# detected it. The sets are the enum's own answer, so the comparison binds a table to the registry.
#
# **Not a claim that either family is detected**, only that these are its members. Coverage is the
# coverage map's question; "zero by construction" remains an act-class claim and is made nowhere
# here.
ACT_CLASSES = frozenset(klass for klass in ViolationClass if klass.family is Family.ACT)
CONTENT_CLASSES = frozenset(klass for klass in ViolationClass if klass.family is Family.CONTENT)

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


#: What `Decision.outage` carries when the checker was unavailable and nothing said why.
#:
#: `Checker.check` raises `CheckerUnavailable(str(last))`, and an exception raised without a message
#: stringifies to `""` -- `str(TimeoutError())` is the empty string. So `outage=str(exc)` alone made
#: the field non-`None` and falsy at once, and `if decision.outage:` -- the natural way to ask
#: whether there was one -- answered no. That reads a detector outage as a judgement, which is the
#: direction that looks like an ordinary denial and so is never questioned.
OUTAGE_WITHOUT_MESSAGE = "checker unavailable, and the failure carried no message"


#: The queue a redirected draft is routed to. One name, because futile and refinable differ in
#: whether a redraft could help and not in who reads the result.
HUMAN_REVIEW = "human-review"

#: The dispositions that route somewhere, derived from the enum rather than written out.
#:
#: Derived for the reason `ACT_CLASSES` and `CONTENT_CLASSES` above are: a hand-written pair has to
#: be remembered on the day a member is added, and the failure mode is silent -- `destination_for`
#: would answer `None` for a new redirect, and its escalations would be dropped at the chokepoint
#: while every call site still looked wired. A member the enum grows joins this set with no edit.
#:
#: **The `REDIRECT_` prefix on the member name is therefore load-bearing**, which is the price of
#: deriving rather than listing. A redirect member named some other way would route nowhere, so
#: `test_the_destination_is_derived_in_one_place` pins the exact membership of `Disposition`: a
#: fourth member fails there, where the routing decision for it has to be taken.
#:
#: It also leaves each redirect member written exactly once in this module, inside
#: `disposition_for`, which is the state
#: `test_the_futile_set_and_both_redirect_dispositions_are_named_only_in_disposition_for` measures.
#: That guard is about deriving a disposition rather than about reading one, so naming them here
#: would not have been 4.7's drift -- but a second site is where that drift starts, and there is no
#: reason to open one.
REDIRECT_DISPOSITIONS = frozenset(d for d in Disposition if d.name.startswith("REDIRECT_"))


def disposition_for(findings: tuple[Finding, ...]) -> Disposition:
    if not findings:
        return Disposition.ALLOW
    if any(f.violation_class in FUTILE_CLASSES for f in findings):
        return Disposition.REDIRECT_FUTILE
    return Disposition.REDIRECT_REFINABLE


def destination_for(disposition: Disposition) -> str | None:
    """Where a redirect routes. One derivation point, so no call site names a queue.

    `None` for anything that is not a redirect, which today is the allow disposition alone. A name
    there would hand an allowed call a queue to be put in, and a caller routing on "the destination
    is not `None`" would then escalate every send it was asked to make.
    """
    return HUMAN_REVIEW if disposition in REDIRECT_DISPOSITIONS else None


def decide(draft: Draft, record: Record, context: ActContext, checker: Checker) -> Decision:
    act_findings = evaluate_act_classes(draft, record, context) + validate_citations(draft, record)
    if act_findings:
        return Decision(False, act_findings, disposition_for(act_findings))

    tripwire_findings = evaluate_tripwires(draft)

    try:
        result = checker.check(draft, record)
    except CheckerUnavailable as exc:
        unavailable = (Finding(ViolationClass.OTHER, f"checker unavailable: {exc}", None),)
        # The one branch that sets `outage`. It is the difference between a draft a detector judged
        # and a draft nothing looked at, and it survives to the reviewer as a field rather than as
        # the prose in `detail` above, which `Handoff` does not carry. Never the empty string: see
        # `OUTAGE_WITHOUT_MESSAGE` for why a falsy outage is worse than a missing one.
        outage = str(exc) or OUTAGE_WITHOUT_MESSAGE
        return Decision(False, unavailable, disposition_for(unavailable), outage=outage)

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
