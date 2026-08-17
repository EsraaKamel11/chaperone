"""Mutation sweep. Design spec 10.1: flip the deny to allow, drop the `finally`, remove the fsync
-- **the tests must fail.** A surviving mutant means the test that should have caught it asserts a
proxy for the property rather than the property.

This tool is itself an enforcement predicate, so the five ways it has failed before are closed here
rather than left to whoever runs it.

**1. A kill is one exit code, not "anything but zero".** `returncode != 0` marks a mutant killed
when pytest collected nothing (5), errored during collection (2), hit an internal error (3), was
handed a bad flag (4), or never launched -- five ways of examining nothing and reporting the mutant
dead. Measured on pytest 8.4.2: 0 is green, 1 is *a test failed*, and only 1 is a kill. Every other
code raises `MutationHarnessError`, and so does a timeout.

**2. The anchor must be unique, not merely present.** `apply_mutant` replaces the first occurrence,
so a `find` string appearing twice mutates a site the mutant was not named for and the KILLED is
recorded for the wrong change. A stale anchor is the opposite failure: the source is left unmutated
and the sweep records a *survivor* that never existed. `audit_anchors` refuses both, and
`tests/test_mutations.py` runs it before the sweep -- where it doubles as a leftover-mutant detector,
since a mutant left applied by a crashed run shows up as an absent anchor.

**3. The file is written as bytes.** `write_text` applies the platform's newline translation, which
on Windows converts the file to CRLF against `.gitattributes`' `eol=lf` pin -- an incident disclosed
by a Task 4 implementer in its own mutation driver and repeated in Task 11. Everything here reads
and writes bytes, and `restore` re-reads and compares them rather than assuming the write landed.
The comparison is on bytes and not on a substring, because
`"return Decision(True, (), Disposition.ALLOW)"` -- one of the replacements below -- also occurs in
the *unmutated* `gates/engine.py`, so a search-based restoration check would be satisfied by a file
still carrying the mutant.

**4. A survivor is a finding only once the mutant is known to mutate.** Two mutants in an earlier
task "survived" as equivalent -- one added `ValueError` to a tuple whose members were already
`ValueError` subclasses -- so `apply_mutant` refuses a replacement that leaves the bytes unchanged.
That is the cheap half. The expensive half is semantic equivalence, which no check here can see: a
mutant whose behaviour is identical on every input the suite can express survives honestly and must
be rewritten to mutate, never weakened or deleted. `binary_append_becomes_text_append` below is the
live instance, and its comment says so.

**5. Restoration is durable, because the sweep does not always live to perform it.** The original
source was held in one in-memory string and put back in a `finally`, so any death that skips that
`finally` -- an interrupt, a harness stop, a timeout, a sleeping machine -- destroyed the only record
of it and left a mutant applied in `src/`, with no recovery available on any later run. Five times in
one session. `apply_mutant` now parks the source on disk before writing the mutation and
`recover_stranded_mutant` puts it back at the start of the next run, under the lock and *before*
`audit_anchors`, which would otherwise refuse the run before recovery could fire. The asymmetry this
closes is the lock's own precedent: `sweep_lock` keeps durable state (the owner's pid) exactly so a
later run can reason about a dead predecessor. Restoration kept none.

A sixth guard has no incident behind it and is here because the failure would be spectacular: the
sweep runs pytest as a subprocess, and `--ignore` is resolved against the invocation directory. Run
from anywhere but the root, the child would collect this sweep and each mutant would fan out into
another whole sweep. `run_suite` passes `cwd=ROOT` with an absolute `--ignore`, *and* sets
`CHILD_ENV_MARKER` so `tests/test_mutations.py` skips itself if the ignore ever stops matching.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "chaperone"
SWEEP_MODULE = ROOT / "tests" / "test_mutations.py"

#: Set in the child's environment so the sweep cannot recurse into itself even if `--ignore` breaks.
CHILD_ENV_MARKER = "CHAPERONE_MUTATION_CHILD"

# Verified empirically on the pinned pytest (8.4.2), not recalled: 0 green, 1 a test failed,
# 2 collection error, 3 internal error, 4 usage error / no such file, 5 nothing collected.
EXIT_GREEN = 0
EXIT_TESTS_FAILED = 1
_EXIT_MEANING = {
    2: "collection was interrupted",
    3: "pytest hit an internal error",
    4: "pytest was invoked wrongly, or the path does not exist",
    5: "pytest collected no test at all",
}


class MutationHarnessError(RuntimeError):
    """The sweep could not be run as written. Never a kill and never a survival."""


@dataclass(frozen=True)
class Mutant:
    name: str
    path: Path
    find: str
    replace: str


MUTANTS: list[Mutant] = [
    Mutant(
        "deny_becomes_allow",
        SRC / "gates" / "engine.py",
        "        return Decision(False, act_findings, disposition_for(act_findings))",
        "        return Decision(True, (), Disposition.ALLOW)",
    ),
    # Re-derived. The brief's anchor -- a literal `Disposition.REDIRECT_FUTILE` in the unavailable
    # branch -- was invalidated when `decide` was rewritten to take every disposition from
    # `disposition_for`, and a stale anchor records a survivor rather than raising. This form flips
    # the same fail-closed branch: the checker gave no usable answer and the draft goes out.
    #
    # **Re-derived twice more, in the commit that gave that branch an `outage`.** The anchor is the
    # branch's whole return line, so any edit to it makes this entry stale -- and a stale anchor is
    # refused rather than quietly recorded as a kill. It went stale first when the return gained
    # `outage=str(exc)`, and again when that became a named local so the empty-message fallback had
    # somewhere to live. Both times the audit was watched failing on the changed line before this
    # string was touched, which is the only thing that shows the anchor is read rather than assumed.
    Mutant(
        "checker_unavailable_fails_open",
        SRC / "gates" / "engine.py",
        "        return Decision(False, unavailable, disposition_for(unavailable), outage=outage)",
        "        return Decision(True, (), Disposition.ALLOW)",
    ),
    # The third of `decide`'s deny branches, and the last to get a registered anchor. Until this
    # entry the branch had guards but no standing one: `tests/testing/test_scripted.py` drove it by
    # hand, watched it red, restored the source, and wrote down the shape it wanted a mutant to
    # take -- the registered engine mutants' own `Decision(True, (), Disposition.ALLOW)`. This is
    # that note becoming the guard it described. Killed on registration by seven tests, the first
    # `tests/gates/test_engine.py::test_flag_for_review_fails_closed_rather_than_passing`. The
    # chokepoint test that specified the shape is among those seven rather than alone, which is
    # the sweep reporting a wider blast radius than the note queuing it predicted -- recorded here
    # because a killer named from reading and a killer named from a run are different claims.
    Mutant(
        "flag_for_review_fails_open",
        SRC / "gates" / "engine.py",
        "    if isinstance(result, FlagForReview):\n"
        "        flagged = (Finding(ViolationClass.OTHER, f\"flagged for review: {result.reason}\", None),) + tripwire_findings\n"
        "        return Decision(False, flagged, disposition_for(flagged))\n",
        "    if isinstance(result, FlagForReview):\n"
        "        return Decision(True, (), Disposition.ALLOW)\n",
    ),
    # The first mutant anchored outside `decide`, and deliberately not a fail-open: the gate still
    # denies, so a sweep looking only for a draft that got sent would score this one killed by
    # nothing. What dies is the wrapper's terminal guarantee. Expiry raising a generic error rather
    # than the declaration `Checker.check` re-raises untouched turns a closed question back into a
    # retryable failure, and the gate pays the availability budget for a hang the clock had already
    # settled. Killed by
    # `test_expiry_is_a_closed_question_and_does_not_burn_the_availability_budget`, at
    # `len(calls) == 1`.
    Mutant(
        "expiry_raises_a_generic_error",
        SRC / "gates" / "deadline.py",
        "            raise CheckerUnavailable(\n"
        "                f\"checker transport gave no answer within {timeout_seconds} seconds; the gate \"\n"
        "                \"stops waiting and fails closed. The in-flight call was abandoned, not cancelled\"\n"
        "            )\n",
        "            raise RuntimeError(\n"
        "                f\"checker transport gave no answer within {timeout_seconds} seconds\"\n"
        "            )\n",
    ),
    # Re-derived, and not as the brief wrote it. `finally:` -> `else:` does not parse -- a `try`
    # with neither `except` nor `finally` is a syntax error -- so that mutant would have killed
    # every test through a collection error, which this tool now refuses as a false kill. What
    # replaces it is finding C's actual shape: the outcome entry moves onto the success path, so
    # every call that raises, and every call the gate denies, goes unrecorded.
    Mutant(
        "audit_entry_leaves_the_finally",
        SRC / "audit" / "gateway.py",
        '            outcome = "allowed"\n'
        "            return GatewayResult(True, value, decision, intent_seq, self._next_seq())\n"
        "        finally:\n"
        '            outcome_seq = self._write("outcome", tool_name, outcome, digest, seed)\n'
        '            object.__setattr__(self, "_last_outcome_seq", outcome_seq)\n',
        '            outcome = "allowed"\n'
        '            outcome_seq = self._write("outcome", tool_name, outcome, digest, seed)\n'
        '            object.__setattr__(self, "_last_outcome_seq", outcome_seq)\n'
        "            return GatewayResult(True, value, decision, intent_seq, self._next_seq())\n"
        "        finally:\n"
        "            pass\n",
    ),
    Mutant(
        "fsync_removed",
        SRC / "audit" / "store.py",
        "            os.fsync(handle.fileno())",
        "            pass",
    ),
    # Re-derived: the brief's anchor predates the torn-line terminator that now sits between the
    # `open` and the write.
    #
    # **Semantically equivalent on POSIX, and that is stated rather than hidden.** Measured on
    # win32, two tests kill it: `test_the_store_opens_in_binary_append_mode`, which greps the
    # module source for `"ab"`, and `test_the_bytes_on_disk_are_newline_terminated_with_no_carriage
    # _returns`, which reads the bytes. Deselecting the first leaves the second still killing it
    # here. On Linux -- where CI runs -- a text-mode append emits the same bytes as a binary one,
    # so the second cannot discriminate and only the source-substring proxy remains.
    #
    # It is kept because it is honest about a real difference on one platform, and **not** relied
    # on for finding D. Finding D's platform-independent half is `fsync_removed` (buffered lines
    # lost on a crash) and `torn_line_terminator_dropped` (an append that absorbs the record before
    # it), both of which die to effects everywhere. Strengthening the proxy is not available: on
    # POSIX there is no observable difference for a test to assert.
    Mutant(
        "binary_append_becomes_text_append",
        SRC / "audit" / "store.py",
        '        with self._path.open("ab") as handle:\n'
        "            if terminate:\n"
        '                handle.write(b"\\n")\n'
        '            handle.write(line.encode("utf-8"))\n',
        '        with self._path.open("a", encoding="utf-8") as handle:\n'
        "            if terminate:\n"
        '                handle.write("\\n")\n'
        "            handle.write(line)\n",
    ),
    # Added, not in the brief. The store's own docstring claims "a torn line never absorbs the
    # record that follows it", and no mutant held that claim: dropping the terminator concatenates
    # the next entry onto the partial one, `append` still returns a well-formed entry and raises
    # nothing, and `count` -- the send cap's predicate -- silently reports one fewer. That is an
    # enforcement predicate failing open, and it dies to an effect on every platform.
    Mutant(
        "torn_line_terminator_dropped",
        SRC / "audit" / "store.py",
        "        terminate = self._ends_mid_line()",
        "        terminate = False",
    ),
    Mutant(
        "checker_model_floor_removed",
        SRC / "gates" / "checker.py",
        "        assert_checker_not_weaker(model, drafter_model)",
        "        pass",
    ),
    Mutant(
        "tripwire_disjunct_dropped",
        SRC / "gates" / "engine.py",
        "    findings = checker_findings + tripwire_findings",
        "    findings = checker_findings",
    ),
    Mutant(
        "citation_accepts_a_bare_token",
        SRC / "policy" / "citations.py",
        "        if canonical not in draft_figures:",
        "        if False:",
    ),
    # "Null passes" -- design spec 8.3's first named wrong answer, in the one line that implements
    # the right one. A candidate whose cheque size nobody recorded stops being a distinct state and
    # becomes an eligible one, so a party the mandate would exclude is surfaced with no bucket, no
    # named field and nothing said. The anchor carries its `if` line because the bare `append` call
    # appears twice in the function and differs only by indentation.
    Mutant(
        "a_missing_eligibility_field_passes_the_filter",
        SRC / "matching" / "filters.py",
        '    if candidate.check_size_max is None:\n        missing.append("check_size_max")',
        "    if candidate.check_size_max is None:\n        pass",
    ),
    # A rigged baseline, which is design spec 1's named failure and the one the matching ablation
    # is least able to survive: if the weighted arm is weak because someone made it weak, the
    # comparison proves nothing. One axis priced at zero is how that happens without looking like
    # it -- and `test_the_weighted_arm_is_tuned_not_a_strawman`, which scans the arm's source for
    # the five constraint names, stays green through it, because the name is still there. Measured:
    # under this mutant that test passes and only the behavioural pair fails. This entry is what
    # keeps the pair from being deleted as a duplicate of the scan.
    Mutant(
        "the_weighted_arm_stops_pricing_one_constraint",
        SRC / "matching" / "ablation.py",
        '("geography", mandate.geography, 0.20)',
        '("geography", mandate.geography, 0.0)',
    ),
    # The gateway's interface contract on `requires_approval_for`, as the one line that carries it.
    # Removing it restores exactly the body `task-24-brief.md` handed over: equality only, which
    # answers `False` for a re-attempt whose arguments could not be canonicalised, because that
    # digest hashes `repr(args)` and `repr` is not stable across object identity or key insertion
    # order. Design spec 5.4(c) calls this mechanism "what prevents the double-send", so a survivor
    # here means the suite pins the mechanism's *name* and not its effect.
    Mutant(
        "the_unavailable_digest_stops_demanding_approval",
        SRC / "audit" / "recovery.py",
        "    if digest.startswith(DIGEST_UNAVAILABLE):",
        "    if False:",
    ),
    # The send cap read off a torn log. `AuditStore.count` reports a number and drops `torn`, and
    # this is the term that puts the lost record back. Without it the count comes back one short
    # after a crash and the cap permits one send too many -- design spec 5.3's enforcement predicate
    # failing open, which is the whole reason durability is not housekeeping here.
    Mutant(
        "the_cap_forgets_the_record_the_tear_took",
        SRC / "audit" / "recovery.py",
        "    return intents - released + (1 if torn else 0)",
        "    return intents - released",
    ),
    # One outcome resolves one intent. The replacement is the set-membership pairing, written out:
    # every intent sharing a digest is resolved by the first outcome carrying it. A re-attempted
    # send digests to the same value as the attempt that crashed, so the mutant reports the
    # dangling intent as complete -- branch (a) over an intent nobody resolved.
    Mutant(
        "one_outcome_resolves_every_intent_sharing_its_digest",
        SRC / "audit" / "recovery.py",
        "        elif entry.kind == \"outcome\" and pending.get(entry.arg_digest):\n"
        "            paired[pending[entry.arg_digest].pop()][1] = entry\n",
        "        elif entry.kind == \"outcome\":\n"
        "            for index in pending.get(entry.arg_digest, []):\n"
        "                paired[index][1] = entry\n"
        "            pending[entry.arg_digest] = []\n",
    ),
    # A counter cached in the gateway rather than read off the log, which is what `_next_seq` was
    # before `recovery.resume` existed to be a second writer to the same file.
    #
    # **The version that caused the reported defect cached at *construction*, and that cannot be
    # restored from one site: after the fix there is no cached seq anywhere, so re-creating it
    # needs a write in `__init__` and a read here.** This is the single-site form -- cache on first
    # use, then increment -- and it is weaker in one specific way that decided which tests had to
    # exist: the crash-restart ordering fills the cache *after* the recovery pass, so it numbers
    # that scenario correctly and
    # `test_a_send_issued_after_recovery_is_not_released_from_the_cap_by_the_next_recovery` alone
    # would let this mutant live. `..._a_send_made_before_recovery_...` is the ordering that fills
    # it early, and it is the reason the reviewed defect's own property -- a live send released
    # from the cap -- has a mutant behind it rather than a single test.
    #
    # Killed twice over, and the second way is worth naming: a cached counter is not idempotent,
    # while `call` reports `outcome_seq` by asking `_next_seq` what the `finally` will allocate. A
    # counter that *consumes* a number on being asked detaches the seq handed to the caller from
    # the seq on disk, which
    # `test_the_result_names_the_seqs_of_the_entries_that_were_actually_written` catches.
    Mutant(
        "the_gateway_numbers_from_a_cached_counter",
        SRC / "audit" / "gateway.py",
        "        return self.store.next_seq()\n",
        "        if not hasattr(self, \"_cached_seq\"):\n"
        "            self._cached_seq = self.store.next_seq()\n"
        "        seq = self._cached_seq\n"
        "        self._cached_seq += 1\n"
        "        return seq\n",
    ),
    # The other writer's allocation, in the form that makes it stop being an allocation at all:
    # the outcome numbered from the record it resolves rather than from the log. A plausible edit
    # -- the intent's seq is right there in `entry` -- and it puts two records under one number,
    # which is the ordering `stale_after_seq` compares on. Killed by
    # `test_resume_allocates_seqs_that_collide_with_nothing_already_in_the_log`.
    Mutant(
        "resume_numbers_an_outcome_from_the_intent_it_resolves",
        SRC / "audit" / "recovery.py",
        "        store.append(dict(seq=store.next_seq(), kind=\"outcome\", tool=entry.tool,\n",
        "        store.append(dict(seq=entry.seq, kind=\"outcome\", tool=entry.tool,\n",
    ),
    # Design spec 5.4(b) is a conjunction, and this is the conjunct a brief-following implementer
    # drops: `stale_after_seq` arrives in the signature and is never read. The mutant releases an
    # intent that may still be in flight from the cap on a probe answer that means "not yet".
    Mutant(
        "the_release_branch_stops_checking_staleness",
        SRC / "audit" / "recovery.py",
        "        absent = _probe(side_effect_absent, entry) if entry.seq <= stale_after_seq else None",
        "        absent = _probe(side_effect_absent, entry)",
    ),
]


#: Where the sweep's advisory lock lives. Under `.pytest_cache/`, which `.gitignore` already holds,
#: so a lock left by a killed run never shows up as a dirty working tree.
SWEEP_LOCK = ROOT / ".pytest_cache" / "chaperone-mutation-sweep.lock"

#: A lock older than this belonged to a process that is gone. Generous against the sweep's own
#: runtime -- a full pass is minutes, not hours -- so a live sweep is never broken into.
STALE_LOCK_SECONDS = 3600.0

#: Where the pre-mutation source waits while its mutant is applied. Beside the lock and under the
#: `.pytest_cache/` that `.gitignore` already holds, so a backup left by a killed run is never
#: mistaken for a dirty working tree by the `git status --porcelain` that guards every commit here.
SWEEP_BACKUP = ROOT / ".pytest_cache" / "chaperone-mutation-backup.json"


def _pid_is_running(pid: int) -> bool:
    """Whether `pid` names a live process. **Unknown answers "yes".**

    Every uncertain case -- a platform call that fails, an exit code this cannot read, a pid owned
    by another user -- returns True, because the only action taken on a False is breaking a lock,
    and breaking a live sweep's lock is the corruption `sweep_lock` exists to prevent. A spurious
    wait costs time; a spurious break costs a fail-open mutant left in `src/`.

    **`os.kill(pid, 0)` is not used on win32, and this is not a portability nicety.** Python's
    `os.kill` on Windows routes any signal other than `CTRL_C_EVENT`/`CTRL_BREAK_EVENT` to
    `TerminateProcess`, so the idiomatic POSIX liveness probe would *kill the sweep it was asking
    about* -- the probe becoming the incident. `OpenProcess` is a query and terminates nothing.
    """
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # PROCESS_QUERY_LIMITED_INFORMATION: the narrowest right that answers this question.
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            # 87 is ERROR_INVALID_PARAMETER, which is how "no such pid" arrives. Anything else --
            # ERROR_ACCESS_DENIED above all -- means the process exists and is not ours to inspect.
            return kernel32.GetLastError() != 87
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # PermissionError and anything else: it is there, or we cannot tell.
    return True


def _lock_owner_is_gone(path: Path) -> bool:
    """True only when the lock names a pid and that pid has exited.

    An absent or unparseable `owner` returns False. `mkdir` and the owner write are two operations,
    so a lock taken microseconds ago is briefly ownerless, and reading that as "gone" would break
    into the live sweep the file was about to name.
    """
    try:
        pid = int((path / "owner").read_bytes().decode("utf-8").strip())
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    return not _pid_is_running(pid)


@contextmanager
def sweep_lock(path: Path = SWEEP_LOCK, timeout: float = 1200.0, poll: float = 0.5):
    """Hold the working tree for one sweep. Two sweeps in one checkout corrupt it.

    Not a theoretical hazard: two agent sessions ran `pytest` against this checkout at the same
    time, and because the sweep mutates files under `src/` that every process in the checkout
    imports, the second sweep took its backup while the first sweep's mutant was on disk and then
    restored that. `gates/engine.py` was left with `checker_unavailable_fails_open` applied --
    the gate failing open -- while **both** sweeps' restores had verified their own writes and
    reported success. Verifying your own write does not make a shared file yours.

    `mkdir` is the primitive because it is atomic on win32 and on POSIX alike, where an `open(...,
    "x")` on a network path is not dependably so. A lock older than `STALE_LOCK_SECONDS` is broken
    rather than waited on, since the alternative is that one killed run stops every later one.

    **The pid is read, not merely recorded.** It was written "for whoever has to read a stuck
    lock", which named a human and left the code waiting the full `timeout` on a lock whose owner
    was gone -- `STALE_LOCK_SECONDS` is an hour, so the age check never reaches it inside one
    session. Measured on this repository: a suite that runs in 250s exceeded 600s twice, blocked on
    a dead owner. So a lock naming an exited pid is broken into at once, and every other answer
    from `_pid_is_running` -- including "cannot tell" -- keeps the wait.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            path.mkdir()
            break
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except OSError:
                continue
            if age > STALE_LOCK_SECONDS or _lock_owner_is_gone(path):
                shutil.rmtree(path, ignore_errors=True)
                continue
            if time.monotonic() >= deadline:
                raise MutationHarnessError(
                    f"another mutation sweep holds {path} (held {age:.0f}s). Two sweeps in one "
                    "checkout leave a mutant applied in the working tree. Wait for it, or remove "
                    "the lock if no sweep is running."
                ) from None
            time.sleep(poll)
    try:
        (path / "owner").write_bytes(f"{os.getpid()}".encode("utf-8"))
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def read_source(path: Path) -> str:
    """The file's text, decoded without newline translation. Never `read_text`."""
    return path.read_bytes().decode("utf-8")


