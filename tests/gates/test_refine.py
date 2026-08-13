from pathlib import Path

import pytest

from chaperone.audit.gateway import Gateway
from chaperone.audit.store import AuditStore
from chaperone.gates.checker import Checker, Verdict
from chaperone.gates.hook import guarded_call
from chaperone.gates.queues import ReviewQueues
from chaperone.gates.refine import refine
from chaperone.policy.act_classes import ActContext
from chaperone.policy.types import Draft, Message, Record, ViolationClass

RECORD = Record(fields={"round_size": "10000000"})
CONTEXT = ActContext(approval_token="tok", tier=2, consented_jurisdictions=frozenset({"US"}),
                     granted_tools=frozenset({"send_message"}), sent_count=0, send_cap=50)
CLEAN = Checker("sonnet-tier", "sonnet-tier",
                transport=lambda m: Verdict(violates=False, confidence=0.9), retries=0)


def _draft(body: str, **overrides) -> Draft:
    base = dict(thread=(Message(role="investor", body="?"),), body=body, cited_fields=(),
                recipient_jurisdiction="US", recipient_domain="example.test", tool_name="send_message")
    base.update(overrides)
    return Draft(**base)


def test_a_futile_class_escalates_without_consuming_budget():
    """'Is this a good deal?' has no compliant direct answer. The compliant response changes the task."""
    calls = []

    def redraft(draft, decision):
        calls.append(1)
        return _draft("rewritten")

    outcome = refine(_draft("Honestly, this is a strong deal."), RECORD, CONTEXT, CLEAN, redraft)
    assert outcome.stopped_for == "futile"
    assert outcome.rounds == 0
    assert calls == []
    assert outcome.alternative is not None


def test_a_refinable_class_is_redrafted_and_resolves():
    def redraft(draft, decision):
        return _draft("The round is $10M and the memo is attached.")

    outcome = refine(_draft("Returns are guaranteed."), RECORD, CONTEXT, CLEAN, redraft)
    assert outcome.resolved is True
    assert outcome.rounds == 1
    assert outcome.stopped_for == "resolved"


def test_the_same_violation_class_twice_stops_for_deadlock():
    def redraft(draft, decision):
        return _draft("Returns are guaranteed.")

    outcome = refine(_draft("Returns are guaranteed."), RECORD, CONTEXT, CLEAN, redraft, budget=5)
    assert outcome.stopped_for == "deadlock"
    assert outcome.rounds < 5


def test_the_budget_is_a_backstop_and_its_exhaustion_is_distinguishable():
    bodies = iter(["Returns are guaranteed.", "They would accept $8M instead.",
                   "Returns are guaranteed.", "They would accept $8M instead."])

    def redraft(draft, decision):
        return _draft(next(bodies))

    outcome = refine(_draft("They would accept $8M instead."), RECORD, CONTEXT, CLEAN, redraft, budget=2)
    assert outcome.resolved is False
    assert outcome.stopped_for == "budget"
    assert outcome.rounds == 2


def test_the_redraft_prompt_receives_the_decision_with_its_violating_span():
    seen = {}

    def redraft(draft, decision):
        seen["category"] = decision.findings[0].violation_class.value
        seen["span"] = decision.findings[0].span
        return _draft("The round is $10M.")

    refine(_draft("Returns are guaranteed."), RECORD, CONTEXT, CLEAN, redraft)
    assert seen["category"] == "content:forward_looking_return"
    assert "guaranteed" in seen["span"]


# ------------------------------------------------------------------------------------------------
# What a redraft is allowed to change
# ------------------------------------------------------------------------------------------------

#: A transmitted turn the checker's verdict turns on. Not matched by any tripwire, so the only
#: detector that can see it is the checker, and the only way the checker sees it is the thread.
PRESSURE = "would you push them on the price?"


def _thread_reading_checker() -> Checker:
    """A checker whose verdict depends on the transmitted thread, as a real reviewer's would.

    `build_checker_messages` puts the thread in the prompt, so a draft body that is innocuous on
    its own can still be a violation in the conversation it answers. This transport is scripted --
    the suite is offline -- but the dependence it scripts is the one design spec 3.3 describes.
    """
    def transport(messages: list[dict]):
        if PRESSURE in messages[0]["content"]:
            return Verdict(violates=True, violation_class=ViolationClass.NEGOTIATES_TERMS,
                           confidence=0.88, span="what I can do")
        return Verdict(violates=False, confidence=0.9)

    return Checker("sonnet-tier", "sonnet-tier", transport=transport, retries=0)


