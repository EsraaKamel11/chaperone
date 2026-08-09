"""The attribution ladder: one verdict source per rung, over one frozen corpus.

**The corpus is the fixed thing and the arms are detectors over it** (design spec 9.2). No arm
produces a draft; each judges the same 160 bodies at the same chokepoint, and the rungs are graded
against `corpus/labels.jsonl`.

**What the labels do and do not establish, stated exactly.** They descend from each row's recorded
provenance and from no reading of any body, so **the labels share no detector with any arm** and no
arm is graded by an oracle that re-read the text it judged. That is what blinding bought, and it is
narrower than independence.

**The residual, named in the direction of what is shared: criterion sharing.** The corpus author's
prompt and the blind judge's prompt were written by one hand and are near-verbatim on the
constraint definitions -- author, *"does not say whether a deal is good, attractive, worth doing, a
strong opportunity..."*; judge, *"states or implies whether an investment is good, attractive,
worth doing, a strong opportunity..."*. Blind authorship removed **vocabulary** sharing, which is
what §9.3's contamination control is about; it did not remove **criterion** sharing. The author
encoded against a definition and the judge decoded against the same definition, so the class-exact
agreement between them is closer to a matched-pair result than to an independent one. This is a
property of how the two prompts were written, no test can see it, and every agreement figure in
Task 17's report and in Task 20's calibration is qualified by it.

**Arm 1 is absent, and `ABSENT_ARMS` is where that is recorded.** See below; the short version is
that a rung with no honest verdict source is reported as missing rather than filled in. `Ladder`
carries the absence beside the numbers so a consumer cannot tabulate one without the other.

**The checker runs on every draft**, including drafts an act finding or a tripwire would have
short-circuited in production order. Design spec 9.3's measurement note: otherwise Task 20's
calibration is computed on a tripwire-negative selection, which is a different population than the
one the checker is claimed to be calibrated over. `run_arm` therefore looks the verdict up before it
asks whether anything else already blocked, and `arm_blocks` deliberately does not short-circuit.

**Where this harness differs from `gates/engine.py::decide`, enumerated by reading both rather than
by recalling.** `test_arm_four_blocks_exactly_what_the_shipped_engine_denies` binds the two on the
**blocking boolean** over all 80 eval rows, plus two constructed rows. It binds nothing else, and
there are four differences, not one:

1. **Verdict population.** `decide` returns on an act finding without consulting the checker; this
   harness records a verdict for every row. Deliberate, and it is §9.3's measurement note above.
2. **`FlagForReview` is unrepresentable.** `CheckerResult` is `Verdict | FlagForReview`; the replay
   schema holds `violates`, `violation_class`, `confidence`, `span` and a JSON `null`, so the third
   outcome cannot be recorded and the equivalence test never exercises it. `decide` denies on it
   and appends the tripwire findings; no arm here can reach that path. No measured number moves --
   the shipped replay contains no such row because it cannot -- but the path is untested by this
   binding.
3. **The retry budget is collapsed.** `Checker.check` spends a budget of attempts on a transport
   that fails and then raises `CheckerUnavailable` -- and skips the budget entirely when the
   transport raises `CheckerUnavailable` itself, which is a declaration rather than a symptom. The
   replay records only a final answer, so every way of failing becomes the one `null` sentinel and
   neither the budget nor the distinction between those two routes is modelled here.
4. **Only a boolean is computed.** `ArmResult` carries no findings tuple and no `disposition`, so
   `disposition_for`, `FUTILE_CLASSES` and the ranking limit `decide` documents are untouched by
   the ladder. Two rows can block for different reasons and this harness cannot tell them apart --
   an unnamed violation blocks here directly and at `decide` via `CheckerUnavailable`, and only the
   boolean agrees.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from chaperone.evals.corpus import CorpusItem, Label, act_findings_for
from chaperone.gates.checker import Verdict
from chaperone.policy.act_classes import ActContext
from chaperone.policy.tripwires import evaluate_tripwires
from chaperone.policy.types import Family, Finding, ViolationClass

_ROOT = Path(__file__).resolve().parents[3]
RECORDED_VERDICTS_PATH = _ROOT / "corpus" / "recorded_verdicts.json"

# Arm 1 of design spec 9.2 -- "the drafter's own model, given a tuned policing prompt" -- has no
# verdict source in this repository, and is named here rather than built.
#
# **What it would need.** A second verdict set over the same 160 bodies, produced by the drafter's
# own model under a self-policing prompt. Arm 1 -> arm 2 is required to differ in exactly one way,
# model identity, so that set must differ from `corpus/blind-verdicts.jsonl` in the model and in
# nothing else.
#
# **Why none of the available substitutes is one.** Deriving arm 1 by thresholding arm 2's
# confidences produces a rung whose every disagreement with arm 2 is a function of arm 2, so the
# gap between them measures the threshold that was chosen and not the model identity that was
# varied -- and the threshold would be chosen by someone who has read the prediction that arm 1 is
# worst. Deriving it from the tripwires or from the labels is worse in the same direction: the
# ladder would grade a detector against itself.
#
# **What the ladder measures without it.** Arms 2, 3 and 4: the value of a fail-closed gate and the
# value of the deterministic layer, over an independent checker. It does not measure the value of
# independence itself, which is exactly what rung 1 -> 2 exists to isolate. That claim is not made
# anywhere in this artifact.
ABSENT_ARMS: tuple[str, ...] = ("1-self-policing",)


class HarnessError(ValueError):
    """A replay artifact, or a request against one, that cannot be honoured as written."""


SCOPE_ALL = "all-classes"


def scope_name(only_family: Family | None) -> str:
    return SCOPE_ALL if only_family is None else f"{only_family.value}-classes-only"


@dataclass
class ArmResult:
    """One arm's 2x2 against the labels, with both denominators carried beside both counts.

    `n_violating` and `n_compliant` are the denominators of the two rates and are reported with
    them everywhere, because they are not constant across scopes. `scope="act-classes-only"` counts
    a content-violating row as **neither** violating nor compliant, so its escape rate is over the
    act-declaring rows alone -- 5 per split -- and `scope="content-classes-only"` is over the 45
    content rows, neither being the 50 the all-classes scope uses. The false-block denominator is
    30 in all three. A rate quoted without its denominator silently invites the wrong one.
    """

    name: str
    n_violating: int
    n_compliant: int
    escapes: int
    false_blocks: int
    scope: str = SCOPE_ALL
    checker_verdicts: dict[str, Verdict] = field(default_factory=dict)

    @property
    def escape_rate(self) -> float | None:
        """escapes / labelled-violating, or None over an empty denominator -- never zero."""
        return self.escapes / self.n_violating if self.n_violating else None

    @property
    def false_block_rate(self) -> float | None:
        """false blocks / labelled-compliant, or None over an empty denominator -- never zero."""
        return self.false_blocks / self.n_compliant if self.n_compliant else None


@dataclass(frozen=True)
class Arm:
    name: str
    use_checker: bool
    fail_closed: bool
    use_deterministic: bool
    verdict_of: Callable[[str], Verdict | None]


def load_recorded(path: Path = RECORDED_VERDICTS_PATH) -> dict[str, dict]:
    """The replay artifact. Raises rather than returning an empty mapping.

    An empty mapping is not a ladder over nothing -- every lookup would raise -- but it is a file
    that read as valid, and the failure would surface one layer away from its cause.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise HarnessError(f"{path}: expected a JSON object, got {type(raw).__name__}")
    if not raw:
        raise HarnessError(f"{path}: read no recorded verdict")
    return raw


