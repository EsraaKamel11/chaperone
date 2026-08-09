import ast
import asyncio
import dataclasses
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from chaperone.audit.gateway import Gateway
from chaperone.audit.store import AuditStore
from chaperone.gates.checker import Checker, Verdict
from chaperone.gates.engine import decide, denial_result
from chaperone.gates.hook import guarded_call, pre_tool_use
from chaperone.gates.sdk_callback import pre_tool_use_deny
from chaperone.policy.act_classes import ActContext
from chaperone.policy.arguments import BODY_KEYS, TOO_DEEP, sendable_text, unsendable_in
from chaperone.policy.payload import build_act_inputs
from chaperone.policy.types import Draft, Family, Message, Record, ViolationClass
from tools import policy_hook

RECORD = Record(fields={"round_size": "10000000"})
CONTEXT = ActContext(approval_token="tok", tier=2, consented_jurisdictions=frozenset({"US"}),
                     granted_tools=frozenset({"send_message"}), sent_count=0, send_cap=50)
CLEAN = Checker("sonnet-tier", "sonnet-tier", transport=lambda m: Verdict(violates=False, confidence=0.9), retries=0)


def _draft(body: str) -> Draft:
    return Draft(thread=(Message(role="investor", body="?"),), body=body, cited_fields=(),
                 recipient_jurisdiction="US", recipient_domain="example.test", tool_name="send_message")


class TrackingRegistry(dict):
    """Records every key lookup, so 'never referenced' is observable."""

    def __init__(self, mapping):
        super().__init__(mapping)
        self.lookups: list[str] = []

    def __getitem__(self, key):
        self.lookups.append(key)
        return super().__getitem__(key)


def _gateway(tmp_path: Path) -> Gateway:
    return Gateway(AuditStore(tmp_path / "audit.jsonl"), principal="agent", tier=2)


def test_an_allowed_call_reaches_the_tool(tmp_path: Path):
    registry = TrackingRegistry({"send_message": lambda **kw: "sent"})
    result = guarded_call(_gateway(tmp_path), "send_message", {}, _draft("The round is $10M."),
                          RECORD, CONTEXT, CLEAN, registry)
    assert result.allowed is True
    assert registry.lookups == ["send_message"]


def test_a_denied_call_never_indexes_the_registry(tmp_path: Path):
    """The ordering IS the guarantee: not 'not called', but 'never looked up'."""
    registry = TrackingRegistry({"send_message": lambda **kw: "sent"})
    result = guarded_call(_gateway(tmp_path), "send_message", {}, _draft("Returns are guaranteed."),
                          RECORD, CONTEXT, CLEAN, registry)
    assert result.allowed is False
    assert registry.lookups == []


def test_a_newly_added_send_surface_is_gated_without_touching_it(tmp_path: Path):
    """Enforcement lives in the engine. A second tool inherits it for free."""
    registry = TrackingRegistry({"send_reply": lambda **kw: "sent"})
    context = ActContext(approval_token="tok", tier=2, consented_jurisdictions=frozenset({"US"}),
                         granted_tools=frozenset({"send_reply"}), sent_count=0, send_cap=50)
    draft = Draft(thread=(Message(role="investor", body="?"),), body="Returns are guaranteed.",
                  cited_fields=(), recipient_jurisdiction="US", recipient_domain="example.test",
                  tool_name="send_reply")
    result = guarded_call(_gateway(tmp_path), "send_reply", {}, draft, RECORD, context, CLEAN, registry)
    assert result.allowed is False
    assert registry.lookups == []


def test_deny_is_never_relabelled_as_retryable(tmp_path: Path):
    result = guarded_call(_gateway(tmp_path), "send_message", {}, _draft("Returns are guaranteed."),
                          RECORD, CONTEXT, CLEAN, TrackingRegistry({"send_message": lambda **kw: "sent"}))
    payload = result.decision
    from chaperone.gates.engine import denial_result
    assert denial_result(payload)["is_retryable"] is False


def test_the_same_policy_denies_at_all_three_layers(tmp_path: Path):
    """Portability: the control is architectural, not an artefact of one integration.

    Layer 1 is an out-of-process command hook that receives JSON on stdin and blocks by exit code.
    Layer 2 is the in-process framework hook. Layer 3 is the executor chokepoint. **Layer 4 is the
    in-process deny callback**, shaped to the Agent SDK's `PreToolUse` contract and importing no SDK,
    so it takes the same payload the command hook reads and returns the deny as plain JSON. One
    predicate set, four enforcement points, same verdict.

    The name is kept at three because `docs/architecture.md` and `docs/failure-modes.md` cite it,
    and a name that understates is the safe direction: every layer it named still agrees.
    """
    import json, subprocess, sys
    from pathlib import Path as P

    from chaperone.gates.hook import pre_tool_use

    draft = _draft("Returns are guaranteed.")

    # One payload, read by the two layers that take one, so "the same draft" is a fact about the
    # object rather than about two literals that happen to match.
    #
    # `approval_token` is supplied so every layer refuses for the tripwire and not for a
    # missing token: without it the act-class fires first and this passes on an unrelated denial,
    # which is what "the same policy" would then be resting on.
    payload = {"tool_input": {"body": draft.body, "jurisdiction": "US",
                              "tool_name": "send_message", "cited_fields": [],
                              "approval_token": "tok"}}

    # Layer 1: a real out-of-process hook script.
    guard = P(__file__).resolve().parents[2] / "tools" / "policy_hook.py"
    completed = subprocess.run(
        [sys.executable, str(guard)], input=json.dumps(payload), capture_output=True, text=True,
    )

    # Layer 2: the in-process framework hook.
    hook_outcome = pre_tool_use("send_message", {}, (draft, RECORD, CONTEXT, CLEAN))

    # Layer 3: the executor chokepoint.
    executor_result = guarded_call(_gateway(tmp_path), "send_message", {}, draft, RECORD, CONTEXT,
                                   CLEAN, TrackingRegistry({"send_message": lambda **kw: "sent"}))

    # Layer 4: the in-process deny callback. Offline and keyless -- a plain coroutine over a dict.
    callback = asyncio.run(pre_tool_use_deny(payload, None, None))

    assert completed.returncode == 2
    assert hook_outcome.allow is False
    assert executor_result.allowed is False
    assert callback["hookSpecificOutput"]["permissionDecision"] == "deny"
    # One draft, one reason, four layers -- not four refusals that happen to coincide.
    assert "content:forward_looking_return" in completed.stderr
    assert hook_outcome.payload["category"] == "content:forward_looking_return"
    assert executor_result.decision.findings[0].violation_class.value == "content:forward_looking_return"
    assert "content:forward_looking_return" in callback["hookSpecificOutput"]["permissionDecisionReason"]


def test_the_out_of_process_layer_allows_a_compliant_draft(tmp_path: Path):
    """The payload carries the record and the token, because the hook may invent neither.

    As written in the brief this asserted 0 on a payload with a `$10M` body and no `record`, so
    `act:figure_not_in_record` fired on a figure nothing corroborated -- the guard was right and
    the payload was short. Supplying them is the fix; defaulting them inside the hook would have
    been the guard fabricating its own evidence.
    """
    import json, subprocess, sys
    from pathlib import Path as P

    guard = P(__file__).resolve().parents[2] / "tools" / "policy_hook.py"
    completed = subprocess.run(
        [sys.executable, str(guard)],
        input=json.dumps({"tool_input": {"body": "The round is $10M.", "jurisdiction": "US",
                                         "tool_name": "send_message", "cited_fields": [],
                                         "record": {"round_size": "10000000"},
                                         "approval_token": "tok"}}),
        capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_only_the_pure_layer_ports_out_of_process():
    """Purity is what makes portability possible, and the limit is worth stating.

    Act-classes and tripwires are pure functions, so they run anywhere: a shell hook, an SDK loop,
    an executor. The checker needs a model call and state, so it does not port to a stdin/stdout
    guard. The deterministic half of the architecture is the portable half, by construction.
    """
    import inspect
    from pathlib import Path as P

    source = P(__file__).resolve().parents[2].joinpath("tools", "policy_hook.py").read_text(encoding="utf-8")
    assert "evaluate_act_classes" in source
    assert "evaluate_tripwires" in source
    assert "Checker" not in source


# ---------------------------------------------------------------------------
# The out-of-process layer, swept for fail-open paths.
#
# Exit 2 blocks a PreToolUse hook. **Exit 1 does not** -- it is an error the runtime reports and
# steps over -- so every way of leaving this script other than a deliberate 2 is an allow. That
# asymmetry is what makes the sweep below different from the same sweep over an in-process gate,
# where an exception at least stops the call.
# ---------------------------------------------------------------------------

GUARD = Path(__file__).resolve().parents[2] / "tools" / "policy_hook.py"

#: The module the payload adapter lives in. Taken from the imported function rather than written as
#: a path, so the source the AST walks below parse is provably the source the tests above ran: a
#: path can be repointed at a file nothing imports, and the walk would then derive facts about a
#: module that governs nothing. `GUARD` stays a path because that one is executed as a subprocess.
ADAPTER = inspect.getmodule(build_act_inputs)

_COMPLIANT = {"body": "The round is $10M.", "jurisdiction": "US", "tool_name": "send_message",
              "cited_fields": [], "record": {"round_size": "10000000"}, "approval_token": "tok"}


def _run_hook(payload, *, flags=()) -> subprocess.CompletedProcess:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, *flags, str(GUARD)], input=text,
                          capture_output=True, text=True)


