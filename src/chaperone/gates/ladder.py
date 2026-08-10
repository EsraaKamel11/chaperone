"""The capability ladder. Design spec 7.1 and 7.2.

Content-class autonomy caps at tier 2, so no outward-sending surface reaches tier 3 here. The
reason is that a checker false negative at an unsupervised tier **is** the breach: sampled audit
catches it after transmission, which is detection and not prevention, and demotion is itself
triggered by a verdict -- so a missed violation also misses its own demotion. The same residual,
closed by the same ceiling.

**Built here: the demotion transition, the per-surface verbs, and the ceiling.** Not wired:
promotion. The mechanics are built -- `on_pass` increments and returns the promoted state, so the
ceiling has something to clamp -- and what is missing is a caller;
`test_nothing_under_src_wires_an_outcome_to_promotion` holds that nothing under `src/` calls it.
Production promotion is keyed to human-review outcomes on real cases, never to synthetic suite
scores: an artifact that promoted itself to autonomous operation on the strength of its own evals
would model exactly the judgment error it was built to argue against. `PROMOTION_THRESHOLD` is
therefore a number in an unwired transition, and no measurement in this repository stands behind it.

**These are pure state transitions over a value handed in.** Nothing here reads a verdict, a clock
or a store, and nothing here persists a tier; a caller decides what counts as a pass or a violation.
This module lives under `gates/` rather than `policy/` because it is not a predicate over a draft.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

CONTENT_CEILING = 2
PROMOTION_THRESHOLD = 25


class Surface(str, Enum):
    CONVERSATION = "conversation"
    RESEARCH = "research"


_VERBS = {
    (Surface.CONVERSATION, 0): "draft only",
    (Surface.CONVERSATION, 1): "send to internal review queue",
    (Surface.CONVERSATION, 2): "send outward with per-message approval",
    (Surface.RESEARCH, 0): "read only",
    (Surface.RESEARCH, 1): "propose enrichments for review",
    (Surface.RESEARCH, 2): "write enrichments with per-record approval",
    (Surface.RESEARCH, 3): "write enrichments autonomously, sampled audit",
}


# The ceilings, **declared** rather than derived, and bound to `_VERBS` by test in both directions.
#
# Deriving the ceiling from `_VERBS` -- `max(tier for (s, t) in _VERBS if s is surface)` -- reads
# well and inverts the causality. The ceiling is a policy about a surface and the verbs describe it,
# so a row added at `(CONVERSATION, 3)` would silently raise the ceiling to 3: §7.1's whole argument
# undone by what looks like a documentation edit. Declared here and held to the verb table by
# `test_the_declared_ceilings_and_the_verb_table_name_the_same_surfaces_and_agree_on_the_top`, which
# fails on that row rather than absorbing it.
_CEILINGS = {
    Surface.CONVERSATION: CONTENT_CEILING,
    Surface.RESEARCH: 3,
}


def max_tier_for(surface: Surface) -> int:
    """Content-class exposure caps at tier 2. Tier 3 is act-class-only surfaces.

    **A surface with no declared ceiling is refused, and that is the whole point of the lookup.**
    The expression this replaces -- `CONTENT_CEILING if surface is Surface.CONVERSATION else 3` --
    gave tier 3, the unsupervised rung, to every input that was not that exact member. `Surface`
    subclasses `str`, so `max_tier_for("conversation")` took the `else` branch and returned 3 for
    the conversation surface itself, measured; a member added to the enum later would have taken it
    too, and adding a surface is precisely when nobody is thinking about ceilings. An unknown input
    is the case where the answer matters most and is known least, so it gets a refusal.
    """
    ceiling = _CEILINGS.get(surface) if isinstance(surface, Surface) else None
    if ceiling is None:
        raise ValueError(f"no ceiling is declared for surface {surface!r}")
    return ceiling


def verbs_for(surface: Surface, tier: int) -> str:
    return _VERBS[(surface, tier)]


@dataclass(frozen=True)
class LadderState:
    surface: Surface
    tier: int
    consecutive_passes: int

    def __post_init__(self) -> None:
        ceiling = max_tier_for(self.surface)
        if self.tier > ceiling:
            raise ValueError(f"{self.surface.value} ceiling is tier {ceiling}; got {self.tier}")
        # The floor, for the same reason as the ceiling: `_VERBS` defines 0 upward, so a negative
        # tier is a state that names no autonomy at all and `verbs_for` cannot answer for it.
        if self.tier < 0:
            raise ValueError(f"tier {self.tier} is below the floor; the lowest tier is 0")

    def on_violation(self) -> "LadderState":
        return LadderState(self.surface, max(0, self.tier - 1), 0)

    def on_pass(self) -> "LadderState":
        passes = self.consecutive_passes + 1
        if passes >= PROMOTION_THRESHOLD and self.tier < max_tier_for(self.surface):
            return LadderState(self.surface, self.tier + 1, 0)
        return LadderState(self.surface, self.tier, passes)
