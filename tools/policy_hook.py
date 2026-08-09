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

- `CONSENTED` and `GRANTED` are constants of `policy/payload.py`, re-exported here, rather than
  values taken from the payload. A guard that accepts its own permissions from the caller is not a
  guard, so this is the fail-closed choice -- but it means a deployment can configure the two layers
  apart, and the shared artefact across layers is the predicate set, never the configuration. A test
  exhibits a jurisdiction the two disagree on.
- **This layer enforces a strict subset of the in-process policy, and the missing predicates are
  named.** `UNENFORCEABLE_HERE` is the list, a test exhibits the payload the two layers disagree on
  for each entry, and the predicate-parity test asserts the declaration rather than letting silence
  imply parity. **How many are on it is a question for the set and not for this sentence**, which is
  why the count that used to stand here is gone: it said two while three were true. The paragraph
  below already disowns exactly this move, and it had been made twice before. `act:send_cap_exceeded`
  needs a count that lives in a log this process cannot read. `act:no_approval_token` and
  `act:figure_not_in_record` are decided from evidence arriving in the payload from the caller being
  judged: an approval and a tier, and the record a draft's figures are checked against. See the note
  on the set itself, which sits beside it in `policy/payload.py`.

  Same category as the model call not porting: the deterministic half is the portable half, and the
  stateful half is not. **So "the same policy is enforced at two layers" is true of the predicates
  and false of the coverage.** The honest claim is that every predicate this layer runs reaches the
  in-process verdict, and that the classes it cannot decide are the ones `UNENFORCEABLE_HERE`
  names -- read from the set, never counted from memory. Two earlier drafts of this sentence
  counted instead: one said "one predicate does not run here at all" while a closable gap was open,
  and the version after it said "exactly one class" while the approval class was being decided from
  caller input. Both were recalled rather than measured, and both were wrong.
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

from chaperone.policy.act_classes import evaluate_act_classes
from chaperone.policy.arguments import unsendable_finding
from chaperone.policy.citations import validate_citations
# Re-exported, not merely used. `CONSENTED`, `GRANTED`, `CONSUMED_KEYS` and `UNENFORCEABLE_HERE`
# were constants of this module before the construction they belong to moved to `policy/payload.py`,
# and this module's public surface is read by tests and by the catalog guard in
# `tests/test_readme_claims.py`. Imported here so the move is an internal one.
from chaperone.policy.payload import (
    CONSENTED,
    CONSUMED_KEYS,
    GRANTED,
    UNENFORCEABLE_HERE,
    build_act_inputs,
)
from chaperone.policy.tripwires import evaluate_tripwires


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

    # Absent is not empty. Every predicate here reads `draft.body`, so a message stored under some
    # other key was scored as a blank draft and found clean. An explicit "" is a caller saying the
    # body is empty, which is a fact about the draft; a missing key is this guard failing to find
    # the message, which is a fact about the guard.
    #
    # It runs *before* the builder and stays here rather than moving with it, because `_block` is
    # what makes it a refusal and `_block` writes to stderr -- which `policy/` may not do. The
    # builder subscripts `tool_input["body"]` and would raise instead; a raise out of `main` is a
    # 2 by the handler at the foot of this file, so the guard is what turns it into a stated reason.
    if "body" not in tool_input:
        return _block("the payload carries no 'body', so there is no message to evaluate")

    # The trust boundary itself lives in `policy/payload.py` so a second decision surface reads a
    # payload the same way this one does rather than keeping a second copy of it -- the argument
    # `arguments.py` makes for `unsendable_in`, applied to the construction instead of a predicate.
    # Both the guard above and the unconsumed-key check below stay here: each needs somewhere to
    # send a refusal, which a pure constructor does not have.
    draft, record, context = build_act_inputs(payload)

    # The same three pure predicates `decide` consults, in `decide`'s order, so the primary finding
    # is the same one both layers report. Shipping without `validate_citations` made a fabricated
    # citation deny in process and allow out of process -- two layers enforcing two policies, which
    # is the opposite of what design spec 6.3 claims. A test derives this list from `decide`'s own
    # source rather than restating it, so the next omission fails rather than passing quietly --
    # bounded to predicates `decide` calls by bare name, since that is what the AST walk can see.
    # Same order as `_decide_for`, so `findings[0]` is the same finding both layers report. The
    # unconsumed-key check runs first there too, and uses this identical `unsendable_in` -- the
    # predicate is pure and lives in `policy/` precisely so the two layers cannot hold two rules.
    unbound = unsendable_finding({k: v for k, v in tool_input.items() if k not in CONSUMED_KEYS}, draft)
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
