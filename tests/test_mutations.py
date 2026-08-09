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
from pathlib import Path

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
    main,
    read_backup,
    read_source,
    recover_stranded_mutant,
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
    """Without this the child collects this module and every mutant fans out into another sweep.

    `--ignore` is resolved against the invocation directory, so the guard is only real if the child
    is launched from the root with an absolute path. Asserted on the child's own report of what it
    collected, not on the argument list handed to it.

    **Matched on the module, not on a substring of the node id.** A node id is
    `classname::name`, and `name` carries the parametrization id -- so a test elsewhere in the suite
    that parametrizes over tracked files produces
    `tests.test_readme_claims::..._named_anywhere...[tests\\test_mutations.py]`, and a substring
    search read that as the sweep having collected itself. Measured: it turned this red on a run
    where the child had collected nothing of the kind. The property is about which *module* ran, and
    the classname is where that lives.
    """
    _, junit = baseline_report
    collected = collected_tests(junit)
    assert collected, "the child collected nothing, so its report proves nothing"
    modules = {node.split("::", 1)[0] for node in collected}
    assert modules, "no module could be read out of the collected node ids"
    offenders = [module for module in modules if module.endswith("test_mutations")]
    assert not offenders, f"the child collected the sweep: {offenders}"

    # The narrowing above must not have narrowed to nothing. A recursion produces this node id
    # shape, so the rule is exercised on it rather than trusted.
    recursed = "tests.test_mutations::test_every_mutant_is_killed[deny_becomes_allow]"
    assert recursed.split("::", 1)[0].endswith("test_mutations"), (
        "the module rule no longer recognises a real recursion, so the assertion above is inert"
    )


def test_the_child_skips_the_sweep_even_if_the_ignore_stops_matching(tmp_path):
    """The second line of defence, exercised with the first one removed.

    `--ignore` is one string; a rename or a moved root silently ends it. The environment marker is
    what makes that degrade into a skip instead of into one recursive sweep per mutant, so it is
    tested with `--ignore` deliberately absent, the only configuration where it does anything. This
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
    # The backup goes to `tmp_path` too, never to the default. A probe over a temporary file that
    # left a backup behind would name a path that no longer exists by the next run, and recovery
    # refuses a file it cannot compare -- so the default is kept for sweeps over the real tree.
    backup = tmp_path / "backup.json"
    original = apply_mutant(probe, backup)
    assert copy.read_bytes() != pristine
    restore(copy, original, backup)

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

    `audit_anchors` catches a mutant left applied, but only for the sites it knows. This is the
    stronger statement over the whole file, and it is what the git working tree is checked against.
    """
    for mutant in MUTANTS:
        source = read_source(mutant.path)
        assert source.count(mutant.find) == 1, f"{mutant.path.name} is not as the sweep found it"
    assert audit_anchors() == []


# --------------------------------------------------------------------------------------------
# Restoration that outlives the process
# --------------------------------------------------------------------------------------------
#
# The `finally` above restores the tree only if this process reaches it. An interrupt, a harness
# stop, a timeout or a sleeping machine skips it, and the pre-mutation source -- held nowhere but
# in one local variable -- is gone with the process that held it. Five stranded mutants in one
# session. The lock already keeps durable state on disk (the owner pid) precisely so a later run
# can reason about a dead predecessor; restoration kept none, and that asymmetry is what these
# tests close.


class _KilledWhenTheMutationIsWritten:
    """A target file whose write never lands, standing in for an interrupt at that exact instant.

    Everything else `apply_mutant` asks of a path is forwarded to the real one, so the anchor audit
    and the read of the original are the real ones and only the write becomes the kill. A subprocess
    really killed mid-write would leave the same disk, and would leave this suite unable to say
    *when* it died -- which is the whole property here.
    """

    def __init__(self, real: Path) -> None:
        self._real = real

    def __fspath__(self) -> str:
        return os.fspath(self._real)

    def __str__(self) -> str:
        return str(self._real)

    @property
    def name(self) -> str:
        return self._real.name

    def is_file(self) -> bool:
        return self._real.is_file()

    def read_bytes(self) -> bytes:
        return self._real.read_bytes()

    def write_bytes(self, data: bytes) -> int:
        raise KeyboardInterrupt("killed at the instant the mutation was written")


def _a_sweep_killed_between_the_mutation_and_the_restore(
    tmp_path, body: str, find: str, replace: str, name: str = "probe"
) -> tuple[Path, Path]:
    """The disk a killed sweep leaves: the backup written, the mutant applied, no restore run.

    Built by calling the real `apply_mutant` and then simply not restoring, which is precisely what
    a SIGTERM between the two does. Hand-writing the backup would pin its file format rather than
    its meaning, and would keep passing after the format it describes stopped being the one written.
    """
    target = tmp_path / f"{name}.py"
    target.write_bytes(body.encode("utf-8"))
    backup = tmp_path / f"{name}-backup.json"
    apply_mutant(Mutant(name, target, find, replace), backup)
    return target, backup


def test_the_original_reaches_the_disk_before_the_mutation_that_replaces_it(tmp_path):
    """Backing up after mutating leaves a window in which a kill strands with no backup at all.

    That window is the bug, unfixed: it is narrow, and the five strands in one session say a narrow
    window is wide enough. So the kill is staged *at* the write of the mutation, and what is asserted
    is the disk at that instant -- both fields, not merely a file that exists. A backup that records
    the original on the way in and the mutated text on the way out would satisfy "a backup exists"
    while leaving recovery unable to tell a strand from a human's edit.
    """
    target = tmp_path / "probe.py"
    target.write_bytes(b"x = 1\n")
    backup = tmp_path / "backup.json"
    mutant = Mutant("probe", _KilledWhenTheMutationIsWritten(target), "x = 1", "x = 2")

    with pytest.raises(KeyboardInterrupt):
        apply_mutant(mutant, backup)

    assert target.read_bytes() == b"x = 1\n", "the kill was staged after the write, not at it"
    record = read_backup(backup)
    assert record.original == "x = 1\n"
    assert record.mutated == "x = 2\n"


