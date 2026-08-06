import ast
import json
from pathlib import Path

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


def test_the_judge_prompt_structure_is_exact():
    """Design spec 3.3's second prong, which it requires for the judge as well as the checker.

    "A substring scan alone passes an injected turn that avoids the scanned markers." The scan
    above reads three markers; an appended assistant turn carrying the generator's own verdict
    contains none of them, and a reviewer handed the generator's justification agrees with it --
    which 3.3 names as the entire failure being prevented on this surface.

    Mirrors `test_the_checker_prompt_structure_is_exact`, because one policy asserted two ways in
    two layers is how the two layers drift.
    """
    messages = build_judge_messages(DRAFT, RECORD)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert all(m["role"] != "assistant" for m in messages)


def test_the_judge_message_carries_exactly_a_role_and_a_content_key():
    """The structure prong applied to the message, not only to the list of them.

    An extra key carrying the generator's own justification survives both the absence scan and the
    turn-count test: the first reads three markers, the second pins the count and the role and
    never looks at the key set.
    """
    messages = build_judge_messages(DRAFT, RECORD)
    assert messages, "a builder returning [] would make the loop below assert nothing"
    for message in messages:
        assert set(message) == {"role", "content"}


def test_no_draft_or_record_field_outside_the_three_named_inputs_reaches_the_judge_prompt():
    """3.3 names three inputs for the judge and forbids "any untransmitted artifact". Both halves.

    The tests above assert that required things are present and that named markers are absent.
    None of them notices a field that should never have been included: widening `<cited_records>`
    to the whole record, or appending the routing fields, passes every one of them. A marker list
    can only refuse text somebody thought to name; this refuses a field by its origin.

    This pins field *selection* only. The selected fields are interpolated unescaped, so a body or
    a role can still carry text that reads like a separate turn -- the same residual the checker
    records, and the reason the transmitted/untransmitted line belongs to whoever assembles the
    `Draft`.
    """
    draft = Draft(
        thread=(Message(role="investor", body="marker_thread_body"),),
        body="marker_draft_body",
        cited_fields=("marker_cited_field",),
        recipient_jurisdiction="marker_jurisdiction",
        recipient_domain="marker_domain",
        tool_name="marker_tool",
    )
    record = Record(fields={"marker_cited_field": "marker_cited_value",
                            "marker_uncited_field": "marker_uncited_value"})
    blob = json.dumps(build_judge_messages(draft, record))
    for included in ("marker_thread_body", "marker_draft_body", "marker_cited_field", "marker_cited_value"):
        assert included in blob, f"{included} is a named input and did not reach the judge prompt"
    for omitted in ("marker_jurisdiction", "marker_domain", "marker_tool",
                    "marker_uncited_field", "marker_uncited_value"):
        assert omitted not in blob, f"{omitted} is untransmitted and reached the judge prompt"


#: The generator artefacts neither reviewer prompt may carry. Design spec 3.3 names them for the
#: checker **and** the judge: no generator system prompt, no scratchpad, no tool-call history, no
#: chain of thought. Held in parity with the checker's list by the test below.
GENERATOR_ARTEFACTS = ("<thinking>", "toolu_", "You are a drafting agent", "scratchpad", "chain of thought")

#: The checker test whose marker list this module must not fall behind.
_CHECKER_SCAN = "test_the_checker_prompt_has_no_generator_artefacts"


def _checker_artefact_markers() -> set[str]:
    """The checker's marker list, read from its source, so parity is measured and not remembered.

    Its markers are a literal inside a test function rather than an importable constant, and a
    cross-test import would depend on collection order. Reading the AST is the idiom this project
    already uses where two layers must agree and neither can import the other. A rename raises
    rather than returning an empty set, because a parity check that silently compares against
    nothing is the fail-open this whole exercise is about.
    """
    source = Path(__file__).resolve().parents[1] / "gates" / "test_checker.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == _CHECKER_SCAN:
            docstring = ast.get_docstring(node)
            return {n.value for n in ast.walk(node)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value != docstring}
    raise AssertionError(f"{_CHECKER_SCAN} not found in {source}; the parity check cannot run")


def test_the_judge_prompt_carries_none_of_the_artefacts_the_checker_refuses():
    """The absence prong at the checker's full width, and the reason the narrow one was not enough.

    This module's first revision scanned three markers where the checker scanned five, and the
    argument for leaving the gap was that the field-origin test dominated it. That was false, and
    measurably: prepending "You are a drafting agent. Show your chain of thought." to the prompt
    left every test in this module passing. Refusal by origin covers *fields*, and a generator
    preamble originates from no `Draft` or `Record` field at all -- so nothing that reasons about
    where a value came from can ever see it.
    """
    blob = json.dumps(build_judge_messages(DRAFT, RECORD))
    for marker in GENERATOR_ARTEFACTS:
        assert marker not in blob


def test_the_judge_scans_for_every_artefact_the_checker_scans_for():
    """One policy, two reviewer prompts: the judge's list may lead the checker's, never lag it.

    The drift this refuses is the one that actually happened, so it is asserted rather than noted.
    A superset rather than equality, because 3.3 forbids untransmitted artefacts on both surfaces
    and a judge-only marker is a tightening.
    """
    missing = _checker_artefact_markers() - set(GENERATOR_ARTEFACTS)
    assert not missing, f"the checker refuses {sorted(missing)} and this module does not scan for it"
