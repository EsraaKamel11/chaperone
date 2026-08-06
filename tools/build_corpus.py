"""Freezes `corpus/drafts.jsonl` from `corpus/blind-drafts.jsonl`. It writes no draft text.

Design spec 9.3 requires that violating drafts' surface forms not come from the tripwire author's
vocabulary. A generator that paraphrases seed violations was the plan; a stand-in returning the seed
plus " (N)" is not a paraphrase, and composed with the later stand-ins it would have made the
artifact's own pre-registered predictions true by construction. So the drafts were written blind
instead -- by an author given the three constraints in prose and forbidden this repository -- and
this tool frames them.

**Framing means: an id, a split, a constant thread, a record, and the provenance carried through.**
Every body is copied verbatim; `tests/evals/test_corpus.py` holds the multiset of `(body, intent)`
pairs equal between input and output, so a single edited character fails the suite.

**The split needs no stored seed.** It is stratified by intent, ordered within each intent by a
digest of the body and assigned alternately, so it is a function of the bodies alone. Ordering by
digest decorrelates it from whatever order the author wrote in; alternating splits an intent of even
size exactly in half and one of odd size to within a row, where `random() < 0.5` can leave a stratum
out of a split entirely -- and a split missing a stratum is a rate over an empty denominator in
Tasks 16 and 17. All four intents here are of even size, so all four divide exactly.

**The record exists to make the act lane controllable**, and holds nothing else. `decide` returns on
an act finding before any tripwire runs, so a content-class row carrying an unbacked figure would
test the act lane instead of the class it was written for. Every figure `figures_in` reads out of a
body is written back as a record field -- except on the rows that deliberately declare
`act:figure_not_in_record`, whose record is empty. Nothing is guessed: `verify` runs the two
predicates `gates/engine.py::decide` evaluates before any tripwire -- the pair Task 17's arm 4 is
specified to evaluate -- over every row, and refuses to write a corpus where a row's observed act
findings differ from what it declares.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from chaperone.evals.corpus import (
    ACT_LANE_CLEAN,
    BLIND_DRAFTS_PATH,
    CORPUS_PATH,
    INTENTS,
    SPLITS,
    CorpusError,
    act_findings_for,
    declared_act_class,
    item_from_row,
)
from chaperone.policy.canonical import figures_in
from chaperone.policy.types import ViolationClass

# One inbound message on every row. A constant rather than 160 authored threads: the corpus is a
# controlled comparison over bodies and records, and a thread varying per row is a second variable
# nothing here would hold. The cost is that the quality judge's "fit" dimension varies with nothing,
# which `evals/corpus.py` states as a limit.
THREAD = [{"role": "investor", "body": "Following up on our conversation."}]
JURISDICTION = "US"
DOMAIN = "example.test"
TOOL_NAME = "send_message"

COMPLIANT_INTENT = "compliant"
UNBACKED_PER_SPLIT = 5
UNBACKED_LANE = ViolationClass.FIGURE_NOT_IN_RECORD.value


def read_blind_drafts(source: Path) -> list[dict]:
    """The blind author's rows, refusing anything that is not one."""
    drafts: list[dict] = []
    for lineno, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        origin = f"{source.name}:{lineno}"
        try:
            raw = json.loads(line)
            draft = {"body": raw["body"], "intent": raw["intent"]}
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise CorpusError(f"{origin}: not a blind draft: {exc!r}") from exc
        if draft["intent"] not in INTENTS:
            raise CorpusError(f"{origin}: intent {draft['intent']!r} is not one this corpus knows")
        drafts.append(draft)
    if not drafts:
        raise CorpusError(f"{source}: no blind draft to frame")
    return drafts


def assign_splits(drafts: list[dict]) -> None:
    for intent in sorted({draft["intent"] for draft in drafts}):
        group = sorted(
            (draft for draft in drafts if draft["intent"] == intent),
            key=lambda draft: hashlib.sha256(draft["body"].encode("utf-8")).hexdigest(),
        )
        for position, draft in enumerate(group):
            draft["split"] = SPLITS[position % len(SPLITS)]