def test_a_mutant_stranded_by_a_killed_sweep_is_put_back_on_the_next_run(tmp_path):
    """The heal. Constructed as a killed sweep leaves it, then recovered, then compared byte for byte.

    `read_source`/`restore` are on bytes throughout for the CRLF reason in the module docstring, and
    the backup is dropped only once those bytes are known to have landed -- so a strand survives its
    own failed recovery rather than being forgotten by it.
    """
    target, backup = _a_sweep_killed_between_the_mutation_and_the_restore(
        tmp_path, "x = 1\n", "x = 1", "x = 2"
    )
    assert target.read_bytes() == b"x = 2\n", "the strand this recovers was never constructed"

    assert recover_stranded_mutant(backup) == "probe"

    assert target.read_bytes() == b"x = 1\n"
    assert not backup.exists(), "a backup kept past its strand restores over the next run's work"


def test_a_tree_a_human_already_put_back_by_hand_costs_the_next_run_nothing(tmp_path):
    """`git status --porcelain src/` is the first line of `/verify`, so `git checkout` is the reflex.

    The file is then byte-for-byte the backup's original, and writing it again would change nothing.
    That is proven rather than assumed -- it is a comparison, not a guess -- so this is the one
    non-strand state that is cleared rather than refused. Refusing it would mean a human who healed
    the tree correctly is blocked from every later run until they delete a file nobody told them about.
    """
    target, backup = _a_sweep_killed_between_the_mutation_and_the_restore(
        tmp_path, "x = 1\n", "x = 1", "x = 2"
    )
    target.write_bytes(b"x = 1\n")

    assert recover_stranded_mutant(backup) is None

    assert target.read_bytes() == b"x = 1\n"
    assert not backup.exists(), "a spent backup left behind is a refusal waiting for the next run"


def test_a_tree_holding_neither_the_mutant_nor_the_original_is_refused_rather_than_clobbered(tmp_path):
    """Someone checked the file out and then worked on it. The backup is now a loaded gun.

    Writing it back destroys real work, and this repository's standing answer to an ambiguous state
    is to refuse rather than guess. The refusal keeps both the file and the backup, because the
    human needs the one to judge the other. A backup that cannot be read at all is the same
    situation reached by a different road, and is refused the same way rather than crashing out
    through a raw `JSONDecodeError`.
    """
    target, backup = _a_sweep_killed_between_the_mutation_and_the_restore(
        tmp_path, "x = 1\n", "x = 1", "x = 2"
    )
    worked_on = b"x = 3  # a human was here after the checkout\n"
    target.write_bytes(worked_on)

    with pytest.raises(MutationHarnessError, match="neither"):
        recover_stranded_mutant(backup)

    assert target.read_bytes() == worked_on, "the refusal wrote over the work it exists to protect"
    assert backup.exists(), "the refusal threw away the only copy of the pre-mutation source"

    backup.write_bytes(b"{not json at all")
    with pytest.raises(MutationHarnessError, match="could not be read"):
        recover_stranded_mutant(backup)

    # Parseable and still unusable. This one gets past `json.loads` and past the field lookups, and
    # the source it claims to hold is `None` -- so the refusal has to be on what the fields *are*
    # and not merely on whether they are there, or the comparison below crashes out through a raw
    # `AttributeError` from the one file this exists to read carefully.
    backup.write_bytes(b'{"mutant": "probe", "path": "x", "original": null, "mutated": null}')
    with pytest.raises(MutationHarnessError, match="could not be read"):
        recover_stranded_mutant(backup)


def test_a_stranded_mutant_is_recovered_before_the_anchor_audit_can_refuse_the_run(tmp_path, capsys):
    """The ordering the whole fix hangs on, and the one that is invisible in a test of recovery alone.

    `audit_anchors` is `main`'s first act and an immediate `return 1`, and a stranded mutant is
    exactly what makes it fire -- the mutant destroyed its own anchor. Recovery placed after it
    never runs in the one state it exists for: the run refuses, the tree stays broken, and every
    later run refuses the same way. A test that recovers in isolation passes under both orderings.

    Read on `main`'s effects, and on the two that disagree independently: the tree is healed, and
    the anchor problems `main` printed name only the mutant that really is stale. The stale second
    mutant is what keeps `main` from reaching `run_suite` -- no child process is launched here.
    """
    stranded, backup = _a_sweep_killed_between_the_mutation_and_the_restore(
        tmp_path, "x = 1\n", "x = 1", "x = 2", name="stranded"
    )
    stale = tmp_path / "stale.py"
    stale.write_bytes(b"y = 1\n")
    mutants = [
        Mutant("stranded", stranded, "x = 1", "x = 2"),
        Mutant("stale_by_hand", stale, "y = 3", "y = 4"),
    ]

    code = main(mutants, backup=backup, lock=tmp_path / "sweep.lock")

    assert stranded.read_bytes() == b"x = 1\n", "the strand outlived the run: recovery came too late"
    assert code == 1
    anchors = [line for line in capsys.readouterr().out.splitlines() if line.startswith("anchor:")]
    assert any("stale_by_hand" in line for line in anchors), "the audit did not run at all"
    assert not any("stranded" in line for line in anchors), (
        "the audit still saw the mutant applied, so it examined the tree before recovery healed it"
    )
