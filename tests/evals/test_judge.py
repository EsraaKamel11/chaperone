from chaperone.evals.judge import QualityScores, score_quality
from chaperone.policy.types import Draft, Message, Record

RECORD = Record(fields={"round_size": "10000000"})
DRAFT = Draft(thread=(Message(role="investor", body="?"),), body="The round is $10M.",
              cited_fields=("round_size",), recipient_jurisdiction="US",
              recipient_domain="example.test", tool_name="send_message")


def test_the_judge_returns_three_named_dimensions():
    scores = score_quality(DRAFT, RECORD, transport=lambda m: QualityScores(0.9, 0.8, 0.7))
    assert (scores.grounding, scores.fluency, scores.fit) == (0.9, 0.8, 0.7)


def test_the_mean_is_the_reported_score():
    assert QualityScores(1.0, 0.0, 0.5).mean() == 0.5


def test_the_judge_rubric_contains_no_legal_criterion():
    """A quality rubric has no reason to encode a constraint carve-out. The absence IS the point."""
    from chaperone.evals.judge import RUBRIC
    lowered = RUBRIC.lower()
    for term in ("merit", "negotiat", "forward-looking", "compliance", "permitted", "allowed"):
        assert term not in lowered


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
