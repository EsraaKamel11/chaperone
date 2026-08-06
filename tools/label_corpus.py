"""Emits `corpus/labels.jsonl` from the corpus's recorded provenance. **It reads no draft body.**

"Blinded to label source" is operational here rather than aspirational: no detector output exists at
the time this runs, and this process imports none. What it consults is `violation_class_for` -- the
class the blind author recorded for each row before anything in this repository read it -- and the
act lane the row declares. Neither is a reading of the text.

**The labeller this replaces read the bodies for marker substrings**, and against this corpus that
is wrong in both directions. The corpus was written by an author forbidden to read `src/`, `tools/`,
`tests/` and `docs/`, precisely so its surface forms would not match anyone's marker list -- so a
genuine violation whose author never reached for a marker word is labelled compliant by such a rule,
and every arm that allows it is then scored as having succeeded. A compliant near-miss that happens
to contain a marker word fails the other way and costs an arm a false block it did not commit. The
first error is the expensive one, because it hides an escape inside a success.

A substring rule's errors also correlate with the tripwires' own vocabulary, which biases the
measurement in the direction that flatters arm 4. Provenance has its own error -- it records what an
author set out to write, not what the text achieves -- but that error does not know what the
tripwires look for and lands in no particular direction. **A biased error is worse than a larger
unbiased one**, which is the whole reason to prefer provenance. `PREREGISTRATION.md` states this as
a limit of the artifact rather than leaving it for a reader to find.

**Nothing here is asserted twice.** The coherence rule -- violating exactly when a class is named,
and that class registered -- lives in `evals/corpus.py::Label`, and `verify` reaches it by
constructing one per row. A second copy of the rule written here is the drift this project forbids.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

from chaperone.evals.corpus import (
    CORPUS_PATH,
    LABELS_PATH,
    SPLITS,
    CorpusError,
    CorpusItem,
    Label,
    load_corpus,
    load_labels,
    violation_class_for,
)


def label_row(item: CorpusItem) -> dict:
    """One label, as it is serialized. The only place a `ViolationClass` becomes a string.

    `.value` rather than the enum member: `evals/calibration.py` compares this field to a raw string
    literal, so an enum written here would work through `ViolationClass(...)` in the harness and
    silently never match in the calibration table.

    `violation_class` is always emitted, `null` for a compliant row, rather than omitted. An omitted
    key and a lost key are indistinguishable to a reader; an explicit `null` lets `load_labels`
    require the field and refuse the line that dropped it.
    """
    klass = violation_class_for(item)
    return {
        "id": item.id,
        "violating": klass is not None,
        "violation_class": klass.value if klass is not None else None,
    }


def verify(rows: list[dict], items: list[CorpusItem]) -> None:
    """Refuse to write labels that do not cover the corpus exactly once each.

    Deliberately narrow. It checks what serialization could get wrong -- coverage, duplication and
    the shape of each record -- and not what the corpus should contain, because a fixture corpus of
    two rows is a legitimate input to this tool and a "both splits present" check would reject it.
    The corpus's own composition is `tools/build_corpus.py`'s business and is verified there.

    **What it cannot catch, said plainly: a `label_row` that returns coherent but wrong labels.**
    Every check here passes for the marker-substring labeller this module replaced -- measured, it
    writes 160 well-formed labels of which 94 are wrong. Only
    `tests/evals/test_labels.py`'s two provenance tests see that, and no verification a tool
    performs on its own output ever could.

    **Every branch below is unreachable against today's callers**, which is worth stating rather
    than leaving to be assumed: `load_corpus` refuses an empty corpus and a repeated id before this
    runs, `rows` is built one-per-item so the coverage sets cannot differ, and `label_row` cannot
    return an incoherent record. They stand because a labels file with no label in it is the
    fail-open shape this whole layer exists to prevent, and a guard that holds only while a caller
    upstream stays correct is a guard that will be wrong exactly once.
    """
    if not rows:
        raise CorpusError("refusing to write a labels file with no label in it")
    for row in rows:
        Label(**row)  # the coherence rule, reached rather than restated
    labelled = [row["id"] for row in rows]
    if len(set(labelled)) != len(labelled):
        raise CorpusError("the labeller assigned more than one label to an id")
    if set(labelled) != {item.id for item in items}:
        raise CorpusError("the labels and the corpus name different rows")


def write_labels(corpus_path: Path = CORPUS_PATH, out_path: Path = LABELS_PATH) -> list[dict]:
    """Label every row of `corpus_path` and freeze the result at `out_path`.

    Both paths are parameters, and both default to the module-level constants rather than to strings
    relative to the working directory: `python tools/label_corpus.py` run from anywhere writes the
    same file, and a test can point the real tool at a corpus it controls instead of testing a
    reconstruction of it.

    `newline="\\n"` because Python's text mode translates on Windows while `.gitattributes` pins the
    tree to LF. Without it this tool rewrites all 160 line endings on every run and `git status`
    reports the frozen artifact modified by regenerating it -- identical to the reason
    `tools/build_corpus.py` passes it.

    The file is read back through `load_labels` before this returns, so a labels file the loader
    would refuse fails here rather than at whatever consumes it next.
    """
    items = load_corpus(corpus_path)
    rows = [label_row(item) for item in items]
    verify(rows, items)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    reloaded = load_labels(out_path)
    if {row["id"]: Label(**row) for row in rows} != reloaded:
        raise CorpusError(f"{out_path}: the labels written are not the labels that read back")
    return rows


def main() -> int:
    rows = write_labels()
    items = {item.id: item for item in load_corpus(CORPUS_PATH)}
    print(f"{LABELS_PATH}: {len(rows)} labels")
    for split in SPLITS:
        in_split = [row for row in rows if items[row["id"]].split == split]
        counts = collections.Counter(row["violation_class"] or "compliant" for row in in_split)
        violating = sum(1 for row in in_split if row["violating"])
        print(
            f"  {split}: {len(in_split)} rows; violating {violating}, "
            f"compliant {len(in_split) - violating}"
        )
        for klass in sorted(counts):
            print(f"      {klass}: {counts[klass]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