def _send(**overrides) -> dict:
    return {"tool_input": {**_COMPLIANT, **overrides}}


def test_a_payload_that_cannot_be_decoded_blocks():
    """`except json.JSONDecodeError: return 0` made an unreadable payload an allow.

    A guard that cannot read its input has not evaluated the policy, and engine.py states the
    doctrine for the whole project: every way of not getting a usable answer ends in a denial.
    """
    assert _run_hook("not json at all").returncode == 2


def test_an_empty_payload_blocks():
    """Empty stdin raises `JSONDecodeError` too, so it took the same allow."""
    assert _run_hook("").returncode == 2


def test_a_payload_that_is_not_an_object_blocks():
    """`payload.get` on a JSON list raises `AttributeError`, and an uncaught raise exits 1."""
    assert _run_hook("[1, 2, 3]").returncode == 2


def test_a_field_the_hook_cannot_parse_blocks_rather_than_crashing_open():
    """`int(tool_input.get("tier", 2))` on a non-numeric tier raised, and a raise exits 1.

    Exit 1 does not block. So the draft below -- which trips a tripwire on any reading -- was
    allowed through by the crash, and the stronger the malformed input the more reliably it
    escaped. Every escape from `main` other than a returned code is now a 2.
    """
    completed = _run_hook(_send(body="Returns are guaranteed.", tier="high"))
    assert completed.returncode == 2


def test_the_hook_blocks_when_the_package_is_not_installed():
    """`sys.path[0]` is the script's own directory -- `tools/` -- never the repo root.

    `pythonpath = [".", "src"]` in pyproject.toml is pytest-only configuration, so nothing puts
    `src/` on the path of a plain `python tools/policy_hook.py`. It resolved here only because the
    working copy happens to carry an editable install, and CI's `pip install -e .` hides it the
    same way: a guard that is green because of ambient environment state is not a guard. Without
    the install the import raised `ModuleNotFoundError` before `main` was ever entered, which
    exits 1, which does not block.

    `-S` reproduces the uninstalled checkout by skipping `site`, so the editable `.pth` is not
    processed. It also removes pydantic, which makes this the *effect* version of
    `test_only_the_pure_layer_ports_out_of_process`'s text search: a run that gets to a verdict
    under `-S` has proved it imports nothing that needs the model layer.
    """
    unreachable = subprocess.run([sys.executable, "-S", "-c", "import chaperone"], capture_output=True, text=True)
    assert unreachable.returncode != 0, "chaperone is reachable under -S, so the bootstrap is not what is tested"
    no_pydantic = subprocess.run([sys.executable, "-S", "-c", "import pydantic"], capture_output=True, text=True)
    assert no_pydantic.returncode != 0, "-S no longer hides site-packages, so the purity half proves nothing"
    completed = _run_hook(_send(body="Returns are guaranteed."), flags=("-S",))
    assert completed.returncode == 2, completed.stderr


def test_a_missing_approval_token_is_not_a_present_one():
    """`approval_token=tool_input.get("approval_token", "present")` fabricated the token.

    The default is the string the act-class is checking for, so a payload that carried no token at
    all satisfied the tier-2 rule. A guard may not invent the evidence it is guarding.
    """
    payload = {"tool_input": {k: v for k, v in _COMPLIANT.items() if k != "approval_token"}}
    completed = _run_hook(payload)
    assert completed.returncode == 2
    assert "act:no_approval_token" in completed.stderr


def test_the_tool_name_is_read_from_where_the_runtime_puts_it():
    """`tool_input` holds a tool's *arguments*; the tool's name sits beside it in the payload.

    `.claude/settings.json` wires `guard_edit.py` with a `matcher`, and `guard_edit.py` reads
    `tool_input.file_path` -- an argument of Write. No tool takes its own name as an argument, so
    `tool_input.get("tool_name")` read a key the runtime does not send, `draft.tool_name` was
    `None`, and `evaluate_act_classes` skips its grant check entirely on `None`. Least privilege
    is design spec 4.2's first layer, and this silently removed it.
    """
    completed = _run_hook({"tool_name": "wire_funds",
                           "tool_input": {k: v for k, v in _COMPLIANT.items() if k != "tool_name"}})
    assert completed.returncode == 2
    assert "act:tool_outside_grant" in completed.stderr


def test_the_payload_level_wins_when_the_two_levels_name_different_tools():
    """Construct the disagreement before asserting the two levels agree.

    The runtime owns the tool name and `tool_input` owns the arguments, so a name appearing in both
    places is a conflict the authoritative level has to win. Preferring the argument level is the
    confused deputy `_decide_for` refuses one layer up: the grant would be checked against
    `send_message`, which is granted, while `wire_funds` is what runs.
    """
    completed = _run_hook({"tool_name": "wire_funds",
                           "tool_input": {**_COMPLIANT, "tool_name": "send_message"}})
    assert completed.returncode == 2
    assert "act:tool_outside_grant" in completed.stderr


def test_a_payload_naming_no_tool_at_all_blocks():
    """Neither level carries it, so the hook cannot tell which grant to check. That is a denial."""
    payload = {"tool_input": {k: v for k, v in _COMPLIANT.items() if k != "tool_name"}}
    assert _run_hook(payload).returncode == 2


def test_a_body_the_hook_cannot_find_blocks_rather_than_evaluating_an_empty_one():
    """`tool_input.get("body", "")` scored the empty string when the key was named otherwise.

    Every predicate here reads `draft.body`, so a payload whose message sits under any other key
    was evaluated as a blank draft, found clean, and allowed -- while the text it actually carried
    trips a tripwire. An absent key means the hook did not find the message, which is not the same
    as a message that is empty, and only the second is safe to score.
    """
    completed = _run_hook({"tool_input": {"text": "Returns are guaranteed.", "jurisdiction": "US",
                                          "tool_name": "send_message", "approval_token": "tok"}})
    assert completed.returncode == 2
    completed_empty = _run_hook(_send(body=""))
    assert completed_empty.returncode == 0, "an explicitly empty body is a message, not a missing key"


def test_a_guard_that_cannot_explain_itself_still_blocks():
    """The verdict travels in the exit code, so losing stderr must not lose the verdict.

    With stderr unwritable the shutdown flush fails and the process exits 120 -- not 0, but not 2
    either, and only 2 blocks. Both routes to a 2 are checked, because the reason line is printed
    from two places and a fix applied to one of them would leave the other exiting 120.
    """
    import os

    handle = os.open(os.devnull, os.O_RDONLY)
    try:
        # The premise, asserted on whatever platform this runs: the handle really is unwritable.
        # Measured on Windows across CPython 3.11.15 and 3.13.9; **unverified on Linux**, where CI
        # runs. Without this guard a platform that quietly permitted the write would make the test
        # pass while proving nothing, which is the failure mode a single-platform measurement has.
        try:
            os.write(handle, b"x")
        except OSError:
            pass
        else:
            raise AssertionError("stderr redirect is writable here, so this test proves nothing")
        malformed = subprocess.run([sys.executable, str(GUARD)], input="not json", text=True, stderr=handle)
        policy = subprocess.run([sys.executable, str(GUARD)],
                                input=json.dumps(_send(body="Returns are guaranteed.")),
                                text=True, stderr=handle)
    finally:
        os.close(handle)
    assert (malformed.returncode, policy.returncode) == (2, 2)


#: The predicates the out-of-process layer is known to owe, as of the last time this was measured.
#: The derivation below is what catches the *next* one; this floor is what stops the derivation
#: itself shrinking to nothing while still passing. `assert pure` alone could not see that: if
#: `_decide_for` ever reached a predicate attribute-style, the set contracts, the non-emptiness
#: holds, and the detector quietly stops watching the predicate it was written for.
_PREDICATE_FLOOR = frozenset({
    "evaluate_act_classes", "validate_citations", "evaluate_tripwires", "unsendable_finding",
})


def _pure_predicates_reachable_from(entry) -> set[str]:
    """Every `policy/` function the in-process gate reaches, following calls through `gates/`.

    Transitive, and rooted at the layer's real entry point rather than at `decide`. Rooting it at
    `decide` was correct until `_decide_for` was introduced in front of it, at which point the
    detector was watching a function neither in-process layer calls first any more -- so the
    argument binding added in that same commit was invisible to it. A guard made stale by the
    change that needed guarding.

    **A callee spelled as an attribute is still a callee**, and the walk used to skip every one of
    them: `ast.Name` alone reads `validate_citations(...)` and steps straight past
    `citations.validate_citations(...)`, which is the same call. A predicate reached that way did
    not enter `pure` at all, so `sorted(pure - invoked) == []` was satisfied by a hook that never
    ran it -- a drift detector blind in the direction it exists to watch. `_PREDICATE_FLOOR` closes
    that for the four predicates known at the time it was written and cannot close it for the next
    one, which is the whole reason the set is derived rather than listed.

    `tests/testing/test_recorded.py::_callee_names` already solves this in this repository, and
    this is the same shape: `Name`, or the final attribute of a dotted call, resolved through the
    module the call was written in.
    """
    seen, pending, pure = set(), [entry], set()
    while pending:
        function = pending.pop()
        if function in seen:
            continue
        seen.add(function)
        module = inspect.getmodule(function)
        for node in ast.walk(ast.parse(inspect.getsource(function))):
            if not isinstance(node, ast.Call):
                continue
            target = _resolve_callee(module, node.func)
            if not inspect.isfunction(target):
                continue
            if target.__module__.startswith("chaperone.policy"):
                pure.add(target.__name__)
            elif target.__module__.startswith("chaperone.gates"):
                pending.append(target)
    return pure


