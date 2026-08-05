"""Two enforcement layers over one predicate set, and the ordering that makes the second binding.

`pre_tool_use` is the in-process framework hook; `guarded_call` is the executor chokepoint. A third
lives out of process in `tools/policy_hook.py`, running the pure half of the same policy as a plain
command hook. Design spec 6.3: the same policy at more than one layer is what shows the control is
a property of the architecture rather than an artifact of one integration.

**Design spec 4.1 is an ordering claim, and this is where it becomes observable.** The gate runs
before the tool function is *looked up*, not merely before it is called: `execute` closes over
`registry[tool_name]` and `Gateway.call` returns on a denial without ever evaluating it, so a
registry that records its key lookups sees none. Task 7 could only show the function was not
entered; the lookup lives here.

**Design spec 4.2 puts least privilege first.** The drafting agent holds no send tool at all, and
that is the layer that does the most work -- a policy engine guarding a capability the agent never
had is defence in depth, while one guarding a capability the agent holds is a single point of
failure. What follows is the second kind, so it is written for the case where the capability *is*
held.

**What the gate decides about must be what the executor runs.** `decide` reads `draft.tool_name`
and `execute` runs `registry[tool_name]`; nothing held the two equal, so a draft naming a granted
`draft_message` passed review and the executor then ran `send_message`. Both tools inside the
grant, so no act-class fired, and the message left. `_decide_for` refuses the mismatch in both
layers, from one function, because a check living in only one of them is the drift 6.3 forbids.

**Not closed: the arguments.** `decide` reads `draft.body` while `execute` passes `args` to the
tool, and nothing binds those either. The tool *identity* is now held; the argument *contents* are
not, so an allow does not establish that what ships is what was reviewed. A test exhibits the gap.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from chaperone.audit.gateway import Gateway, GatewayResult
from chaperone.gates.checker import Checker
from chaperone.gates.engine import decide, denial_result, disposition_for
from chaperone.policy.act_classes import ActContext
from chaperone.policy.types import Decision, Draft, Finding, Record, ViolationClass


@dataclass(frozen=True)
class HookOutcome:
    allow: bool
    payload: dict | None


def _decide_for(
    tool_name: str, draft: Draft, record: Record, context: ActContext, checker: Checker
) -> Decision:
    """The one decision both layers take, so neither can hold a predicate the other lacks.

    The mismatch is classed `act:tool_outside_grant`: the call names a tool the reviewed draft did
    not, so nothing has authorised *this* tool, whatever the grant says about the other one. That
    puts it in the futile set by derivation rather than by a literal, which is where design spec
    4.7 requires every disposition to come from.
    """
    if draft.tool_name != tool_name:
        mismatch = (Finding(
            ViolationClass.TOOL_OUTSIDE_GRANT,
            f"the call names {tool_name!r}; the reviewed draft names {draft.tool_name!r}",
            None,
        ),)
        return Decision(False, mismatch, disposition_for(mismatch))
    return decide(draft, record, context, checker)


def pre_tool_use(tool_name: str, args: dict, ctx: tuple[Draft, Record, ActContext, Checker]) -> HookOutcome:
    """Hook layer. The runtime synthesizes the tool result; the reason content is ours."""
    draft, record, context, checker = ctx
    decision = _decide_for(tool_name, draft, record, context, checker)
    if decision.allowed:
        return HookOutcome(allow=True, payload=None)
    return HookOutcome(allow=False, payload=denial_result(decision))


def guarded_call(
    gateway: Gateway,
    tool_name: str,
    args: dict,
    draft: Draft,
    record: Record,
    context: ActContext,
    checker: Checker,
    registry: Mapping[str, object],
) -> GatewayResult:
    """Executor layer. Owns the full denial contract, including result shape and retryability."""
    return gateway.call(
        tool_name,
        args,
        decide=lambda: _decide_for(tool_name, draft, record, context, checker),
        execute=lambda: registry[tool_name](**args),
        effectful=True,
    )
