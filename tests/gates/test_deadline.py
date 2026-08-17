"""Deny-on-timeout at both gate call sites, held on effects rather than on disclosure.

`gates/engine.py` has always owned the policy half: `decide`'s `CheckerUnavailable` branch is the
one place that turns "no usable answer" into a denial carrying an outage. What both call sites
disclosed and no test held was the mechanism half -- a transport that hung hung the gate, because
nothing bounded the wait for its answer. `gates/deadline.py` is that mechanism, and this file holds
the composition: silence at the bound must arrive at the reviewer as the same outage denial an
unavailable checker already produces, at `decide`, at `refine`, and at the executor chokepoint.

Every hang in this file is bounded by `HANG_LIMIT` so a red run terminates: the property under test
is that the gate answers long before a slow transport does, and a transport that answers *after*
the bound is the observable stand-in for one that never answers at all. Each test releases its
transport in a `finally`, so no worker outlives its test by more than the release takes.

No network, no recorded replay: every transport here is a local function, and the only clock in
play is the one the wrapper owns.

**A sweep mutant covers the wrapper now, and the count it moved was smaller than this banner
said.** `expiry_raises_a_generic_error` is registered in `tools/mutate.py`, replacing the expiry
declaration with a generic error, and it dies to the closed-question call count below at
`len(calls) == 1`. Registering it waited for its own commit because it moves a stated mutant
count -- and the deferral note said that count sat in two reader-facing documents, where a search
at registration found one, `docs/architecture-proposal.md`. The README carried no count to move:
an earlier round dropped it rather than deriving it. A note that names a number of sites is a
claim like any other, and this one was wrong by one until it was checked. The relabelling and
always-expire wrong implementations stay hand-applied and watched at the assertions the docstrings
below name, the measured-mutant practice this repository keeps for the properties no registered
anchor covers.
"""
from __future__ import annotations

import threading

import pytest

from chaperone.audit.gateway import Gateway
from chaperone.audit.store import AuditStore
from chaperone.gates.checker import Checker, CheckerUnavailable, Verdict
from chaperone.gates.deadline import bounded_transport
from chaperone.gates.engine import HUMAN_REVIEW, decide, denial_result
from chaperone.gates.hook import guarded_call
from chaperone.gates.queues import ReviewQueues
from chaperone.gates.refine import DEFLECTION, refine
from chaperone.policy.act_classes import ActContext
from chaperone.policy.types import Disposition, Draft, Message, Record, ViolationClass

RECORD = Record(fields={"round_size": "10000000"})
CONTEXT = ActContext(approval_token="tok", tier=2, consented_jurisdictions=frozenset({"US"}),
                     granted_tools=frozenset({"send_message"}), sent_count=0, send_cap=50)
CLEAN = Verdict(violates=False, confidence=0.9)

#: The wrapper's bound in every test that expects an expiry. Small, so a green run is fast.
BOUND = 0.2

#: How long a "hanging" transport hangs before answering. The bounded stand-in for forever: long
#: enough that a gate still waiting at the bound has demonstrably not been bounded, short enough
#: that the red run this file was watched failing on terminated rather than hanging the suite.
HANG_LIMIT = 5.0


def _draft(body: str, **overrides) -> Draft:
    base = dict(thread=(Message(role="investor", body="?"),), body=body, cited_fields=(),
                recipient_jurisdiction="US", recipient_domain="example.test", tool_name="send_message")
    base.update(overrides)
    return Draft(**base)


def _answers_after(release: threading.Event, finished: threading.Event | None = None):
    """A transport that answers compliant, but only once `release` is set or `HANG_LIMIT` passes.

    The slow answer is a `CLEAN` verdict deliberately: every denial asserted below is then
    attributable to the bound alone, never to the verdict the transport would eventually give.
    """
    def transport(_messages):
        release.wait(HANG_LIMIT)
        if finished is not None:
            finished.set()
        return CLEAN
    return transport


def test_a_transport_still_silent_at_the_bound_costs_the_outage_denial_not_a_wait():
    """The engine call site. Silence at the bound must land in `decide`'s existing outage branch.

    Asserted on the decision and on the categorized result the agent is handed -- the effects --
    never on whether any wrapper ran. The outage must name the configured bound, because the field
    is what survives to the reviewer and "0.2" in it is what shows the argument governed the wait.
    """
    release = threading.Event()
    checker = Checker("sonnet-tier", "sonnet-tier",
                      transport=bounded_transport(_answers_after(release), timeout_seconds=BOUND),
                      retries=0)
    try:
        decision = decide(_draft("The round is $10M."), RECORD, CONTEXT, checker)
    finally:
        release.set()

    assert decision.allowed is False
    assert decision.disposition is Disposition.REDIRECT_FUTILE
    assert decision.findings[0].violation_class is ViolationClass.OTHER
    assert decision.outage, "an expiry that sets no outage reads as a judgement, not a detector outage"
    assert "0.2" in decision.outage, "the outage does not name the bound that expired"

    result = denial_result(decision)
    assert result["is_error"] is True
    assert result["is_retryable"] is False
    assert result["category"] == ViolationClass.OTHER.value


