import ast
from pathlib import Path

import pytest

from chaperone.gates.ladder import CONTENT_CEILING, LadderState, Surface, max_tier_for


def test_the_conversation_surface_cannot_exceed_tier_two():
    assert max_tier_for(Surface.CONVERSATION) == CONTENT_CEILING == 2


def test_the_research_surface_reaches_tier_three():
    assert max_tier_for(Surface.RESEARCH) == 3


def test_a_violation_demotes_immediately_by_one_tier():
    state = LadderState(Surface.CONVERSATION, tier=2, consecutive_passes=40)
    demoted = state.on_violation()
    assert demoted.tier == 1
    assert demoted.consecutive_passes == 0


def test_demotion_never_goes_below_tier_zero():
    assert LadderState(Surface.CONVERSATION, 0, 0).on_violation().tier == 0


def test_promotion_stops_at_the_surface_ceiling():
    """**Vacuous with respect to promotion happening**, and kept anyway for the clamp.

    The state starts at tier 2, which *is* the conversation ceiling, so this cannot distinguish "the
    promotion transition clamps" from "there is no promotion transition". What it does kill is a
    missing clamp: without one, a hundred passes reach tier 6.
    """
    state = LadderState(Surface.CONVERSATION, tier=2, consecutive_passes=0)
    for _ in range(100):
        state = state.on_pass()
    assert state.tier == 2


def test_a_conversation_surface_cannot_be_constructed_above_its_ceiling():
    with pytest.raises(ValueError, match="ceiling"):
        LadderState(Surface.CONVERSATION, tier=3, consecutive_passes=0)


def test_tier_verbs_are_defined_per_surface():
    from chaperone.gates.ladder import verbs_for
    assert verbs_for(Surface.RESEARCH, 3) == "write enrichments autonomously, sampled audit"
    assert verbs_for(Surface.CONVERSATION, 2) == "send outward with per-message approval"


# ------------------------------------------------------------------------------------------------
# What an unknown input is given
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("unknown", ["conversation", "research", "broadcast", None, 3, Surface])
def test_a_surface_with_no_declared_ceiling_is_refused_rather_than_given_the_top_tier(unknown):
    """The fail-open sweep, on the one function whose wrong answer is *more* autonomy.

    `CONTENT_CEILING if surface is Surface.CONVERSATION else 3` hands tier 3 -- the unsupervised
    rung -- to every input that is not that exact member. `Surface` subclasses `str`, so the
    conversation surface spelled as the string it compares equal to takes the `else` branch and is
    granted the ceiling §7.1 exists to deny it. A member added to the enum later takes it too.

    A ceiling is a declaration about a surface. A surface nobody has declared one for does not
    thereby get the highest one.
    """
    with pytest.raises(ValueError, match="ceiling"):
        max_tier_for(unknown)


def test_a_tier_below_the_floor_is_refused_so_every_state_names_a_verb():
    """The other end of the same interval. A tier with no verb describes no autonomy at all."""
    with pytest.raises(ValueError, match="tier"):
        LadderState(Surface.CONVERSATION, tier=-1, consecutive_passes=0)


def test_the_declared_ceilings_and_the_verb_table_name_the_same_surfaces_and_agree_on_the_top():
    """Two tables, bound in both directions rather than left to agree by inspection.

    The ceiling is **declared**, not derived from `_VERBS`: deriving it would mean a verb row added
    at `(CONVERSATION, 3)` silently raised the ceiling to 3, which is the failure §7.1 is written
    against, dressed as a documentation edit. Declared and bound instead -- so that same row fails
    here, and so does a `Surface` member added to one table and not the other.

    `verbs_for` resolving across the whole interval is the third direction: a ceiling with no verb
    at the top would let a state exist that names no autonomy.
    """
    from chaperone.gates.ladder import _CEILINGS, _VERBS, verbs_for

    assert set(_CEILINGS) == set(Surface), "a surface has no declared ceiling, or vice versa"
    assert {surface for surface, _ in _VERBS} == set(Surface)
    for surface in Surface:
        tiers = sorted(tier for declared, tier in _VERBS if declared is surface)
        assert tiers == list(range(max_tier_for(surface) + 1)), (
            f"{surface.value}: verbs are defined for {tiers}, ceiling is {max_tier_for(surface)}"
        )
        for tier in tiers:
            assert verbs_for(surface, tier)


# ------------------------------------------------------------------------------------------------
# Built, and not built
# ------------------------------------------------------------------------------------------------

SRC = Path(__file__).resolve().parents[2] / "src" / "chaperone"


def _calls_of(name: str, source: str) -> list[int]:
    """Every line calling `name`, as a plain call or through an attribute."""
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and name in (getattr(node.func, "attr", None), getattr(node.func, "id", None))
    ]


def test_nothing_under_src_wires_an_outcome_to_promotion():
    """Design spec 7.2: the demotion transition is built, promotion mechanics are not.

    `LadderState.on_pass` exists as a state transition, so the ceiling has something to clamp. What
    must not exist is a caller: an artifact that promoted itself to autonomous operation on the
    strength of its own synthetic evals would model exactly the judgment error it was built to argue
    against. Production promotion is keyed to human-review outcomes on real cases.

    The scanner is proved to bite before its silence is read as evidence -- an AST walk that matched
    nothing would report this clean over a tree full of callers, which is the shape of the four
    tools in this project that reported success having examined nothing. It is an AST walk and not a
    grep, so `# state.on_pass()` in a comment cannot fail it and `getattr(state, "on_pass")()` can
    still evade it; this makes an accidental wiring impossible and a deliberate one conspicuous.
    """
    assert _calls_of("on_pass", "state.on_pass()\n") == [1], "the scanner misses an attribute call"
    assert _calls_of("on_pass", "on_pass(state)\n") == [1], "the scanner misses a plain call"
    assert _calls_of("on_pass", "# state.on_pass()\n") == []

    modules = sorted(SRC.rglob("*.py"))
    assert modules, "no source module was scanned, so this guard would pass vacuously"
    callers = {
        str(path.relative_to(SRC)): lines
        for path in modules
        if (lines := _calls_of("on_pass", path.read_text(encoding="utf-8")))
    }
    assert not callers, f"promotion has acquired a caller under src/: {callers}"