@dataclass(frozen=True)
class Backup:
    """The durable half of a mutation: which file, what was there, and what replaced it.

    **Both texts, not just the original.** Recovery has to tell a strand still sitting in the tree
    from a file a human has since edited, and only the mutated text answers that question. The
    difference is between putting the source back and writing over somebody's work.
    """

    mutant: str
    path: Path
    original: str
    mutated: str


def write_backup(backup: Path, mutant: Mutant, original: str, mutated: str) -> None:
    """Park the pre-mutation source on disk. Called before the mutation is written, never after.

    Written under a staging name, flushed, fsynced, and moved into place with `os.replace`, which is
    atomic on win32 and on POSIX alike. **Scoped to the deaths this covers, which are the ones on
    the list: a process that stops.** Against those, the only two states a reader can find are *no
    backup and no mutation*, where the tree was never touched and there is nothing to recover, or *a
    complete backup*, which is what `recover_stranded_mutant` reads -- a half-written backup, a
    recovery holding half the source it needs, is never read because the rename publishes the file
    whole or not at all.

    Two things it does **not** cover, stated rather than implied. There is no fsync of the
    containing directory, so on POSIX the rename itself is not forced to disk: a machine that stops
    can lose the rename while keeping the mutation, and that state is not recoverable. And a death
    between the staged write and the rename leaves a `.staged` file behind for good -- harmless,
    git-ignored, and overwritten by the next backup, but a third on-disk state all the same.

    **Read back and compared, never assumed** -- `restore` models it, and the two halves cover
    different failures: `os.replace` covers the interruption, and the comparison covers a write that
    reported success without landing. It raises *before* `apply_mutant` writes the mutation, so a
    backup that cannot be trusted leaves the tree unmutated rather than unrecoverable.
    """
    backup.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"mutant": mutant.name, "path": str(mutant.path), "original": original, "mutated": mutated},
        ensure_ascii=False,
    ).encode("utf-8")
    staged = backup.with_suffix(".staged")
    with staged.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(staged, backup)
    landed = backup.read_bytes()
    if landed != payload:
        raise MutationHarnessError(
            f"{backup} holds {len(landed)} bytes against {len(payload)} written, so the source this "
            "mutation would have to be recovered from is not on disk. Nothing has been mutated."
        )


