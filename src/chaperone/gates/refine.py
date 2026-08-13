"""The bounded refinement loop. Design spec 4.8.

Termination is `verdict == pass`. The budget is a backstop, not the control signal -- an integer cap
is a state guard, and a loop that stops because it ran out of rounds has not resolved anything.
`stopped_for` says which of the four ways the loop ended, so a viewer can tell resolution from
exhaustion without inferring it from a round count, and `build_handoff` surfaces the count beside it.

**This module transmits nothing.** It returns a proposed body; `build_handoff` carries it to a human
as `proposed_alternative` beside the body that was blocked, and the original attempt stays terminal.
A redraft that transmitted by itself after a permission failure would be an auto-retry of a
permission failure, which is the thing this architecture exists to refuse.
`test_a_redraft_never_transmits_without_approval` holds the composition on effects -- the send tool
is never entered and the audit log says `redirected` -- and the send symbol is reserved to the
gateway module package-wide by `tools/static_audit.py::audit_send_references`, which this file came
under the moment it existed. Neither is a proof about a composition nobody has written yet.

**A redraft proposes a body, not an envelope.** `redraft` returns a whole `Draft`, and handing that
straight back to `decide` lets the redrafter choose its own reviewer input: dropping the transmitted
turn the checker's verdict turned on converts a denial into a `resolved`, measured. So the proposal
contributes `body` and `cited_fields` and the original contributes the rest. `cited_fields` travels
with the body deliberately -- `act:figure_not_in_record` is the one act-class 4.8 leaves refinable
because a figure can be corrected or removed, and pinning the citations would leave the loop unable
to resolve the very class it was left refinable for.

**Three things this module does NOT do.**

- **It does not judge whether a class is the right one.** Futility is read off `disposition`, which
  `disposition_for` derives from the finding classes. A draft the checker names with the wrong class
  is routed by the wrong class, and when that class is futile the budget is skipped entirely: the
  redraft that would have resolved it is never asked for. Task 20 measured a corpus row of exactly
  that shape. Recorded as a limit test, with a control that differs only in the class named.
- **It does not rank findings.** `findings[0]` decides the deadlock comparison, so two decisions
  carrying the same classes in a different order read as progress. The engine does not rank either.
- **It runs no clock and no retry.** An unwrapped transport that hangs still hangs the loop, as it
  hangs the gate; one wrapped by `gates/deadline.py` costs an outage denial instead, classed
  futile, so the loop escalates with the deflection rather than waiting -- held by
  `tests/gates/test_deadline.py`. The wrapper bounds each wait, not the transport's execution, and
  an abandoned in-flight call survives the deny.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from chaperone.gates.checker import Checker
from chaperone.gates.engine import decide
from chaperone.policy.act_classes import ActContext
from chaperone.policy.types import Decision, Disposition, Draft, Record

#: What the escalation proposes when no redraft can answer the denial: a response that changes the
#: task rather than rewording it.
DEFLECTION = (
    "I am not able to offer a view on the merits. Here are the round facts on record, "
    "and I can arrange time with the founder."
)


@dataclass(frozen=True)
class RefinementOutcome:
    """`stopped_for` is one of `resolved`, `futile`, `deadlock`, `budget`."""

    resolved: bool
    rounds: int
    alternative: str | None
    stopped_for: str


def refine(
    draft: Draft,
    record: Record,
    context: ActContext,
    checker: Checker,
    redraft: Callable[[Draft, Decision], Draft],
    budget: int = 3,
) -> RefinementOutcome:
    """Termination is verdict-pass. The budget is a backstop, not the control signal."""
    if budget < 0:
        raise ValueError(
            f"budget must be >= 0, got {budget}: a negative cap would skip the loop and "
            "report exhaustion for rounds that never ran"
        )
    decision = decide(draft, record, context, checker)
    if decision.allowed:
        return RefinementOutcome(True, 0, draft.body, "resolved")

    if decision.disposition is Disposition.REDIRECT_FUTILE:
        return RefinementOutcome(False, 0, DEFLECTION, "futile")

    previous_class = decision.findings[0].violation_class
    rounds = 0
    current = draft
    while rounds < budget:
        proposal = redraft(current, decision)
        current = replace(draft, body=proposal.body, cited_fields=proposal.cited_fields)
        rounds += 1
        decision = decide(current, record, context, checker)
        if decision.allowed:
            return RefinementOutcome(True, rounds, current.body, "resolved")
        if decision.disposition is Disposition.REDIRECT_FUTILE:
            return RefinementOutcome(False, rounds, DEFLECTION, "futile")
        if decision.findings[0].violation_class is previous_class:
            return RefinementOutcome(False, rounds, None, "deadlock")
        previous_class = decision.findings[0].violation_class

    return RefinementOutcome(False, rounds, None, "budget")
