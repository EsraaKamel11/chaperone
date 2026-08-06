from chaperone.evals.judge import QualityScores, build_judge_messages, score_quality
from chaperone.policy.types import Draft, Message, Record

RECORD = Record(fields={"round_size": "10000000"})
DRAFT = Draft(thread=(Message(role="investor", body="?"),), body="The round is $10M.",
              cited_fields=("round_size",), recipient_jurisdiction="US",
              recipient_domain="example.test", tool_name="send_message")

#: The constraint vocabulary a quality rubric must not reach for. "merit", "negotiat" and
#: "forward-looking" name the three content-classes; "compliance", "permitted" and "allowed" are the
#: permission lane's own words. One tuple, read by both tests below, because two copies of a
#: denylist drift and the drift is silent in the safe-looking direction: a term dropped from one
#: copy still passes the other.
LEGAL_TERMS = ("merit", "negotiat", "forward-looking", "compliance", "permitted", "allowed")


def test_the_judge_returns_three_named_dimensions():
    scores = score_quality(DRAFT, RECORD, transport=lambda m: QualityScores(0.9, 0.8, 0.7))
    assert (scores.grounding, scores.fluency, scores.fit) == (0.9, 0.8, 0.7)


def test_the_mean_is_the_reported_score():
    assert QualityScores(1.0, 0.0, 0.5).mean() == 0.5


def test_the_judge_rubric_contains_no_legal_criterion():
    """A quality rubric has no reason to encode a constraint carve-out. The absence IS the point."""
    from chaperone.evals.judge import RUBRIC
    lowered = RUBRIC.lower()
    for term in LEGAL_TERMS:
        assert term not in lowered


def test_the_assembled_judge_prompt_carries_no_legal_criterion():
    """§9.4's property is about what the judge is **asked**, and it is asked the assembled prompt.

    The test above guards the constant. `RUBRIC` is interpolated into a larger f-string, so a
    criterion written straight into `build_judge_messages` never touches it -- measured, not
    supposed: adding "Also score whether the message is permitted under the stated constraints." to
    that f-string left all nine tests of this task passing before this test existed. §9.4 says the
    rubric's silence "is the point rather than a convenient omission", and a test that cannot see
    the criterion arrive leaves that sentence unbacked.

    The same shape as the checker's independence tests, which assert over `build_checker_messages`
    for the same reason: the built messages are what the model receives.

    **The fixture's own text is asserted clean first**, so a failure below is this project's wording
    and not a draft that happens to discuss merits. That guard is the one asserted here rather than
    described, because the residual runs the other way: a future fixture body about merits would
    fail this test spuriously, which is the safe direction and a visible one.
    """
    prompt = "\n".join(message["content"] for message in build_judge_messages(DRAFT, RECORD)).lower()
    supplied = " ".join((
        DRAFT.body, *(m.role for m in DRAFT.thread), *(m.body for m in DRAFT.thread),
        *DRAFT.cited_fields, *RECORD.fields.values(),
    )).lower()
    for term in LEGAL_TERMS:
        assert term not in supplied, f"the fixture itself carries {term!r}; the next assertion would not mean what it says"
        assert term not in prompt


def test_the_judge_sees_the_record_because_grounding_requires_it():
    captured = {}

    def transport(messages):
        captured["blob"] = str(messages)
        return QualityScores(0.9, 0.9, 0.9)

    score_quality(DRAFT, RECORD, transport=transport)
    assert "10000000" in captured["blob"]


def test_the_judge_sees_no_generator_artefacts():
    captured = {}

    def transport(messages):
        captured["blob"] = str(messages)
        return QualityScores(0.9, 0.9, 0.9)

    score_quality(DRAFT, RECORD, transport=transport)
    for marker in ("<thinking>", "toolu_", "scratchpad"):
        assert marker not in captured["blob"]