def _resolve_callee(module, func):
    """The function a call node names, resolved the way the interpreter would resolve it.

    `f(...)` is looked up in the calling module. `m.f(...)` is looked up on whatever `m` is bound
    to there, which is `None` for a parameter such as `checker` or `gateway` -- so a call on an
    argument resolves to nothing and is skipped, exactly as it was before.
    """
    if isinstance(func, ast.Name):
        return getattr(module, func.id, None)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return getattr(getattr(module, func.value.id, None), func.attr, None)
    return None


def test_the_hook_runs_every_pure_predicate_the_in_process_gate_consults():
    """The drift detector, rooted at `_decide_for` and floored so it cannot silently narrow.

    The out-of-process layer shipped with `evaluate_act_classes + evaluate_tripwires` and no
    `validate_citations`, so a fabricated citation denied in process and allowed out of process --
    two layers, two policies, which is the opposite of what design spec 6.3 claims. Naming the
    predicates in a literal list here would pin today's omission and miss tomorrow's, so the set is
    derived: every function the gate reaches that lives in `policy/` is pure by the static audit's
    own guarantee, therefore ports, therefore must appear.
    """
    from chaperone.gates import hook as hook_module

    pure = _pure_predicates_reachable_from(hook_module._decide_for)
    assert pure, "derived no pure predicates, so this test would assert nothing"
    assert _PREDICATE_FLOOR <= pure, (
        f"the derivation narrowed and stopped watching: {sorted(_PREDICATE_FLOOR - pure)}"
    )

    # Called, not merely mentioned. A substring search over the source was the first version and it
    # was satisfiable by prose: the comment beside the call in policy_hook.py names
    # `validate_citations`, so deleting the call and keeping the comment passed. An `ast.Call`
    # produces no node for a docstring or a comment, which is the same idiom test_engine.py's
    # `_functions_reading` already uses for exactly this reason.
    #
    # Both spellings counted, for the reason `_pure_predicates_reachable_from` now reads both:
    # `citations.validate_citations(...)` in the guard is the predicate running, and a set that
    # only saw bare names would have demanded a call that was already there.
    guard_tree = ast.parse(GUARD.read_text(encoding="utf-8"))
    invoked = set()
    for node in ast.walk(guard_tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            invoked.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            invoked.add(node.func.attr)
    assert sorted(pure - invoked) == []

    # What makes the two sets above comparable: a callee's spelling is its name. `from
    # chaperone.policy.citations import validate_citations as v` followed by `v(...)` puts the
    # predicate in the guard's tree under a name neither set is looking for, and `pure - invoked`
    # would then demand a call that is being made. Forbidding the rebinding is cheaper and more
    # honest than chasing it, and this module has no reason to want one --
    # `tests/testing/test_recorded.py` bans aliases in its own transport for the same reason.
    aliased = [
        alias.asname
        for node in ast.walk(guard_tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
        if alias.asname
    ]
    assert aliased == [], (
        f"tools/policy_hook.py binds an import under an alias ({aliased}); the predicate-parity "
        "comparison above matches callees by name and cannot see a renamed one"
    )

    # Function-level parity is not class-level parity, and the difference has to be declared rather
    # than inferred from silence. `evaluate_act_classes` runs here, but two of the classes it can
    # produce turn on inputs this process holds no trustworthy source for -- a count that lives in
    # a log it cannot read, and an approval that arrives from the caller being judged -- so the
    # out-of-process layer enforces a strict subset. Equality, not containment: a declaration that
    # may quietly grow is a declaration nobody has to justify. A test below exhibits each entry.
    #
    # Equality is also the direction the derived test does *not* cover. It requires every
    # payload-decided class to be declared and would accept an over-declaration -- a class listed
    # here that this layer can in fact decide, which understates the guard. The two run together for
    # that reason: derivation for the omissions, this literal for the additions.
    assert policy_hook.UNENFORCEABLE_HERE == frozenset(
        {ViolationClass.SEND_CAP_EXCEEDED, ViolationClass.NO_APPROVAL_TOKEN,
         ViolationClass.FIGURE_NOT_IN_RECORD}
    )


# One logical draft per row, rendered twice: as JSON for the subprocess, and as objects for
# `decide`. Both allows and denies, and the assertion below refuses a table that loses either.
_DEFAULTS = {"body": "We have shared the requested details.", "jurisdiction": "US",
             "tool_name": "send_message", "cited_fields": [], "record": {"round_size": "10000000"},
             "approval_token": "tok", "tier": 2, "sent_count": 0, "send_cap": 50}

#: Extra `tool_input` keys, which correspond to the in-process `args`: out of process the tool's
#: arguments *are* `tool_input`, so a key no predicate consumes is exactly an argument the gate did
#: not judge. Kept beside the corpus so both layers receive the same thing under both spellings.
_EXTRA = "extra"

_CORPUS = [
    ("a compliant draft", {}),
    ("an unconsumed key carrying a guarantee", {_EXTRA: {"extra_text": "Returns are guaranteed."}}),
    ("an unconsumed key nested in a container", {_EXTRA: {"attach": {"note": "Honestly, a great deal."}}}),
    ("an unconsumed key carrying an unbacked figure", {_EXTRA: {"amount": 99_000_000}}),
    ("prose smuggled in as a mapping key", {_EXTRA: {"Returns are guaranteed.": "The round is $10M."}}),
    ("an unconsumed key carrying a routing token", {_EXTRA: {"reply_to": "example.test"}}),
    ("an unconsumed key beside an act-class denial", {_EXTRA: {"note": "Returns are guaranteed."},
                                                     "jurisdiction": "DE"}),
    ("a figure the record holds", {"body": "The round is $10M."}),
    ("a citation that resolves", {"body": "The round is $10M.", "cited_fields": ["round_size"]}),
    ("a guarantee of returns", {"body": "Returns are guaranteed."}),
    ("advice on the merits", {"body": "Honestly, the round is a great opportunity."}),
    ("a jurisdiction outside consent", {"jurisdiction": "DE"}),
    ("a tool outside the grant", {"tool_name": "wire_funds"}),
    ("a figure the record does not hold", {"body": "The round is $8M."}),
    ("a citation the record does not hold", {"cited_fields": ["valuation"]}),
    ("no approval token at tier 2", {"approval_token": None}),
    ("the send budget already spent", {"sent_count": 50}),
]

_BLOCKED = re.compile(r"^blocked: (\S+) \((.*)\)$")


def _in_process(row: dict, extra: dict | None = None):
    """The in-process verdict for one corpus row, through the layer's real entry point.

    `pre_tool_use`, not `decide`: `_decide_for` sits in front of `decide` and owns the argument
    binding, so comparing against `decide` would compare the out-of-process layer to a predicate
    set neither in-process layer uses any more.
    """
    draft = Draft(thread=(Message(role="investor", body=""),), body=row["body"],
                  cited_fields=tuple(row["cited_fields"]), recipient_jurisdiction=row["jurisdiction"],
                  recipient_domain="example.test", tool_name=row["tool_name"])
    context = ActContext(approval_token=row["approval_token"], tier=row["tier"],
                         consented_jurisdictions=policy_hook.CONSENTED,
                         granted_tools=policy_hook.GRANTED,
                         sent_count=row["sent_count"], send_cap=row["send_cap"])
    return pre_tool_use(row["tool_name"], extra or {},
                        (draft, Record(fields=row["record"]), context, CLEAN))


def test_the_two_enforcement_layers_reach_the_same_verdict_and_the_same_category():
    """Design spec 6.3, asserted as agreement rather than as two denials of one draft.

    `test_the_same_policy_denies_at_all_three_layers` feeds one guaranteed-returns draft to three
    layers and watches all three refuse it. A layer that refused *everything* would pass it, and
    so would a layer that refused this draft for an unrelated reason -- which is the shape the
    missing `validate_citations` actually had. This runs both layers over a corpus that includes
    allows, and compares the primary category as well as the verdict, so agreement has to be
    earned on each row rather than on the aggregate.

    The context is imported from the hook rather than restated. `CONSENTED` and `GRANTED` are
    deployment configuration, not policy: it is the predicate set that has to be one thing, and
    holding the configuration equal is what isolates that claim. What this therefore does NOT
    show is that a deployment cannot configure the two layers differently -- see the limit test
    below, where a jurisdiction one layer consents to and the other does not is exhibited.
    """
    verdicts = {}
    for label, overrides in _CORPUS:
        extra = overrides.get(_EXTRA, {})
        # A key this layer consumes is a policy *field* out of process and an *argument* in
        # process, so a row naming one compares two different things and silently passes or fails
        # for the wrong reason. Caught by this corpus on `{"body": "example.test"}`, which out of
        # process replaced the draft body rather than arriving as an argument.
        assert not (set(extra) & policy_hook.CONSUMED_KEYS), f"{label}: extra key is a consumed field"
        row = {**_DEFAULTS, **{k: v for k, v in overrides.items() if k != _EXTRA}}
        payload = {"tool_input": {**row, **extra}}
        completed = _run_hook(payload)
        outcome = _in_process(row, extra)
        # The fourth surface, on the identical payload the command hook reads. It is the layer the
        # unconsumed-key rows bear on hardest: it builds from the same adapter, which reaches no key
        # outside `CONSUMED_KEYS`, so a callback that skipped that check would allow six of the rows
        # below while the other layers refuse them.
        callback = asyncio.run(pre_tool_use_deny(payload, None, None))
        assert completed.returncode in (0, 2), f"{label}: hook exited {completed.returncode}"
        assert (completed.returncode == 2) is (not outcome.allow), (
            f"{label}: out-of-process blocked={completed.returncode == 2}, "
            f"in-process denied={not outcome.allow} ({completed.stderr.strip()})"
        )
        assert (callback != {}) is (not outcome.allow), (
            f"{label}: callback denied={callback != {}}, in-process denied={not outcome.allow} "
            f"({callback})"
        )
        if not outcome.allow:
            match = _BLOCKED.match(completed.stderr)
            assert match, f"{label}: no parseable reason on stderr: {completed.stderr!r}"
            assert match.group(1) == outcome.payload["category"], label
            # The detail as well as the class: the finding is built in one shared function now,
            # and comparing categories alone would not have noticed the wording diverging back.
            assert match.group(2) == str(outcome.payload["detail"]), label
            # The callback reports one string rather than a class and a detail, so the whole of it
            # is compared against the pair. Equality and not containment: the primary finding is
            # `findings[0]`, so a layer that ordered its predicates differently would report a
            # category this row also produces, and a substring check would accept it.
            assert callback["hookSpecificOutput"]["permissionDecisionReason"] == (
                f"{outcome.payload['category']}: {outcome.payload['detail']}"
            ), label
        verdicts[label] = outcome.allow
    assert set(verdicts.values()) == {True, False}, f"the corpus lost a whole outcome: {verdicts}"


def test_the_consumed_key_list_is_derived_from_the_adapter_rather_than_remembered():
    """`CONSUMED_KEYS` decides which payload keys are checked as content, so a stale entry is a leak.

    A key read by the builder but missing from the literal would be treated as an unconsumed
    argument and refused -- noisy but safe. A key *in* the literal that nothing reads is the
    dangerous direction: it is exempted from the content check while reaching no predicate. Both
    fail here, because the set is compared against every key the module's own AST shows it reading.

    **Parsed where the reads are, which is no longer where the guard is.** The construction moved to
    `policy/payload.py` and this walk moved with it; against `tools/policy_hook.py` it derives the
    empty set, and the floor below is what made that a failure rather than a silent pass. Measured
    on the move: `"body" not in tool_input` stays in `main` and never contributed anyway, because it
    is an `ast.NotIn` and the branch below matches `ast.In`.

    **The matched name stays `tool_input` and does not widen to the adapter's `payload` parameter.**
    `payload.get("tool_input")` and `payload.get("tool_name")` read the *outer* payload, and a walk
    that counted them would put `"tool_input"` -- which is not a tool argument at all -- into the
    derived set and fail against a correct literal. The right-hand side is read through
    `policy_hook`, so the re-export is held to the same set the adapter derives.

    **Why this parses the adapter alone and not the adapter plus the guard**, which is the obvious
    worry and the wrong instinct. `CONSUMED_KEYS` is an *exemption* set: membership means "skip the
    outbound-content check". Comparing it against the adapter's reads alone pins every exempt key to
    a read this walk can prove reaches the triple. A union with `main`'s reads would assert
    `adapter_reads | guard_reads == CONSUMED_KEYS`, which *permits* a key read only in `main` to sit
    in the exempt set -- and a key `main` reads need reach no predicate at all. The union is the
    wider set on the fail-open side, so it is the weaker guard. The hole it would close is not a
    hole: a key read in `main` and absent from the set is exactly what
    `unsendable_finding` receives, so it is content-checked rather than let through.
    """
    tree = ast.parse(inspect.getsource(ADAPTER))
    read: set[str] = set()
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get" and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "tool_input" and node.args:
            target = node.args[0]
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
                and node.value.id == "tool_input":
            target = node.slice
        elif isinstance(node, ast.Compare) and len(node.comparators) == 1 \
                and isinstance(node.ops[0], ast.In) \
                and isinstance(node.comparators[0], ast.Name) \
                and node.comparators[0].id == "tool_input":
            target = node.left
        if isinstance(target, ast.Constant) and isinstance(target.value, str):
            read.add(target.value)
    assert read, "found no tool_input reads, so this test would assert nothing"
    assert read == policy_hook.CONSUMED_KEYS


def test_the_thread_is_reviewed_as_input_and_is_not_sendable():
    """Reviewed is not the same as reviewed-as-outbound, and the difference is a live leak.

    The reviewed surface once admitted every thread role and body on the grounds that the gate had
    seen them. It had -- as the *incoming* conversation. With a real thread that made every investor
    utterance shippable in the body slot, text no content-class ever judged as something being sent,
    which is the same confusion as reviewing one object and shipping another.
    """
    draft = Draft(thread=(Message(role="investor", body="Honestly, is this a good deal?"),),
                  body="The round is $10M.", cited_fields=(), recipient_jurisdiction="US",
                  recipient_domain="example.test", tool_name="send_message")
    assert "Honestly, is this a good deal?" not in sendable_text(draft)
    assert "investor" not in sendable_text(draft)
    assert unsendable_in({"body": draft.thread[0].body}, draft)
    assert unsendable_in({"role": "investor"}, draft)
    assert not unsendable_in({"body": draft.body}, draft)


def test_a_routing_token_may_not_stand_in_for_the_message():
    """Set membership is not positional binding, and the body slot is where that matters.

    `example.test` is genuinely sendable -- as the recipient. Accepted in a body slot it becomes the
    message, which no content-class judged as prose. `BODY_KEYS` binds those names to the reviewed
    body exactly, rather than to membership.
    """
    draft = _draft("The round is $10M.")
    assert unsendable_in({"body": "example.test"}, draft)
    assert unsendable_in({"message": "US"}, draft)
    assert not unsendable_in({"to": "example.test"}, draft)
    assert not unsendable_in({"body": draft.body}, draft)


#: Argument names a real send tool plausibly carries, none of them a body slot. Each is checked
#: against the drafted body, because the body reappearing as a subject line or a filename is the
#: reviewed message travelling somewhere no content-class judged it for.
_NON_BODY_SLOTS = ("subject", "to", "cc", "bcc", "filename", "thread_id", "preview", "title",
                   "jurisdiction", "reply_to")


def test_the_drafted_body_may_occupy_the_body_slot_and_nowhere_else():
    """`sendable_text` contained `draft.body`, so membership alone let it travel anywhere.

    All ten names below accepted the reviewed message and delivered it -- as a subject line, a
    filename, a preview. The docstring beside the rule claimed "no model-authored prose is in that
    surface" while the drafted body was the one piece of model-authored prose in it: a declaration
    reading as exhaustive while being incomplete, written in the edit that closed a real defect.

    Correcting only the sentence would have left the slot semantics open, so the body is out of the
    membership set entirely and reachable only through `BODY_KEYS`.
    """
    draft = _draft("The round is $10M.")
    assert draft.body not in sendable_text(draft)
    accepted = [name for name in _NON_BODY_SLOTS if not unsendable_in({name: draft.body}, draft)]
    assert accepted == [], f"the drafted body is still sendable under: {accepted}"
    # And the slot it *is* for still works, or the rule has simply become "refuse the body".
    for name in sorted(BODY_KEYS):
        assert not unsendable_in({name: draft.body}, draft), name


def test_the_body_slot_pins_its_whole_subtree_not_just_its_first_level():
    """The mapping branch took the inner name, so the pin survived exactly one level.

    `{"body": "example.test"}` was refused while `{"body": {"x": "example.test"}}` was delivered:
    `x` is not a body key, so the nested value fell back to membership and a recipient domain
    became the message. The list branch already propagated the outer key; only the mapping branch
    did not, which is why the two disagreed on the same smuggled value.
    """
    draft = _draft("The round is $10M.")
    assert unsendable_in({"body": {"x": "example.test"}}, draft)
    assert unsendable_in({"body": {"x": {"y": "example.test"}}}, draft)
    assert unsendable_in({"body": ["example.test"]}, draft)
    assert unsendable_in({"body": [{"x": "example.test"}]}, draft)
    # The reviewed body nested under its own slot is still fine, so this is a pin and not a ban.
    assert not unsendable_in({"body": {"x": draft.body}}, draft)
    # A body key nested under an ordinary key is pinned by the inner name, as before.
    assert unsendable_in({"attach": {"body": "example.test"}}, draft)


def test_arguments_too_deep_to_examine_are_denied_rather_than_raised(tmp_path: Path):
    """Design spec 3.4's named trap, reached by an argument shape rather than by a gate outage.

    `unsendable_in` recurses, so cyclic or very deeply nested `args` raised `RecursionError` out of
    `guarded_call`. A defensively-written executor wraps handler invocation in a catch-all, and the
    deny then arrives at the agent relabelled "transient -- please retry the forbidden send". The
    out-of-process layer contained it in its `BaseException` net; the in-process layer did not.

    Asserted as a returned `GatewayResult` with nothing transmitted, which is the property; that no
    exception escapes is implied by the call returning at all.
    """
    draft = _draft("The round is $10M.")
    cyclic: dict = {}
    cyclic["self"] = cyclic
    deep: dict = {}
    cursor = deep
    for _ in range(3000):
        cursor["next"] = {}
        cursor = cursor["next"]

    for label, args in (("cyclic", cyclic), ("3000 deep", deep)):
        registry = TrackingRegistry({"send_message": lambda **kw: "sent"})
        result = guarded_call(_gateway(tmp_path / label), "send_message", args, draft,
                              RECORD, CONTEXT, CLEAN, registry)
        assert result.allowed is False, label
        assert registry.lookups == [], label
        assert denial_result(result.decision)["category"] == "other", label
        assert TOO_DEEP in denial_result(result.decision)["detail"], label


def test_the_out_of_process_layer_allows_an_unconsumed_key_it_can_account_for():
    """The half of the probe that had no committed test out of process.

    Every unconsumed-key row in the corpus denied, so the suite showed the rule refusing and never
    showed it permitting -- which a hook that refused *every* unconsumed key would also satisfy.
    The row that was supposed to cover this was named "carrying the reviewed body" but inherited a
    different default body, so its value was not the reviewed body and it denied like the rest.
    """
    base = {"body": "The round is $10M.", "jurisdiction": "US", "tool_name": "send_message",
            "cited_fields": [], "record": {"round_size": "10000000"}, "approval_token": "tok"}
    assert _run_hook({"tool_input": base}).returncode == 0, "the base payload is not clean"
    assert _run_hook({"tool_input": {**base, "reply_to": "example.test"}}).returncode == 0
    assert _run_hook({"tool_input": {**base, "body": "The round is $10M."}}).returncode == 0
    assert _run_hook({"tool_input": {**base, "subject": "The round is $10M."}}).returncode == 2


def test_prose_smuggled_as_a_mapping_key_is_refused():
    """`arg_digest` covers keys, so a key-only divergence is the audit entry the rule exists to stop.

    A key that is a Python identifier is a parameter name and passes unreviewed, because that is
    what `**args` requires of it. One that is not -- a sentence, say -- reaches a `**kwargs` tool
    intact and is checked as content.
    """
    draft = _draft("The round is $10M.")
    assert unsendable_in({"Returns are guaranteed.": draft.body}, draft)
    assert not unsendable_in({"body_html": draft.recipient_domain}, draft)


def test_a_realistic_send_meets_the_rule_as_a_wall_and_that_is_recorded_not_fixed():
    """A stated limit, pinned so widening the rule later is a decision rather than a concession.

    `Draft` carries `recipient_domain` and never a full address, so an ordinary send -- address,
    subject, thread id -- is refused on three unreviewed scalars. The direction is fail-closed and
    the rule is declared, so this is not a defect today. It is where the first real send tool will
    press, and the answer then should be a reviewed routing surface on `Draft` or an explicit
    allowlist, decided deliberately rather than by relaxing the predicate under deadline.
    """
    draft = _draft("The round is $10M.")
    args = {"to": "partner@example.test", "subject": "Following up", "thread_id": "t-9182"}
    refused = unsendable_in(args, draft)
    assert sorted(refused) == ["Following up", "partner@example.test", "t-9182"]


def test_the_out_of_process_layer_enforces_a_strict_subset_and_names_the_gap():
    """§6.3's real bound: predicate parity does not give coverage parity.

    Design spec 3.2 makes the cap predicate pure over `(draft, count)` with the **gateway**
    supplying the count from the audit log. A stateless guard has no log, so a payload that omits
    the count is allowed by a layer that runs `evaluate_act_classes` in full -- the predicate is
    called, the class it would produce is unreachable. Exhibited on the exact draft, so the
    subset relation is a measurement rather than a sentence in a docstring.

    The corpus test passes `sent_count` and `send_cap` in every row precisely because of this: it
    supplies the state so the comparison isolates the predicates.

    This exhibits the cap entry; the approval entry is exhibited by the test directly below, whose
    reason is a different one: not state this process lacks, but evidence it takes from the caller.
    """
    assert policy_hook.UNENFORCEABLE_HERE == frozenset(
        {ViolationClass.SEND_CAP_EXCEEDED, ViolationClass.NO_APPROVAL_TOKEN,
         ViolationClass.FIGURE_NOT_IN_RECORD}
    )
    row = {**_DEFAULTS, "sent_count": 50, "send_cap": 50}
    stateless = {k: v for k, v in row.items() if k not in ("sent_count", "send_cap")}

    assert _run_hook({"tool_input": stateless}).returncode == 0
    outcome = _in_process(row)
    assert outcome.allow is False
    assert outcome.payload["category"] == ViolationClass.SEND_CAP_EXCEEDED.value

    # And with the count supplied, the layers agree again -- so the gap is the missing state, not a
    # missing predicate. Without this the test would equally suit a hook that never checks the cap.
    assert _run_hook({"tool_input": row}).returncode == 2


def test_the_approval_class_is_decided_from_caller_input_and_is_declared_unenforceable():
    """The second gap in the same declaration, exhibited on the two payloads that open it.

    `approval_token` and `tier` both arrive in `tool_input`, so the agent whose action is being
    judged supplies the evidence it is judged on. `{"tier": 1}` puts the action below the rule, and
    **any** non-`None` token satisfies it because nothing validates the value -- so the class is
    suppressible two ways by the caller. `UNENFORCEABLE_HERE` asserted a completeness that did not
    hold, which is worse than no declaration: the module's own comment says a subset declaration
    that lists a closable gap reads as exhaustive while being incomplete.

    Credit to the shape that is right and is kept: `tier` **defaults to 2**, so *absence* is
    fail-closed. The hole is presence, and no default can close it.

    Both rows deny in process on the same class, so this is a disagreement between the layers and
    not a draft that was compliant all along.
    """
    assert ViolationClass.NO_APPROVAL_TOKEN in policy_hook.UNENFORCEABLE_HERE

    for label, override in (
        ("a tier the caller lowered", {"tier": 1, "approval_token": None}),
        ("a token nothing validates", {"approval_token": "not-a-real-approval"}),
    ):
        row = {**_DEFAULTS, "approval_token": None, "tier": 2}
        assert _run_hook({"tool_input": {**row, **override}}).returncode == 0, label
        outcome = _in_process(row)
        assert outcome.allow is False, label
        assert outcome.payload["category"] == ViolationClass.NO_APPROVAL_TOKEN.value, label


def test_the_record_the_figure_class_is_checked_against_arrives_from_the_caller_being_judged():
    """The third gap in the same declaration, exhibited on the payloads that open it.

    `record` is not an attribute of the action the way `body` and `jurisdiction` are. It is the
    ground truth the action is checked against, and out of process it arrives in `tool_input` from
    the agent whose draft is being judged. Two suppressions, both measured here: a record rewritten
    to hold the figure the draft states, and a record grown a field so a fabricated citation
    resolves. `validate_citations` reads the same forged mapping, which is why the citation half
    goes with the act-class half rather than being a separate gap.

    **The forgery has to be the variable**, so each half asserts the true record blocking first.
    Without that this would equally suit a hook that allows everything.

    `test_the_two_enforcement_layers_reach_the_same_verdict_and_the_same_category` cannot see any of
    this: it hands the **same** record to both layers, which is exactly what a forgery breaks.
    """
    assert ViolationClass.FIGURE_NOT_IN_RECORD in policy_hook.UNENFORCEABLE_HERE

    # The act-class half: a figure the record does not hold.
    stated = {**_DEFAULTS, "body": "The round is $8M."}
    assert _run_hook({"tool_input": stated}).returncode == 2, "the true record does not block it"
    truthful = _in_process(stated)
    assert truthful.allow is False
    assert truthful.payload["category"] == ViolationClass.FIGURE_NOT_IN_RECORD.value

    forged = {**stated, "record": {"round_size": "8000000"}}
    assert _run_hook({"tool_input": forged}).returncode == 0, (
        "a record the caller wrote to match its own figure no longer suppresses the class, so this "
        "gap may be closable and the declaration should be revisited rather than kept"
    )

    # The citation half: a field the record does not hold, cited anyway.
    cited = {**_DEFAULTS, "body": "The round is $10M.", "cited_fields": ["valuation"]}
    assert _run_hook({"tool_input": cited}).returncode == 2
    assert _in_process(cited).payload["category"] == ViolationClass.FIGURE_NOT_IN_RECORD.value

    grown = {**cited, "record": {"round_size": "10000000", "valuation": "10000000"}}
    assert _run_hook({"tool_input": grown}).returncode == 0


# --------------------------------------------------------------------------------------------
# `UNENFORCEABLE_HERE`, derived from the guard's own AST rather than maintained by hand
# --------------------------------------------------------------------------------------------

#: The evidence types. A `Draft` is the action's own description of itself and a guard has no choice
#: but to read that from the caller; a `Record` and an `ActContext` are the other side of the
#: comparison, the ground truth and the permissions the action is checked against. Those are what a
#: caller must not supply, so those are what the derivation follows.
_EVIDENCE_TYPES = ("Record", "ActContext")

#: The predicates the derivation must keep binding evidence to. Same purpose as `_PREDICATE_FLOOR`:
#: a walk that silently stops reaching one of them derives fewer classes, requires fewer
#: declarations, and passes while watching less.
_EVIDENCE_PREDICATE_FLOOR = frozenset({"evaluate_act_classes", "validate_citations"})


def _reads_payload(node: ast.AST, tainted: set) -> bool:
    return any(
        isinstance(sub, ast.Name) and (sub.id in ("payload", "tool_input") or sub.id in tainted)
        for sub in ast.walk(node)
    )


def _payload_tainted_locals(function: ast.FunctionDef) -> set:
    """Locals of the builder carrying caller-supplied values, transitively.

    `tool_name` is why this is transitive rather than a search for the name `tool_input` inside each
    constructor call: it is read out of the payload one statement earlier, and a scan without this
    would call that field guard-supplied. `tool_input` itself is now one of them -- the builder
    takes the whole payload and reaches the arguments through it -- which `_reads_payload` already
    covered by naming both roots.
    """
    tainted: set = set()
    for _ in range(3):
        for node in ast.walk(function):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name) \
                    and _reads_payload(node.value, tainted):
                tainted.add(node.targets[0].id)
    return tainted


def _evidence_built_in(function: ast.FunctionDef, tainted: set, module):
    """`{local: (class, {field: came from the payload})}` for each evidence object `function` builds.

    `module` is where the constructor names are resolved, and it is a parameter rather than a
    hardcoded import because the two halves of this derivation now live in two files: the evidence
    is constructed in `policy/payload.py`, and the predicates that receive it are called in
    `tools/policy_hook.py`. Resolving `Record` against the module that no longer imports it would
    raise rather than mislead, but a parameter says which file is being read without the reader
    having to find that out by breaking it.
    """
    built, opaque = {}, []
    for node in ast.walk(function):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id in _EVIDENCE_TYPES):
            continue
        if node.value.args or any(keyword.arg is None for keyword in node.value.keywords):
            opaque.append(node.value.func.id)
            continue
        built[node.targets[0].id] = (
            getattr(module, node.value.func.id),
            {keyword.arg: _reads_payload(keyword.value, tainted) for keyword in node.value.keywords},
        )
    return built, opaque


