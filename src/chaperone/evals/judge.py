"""The minimal quality judge. It measures; it authorizes nothing.

**The rubric carries no legal criterion, and that absence is the point** (design spec 9.4). A quality
rubric has no reason to encode a constraint carve-out, so this one does not, and a reader who is not
told that will reasonably suspect it was stripped to make the demo land. It was not: the three
dimensions below are the ones a quality judge would carry anyway, and two tests assert the silence
so nobody can quietly add a criterion and nobody can claim one was removed. The six terms they
refuse map onto the constraint vocabulary this project enforces elsewhere -- "merit", "negotiat" and
"forward-looking" onto the three content-classes, "compliance", "permitted" and "allowed" onto the
permission lane's own words.

**Two tests, because the constant is not what the judge is asked.** `RUBRIC` is interpolated into a
larger f-string, so a criterion written straight into `build_judge_messages` leaves the constant
untouched -- measured, not supposed: a mutant doing exactly that passed every test in this module's
first revision. `test_the_assembled_judge_prompt_carries_no_legal_criterion` asserts over the built
messages, which is what the model receives, and it is the one carrying 9.4's property.

**Design spec 1: an eval score is a measurement, not an authorization.** `demo/day2.py` exists to
show the two lanes disagreeing on one draft, and the disagreement is the thesis rather than a bug in
either lane. Three consequences are load-bearing and are stated here rather than left to be inferred:

- **A passing mean is available for a draft with no grounding whatsoever.** This module computes
  nothing about grounding; it *asks*, and returns what it is told. A draft citing nothing produces an
  empty `<cited_records>` block and a transport may still answer 1.0 on every dimension. Grounding as
  an **enforced** property lives in `policy/citations.py` and `policy/act_classes.py`, in the
  permission lane, and nothing here stands in for it.
- **There is no fail-closed path here, and there must not be one.** `gates/checker.py` refuses
  verdicts it cannot act on, spends a retry on each and fails closed as `CheckerUnavailable`. This
  module has no analogue, and that is the design rather than an omission: design spec 1 says
  "quality and permission are different questions and never share a mechanism", so a judge that
  failed *closed* would be a quality signal gating an action -- the confusion the whole artifact
  exists to refuse. **This is a measurement lane. Its failure blocks nothing, and must not.** What a
  judge outage costs is a missing measurement, and the place to fail closed on a missing measurement
  is a report that declines to state a number, never a gate.
- **A failure is not silently rendered as a score, measured.** The question is worth asking of a
  lane with no validation, so it was: a transport that raises propagates; one returning `None`, a
  dict, prose or the class itself is returned unchanged and raises at the first attempt to read it
  as a score. None of those becomes a passing mean. What *is* accepted silently is a well-typed
  score out of its stated range -- `QualityScores(2.0, 2.0, 2.0).mean()` is 2.0, and because `bool`
  is an `int`, `QualityScores(True, True, True).mean()` is 1.0. That is value fidelity, not
  failure-as-success, and where it would matter is 9.4's discrimination table rather than any
  permission: an inflated score corrupts an AUC, and authorizes nothing.
- **`mean()` divides by a literal 3**, so a fourth dimension is a silent arithmetic error rather than
  a failure. Adding one means editing both.

**Independence is enforced by omission**, exactly as in `gates/checker.py` and for design spec 3.3's
reason: `build_judge_messages` reads the transmitted thread, the candidate draft and the cited
records, and no untransmitted artifact. The record is present because grounding cannot be judged
without it. 3.3 asks for two prongs -- a substring scan for generator artefacts **and** an exact
structure assertion, because "a substring scan alone passes an injected turn that avoids the scanned
markers" -- and both are committed, mirroring the checker's so one policy is not asserted two ways
in two layers. A third goes further than either: a marker list can only refuse text somebody thought
to name, so `test_no_draft_or_record_field_outside_the_three_named_inputs_reaches_the_judge_prompt`
refuses a field by its origin instead, and `recipient_jurisdiction`, `recipient_domain`, `tool_name`
and every uncited record field are measured absent.

**What remains, and it is field selection's own limit.** As in the checker, the selected fields are
interpolated unescaped, so a thread role or a body carrying delimiter text still reads as part of
the prompt. Whoever assembles the `Draft` owns the transmitted/untransmitted line for the text
inside them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from chaperone.policy.types import Draft, Record

RUBRIC = (
    "Score this outbound message on three dimensions, each 0.0 to 1.0.\n"
    "grounding: every factual statement traces to a supplied record value.\n"
    "fluency: it reads as a person wrote it, not a template.\n"
    "fit: it responds to what was actually asked."
)


@dataclass(frozen=True)
class QualityScores:
    grounding: float
    fluency: float
    fit: float

    def mean(self) -> float:
        return (self.grounding + self.fluency + self.fit) / 3


def build_judge_messages(draft: Draft, record: Record) -> list[dict]:
    thread = "\n".join(f"[{m.role}] {m.body}" for m in draft.thread)
    cited = "\n".join(f"{n}: {record.get(n)}" for n in draft.cited_fields if record.get(n))
    return [{
        "role": "user",
        "content": (
            f"{RUBRIC}\n\n<transmitted_thread>\n{thread}\n</transmitted_thread>\n\n"
            f"<candidate_draft>\n{draft.body}\n</candidate_draft>\n\n"
            f"<cited_records>\n{cited}\n</cited_records>"
        ),
    }]


def score_quality(draft: Draft, record: Record, transport: Callable[[list[dict]], QualityScores]) -> QualityScores:
    return transport(build_judge_messages(draft, record))
