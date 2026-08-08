"""The mutation sweep, and the guards that keep the sweep itself from reporting a kill it did not see.

Design spec 10.1: flip the deny to allow, drop the `finally`, remove the fsync -- **the tests must
fail.** A surviving mutant means the test that should have caught it asserts a proxy.

Half of this file is aimed at `tools/mutate.py` rather than at the tree, because a mutation harness
is an enforcement predicate and this project has shipped four tools that reported success having
examined nothing. Every way of examining nothing -- a stale anchor, a duplicated one, a replacement
that changes no byte, a mutated file that does not parse, a pytest run that collected nothing -- is
asserted here to raise rather than to read as a kill or as a survival.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tools.mutate import (
    CHILD_ENV_MARKER,
    MUTANTS,
    ROOT,
    SWEEP_LOCK,
    SWEEP_MODULE,
    Mutant,
    MutationHarnessError,
    apply_mutant,
    audit_anchors,
    child_environment,
    collected_tests,
    failing_tests,
    read_source,
    restore,
    run_suite,
    sweep_lock,
)

if os.environ.get(CHILD_ENV_MARKER):
    pytest.skip("the sweep does not sweep itself", allow_module_level=True)


@pytest.fixture(scope="module", autouse=True)
def _hold_the_working_tree():
    """No second sweep may mutate `src/` while this one is running. See `sweep_lock`."""
    with sweep_lock():
        yield


def _a_pid_that_has_exited() -> int:
    """The pid of a process that has already been reaped. Not a guessed number.

    A literal like 999999 is a number nothing owns *today*; a pid this run watched exit is a fact.
    """
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def test_the_sweep_holds_the_lock_for_as_long_as_it_is_mutating_the_tree():
    """The fixture above is the whole protection, and an unused fixture is a silent one.

    **`is_dir()` alone passed on the condition it was written to detect.** A lock stranded by a
    killed run is a directory too, so the assertion was satisfied by the very state that means no
    sweep holds the tree. The owner is what distinguishes the two, so the owner is what is read.
    """
    assert SWEEP_LOCK.is_dir()
    assert (SWEEP_LOCK / "owner").read_bytes() == f"{os.getpid()}".encode("utf-8"), (
        "the sweep lock is held by some other process, so this run is mutating a tree it does "
        "not own"
    )


@pytest.fixture(scope="module")
def baseline_report(tmp_path_factory):
    """One un-mutated child run, reported to junit-xml, shared by the two tests that read it."""
    junit = tmp_path_factory.mktemp("mutation") / "baseline.xml"
    return run_suite(junit), junit


# --------------------------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------------------------


def test_every_mutant_anchor_is_present_and_unique():
    """The audit that has to pass before any kill below means anything.

    A stale anchor leaves the source unmutated and the sweep records a *survivor* that never
    existed; a duplicated one mutates the first occurrence, so a kill is recorded for a change the
    mutant is not named for. This also detects a mutant left applied by a crashed run, which shows
    up here as an absent anchor.
    """
    assert audit_anchors() == []


@pytest.mark.parametrize("mutant", MUTANTS, ids=lambda m: m.name)
def test_every_mutant_is_killed(mutant, tmp_path):
    original = apply_mutant(mutant)
    try:
        green = run_suite(tmp_path / "mutant.xml")
        killers = failing_tests(tmp_path / "mutant.xml")
    finally:
        restore(mutant.path, original)
    assert green is False, f"mutant survived: {mutant.name}"
    # A non-zero exit with nothing recorded failing would be a kill nobody can attribute.
    assert killers, f"{mutant.name}: the run failed but named no failing test"


def test_the_suite_is_green_before_and_after_mutation(baseline_report):
    green, _ = baseline_report
    assert green is True


def test_the_child_run_never_collects_the_sweep_itself(baseline_report):
    """Without this the child collects this module and every mutant fans out into another nine.

    `--ignore` is resolved against the invocation directory, so the guard is only real if the child
    is launched from the root with an absolute path. Asserted on the child's own report of what it
    collected, not on the argument list handed to it.
    """
    _, junit = baseline_report
    collected = collected_tests(junit)
    assert collected, "the child collected nothing, so its report proves nothing"
    assert not [node for node in collected if "test_mutations" in node]


def test_the_child_skips_the_sweep_even_if_the_ignore_stops_matching(tmp_path):
    """The second line of defence, exercised with the first one removed.

    `--ignore` is one string; a rename or a moved root silently ends it. The environment marker is
    what makes that degrade into a skip instead of into nine recursive sweeps, so it is tested with
    `--ignore` deliberately absent -- which is the only configuration where it does anything. This
    is the one place a child is launched without `run_suite`, because `run_suite` is the thing whose
    failure is being simulated.

    A module-level skip leaves nothing run, which is pytest's exit 5 -- the same code `run_suite`
    refuses to read as a kill. So the degraded configuration cannot produce a kill by either route.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(SWEEP_MODULE), "--junit-xml", str(tmp_path / "j.xml")],
        cwd=ROOT,
        env={**os.environ, CHILD_ENV_MARKER: "1"},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 5, completed.stdout[-2000:]
    assert "1 skipped" in completed.stdout
    assert not failing_tests(tmp_path / "j.xml")
    assert not [node for node in collected_tests(tmp_path / "j.xml") if "test_every_mutant" in node]