def test_a_redraft_cannot_widen_the_envelope_it_was_judged_under():
    """The proposal is a body. The thread, recipient and tool it is judged against are the original's.

    `redraft` returns a whole `Draft`, so a loop that hands the proposal straight to `decide` lets
    the redrafter choose its own reviewer input. Dropping the transmitted turn that made the body a
    violation then turns a denial into a `resolved` -- a permission failure laundered into a pass by
    rewriting what the gate is allowed to look at, which is what design spec 4.8 refuses.
    """
    thread = (Message(role="investor", body=PRESSURE),)
    original = _draft("I will see what I can do.", thread=thread)

    def redraft(draft, decision):
        return _draft("I will see what I can do.", thread=())

    outcome = refine(original, RECORD, CONTEXT, _thread_reading_checker(), redraft)
    assert outcome.resolved is False
    assert outcome.stopped_for == "deadlock"
    assert outcome.alternative is None


def test_a_redraft_may_correct_the_citations_that_made_the_draft_a_violation():
    """The other half of the envelope decision, and the reason it is a decision and not a default.

    `act:figure_not_in_record` is the one act-class design spec 4.8 leaves refinable, because a
    figure can be corrected or removed -- and `validate_citations` reads `cited_fields`. Pinning
    that to the original alongside the recipient and the tool would leave the loop unable to resolve
    the very class it was left refinable for: the corrected body arrives still carrying the citation
    that was wrong. So `cited_fields` travels with the body, and the envelope is what remains.
    """
    original = _draft("The round is $10M.", cited_fields=("valuation",))

    def redraft(draft, decision):
        return _draft("The round is $10M.", cited_fields=("round_size",))

    outcome = refine(original, RECORD, CONTEXT, CLEAN, redraft)
    assert outcome.resolved is True
    assert outcome.rounds == 1
    assert outcome.stopped_for == "resolved"


# ------------------------------------------------------------------------------------------------
# The redraft never transmits on its own
# ------------------------------------------------------------------------------------------------


def test_a_redraft_never_transmits_without_approval(tmp_path: Path):
    """Design spec 4.8, and the clause a weakening of would be Critical.

    A redraft that transmits by itself after a permission failure is an auto-retry of a permission
    failure, which is the thing this architecture exists to refuse. So the loop resolving is not an
    event that sends anything: the original attempt stays terminal and goes to a human instead.

    Both halves are asserted as effects on the real chokepoint. The send tool is a closure that
    records every entry, so `entered == []` is evidence the function was never run and not a count
    of calls to a spy; the audit log's own outcome entry is read back off disk; and the escalation
    is read out of the queue the chokepoint routed it to.

    **Where the alternative does and does not go, since Task 7 made half of this observable.** The
    escalation that is actually routed carries the blocked body and `proposed_alternative=None`: the
    chokepoint enqueues at the moment of denial, before the loop has produced anything, and nothing
    in this tree updates a queued `Handoff` afterwards. So this asserts the boundary rather than
    describing past it. `build_handoff`'s `alternative` parameter is how a proposal would reach a
    reviewer and `tests/gates/test_handoff.py` holds that it is carried; what no test holds, because
    no such composition exists, is anything routing a payload that has one.

    This used to build a second `Handoff` here and assert that it carried `outcome.alternative`,
    which compared `build_handoff`'s argument with its own output -- duplicating
    `test_every_field_traces_to_the_draft_the_record_or_the_decision`, which already asserts that
    field, and, worse, standing in for a claim about a reviewer that the routed escalation does not
    support.

    **What this does not establish.** It holds this composition, not every composition anyone might
    write later. What holds those is `tools/static_audit.py::audit_send_references`, which reserves
    the send symbol to the gateway module package-wide -- `refine.py` came under it the moment the
    file existed, and `test_a_send_reference_outside_the_gateway_is_caught` is the planted-violation
    control proving that audit still bites.
    """
    original = _draft("Returns are guaranteed.")

    def redraft(draft, decision):
        return _draft("The round is $10M and the memo is attached.")

    outcome = refine(original, RECORD, CONTEXT, CLEAN, redraft)
    assert outcome.resolved is True, "the premise is a loop that did find a compliant alternative"
    assert outcome.alternative != original.body

    entered: list[dict] = []
    registry = {"send_message": lambda **kw: entered.append(kw) or "sent"}
    gateway = Gateway(AuditStore(tmp_path / "audit.jsonl"), principal="conversation-agent", tier=2)
    queues = ReviewQueues()
    result = guarded_call(gateway, "send_message", {"to": "example.test"},
                          original, RECORD, CONTEXT, CLEAN, registry, queues=queues)

    assert result.allowed is False
    assert entered == [], "a compliant alternative existed and the blocked draft was sent anyway"
    entries, _ = gateway.store.read_all()
    assert [e.outcome for e in entries if e.kind == "outcome"] == ["redirected"]

    queued = queues.items("human-review")
    assert len(queued) == 1, f"one denial routed {len(queued)} escalations"
    assert queued[0].blocked_body == original.body
    assert queued[0].proposed_alternative is None, (
        "a queued escalation now carries a proposal, so the boundary this pins has moved"
    )