def read_backup(backup: Path) -> Backup:
    """The parked source, or a refusal naming the file. Never a raw decoding error.

    A backup that cannot be read is still the only record of what a sweep mutated, so it is left
    exactly where it is and the message tells a human to open it rather than offering to clear it.
    """
    try:
        record = json.loads(backup.read_bytes().decode("utf-8"))
        name, original, mutated = record["mutant"], record["original"], record["mutated"]
        # A field of the wrong type is the same kind of unusable as a missing one, and it gets
        # further: both comparisons in `recover_stranded_mutant` are on the encoded bytes of these
        # two, so a `null` that reached them would crash out through a raw `AttributeError`. `mutant`
        # is checked with them because it is printed, and a run that recovers should not be the run
        # that discovers its own log line cannot be formatted.
        wrong = {f: type(v).__name__ for f, v in
                 (("mutant", name), ("original", original), ("mutated", mutated))
                 if not isinstance(v, str)}
        if wrong:
            raise TypeError(f"these fields must be text: {wrong}")
        return Backup(name, Path(record["path"]), original, mutated)
    except (OSError, ValueError, LookupError, TypeError) as exc:
        raise MutationHarnessError(
            f"{backup} could not be read as a mutation backup ({type(exc).__name__}: {exc}), and it "
            "is the only record of the source some sweep mutated. It is left in place: read it by "
            "hand, put the file back yourself, then delete it."
        ) from exc


