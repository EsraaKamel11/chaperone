"""The fourth decision surface: an in-process deny callback shaped to the `PreToolUse` contract.

The module under test imports no SDK, which is the whole premise, and the two tests at the foot of
this file are what hold it to that -- `tools/static_audit.py` does not, for the reason the module's
own docstring gives.

Everything here is offline and synchronous. `asyncio.run` drives the coroutine because an unmarked
`async def` test **fails** in this repository: `pytest-asyncio` 1.3.0 is installed but unconfigured,
which leaves it in strict mode, so an async test without the marker is collected and then errors
with *"async def functions are not natively supported"*. Measured. That is a stronger reason than
the skip this note first claimed -- a skip is a quiet subtraction from the suite, while this is a
red build -- and it is the reason worth writing down, because the wrong one would have made an
`async def` here look merely untidy rather than broken.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path

from chaperone.gates import sdk_callback
from chaperone.gates.sdk_callback import pre_tool_use_deny
from chaperone.policy.payload import UNENFORCEABLE_HERE
from chaperone.policy.types import ViolationClass
from tools.static_audit import FORBIDDEN_IN_POLICY

#: A payload carrying every piece of evidence the predicates need, so a test naming one class is
#: refused for that class and not for a neighbouring one.
#:
#: **The brief's two payloads omitted `jurisdiction` and `approval_token`**, and measured against the
#: real predicates that is four findings rather than one: `act:no_approval_token` fires because
#: `tier` defaults closed to 2 with no token, and `act:jurisdiction_not_consented` fires because an
#: absent jurisdiction is not a consented one. `findings[0]` is then the approval class, so the
#: brief's own assertion about `act:figure_not_in_record` fails, and its compliant payload denies as
#: well. Supplying the evidence is the fix, exactly as
#: `test_the_out_of_process_layer_allows_a_compliant_draft` records for the same omission one layer
#: down: the guard was right and the payload was short. Defaulting either field inside the callback
#: would be the guard inventing the evidence it is guarding.
_SEND = {"jurisdiction": "US", "tool_name": "send_message", "approval_token": "tok",
         "record": {"round_size": "10000000"}, "cited_fields": ["round_size"]}


def _call(payload) -> dict:
    """The callback's output for one whole payload, driven synchronously."""
    return asyncio.run(pre_tool_use_deny(payload, None, None))


def _verdict(**overrides) -> dict:
    """The callback's output for a well-formed send payload, with `overrides` applied."""
    return _call({"tool_name": "send_message", "tool_input": {**_SEND, **overrides}})


def test_a_violating_payload_is_denied_with_the_category_in_the_reason():
    out = _verdict(body="The round is $8M.")
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "deny"
    assert "act:figure_not_in_record" in spec["permissionDecisionReason"]


def test_a_compliant_payload_returns_an_empty_allow():
    assert _verdict(body="The round is $10M.") == {}


def _reason(out: dict) -> str:
    return out["hookSpecificOutput"]["permissionDecisionReason"]


def test_a_key_no_predicate_consumes_is_checked_as_outbound_content():
    """The first of the two guards that live outside the block the adapter extracts.

    `build_act_inputs` reads the keys in `CONSUMED_KEYS` and passes silently over the rest, so a
    payload key nothing consumes reaches no predicate at all -- and out of process a tool's
    arguments *are* its `tool_input`, which makes such a key exactly an argument the gate did not
    judge. `tools/policy_hook.py` runs `unsendable_finding` over the difference; a surface that
    builds from the adapter and skips that step allows `{"extra_text": "Returns are guaranteed."}`
    on an otherwise-compliant payload while the other three layers refuse it.
    """
    out = _verdict(body="The round is $10M.", extra_text="Returns are guaranteed.")
    assert _reason(out).startswith("other: ")


def test_the_unconsumed_key_check_runs_ahead_of_every_predicate():
    """Order, not merely presence: `findings[0]` is what each layer reports as its category.

    The parity claim is that one payload yields one primary category everywhere, so a payload that
    trips both the unconsumed-key rule and an act-class has to report the same one of them here as
    it does at the command hook, where the unsendable finding is concatenated first. Running the
    check last would still deny this payload -- on `act:jurisdiction_not_consented` -- and the
    verdict would agree while the category diverged.
    """
    out = _verdict(body="The round is $10M.", jurisdiction="DE", note="Returns are guaranteed.")
    assert _reason(out).startswith("other: ")