def test_a_misclassified_content_class_skips_the_budget_a_recorded_limit():
    """A wrong class does not merely mislabel: it decides whether the budget is spent at all.

    Task 20 measured an eval row caught but named with the wrong content class. `disposition_for`
    reads the class, so a body whose true class is refinable is routed **futile** when the checker
    names a futile one -- and futility skips the loop entirely. The redraft that would have resolved
    it is never asked for.

    The loop is behaving correctly given a wrong input, which is why this is recorded as a limit
    rather than filed as a defect. What makes it a limit and not a tautology is the control below:
    the identical body, the identical redraft and the identical record differ only in the class the
    checker names, and they end in opposite places. Nothing here is a claim about how often the
    checker is wrong; that is measured in the evals and asserted nowhere.
    """
    body = "Ask them to come down."
    resolved_bodies = ["I can share the round facts on record."]

    def redraft(draft, decision):
        return _draft(resolved_bodies[0])

    def _checker_naming(violation_class: ViolationClass) -> Checker:
        """Violates on this body and on nothing else, so the control can actually resolve.

        Written unconditional first, and the control caught it: a transport that violates whatever
        it is shown deadlocks the loop on the redraft too, and the comparison would then have been
        between a futile stop and a deadlock stop -- neither of which spends the budget, so the
        limit being recorded would have been invisible in its own test.
        """
        def transport(messages: list[dict]):
            if "come down" not in messages[0]["content"].split("<candidate_draft>")[1]:
                return Verdict(violates=False, confidence=0.9)
            return Verdict(violates=True, violation_class=violation_class,
                           confidence=0.7, span="come down")

        return Checker("sonnet-tier", "sonnet-tier", transport=transport, retries=0)

    mislabelled = refine(_draft(body), RECORD, CONTEXT,
                         _checker_naming(ViolationClass.ADVISES_ON_MERITS), redraft)
    assert mislabelled.stopped_for == "futile"
    assert mislabelled.rounds == 0
    assert mislabelled.alternative != resolved_bodies[0], "the redraft was never asked for"

    correct = refine(_draft(body), RECORD, CONTEXT,
                     _checker_naming(ViolationClass.NEGOTIATES_TERMS), redraft)
    assert correct.stopped_for == "resolved"
    assert correct.rounds == 1
    assert correct.alternative == resolved_bodies[0]


def test_a_negative_budget_raises_rather_than_reporting_an_exhaustion_that_never_ran():
    """budget=-1 would skip the loop entirely and return stopped_for="budget" with
    rounds=0, indistinguishable from a legitimate budget=0 whose zero rounds were the
    configured allowance. A negative cap is a misconfiguration, and a misconfiguration
    fails loudly at the call instead of silently doing nothing and labelling it
    exhaustion."""
    with pytest.raises(ValueError):
        refine(_draft("They would accept $8M instead."), RECORD, CONTEXT, CLEAN,
               lambda d, decision: d, budget=-1)