def audit_anchors(mutants: list[Mutant] = MUTANTS) -> list[str]:
    """Every anchor that is missing, duplicated, inert or unparseable. `[]` means all are exact.

    An empty `mutants` is itself a finding. A sweep over no mutant reports every mutant killed,
    which is the shape of a guard that examined nothing and passed.
    """
    problems: list[str] = []
    if not mutants:
        problems.append("no mutant is registered: a sweep over nothing reports every mutant killed")
    seen: set[str] = set()
    for mutant in mutants:
        if mutant.name in seen:
            problems.append(f"{mutant.name}: registered more than once")
        seen.add(mutant.name)
        if not mutant.path.is_file():
            problems.append(f"{mutant.name}: {mutant.path} is not a file")
            continue
        original = read_source(mutant.path)
        occurrences = original.count(mutant.find)
        if occurrences == 0:
            problems.append(
                f"{mutant.name}: its anchor is not in {mutant.path.name} -- either the source moved "
                "and the mutant must be re-derived, or a crashed sweep left a mutant applied"
            )
            continue
        if occurrences > 1:
            problems.append(
                f"{mutant.name}: its anchor occurs {occurrences} times in {mutant.path.name}; the "
                "first is replaced, so a kill would be recorded for a change the mutant is not named for"
            )
            continue
        mutated = original.replace(mutant.find, mutant.replace, 1)
        if mutated == original:
            problems.append(f"{mutant.name}: mutating changes no byte, so it cannot be killed")
            continue
        try:
            ast.parse(mutated)
        except SyntaxError as exc:
            problems.append(
                f"{mutant.name}: the mutated source does not parse ({exc}); every test would fail "
                "through a collection error rather than through the property"
            )
    return problems


