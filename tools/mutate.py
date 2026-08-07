"""Mutation sweep. Design spec 10.1: flip the deny to allow, drop the `finally`, remove the fsync
-- **the tests must fail.** A surviving mutant means the test that should have caught it asserts a
proxy for the property rather than the property.

This tool is itself an enforcement predicate, so the four ways it has failed before are closed here
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

A fifth guard has no incident behind it and is here because the failure would be spectacular: the
sweep runs pytest as a subprocess, and `--ignore` is resolved against the invocation directory. Run
from anywhere but the root, the child would collect this sweep and each mutant would fan out into
another nine. `run_suite` passes `cwd=ROOT` with an absolute `--ignore`, *and* sets
`CHILD_ENV_MARKER` so `tests/test_mutations.py` skips itself if the ignore ever stops matching.
"""
from __future__ import annotations

import ast
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
    Mutant(
        "checker_unavailable_fails_open",
        SRC / "gates" / "engine.py",
        "        return Decision(False, unavailable, disposition_for(unavailable))",
        "        return Decision(True, (), Disposition.ALLOW)",
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
        "            return GatewayResult(True, value, decision, intent_seq, self._seq)\n"
        "        finally:\n"
        '            outcome_seq = self._write("outcome", tool_name, outcome, digest, seed)\n'
        '            object.__setattr__(self, "_last_outcome_seq", outcome_seq)\n',
        '            outcome = "allowed"\n'
        '            outcome_seq = self._write("outcome", tool_name, outcome, digest, seed)\n'
        '            object.__setattr__(self, "_last_outcome_seq", outcome_seq)\n'
        "            return GatewayResult(True, value, decision, intent_seq, self._seq)\n"
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
    # `open` and the write. **Known to be platform-conditional**, which is why the mutant below it
    # exists. On POSIX a text-mode append is byte-identical to a binary one, so the only thing that
    # kills this on Linux is `test_the_store_opens_in_binary_append_mode`, which greps the module
    # source for `"ab"` -- a proxy. On win32 the platform really translates and
    # `test_the_bytes_on_disk_are_newline_terminated_with_no_carriage_returns` kills it on the
    # bytes. Measured both ways and reported rather than asserted away.
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
]


#: Where the sweep's advisory lock lives. Under `.pytest_cache/`, which `.gitignore` already holds,
#: so a lock left by a killed run never shows up as a dirty working tree.
SWEEP_LOCK = ROOT / ".pytest_cache" / "chaperone-mutation-sweep.lock"

#: A lock older than this belonged to a process that is gone. Generous against the sweep's own
#: runtime -- a full pass is minutes, not hours -- so a live sweep is never broken into.
STALE_LOCK_SECONDS = 3600.0


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
    "x")` on a network path is not dependably so. The pid is recorded for whoever has to read a
    stuck lock, and a lock older than `STALE_LOCK_SECONDS` is broken rather than waited on, since
    the alternative is that one killed run stops every later one.
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
            if age > STALE_LOCK_SECONDS:
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


def apply_mutant(mutant: Mutant) -> str:
    """Write the mutant and return the original text. Raises unless the anchor is exact.

    A mutant that silently stops applying is a test that silently stops testing, so this refuses
    rather than warning -- and it refuses a duplicated anchor and an inert or unparseable
    replacement for the reasons in the module docstring.
    """
    problems = audit_anchors([mutant])
    if problems:
        raise MutationHarnessError(f"{'; '.join(problems)} -- update tools/mutate.py")
    original = read_source(mutant.path)
    mutant.path.write_bytes(original.replace(mutant.find, mutant.replace, 1).encode("utf-8"))
    return original


def restore(path: Path, original: str) -> None:
    """Put the file back, and prove it. Bytes, never `write_text`; compared, never assumed."""
    expected = original.encode("utf-8")
    path.write_bytes(expected)
    landed = path.read_bytes()
    if landed != expected:
        raise MutationHarnessError(
            f"{path} was not restored byte-for-byte: {len(landed)} bytes on disk against "
            f"{len(expected)} expected -- the working tree is dirty and must be checked out"
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


def main() -> int:
    """Report every mutant, the tests that killed it, and refuse a sweep whose anchors have drifted."""
    problems = audit_anchors()
    if problems:
        for problem in problems:
            print(f"anchor: {problem}")
        return 1

    junit = ROOT / ".pytest_cache" / "mutation.xml"
    junit.parent.mkdir(parents=True, exist_ok=True)
    if not run_suite(junit):
        print("the suite is not green before mutation; every kill below would be unattributable")
        return 1

    survivors: list[str] = []
    with sweep_lock():
        for mutant in MUTANTS:
            original = apply_mutant(mutant)
            try:
                green = run_suite(junit)
                killers = failing_tests(junit)
            finally:
                restore(mutant.path, original)
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
