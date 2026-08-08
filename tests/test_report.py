"""The results page is generated, and what is guarded here is its shape rather than its numbers.

`docs/RESULTS.md` is nothing but rates, and design spec 9.6 forbids asserting one. So no test in
this module compares a published figure against a literal. What is asserted is the set of
properties a rate table can lose while every number in it stays correct: a denominator that does
not travel with its rate, a rung the ladder could not run going untabulated, an empty denominator
rendering as a flattering zero, and a measured absence disappearing instead of being published.

**Why this is not byte equality.** `tests/test_perturbation_log.py` binds its document to its
generator byte for byte and says why it may: no cell there renders a rate. Every cell here does,
so the same binding would turn each of them into an asserted probabilistic quantity, which is the
rule that document's own header cites.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from chaperone.evals.corpus import CONTROLLED_CONTEXT, CORPUS_PATH, LABELS_PATH, load_corpus, load_labels
from chaperone.evals.harness import ArmResult, build_arms, load_recorded, run_ladder

from chaperone.evals.calibration import Cell, worst_cell
from chaperone.policy.types import Family, ViolationClass

from chaperone.evals.discrimination import load_quality_scores, quality_means, run_discrimination

from tools.report import (
    ABSENT_RATE,
    DOCUMENT_PATH,
    NO_CELL_OVERSHOT,
    calibration_section,
    ladder_section,
    rate_cell,
    render,
    write_report,
)

#: The content family, named from the enum rather than retyped as strings.
CONTENT_CLASSES = frozenset(
    c.value for c in ViolationClass if c.family is Family.CONTENT
)


def test_a_rate_over_an_empty_denominator_is_published_as_absent_and_never_as_zero():
    """`ArmResult.escape_rate` is `float | None`, and None is what an empty denominator returns.

    Production change that breaks this: rendering the cell as `f"{rate:.3f}"`. On None that raises
    `TypeError`; on a `rate or 0.0` repair it publishes **0.000**, which reads as an arm that let
    nothing through when it is an arm that was handed nothing to let through. Task 23 made the
    absence explicit one layer down precisely so this layer could not spend it.
    """
    nothing_to_escape = ArmResult("an arm scored over no violating row", 0, 30, 0, 0)
    assert nothing_to_escape.escape_rate is None, "the fixture no longer has an empty denominator"

    cell = rate_cell(nothing_to_escape.escape_rate, nothing_to_escape.n_violating)

    assert "0.000" not in cell, f"an empty denominator published a rate of zero: {cell!r}"
    assert ABSENT_RATE in cell, f"an empty denominator published no absence marker: {cell!r}"


def _eval_ladder():
    items = load_corpus(CORPUS_PATH, split="eval")
    labels = load_labels(LABELS_PATH)
    return run_ladder(items, labels, CONTROLLED_CONTEXT, build_arms(load_recorded()))


def test_the_ladder_table_reports_the_rung_that_could_not_run_beside_the_rungs_that_did():
    """`Ladder` carries `absent` so that no consumer can tabulate the rungs without it.

    Production change that breaks this: iterating `ladder.results` and never reading
    `ladder.absent`. The table then presents three rungs as though three rungs were the design,
    and a reader counting them has no way to learn that rung 1 has no verdict source. That is the
    exact reading `Ladder`'s docstring names this task in order to prevent.
    """
    ladder = _eval_ladder()
    assert ladder.absent, "no rung is absent, so this guard would pass vacuously"

    section = "\n".join(ladder_section(ladder))

    for result in ladder:
        assert result.name in section, f"the rung {result.name!r} was not tabulated"
    for absent in ladder.absent:
        assert absent in section, f"the absent rung {absent!r} is missing from the table"


#: A table in which every content cell delivered more agreement than it claimed, which is the shape
#: Task 20 measured on this corpus. `worst_cell` answers None over it.
UNDERCONFIDENT_CELLS = [
    Cell("content:advises_on_merits", 15, 0.930, 1.0, 0.0082),
    Cell("content:forward_looking_return", 15, 0.883, 1.0, 0.0284),
]

#: The same table with one cell whose stated confidence overshoots its observed agreement.
OVERSHOOTING_CELLS = [
    *UNDERCONFIDENT_CELLS,
    Cell("content:negotiates_terms", 15, 0.891, 0.600, 0.1900),
]


def test_a_checker_that_overshoots_nowhere_publishes_that_absence_rather_than_omitting_the_line():
    """`worst_cell` returns None on this corpus, and None is a measurement rather than a gap.

    Production change that breaks this: `worst = worst_cell(cells)` followed by `if worst:` and no
    else. The document then falls silent exactly where the reader is looking for the cell that
    justifies the tier-2 ceiling, and silence there reads as an omission rather than as the finding
    it is. The other repair is worse: a `max` over the gap always answers, so it would name the
    **best**-calibrated row in the table and the sentence beside it would be false.
    """
    assert worst_cell(UNDERCONFIDENT_CELLS) is None, "the fixture no longer overshoots nowhere"

    section = "\n".join(calibration_section(UNDERCONFIDENT_CELLS))

    assert NO_CELL_OVERSHOT in section, f"the measured absence was not published: {section!r}"


def test_a_cell_that_does_overshoot_is_named_rather_than_reported_as_no_overshoot():
    """The input that makes the two branches disagree, so the guard above is not a constant string.

    Production change that breaks this: publishing `NO_CELL_OVERSHOT` unconditionally. That version
    passes the test above on every input, including a table whose checker was badly overconfident,
    which is the one result the ceiling would actually have evidence from.
    """
    worst = worst_cell(OVERSHOOTING_CELLS)
    assert worst is not None, "the fixture no longer overshoots anywhere"

    section = "\n".join(calibration_section(OVERSHOOTING_CELLS))

    assert worst.violation_class in section, "the overshooting cell was not named"
    assert NO_CELL_OVERSHOT not in section, "a table with an overshooting cell reported no overshoot"


#: The substitution the brief shipped: one constant score for every row. Named here so the guard
#: below can show that it changes the answer rather than merely asserting that it would.
CONSTANT_SCORE = 0.85


def test_the_discrimination_section_ranks_the_shipped_judge_scores_rather_than_a_constant():
    """A constant score gives an AUC of exactly 0.5, which is the value prediction 4 predicted.

    Production change that breaks this: `scores = {i.id: CONSTANT_SCORE for i in items}` in place
    of the shipped judge's scores. Every row then ties with every other, the AUC is 0.5 by
    construction, the pre-registered null passes having ranked nothing, and the document publishes
    that pass. `score_by_id` refuses a **missing** score for this reason; a constant one is the
    same defect arriving through a door that module cannot close.

    **The reference is the library's own answer, deliberately.** Standing check 8: this generator
    must re-derive nothing `discrimination.py` already computes, so the property under test is
    delegation, and the only honest reference for delegation is the thing being delegated to. The
    second assertion is what stops that being circular: it constructs the input on which the two
    disagree, so a generator that computed its own scores could not satisfy both.
    """
    items = load_corpus(CORPUS_PATH)
    labels = load_labels(LABELS_PATH)
    shipped = run_discrimination(items, labels, quality_means(load_quality_scores()))
    flat = run_discrimination(items, labels, {item.id: CONSTANT_SCORE for item in items})

    assert flat.auc != shipped.auc, (
        "a constant score already agrees with the shipped scores, so this guard is vacuous"
    )

    assert f"{shipped.auc:.3f}" in render(), (
        "the discrimination section does not publish the AUC of the shipped judge scores"
    )


#: A published rate: three decimals, which is what `rate_cell` and `discrimination_row` emit.
PUBLISHED_RATE = re.compile(r"\d\.\d{3}")


def _tables(document: str) -> list[tuple[list[str], list[str]]]:
    """Every markdown table in `document`, as its header cells and its body rows."""
    tables: list[tuple[list[str], list[str]]] = []
    lines = document.splitlines()
    for index, line in enumerate(lines):
        if not set(line.replace("|", "").replace("-", "").strip()) and "---" in line:
            header = [c.strip() for c in lines[index - 1].strip().strip("|").split("|")]
            body: list[str] = []
            for row in lines[index + 1:]:
                if not row.startswith("| "):
                    break
                body.append(row)
            tables.append((header, body))
    return tables


def test_every_table_publishing_a_rate_publishes_the_denominator_it_was_taken_over():
    """A rate quoted without its denominator silently invites the wrong one.

    The denominators here are not one number: the act scope counts over 5 rows per split, the
    content scope over 45, the all-classes scope over 50, and the false-block denominator is 30 in
    all three. `ArmResult` carries both counts beside both rates for exactly this reason, and a
    generator is the layer where that pairing is most easily dropped, because the table looks tidier
    without it.

    Production change that breaks this: `rate_cell` returning `f"{rate:.3f}"` with the `(n=...)`
    removed, or the calibration table losing its `n` column. Measured with the `(n={n})` suffix
    deleted: eight of the document's tables lose their denominator and this fails naming them.
    """
    tables = _tables(render())
    assert tables, "no table was parsed out of the document, so this guard would be vacuous"

    rate_bearing = [(h, b) for h, b in tables if any(PUBLISHED_RATE.search(r) for r in b)]
    assert rate_bearing, "no table publishes a rate, so this guard would be vacuous"

    for header, body in rate_bearing:
        named_column = any(cell.strip().lower().startswith("n") for cell in header)
        inline = all("(n=" in row for row in body if PUBLISHED_RATE.search(row))
        assert named_column or inline, (
            f"a table publishing a rate carries no denominator: {header} / {body[:1]}"
        )


REPO = Path(__file__).resolve().parent.parent
PREREGISTRATION = REPO / "PREREGISTRATION.md"

#: A prediction heading in the pre-registration: `**1. Act-class escape rate ...`
PREDICTION_HEADING = re.compile(r"^\*\*(\d)\. ", re.M)


def _pre_registered_predictions() -> list[str]:
    """The predictions the pre-registration actually records, read from that document.

    Sourced from `PREREGISTRATION.md` rather than from a tuple in `tools/report.py`, because a
    count the generator keeps for itself is a count that agrees with the generator by construction.
    Standing check 3: the reference must not be computed the same way as the code under test.
    """
    body = PREREGISTRATION.read_text(encoding="utf-8").split("## Predictions, recorded before")[1]
    return PREDICTION_HEADING.findall(body.split("## Interpretation")[0])


def test_every_pre_registered_prediction_is_adjudicated_rather_than_only_the_ones_that_held():
    """§9.4 pre-commits to reporting a failed prediction as a failed prediction.

    Four of the five failed or landed on equality on this corpus, so a document that published
    only the adjudications it liked would publish one row. Production change that breaks this:
    emitting a prediction's row only where its outcome is `held`, or dropping the section and
    leaving each outcome to be inferred from the tables above it. Both leave a reader counting
    successes rather than predictions.
    """
    numbers = _pre_registered_predictions()
    assert len(numbers) == 5, f"the pre-registration no longer records five predictions: {numbers}"

    document = render()
    for number in numbers:
        row = [l for l in document.splitlines() if l.startswith(f"| {number} |")]
        assert len(row) == 1, f"prediction {number} is not adjudicated in exactly one row: {row}"
        assert "held" in row[0].lower() or "failed" in row[0].lower(), (
            f"prediction {number} is tabulated without an outcome: {row[0]}"
        )


def test_the_generator_writes_beside_the_repository_and_not_beside_the_directory_it_was_run_from(
    tmp_path: Path,
):
    """`Path("docs/RESULTS.md")` resolves against the process cwd, so the document lands wherever
    the tool happened to be invoked and the committed one silently goes stale.

    Production changes that break this: a relative write target, and a `main` that writes nothing.

    **A marker is written before the run, and that is the whole point.** `not (tmp_path /
    "docs").exists()` is satisfied by a generator that wrote nothing anywhere, so on its own it
    passes a `main` replaced by `pass`. Only a real write at the repository path can displace the
    marker.

    **What is deliberately not asserted: the bytes that replaced it.** `tests/test_perturbation_log.py`
    compares its committed document byte for byte and says why it may, namely that no cell there is
    a rate. Every cell here is one, so the same comparison would make every rate in the document an
    asserted quantity, which is the rule design spec 9.6 states and that document's own header
    cites. What is asserted is that the marker is gone and that a results document stands where it
    stood.

    This touches a committed file and restores it in a `finally`. Same coupling, and the same
    caveat: safe under serial execution and not under a parallel runner, which this repository does
    not use.
    """
    marker = b"# stale, and only a write at this path can undo it\n"
    before = DOCUMENT_PATH.read_bytes()
    DOCUMENT_PATH.write_bytes(marker)
    try:
        completed = subprocess.run([sys.executable, str(REPO / "tools" / "report.py")],
                                   capture_output=True, text=True, cwd=tmp_path, check=True)

        assert not (tmp_path / "docs").exists(), f"wrote beside the caller: {completed.stdout}"
        written = DOCUMENT_PATH.read_bytes()
        assert written != marker, (
            "the run left the marker standing, so it wrote elsewhere or it wrote nothing"
        )
        assert written.startswith(b"# Results"), (
            f"the repository's document is not a results page after the run: {written[:60]!r}"
        )
    finally:
        DOCUMENT_PATH.write_bytes(before)


def test_the_document_is_written_with_the_line_endings_the_repository_pins(tmp_path: Path):
    """`.gitattributes` pins this repository to LF and Python's text mode translates on Windows.

    Production change that breaks this: `path.write_text(document, encoding="utf-8")`. The
    regenerated document then differs from the committed one in every line while reading
    identically, which has flipped a file in this repository three times.
    """
    target = tmp_path / "RESULTS.md"
    write_report(target)

    assert b"\r\n" not in target.read_bytes(), "the document was written with CRLF line endings"


def test_the_calibration_denominators_reconcile_with_the_split_they_are_claimed_over():
    """A cell counted over a filtered population describes a checker nobody asked about.

    `harness.py` runs the checker on every draft precisely so calibration is not computed on a
    tripwire-negative selection, which is a different population from the one the checker is
    claimed to be calibrated over. This generator is the layer where that would be undone silently,
    by calibrating the rows that happened to be convenient.

    Production change that breaks this: calibrating over anything narrower than the eval split, for
    instance the rows arm 4 blocked. The reference comes from the corpus and the labels rather than
    from anything `tools/report.py` computes.
    """
    items = load_corpus(CORPUS_PATH, split="eval")
    labels = load_labels(LABELS_PATH)
    content_rows = sum(1 for i in items if labels[i.id].violation_class in CONTENT_CLASSES)

    table = [t for t in _tables(render()) if t[0][:2] == ["Class", "n"]]
    assert len(table) == 1, f"the calibration table is not identifiable: {[t[0] for t in table]}"
    counts = {r.split("|")[1].strip().strip("`"): int(r.split("|")[2]) for r in table[0][1]}

    assert sum(counts.values()) == len(items), (
        f"the cells total {sum(counts.values())} over an eval split of {len(items)}"
    )
    assert sum(n for k, n in counts.items() if k in CONTENT_CLASSES) == content_rows, (
        "the content cells do not total the corpus's content-violating rows"
    )