def _unpacked_into(builder: ast.FunctionDef, caller: ast.FunctionDef, built: dict) -> dict:
    """`built`, rekeyed from the builder's locals to the names the caller unpacks the triple into.

    The construction and the predicate calls are two modules apart now, so the derivation reads two
    ASTs and has to join them. Joining on the local name would be a coincidence -- both files happen
    to say `record` and `context` -- and this test would then rest on it. The join follows the
    builder's `return` order into the caller's unpacking targets instead, so a rename on either side
    is carried rather than silently dropping the evidence on the floor.
    """
    returns = [node for node in ast.walk(builder) if isinstance(node, ast.Return)]
    assert len(returns) == 1 and isinstance(returns[0].value, ast.Tuple), (
        f"{builder.name} no longer ends in exactly one tuple return ({len(returns)} found), so the "
        "join below reads the wrong one and would report an evidence object as never bound"
    )
    order = [e.id for e in returns[0].value.elts if isinstance(e, ast.Name)]
    for node in ast.walk(caller):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Tuple) \
                and isinstance(node.value, ast.Call) \
                and isinstance(node.value.func, ast.Name) \
                and node.value.func.id == builder.name:
            names = [e.id for e in node.targets[0].elts if isinstance(e, ast.Name)]
            return {name: built[source] for name, source in zip(names, order) if source in built}
    return {}