def apply_mutant(mutant: Mutant, backup: Path = SWEEP_BACKUP) -> str:
    """Write the mutant and return the original text. Raises unless the anchor is exact.

    A mutant that silently stops applying is a test that silently stops testing, so this refuses
    rather than warning -- and it refuses a duplicated anchor and an inert or unparseable
    replacement for the reasons in the module docstring.

    **The original reaches the disk before the mutation that replaces it does.** The returned string
    used to be the only record of it anywhere, and the `finally` that restores from it runs only if
    this process reaches that line -- so an interrupt, a harness stop, a timeout or a sleeping
    machine left a mutant applied in `src/` with nothing left to put it back from, on any later run.
    Five times in one session. `sweep_lock` has kept durable state on disk (the owner's pid) since a
    dead owner blocked a run past 600s, and for the same reason: a later run can reason about a dead
    predecessor only from what that predecessor wrote down. Restoration wrote nothing down.
    """
    problems = audit_anchors([mutant])
    if problems:
        raise MutationHarnessError(f"{'; '.join(problems)} -- update tools/mutate.py")
    original = read_source(mutant.path)
    mutated = original.replace(mutant.find, mutant.replace, 1)
    write_backup(backup, mutant, original, mutated)
    mutant.path.write_bytes(mutated.encode("utf-8"))
    return original