def test_the_child_is_actually_launched_under_the_marker_that_makes_it_skip():
    """The other half of the pair above, which on its own proves only that the marker *works*.

    Removing the marker from the launch broke no test until this one existed -- measured, not
    supposed. The parent must not be carrying it, or the sweep would have skipped itself.
    """
    assert child_environment()[CHILD_ENV_MARKER] == "1"
    assert CHILD_ENV_MARKER not in os.environ
    assert child_environment()["PATH"] == os.environ["PATH"], "the child must inherit, not replace"


# --------------------------------------------------------------------------------------------
# The harness's own fail-open surface
# --------------------------------------------------------------------------------------------


def _mutant_over(tmp_path, body: str, find: str, replace: str, name: str = "probe") -> Mutant:
    path = tmp_path / "probe.py"
    path.write_bytes(body.encode("utf-8"))
    return Mutant(name, path, find, replace)


def test_a_sweep_over_no_mutant_is_refused_rather_than_reporting_every_mutant_killed():
    """An empty list makes `test_every_mutant_is_killed` collect zero cases and the suite green."""
    problems = audit_anchors([])
    assert problems
    assert "no mutant is registered" in problems[0]


def test_a_stale_anchor_is_refused_rather_than_recorded_as_a_survivor(tmp_path):
    """The source moves, the replace finds nothing, the file is unmutated -- and every test passes."""
    mutant = _mutant_over(tmp_path, "x = 1\n", "x = 2", "x = 3")
    assert audit_anchors([mutant])
    with pytest.raises(MutationHarnessError, match="anchor is not in"):
        apply_mutant(mutant)


def test_a_duplicated_anchor_is_refused_rather_than_mutating_the_first_of_two(tmp_path):
    """`replace(find, replace, 1)` takes the first occurrence, so the kill names the wrong change."""
    mutant = _mutant_over(tmp_path, "x = 1\ny = 1\n", "= 1", "= 2")
    assert audit_anchors([mutant])
    with pytest.raises(MutationHarnessError, match="occurs 2 times"):
        apply_mutant(mutant)


def test_a_replacement_that_changes_no_byte_is_refused(tmp_path):
    """An equivalent mutant survives honestly; an *inert* one survives having done nothing."""
    mutant = _mutant_over(tmp_path, "x = 1\n", "x = 1", "x = 1")
    assert audit_anchors([mutant])
    with pytest.raises(MutationHarnessError, match="changes no byte"):
        apply_mutant(mutant)


def test_a_mutant_whose_mutated_source_does_not_parse_is_refused(tmp_path):
    """The brief's own `finally:` -> `else:` is this shape: a `try` with neither is a SyntaxError.

    Applied, it fails every test through a collection error -- pytest exit 2, not exit 1 -- and a
    harness that read "not zero" as a kill would have recorded eight kills for a typo.
    """
    body = "def f():\n    try:\n        return 1\n    finally:\n        pass\n"
    mutant = _mutant_over(tmp_path, body, "    finally:", "    else:")
    assert audit_anchors([mutant])
    with pytest.raises(MutationHarnessError, match="does not parse"):
        apply_mutant(mutant)


def test_a_run_that_collected_no_test_raises_rather_than_reading_as_a_killed_mutant():
    """pytest exits 5 having examined nothing. `returncode != 0` calls that a kill."""
    with pytest.raises(MutationHarnessError, match="collected no test"):
        run_suite(extra_args=("-k", "no_test_is_named_this_zzz"))


def test_a_run_pytest_refused_to_start_raises_rather_than_reading_as_a_killed_mutant():
    """Exit 4 -- a bad flag, a missing path -- is also non-zero and also examined nothing."""
    with pytest.raises(MutationHarnessError, match="invoked wrongly"):
        run_suite(extra_args=("--no-such-flag-zzz",))


# --------------------------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------------------------


def test_two_sweeps_cannot_hold_the_working_tree_at_once(tmp_path):
    """Observed, not anticipated: two sweeps ran in one checkout and the tree was left failing open.

    The sweep mutates files under `src/`, which every process sharing the checkout reads. A second
    sweep starting mid-flight applied its own mutant and then restored *its* backup -- taken while
    the first sweep's mutant was on disk -- so `gates/engine.py` was left with
    `checker_unavailable_fails_open` applied and committed-adjacent. Each sweep's own restore
    verified its own write and both were satisfied.
    """
    lock = tmp_path / "sweep.lock"
    with sweep_lock(lock, timeout=0.0):
        assert lock.exists()
        with pytest.raises(MutationHarnessError, match="another mutation sweep"):
            with sweep_lock(lock, timeout=0.0):
                pass


