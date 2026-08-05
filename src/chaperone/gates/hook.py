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

**The arguments must be what the gate reviewed, too.** `decide` reads the `Draft` while `execute`
passes `args` to the tool, and holding the tool identity equal is only half the binding: an
approved draft with `args` carrying different prose ships text no layer ever judged. §4.1's
ordering guarantee is empty unless the thing reviewed is the thing sent, so `_unreviewed_in`
refuses any text in `args` that is not a value the gate saw. The rule is deliberately strict --
`args` may carry only values drawn from the reviewed draft -- because the safe direction here is a
spurious refusal, and because `Gateway.call` digests `args` into the audit log: if the reviewed
object and the digested object can differ, the log describes a call nobody assessed.

**Bounded, and the bound is on keys not values.** Mapping *keys* are parameter names rather than
content and are not checked; `None` and booleans carry no text. Everything else, including numbers,
is compared by its text and must have been reviewed.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from chaperone.audit.gateway import Gateway, GatewayResult
from chaperone.gates.checker import Checker
from chaperone.gates.engine import decide, denial_result, disposition_for
from chaperone.policy.act_classes import ActContext
from chaperone.policy.types import Decision, Draft, Finding, Record, ViolationClass


@dataclass(frozen=True)
class HookOutcome:
    allow: bool
    payload: dict | None


def _reviewed_text(draft: Draft) -> frozenset[str]:
    """Every string the gate actually looked at while deciding.

    The draft *is* the reviewed object, so this is its full text surface: the body the content
    classes judge, the recipient fields the act-classes judge, the cited field names, the thread the
    checker is shown, and the tool name. Anything outside this set reached no predicate.
    """
    values = {draft.body, draft.recipient_domain, draft.recipient_jurisdiction}
    values.update(draft.cited_fields)
    for message in draft.thread:
        values.update((message.role, message.body))
    if draft.tool_name is not None:
        values.add(draft.tool_name)
    return frozenset(values)


def _unreviewed_in(value: object, reviewed: frozenset[str]) -> tuple[str, ...]:
    """Every scalar inside `value` that the gate did not review, walking nested containers.

    `str` is tested before the container branches on purpose: a string is itself a sequence, so a
    later check would take every body apart into characters and find each one unreviewed.

    `None` and booleans are exempt because neither carries text a predicate could have judged.
    Numbers are not exempt: an amount passed as an argument is a figure the act-classes never saw,
    and `act:figure_not_in_record` exists precisely because an unbacked figure matters.
    """
    if isinstance(value, str):
        return () if value in reviewed else (value,)
    if isinstance(value, Mapping):
        return tuple(item for inner in value.values() for item in _unreviewed_in(inner, reviewed))
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(item for inner in value for item in _unreviewed_in(inner, reviewed))
    if value is None or isinstance(value, bool):
        return ()
    text = str(value)
    return () if text in reviewed else (text,)


def _decide_for(
    tool_name: str, args: dict, draft: Draft, record: Record, context: ActContext, checker: Checker
) -> Decision:
    """The one decision both layers take, so neither can hold a predicate the other lacks.

    Identity first, then contents. The tool mismatch is classed `act:tool_outside_grant`: the call
    names a tool the reviewed draft did not, so nothing has authorised *this* tool, whatever the
    grant says about the other one. Unreviewed arguments are classed `other`, because no existing
    class describes "the gate assessed a different object" -- and `other` carries the detail text a
    reviewer needs, which `Finding` requires for exactly that class.

    Both put the denial in the futile set by derivation rather than by a literal, which is where
    design spec 4.7 requires every disposition to come from, and both are right to be futile: no
    redraft of the body answers a call that runs another tool or ships text nobody read.
    """
    if draft.tool_name != tool_name:
        mismatch = (Finding(
            ViolationClass.TOOL_OUTSIDE_GRANT,
            f"the call names {tool_name!r}; the reviewed draft names {draft.tool_name!r}",
            None,
        ),)
        return Decision(False, mismatch, disposition_for(mismatch))
    unreviewed = _unreviewed_in(args, _reviewed_text(draft))
    if unreviewed:
        unbound = (Finding(
            ViolationClass.OTHER,
            f"the call carries {len(unreviewed)} argument value(s) the gate did not review, "
            f"beginning {unreviewed[0]!r}",
            None,
        ),)
        return Decision(False, unbound, disposition_for(unbound))
    return decide(draft, record, context, checker)


def pre_tool_use(tool_name: str, args: dict, ctx: tuple[Draft, Record, ActContext, Checker]) -> HookOutcome:
    """Hook layer. The runtime synthesizes the tool result; the reason content is ours."""
    draft, record, context, checker = ctx
    decision = _decide_for(tool_name, args, draft, record, context, checker)
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
        decide=lambda: _decide_for(tool_name, args, draft, record, context, checker),
        execute=lambda: registry[tool_name](**args),
        effectful=True,
    )