def assign_act_lanes(drafts: list[dict]) -> None:
    """Declare which rows deliberately carry an act-class, and refuse to guess if there are too few.

    Taken from the compliant intents only, so no row instances two classes at once and every label
    stays single-valued. Taken per split, because Task 17's prediction 1 is measured on eval and a
    prediction with an empty denominator is not a measurement.
    """
    for draft in drafts:
        draft["act_lane"] = ACT_LANE_CLEAN
    for split in SPLITS:
        candidates = [
            draft
            for draft in drafts
            if draft["split"] == split
            and draft["intent"] == COMPLIANT_INTENT
            and figures_in(draft["body"])
        ]
        if len(candidates) < UNBACKED_PER_SPLIT:
            raise CorpusError(
                f"split {split!r} has {len(candidates)} compliant figure-bearing drafts, "
                f"fewer than the {UNBACKED_PER_SPLIT} the act lane needs"
            )
        for draft in candidates[:UNBACKED_PER_SPLIT]:
            draft["act_lane"] = UNBACKED_LANE


def row_for(index: int, draft: dict) -> dict:
    record = (
        {
            f"figure_{position}": format(value, "f")
            for position, value in enumerate(sorted(figures_in(draft["body"])))
        }
        if draft["act_lane"] == ACT_LANE_CLEAN
        else {}
    )
    return {
        "id": f"c{index:04d}",
        "split": draft["split"],
        "thread": THREAD,
        "body": draft["body"],
        "cited_fields": [],
        "jurisdiction": JURISDICTION,
        "domain": DOMAIN,
        "tool_name": TOOL_NAME,
        "record": record,
        "intent": draft["intent"],
        "act_lane": draft["act_lane"],
    }


def verify(rows: list[dict]) -> None:
    """Refuse to write a corpus whose act lane does not do what each row declares.

    Every row goes through `item_from_row`, so the vocabulary the loader enforces is enforced before
    anything is written rather than discovered when the file is read back. Then the observed act
    findings are compared against the declared lane in both directions: a clean row that trips
    anything, and a declaring row that trips nothing or trips something else, are both refusals.
    """
    for row in rows:
        item = item_from_row(row, f"{row.get('id', '<no id>')}")
        observed = {finding.violation_class for finding in act_findings_for(item)}
        declared = {klass for klass in (declared_act_class(item),) if klass is not None}
        if observed != declared:
            raise CorpusError(
                f"{item.id}: declares act lane {item.act_lane!r} but carries "
                f"{sorted(klass.value for klass in observed)}"
            )
    if len({row["id"] for row in rows}) != len(rows):
        raise CorpusError("the corpus assigns one id to more than one row")
    for split in SPLITS:
        if not any(row["split"] == split for row in rows):
            raise CorpusError(f"split {split!r} is empty")


def build(source: Path = BLIND_DRAFTS_PATH, out_path: Path = CORPUS_PATH) -> list[dict]:
    drafts = read_blind_drafts(source)
    assign_splits(drafts)
    assign_act_lanes(drafts)
    rows = [row_for(index, draft) for index, draft in enumerate(drafts)]
    verify(rows)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # `newline="\n"` because Python's text mode translates on Windows and `.gitattributes` pins the
    # tree to LF: without it this tool rewrites every line ending on every run and `git status`
    # reports the frozen artifact modified by regenerating it.
    with out_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return rows


def main() -> int:
    rows = build()
    print(f"{CORPUS_PATH}: {len(rows)} rows")
    for split in SPLITS:
        in_split = [row for row in rows if row["split"] == split]
        lanes = sum(1 for row in in_split if row["act_lane"] != ACT_LANE_CLEAN)
        intents = ", ".join(
            f"{intent} {sum(1 for row in in_split if row['intent'] == intent)}" for intent in INTENTS
        )
        print(f"  {split}: {len(in_split)} rows ({intents}); declaring an act-class: {lanes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
