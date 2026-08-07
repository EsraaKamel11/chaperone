"""Records one checker verdict per corpus row so every arm replays identical verdicts.

`corpus/blind-verdicts.jsonl` is the source, and it is the second blind artifact in this project.
It was produced by an agent that read the draft **bodies** and the three content constraints in
prose, and that read no label, no `policy/tripwires.py` and no file in this repository. That is a
fact about how it was produced, recorded here and in this task's report; no test can establish it,
exactly as no test can establish that `corpus/blind-drafts.jsonl` was written blind.

**What that blinding bought, and what it did not -- read this before quoting any agreement figure
computed against these verdicts.** The judge shares **no detector** with the corpus author: it read
bodies, not provenance, and the labels were never derived from any body. What it does share is the
**criterion**. Both prompts were written by one hand and are near-verbatim on the constraint
definitions -- author, *"does not say whether a deal is good, attractive, worth doing, a strong
opportunity..."*; judge, *"states or implies whether an investment is good, attractive, worth
doing, a strong opportunity..."*. The author encoded against a definition; the judge decoded
against the same one. Blind authorship removes shared **vocabulary**, which is what design spec
9.3's contamination control targets, and leaves shared **criterion** untouched. So agreement
between these verdicts and the labels is closer to a matched-pair result than to an independent
one, and the judge's apparent strength on this corpus has that competing explanation. Nothing here
detects it, because it lives in two prompts rather than in any artifact this repository holds.

**Why it is not derived from the tripwires.** The obvious stand-in -- run `evaluate_tripwires` and
call its answer the checker's -- makes the checker and the deterministic layer the same signal, so
arms 2, 3 and 4 stop being three verdict sources and become one. Every rung between them would then
measure the lexical table against itself, and the ladder would collapse into a single arm wearing
four names.

**What the boundary adapts.** The blind judge wrote bare class names (`advises_on_merits`) because
that is the vocabulary it was given the constraints in. Everything downstream reads
`ViolationClass` values (`content:advises_on_merits`). The translation happens once, here, and the
committed artifact holds registry values only -- so `evals/harness.py` never sees two vocabularies
and a name that fits neither fails at this boundary rather than becoming a silent non-match in a
calibration cell.

**Every refusal below writes nothing.** A recorder that emitted a partial or empty mapping would
hand every arm a verdict-free replay, and a verdict-free replay reports zero escapes -- the number
the best arm is predicted to produce. So a missing source, an empty one, a duplicate id, a row the
corpus has no counterpart for and a corpus row with no verdict are all raises before the write,
never a shorter file.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from chaperone.evals.corpus import CORPUS_PATH, INTENTS, load_corpus
from chaperone.policy.types import Family, ViolationClass

_ROOT = Path(__file__).resolve().parents[1]
BLIND_VERDICTS_PATH = _ROOT / "corpus" / "blind-verdicts.jsonl"
RECORDED_PATH = _ROOT / "corpus" / "recorded_verdicts.json"


class RecordingError(ValueError):
    """A blind verdict, or a source of them, that cannot be recorded as written."""


def content_class_for(name: str) -> ViolationClass:
    """The registered class a blind verdict's bare class name denotes.

    `INTENTS` is reused rather than copied, because it already maps the blind vocabulary to the
    registry and a second map here would be two tables to keep in step (`CLAUDE.md`: two layers
    must not drift). It is reused with a narrowing, not verbatim: `INTENTS` also holds `compliant`,
    which maps to `None`, and a fourth intent added there later would silently widen what a verdict
    may name. So the family is checked as well -- only a `content:` class is a verdict this judge's
    remit could produce, and anything else is a boundary defect rather than a class to record.
    """
    klass = INTENTS.get(name)
    if klass is None:
        raise RecordingError(
            f"blind verdict names {name!r}, which is not one of the content constraints the judge was given"
        )
    if klass.family is not Family.CONTENT:
        raise RecordingError(f"blind verdict names {name!r}, whose family is {klass.family.value}, not content")
    return klass


def recorded_row(raw: object, origin: str) -> dict:
    """One blind verdict as the harness reads it, or a refusal naming where it came from.

    The coherence rule is the one `Verdict` and `_reject_unusable` already apply at the gate: a
    violation must name a class, and a compliant answer must not. Applying it here as well is not
    redundancy -- Task 17's harness builds `Verdict`s straight from this file and never calls
    `Checker.check`, so `_reject_unusable` is not on the path and this is the only place the shape
    is examined before it becomes a replayed verdict.
    """
    if not isinstance(raw, dict):
        raise RecordingError(f"{origin}: expected a JSON object, got {type(raw).__name__}")
    try:
        violates, name, confidence, span = (
            raw["violates"],
            raw["violation_class"],
            raw["confidence"],
            raw["span"],
        )
    except KeyError as exc:
        raise RecordingError(f"{origin}: verdict is missing a required field: {exc!r}") from exc

    if type(violates) is not bool:
        raise RecordingError(f"{origin}: 'violates' is {violates!r}, and every consumer branches on it as a bool")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0.0 <= confidence <= 1.0:
        raise RecordingError(f"{origin}: confidence {confidence!r} is not a probability")
    # `span` is quoted verbatim to a human by `denial_result` while they decide whether a draft may
    # go out, and Task 20's calibration reads it off the replayed verdict. `Verdict` types it
    # `str | None` and pydantic would refuse a bad one -- but this harness builds `Verdict`s from
    # this file and nothing between here and there re-checks it, so it is checked here.
    if span is not None and not isinstance(span, str):
        raise RecordingError(f"{origin}: span is {span!r}, a {type(span).__name__}, and a span is quoted as text")
    if violates and name is None:
        raise RecordingError(f"{origin}: verdict reports a violation without naming a class")
    if not violates and name is not None:
        raise RecordingError(f"{origin}: compliant verdict names {name!r}")
    return {
        "violates": violates,
        "violation_class": content_class_for(name).value if name is not None else None,
        "confidence": float(confidence),
        "span": span,
    }


def read_blind(path: Path = BLIND_VERDICTS_PATH) -> dict[str, dict]:
    """Every blind verdict in `path`, keyed by corpus id. Raises rather than returning `{}`."""
    if not path.exists():
        raise RecordingError(f"{path}: no blind verdict source to record from")
    rows: dict[str, dict] = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        origin = f"{path.name}:{lineno}"
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RecordingError(f"{origin}: line is not readable as JSON: {exc}") from exc
        if not isinstance(raw, dict) or "id" not in raw:
            raise RecordingError(f"{origin}: verdict names no corpus id")
        if raw["id"] in rows:
            raise RecordingError(f"{origin}: id {raw['id']!r} carries more than one verdict")
        rows[raw["id"]] = recorded_row(raw, origin)
    if not rows:
        raise RecordingError(f"{path}: read no blind verdict")
    return rows


def record(
    blind_path: Path = BLIND_VERDICTS_PATH,
    corpus_path: Path = CORPUS_PATH,
    out_path: Path = RECORDED_PATH,
) -> dict[str, dict]:
    """Write the replay artifact, or raise having written nothing.

    The id sets are held equal in both directions before the write. A corpus row with no verdict
    would be graded against an absent checker on every arm; a verdict for a row the corpus does not
    hold means the two files describe different corpora, and the halves that happen to line up are
    then a coincidence nobody checked.
    """
    rows = read_blind(blind_path)
    corpus_ids = {item.id for item in load_corpus(corpus_path)}
    missing = sorted(corpus_ids - set(rows))
    extra = sorted(set(rows) - corpus_ids)
    if missing or extra:
        raise RecordingError(
            f"{blind_path.name} does not cover {corpus_path.name}: "
            f"{len(missing)} corpus rows carry no verdict {missing[:3]}, "
            f"{len(extra)} verdicts name no corpus row {extra[:3]}"
        )
    # `newline="\n"` because Python's text mode translates on Windows and `.gitattributes` pins the
    # repository to LF -- so a rebuild on the recording platform would otherwise differ from the
    # committed artifact in every line, and the byte-equality test that exists to catch drift would
    # fail for a reason that is not drift. `tools/build_corpus.py` and `tools/label_corpus.py`
    # already write this way; this is the same rule, not a third convention.
    with out_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(rows, indent=2, sort_keys=True) + "\n")
    return rows


def main() -> int:
    rows = record()
    violating = sum(1 for row in rows.values() if row["violates"])
    print(f"{RECORDED_PATH}: {len(rows)} verdicts, {violating} reporting a violation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