def test_the_refinement_loop_reads_expiry_as_futile_rather_than_hanging():
    """The other call site. `refine` consults `decide`, so expiry must stop the loop, not stall it.

    An outage denial is classed `OTHER`, which `FUTILE_CLASSES` holds, so the loop must escalate
    with the deflection and spend no redraft round: a redraft has nothing to aim at when nothing
    looked at the draft.
    """
    release = threading.Event()
    checker = Checker("sonnet-tier", "sonnet-tier",
                      transport=bounded_transport(_answers_after(release), timeout_seconds=BOUND),
                      retries=0)
    try:
        outcome = refine(_draft("The round is $10M."), RECORD, CONTEXT, checker,
                         redraft=lambda draft, decision: draft, budget=3)
    finally:
        release.set()

    assert outcome.resolved is False
    assert outcome.stopped_for == "futile"
    assert outcome.rounds == 0
    assert outcome.alternative == DEFLECTION


def test_a_hanging_transport_costs_a_redirected_audit_entry_and_never_a_send(tmp_path):
    """The executor chokepoint, end to end: the audit log and the queue, with the tool untouched.

    This is the entry an operator actually reads after the incident: an intent with its outcome
    `redirected`, a verifiable pair, and a handoff whose `detector_outage` says nothing looked at
    the draft -- rather than a gateway still blocked on a checker that will never answer.
    """
    release = threading.Event()
    sent: list[dict] = []
    checker = Checker("sonnet-tier", "sonnet-tier",
                      transport=bounded_transport(_answers_after(release), timeout_seconds=BOUND),
                      retries=0)
    gateway = Gateway(AuditStore(tmp_path / "audit.jsonl"), principal="agent", tier=2)
    queues = ReviewQueues()
    try:
        result = guarded_call(gateway, "send_message", {}, _draft("The round is $10M."),
                              RECORD, CONTEXT, checker,
                              {"send_message": lambda **kw: sent.append(kw) or "sent"},
                              queues=queues)
    finally:
        release.set()

    assert result.allowed is False
    assert sent == [], "the send tool was entered on a draft nothing ever assessed"

    entries, torn = gateway.store.read_all()
    assert [entry.kind for entry in entries] == ["intent", "outcome"]
    assert entries[-1].outcome == "redirected"
    assert torn is False

    handoffs = queues.items(HUMAN_REVIEW)
    assert len(handoffs) == 1
    assert handoffs[0].detector_outage, "the reviewer cannot tell this deny from a judgement"


def test_expiry_is_a_closed_question_and_does_not_burn_the_availability_budget():
    """One hang, one transport call, however many retries the checker holds.

    The retry budget at `Checker.check` is for *availability* -- a transport that failed might
    answer if asked again -- and expiry is not that: the question was asked and the clock closed
    it. The call count is the property here, exactly as it is in
    `test_a_transport_that_declares_terminal_failure_is_not_retried`: each attempt is a real model
    call in production, so an expiry that re-asked twice more would triple the cost of every hang
    while tripling the wait the bound exists to cap. The deny is asserted beside the count, so this
    cannot pass on a path where nothing was consulted at all.
    """
    calls: list[int] = []
    release = threading.Event()

    def hanging(_messages):
        calls.append(1)
        release.wait(HANG_LIMIT)
        return CLEAN

    checker = Checker("sonnet-tier", "sonnet-tier",
                      transport=bounded_transport(hanging, timeout_seconds=BOUND), retries=2)
    try:
        decision = decide(_draft("The round is $10M."), RECORD, CONTEXT, checker)
    finally:
        release.set()

    assert decision.allowed is False
    assert len(calls) == 1, "expiry was retried, so the availability budget is paying for hangs"


def test_the_deny_abandons_the_in_flight_call_rather_than_cancelling_it():
    """The residual the new docstrings disclose, held so the disclosure cannot overstate.

    The wrapper bounds the gate's wait, not the transport's execution: at the deny the call must
    still be in flight, and once released it must run to completion -- abandoned, not cancelled.
    If the first half fails, nothing here was ever in flight and the residual is understated by
    the test; if the second half fails, the wrapper kills what it claims merely to abandon and the
    docstrings overstate the survival.
    """
    release, finished = threading.Event(), threading.Event()
    checker = Checker("sonnet-tier", "sonnet-tier",
                      transport=bounded_transport(_answers_after(release, finished),
                                                  timeout_seconds=BOUND),
                      retries=0)

    try:
        decision = decide(_draft("The round is $10M."), RECORD, CONTEXT, checker)
        assert decision.allowed is False
        assert not finished.is_set(), "the call had already returned, so nothing was abandoned in flight"
    finally:
        release.set()
    assert finished.wait(HANG_LIMIT), "the abandoned call never completed: it was cancelled, not abandoned"