def test_a_key_the_adapter_consumes_is_not_checked_as_content():
    """The companion that keeps the rule above from being "refuse every payload".

    `CONSUMED_KEYS` is the exemption set: membership means the key reached a predicate, so it must
    not be checked a second time as unjudged text. Without this the tests above are satisfied by a
    callback that denies everything, and the compliant payload would be the only thing standing
    between that and a green suite.
    """
    assert _verdict(body="The round is $10M.", tier=2, sent_count=0, send_cap=50) == {}


# ---------------------------------------------------------------------------
# The malformed payloads, which is where an async surface differs from a command hook.
#
# `tools/policy_hook.py` documents its exit codes at length: exit 2 blocks, exit 1 does not, so
# every escape from `main` is wrapped into a deliberate 2 and a raise is still a refusal. **One
# layer over, an exception is not a denial at all.** It propagates out of the coroutine into the
# hook machinery awaiting it, and nothing here can promise what that machinery does with it -- this
# module imports no SDK precisely so it cannot find out. So each shape below is refused on purpose
# rather than left to whichever exception happens to escape.
#
# `build_act_inputs` validates nothing, and says so: a non-dict `tool_input` reaches `.get` on the
# wrong type and raises `AttributeError`, an absent `body` is a subscript and raises `KeyError`, and
# a `tier` that will not convert raises `ValueError` from `int`. Every one of those is a fail-open
# here unless the callback closes it.
# ---------------------------------------------------------------------------


def _refusal(payload) -> str:
    """The stated reason for a refusal, asserting that it *is* one.

    The reason is read as well as the decision, so each shape below is pinned to the guard written
    for it. A catch-all alone would satisfy a bare `permissionDecision == "deny"` on every one of
    them, and the callback would then be refusing these payloads by accident -- which is the same
    thing as not having decided what to do about them.
    """
    out = _call(payload)
    spec = out.get("hookSpecificOutput", {})
    assert spec.get("permissionDecision") == "deny", f"not a refusal: {out}"
    return spec["permissionDecisionReason"]


def test_a_payload_carrying_no_body_denies_rather_than_raising():
    """The second guard living outside the extracted block, and absent is not empty.

    Every predicate reads `draft.body`, so a message stored under some other key would be scored as
    a blank draft and found clean -- which is why the adapter subscripts rather than defaulting, and
    why the check has to run *before* the builder. The raise it produces is fail-closed only where
    something turns it into a refusal; in an async hook nothing does.
    """
    reason = _refusal({"tool_name": "send_message",
                       "tool_input": {"text": "Returns are guaranteed.", "jurisdiction": "US"}})
    assert "no 'body'" in reason, reason
    # An explicit "" is a caller stating its draft is empty, which is a fact about the draft and
    # still builds. Without this the assertion above is satisfied by refusing every body.
    #
    # `cited_fields` is cleared with it, because a citation cannot resolve into a body with no text
    # in it: keeping the default denied on `act:figure_not_in_record` and would have made this read
    # as the empty body being refused. Measured on the first run of this test.
    assert _verdict(body="", cited_fields=[]) == {}


def test_a_tool_input_that_is_not_a_mapping_denies():
    """`build_act_inputs` reaches `.get` on it and raises `AttributeError`. Measured, not supposed."""
    for tool_input, named in (("Returns are guaranteed.", "str"), ([1, 2, 3], "list")):
        reason = _refusal({"tool_name": "send_message", "tool_input": tool_input})
        assert reason == f"tool_input is a {named}, not a JSON object", reason


def test_a_payload_that_is_not_a_mapping_denies():
    """The outermost shape, refused for the same reason the command hook refuses a JSON list."""
    for payload, named in (("not a payload at all", "str"), ([1, 2, 3], "list"), (None, "NoneType")):
        reason = _refusal(payload)
        assert reason == f"the payload is a {named}, not a JSON object", reason


def test_a_field_that_will_not_convert_denies_rather_than_escaping_the_coroutine():
    """The shape the brief does not name and the out-of-process layer already has a test for.

    `int(tool_input.get("tier", 2))` on a non-numeric tier raises `ValueError` from inside the
    builder, and `test_a_field_the_hook_cannot_parse_blocks_rather_than_crashing_open` pins the
    command hook's answer to it. The body below trips a tripwire on any reading, so the stronger the
    malformed input the more reliably it would escape -- an exception thrown past an await is the
    exit-1 fail-open one layer up, wearing different clothes.
    """
    reason = _refusal({"tool_name": "send_message",
                       "tool_input": {**_SEND, "body": "Returns are guaranteed.", "tier": "high"}})
    assert reason.startswith("the guard could not complete: ValueError"), reason
    assert _refusal({"tool_name": "send_message",
                     "tool_input": {**_SEND, "body": "The round is $10M.", "sent_count": "many"}})


