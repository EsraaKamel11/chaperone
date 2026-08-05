import ast
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
from chaperone.policy.act_classes import ActContext
from chaperone.policy.types import Draft, Message, Record, ViolationClass
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
    Layer 2 is the in-process framework hook. Layer 3 is the executor chokepoint. One predicate set,
    three enforcement points, same verdict.
    """
    import json, subprocess, sys
    from pathlib import Path as P

    from chaperone.gates.hook import pre_tool_use

    draft = _draft("Returns are guaranteed.")

    # Layer 1: a real out-of-process hook script.
    guard = P(__file__).resolve().parents[2] / "tools" / "policy_hook.py"
    # `approval_token` is supplied so all three layers refuse for the tripwire and not for a
    # missing token: without it the act-class fires first and this passes on an unrelated denial,
    # which is what "the same policy" would then be resting on.
    completed = subprocess.run(
        [sys.executable, str(guard)],
        input=json.dumps({"tool_input": {"body": draft.body, "jurisdiction": "US",
                                         "tool_name": "send_message", "cited_fields": [],
                                         "approval_token": "tok"}}),
        capture_output=True, text=True,
    )

    # Layer 2: the in-process framework hook.
    hook_outcome = pre_tool_use("send_message", {}, (draft, RECORD, CONTEXT, CLEAN))

    # Layer 3: the executor chokepoint.
    executor_result = guarded_call(_gateway(tmp_path), "send_message", {}, draft, RECORD, CONTEXT,
                                   CLEAN, TrackingRegistry({"send_message": lambda **kw: "sent"}))

    assert completed.returncode == 2
    assert hook_outcome.allow is False
    assert executor_result.allowed is False
    # One draft, one reason, three layers -- not three refusals that happen to coincide.
    assert "content:forward_looking_return" in completed.stderr
    assert hook_outcome.payload["category"] == "content:forward_looking_return"
    assert executor_result.decision.findings[0].violation_class.value == "content:forward_looking_return"


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


def test_the_hook_runs_every_pure_predicate_the_engine_consults():
    """The drift detector: derived from `decide`'s own source, so it catches the *next* omission.

    The out-of-process layer shipped with `evaluate_act_classes + evaluate_tripwires` and no
    `validate_citations`, so a fabricated citation denied in process and allowed out of process --
    two layers, two policies, which is the opposite of what design spec 6.3 claims. Naming the
    three predicates in a literal list here would pin today's omission and miss tomorrow's, so the
    set is read out of `decide`: every function it calls that lives in `policy/` is pure by the
    static audit's own guarantee, therefore ports, therefore must appear.
    """
    from chaperone.gates import engine

    called = {
        node.func.id
        for node in ast.walk(ast.parse(inspect.getsource(engine.decide)))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    pure = {
        name for name in called
        if inspect.isfunction(getattr(engine, name, None))
        and getattr(engine, name).__module__.startswith("chaperone.policy")
    }
    assert pure, "derived no pure predicates from `decide`, so this test would assert nothing"

    # Called, not merely mentioned. A substring search over the source was the first version and it
    # was satisfiable by prose: the comment beside the call in policy_hook.py names
    # `validate_citations`, so deleting the call and keeping the comment passed. `ast.Call` with an
    # `ast.Name` func produces no node for a docstring or a comment, which is the same idiom
    # test_engine.py's `_functions_reading` already uses for exactly this reason.
    invoked = {
        node.func.id
        for node in ast.walk(ast.parse(GUARD.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert sorted(pure - invoked) == []

    # Function-level parity is not class-level parity, and the difference has to be declared rather
    # than inferred from silence. `evaluate_act_classes` runs here, but one of the classes it can
    # produce needs a count this process does not hold, so the out-of-process layer enforces a
    # strict subset. The exemption is named, and a test below exhibits it.
    assert policy_hook.UNENFORCEABLE_HERE == frozenset({ViolationClass.SEND_CAP_EXCEEDED})


# One logical draft per row, rendered twice: as JSON for the subprocess, and as objects for
# `decide`. Both allows and denies, and the assertion below refuses a table that loses either.
_DEFAULTS = {"body": "We have shared the requested details.", "jurisdiction": "US",
             "tool_name": "send_message", "cited_fields": [], "record": {"round_size": "10000000"},
             "approval_token": "tok", "tier": 2, "sent_count": 0, "send_cap": 50}

_CORPUS = [
    ("a compliant draft", {}),
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

_BLOCKED = re.compile(r"^blocked: (\S+) \(")


def _in_process(row: dict):
    draft = Draft(thread=(Message(role="investor", body=""),), body=row["body"],
                  cited_fields=tuple(row["cited_fields"]), recipient_jurisdiction=row["jurisdiction"],
                  recipient_domain="example.test", tool_name=row["tool_name"])
    context = ActContext(approval_token=row["approval_token"], tier=row["tier"],
                         consented_jurisdictions=policy_hook.CONSENTED,
                         granted_tools=policy_hook.GRANTED,
                         sent_count=row["sent_count"], send_cap=row["send_cap"])
    return decide(draft, Record(fields=row["record"]), context, CLEAN)


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
        row = {**_DEFAULTS, **overrides}
        completed = _run_hook({"tool_input": row})
        decision = _in_process(row)
        assert completed.returncode in (0, 2), f"{label}: hook exited {completed.returncode}"
        assert (completed.returncode == 2) is (not decision.allowed), (
            f"{label}: out-of-process blocked={completed.returncode == 2}, "
            f"in-process denied={not decision.allowed} ({completed.stderr.strip()})"
        )
        if not decision.allowed:
            match = _BLOCKED.match(completed.stderr)
            assert match, f"{label}: no parseable reason on stderr: {completed.stderr!r}"
            assert match.group(1) == decision.findings[0].violation_class.value, label
        verdicts[label] = decision.allowed
    assert set(verdicts.values()) == {True, False}, f"the corpus lost a whole outcome: {verdicts}"


def test_the_out_of_process_layer_enforces_a_strict_subset_and_names_the_gap():
    """§6.3's real bound: predicate parity does not give coverage parity.

    Design spec 3.2 makes the cap predicate pure over `(draft, count)` with the **gateway**
    supplying the count from the audit log. A stateless guard has no log, so a payload that omits
    the count is allowed by a layer that runs `evaluate_act_classes` in full -- the predicate is
    called, the class it would produce is unreachable. Exhibited on the exact draft, so the
    subset relation is a measurement rather than a sentence in a docstring.

    The corpus test passes `sent_count` and `send_cap` in every row precisely because of this: it
    supplies the state so the comparison isolates the predicates.
    """
    assert policy_hook.UNENFORCEABLE_HERE == frozenset({ViolationClass.SEND_CAP_EXCEEDED})
    row = {**_DEFAULTS, "sent_count": 50, "send_cap": 50}
    stateless = {k: v for k, v in row.items() if k not in ("sent_count", "send_cap")}

    assert _run_hook({"tool_input": stateless}).returncode == 0
    decision = _in_process(row)
    assert decision.allowed is False
    assert [f.violation_class for f in decision.findings] == [ViolationClass.SEND_CAP_EXCEEDED]

    # And with the count supplied, the layers agree again -- so the gap is the missing state, not a
    # missing predicate. Without this the test would equally suit a hook that never checks the cap.
    assert _run_hook({"tool_input": row}).returncode == 2


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
