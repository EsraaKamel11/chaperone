"""Out-of-process enforcement of the deterministic layer. Exit 2 blocks.

Only the pure half ports here. Act-classes, citation validation and tripwires are functions of
their arguments, so they run in a shell hook, an SDK loop, or an executor without modification.
The content check needs a model call and cannot be a stdin/stdout guard, which is the honest limit
of this demonstration.

**Exit 2 blocks; exit 1 does not.** A PreToolUse hook that exits 1 reports an error the runtime
steps over, so every way of leaving this script other than a deliberate 2 is an allow. That makes
the ordinary Python failure modes -- an unreadable payload, a field that will not convert, a module
that will not import -- into fail-open paths, and each is closed below rather than left to chance:

- **The payload.** Anything that is not a readable JSON object blocks. `guard_edit.py` returns 0 on
  the same condition, and the two are not inconsistent: it cannot tell which file is being edited,
  while this cannot evaluate the policy at all. engine.py states the doctrine for the project --
  every way of not getting a usable answer ends in a denial.
- **Any raise.** `main` is wrapped at the entry point and every escape becomes a 2. Enumerating the
  exception types was tried first and missed the one that mattered, exactly as the gateway records:
  fail-closed by construction beats enumerating exception types.
- **The import.** `sys.path[0]` is this file's own directory, so nothing puts `src/` on the path of
  a plain `python tools/policy_hook.py` -- `pythonpath` in pyproject.toml is pytest-only. The
  bootstrap below is what makes the guard work in a checkout with no install, and its absence was
  invisible here only because the working copy carries an editable one.

**Two limits, both stated because they are otherwise invisible.**

- `CONSENTED` and `GRANTED` are held here rather than taken from the payload. A guard that accepts
  its own permissions from the caller is not a guard, so this is the fail-closed choice -- but it
  means a deployment can configure the two layers apart, and the shared artefact across layers is
  the predicate set, never the configuration. A test exhibits a jurisdiction the two disagree on.
- **This layer enforces a strict subset of the in-process policy, and the missing predicate is
  named.** `act:send_cap_exceeded` is the one act-class it cannot decide. Design spec 3.2 makes the
  cap predicate pure over `(draft, count)` with the **gateway** supplying the count from the audit
  log; this process holds no log and no state, so `sent_count` and `send_cap` can only arrive in
  the payload or fall back to a permissive pair, and a caller that omits them gets no cap check at
  all. `UNENFORCEABLE_HERE` declares it, a test exhibits the draft the two layers disagree on, and
  the predicate-parity test asserts the declaration rather than letting silence imply parity.

  Same category as the model call not porting: the deterministic half is the portable half, and the
  stateful half is not. **So "the same policy is enforced at two layers" is true of the predicates
  and false of the coverage.** The honest claim is that every predicate this layer runs reaches the
  in-process verdict, and that exactly one class -- counted by reading `UNENFORCEABLE_HERE`, not
  from memory -- is undecidable here. An earlier draft of this sentence said "one predicate does
  not run here at all" while a second, closable gap was open; the count was recalled rather than
  measured, and it was wrong.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Before the first `chaperone` import, and the reason is a fail-open this guard cannot survive:
# `sys.path[0]` is `tools/`, an import error raises before `main` is entered, and the traceback
# exits 1, which does not block. Prepended rather than appended so a checkout governs its own
# guard even where a stale copy is installed elsewhere.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from chaperone.policy.act_classes import ActContext, evaluate_act_classes
from chaperone.policy.arguments import unsendable_in
from chaperone.policy.citations import validate_citations
from chaperone.policy.tripwires import evaluate_tripwires
from chaperone.policy.types import Draft, Finding, Message, Record, ViolationClass

CONSENTED = frozenset({"US", "UK"})
GRANTED = frozenset({"send_message", "send_reply", "draft_message", "read_policy"})

#: The violation classes this layer cannot decide, so the subset relation is declared rather than
#: discovered. `evaluate_act_classes` runs here and can produce `act:send_cap_exceeded`, but only
#: from a count that lives in the audit log this process cannot read -- so a caller who omits the
#: count gets an allow where the in-process gate denies. Declared as a set rather than prose so the
#: predicate-parity test can assert it, and so widening it is an edit somebody has to make on
#: purpose.
#:
#: **This set holds only what state makes impossible.** It briefly also covered unconsumed payload
#: keys, which was wrong twice over: refusing them is a pure function of the payload and needs no
#: state at all, and a subset declaration that lists a *closable* gap reads as exhaustive while
#: being incomplete -- worse than no declaration. That gap is closed below, not declared here.
UNENFORCEABLE_HERE = frozenset({ViolationClass.SEND_CAP_EXCEEDED})

#: Every key of `tool_input` this module reads. Anything else is an argument no predicate here
#: consumes, and it is checked as outbound content rather than ignored: nine consumed keys and a
#: silent pass on the rest meant `{"extra_text": "Returns are guaranteed."}` rode through on an
#: otherwise-compliant payload while the in-process gate denied it -- design spec 6.3 false again,
#: one layer over from where it was first false.
#:
#: A test derives this set from this module's own AST rather than trusting the literal, so a key
#: read here and forgotten here cannot exist.
CONSUMED_KEYS = frozenset({
    "body", "cited_fields", "jurisdiction", "domain", "tool_name", "record",
    "approval_token", "tier", "sent_count", "send_cap",
})


def _say(line: str) -> None:
    """Write the reason, or write nothing. The exit code is what blocks; the text only explains.

    With stderr unwritable -- a hook wired behind a redirect that failed, say -- the interpreter's
    shutdown flush reports the failure and the process exits 120 -- measured on Windows
    under CPython 3.11.15 and 3.13.9, and **unverified on Linux**, where CI runs. That is neither 0 nor 2, and only
    2 blocks, so a guard that lost its voice also lost its verdict.

    **Swallowing the exception is not enough, measured.** The failed write stays in the stream's
    buffer and shutdown retries it, so the 120 survives an `except: pass` -- which is why the
    handler replaces the stream rather than merely ignoring the error. Rebinding gives shutdown a
    sink with nothing owed, and the exit code is then the one `main` chose.
    """
    try:
        print(line, file=sys.stderr)
        sys.stderr.flush()
    except Exception:
        try:
            sys.stderr = open(os.devnull, "w")
        except Exception:
            pass


def _block(reason: str) -> int:
    _say(f"blocked: {reason}")
    return 2


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        return _block(f"the payload could not be read as JSON: {exc}")

    if not isinstance(payload, dict):
        return _block(f"the payload is a {type(payload).__name__}, not a JSON object")

    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return _block(f"tool_input is a {type(tool_input).__name__}, not a JSON object")

    # A tool's own name is not one of its arguments: the runtime sends it beside `tool_input`,
    # which is why `guard_edit.py` reads `tool_input.file_path` and no tool name at all. So the
    # payload level is read *first* and the argument level is only a fallback. Preferring the
    # argument level is the same confused deputy `_decide_for` refuses one layer up: a payload
    # naming `wire_funds` beside an argument naming `send_message` would have had its grant checked
    # against the tool that is not being run.
    #
    # Falls back to "" rather than None: `evaluate_act_classes` skips the grant check entirely on
    # None, so a payload naming no tool would otherwise be the one payload never checked at all.
    tool_name = payload.get("tool_name") or tool_input.get("tool_name") or ""

    # Absent is not empty. Every predicate here reads `draft.body`, so a message stored under some
    # other key was scored as a blank draft and found clean. An explicit "" is a caller saying the
    # body is empty, which is a fact about the draft; a missing key is this guard failing to find
    # the message, which is a fact about the guard.
    if "body" not in tool_input:
        return _block("the payload carries no 'body', so there is no message to evaluate")

    draft = Draft(
        thread=(Message(role="investor", body=""),),
        body=tool_input["body"],
        cited_fields=tuple(tool_input.get("cited_fields", ())),
        recipient_jurisdiction=tool_input.get("jurisdiction", ""),
        recipient_domain=tool_input.get("domain", "example.test"),
        tool_name=tool_name,
    )
    record = Record(fields=tool_input.get("record", {}))
    context = ActContext(
        # No default token. The old default was the very string the act-class looks for, so a
        # payload carrying no token satisfied the tier-2 rule. A guard may not invent the evidence
        # it is guarding, and tier defaults high for the same reason.
        approval_token=tool_input.get("approval_token"),
        tier=int(tool_input.get("tier", 2)),
        consented_jurisdictions=CONSENTED,
        granted_tools=GRANTED,
        sent_count=int(tool_input.get("sent_count", 0)),
        send_cap=int(tool_input.get("send_cap", 10_000)),
    )

    # The same three pure predicates `decide` consults, in `decide`'s order, so the primary finding
    # is the same one both layers report. Shipping without `validate_citations` made a fabricated
    # citation deny in process and allow out of process -- two layers enforcing two policies, which
    # is the opposite of what design spec 6.3 claims. A test derives this list from `decide`'s own
    # source rather than restating it, so the next omission fails rather than passing quietly --
    # bounded to predicates `decide` calls by bare name, since that is what the AST walk can see.
    # Same order as `_decide_for`, so `findings[0]` is the same finding both layers report. The
    # unconsumed-key check runs first there too, and uses this identical `unsendable_in` -- the
    # predicate is pure and lives in `policy/` precisely so the two layers cannot hold two rules.
    unsendable = unsendable_in({k: v for k, v in tool_input.items() if k not in CONSUMED_KEYS}, draft)
    unbound = () if not unsendable else (Finding(
        ViolationClass.OTHER,
        f"the call carries {len(unsendable)} argument value(s) the gate did not judge as "
        f"outbound, beginning {unsendable[0]!r}",
        None,
    ),)
    findings = (
        unbound
        + evaluate_act_classes(draft, record, context)
        + validate_citations(draft, record)
        + evaluate_tripwires(draft)
    )
    if findings:
        primary = findings[0]
        _say(f"blocked: {primary.violation_class.value} ({primary.detail})")
        return 2
    return 0


if __name__ == "__main__":
    try:
        code = main()
    # A guard that raises is a guard that allowed, so the net is `BaseException` rather than
    # `Exception`: `KeyboardInterrupt`, `SystemExit` and `GeneratorExit` do not derive from
    # `Exception`, and a guard interrupted mid-decision has decided nothing. `sys.exit` sits
    # outside the `try`, so the code chosen here is the code returned.
    except BaseException as exc:
        code = _block(f"the guard could not complete: {type(exc).__name__}: {exc}")
    sys.exit(code)
