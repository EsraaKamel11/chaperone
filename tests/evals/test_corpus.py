"""What the frozen corpus is, and what it is safe to conclude from it.

Three properties carry the weight, and each has its own test below because each has been got wrong
somewhere in this project already:

- **No draft body originates here.** Design spec 9.3 makes the violating drafts' surface forms
  independent of the tripwire author's vocabulary. `test_every_body_and_intent_is_the_blind_authors_own`
  is the executable form of that: the multiset of `(body, intent)` pairs the corpus ships equals the
  multiset in `corpus/blind-drafts.jsonl`, so an edit to one character of one body fails it.
- **The act lane is controllable and stated.** A row meant to test a content class must not trip
  `act:figure_not_in_record`, because `gates/engine.py::decide` returns on act findings before
  `evaluate_tripwires` is ever called. Two tests hold both directions, and both run the same pair of
  predicates Task 17's arm 4 runs, under the same `ActContext`.
- **The label comes from provenance, never from the body.**
  `test_the_violation_class_is_read_from_provenance_and_never_from_the_body` is the one that matters
  for Task 16.

**Deliberately not measured here: how often a tripwire fires on this corpus.** Spec 9.3 tunes the
tripwires on dev and touches eval once. A hit-rate printed in this task's report is a number about
the eval split available before any arm runs, so it is not computed. What is computed is whether a
figure is present, which is a property of the text and of no detector.
"""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path

import pytest

from chaperone.evals import corpus as corpus_module
from chaperone.evals.corpus import (
    ACT_LANE_CLEAN,
    ACT_LANES,
    BLIND_DRAFTS_PATH,
    CORPUS_PATH,
    INTENTS,
    CorpusError,
    CorpusItem,
    act_findings_for,
    load_corpus,
    violation_class_for,
)
from chaperone.policy.canonical import figures_in, normalize_money
from chaperone.policy.types import Draft, Message, Record, ViolationClass
from tools import build_corpus


def _doc() -> str:
    """The module docstring with runs of whitespace collapsed.

    The numbers below are bound to the artifact on purpose, but where a sentence happens to wrap is
    not a property of anything. Matching the raw string would turn a reflow into a red suite, and a
    test that cries on a harmless edit gets loosened by whoever hits it next.
    """
    return re.sub(r"\s+", " ", corpus_module.__doc__)


def _item(
    body: str,
    intent: str,
    act_lane: str = ACT_LANE_CLEAN,
    cited_fields: tuple[str, ...] = (),
) -> CorpusItem:
    return CorpusItem(
        id="probe",
        split="dev",
        draft=Draft(
            thread=(Message(role="investor", body="Following up on our conversation."),),
            body=body,
            cited_fields=cited_fields,
            recipient_jurisdiction="US",
            recipient_domain="example.test",
            tool_name="send_message",
        ),
        record=Record(fields={}),
        intent=intent,
        act_lane=act_lane,
    )


def _row(**overrides) -> dict:
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
        "act_lane": ACT_LANE_CLEAN,
    }
    row.update(overrides)
    return row


def _write(tmp_path: Path, *rows: dict) -> Path:
    path = tmp_path / "drafts.jsonl"
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    return path


# --- the brief's five ----------------------------------------------------------------------------


def test_the_corpus_is_large_enough_to_measure_a_rate():
    assert len(load_corpus(CORPUS_PATH)) >= 150


def test_the_corpus_splits_into_dev_and_eval():
    dev = load_corpus(CORPUS_PATH, split="dev")
    evaluation = load_corpus(CORPUS_PATH, split="eval")
    assert len(dev) > 0 and len(evaluation) > 0
    assert len(dev) + len(evaluation) == len(load_corpus(CORPUS_PATH))
    assert {i.id for i in dev}.isdisjoint({i.id for i in evaluation})


def test_the_corpus_file_carries_no_adjudicated_label_and_no_detector_output():
    """What the file withholds, named as what it withholds rather than as what it cannot do.

    It does carry `intent`: the class each draft was written to instance, recorded by an author who
    was forbidden to read this repository. Spec 9.3 derives the labels from exactly that provenance,
    and the alternative -- re-reading the body for marker substrings -- mislabels every genuine
    paraphrase that drops the marker. What is absent is an adjudicated verdict, an arm identity and
    any detector's output, none of which exists at the time this file is frozen.
    """
    for line in CORPUS_PATH.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        for forbidden in ("label", "violating", "violation_class", "verdict", "arm"):
            assert forbidden not in record


def test_every_item_has_a_unique_id():
    items = load_corpus(CORPUS_PATH)
    assert len({i.id for i in items}) == len(items)