def restore(path: Path, original: str, backup: Path = SWEEP_BACKUP) -> None:
    """Put the file back, prove it, and only then drop the backup.

    Bytes, never `write_text`; compared, never assumed. The unlink is last and is reached only past
    the comparison: the backup is the sole record of the pre-mutation source, so dropping it before
    those bytes are known to have landed would turn a restore that failed into one that can never be
    retried. A restore that raises leaves the backup where the next run's recovery will find it.
    """
    expected = original.encode("utf-8")
    path.write_bytes(expected)
    landed = path.read_bytes()
    if landed != expected:
        raise MutationHarnessError(
            f"{path} was not restored byte-for-byte: {len(landed)} bytes on disk against "
            f"{len(expected)} expected -- the working tree is dirty and must be checked out"
        )
    backup.unlink(missing_ok=True)


def recover_stranded_mutant(backup: Path = SWEEP_BACKUP) -> str | None:
    """Put back the source a killed sweep left mutated. The mutant's name, or None if nothing was.

    **This runs before `audit_anchors`, and that ordering is the fix rather than a detail of it.** A
    stranded mutant usually destroys its own anchor, which is what keeps a later run from
    snapshotting mutated source as the new "original" -- but the audit would otherwise be `main`'s
    first act and an immediate `return 1`, as it was before this existed. Recovery placed after it
    never executes in the one state it exists for: the tree stays broken and the fix cannot fire.

    Three states, and exactly one of them is written to:

    * the file still holds the mutant -- the strand is demonstrably there, so it is put back;
    * the file already holds the original -- a human read `git status --porcelain src/` and ran
      `git checkout` -- so writing it again would change nothing, and the spent backup is dropped;
    * anything else -- checked out and then worked on, edited in place, or gone -- and this refuses.
      Writing the backup over it destroys real work, and nothing available here can tell which bytes
      are wanted. Refuse rather than guess, and say in the message what a human should do.
    """
    if not backup.is_file():
        return None
    record = read_backup(backup)
    on_disk = record.path.read_bytes() if record.path.is_file() else None
    if on_disk == record.mutated.encode("utf-8"):
        restore(record.path, record.original, backup)
        return record.mutant
    if on_disk == record.original.encode("utf-8"):
        backup.unlink()
        return None
    state = (
        "holds neither the {name} mutant nor the source it was mutated from"
        if on_disk is not None
        else "is not there at all, so nothing can be compared against the {name} mutant"
    ).format(name=record.mutant)
    raise MutationHarnessError(
        f"{record.path} {state}, so the backup at {backup} cannot be applied without destroying "
        "whatever is there now. Nothing was written. Compare the file against that backup's "
        "'original' by hand, then either keep the file and delete the backup, or write the "
        "'original' back yourself and delete it."
    )