class _RaisesOnRead(dict):
    """A well-shaped payload that raises the moment the callback reads it.

    A dict subclass, so `isinstance(payload, dict)` passes and the failure lands *inside* the
    decision rather than being turned away by the shape guard in front of it. This is how a
    `BaseException` is injected deterministically without waiting for a real interrupt.
    """

    def __init__(self, exception: BaseException):
        super().__init__({"tool_name": "send_message", "tool_input": {"body": "The round is $10M."}})
        self.exception = exception

    def get(self, *args, **kwargs):
        raise self.exception


def test_an_interruption_that_is_not_an_exception_is_still_a_refusal():
    """`except Exception` is the net that misses the ones that matter, and both are pinned here.

    `KeyboardInterrupt`, `SystemExit`, `GeneratorExit` and `asyncio.CancelledError` all derive from
    `BaseException` and none from `Exception`, so a narrower net lets each of them past -- and past,
    in an async hook, means the call was never judged.

    **What the `asyncio.CancelledError` row pins, stated exactly, because it is easy to overread.**
    It pins that a `CancelledError` raised *inside* the decision becomes a refusal like any other
    `BaseException`. It does **not** pin anything about task cancellation, and the callback does not
    in fact swallow one: the coroutine contains no `await`, so a cancellation before its first step
    is thrown into a body that never runs and propagates out, and after its first step the body has
    already completed and there is nothing to cancel. Measured both ways. The row is kept because the
    property it does pin is real -- these four types derive from `BaseException` and not from
    `Exception`, so a narrower net lets each past, and past in an async hook means the call was never
    judged. Asserted rather than left to inheritance, so narrowing the net later is a decision
    somebody has to make on purpose.
    """
    for exception in (KeyboardInterrupt("interrupted mid-decision"),
                      asyncio.CancelledError("torn down mid-decision")):
        reason = _refusal(_RaisesOnRead(exception))
        assert reason.startswith(f"the guard could not complete: {type(exception).__name__}"), reason


#: One payload per entry of `UNENFORCEABLE_HERE`, each denying on that entry alone. Keyed by the
#: class so the set below can be compared rather than a count restated: an entry added to the
#: declaration with no payload here fails, which is what stops this exhibiting a set it has stopped
#: matching.
_DECLARED_UNENFORCEABLE = {
    ViolationClass.FIGURE_NOT_IN_RECORD: {"body": "The round is $8M."},
    ViolationClass.NO_APPROVAL_TOKEN: {"body": "The round is $10M.", "approval_token": None},
    ViolationClass.SEND_CAP_EXCEEDED: {"body": "The round is $10M.", "sent_count": 50,
                                       "send_cap": 50},
}


def test_the_classes_declared_unenforceable_still_deny_here_rather_than_being_suppressed():
    """`UNENFORCEABLE_HERE` is a declaration about reliability, and filtering by it is a fail-open.

    The set names the classes a layer building its inputs from `policy/payload.py` holds no
    trustworthy source for -- a count that lives in a log, an approval and a record that arrive from
    the caller being judged. `policy/payload.py` states the residual exactly: *every predicate still
    runs, so a layer building from here can still deny on these; what it cannot do is refuse to be
    talked out of the denial.* They are classes that cannot be relied on to **fire**, not classes
    that must not fire.

    So this surface does not filter by the set, and `tools/policy_hook.py` does not either. Dropping
    findings whose class is declared turns a documented residual into a live hole: the payload below
    for `act:figure_not_in_record` produces that class and nothing else, so a filtered callback
    returns `{}` and allows a draft all three other layers refuse. It is also the payload the brief's
    own first test asserts a **deny** on, which is the discriminator that settles the question.

    Each entry is exhibited on a payload that denies on it alone, and the keys are compared against
    the declaration so a fourth entry cannot arrive here unexhibited.
    """
    assert set(_DECLARED_UNENFORCEABLE) == UNENFORCEABLE_HERE, (
        "the declaration changed and this exhibition did not: "
        f"{sorted(c.value for c in UNENFORCEABLE_HERE ^ set(_DECLARED_UNENFORCEABLE))}"
    )
    for violation_class, overrides in _DECLARED_UNENFORCEABLE.items():
        reason = _refusal({"tool_name": "send_message", "tool_input": {**_SEND, **overrides}})
        assert reason.startswith(f"{violation_class.value}: "), (
            f"{violation_class.value} was suppressed rather than reported: {reason}"
        )


