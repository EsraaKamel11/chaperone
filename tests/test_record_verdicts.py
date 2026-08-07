"""The boundary where a blind judge's vocabulary becomes the registry's, and what it refuses."""
from __future__ import annotations

import json

import pytest

from chaperone.evals.corpus import CORPUS_PATH
from chaperone.policy.types import ViolationClass
from tools.record_verdicts import (
    BLIND_VERDICTS_PATH,
    RECORDED_PATH,
    RecordingError,
    content_class_for,
    record,
    recorded_row,
)


def test_a_bare_content_class_name_is_adapted_to_the_registered_class():
    """The blind judge wrote `advises_on_merits`; every consumer here reads `content:...`."""
    assert content_class_for("advises_on_merits") is ViolationClass.ADVISES_ON_MERITS
    assert content_class_for("negotiates_terms") is ViolationClass.NEGOTIATES_TERMS
    assert content_class_for("forward_looking_return") is ViolationClass.FORWARD_LOOKING_RETURN


def test_a_name_outside_the_judges_remit_is_refused_rather_than_recorded():
    """`compliant` is a name the map holds and no class; an act-class was never in the remit."""
    for name in ("compliant", "act:figure_not_in_record", "content:advises_on_merits", "advises"):
        with pytest.raises(RecordingError):
            content_class_for(name)


def test_a_violation_that_names_no_class_is_refused_rather_than_recorded():
    """The engine denies on this shape; the recorder must not manufacture one from a source file."""
    with pytest.raises(RecordingError):
        recorded_row({"violates": True, "violation_class": None, "confidence": 0.9, "span": "x"}, "row 1")


def test_a_compliant_verdict_naming_a_class_is_refused():
    with pytest.raises(RecordingError):
        recorded_row(
            {"violates": False, "violation_class": "negotiates_terms", "confidence": 0.9, "span": None},
            "row 1",
        )


def test_a_missing_blind_source_raises_rather_than_writing_an_empty_artifact(tmp_path):
    """A recorder that wrote `{}` gives every arm a verdict-free replay and zero escapes."""
    out = tmp_path / "recorded_verdicts.json"
    with pytest.raises(RecordingError):
        record(blind_path=tmp_path / "absent.jsonl", out_path=out)
    assert not out.exists()


def test_a_source_that_does_not_cover_every_corpus_row_is_refused(tmp_path):
    """A short source is a silently smaller denominator on every arm."""
    lines = BLIND_VERDICTS_PATH.read_text(encoding="utf-8").splitlines()
    short = tmp_path / "short.jsonl"
    short.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    out = tmp_path / "recorded_verdicts.json"
    with pytest.raises(RecordingError):
        record(blind_path=short, corpus_path=CORPUS_PATH, out_path=out)
    assert not out.exists()


def test_the_committed_replay_is_a_rebuild_of_the_committed_blind_verdicts(tmp_path):
    """Byte equality, so the artifact CI replays cannot drift from the source it was recorded from."""
    rebuilt = tmp_path / "recorded_verdicts.json"
    record(out_path=rebuilt)
    assert rebuilt.read_bytes() == RECORDED_PATH.read_bytes()


def test_every_recorded_class_is_a_registered_content_class():
    recorded = json.loads(RECORDED_PATH.read_text(encoding="utf-8"))
    named = {row["violation_class"] for row in recorded.values() if row is not None}
    named.discard(None)
    assert named == {
        ViolationClass.ADVISES_ON_MERITS.value,
        ViolationClass.NEGOTIATES_TERMS.value,
        ViolationClass.FORWARD_LOOKING_RETURN.value,
    }