def _child_command(junit_path: Path | None, extra_args: tuple[str, ...]) -> list[str]:
    command = [sys.executable, "-m", "pytest", f"--ignore={SWEEP_MODULE}"]
    if junit_path is not None:
        command.append(f"--junit-xml={junit_path}")
    return command + list(extra_args)


def child_environment() -> dict[str, str]:
    """The environment the child is launched under: this process's, plus the recursion marker.

    A named function rather than an inline dict because it is the only half of the recursion guard
    a test can reach directly. The other half -- that the marker makes `tests/test_mutations.py`
    skip -- is asserted by running a child under it; that a child is *actually* launched under it
    is asserted here. Inlined, removing the marker broke no test, measured.
    """
    return {**os.environ, CHILD_ENV_MARKER: "1"}


def run_suite(
    junit_path: Path | None = None,
    timeout: float = 900.0,
    extra_args: tuple[str, ...] = (),
) -> bool:
    """True when the suite is green, False when a test failed. **Raises on anything else.**

    The bool is deliberately total over exactly two outcomes. Every other way a pytest run can end
    -- nothing collected, a collection error, an internal error, a usage error, a timeout -- is a
    run that examined nothing or examined it wrongly, and folding any of them into `False` would
    report a mutant killed by a suite that never ran it.

    `-q` is not passed: `addopts = "-q"` is already in `pyproject.toml`, and a second one is `-qq`.

    `extra_args` narrows the child -- `("-k", "audit")` while chasing a survivor. It is also how the
    exit codes above are exercised for real rather than through a stubbed `subprocess.run`, since a
    selector matching nothing is genuinely how pytest comes to exit 5.
    """
    try:
        completed = subprocess.run(
            _child_command(junit_path, extra_args),
            cwd=ROOT,
            env=child_environment(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise MutationHarnessError(
            f"the suite did not finish within {timeout}s; a hung run is not a killed mutant"
        ) from exc
    if completed.returncode == EXIT_GREEN:
        return True
    if completed.returncode == EXIT_TESTS_FAILED:
        return False
    meaning = _EXIT_MEANING.get(completed.returncode, "pytest did not run")
    raise MutationHarnessError(
        f"pytest exited {completed.returncode} ({meaning}), which is neither green nor a test "
        f"failure:\n{completed.stdout[-2000:]}\n{completed.stderr[-2000:]}"
    )


def collected_tests(junit_path: Path) -> tuple[str, ...]:
    """Every node id the child actually collected, from its own report of the run.

    What the child collected is the only evidence that `--ignore` matched anything; the argument
    list handed to `subprocess.run` says what was asked for, not what happened.
    """
    root = ET.parse(junit_path).getroot()
    return tuple(
        f"{case.get('classname', '')}::{case.get('name', '')}" for case in root.iter("testcase")
    )


def failing_tests(junit_path: Path) -> tuple[str, ...]:
    """The node ids a junit-xml report records as failed or errored, in file order.

    Read from the report rather than from stdout so a kill count is a count of test cases and not
    of lines that happened to match a pattern.
    """
    root = ET.parse(junit_path).getroot()
    return tuple(
        f"{case.get('classname', '')}::{case.get('name', '')}"
        for case in root.iter("testcase")
        if case.find("failure") is not None or case.find("error") is not None
    )


def main(
    mutants: list[Mutant] = MUTANTS,
    backup: Path = SWEEP_BACKUP,
    lock: Path = SWEEP_LOCK,
) -> int:
    """Report every mutant, the tests that killed it, and refuse a sweep whose anchors have drifted.

    **The lock is taken first, around everything.** It used to be taken after the anchor audit and
    the baseline run, both of which read files a concurrent sweep mutates; and recovery cannot run
    outside it at all, since restoring a file while another sweep holds its mutant is the two-sweep
    corruption `sweep_lock` was written for. That the lock breaks into a dead owner's lock at once is
    what leaves the door open here: the run that strands a mutant strands its lock too.
    """
    survivors: list[str] = []
    with sweep_lock(lock):
        # Before the audit, and before anything reads the tree. A strand destroys its own anchor,
        # so an audit that ran first would refuse and return -- and recovery placed after it would
        # never execute in the one state it exists for. See `recover_stranded_mutant`.
        #
        # Its refusal is caught and printed rather than raised: this is a program, and a refusal
        # written to be read by whoever has to resolve the ambiguity arrives as a crash if it comes
        # out as a traceback.
        try:
            recovered = recover_stranded_mutant(backup)
        except MutationHarnessError as exc:
            print(f"refusing: {exc}")
            return 1
        if recovered:
            print(f"recovered {recovered}: a killed sweep had left it applied; the file is back")

        problems = audit_anchors(mutants)
        if problems:
            for problem in problems:
                print(f"anchor: {problem}")
            return 1

        junit = ROOT / ".pytest_cache" / "mutation.xml"
        junit.parent.mkdir(parents=True, exist_ok=True)
        if not run_suite(junit):
            print("the suite is not green before mutation; every kill below would be unattributable")
            return 1

        for mutant in mutants:
            original = apply_mutant(mutant, backup)
            try:
                green = run_suite(junit)
                killers = failing_tests(junit)
            finally:
                restore(mutant.path, original, backup)
            if green:
                survivors.append(mutant.name)
                print(f"SURVIVED {mutant.name}")
            else:
                print(f"killed   {mutant.name}  by {len(killers)} test(s), first {killers[0]}")
    if survivors:
        print(f"surviving mutants: {survivors}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
