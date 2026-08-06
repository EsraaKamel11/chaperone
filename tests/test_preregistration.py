"""What `PREREGISTRATION.md` has to say, held to the artifact rather than to a reader's memory.

Spec 9.1 makes the document the evidence contract: *"it contains every metric, the grading
procedure, and the predicted direction of every result."* A test can check three things about that
and cannot check a fourth, and being clear about which is which is the point of this module.

- **It can check the document says the load-bearing things.** The brief's three substring checks do
  that for two definitions and one committed interpretation.
- **It can check the document's figures are still true.** Every number quoted in the document is
  recomputed here from `corpus/labels.jsonl` or, for the one dev-split measurement, from the dev
  split -- so a later change that falsifies a quoted figure forces a conspicuous amendment to a
  document that is supposed to be frozen. That is the correct outcome, not a nuisance: an amendment
  in git history is exactly the record pre-registration exists to leave.
- **It can check no labelled class was left out of the predictions.** The classes are read off the
  label file, so a class added to the corpus later cannot be silently absent from the document.
- **It cannot check the document preceded the numbers.** Nothing executable can. Git history is the
  only witness, which is why the document says so in its own first line.

**Nothing here loads the eval split.** Spec 9.3 touches eval once, and a guard test that measures it
spends that once just as surely as a report would.
"""
from __future__ import annotations

import re
from pathlib import Path

from chaperone.evals.corpus import CORPUS_PATH, LABELS_PATH, load_corpus, load_labels
from chaperone.policy.tripwires import evaluate_tripwires

PREREG = Path(__file__).resolve().parents[1] / "PREREGISTRATION.md"


def _text() -> str:
    """The document with runs of whitespace collapsed.

    The figures below are bound to the artifact on purpose; where a table happens to align, or a
    sentence happens to wrap, is not a property of anything. Matching raw text would turn a reflow
    into a red suite, and a test that cries on a harmless edit gets loosened by whoever hits it next.
    """
    return re.sub(r"\s+", " ", PREREG.read_text(encoding="utf-8"))


def _section(start: str, end: str) -> str:
    """The document between two markers, so a phrase found elsewhere cannot vouch for a claim here.

    A mutant sweep is what put this in: `"tripwire half" in text` survived rewriting prediction 3's
    mechanism, because the phrase also appears in that prediction's own heading. Whole-document
    substring checks answer "is this string present", and every assertion below wants "does *this*
    prediction say it" -- which is a different question wherever a document repeats itself.
    """
    text = _text()
    begin = text.index(start)
    return text[begin : text.index(end, begin)]


# --- the brief's three ------------------------------------------------------------------------------


def test_preregistration_exists_and_defines_both_rates():
    text = PREREG.read_text(encoding="utf-8")
    assert "Escape rate" in text
    assert "False-block rate" in text


def test_preregistration_predicts_the_false_block_cost_of_the_best_arm():
    """The string this matches is spec 9.3's prediction, which prediction 3 supersedes.

    It is kept because the supersession has to stay visible: a document that quietly dropped the
    spec's wording, rather than quoting it and saying why it no longer holds, would read as never
    having predicted a cost at all. What the document predicts *instead* is asserted below.
    """
    assert "rises from arm 3 to arm 4" in PREREG.read_text(encoding="utf-8")


def test_preregistration_commits_the_interpretation_of_a_failed_null():
    assert "reported as a failed prediction" in PREREG.read_text(encoding="utf-8")


# --- prediction 3 names the half of the deterministic layer it rests on -------------------------------


def test_prediction_three_names_the_tripwire_half_as_its_mechanism():
    """A prediction whose stated mechanism cannot fire is not a prediction.

    Arm 4 adds two blocking disjuncts to arm 3, and only one of them can move the false-block rate
    on this corpus: the act half is silent on all 60 labelled-compliant rows, which
    `tests/evals/test_corpus.py::test_the_act_lane_contributes_no_false_block_on_this_corpus` holds
    to the artifact. So the document has to say the prediction is about the tripwire half, or it
    attributes a cost to a mechanism that contributes none of it.
    """
    section = _section("**3. False blocks", "**4. Discrimination")
    assert "tripwire half" in section
    assert "act half" in section