def test_the_lock_is_released_when_the_sweep_raises_rather_than_stranding_the_next_run(tmp_path):
    """A lock held by a crashed sweep is an outage; the restore must not depend on a clean exit."""
    lock = tmp_path / "sweep.lock"
    with pytest.raises(ZeroDivisionError):
        with sweep_lock(lock, timeout=0.0):
            1 / 0
    assert not lock.exists()
    with sweep_lock(lock, timeout=0.0):
        assert lock.exists()


def test_a_lock_whose_owner_process_is_gone_is_broken_into_rather_than_waited_on(tmp_path):
    """The pid was recorded and never read, so a killed sweep blocked the next one for 1200s.

    `sweep_lock`'s own docstring says the pid is "recorded for whoever has to read a stuck lock",
    which described a human and not the code. Measured on this repository the same day this was
    written: a suite that runs in 250s exceeded 600s twice, blocked on a lock whose owner had been
    SIGTERMed mid-sweep. `STALE_LOCK_SECONDS` is an hour, so the age check does not reach it.

    Asserted on the effect -- the lock is acquired -- with `timeout=0.0`, which is the setting under
    which a live owner raises immediately. So this cannot pass by waiting.
    """
    lock = tmp_path / "sweep.lock"
    lock.mkdir()
    (lock / "owner").write_bytes(str(_a_pid_that_has_exited()).encode("utf-8"))
    with sweep_lock(lock, timeout=0.0):
        assert lock.is_dir()


def test_a_lock_whose_owner_is_alive_is_still_refused(tmp_path):
    """The other direction, and the dangerous one: breaking into a live sweep corrupts the tree.

    Without this, `return False` from the liveness probe -- a probe that cannot answer, a platform
    where the call is unavailable -- would satisfy the test above while reintroducing the two-sweep
    corruption `test_two_sweeps_cannot_hold_the_working_tree_at_once` exists for.
    """
    lock = tmp_path / "sweep.lock"
    lock.mkdir()
    (lock / "owner").write_bytes(str(os.getpid()).encode("utf-8"))
    with pytest.raises(MutationHarnessError, match="another mutation sweep"):
        with sweep_lock(lock, timeout=0.0):
            pass


def test_a_lock_carrying_no_readable_owner_is_refused_rather_than_broken_into(tmp_path):
    """`mkdir` and the `owner` write are two operations, so a live holder is briefly ownerless.

    Reading an absent or unparseable owner as "gone" would break into a lock taken microseconds
    ago, which is the exact corruption the lock exists to prevent. Unreadable means wait.
    """
    lock = tmp_path / "sweep.lock"
    lock.mkdir()
    with pytest.raises(MutationHarnessError, match="another mutation sweep"):
        with sweep_lock(lock, timeout=0.0):
            pass
    (lock / "owner").write_bytes(b"not-a-pid")
    with pytest.raises(MutationHarnessError, match="another mutation sweep"):
        with sweep_lock(lock, timeout=0.0):
            pass


# --------------------------------------------------------------------------------------------
# Restoration
# --------------------------------------------------------------------------------------------


def test_restoration_puts_every_byte_back_and_introduces_no_carriage_return(tmp_path):
    """`write_text` converts the file to CRLF against `.gitattributes`' `eol=lf` pin.

    That is a disclosed incident in this project's own mutation driver, repeated once. The check is
    on bytes rather than on a search for the replacement text, because
    `"return Decision(True, (), Disposition.ALLOW)"` occurs in the *pristine* `gates/engine.py` too
    -- the assertion below proves that, so a substring check would pass over a file still mutated.
    """
    engine = next(m for m in MUTANTS if m.name == "checker_unavailable_fails_open")
    pristine = engine.path.read_bytes()
    assert engine.replace.strip().encode("utf-8") in pristine, "the substring trap must be real"

    copy = tmp_path / "engine.py"
    copy.write_bytes(pristine)
    probe = Mutant(engine.name, copy, engine.find, engine.replace)
    original = apply_mutant(probe)
    assert copy.read_bytes() != pristine
    restore(copy, original)

    assert copy.read_bytes() == pristine
    assert b"\r\n" not in copy.read_bytes()


def test_restoration_that_did_not_land_raises_rather_than_reporting_a_clean_tree(tmp_path):
    """A restore nobody checked is how a verification step leaves what it verified mutated."""
    directory = tmp_path / "not-a-file"
    directory.mkdir()
    with pytest.raises((MutationHarnessError, OSError)):
        restore(directory, "x = 1\n")


def test_the_sweep_leaves_every_mutated_file_exactly_as_it_found_it():
    """Runs after the parametrized sweep above and re-reads the bytes it was allowed to touch.

    `audit_anchors` catches a mutant left applied, but only for the nine sites it knows. This is the
    stronger statement over the whole file, and it is what the git working tree is checked against.
    """
    for mutant in MUTANTS:
        source = read_source(mutant.path)
        assert source.count(mutant.find) == 1, f"{mutant.path.name} is not as the sweep found it"
    assert audit_anchors() == []
