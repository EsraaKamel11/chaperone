"""The synthetic candidate corpus the ablation is reported over, and the refusals in its loader.

Design spec 8.2 item 5: *held-out synthetic introductions, ranking metrics, labelled as a
demonstration of the protocol and never as evidence the ranker is good.* The labels here were
generated in this repository, which is the whole reason the numbers taken over them are reported
and never asserted -- and the reason the loader has to refuse a corpus it cannot read completely,
since a rate over the rows that happened to parse is a rate over a biased sample.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from chaperone.matching.filters import Eligibility, classify
from tools.build_candidates import (
    CANDIDATES_PATH, COMPLIANT_VALUES, CorpusError, REFERENCE_MANDATE, VIOLATED_AXES,
    build_candidates, load_candidates,
)


def _write(tmp_path, rows):
    path = tmp_path / "candidates.jsonl"
    path.write_bytes("\n".join(json.dumps(r) for r in rows).encode("utf-8") + b"\n")
    return path


def test_the_shipped_corpus_is_exactly_what_the_seeded_generator_produces():
    """The artifact and the code that claims to describe it cannot drift. An edited file, or a
    generator changed without regenerating, is a corpus nobody can reproduce from this repository
    -- and every number in the write-up is quoted against it."""
    shipped = [json.loads(line) for line in CANDIDATES_PATH.read_text(encoding="utf-8").splitlines()]
    assert shipped == build_candidates()


def test_every_row_declares_an_eligibility_the_filters_agree_with_on_the_clean_record():
    """The declared label is the generator's own intent and is what the ablation scores against;
    recomputing it with `classify` would score the arms against the answer one arm produced. Held
    against `classify` once, here, so a generator that emits a row it mislabels is caught rather
    than quietly moving every contamination number."""
    candidates, truth = load_candidates(CANDIDATES_PATH)
    for candidate in candidates:
        eligible = classify(candidate, REFERENCE_MANDATE)[0] is Eligibility.ELIGIBLE
        assert eligible == truth[candidate.id], candidate.id


@pytest.mark.parametrize("axis", VIOLATED_AXES)
def test_the_corpus_violates_every_eligibility_axis_the_filters_exclude_on(axis):
    """A corpus that exercises four of the five constraints scores both arms on an axis neither was
    tested against -- which is how Task 22's eight tests went blind to `geography`.

    Asserted as an effect and not as a count of rows: some ineligible party is ineligible *because
    of this axis alone*, which is what repairing the axis and watching the record become eligible
    establishes. A row violating two axes would satisfy a naive per-axis tally without ever letting
    either axis decide anything on its own.
    """
    candidates, truth = load_candidates(CANDIDATES_PATH)
    repaired = (replace(c, **{axis: COMPLIANT_VALUES[axis]}) for c in candidates if not truth[c.id])
    assert any(classify(c, REFERENCE_MANDATE)[0] is Eligibility.ELIGIBLE for c in repaired), axis


def test_an_empty_candidate_file_is_refused_rather_than_read_as_a_corpus(tmp_path):
    """An ablation over no candidates reports two arms that agree perfectly about nothing."""
    path = tmp_path / "candidates.jsonl"
    path.write_bytes(b"")
    with pytest.raises(CorpusError, match="no candidate"):
        load_candidates(path)


def test_a_row_missing_a_field_is_refused_rather_than_defaulted(tmp_path):
    """`row.get("stage")` reads an absent column as a null, and a null is a *decided* state here --
    the needs-verification bucket. A corpus with a column nobody wrote would arrive as a population
    of parties whose stage is genuinely unknown, which is a different experiment."""
    rows = build_candidates()
    del rows[3]["stage"]
    with pytest.raises(CorpusError, match="stage"):
        load_candidates(_write(tmp_path, rows))


def test_a_repeated_candidate_id_is_refused_rather_than_silently_collapsed(tmp_path):
    """Ground truth is keyed by id. A repeat overwrites one row's label with another's, so a party
    is scored against somebody else's -- and both denominators shrink by one without saying so."""
    rows = build_candidates()
    rows[7]["id"] = rows[2]["id"]
    with pytest.raises(CorpusError, match="repeated"):
        load_candidates(_write(tmp_path, rows))


def test_a_row_whose_declared_eligibility_is_not_a_boolean_is_refused(tmp_path):
    """`bool("false")` is True, and a truth mapping built through it labels every row eligible --
    a contamination of zero over a corpus in which nothing was ever ineligible."""
    rows = build_candidates()
    rows[5]["eligible"] = "false"
    with pytest.raises(CorpusError, match="eligible"):
        load_candidates(_write(tmp_path, rows))