# ---------------------------------------------------------------------------
# The premise: no SDK is imported, so the gate is legible without one. These two tests are the only
# thing in the repository enforcing it -- see the module docstring for why the static audit is not.
# ---------------------------------------------------------------------------

#: The client the deny shape is modelled on. Written out once here and then asserted to be a member
#: of the audit's denylist, which is where the guarantee comes from: the literal cannot be read off
#: `FORBIDDEN_IN_POLICY`, because that set holds nine other names and nothing marks which is this
#: one. So the assertion is the mechanism and this constant is only the spelling -- a rename in the
#: audit turns that assertion red rather than leaving this watching a string nothing enforces.
_SDK = "claude_agent_sdk"


def _sdk_imports_in(source: str) -> list[str]:
    """Every module imported by `source` that is the SDK or a submodule of it."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split(".")[0] == _SDK]
        elif isinstance(node, ast.ImportFrom) and node.module:
            found += [node.module] if node.module.split(".")[0] == _SDK else []
    return found


def test_the_callback_module_imports_no_sdk():
    """The whole reason this surface exists in plain Python, asserted rather than assumed.

    `tools/static_audit.py` forbids `claude_agent_sdk` under `policy/` and this module lands in
    `gates/`, so nothing else in the repository holds it to this -- which makes this test the
    enforcement rather than a restatement of it.

    **Exactly one file in the shipped tree imports the SDK, and it is the wiring.** Measured:
    `demo/sdk_hook.py` builds the options object a client is constructed with, and nothing else
    imports it. Every other occurrence anywhere is a name being looked for rather than used: the two
    denylist literals in `tools/static_audit.py` and `tools/guard_edit.py`, and this module. That
    confinement is the premise this test rests on and **not** what it measures -- measuring it needs
    a scan of the whole tree rather than of one module, and no test in this repository runs one. So
    what is asserted here is the narrow half, which is that the property holds of the callback
    whatever arrives elsewhere.

    Both directions, because a scan of a clean tree is green whether or not the detector works. The
    synthetic sources below are the detector being exercised on text this module does not contain --
    the idiom `test_a_wrapped_claim_beside_a_content_class_is_detected` already uses here.
    """
    assert _SDK in FORBIDDEN_IN_POLICY, (
        f"{_SDK!r} left the audit's denylist, so this test is watching a name nothing enforces"
    )
    source = Path(inspect.getfile(sdk_callback)).read_text(encoding="utf-8")
    assert _sdk_imports_in(source) == [], "the callback module imports the SDK"

    assert _sdk_imports_in(f"import {_SDK}\n") == [_SDK]
    assert _sdk_imports_in(f"from {_SDK}.types import HookMatcher\n") == [f"{_SDK}.types"]
    assert _sdk_imports_in(f"import {_SDK}.types as t\n") == [f"{_SDK}.types"]
    assert _sdk_imports_in("import json\nfrom chaperone.policy.types import Draft\n") == []


def test_every_chaperone_module_the_callback_imports_is_one_the_purity_audit_covers():
    """The transitive half, and the reason it is cheap: the callback imports `policy/` and nothing else.

    A module that imports no SDK itself but imports one that does has the premise back. Rather than
    walking the graph, this pins the graph to the region `audit_policy_purity` already governs --
    `policy/` may import no LLM client at all, `claude_agent_sdk` among them -- so the audit that
    reads this gate also covers everything underneath it. A future import from `gates/` or `audit/`
    fails here, which is the commit on which somebody has to widen the guard or reconsider.
    """
    source = Path(inspect.getfile(sdk_callback)).read_text(encoding="utf-8")
    imported = sorted({
        module for node in ast.walk(ast.parse(source))
        for module in ([node.module] if isinstance(node, ast.ImportFrom) and node.module
                       else [a.name for a in node.names] if isinstance(node, ast.Import) else [])
        if module.split(".")[0] == "chaperone"
    })
    assert imported, "no chaperone imports were found, so this guard would pass vacuously"
    outside = [m for m in imported if not m.startswith("chaperone.policy.")]
    assert outside == [], f"the callback imports outside the audited pure region: {outside}"
