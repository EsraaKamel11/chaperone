"""The synthetic candidate corpus the matching ablation is reported over.

Design spec 8.2 item 5: *held-out synthetic introductions, ranking metrics, labelled as a
demonstration of **the protocol** and never as evidence the ranker is good.* Every label below was
written by this generator, so a ranking metric taken over them says what the two matching
architectures do to each other and nothing whatever about the world. That is why the numbers are
reported and never asserted.

**The label is the generator's own intent, not `classify`'s answer.** A corpus labelled by running
the filters would score the hard-exclusion arm against the predicate that arm is built on, and its
contamination would be zero for a reason that has nothing to do with filtering being right.
`tests/matching/test_candidate_corpus.py` holds the two against each other once, on the clean
record, so a mislabelled row is caught rather than quietly moving every number downstream.

The population follows design spec 8.1's regime: at this N the relationally hot contacts are
frequently the ones who do not fit the mandate, so the eligible parties here are colder than the
ineligible ones. That is what gives a softly-weighted arm something to get wrong.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

from chaperone.matching.filters import ELIGIBILITY_FIELDS, Candidate, Mandate

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES_PATH = ROOT / "corpus" / "candidates.jsonl"

#: The seed is recorded here rather than passed at the command line, so the shipped artifact is
#: reproducible from the repository alone and a test can demand it back byte for byte.
SEED = 20260805

REFERENCE_MANDATE = Mandate(check_size_min="5000000", stage="Series A", sector="fintech",
                            geography="US", consented_jurisdictions=frozenset({"US"}))

#: The axes are `filters.ELIGIBILITY_FIELDS` and not a list retyped here: an axis added to
#: `classify` and forgotten in this corpus would leave both arms scored on a population that never
#: exercises it.
VIOLATED_AXES = ELIGIBILITY_FIELDS

COMPLIANT_VALUES = {
    "check_size_max": "25000000",
    "stage": "Series A",
    "sector": "fintech",
    "geography": "US",
    "jurisdiction": "US",
}
VIOLATING_VALUES = {
    "check_size_max": "250000",
    "stage": "Series C",
    "sector": "biotech",
    "geography": "EU",
    "jurisdiction": "DE",
}

_ROW_FIELDS = ("id", *ELIGIBILITY_FIELDS, "days_since_touch", "prior_passes", "eligible")


class CorpusError(Exception):
    """A candidate corpus that cannot be generated, or cannot be read completely, as written."""


def build_candidates(seed: int = SEED) -> list[dict]:
    """Sixty eligible parties nobody has called recently, forty ineligible ones who were.

    Each ineligible row breaks exactly one axis, cycling through all of them, so no axis can be the
    one the ablation never tested and no row can be excluded for two reasons at once -- a row
    violating two constraints would let either one carry the exclusion the other was credited with.
    """
    unpriced = [axis for axis in VIOLATED_AXES
                if axis not in COMPLIANT_VALUES or axis not in VIOLATING_VALUES]
    if unpriced:
        raise CorpusError(
            f"no compliant/violating value for {unpriced!r}; an eligibility axis added to the "
            "filters has to be given both here before the corpus can exercise it"
        )
    rng = random.Random(seed)
    rows = []
    for i in range(60):
        rows.append(_row(f"g{i}", rng.randrange(60, 366), rng.randrange(0, 4), True, {}))
    for i in range(40):
        axis = VIOLATED_AXES[i % len(VIOLATED_AXES)]
        rows.append(
            _row(f"b{i}", rng.randrange(0, 121), rng.randrange(0, 4), False,
                 {axis: VIOLATING_VALUES[axis]})
        )
    return rows


def _row(cid: str, days: int, passes: int, eligible: bool, violation: dict) -> dict:
    row = {"id": cid, **COMPLIANT_VALUES, "days_since_touch": days, "prior_passes": passes,
           "eligible": eligible}
    row.update(violation)
    return row


def load_candidates(path: Path = CANDIDATES_PATH) -> tuple[list[Candidate], dict[str, bool]]:
    """Every candidate in `path` with its label. Raises rather than returning a partial corpus.

    A corpus read short is the failure that matters here: both of the ablation's denominators are
    counted over this list, so a row dropped for being unreadable narrows them precisely on the
    rows nobody could parse, and the result describes a biased sample while reading as the whole.
    Same rule as `evals/corpus.py`, for the same reason.
    """
    parsed: list[tuple[int, dict]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CorpusError(f"{path}:{number}: not readable as JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise CorpusError(f"{path}:{number}: expected an object, got {type(row).__name__}")
        parsed.append((number, row))
    if not parsed:
        raise CorpusError(f"{path}: read no candidate row; an ablation over an empty population "
                          "compares two arms that agree perfectly about nothing")

    candidates: list[Candidate] = []
    truth: dict[str, bool] = {}
    for number, row in parsed:
        for field_name in _ROW_FIELDS:
            if field_name not in row:
                raise CorpusError(
                    f"{path}:{number}: row carries no {field_name!r}. A column nobody wrote is not "
                    "a value nobody knows -- null is a decided state here, and reading an absent "
                    "field as one would silently change the experiment"
                )
        if not isinstance(row["eligible"], bool):
            raise CorpusError(
                f"{path}:{number}: {'eligible'!r} is {row['eligible']!r}, not a boolean. "
                'bool("false") is True, and a truth mapping built through it labels every row '
                "eligible -- a contamination of zero over a corpus that held nobody ineligible"
            )
        if row["id"] in truth:
            raise CorpusError(
                f"{path}:{number}: repeated candidate id {row['id']!r}. Truth is keyed by id, so a "
                "repeat scores one party against another's label and shrinks both denominators"
            )
        truth[row["id"]] = row["eligible"]
        candidates.append(Candidate(**{f: row[f] for f in _ROW_FIELDS if f != "eligible"}))
    return candidates, truth


def main() -> int:
    rows = build_candidates()
    payload = "".join(json.dumps(row) + "\n" for row in rows)
    CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Bytes, and an explicit newline. `write_text` applies the platform's newline translation,
    # which on Windows converts the artifact to CRLF against `.gitattributes`' `eol=lf` pin.
    CANDIDATES_PATH.write_bytes(payload.encode("utf-8"))
    print(f"{CANDIDATES_PATH}: {len(rows)} candidates, seed {SEED}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