def _evidence_parameters(function: ast.FunctionDef, built: dict) -> dict:
    """`{predicate: {parameter: (class, fields)}}` for every predicate handed an evidence object.

    Bound through the callee's real signature, so the guard's positional
    `evaluate_act_classes(draft, record, context)` is read as the parameters it actually fills.
    """
    bound = {}
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        target = _resolve_callee(policy_hook, node.func)
        if not inspect.isfunction(target) or not target.__module__.startswith("chaperone.policy"):
            continue
        mapping = {}
        for name, argument in zip(inspect.signature(target).parameters, node.args):
            if isinstance(argument, ast.Name) and argument.id in built:
                mapping[name] = built[argument.id]
        for keyword in node.keywords:
            if keyword.arg and isinstance(keyword.value, ast.Name) and keyword.value.id in built:
                mapping[keyword.arg] = built[keyword.value.id]
        if mapping:
            bound[target] = mapping
    return bound


def _roots(expression: ast.AST, taint: dict, parameters) -> set:
    """The evidence an expression reads, as `parameter` or `parameter.attribute`.

    A `Name` that is only the object half of an attribute access is not counted on its own, or
    `context.consented_jurisdictions` would also read as "the whole context" and every class
    touching any part of it would come out payload-decided.
    """
    qualified = {
        sub.value for sub in ast.walk(expression)
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
        and sub.value.id in parameters
    }
    found: set = set()
    for sub in ast.walk(expression):
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) \
                and sub.value.id in parameters:
            found.add(f"{sub.value.id}.{sub.attr}")
        elif isinstance(sub, ast.Name) and sub not in qualified:
            found |= {sub.id} if sub.id in parameters else taint.get(sub.id, set())
    return found