def test_items_load_as_typed_drafts():
    item = load_corpus(CORPUS_PATH)[0]
    assert item.draft.body
    assert item.draft.recipient_jurisdiction


# --- the drafts are the blind author's, and the artifact is a function of them ---------------------


def test_every_body_and_intent_is_the_blind_authors_own():
    """No body was written, paraphrased or edited in this task. The multisets are equal.

    Equality of multisets rather than of sets: a row silently duplicated, or a class's count
    quietly rebalanced, changes a multiset and leaves a set alone.
    """
    authored = collections.Counter(
        (raw["body"], raw["intent"])
        for raw in (
            json.loads(line)
            for line in BLIND_DRAFTS_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    )
    shipped = collections.Counter((i.draft.body, i.intent) for i in load_corpus(CORPUS_PATH))
    assert shipped == authored


def test_reordering_the_blind_file_renumbers_every_row_and_moves_none_between_splits(tmp_path):
    """The hazard the module docstring warns about, demonstrated rather than asserted.

    The split is keyed on a digest of the body specifically so that the source's line order cannot
    move a draft between dev and eval, and that half is shown here to hold. The id is positional and
    that half is shown here to fail: reordering the source renumbers every row, and the id is what
    `corpus/labels.jsonl` and `corpus/recorded_verdicts.json` join on. **No other test in this file
    notices**, because the byte-equality test rebuilds from the same reordered source and agrees
    with itself -- which is exactly why the warning is written down and why this stands under it.

    **What this test does not do, measured rather than assumed.** Making ids content-derived does
    *not* fail it: the comparison is shipped-file against rebuild, so a changed id scheme makes every
    id differ, which is what the assertion already expects. That mutant is caught by
    `test_the_frozen_corpus_is_byte_for_byte_what_the_builder_produces` instead, because the shipped
    file has positional ids. The sentence that stood here claimed the opposite and a one-line mutant
    disproved it. So: this test pins the hazard under the scheme in force, and whoever changes the
    scheme must rewrite it rather than expect it to object.
    """
    reordered = tmp_path / "blind-drafts.jsonl"
    lines = [l for l in BLIND_DRAFTS_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    reordered.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")
    build_corpus.build(reordered, tmp_path / "drafts.jsonl")

    shipped = {i.draft.body: (i.id, i.split) for i in load_corpus(CORPUS_PATH)}
    rebuilt = {i.draft.body: (i.id, i.split) for i in load_corpus(tmp_path / "drafts.jsonl")}
    assert set(shipped) == set(rebuilt)
    assert {b for b in shipped if shipped[b][0] != rebuilt[b][0]} == set(shipped)
    assert {b for b in shipped if shipped[b][1] != rebuilt[b][1]} == set()


def test_the_frozen_corpus_is_byte_for_byte_what_the_builder_produces(tmp_path):
    """The committed artifact is a function of the committed blind drafts and of nothing else.

    Byte equality rather than row equality, because the split assignment, the ids, the record
    contents and the line terminator are all part of what is frozen.
    """
    rebuilt = tmp_path / "drafts.jsonl"
    build_corpus.build(BLIND_DRAFTS_PATH, rebuilt)
    assert rebuilt.read_bytes() == CORPUS_PATH.read_bytes()


# --- the act lane -------------------------------------------------------------------------------


def test_the_shared_act_layer_runs_both_predicates_and_not_only_the_first():
    """Built to make the two disagree, because everything else here trusts them to agree.

    `act_findings_for` is one implementation used by the builder and by the tests below, which is
    what stops the corpus's stated property and its checked property from drifting -- and is exactly
    why it needs a row where each predicate contributes a finding the other cannot. The unbacked
    figure can only come from `evaluate_act_classes`; the citation to a field the record does not
    hold can only come from `validate_citations`.
    """
    probe = _item("We raised 7.", "compliant", cited_fields=("absent",))
    assert [f.detail for f in act_findings_for(probe)] == [
        "7",
        "index 0: field 'absent' is not in the record",
    ]


def test_the_act_lane_is_silent_on_every_row_that_declares_it_clean():
    """`decide` returns on an act finding before a tripwire is evaluated, so a content-class row
    that trips one tests the act lane instead of the class it was written for."""
    clean = [i for i in load_corpus(CORPUS_PATH) if i.act_lane == ACT_LANE_CLEAN]
    assert clean
    tripped = {i.id: [f.violation_class.value for f in act_findings_for(i)] for i in clean}
    assert {k: v for k, v in tripped.items() if v} == {}


def test_every_row_that_declares_an_act_class_carries_that_class_and_no_other():
    declared = [i for i in load_corpus(CORPUS_PATH) if i.act_lane != ACT_LANE_CLEAN]
    assert declared
    for item in declared:
        assert {f.violation_class for f in act_findings_for(item)} == {ViolationClass(item.act_lane)}


def test_the_record_holds_the_bodys_figures_and_nothing_else():
    """The record's whole job is to make the act lane controllable, so it holds figures only.

    Every field value is a figure of the body; a clean row backs all of them and a row declaring an
    act class backs none. A field carrying anything else -- provenance smuggled where an arm could
    read it, most of all -- makes `normalize_money` raise here.
    """
    for item in load_corpus(CORPUS_PATH):
        recorded = {normalize_money(value) for value in item.record.fields.values()}
        body_figures = figures_in(item.draft.body)
        if item.act_lane == ACT_LANE_CLEAN:
            assert recorded == body_figures
        else:
            assert recorded == set()
            assert body_figures


def test_the_act_lane_contributes_no_false_block_on_this_corpus():
    """Prediction 3's attribution, held in the vocabulary the false-block rate is counted in.

    Arm 4 blocks a labelled-compliant row on either half of its deterministic layer, and this corpus
    supplies no row where the act half does it. So "false blocks rise from arm 3 to arm 4" is a
    claim about the tripwires alone here.

    Stated over `violation_class_for` rather than over `act_lane`, and the two are not the same
    test: a change making `violation_class_for` return None for the declaring rows would move ten
    rows carrying a real act finding into the false-block denominator, and the lane-level test would
    not notice, because those rows still declare their lane truthfully.
    """
    compliant = [i for i in load_corpus(CORPUS_PATH) if violation_class_for(i) is None]
    assert compliant
    assert {i.id: [f.violation_class.value for f in act_findings_for(i)] for i in compliant if act_findings_for(i)} == {}
    # The denominator is quoted in the module docstring, so it is held to the corpus like every
    # other number there. A disclosure that silently goes stale is worse than none.
    assert f"0 of {len(compliant)} carry any act finding" in _doc()


def test_both_splits_carry_every_intent_and_a_row_from_each_act_lane():
    """A stratum missing from a split is a rate computed over nothing in Tasks 16 and 17."""
    for split in ("dev", "eval"):
        items = load_corpus(CORPUS_PATH, split=split)
        assert {i.intent for i in items} == set(INTENTS)
        assert {i.act_lane for i in items} == set(ACT_LANES)


# --- provenance is the label source ---------------------------------------------------------------


def test_the_violation_class_is_read_from_provenance_and_never_from_the_body():
    """Both directions of the substring labeller's error, which is why provenance is carried at all.

    A marker-bearing compliant draft is labelled violating by a substring rule, and a paraphrase
    that dropped its marker is labelled compliant -- and that second one scores an escape as a
    success.
    """
    assert violation_class_for(_item("Honestly, this is a strong deal.", "compliant")) is None
    assert (
        violation_class_for(_item("Take it.", "advises_on_merits"))
        is ViolationClass.ADVISES_ON_MERITS
    )


def test_a_row_constructed_to_carry_two_classes_is_refused_rather_than_labelled_with_one():
    """`Label` holds one class, so a row instancing two would be labelled by whichever the
    derivation happened to check first."""
    both = _item("They would take 8.", "negotiates_terms", ViolationClass.FIGURE_NOT_IN_RECORD.value)
    with pytest.raises(CorpusError):
        violation_class_for(both)


def test_every_shipped_row_yields_a_class_or_an_explicit_none():
    classes = {i.id: violation_class_for(i) for i in load_corpus(CORPUS_PATH)}
    assert set(classes.values()) == {
        None,
        ViolationClass.ADVISES_ON_MERITS,
        ViolationClass.NEGOTIATES_TERMS,
        ViolationClass.FORWARD_LOOKING_RETURN,
        ViolationClass.FIGURE_NOT_IN_RECORD,
    }


# --- the loader refuses rather than reporting an empty corpus --------------------------------------


def test_an_unknown_split_is_named_as_unknown_rather_than_reported_as_an_empty_one():
    """Two guards refuse this, and only the message says which fault it was.

    Deleting the split-name check still raises, because filtering on a split no row carries leaves
    nothing and the empty-load guard fires -- so a test asserting only `CorpusError` passes with the
    check gone, measured. The two situations need different fixes: a caller that misspelled `eval`
    must repair the call, and a corpus with no row in a real split must be rebuilt. A refusal that
    cannot tell them apart sends the reader to the wrong one.
    """
    with pytest.raises(CorpusError) as caught:
        load_corpus(CORPUS_PATH, split="evla")
    assert "unknown split" in str(caught.value)


def test_a_row_whose_split_is_neither_dev_nor_eval_is_refused(tmp_path):
    path = _write(tmp_path, _row(split="holdout"))
    with pytest.raises(CorpusError):
        load_corpus(path)


def test_a_corpus_file_with_no_rows_is_refused_rather_than_loading_clean(tmp_path):
    path = tmp_path / "drafts.jsonl"
    path.write_text("\n   \n", encoding="utf-8")
    with pytest.raises(CorpusError):
        load_corpus(path)


def test_a_split_that_selects_no_row_is_refused_rather_than_loading_clean(tmp_path):
    path = _write(tmp_path, _row(split="dev"))
    with pytest.raises(CorpusError):
        load_corpus(path, split="eval")


def test_a_malformed_line_names_the_file_and_the_line(tmp_path):
    path = tmp_path / "drafts.jsonl"
    path.write_text(json.dumps(_row()) + "\n{not json\n", encoding="utf-8")
    with pytest.raises(CorpusError) as caught:
        load_corpus(path)
    assert "drafts.jsonl:2" in str(caught.value)


def test_a_missing_corpus_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_corpus(tmp_path / "absent.jsonl")


def test_a_repeated_id_is_refused(tmp_path):
    path = _write(tmp_path, _row(id="c0000"), _row(id="c0000", split="eval"))
    with pytest.raises(CorpusError):
        load_corpus(path)


def test_a_repeated_id_is_refused_even_when_the_duplicate_is_filtered_out(tmp_path):
    """The filter runs after the check, or a duplicate hides in the split not being asked for."""
    path = _write(tmp_path, _row(id="c0000"), _row(id="c0000", split="eval"))
    with pytest.raises(CorpusError):
        load_corpus(path, split="dev")


def test_an_unrecognised_intent_is_refused(tmp_path):
    path = _write(tmp_path, _row(intent="advises_on_merit"))
    with pytest.raises(CorpusError):
        load_corpus(path)


def test_an_unrecognised_act_lane_is_refused(tmp_path):
    path = _write(tmp_path, _row(act_lane="act:figure_not_in_the_record"))
    with pytest.raises(CorpusError):
        load_corpus(path)


def test_a_row_missing_a_required_key_is_refused(tmp_path):
    row = _row()
    del row["intent"]
    path = _write(tmp_path, row)
    with pytest.raises(CorpusError):
        load_corpus(path)


def test_a_line_that_is_json_but_not_an_object_is_named_for_what_it_is(tmp_path):
    """Same measured reason as the unknown split: the verdict is refusal either way.

    With the shape check deleted, `raw["id"]` raises `TypeError` on a list and the missing-field
    handler turns that into the same `CorpusError` -- so the guard's whole contribution is saying
    `list` rather than reporting a row that is missing a field it could never have had.
    """
    path = tmp_path / "drafts.jsonl"
    path.write_text("[1, 2, 3]\n", encoding="utf-8")
    with pytest.raises(CorpusError) as caught:
        load_corpus(path)
    assert "got list" in str(caught.value)


# --- the documentation is bound to the artifact ----------------------------------------------------


def test_the_corpus_documentation_states_the_measured_class_asymmetry():
    """The blind author's finding is a claim about what the tripwires can reach, so the numbers it
    rests on are measured from the shipped corpus and the docstring is held to them.

    Figure-bearing is a proxy for "names a term", not the property itself -- a body can imply a fee
    is movable with no digit in it. The proxy is what can be measured without writing down a term
    vocabulary, and writing one down here is how a later tripwire author would be contaminated.
    """
    items = load_corpus(CORPUS_PATH)
    doc = _doc()
    for intent in INTENTS:
        group = [i for i in items if i.intent == intent]
        bearing = [i for i in group if figures_in(i.draft.body)]
        assert f"{intent} {len(bearing)} of {len(group)}" in doc
    silent = [
        i for i in items if i.intent == "negotiates_terms" and not figures_in(i.draft.body)
    ]
    assert f"the remaining {len(silent)} of 30" in doc


def test_the_corpus_documentation_states_the_size_of_each_act_lane():
    """The counts the module quotes for its own lanes, held to the artifact and to the split.

    Quoting "5 in each split" is a claim about both splits, so the phrase is only allowed to appear
    when the two are in fact equal -- otherwise the sentence reads as balance over an imbalance.
    """
    items = load_corpus(CORPUS_PATH)
    for lane in ACT_LANES:
        in_lane = [i for i in items if i.act_lane == lane]
        per_split = {s: sum(1 for i in in_lane if i.split == s) for s in ("dev", "eval")}
        assert len(set(per_split.values())) == 1, per_split
        assert f"{len(in_lane)} rows, {per_split['dev']} in each split" in _doc()