def test_prediction_three_states_the_limitation_rather_than_reading_as_a_free_lunch():
    """Without this sentence the document reads "arm 4 costs nothing", which is the cherry-picking
    spec 9.3 named. A near-zero false-block rate here is a fact about a corpus with no compliant
    near-miss in the tripwires' reach, and not a measurement of what lexical tripwires cost."""
    section = _section("**3. False blocks", "**4. Discrimination")
    assert "cannot measure the false-block cost of lexical tripwires" in section


def test_the_committed_interpretation_names_the_prediction_it_belongs_to():
    """Spec 9.4 pre-commits the interpretation of the **discrimination** null specifically.

    "Reported as a failed prediction" appearing somewhere in a document is not a commitment about
    anything in particular -- the phrase occurs three times here, and the brief's guard above is
    satisfied by any one of them. This one holds it inside the paragraph that names prediction 4.
    """
    section = _section("**Prediction 4.**", "**Prediction 3.**")
    assert "reported as a failed prediction" in section


def test_the_dev_only_figure_the_document_quotes_is_still_true_of_the_dev_split():
    """The one measured number in the document, recomputed from the split it came from.

    Spec 9.3 permits tuning the tripwires on dev. If a later task does that and moves this figure,
    the document must be amended and this test is what forces it -- a frozen document quoting a
    figure that has stopped being true is worse than no figure.

    **Dev only.** `load_corpus(..., split="dev")` is the only corpus call here.
    """
    labels = load_labels(LABELS_PATH)
    dev = load_corpus(CORPUS_PATH, split="dev")
    compliant = [item for item in dev if not labels[item.id].violating]
    assert compliant
    hits = [item for item in compliant if evaluate_tripwires(item.draft)]
    assert f"{len(hits)} of {len(compliant)} labelled-compliant dev rows" in _text()


# --- every figure and every class in the document is read back off the labels --------------------------


def test_the_label_counts_the_document_quotes_are_what_the_label_file_holds():
    """The denominators of both rates, bound to the file they are counted over.

    Stated per split as well as in total, because a document quoting a total over a corpus whose
    splits are unbalanced would describe a balance that does not exist -- and both of Task 17's
    rates are computed on one split at a time.
    """
    labels = load_labels(LABELS_PATH)
    per_split = {
        split: [labels[item.id] for item in load_corpus(CORPUS_PATH, split=split)]
        for split in ("dev", "eval")
    }
    text = _text()

    classes = sorted({l.violation_class for l in labels.values() if l.violating})
    assert classes, "no violating label: every row below would be vacuous"
    for klass in classes:
        counts = [sum(1 for l in per_split[s] if l.violation_class == klass) for s in ("dev", "eval")]
        assert f"| `{klass}` | {counts[0]} | {counts[1]} | {sum(counts)} |" in text

    for name, predicate in (
        ("labelled violating", lambda l: l.violating),
        ("labelled compliant", lambda l: not l.violating),
    ):
        counts = [sum(1 for l in per_split[s] if predicate(l)) for s in ("dev", "eval")]
        assert f"| **{name}** | {counts[0]} | {counts[1]} | {sum(counts)} |" in text

    sizes = [len(per_split[s]) for s in ("dev", "eval")]
    assert f"| **rows** | {sizes[0]} | {sizes[1]} | {sum(sizes)} |" in text


def test_the_document_names_every_class_the_labels_actually_carry():
    """A class present in the labelled corpus and absent from the predictions is an omission.

    Read off the label file rather than from a list written here, so a class added later is caught
    by this test rather than by whoever notices the table is short a row.
    """
    text = _text()
    carried = {l.violation_class for l in load_labels(LABELS_PATH).values() if l.violating}
    assert carried
    for klass in carried:
        assert klass in text


def test_the_document_names_every_metric_the_artifact_will_report():
    """Spec 9.1: the document contains **every** metric. Absent ones cannot be added later."""
    text = _text()
    for metric in ("Escape rate", "False-block rate", "AUC", "Brier", "observed agreement"):
        assert metric in text


def test_every_figure_in_the_document_is_attributed_to_the_split_it_came_from():
    """Spec 9.3 touches eval once. A figure with no split named cannot be audited against that."""
    text = _text()
    assert "dev split" in text
    assert "The eval split has not been measured" in text
