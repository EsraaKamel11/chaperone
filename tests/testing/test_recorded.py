"""The replay transport, driven through the real `Checker` rather than beside it."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from chaperone.evals.corpus import CONTROLLED_CONTEXT, load_corpus
from chaperone.evals.harness import RECORDED_VERDICTS_PATH, HarnessError, load_recorded
from chaperone.gates.checker import Checker, Verdict, build_checker_messages
from chaperone.gates.engine import decide
from chaperone.policy.act_classes import ActContext
from chaperone.policy.types import Draft, Message, Record, ViolationClass
from chaperone.testing import recorded as recorded_module
from chaperone.testing.recorded import RecordedTransport, key_for, replay_over_corpus

RECORD = Record(fields={"round_size": "10000000"})
CONTEXT = ActContext(approval_token="tok", tier=2, consented_jurisdictions=frozenset({"US"}),
                     granted_tools=frozenset({"send_message"}), sent_count=0, send_cap=50)


def _draft(body: str) -> Draft:
    return Draft(thread=(Message(role="investor", body="?"),), body=body, cited_fields=(),
                 recipient_jurisdiction="US", recipient_domain="example.test",
                 tool_name="send_message")


def _checker(transport) -> Checker:
    return Checker("sonnet-tier", "sonnet-tier", transport=transport, retries=0)


def test_a_recorded_verdict_is_replayed_through_the_real_checker_without_network():
    """Production change that breaks this: any signature other than `Checker`'s one-argument
    transport, which is what the brief's two-argument `__call__` could not satisfy."""
    draft = _draft("This is a strong deal.")
    row = {"violates": True, "violation_class": "content:advises_on_merits",
           "confidence": 0.9, "span": "strong deal"}
    transport = RecordedTransport({key_for(build_checker_messages(draft, RECORD)): row})

    verdict = _checker(transport).check(draft, RECORD)

    assert verdict == Verdict(violates=True, violation_class=ViolationClass.ADVISES_ON_MERITS,
                              confidence=0.9, span="strong deal")


def test_a_message_set_with_no_recording_raises_rather_than_answering():
    """The fail-open shape this project has met before: a lookup miss read as *nothing to block on*.

    Production change that breaks this: returning `None`, or a clean `Verdict`, for a key the
    replay does not hold. `harness.recorded_verdict` is what refuses it and this asserts the
    refusal survives the transport rather than being softened by it.
    """
    transport = RecordedTransport({"a-key-that-is-not-this-one": {
        "violates": False, "violation_class": None, "confidence": 0.8, "span": None}})

    with pytest.raises(HarnessError, match="holds no verdict"):
        transport(build_checker_messages(_draft("The round is $10M."), RECORD))


def test_a_recorded_unavailability_reaches_the_gate_as_a_closed_door():
    """A recorded JSON `null` is *the checker gave no usable answer*, and the gate must deny on it.

    The assertion is the gate's effect and not the transport's return value: production changes
    that break it are returning a compliant `Verdict` for a recorded `null`, and `decide` allowing
    a draft whose checker was unavailable.
    """
    draft = _draft("The round is $10M.")
    transport = RecordedTransport({key_for(build_checker_messages(draft, RECORD)): None})

    decision = decide(draft, RECORD, CONTEXT, _checker(transport))

    assert decision.allowed is False
    assert "unavailable" in decision.findings[0].detail


def test_a_transport_over_no_recording_is_refused_rather_than_built():
    """An empty replay is a file that read as valid, and `harness.load_recorded` already refuses it.

    Production change that breaks this: accepting `{}`. Every call would then raise one layer away
    from the cause, and under a fail-closed gate the whole corpus would block for a reason that has
    nothing to do with any draft.
    """
    with pytest.raises(HarnessError, match="no recorded verdict"):
        RecordedTransport({})


def test_the_shipped_replay_drives_the_real_gate_over_the_whole_corpus():
    """The transport's only reason to exist: `decide` run over the corpus with recorded verdicts.

    The reference is `corpus/recorded_verdicts.json` read directly here, never a second call into
    the harness, so this compares the gate's answer against the artifact rather than against
    another function that decodes it the same way.

    Production change that breaks this: a `replay_over_corpus` that keys on anything
    `build_checker_messages` does not determine, which lands every row on `CheckerUnavailable` and
    denies the whole corpus.
    """
    items = load_corpus()
    checker = _checker(replay_over_corpus(items))
    raw = json.loads(RECORDED_VERDICTS_PATH.read_text(encoding="utf-8"))

    decisions = {item.id: decide(item.draft, item.record, CONTROLLED_CONTEXT, checker)
                 for item in items}

    allowed = {item_id for item_id, d in decisions.items() if d.allowed}
    assert allowed and len(allowed) < len(items), "one answer for every row makes this vacuous"
    for item_id, decision in decisions.items():
        if raw[item_id]["violates"]:
            assert decision.allowed is False, f"{item_id}: recorded a violation and was allowed"


def test_a_corpus_row_the_replay_does_not_cover_is_refused_rather_than_replayed_as_clean():
    """A row with no recorded verdict must not become a transport that answers for it.

    Production change that breaks this: building the view with `recorded.get(item.id)`, which
    records the row as an unavailability and hands a fail-closed gate a denial whose reason is a
    gap in the artifact rather than anything about the draft.
    """
    items = load_corpus()[:2]
    covered = {items[0].id: {"violates": False, "violation_class": None,
                             "confidence": 0.8, "span": None}}

    with pytest.raises(HarnessError, match="holds no verdict"):
        replay_over_corpus(items, covered)


def test_two_rows_asking_the_checker_the_same_question_are_refused_rather_than_merged():
    """Distinct rows that digest to one key would silently share one verdict.

    Production change that breaks this: dropping the collision guard, after which the view is
    shorter than the corpus and the row that lost is graded against the row that won.
    """
    items = load_corpus()
    twin = items[0].__class__(id="twin", split=items[0].split, draft=items[0].draft,
                              record=items[0].record, intent=items[0].intent,
                              act_lane=items[0].act_lane)
    recorded = load_recorded()
    recorded["twin"] = recorded[items[0].id]

    with pytest.raises(HarnessError, match="same checker question"):
        replay_over_corpus([items[0], twin], recorded)


def test_the_transport_decodes_no_recorded_row_of_its_own():
    """Standing check 8, held structurally because a behavioural test cannot see a correct copy.

    Task 22 measured it: re-inlining a behaviourally identical copy of a shipped decoder left 25 of
    26 agreement rows green, and only a structural test fired. So this reads the module rather than
    running it. Production change that breaks it: building a `Verdict` here, or reading the replay
    artifact here, instead of delegating both to `evals/harness.py`.
    """
    tree = ast.parse(Path(recorded_module.__file__).read_text(encoding="utf-8"))
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    imported = {m for n in ast.walk(tree) if isinstance(n, ast.Import)
                for m in (a.name.split(".")[0] for a in n.names)}

    assert "recorded_verdict" in called, "the delegation is gone, so this guard is vacuous"
    assert "Verdict" not in called, "the row decoder has been copied into the transport"
    assert "json" not in imported, "the replay artifact has a second reader"
