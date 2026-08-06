"""The frozen corpus: 160 drafts, a dev/eval split, and the provenance the labels are derived from.

**Every body was written by an author who could not read this repository.** Design spec 9.3 requires
that violating drafts' surface forms not be drawn from the tripwire author's vocabulary. The
mechanism is blind offline authorship rather than paraphrase: a separate author, given the three
constraints in prose and forbidden `src/`, `tools/`, `tests/` and `docs/`, wrote
`corpus/blind-drafts.jsonl`, and `tools/build_corpus.py` frames those bodies without altering one
character of them. `tests/evals/test_corpus.py` holds the multiset of `(body, intent)` pairs equal
between the two files, so *that* half is executable rather than remembered. The other half is not:
no test here can establish that the file itself was written blind. That is a fact about how it was
produced, recorded in this task's report, and it is the assumption everything below rests on.

**`intent` is provenance, and it is the label source.** Each blind draft carries the class it was
written to instance, or `compliant`. Task 16 derives `corpus/labels.jsonl` from `violation_class_for`
below and from nothing else. Deriving a label by scanning the body for marker substrings is the
alternative, and it is wrong in both directions: a compliant draft that happens to say "honestly" is
labelled violating, and -- the expensive one -- a violation whose author never used the marker word
is labelled compliant, so the escape it represents is scored as a success. Provenance cannot make
*that* mistake, because it was recorded before any body was read by anything here.

**It has its own, and it is not smaller.** Provenance records what the author set out to write, not
what the text achieves. A draft intended as `negotiates_terms` that lands innocuous is still
labelled violating, and every arm that allows it is charged an escape it did not commit; a draft
intended as `compliant` that strays is labelled compliant, and an arm that blocks it is charged a
false block. Nothing here detects either -- checking would mean reading the bodies with a detector,
which is the circularity the labels exist to avoid. The trade is deliberate: a substring rule's
errors correlate with the tripwires' own vocabulary, so they bias the measurement in the direction
that flatters arm 4, while an author's misses do not know what the tripwires look for and land in
no particular direction. **A biased error is worse than a larger unbiased one**, which is the whole
reason to prefer provenance here. The residual is real and unquantified.

Provenance rides on the `CorpusItem` and not inside the `Draft` or the `Record`, which are the two
objects an arm is handed -- so no detector is given the recorded intent. That is a narrow claim and
is meant to be: a detector reads the body, and the body is the violation. What it cannot read is the
answer key. The record is where that could quietly stop being true, since this module builds it, so
a test holds every record field to being a figure of its own body and nothing else.

**The act lane is controlled, and `CONTROLLED_CONTEXT` is the context it is controlled under.**
`gates/engine.py::decide` returns on an act finding before `evaluate_tripwires` is ever called, so a
row written to test a content class and carrying an unbacked figure tests the act lane instead. Every
row therefore declares its `act_lane`:

- `clean` -- under `CONTROLLED_CONTEXT`, `evaluate_act_classes` and `validate_citations` both return
  nothing. Every figure the body states is backed by a record field; the jurisdiction, the tool and
  the tier are the consented ones. 150 rows, 75 in each split.
- `act:figure_not_in_record` -- the record is empty on purpose, so every figure in the body is
  unbacked and the row carries that act-class and only that one. 10 rows, 5 in each split, taken
  from the compliant intents so that no row instances two classes at once. They exist because
  Task 17's prediction 1 -- act-class escapes on arm 4 -- is otherwise a rate over an empty
  denominator, which is the shape this project has already had to fix three times.

`violation_class_for` refuses a row that declares a content intent *and* an act lane, because
`Label` holds one class and a row instancing two would be labelled by whichever the derivation
happened to consult first.

**An asymmetry in what a lexical detector can reach, reported by the blind author.**
`negotiates_terms` resists indirection far more than the other two classes: implying a number is
movable usually requires naming the number, so its indirect drafts still carry a term word even
where no proposing verb appears, and the indirection lives in the modality rather than in the
vocabulary. Merits and forward-looking can both be violated with no domain vocabulary at all --
"take it" advises on merits and names nothing.

The measurable proxy is whether the body states a figure at all, and it is a proxy: a body can imply
a fee is movable with no digit in it. Measured over the shipped corpus, bodies carrying at least one
figure are negotiates_terms 21 of 30, forward_looking_return 13 of 30, advises_on_merits 3 of 30 and
compliant 33 of 70, and a test holds those four counts to the corpus so this paragraph cannot drift
from it. **The direction holds; "nearly always" does not** -- the remaining 9 of 30
negotiates_terms bodies state no figure whatever. Read this as predicting which class a lexical
table will reach furthest into, not as a claim about how many drafts any tripwire catches.

**What this module does not establish.** How often any detector fires on this corpus is not measured
here and is not in this task's report: 9.3 tunes the tripwires on dev and touches eval once, and a
hit-rate published before any arm runs spends that once. Whether a figure is present is a property
of the text and of no detector, which is why the paragraph above may state it.

**Known limits, stated rather than discovered later.** The thread is one constant inbound message on
every row, so the judge's "fit" dimension varies with nothing and the corpus is a controlled
comparison over bodies and records alone. `cited_fields` is empty on every row, so
`validate_citations` returns nothing for the whole corpus and the citation half of arm 4's
deterministic layer is exercised by unit tests and not by this artifact. Four of the five act-classes
are likewise unexercised: the corpus varies the record, never the jurisdiction, the tool, the tier or
the send count.

**And the one a Task 17 reader would otherwise attribute wrongly: the act lane contributes exactly
zero false blocks here.** Every labelled-compliant row is act-clean -- measured, 0 of 60 carry any
act finding -- because a row declaring `act:figure_not_in_record` is labelled violating, and every
other row backs its figures. So pre-registered prediction 3, *"false blocks rise from arm 3 to arm
4"*, rests **entirely on the tripwire half** of arm 4's deterministic layer. Whatever that
prediction does, the act half did not cause it. This is a consequence of the design rather than a
defect in it: backing the figures is what makes the content lane reachable at all, and of the two
directions available it is the one that costs a spurious block and never hides a violation. A test
holds the zero, in the labels' own vocabulary rather than the lanes', because that is the vocabulary
the false-block denominator is counted in.

**The blind file's line order is load-bearing, which the split's independence from it may disguise.**
The split is a function of `sha256(body)` precisely so that reordering the source cannot move a draft
between dev and eval. Ids are not: `tools/build_corpus.py` numbers rows `c0000..c0159` by position in
`corpus/blind-drafts.jsonl`, and the id is the join key `corpus/labels.jsonl` and
`corpus/recorded_verdicts.json` are keyed on. **Reordering that file renumbers every row and every
test still passes** -- the byte-equality test compares the corpus against a rebuild from the same
reordered source, so it agrees. Treat the file's line order as frozen, not as incidental.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from chaperone.policy.act_classes import ActContext, evaluate_act_classes
from chaperone.policy.citations import validate_citations
from chaperone.policy.types import Draft, Finding, Message, Record, ViolationClass

_ROOT = Path(__file__).resolve().parents[3]
CORPUS_PATH = _ROOT / "corpus" / "drafts.jsonl"
BLIND_DRAFTS_PATH = _ROOT / "corpus" / "blind-drafts.jsonl"

SPLITS = ("dev", "eval")

# The blind author's four intent names, mapped to the class each instances. `compliant` maps to
# None, which is a value rather than an absence: `violation_class_for` distinguishes "this row
# instances no content class" from "this intent is not one I know", and refuses the second.
INTENTS: Mapping[str, ViolationClass | None] = {
    "compliant": None,
    "advises_on_merits": ViolationClass.ADVISES_ON_MERITS,
    "negotiates_terms": ViolationClass.NEGOTIATES_TERMS,
    "forward_looking_return": ViolationClass.FORWARD_LOOKING_RETURN,
}

ACT_LANE_CLEAN = "clean"
# The non-clean lanes are spelled as `ViolationClass` values, so a consumer reads the class off the
# row with `ViolationClass(item.act_lane)` and a lane naming a class that does not exist fails at
# the enum rather than becoming a string nobody compares against.
ACT_LANES = (ACT_LANE_CLEAN, ViolationClass.FIGURE_NOT_IN_RECORD.value)

# The context every row's act lane is silent under, and the only one. "Controllable" is meaningless
# without it: the same rows are three act-class violations each under a context that consents to no
# jurisdiction and grants no tool. Task 17's harness builds this same context, and holding both to
# one definition is what keeps the corpus's stated property and the harness's measurement from
# drifting apart.
CONTROLLED_CONTEXT = ActContext(
    approval_token="tok",
    tier=2,
    consented_jurisdictions=frozenset({"US"}),
    granted_tools=frozenset({"send_message"}),
    sent_count=0,
    send_cap=10_000,
)


class CorpusError(ValueError):
    """A corpus row, or a request for one, that cannot be honoured as written."""


@dataclass(frozen=True)
class CorpusItem:
    id: str
    split: str
    draft: Draft
    record: Record
    intent: str
    act_lane: str


def item_from_row(raw: object, origin: str) -> CorpusItem:
    """Validate one row and build the item, or raise naming where the row came from.

    The loader's only construction site, and `tools/build_corpus.py` calls it before serializing
    anything -- so a row the loader would refuse is never written in the first place, and the
    vocabulary is enforced once instead of in two places that can disagree.
    """
    if not isinstance(raw, dict):
        raise CorpusError(f"{origin}: expected a JSON object, got {type(raw).__name__}")
    try:
        item = CorpusItem(
            id=raw["id"],
            split=raw["split"],
            draft=Draft(
                thread=tuple(Message(role=m["role"], body=m["body"]) for m in raw["thread"]),
                body=raw["body"],
                cited_fields=tuple(raw["cited_fields"]),
                recipient_jurisdiction=raw["jurisdiction"],
                recipient_domain=raw["domain"],
                tool_name=raw["tool_name"],
            ),
            record=Record(fields=raw["record"]),
            intent=raw["intent"],
            act_lane=raw["act_lane"],
        )
    except (KeyError, TypeError) as exc:
        raise CorpusError(f"{origin}: row is missing or mistyped a required field: {exc!r}") from exc

    if item.split not in SPLITS:
        raise CorpusError(f"{origin}: split {item.split!r} is not one of {SPLITS}")
    if item.intent not in INTENTS:
        raise CorpusError(f"{origin}: intent {item.intent!r} is not one the blind author recorded")
    if item.act_lane not in ACT_LANES:
        raise CorpusError(f"{origin}: act_lane {item.act_lane!r} is not one of {ACT_LANES}")
    return item


def load_corpus(path: Path = CORPUS_PATH, split: str | None = None) -> list[CorpusItem]:
    """Every item in `path`, or those in one split. Raises rather than returning an empty list.

    **A load that yielded nothing must never read as a clean corpus.** Three tools in this project
    have reported success having examined nothing, and a corpus is the same hazard one layer up: an
    arm run over zero items reports zero escapes, and zero escapes is what the best arm is predicted
    to produce. So a misspelled split, an empty file and a split that selects no row are all
    refusals here, not empty lists downstream.

    Duplicate ids are checked across the whole file before the split filter, or a duplicate hides in
    the split that was not asked for.
    """
    if split is not None and split not in SPLITS:
        raise CorpusError(f"unknown split {split!r}: the corpus is split into {SPLITS}")

    items: list[CorpusItem] = []
    seen: set[str] = set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        origin = f"{path.name}:{lineno}"
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CorpusError(f"{origin}: line is not readable as JSON: {exc}") from exc
        item = item_from_row(raw, origin)
        if item.id in seen:
            raise CorpusError(f"{origin}: id {item.id!r} appears more than once")
        seen.add(item.id)
        if split is not None and item.split != split:
            continue
        items.append(item)

    if not items:
        selector = "" if split is None else f" for split {split!r}"
        raise CorpusError(f"{path}: loaded no corpus item{selector}")
    return items


def declared_act_class(item: CorpusItem) -> ViolationClass | None:
    """The act-class this row declares it carries, or None for a row declaring a clean act lane."""
    return None if item.act_lane == ACT_LANE_CLEAN else ViolationClass(item.act_lane)


def act_findings_for(item: CorpusItem) -> tuple[Finding, ...]:
    """Arm 4's deterministic layer over one row, under `CONTROLLED_CONTEXT`.

    One implementation, called by `tools/build_corpus.py` before it writes a row and by the corpus
    tests before the artifact ships, so what the rows are built to satisfy and what they are checked
    against cannot become two different properties.

    **Both predicates, not only the first.** This is the pair `gates/engine.py::decide` evaluates
    before it reaches a tripwire and the pair `evals/harness.py` gives arm 4. A row with a fabricated
    citation trips `validate_citations` and not `evaluate_act_classes`, so a version of this function
    calling one of them would declare such a row clean and arm 4 would block it anyway. A test
    constructs exactly that row and demands both findings back.
    """
    return evaluate_act_classes(item.draft, item.record, CONTROLLED_CONTEXT) + validate_citations(
        item.draft, item.record
    )


def violation_class_for(item: CorpusItem) -> ViolationClass | None:
    """The class this row was constructed to instance, from provenance and never from its body.

    Task 16's labels come from here. Reading the body instead -- for marker substrings, or with any
    detector -- makes the label agree with a detector by construction, and the escape rate measured
    against it is then a measurement of nothing.

    A row declaring both a content intent and an act lane is refused rather than resolved by
    precedence: `Label` carries one class, so silently picking one would drop the other from every
    rate it belongs in.
    """
    content = INTENTS[item.intent]
    act = declared_act_class(item)
    if content is not None and act is not None:
        raise CorpusError(
            f"{item.id}: row instances both {content.value} and {act.value}; a label carries one class"
        )
    return content if content is not None else act
