"""The in-process `PreToolUse` callback: payload in, deny-dict out. Imports no SDK.

The deny shape is the Agent SDK's documented `HookJSONOutput` contract, produced here as plain JSON
so the static audit can read the gate it is asked to trust. The deny surface is the content classes
(via tripwires) plus the act classes, over the same payload trust boundary as
`tools/policy_hook.py`, carried by the shared adapter in `policy/payload.py`.

**`UNENFORCEABLE_HERE` is not consulted here, and that is deliberate.** The set in
`policy/payload.py` declares the classes a layer building its inputs from that adapter holds no
trustworthy source for, and the residual it states is precise: every predicate still runs, so such a
layer can still deny on those classes from caller input; what it cannot do is refuse to be talked
out of the denial. They are classes that cannot be relied on to **fire**, never classes that must be
suppressed. Filtering findings by the set would convert a documented residual into a live fail-open
-- a draft stating a figure its own record contradicts produces `act:figure_not_in_record` and
nothing else, so the filtered callback would return an allow while all three other layers refuse it.
`tools/policy_hook.py` does not filter either, which is what keeps the primary categories equal.
"""
from __future__ import annotations

from chaperone.policy.act_classes import evaluate_act_classes
from chaperone.policy.arguments import unsendable_finding
from chaperone.policy.citations import validate_citations
from chaperone.policy.payload import CONSUMED_KEYS, build_act_inputs
from chaperone.policy.tripwires import evaluate_tripwires


def _deny(reason: str) -> dict:
    """The one deny shape, built in one place so no refusal can be spelled a second way."""
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}


def _decide(input_data) -> dict:
    """`{}` to allow, the deny dict to refuse. Same findings, same order, as the command hook.

    The three shape guards below are `policy_hook.main`'s, in `main`'s order, and they are here for
    `main`'s reason: `build_act_inputs` validates nothing and documents that it validates nothing.
    Each guard is the caller's obligation, and a caller that omits one is weaker than the guard that
    holds all of them.
    """
    if not isinstance(input_data, dict):
        return _deny(f"the payload is a {type(input_data).__name__}, not a JSON object")

    tool_input = input_data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return _deny(f"tool_input is a {type(tool_input).__name__}, not a JSON object")

    # Absent is not empty. Every predicate reads `draft.body`, so a message stored under some other
    # key would be scored as a blank draft and found clean. The builder subscripts
    # `tool_input["body"]` and raises instead, which is fail-closed only where something turns the
    # raise into a refusal -- so the check runs *before* the builder here exactly as it does there.
    if "body" not in tool_input:
        return _deny("the payload carries no 'body', so there is no message to evaluate")

    draft, record, act_context = build_act_inputs(input_data)

    # `main`'s order exactly, because `findings[0]` is what every layer reports as its category and
    # the parity claim is that one payload yields one primary category everywhere. The unconsumed-key
    # check is *first*: a key outside `CONSUMED_KEYS` reaches no predicate, so out of process -- where
    # a tool's arguments are its `tool_input` -- it is precisely an argument the gate did not judge.
    # `CONSUMED_KEYS` is imported rather than restated so the two layers cannot hold two lists.
    unbound = unsendable_finding({k: v for k, v in tool_input.items() if k not in CONSUMED_KEYS}, draft)
    findings = (
        unbound
        + evaluate_act_classes(draft, record, act_context)
        + validate_citations(draft, record)
        + evaluate_tripwires(draft)
    )
    if not findings:
        return {}
    first = findings[0]
    return _deny(f"{first.violation_class.value}: {first.detail}")


async def pre_tool_use_deny(input_data: dict, tool_use_id, context) -> dict:
    """The callback the runtime awaits. `{}` allows; the deny dict refuses.

    **An exception here is not a denial**, which is the whole reason this net exists.
    `tools/policy_hook.py` documents the same asymmetry in the currency it has -- exit 2 blocks and
    exit 1 does not, so every escape from `main` is turned into a deliberate 2. One layer over there
    is no exit code: an exception raised inside a hook propagates into machinery this module cannot
    see, because importing the SDK to find out is exactly what it refuses to do. So a raise is
    converted here rather than delegated, and the shape is the guard's: `BaseException`, not
    `Exception`. Enumerating the exception types was tried in that file first and missed the one that
    mattered.

    **The cost, stated because `BaseException` is a wider net in an async function than in a script.**
    `asyncio.CancelledError` does not derive from `Exception`, so a cancelled hook returns a refusal
    instead of propagating the cancellation, and cooperative cancellation is broken to that extent.
    That is a deliberate choice and not an oversight: this repository's doctrine is that every way of
    not getting a usable answer ends in a denial, a guard interrupted mid-decision has decided
    nothing, and a refusal is the one outcome that cannot be read as permission. The caller is
    tearing the call down in any case, so the refusal costs it nothing it wanted.
    """
    try:
        return _decide(input_data)
    except BaseException as exc:
        return _deny(f"the guard could not complete: {type(exc).__name__}: {exc}")
