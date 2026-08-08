"""The perturbation log, and what stops the committed document drifting from its generator.

`docs/PERTURBATIONS.md` is read as evidence, and a generated document nothing binds to its
generator is a document that becomes wrong the moment any cell moves. Byte equality is the binding,
which is affordable here for one reason worth stating: **no row renders a rate.** Every cell is a
boolean, an enum value or a count that pure code decides, so asserting the bytes asserts no
probabilistic quantity, and design spec 9.6's rule is untouched.

**Two tests in this module touch the committed `docs/PERTURBATIONS.md`, and the coupling is named
here because nothing enforces it.** `test_the_generator_writes_to_the_repository_...` perturbs that
file and restores it in a `finally`, and
`test_the_committed_perturbation_log_is_what_the_generator_produces` reads it. That is safe under
serial execution and **not safe under `pytest-xdist` or any parallel runner**, which this repository
does not use. The `finally` covers an exception and an assertion failure; it does not cover the
process being killed. The residual failure is loud rather than silent in every case: a stranded
perturbation leaves the binding test red and `git status` dirty, and `git checkout` recovers the
file. Anyone adding a parallel runner has to read this paragraph first.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tools.perturbation_log import (
    DOCUMENT_PATH,
    PerturbationError,
    render,
    rows,
    write_perturbation_log,
)

REPO = Path(__file__).resolve().parent.parent


def test_the_committed_perturbation_log_is_what_the_generator_produces(tmp_path: Path):
    """The binding. Production change that breaks it: any cell moving without a regenerate.

    Compared as bytes rather than as text, because the repository pins LF and a generator writing
    in text mode on Windows would produce a document that differs from the committed one in every
    line while reading identically.
    """
    fresh = tmp_path / "PERTURBATIONS.md"
    write_perturbation_log(fresh)

    assert fresh.read_bytes() == DOCUMENT_PATH.read_bytes(), (
        "docs/PERTURBATIONS.md is not what tools/perturbation_log.py produces; regenerate it"
    )


def test_a_perturbation_whose_two_cells_agree_is_refused_rather_than_published():
    """A row where nothing moved documents a safeguard firing when no safeguard fired.

    Production change that breaks this: rendering whatever `rows` returns. The document would then
    claim a perturbation was survived by a system that never noticed it.
    """
    with pytest.raises(PerturbationError, match="unperturbed and perturbed"):
        render([("a perturbation that changed nothing", "allowed=True", "allowed=True")])


def test_a_log_over_no_row_is_refused_rather_than_written(tmp_path: Path):
    """Standing check 1 applied to a document: a header with no row must not report clean.

    Production change that breaks this: rendering an empty table. Nothing is then written that a
    reader could tell apart from a run where every safeguard held.
    """
    target = tmp_path / "PERTURBATIONS.md"
    with pytest.raises(PerturbationError, match="no perturbation"):
        write_perturbation_log(target, [])
    assert not target.exists(), "a refused generation must leave no document behind"


def test_the_generator_writes_to_the_repository_and_not_to_the_directory_it_was_run_from(
    tmp_path: Path,
):
    """`Path("docs/PERTURBATIONS.md")` resolves against the process cwd, so the document lands
    wherever the tool happened to be invoked and the committed one silently goes stale.

    Production changes that break this: a relative write target, and a `main` that writes nothing.

    **The document is perturbed before the run, and that is the whole point.** Comparing the
    committed bytes against themselves after a run passes whether the generator wrote to the
    repository or never wrote anywhere at all, because the generator is deterministic and
    `not (tmp_path / "docs").exists()` is satisfied by writing nothing too. Measured: with `main`
    replaced by `pass`, that version of this test still reported green. A stale marker is written
    first so that only a real write at the repository path can restore it, and it is restored in a
    `finally` because this touches a committed file.
    """
    before = DOCUMENT_PATH.read_bytes()
    DOCUMENT_PATH.write_bytes(b"# stale, and only a write at this path can undo it\n")
    try:
        completed = subprocess.run([sys.executable, str(REPO / "tools" / "perturbation_log.py")],
                                   capture_output=True, text=True, cwd=tmp_path, check=True)

        assert not (tmp_path / "docs").exists(), f"wrote beside the caller: {completed.stdout}"
        assert DOCUMENT_PATH.read_bytes() == before, (
            "the run left the repository's document stale, so it wrote elsewhere or not at all"
        )
    finally:
        DOCUMENT_PATH.write_bytes(before)


def test_the_content_gate_rows_are_answered_by_the_content_lane_and_not_by_the_act_lane():
    """`decide` returns on an act finding before a tripwire is ever consulted, so a perturbed body
    carrying an unbacked figure exercises the act lane while the row claims the content gate.

    Measured: the first draft written for this row was "This will return 3x within four years",
    and the generated document read `act:figure_not_in_record`. Production change that breaks this:
    any content-gate perturbation whose body states a figure the record does not back.
    """
    content_rows = [row for row in rows() if row[0].startswith("Content gate")]
    assert content_rows, "the content-gate perturbations are gone, so this guard is vacuous"
    for name, _, perturbed in content_rows:
        assert "act:" not in perturbed, f"{name}: answered by the act lane, not the content gate"


def test_the_torn_tail_travels_with_the_entries_it_was_read_with():
    """`verify(entries)` defaults `torn_tail` to False, so a one-argument call always reports a
    whole log however the log was read.

    Production change that breaks this: dropping the flag `AuditStore.read_all` returned. The row
    would then claim an intact tail over a log whose last record was lost, which is the permissive
    direction for a count that charges against the send cap.
    """
    torn_rows = [row for row in rows() if "truncated" in row[0]]
    assert len(torn_rows) == 1, "the torn-tail perturbation is gone, so this guard is vacuous"
    _, unperturbed, perturbed = torn_rows[0]

    assert "torn_tail=False" in unperturbed
    assert "torn_tail=True" in perturbed