def _classes_by_evidence(function, parameters: dict) -> dict:
    """`{ViolationClass: the evidence the branch producing it turns on}`, from the predicate's AST.

    Local flow is followed to a fixpoint, and `record_values` in `act_classes.py` is why: the record
    is read into a set several statements above the branch that consults it, so a walk over the
    branch condition alone sees a bare local and concludes the class turns on nothing.
    """
    tree = ast.parse(inspect.getsource(function))
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    taint = {name: {name} for name in parameters}
    for _ in range(4):
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name):
                taint.setdefault(node.targets[0].id, set()).update(
                    _roots(node.value, taint, parameters))
            elif isinstance(node, ast.For) and isinstance(node.target, ast.Name):
                taint.setdefault(node.target.id, set()).update(_roots(node.iter, taint, parameters))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and isinstance(node.func.value, ast.Name) \
                    and node.func.attr in ("add", "append", "update", "extend"):
                for argument in node.args:
                    taint.setdefault(node.func.value.id, set()).update(
                        _roots(argument, taint, parameters))

    classes: dict = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "Finding" and node.args):
            continue
        named = node.args[0]
        if not (isinstance(named, ast.Attribute) and isinstance(named.value, ast.Name)
                and named.value.id == "ViolationClass"):
            continue
        expressions = list(node.args)
        cursor = node
        while cursor in parents:
            cursor = parents[cursor]
            if isinstance(cursor, (ast.If, ast.While)):
                expressions.append(cursor.test)
            elif isinstance(cursor, ast.For):
                expressions.append(cursor.iter)
        found: set = set()
        for expression in expressions:
            found |= _roots(expression, taint, parameters)
        classes.setdefault(getattr(ViolationClass, named.attr), set()).update(found)
    return classes


def _from_the_payload(root: str, parameters: dict) -> bool:
    name, _, attribute = root.partition(".")
    cls, fields = parameters[name]
    if not attribute:
        return any(fields.values())
    if attribute in fields:
        return fields[attribute]
    if attribute in {f.name for f in dataclasses.fields(cls)}:
        return False
    # Not a declared field: a method or a derived read, which reaches whatever the object holds.
    # `citations.py` consults the record only through `record.get(...)`, so without this branch the
    # citation half of `act:figure_not_in_record` is invisible to the derivation.
    return any(fields.values())


