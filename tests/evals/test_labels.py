"""What `corpus/labels.jsonl` is, and the one thing a shape test cannot establish about it.

The five tests the brief specifies check the labels' **shape**: an id per corpus row, no detector
vocabulary, a class on every violating row and none on any compliant one, both splits populated.
Every one of them passes for a labeller that labels the wrong rows -- including the marker-substring
labeller this task deleted, which mislabels a paraphrase that dropped its marker as compliant and so
scores an escape as a success. A shape test cannot see that, and neither can a round-trip: regenerate
the file with a wrong labeller and it reproduces its own wrong answer byte for byte.

**Two tests carry the content claim, and they work at different layers.**

- `test_a_labeller_that_read_the_body_could_not_have_produced_these_labels` is the universal. It
  rebuilds the corpus with **every body replaced by one constant string** and demands the labeller
  return the shipped file unchanged. Any labeller that is a function of the body alone must emit one
  label value over that input; the shipped file carries five. So the test is non-vacuous by
  construction rather than by a marker list that would have to be kept current.
- `test_provenance_beats_the_body_in_both_directions_of_the_error` is the direction. It drives the
  real tool over two rows whose bodies contradict their recorded intent, one each way, and names
  which of the two errors is the expensive one.

`tests/evals/test_corpus.py::test_the_violation_class_is_read_from_provenance_and_never_from_the_body`
holds the layer below -- `violation_class_for` itself. These tests hold the tool and the artifact. A
wrong `violation_class_for` is not caught here and is not meant to be; that is the split, stated
rather than left for a reader to discover.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chaperone.evals.corpus import (
    CORPUS_PATH,
    LABELS_PATH,
    CorpusError,
    Label,
    load_corpus,
    load_labels,
    violation_class_for,
)
from tools import label_corpus


def _label_row(**overrides) -> dict:
    row = {"id": "c0000", "violating": False, "violation_class": None}
    row.update(overrides)
    return row


def _write_labels(tmp_path: Path, *rows: dict) -> Path:
    path = tmp_path / "labels.jsonl"
    path.write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8", newline="\n"
    )
    return path


def _corpus_row(**overrides) -> dict:
    row = {
        "id": "c0000",
        "split": "dev",
        "thread": [{"role": "investor", "body": "Following up on our conversation."}],
        "body": "Attached.",
        "cited_fields": [],
        "jurisdiction": "US",
        "domain": "example.test",
        "tool_name": "send_message",
        "record": {},
        "intent": "compliant",
        "act_lane": "clean",
    }
    row.update(overrides)
    return row


def _write_corpus(tmp_path: Path, rows: list[dict], name: str = "drafts.jsonl") -> Path:
    path = tmp_path / name
    path.write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8", newline="\n"
    )
    return path


# --- the brief's five ------------------------------------------------------------------------------


def test_every_corpus_item_has_exactly_one_label():
    items = load_corpus(CORPUS_PATH)
    labels = load_labels(LABELS_PATH)
    assert {i.id for i in items} == set(labels)


def test_the_label_file_carries_no_arm_or_detector_identifiers():
    """Blinding is to label source. A label must not know which detector saw the draft.

    **Substring over each key, not `in record`.** The brief's form tests dict-*key* membership, so
    it asks whether a key is named exactly `tripwire` -- and a key named `tripwire_hit`,
    `arm_id` or `checker_confidence` passes it untouched. Every name a real leak would plausibly
    carry is a compound, so the verbatim form is satisfied by precisely the records it is meant to
    reject. Harmless against the shipped file, which carries three keys and no detector vocabulary;
    fixed rather than renamed, because the name states the property correctly and the assertion did
    not.
    """
    for line in LABELS_PATH.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        for forbidden in ("arm", "detector", "checker", "tripwire", "verdict", "confidence"):
            leaked = [key for key in record if forbidden in key]
            assert not leaked, f"{forbidden!r} appears in label keys {leaked}"


def test_a_violating_label_names_its_class():
    labels = load_labels(LABELS_PATH)
    violating = [l for l in labels.values() if l.violating]
    assert violating
    assert all(l.violation_class is not None for l in violating)


def test_a_compliant_label_names_no_class():
    labels = load_labels(LABELS_PATH)
    compliant = [l for l in labels.values() if not l.violating]
    assert compliant
    assert all(l.violation_class is None for l in compliant)


def test_both_splits_contain_violating_and_compliant_items():
    labels = load_labels(LABELS_PATH)
    for split in ("dev", "eval"):
        ids = {i.id for i in load_corpus(CORPUS_PATH, split=split)}
        subset = [labels[i] for i in ids]
        assert any(l.violating for l in subset)
        assert any(not l.violating for l in subset)


# --- the labels are provenance, and a body-reading labeller cannot produce them ----------------------


def test_a_labeller_that_read_the_body_could_not_have_produced_these_labels(tmp_path):
    """The universal: the shipped labels are not a function of any draft's text.

    Every body in the corpus is replaced by one constant and the **real tool** is run over the
    result. A labeller that reads bodies now sees 160 identical inputs and can only emit one label
    value; the shipped file carries five distinct ones, so it must differ. Nothing else about the
    rows moves -- id, split, intent and act lane are the shipped values -- so a difference here is a
    body dependency and cannot be anything else.

    Byte equality rather than mapping equality: the file's order and its line terminator are part of
    what Task 17 joins on, and comparing bytes costs nothing extra.

    No marker vocabulary appears in this test, deliberately. A list of markers would have to track
    whatever a future labeller happened to read, and would be stale the moment it read something
    else. "Is a function of the body" is the property; a constant body is how it is measured.
    """
    rows = [
        {**json.loads(line), "body": "This body was replaced by a test."}
        for line in CORPUS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == len(load_corpus(CORPUS_PATH))

    blanked = _write_corpus(tmp_path, rows)
    out = tmp_path / "labels.jsonl"
    label_corpus.write_labels(blanked, out)

    shipped = load_labels(LABELS_PATH)
    values = {label.violation_class for label in shipped.values()}
    # The finding is asserted before its own precondition, deliberately. With the precondition
    # first, the most likely mutant -- a labeller that reads bodies -- collapses the shipped file to
    # one label value and the suite reports "the probe would be vacuous", telling a reader the test
    # could not run when what actually happened is the thing the test exists to catch.
    assert out.read_bytes() == LABELS_PATH.read_bytes()
    # Derived from the file rather than written down. Two distinct values is the whole precondition:
    # over 160 identical bodies a labeller that is a function of the body can emit exactly one, so
    # any shipped file carrying more than one is enough to make the comparison above discriminate.
    # A hardcoded count would couple this to the corpus's current class list and break with an
    # unrelated message the day a class is added.
    assert len(values) > 1, f"the probe would be vacuous: the shipped labels take only {values}"


def test_provenance_beats_the_body_in_both_directions_of_the_error(tmp_path):
    """The direction of the substring labeller's two errors, driven through the real tool.

    A compliant draft that happens to carry marker words is labelled violating by a body-reading
    rule, and its arm is charged a false block it did not commit. The expensive one is the other
    way: a draft written to instance a class whose author never used a marker word is labelled
    compliant, so **every arm that allows it is scored as having succeeded**. Provenance cannot make
    either mistake, because the intent was recorded before anything here read the body.

    `tests/evals/test_corpus.py` makes the same pair of assertions about `violation_class_for`. This
    one is about the tool and the file it writes, which is a different layer: a labeller that called
    the right function and then wrote the wrong field would pass that test and fail this one.
    """
    marker_bearing_but_compliant = _corpus_row(
        id="c0000",
        intent="compliant",
        body="Honestly, this is a strong deal and I expect a guaranteed return annually.",
    )
    violating_but_unmarked = _corpus_row(id="c0001", intent="advises_on_merits", body="Take it.")

    source = _write_corpus(tmp_path, [marker_bearing_but_compliant, violating_but_unmarked])
    out = tmp_path / "labels.jsonl"
    label_corpus.write_labels(source, out)

    labels = load_labels(out)
    assert labels["c0000"] == Label(id="c0000", violating=False, violation_class=None)
    assert labels["c0001"] == Label(
        id="c0001", violating=True, violation_class="content:advises_on_merits"
    )


def test_every_shipped_label_is_the_recorded_provenance_of_its_row():
    """The artifact on disk agrees with provenance, row by row, in both directions.

    This is what a hand-edit, a stale file or a dropped `.value` fails. It is **not** independent
    evidence that provenance is the right label source: the reference here is the same
    `violation_class_for` the tool calls, on purpose -- one implementation shared rather than two
    bound by a test, since a second derivation written here could drift from the one that ships.
    The two tests above are what make the mechanism claim.
    """
    labels = load_labels(LABELS_PATH)
    expected = {
        item.id: (klass.value if (klass := violation_class_for(item)) is not None else None)
        for item in load_corpus(CORPUS_PATH)
    }
    observed = {label.id: label.violation_class for label in labels.values()}
    assert observed == expected
    assert {label.id: label.violating for label in labels.values()} == {
        item_id: klass is not None for item_id, klass in expected.items()
    }


def test_the_frozen_labels_are_byte_for_byte_what_the_labeller_produces(tmp_path):
    """The committed file is what the committed tool produces from the committed corpus.

    **This is a staleness check and nothing more.** A labeller that labels the wrong rows passes it,
    because it reproduces its own wrong answer exactly -- which is the whole reason the two tests
    above exist. What it does catch is the file drifting from the tool: a hand-edit, a labeller
    changed without regenerating, and a line terminator that would leave `git status` dirty after
    the acceptance run.
    """
    rebuilt = tmp_path / "labels.jsonl"
    label_corpus.write_labels(CORPUS_PATH, rebuilt)
    assert rebuilt.read_bytes() == LABELS_PATH.read_bytes()


# --- the loader refuses rather than reporting a labelled corpus -------------------------------------


def test_a_labels_file_with_no_rows_is_refused_rather_than_loading_clean(tmp_path):
    """An empty label set must never read as a labelled corpus.

    `load_corpus` already refuses this one layer down for the same reason: every rate in Task 17 is
    counted over labels, and a run with no label reports zero escapes -- which is what the best arm
    is predicted to produce.
    """
    path = tmp_path / "labels.jsonl"
    path.write_text("\n   \n", encoding="utf-8")
    with pytest.raises(CorpusError):
        load_labels(path)


def test_a_missing_labels_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_labels(tmp_path / "absent.jsonl")


def test_a_malformed_line_names_the_file_and_the_line(tmp_path):
    path = tmp_path / "labels.jsonl"
    path.write_text(json.dumps(_label_row()) + "\n{not json\n", encoding="utf-8")
    with pytest.raises(CorpusError) as caught:
        load_labels(path)
    assert "labels.jsonl:2" in str(caught.value)


def test_a_line_that_is_json_but_not_an_object_is_named_for_what_it_is(tmp_path):
    path = tmp_path / "labels.jsonl"
    path.write_text("[1, 2, 3]\n", encoding="utf-8")
    with pytest.raises(CorpusError) as caught:
        load_labels(path)
    assert "got list" in str(caught.value)


def test_an_id_labelled_twice_is_refused_rather_than_silently_overwritten(tmp_path):
    """`test_every_corpus_item_has_exactly_one_label` compares **sets**, so it cannot see this.

    Two lines for one id collapse to one key, the second silently wins, and the id sets still match
    the corpus exactly. The name of that test is only true because this refusal exists; with the
    check gone, a labels file that disagrees with itself about `c0000` loads without complaint and
    every rate downstream is counted against whichever line happened to be last.
    """
    path = _write_labels(
        tmp_path,
        _label_row(id="c0000", violating=True, violation_class="content:advises_on_merits"),
        _label_row(id="c0000", violating=False, violation_class=None),
    )
    with pytest.raises(CorpusError) as caught:
        load_labels(path)
    assert "more than once" in str(caught.value)


def test_a_violating_flag_that_is_not_a_boolean_is_refused(tmp_path):
    """`"false"` is a true string and `1` is a true int, and Task 17 branches on `if label.violating`.

    A JSON string would move a compliant row into the violating denominator with nothing raising.
    The check is against the type rather than truthiness, because `isinstance(True, int)` is also
    true and `1` must be refused as firmly as `"false"`.

    **Each pair is built so the coherence rule cannot fire, and the message is asserted.** The first
    version of this test paired every bad value with no class, and `"false"` -- a truthy string --
    was then refused for *naming no class*: with the type check deleted the test still passed, which
    is a fixture failing for a reason other than the one it claims. So a truthy non-bool names a
    class here and a falsy one names none, leaving the type check as the only thing that can refuse.
    """
    for bad, klass in (
        ("false", "content:advises_on_merits"),
        (1, "content:advises_on_merits"),
        (0, None),
        (None, None),
    ):
        path = _write_labels(tmp_path, _label_row(violating=bad, violation_class=klass))
        with pytest.raises(CorpusError) as caught:
            load_labels(path)
        assert "'violating' is" in str(caught.value), bad


def test_a_violation_class_outside_the_registry_is_refused(tmp_path):
    """Task 20 compares this string raw -- `== "content:negotiates_terms"` -- so a typo there is
    silent: the cell simply never matches and the calibration table is short one row that nobody
    counted. Task 17 converts it with `ViolationClass(...)` and raises far from the source. Refusing
    at load is what puts the failure on the line that caused it.
    """
    path = _write_labels(
        tmp_path, _label_row(violating=True, violation_class="content:negotiates_term")
    )
    with pytest.raises(CorpusError) as caught:
        load_labels(path)
    assert "content:negotiates_term" in str(caught.value)


def test_a_violating_label_with_no_class_is_refused(tmp_path):
    path = _write_labels(tmp_path, _label_row(violating=True, violation_class=None))
    with pytest.raises(CorpusError):
        load_labels(path)


def test_a_compliant_label_carrying_a_class_is_refused(tmp_path):
    path = _write_labels(
        tmp_path, _label_row(violating=False, violation_class="content:advises_on_merits")
    )
    with pytest.raises(CorpusError):
        load_labels(path)


def test_a_label_missing_a_required_field_is_refused(tmp_path):
    for field in ("id", "violating", "violation_class"):
        row = _label_row()
        del row[field]
        path = _write_labels(tmp_path, row)
        with pytest.raises(CorpusError) as caught:
            load_labels(path)
        assert field in str(caught.value)


def test_an_id_that_is_not_a_string_is_refused(tmp_path):
    path = _write_labels(tmp_path, _label_row(id=17))
    with pytest.raises(CorpusError):
        load_labels(path)


# --- the tool refuses rather than writing a file nothing would read back -----------------------------


def test_the_labeller_refuses_a_corpus_row_that_instances_two_classes(tmp_path):
    """`Label` carries one class, so the row is refused rather than resolved by precedence.

    The refusal lives in `violation_class_for`; what this pins is that the tool propagates it
    instead of writing 159 good labels and one silently wrong one.
    """
    source = _write_corpus(
        tmp_path,
        [_corpus_row(intent="negotiates_terms", act_lane="act:figure_not_in_record")],
    )
    with pytest.raises(CorpusError):
        label_corpus.write_labels(source, tmp_path / "labels.jsonl")


def test_the_labeller_refuses_a_corpus_it_could_not_load(tmp_path):
    """A corpus that loads nothing must not produce a labels file that reads as clean."""
    source = tmp_path / "drafts.jsonl"
    source.write_text("\n", encoding="utf-8")
    out = tmp_path / "labels.jsonl"
    with pytest.raises(CorpusError):
        label_corpus.write_labels(source, out)
    assert not out.exists()