def recorded_verdict(recorded: Mapping[str, dict | None], item_id: str) -> Verdict | None:
    """The replayed verdict for one row, or None meaning *the checker gave no usable answer*.

    **The two ways of having no verdict are not one value here.** An id the artifact does not hold
    is a defect in the artifact and raises: the replay and the corpus would be describing different
    corpora, and an arm that is not fail-closed would read the absence as "nothing to block on" and
    allow the row. That is the fail-open shape this project has met four times, and it would land
    on precisely the rows whose verdicts went missing.

    Recorded unavailability is a JSON `null`, written deliberately, and it is what arm 3's rung
    exists to answer. It is what `CheckerUnavailable` becomes on this replay: arm 2 allows on it and
    arm 3 blocks on it.
    """
    if item_id not in recorded:
        raise HarnessError(f"the replay artifact holds no verdict for {item_id!r}")
    raw = recorded[item_id]
    if raw is None:
        return None
    try:
        return Verdict(
            violates=raw["violates"],
            violation_class=ViolationClass(raw["violation_class"]) if raw["violation_class"] else None,
            confidence=raw["confidence"],
            span=raw["span"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HarnessError(f"{item_id!r}: recorded verdict is not one this harness can replay: {exc}") from exc


def build_arms(recorded: Mapping[str, dict | None]) -> list[Arm]:
    """The rungs a verdict source exists for, in ladder order. Arm 1 is in `ABSENT_ARMS`."""
    replay: Callable[[str], Verdict | None] = lambda item_id: recorded_verdict(recorded, item_id)
    return [
        Arm("2-independent-checker", True, False, False, replay),
        Arm("3-fail-closed-gate", True, True, False, replay),
        Arm("4-plus-deterministic", True, True, True, replay),
    ]


def arm_by_name(arms: list[Arm], name: str) -> Arm:
    """Selected by name, never by index: a rung removed would otherwise silently shift the rest."""
    for arm in arms:
        if arm.name == name:
            return arm
    raise HarnessError(f"no arm named {name!r} among {[a.name for a in arms]}")


def arm_blocks(
    arm: Arm, item: CorpusItem, context: ActContext, act_layer_only: bool = False
) -> tuple[bool, Verdict | None]:
    """Whether this arm blocks this row, and the verdict it saw. Both, always.

    The verdict is looked up before anything else is consulted and is returned even where the row
    was already blocked, because design spec 9.3 requires the checker to have run on every draft.

    **`act_layer_only` is a narrowing of the blocking rule, and it applies to the act-class scope
    alone.** Under it the answer comes from `act_findings_for` and from nothing else -- no
    tripwires, no checker verdict, no fail-closed default -- so prediction 1's zero is attributable
    to pure functions over the record and the context and to nothing probabilistic. Anything else
    answering there would let a model's verdict, or a model's *absence*, supply the zero that is
    claimed as structural.

    Every other scope takes the arm's whole answer, because an escape is a row the arm allowed and
    which disjunct would have blocked it is not part of that question.
    """
    deterministic: tuple[Finding, ...] = ()
    if arm.use_deterministic:
        deterministic = act_findings_for(item, context)
        if not act_layer_only:
            deterministic = deterministic + evaluate_tripwires(item.draft)

    verdict = arm.verdict_of(item.id) if arm.use_checker else None
    # The narrowing is applied before the fail-closed check, not after it. Reversed -- which is how
    # this was first written -- arm 4 returned a block on a recorded `null` without ever consulting
    # `deterministic`, so prediction 1's zero became attributable to a checker's *absence* rather
    # than to pure functions, on exactly the rows the invariant is asserted over.
    if act_layer_only:
        return bool(deterministic), verdict
    if verdict is None and arm.use_checker and arm.fail_closed:
        return True, None
    return bool(deterministic) or bool(verdict and verdict.violates), verdict


def run_arm(
    arm: Arm,
    items: list[CorpusItem],
    labels: dict[str, Label],
    context: ActContext,
    only_family: Family | None = None,
) -> ArmResult:
    """One arm's 2x2 over `items`, scored against `labels` and never against any detector.

    **`only_family` narrows the violating denominator and leaves the compliant one whole.** A row
    violating some *other* family is counted as neither violating nor compliant: it is not an
    escape of the family being counted when allowed, and calling it compliant would charge a false
    block for blocking a row that really does violate. So under `Family.ACT` the escape rate is
    over the 5 act-declaring rows per split and under `Family.CONTENT` over the 45 content rows,
    while the false-block rate stays over all 30 labelled-compliant rows in both. `ArmResult.scope`
    carries which is in force and both denominators travel with the counts, because they are not
    the same denominator and a rate quoted without one invites the wrong one.

    **One narrowing, two knobs, and they are not the same knob.** `only_family` selects which rows
    are *counted*; `act_layer_only` narrows how the arm *answers*. Only the act scope sets the
    second, for the reason given on `arm_blocks` -- prediction 1's zero has to be structural. The
    content scope counts fewer rows and changes no answer, which is what makes prediction 2's
    0-of-45 a measurement of the arms rather than of this function.
    """
    result = ArmResult(arm.name, 0, 0, 0, 0, scope=scope_name(only_family))
    for item in items:
        if item.id not in labels:
            raise HarnessError(f"{item.id!r} carries no label; every rate here is counted over labels")
        label = labels[item.id]
        blocked, verdict = arm_blocks(arm, item, context, act_layer_only=only_family is Family.ACT)
        if verdict is not None:
            result.checker_verdicts[item.id] = verdict
        if label.violating:
            if only_family is not None and ViolationClass(label.violation_class).family is not only_family:
                continue
            result.n_violating += 1
            if not blocked:
                result.escapes += 1
        else:
            result.n_compliant += 1
            if blocked:
                result.false_blocks += 1
    return result


@dataclass(frozen=True)
class Ladder:
    """The rungs that ran, and the rungs that could not. Both, because only one of them is visible.

    A plain list of results reports a three-rung ladder as though three rungs were the design.
    `ABSENT_ARMS` is a module constant a consumer has to know to go and look for; `absent` arrives
    unasked, attached to the numbers it qualifies, so Task 26 cannot tabulate the ladder without
    having been handed the fact that a rung is missing from it.

    Iterable, sized and indexable so it reads as the sequence of results everywhere that only wants
    those -- the disclosure costs a caller nothing and is available to every caller.
    """

    results: list[ArmResult]
    absent: tuple[str, ...]

    def __iter__(self):
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    def __getitem__(self, index):
        return self.results[index]


def run_ladder(
    items: list[CorpusItem],
    labels: dict[str, Label],
    context: ActContext,
    arms: list[Arm],
    only_family: Family | None = None,
) -> Ladder:
    """Every rung, in ladder order, over identical items and identical replayed verdicts."""
    return Ladder([run_arm(arm, items, labels, context, only_family) for arm in arms], ABSENT_ARMS)


#: The grid the injection decision is taken on. A row is injected when `sha256(id)` modulo this
#: falls below `fraction` of it, so the selection is a function of the id and of nothing else --
#: no clock, no seed, no iteration order -- exactly as `corpus.py` splits on `sha256(body)`.
_INJECTION_RESOLUTION = 10_000

#: Not a rung. Named so a consumer cannot tabulate it beside the ladder by accident.
UNAVAILABILITY_PROBE_NAME = "probe-unavailability-injected"


def with_unavailability(recorded: Mapping[str, dict | None], fraction: float) -> dict[str, dict | None]:
    """A **copy** of the replay with a deterministic `fraction` of its rows recorded unavailable.

    The shipped artifact holds a verdict for all 160 rows, so `arm_blocks`' fail-closed branch is
    never reached and arms 2 and 3 return identical counts -- design spec 3.4's "checker
    unavailability fails closed" is held by unit tests and by nothing at the ladder level. This is
    what makes rung 2 -> 3 informative: it exercises a branch that exists and is unreached.

    **It is a sensitivity probe and not a measurement.** No rate in `PREREGISTRATION.md`, in the
    eval split's reported numbers or in `corpus/labels.jsonl` derives from it, and nothing here
    writes to `corpus/recorded_verdicts.json`: the input mapping is left as it was found and a new
    dict is returned. Injecting unavailability adjusts no prediction -- it changes which code path
    runs, not what any arm is being graded against.

    A fraction that rounds to no row is refused. An injection that injected nothing makes every
    downstream comparison agree for free while the probe reports success, which is the shape three
    tools in this project have already shipped.
    """
    if not 0.0 < fraction <= 1.0:
        raise HarnessError(f"fraction {fraction!r} is not in (0, 1]; an injection of none or of more than all")
    threshold = fraction * _INJECTION_RESOLUTION
    injected = {
        item_id
        for item_id in recorded
        if int(hashlib.sha256(item_id.encode("utf-8")).hexdigest(), 16) % _INJECTION_RESOLUTION < threshold
    }
    if not injected:
        raise HarnessError(
            f"fraction {fraction!r} selected no row of {len(recorded)}; an empty injection makes "
            "arms 2 and 3 agree for free"
        )
    return {item_id: (None if item_id in injected else raw) for item_id, raw in recorded.items()}


@dataclass(frozen=True)
class UnavailabilityProbe:
    """One run of the arms over an injected replay, carrying what was injected beside the counts.

    Separate from `Ladder` rather than a fourth rung on it, for the reason `reference_comparison`
    is returned separately: it is not a measurement of this corpus. A consumer that received bare
    `ArmResult`s could tabulate them beside the real ladder with nothing in the output saying the
    verdicts underneath them were altered, so `fraction` and `injected` travel with the numbers.
    """

    name: str
    fraction: float
    injected: tuple[str, ...]
    results: dict[str, ArmResult]


def unavailability_probe(
    items: list[CorpusItem],
    labels: dict[str, Label],
    context: ActContext,
    fraction: float,
    recorded: Mapping[str, dict | None] | None = None,
    only_family: Family | None = None,
) -> UnavailabilityProbe:
    """Every rung over a replay with `fraction` of its verdicts recorded unavailable.

    Read the pair (arm 2, arm 3) as the fail-closed trade and nothing more: arm 2 allows a row whose
    checker gave no answer and is charged the escape, arm 3 blocks it and is charged the false block
    where the row was compliant. Both directions move, which is why both counts are carried.
    """
    injected = with_unavailability(load_recorded() if recorded is None else recorded, fraction)
    arms = build_arms(injected)
    return UnavailabilityProbe(
        name=UNAVAILABILITY_PROBE_NAME,
        fraction=fraction,
        injected=tuple(item.id for item in items if injected.get(item.id, "present") is None),
        results={arm.name: run_arm(arm, items, labels, context, only_family) for arm in arms},
    )


def reference_comparison(
    items: list[CorpusItem],
    labels: dict[str, Label],
    context: ActContext,
    arms: list[Arm],
) -> tuple[ArmResult, ArmResult]:
    """Generation-stage prompting against full enforcement. More than one variable moves.

    Returned separately and never as a rung, because it is the comparison design spec 9.2 marks as
    explicitly multi-variable: stage, mechanism and failure behaviour all change at once.

    **`reference-prompt-only` carries no detector, so its escape rate is 1.0 by construction and is
    not a measurement of prompting.** The stand-in models the shipped configuration's *chokepoint*
    -- there isn't one -- and says nothing about how often a generation-stage instruction succeeds,
    which this artifact does not measure. Read as "how much of the corpus reaches the recipient when
    nothing inspects it", which is what a chokepoint's absence means and all it means.

    Arm 4 is selected by name. Selecting `arms[3]` would silently pick a different rung the moment
    the ladder's length changes, and it changed in this task: arm 1 has no verdict source and is in
    `ABSENT_ARMS`.
    """
    prompt_only = Arm("reference-prompt-only", False, False, False, lambda _: None)
    return (
        run_arm(prompt_only, items, labels, context),
        run_arm(arm_by_name(arms, "4-plus-deterministic"), items, labels, context),
    )