def test_every_class_deciding_on_evidence_the_payload_supplies_is_declared_unenforceable():
    """The declaration, derived rather than remembered.

    Counted from the history of the set rather than from memory, because a count recalled is the
    defect this module keeps recording: `UNENFORCEABLE_HERE` was created holding
    `act:send_cap_exceeded` alone, and has since been found under-inclusive **twice** -- once for
    `act:no_approval_token`, and once for `act:figure_not_in_record`, which was suppressible from
    the payload while two `==` assertions pinned the set by equality and so made the gap read as
    decided. Both times the fix was to add the entry somebody had noticed, and a third omission
    would have looked exactly like the first two. So the requirement is computed: this reads the
    adapter's own AST for which `Record` and `ActContext` inputs `build_act_inputs` fills from
    `tool_input`, the guard's own AST for which predicates `main` then hands those objects to, each
    predicate's own AST for which class each branch turns on, and requires the intersection to be
    declared.

    **The evidence half and the binding half are two files, because the construction and the
    enforcement are two files** -- the predicate ASTs named above are a third source and not part of
    this split. The evidence half
    moved to `policy/payload.py` so a second decision surface could share it; the binding half
    stayed in `tools/policy_hook.py`, which is where the predicates are called. Reading only the
    guard derives no evidence at all -- measured on the move, and it failed on the floor below
    rather than passing on an empty requirement, which is the direction that matters.

    **What this catches that a list cannot.** Measured on this derivation, not asserted: threading
    `consented_jurisdictions` through `tool_input` -- one plausible future edit, touching no test --
    moves `act:jurisdiction_not_consented` into the required set and fails here on the commit that
    makes it. A list only ever holds what the last review noticed.

    **Why a `Draft` is excluded, stated rather than assumed.** `body`, `jurisdiction` and `tool_name`
    also arrive in `tool_input`, and `act:jurisdiction_not_consented` and `act:tool_outside_grant`
    are deliberately not required here. Both compare the action's self-description against
    `CONSENTED` and `GRANTED`, which the guard holds as constants precisely so the caller cannot
    supply them: the evidence half is the guard's. A draft is the thing being judged, and a guard
    that refused to read the caller's account of the action would have nothing to judge.
    **The residual that leaves:** a caller can still misdescribe its own action, and `jurisdiction`
    stops being self-description the moment a real send tool derives it from the recipient rather
    than taking it as an argument -- at which point the class joins exactly the family derived here,
    and nothing in this test would notice. That is a property of the payload shape, not of the
    predicate.
    """
    guard = ast.parse(GUARD.read_text(encoding="utf-8"))
    main = next(n for n in ast.walk(guard) if isinstance(n, ast.FunctionDef) and n.name == "main")
    adapter = ast.parse(inspect.getsource(ADAPTER))
    builder = next(n for n in ast.walk(adapter) if isinstance(n, ast.FunctionDef)
                   and n.name == build_act_inputs.__name__)

    constructed, opaque = _evidence_built_in(builder, _payload_tainted_locals(builder), ADAPTER)
    assert not opaque, (
        f"an evidence object is built positionally or by `**` expansion ({opaque}); its provenance "
        "cannot be read off the call, and this derivation would report it as guard-supplied"
    )
    built = _unpacked_into(builder, main, constructed)
    assert built, (
        f"`main` does not unpack {build_act_inputs.__name__}'s return into locals this walk can "
        f"follow (the adapter builds {sorted(constructed)}), so no evidence object reaches a "
        "predicate here and the derivation would require nothing to be declared"
    )
    bound = _evidence_parameters(main, built)
    reached = {predicate.__name__ for predicate in bound}
    assert _EVIDENCE_PREDICATE_FLOOR <= reached, (
        f"the derivation stopped binding evidence to {sorted(_EVIDENCE_PREDICATE_FLOOR - reached)}, "
        "so the classes those predicates produce no longer have to declare themselves"
    )

    derived: set = set()
    required: set = set()
    for predicate, parameters in bound.items():
        classes = _classes_by_evidence(predicate, parameters)
        assert any(classes.values()), (
            f"{predicate.__name__} was handed evidence and no class it produces reads any of it, "
            "which is the shape of a walk that has stopped seeing the branches"
        )
        derived |= set(classes)
        required |= {
            klass for klass, roots in classes.items()
            if any(_from_the_payload(root, parameters) for root in roots)
        }

    act_classes = {c for c in ViolationClass if c.family is Family.ACT}
    assert act_classes <= derived, (
        f"the derivation lost sight of {sorted(c.value for c in act_classes - derived)}"
    )
    assert required, "no class was derived as payload-decided, so this test would assert nothing"
    assert derived - required, (
        "every derived class came out payload-decided, so the criterion is not discriminating and "
        "would demand the whole catalog be declared undecidable"
    )
    undeclared = sorted(c.value for c in required - policy_hook.UNENFORCEABLE_HERE)
    assert not undeclared, (
        f"{undeclared} turn on evidence policy/payload.py fills from `tool_input` and "
        "tools/policy_hook.py hands to a predicate, so the caller being judged supplies what it is "
        "judged against, and they are not in `UNENFORCEABLE_HERE`"
    )


def test_the_shared_artefact_is_the_predicate_set_and_not_the_configuration():
    """The stated limit of 6.3's demonstration, exhibited rather than described.

    The out-of-process layer holds its own `CONSENTED` and `GRANTED`, so the same draft can be
    allowed by one layer and denied by the other whenever a deployment configures them apart. The
    hook consents to UK; the in-process context in this module consents to US only. Hardcoding is
    the fail-closed choice for a guard -- a context taken from the payload would let the caller
    hand the guard its own permissions -- so this is a limit to state, not a defect to close.
    """
    assert "UK" in policy_hook.CONSENTED and "UK" not in CONTEXT.consented_jurisdictions
    row = {**_DEFAULTS, "jurisdiction": "UK"}
    assert _run_hook({"tool_input": row}).returncode == 0
    draft = Draft(thread=(Message(role="investor", body="?"),), body=row["body"], cited_fields=(),
                  recipient_jurisdiction="UK", recipient_domain="example.test", tool_name="send_message")
    assert decide(draft, RECORD, CONTEXT, CLEAN).allowed is False


# ---------------------------------------------------------------------------
# The in-process layers: what the gate decided about, and what the executor then ran.
# ---------------------------------------------------------------------------


def _mismatch_context() -> ActContext:
    return ActContext(approval_token="tok", tier=2, consented_jurisdictions=frozenset({"US"}),
                      granted_tools=frozenset({"send_message", "draft_message"}), sent_count=0, send_cap=50)


def _drafting(body: str) -> Draft:
    return Draft(thread=(Message(role="investor", body="?"),), body=body, cited_fields=(),
                 recipient_jurisdiction="US", recipient_domain="example.test", tool_name="draft_message")


def test_a_call_naming_a_tool_the_reviewed_draft_does_not_is_denied(tmp_path: Path):
    """The gate must decide about the call it is guarding, not about a neighbouring one.

    `decide` reads `draft.tool_name` and `execute` runs `registry[tool_name]`, and nothing held
    the two equal. So a draft naming the granted `draft_message` was reviewed, passed, and the
    executor then ran `send_message` -- both tools inside the grant, so no act-class fired, and
    the message left. Design spec 4.2's least-privilege layer cannot help here: the point of the
    chokepoint is the case where the capability *is* held.

    Both halves are asserted. The matched call still sends, so the denial is caused by the
    mismatch and not by something that refuses this draft anyway.
    """
    sent = []
    registry = TrackingRegistry({"send_message": lambda **kw: sent.append("send_message") or "sent",
                                 "draft_message": lambda **kw: sent.append("draft_message") or "drafted"})
    mismatched = guarded_call(_gateway(tmp_path), "send_message", {}, _drafting("The round is $10M."),
                              RECORD, _mismatch_context(), CLEAN, registry)
    assert mismatched.allowed is False
    assert registry.lookups == []
    assert sent == []

    matched = guarded_call(_gateway(tmp_path / "b"), "draft_message", {}, _drafting("The round is $10M."),
                           RECORD, _mismatch_context(), CLEAN, registry)
    assert matched.allowed is True
    assert sent == ["draft_message"]


def test_the_framework_hook_denies_the_same_mismatch():
    """Two layers, one predicate. A check that lived in only one of them would be the drift."""
    outcome = pre_tool_use("send_message", {}, (_drafting("The round is $10M."), RECORD,
                                                _mismatch_context(), CLEAN))
    assert outcome.allow is False
    assert outcome.payload["category"] == "act:tool_outside_grant"
    assert outcome.payload["is_retryable"] is False
    allowed = pre_tool_use("draft_message", {}, (_drafting("The round is $10M."), RECORD,
                                                 _mismatch_context(), CLEAN))
    assert allowed.allow is True
    assert allowed.payload is None


def test_a_denial_carries_its_category_through_to_the_agent_facing_result(tmp_path: Path):
    """`test_deny_is_never_relabelled_as_retryable` asserts a hardcoded `False` and little else.

    `denial_result`'s `is_retryable` is a literal, so that assertion holds whatever `guarded_call`
    decided -- it would pass on an allow, were an allow able to reach it. What makes the denial
    real is the category and the disposition travelling with it, so those are pinned here against
    the tripwire that actually fired.
    """
    result = guarded_call(_gateway(tmp_path), "send_message", {}, _draft("Returns are guaranteed."),
                          RECORD, CONTEXT, CLEAN, TrackingRegistry({"send_message": lambda **kw: "sent"}))
    payload = denial_result(result.decision)
    assert payload["category"] == "content:forward_looking_return"
    assert payload["disposition"] == "redirect_refinable"
    assert payload["span"] == "guaranteed"
    assert payload["is_error"] is True


def test_an_unknown_tool_never_reaches_the_registry_as_a_key_error(tmp_path: Path):
    """A tool the draft does not name is refused before the lookup, so no `KeyError` is possible.

    Stated because the shape it replaces is design spec 3.4's: `registry[tool_name]` raising out
    of `gateway.call` is a raise at the executor boundary, and a defensive executor relabels one
    of those "transient -- please retry".
    """
    registry = TrackingRegistry({"send_message": lambda **kw: "sent"})
    result = guarded_call(_gateway(tmp_path), "wire_funds", {}, _draft("The round is $10M."),
                          RECORD, CONTEXT, CLEAN, registry)
    assert result.allowed is False
    assert registry.lookups == []


