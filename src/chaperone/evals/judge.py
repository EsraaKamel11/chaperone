"""The minimal quality judge. It measures; it authorizes nothing.

**The rubric carries no legal criterion, and that absence is the point** (design spec 9.4). A quality
rubric has no reason to encode a constraint carve-out, so this one does not, and a reader who is not
told that will reasonably suspect it was stripped to make the demo land. It was not: the three
dimensions below are the ones a quality judge would carry anyway, and
`test_the_judge_rubric_contains_no_legal_criterion` asserts the silence so nobody can quietly add a
criterion and nobody can claim one was removed. The six terms it refuses map onto the constraint
vocabulary this project enforces elsewhere -- "merit", "negotiat" and "forward-looking" onto the
three content-classes, "compliance", "permitted" and "allowed" onto the permission lane's own words.

**Design spec 1: an eval score is a measurement, not an authorization.** `demo/day2.py` exists to
show the two lanes disagreeing on one draft, and the disagreement is the thesis rather than a bug in
either lane. Three consequences are load-bearing and are stated here rather than left to be inferred:

- **A passing mean is available for a draft with no grounding whatsoever.** This module computes
  nothing about grounding; it *asks*, and returns what it is told. A draft citing nothing produces an
  empty `<cited_records>` block and a transport may still answer 1.0 on every dimension. Grounding as
  an **enforced** property lives in `policy/citations.py` and `policy/act_classes.py`, in the
  permission lane, and nothing here stands in for it.
- **Nothing validates the transport's answer.** `gates/checker.py` refuses verdicts it cannot act on,
  spends a retry on each and fails closed as `CheckerUnavailable`; there is no analogue here, and
  `QualityScores` carries no 0.0-1.0 bound where `Verdict.confidence` does, so
  `QualityScores(2.0, 2.0, 2.0).mean()` is 2.0. That asymmetry is safe only for as long as a score
  authorizes nothing. The moment any caller reads a score as permission it becomes a fail-open, and
  the fix then belongs here rather than at the caller.
- **`mean()` divides by a literal 3**, so a fourth dimension is a silent arithmetic error rather than
  a failure. Adding one means editing both.

**Independence is enforced by omission**, exactly as in `gates/checker.py` and for design spec 3.3's
reason: `build_judge_messages` reads the transmitted thread, the candidate draft and the cited
records, and no untransmitted artifact. The record is present because grounding cannot be judged
without it. 3.3 asks for two prongs -- a substring scan for generator artefacts **and** an exact
structure assertion -- and only the first is committed here, so an injected turn that avoids the
scanned markers would pass today. As in the checker, the selected fields are interpolated unescaped,
so whoever assembles the `Draft` owns the transmitted/untransmitted line for the text inside them.
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