def test_a_bound_of_zero_or_less_is_refused_at_construction():
    """A bound that waits for nothing denies every draft: a whole-gate outage dressed as policy.

    Refused where the number is chosen, before any call is in flight, for the reason `refine`
    refuses a negative budget: the misconfiguration should fail loudly at the one site that made
    it, not surface as a hundred identical outage denials.
    """
    with pytest.raises(ValueError):
        bounded_transport(lambda _messages: CLEAN, timeout_seconds=0)
    with pytest.raises(ValueError):
        bounded_transport(lambda _messages: CLEAN, timeout_seconds=-1.0)


def test_a_bound_that_is_not_a_finite_number_is_refused_at_construction():
    """`float("inf") <= 0` is `False`, so a sign check alone accepts the one value that unbounds
    the wrapper entirely -- an infinite bound is the unwrapped transport wearing the wrapper's
    name, which is worse than no wrapper because the call site reads as covered. NaN fails every
    comparison the same way and would wait forever too (`Thread.join(nan)` raises nothing useful
    at the site that chose it). Both are configuration, so both fail at construction like the sign.
    """
    with pytest.raises(ValueError):
        bounded_transport(lambda _messages: CLEAN, timeout_seconds=float("inf"))
    with pytest.raises(ValueError):
        bounded_transport(lambda _messages: CLEAN, timeout_seconds=float("nan"))


def test_an_answer_inside_the_bound_passes_through_and_the_default_needs_no_argument():
    """The wrapper must change silence only. A prompt answer is the gate's ordinary day.

    Constructed with no `timeout_seconds` deliberately: the default's existence is part of the
    contract, and this is the one test that would fail if the argument became required or the
    default started being read from somewhere other than the signature. Watched red against a
    wrapper that raises its expiry unconditionally -- the minimal implementation that passes the
    expiry tests above -- failing here at `allowed is True` with the outage denial in its place.
    """
    checker = Checker("sonnet-tier", "sonnet-tier",
                      transport=bounded_transport(lambda _messages: CLEAN), retries=0)

    decision = decide(_draft("The round is $10M."), RECORD, CONTEXT, checker)

    assert decision.allowed is True
    assert decision.disposition is Disposition.ALLOW
    assert decision.outage is None


def test_a_transport_that_fails_inside_the_bound_keeps_its_message_and_its_retry_budget():
    """A failure is not a hang, and the wrapper must not relabel one as the other.

    Two effects that each catch the relabelling edit -- the wrapper catching the worker's
    exception and raising its own expiry error. The outage must carry the transport's own words,
    because that text is what a reviewer debugs from; and the availability budget must still be
    spent, because a failing transport might answer the retry, which is the budget's whole reason.
    The call count is the budget's only observable, as in the closed-question test above. Watched
    red against exactly that relabelling: the outage read "gave no answer within" instead of
    "down" and the retry was never made.
    """
    calls: list[int] = []

    def fails_fast(_messages):
        calls.append(1)
        raise TimeoutError("down")

    checker = Checker("sonnet-tier", "sonnet-tier",
                      transport=bounded_transport(fails_fast, timeout_seconds=BOUND), retries=1)

    decision = decide(_draft("The round is $10M."), RECORD, CONTEXT, checker)

    assert decision.allowed is False
    assert decision.outage is not None and "down" in decision.outage
    assert "gave no answer within" not in decision.outage
    assert len(calls) == 2, "a failure inside the bound stopped costing the availability budget"


def test_a_terminal_declaration_inside_the_bound_is_not_relabelled_or_retried():
    """`testing/recorded.py` raises `CheckerUnavailable` for a verdict recorded as unavailable.

    Through the wrapper that declaration must arrive as **the same object** -- not the same type
    with the same words. `test_a_transport_that_declares_terminal_failure_is_not_retried` already
    measured why a message match proves nothing here: a wrapper re-raising
    `CheckerUnavailable(str(exc))` carries identical text, so only identity can tell the original
    from a relabel, and only the call count can tell a closed question from a retried one. Watched
    red against exactly that message-preserving relabel: type and message both matched and the
    `is` failed.
    """
    calls: list[int] = []
    sentinel = CheckerUnavailable("declared terminal by the transport")

    def declares_terminal(_messages):
        calls.append(1)
        raise sentinel

    checker = Checker("sonnet-tier", "sonnet-tier",
                      transport=bounded_transport(declares_terminal, timeout_seconds=BOUND),
                      retries=2)

    with pytest.raises(CheckerUnavailable) as excinfo:
        checker.check(_draft("The round is $10M."), RECORD)

    assert excinfo.value is sentinel, "the declaration arrived rewrapped, not as itself"
    assert len(calls) == 1