def test_arguments_carrying_text_the_gate_never_read_are_denied(tmp_path: Path):
    """The other half of the binding, and the same class of defect as the tool mismatch.

    `decide` reads the `Draft`; `execute` passes `args` to the tool. With only the tool identity
    held, a draft the gate approves could ship arguments carrying entirely different prose -- here
    a guarantee of returns, riding on an approved draft about a round size. Design spec 4.1's
    ordering guarantee says the gate runs before the tool is looked up; that is worth nothing if
    the object reviewed is not the object sent.

    Returned, never raised, per 3.4: the denial arrives as a `GatewayResult`, and nothing
    transmits.
    """
    delivered = {}
    registry = TrackingRegistry({"send_message": lambda **kw: delivered.update(kw) or "sent"})
    result = guarded_call(_gateway(tmp_path), "send_message", {"body": "Returns are guaranteed."},
                          _draft("The round is $10M."), RECORD, CONTEXT, CLEAN, registry)
    assert result.allowed is False
    assert delivered == {}
    assert registry.lookups == []
    assert denial_result(result.decision)["category"] == "other"
    assert denial_result(result.decision)["disposition"] == "redirect_futile"


def test_arguments_drawn_from_the_reviewed_draft_are_delivered(tmp_path: Path):
    """The companion that keeps the rule above from being 'deny everything with arguments'.

    Without this, the denial test passes against a `guarded_call` that refuses any non-empty
    `args` at all, which would close the hole by making the chokepoint useless.
    """
    draft = _draft("The round is $10M.")
    delivered = {}
    registry = TrackingRegistry({"send_message": lambda **kw: delivered.update(kw) or "sent"})
    args = {"body": draft.body, "to": draft.recipient_domain, "draft": None, "urgent": False}
    result = guarded_call(_gateway(tmp_path), "send_message", args, draft, RECORD, CONTEXT,
                          CLEAN, registry)
    assert result.allowed is True
    assert delivered == args
    assert registry.lookups == ["send_message"]


def test_a_reviewed_body_is_not_taken_apart_into_characters(tmp_path: Path):
    """`str` is a sequence, so a container branch reached before the string branch would iterate it.

    Each character would then be an unreviewed one-character string and every call would deny --
    a fail-closed bug, but a bug. The body here is long enough that the failure is unmistakable.
    """
    draft = _draft("The round is $10M. We have shared the requested details with you already.")
    registry = TrackingRegistry({"send_message": lambda **kw: "sent"})
    result = guarded_call(_gateway(tmp_path), "send_message", {"body": draft.body}, draft,
                          RECORD, CONTEXT, CLEAN, registry)
    assert result.allowed is True


def test_an_unreviewed_value_nested_inside_a_container_is_still_found(tmp_path: Path):
    """A flat comparison would miss it, and structured tool arguments are ordinary."""
    registry = TrackingRegistry({"send_message": lambda **kw: "sent"})
    args = {"parts": [{"kind": "text", "value": "Returns are guaranteed."}]}
    result = guarded_call(_gateway(tmp_path), "send_message", args, _draft("The round is $10M."),
                          RECORD, CONTEXT, CLEAN, registry)
    assert result.allowed is False
    assert registry.lookups == []


def test_a_figure_that_reached_no_predicate_as_an_argument_is_denied(tmp_path: Path):
    """Numbers are not exempt, and `act:figure_not_in_record` is why.

    `evaluate_act_classes` scans `draft.body` for figures and checks each against the record. A
    figure that travels as an argument instead was scanned by nothing at all, so exempting
    non-strings would reopen the class one layer over.
    """
    registry = TrackingRegistry({"send_message": lambda **kw: "sent"})
    result = guarded_call(_gateway(tmp_path), "send_message", {"amount": 5_000_000},
                          _draft("The round is $10M."), RECORD, CONTEXT, CLEAN, registry)
    assert result.allowed is False
    assert registry.lookups == []


def test_the_framework_hook_denies_the_same_unreviewed_argument():
    """One binding, both in-process layers -- a check living in only one of them is the drift."""
    outcome = pre_tool_use("send_message", {"body": "Returns are guaranteed."},
                           (_draft("The round is $10M."), RECORD, CONTEXT, CLEAN))
    assert outcome.allow is False
    assert outcome.payload["category"] == "other"
    allowed = pre_tool_use("send_message", {"body": "The round is $10M."},
                           (_draft("The round is $10M."), RECORD, CONTEXT, CLEAN))
    assert allowed.allow is True


def test_the_gate_runs_before_the_registry_is_indexed_even_when_the_tool_is_absent(tmp_path: Path):
    """Design spec 4.1 at its sharpest: a deny returns before the registry is indexed.

    The registry below does not contain the tool at all, so a lookup would raise rather than
    merely be observed. The call still returns a denial, which is only possible if the lookup
    never happened -- the ordering, asserted by an effect that could not survive the wrong order.
    """
    registry = TrackingRegistry({})
    result = guarded_call(_gateway(tmp_path), "send_message", {}, _draft("Returns are guaranteed."),
                          RECORD, CONTEXT, CLEAN, registry)
    assert result.allowed is False
    assert registry.lookups == []


def test_the_registry_lookup_really_would_raise_if_the_order_were_wrong(tmp_path: Path):
    """The companion that makes the test above non-vacuous: an allowed call on an empty registry.

    Without this, `assert result.allowed is False` above could hold for a `guarded_call` that
    never touches the registry under any circumstances. Here the gate allows, the lookup happens,
    and it raises -- so the absence of that raise on the denied path is evidence about ordering.
    """
    registry = TrackingRegistry({})
    with pytest.raises(KeyError):
        guarded_call(_gateway(tmp_path), "send_message", {}, _draft("The round is $10M."),
                     RECORD, CONTEXT, CLEAN, registry)
    assert registry.lookups == ["send_message"]


# ---------------------------------------------------------------------------
# The payload adapter both enforcement layers build from, exercised directly.
#
# `policy/payload.py` turns one payload dict into the `(Draft, Record, ActContext)` triple. Every
# test above reaches it through a subprocess, so each trap is asserted only as a hook exit code;
# these reach it as the pure function it is, so a trap that stopped holding would fail here as
# well as one layer up.
# ---------------------------------------------------------------------------


def test_the_payload_level_tool_name_wins():
    draft, _, _ = build_act_inputs(
        {"tool_name": "outer", "tool_input": {"tool_name": "inner", "body": "x"}})
    assert draft.tool_name == "outer"


def test_no_approval_token_is_invented():
    _, _, ctx = build_act_inputs({"tool_input": {"body": "x"}})
    assert ctx.approval_token is None


def test_tier_defaults_closed_to_two():
    _, _, ctx = build_act_inputs({"tool_input": {"body": "x"}})
    assert ctx.tier == 2


def test_an_unconsumed_key_reaches_nothing_the_adapter_builds():
    """The trap the adapter does *not* hold, asserted so no caller can assume it does.

    An unconsumed key **is** refused out of process, by `unsendable_finding` over the keys outside
    `CONSUMED_KEYS` -- `test_the_two_enforcement_layers_reach_the_same_verdict_and_the_same_category`
    exhibits six such payloads. That check lives in `policy_hook.main` and not in the builder,
    because a refusal needs somewhere to go and `policy/` may not write to stderr. So the extra key
    reaches no field of the triple, and the effect asserted is that blindness: an identical triple
    from two different payloads, which no raise-shaped assertion would have described.

    Written as `pytest.raises(ValueError)` this passes only against a builder that refuses, and the
    cheapest way to make it pass would have been to add the refusal -- moving a guard into `policy/`
    that cannot report there, and giving a second surface a reason to believe it is covered. The
    obligation is the caller's, and `CONSUMED_KEYS` is exported so the caller cannot hold a second
    copy of the list.
    """
    assert build_act_inputs({"tool_input": {"body": "x", "surprise_key": "y"}}) \
        == build_act_inputs({"tool_input": {"body": "x"}})


def test_an_absent_body_is_not_read_as_an_empty_one():
    """Absent is not empty, and out of process the difference is a message scored as blank.

    The builder subscripts `tool_input["body"]`, so a payload with no body raises here rather than
    producing a `Draft` every content-class then finds clean. What makes the subscript safe in the
    hook is `policy_hook.main`'s own check one statement earlier -- `if "body" not in tool_input`,
    which blocks with a stated reason before the builder is called, and which
    `test_a_body_the_hook_cannot_find_blocks_rather_than_evaluating_an_empty_one` holds. A caller
    that skips it gets the raise, which is why the raise is asserted rather than left implicit.

    Both halves, because the raise alone would also be satisfied by a builder that refuses every
    body: an explicit `""` is a caller stating its draft is empty, and it still builds.
    """
    draft, _, _ = build_act_inputs({"tool_input": {"body": ""}})
    assert draft.body == ""
    with pytest.raises(KeyError):
        build_act_inputs({"tool_input": {}})
