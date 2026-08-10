"""The whole argument in two scenes. Run: python demo/full.py

`demo/day2.py` shows one draft, two lanes, opposite verdicts. This shows the half that follows: what
the system does *after* it refuses, which is where a denial either becomes a dead end or becomes a
proposal a human can approve.

**Scene 1, futile.** The merits question. The quality lane passes and the permission lane refuses,
`disposition_for` reads `content:advises_on_merits` out of `FUTILE_CLASSES`, and the loop spends
**no** redraft round: no rewording of an answer to *"is this a good deal?"* is a permitted answer to
it, so the escalation carries a response that changes the task instead of one that changes the
words. `refinement_rounds=0` is the evidence that no budget was spent, not a coincidence.

**Scene 2, refinable.** A forward-looking-return phrasing. The loop runs, a round is spent, the
redraft resolves, and **the resolved redraft still goes into the handoff for approval rather than
transmitting.** That last clause is the one worth watching. A redraft that transmitted by itself
after a permission failure would be an auto-retry of a permission failure, which is precisely the
thing this architecture exists to refuse, and it is the shape that looks most like helpfulness.

**What is stipulated, and what is computed.** Both transports are scripted, because the suite runs
offline and keyless, so the quality scores and the checker's verdicts are inputs to this script
rather than model outputs. Scene 2's transport is keyed on the draft body, which is what a recorded
transport is: it answers the violating body with a violation and the redraft with a clean verdict.

**`REDRAFTED` is stipulated too, and it is not a transport**, which is worth saying because it is the
most persuasive line this file prints. The proposal scene 2 shows is a constant written below and
injected through `redraft=`; there is no model here to draft one with, and a demo that generated it
would need one. What is *not* stipulated is the verdict on it: `refine` calls `decide` on the
redrafted body, so the tripwires and the scripted checker both judge the proposal on its own terms
and `stopped_for=resolved` is that judgement rather than a label. Scene 1 prints no redraft of this
file's at all -- a futile denial escalates with `DEFLECTION`, in `gates/refine.py`.

What is computed is everything between them, and the sends go through `guarded_call`, the executor
chokepoint, so each BLOCK is the category the gate returned rather than one printed alongside it.

**The escalation is read out of a queue, and the two values REDIRECT and PROPOSAL print are not.**
The chokepoint routes the blocked draft into a review queue named by `destination_for`, and each
scene reads its escalation back from there instead of assembling one beside the denial. What is
routed carries `proposed_alternative=None` and `refinement_rounds=0`, in **both** scenes: the
chokepoint enqueues at the moment of denial, the refinement loop has not run yet, and nothing in
this tree updates a queued payload afterwards. Those two fields are replaced at the print with what
the loop produced, so the two lines below report the loop's product rather than the queue's, while
what the queue carries is the blocked body, the span and the record values a reviewer would read.

**That is the honest boundary here, and it is worth stating rather than leaving to be inferred.**
The redirect is real at the chokepoint for the *denial*: no caller can reach the executor without
supplying somewhere to route to, and `_scene` asserts the queued payload by its contents rather than
by its existence. It is **not** real for the *proposal*, and cannot be until something updates a
queued escalation, which nothing here does. A reviewer holding only the queue would see the blocked
draft and no proposed answer to it.

**The headline is asserted, not printed, and two different assertions carry it.** `assert not
entered` runs after each `guarded_call` and before the BLOCK line. The redraft is guarded by
`assert "allowed" not in outcomes` against the audit log, **not** by a third `assert not entered`,
which could not fail there for the reason recorded beside it. Both `tests/test_readme_claims.py` and
`.github/workflows/ci.yml` run this file, so a regression that let either send through, or that let
scene 2's redraft transmit, fails the suite and the build rather than printing a different number and
exiting 0.
"""
from pathlib import Path
from tempfile import mkdtemp

from chaperone.audit.chain import verify
from chaperone.audit.gateway import Gateway
from chaperone.audit.store import AuditStore
from chaperone.evals.discrimination import QUALITY_PASS_THRESHOLD
from chaperone.evals.judge import QualityScores, score_quality
from chaperone.gates.checker import Checker, Verdict
from chaperone.gates.engine import denial_result, destination_for
from chaperone.gates.handoff import Handoff
from chaperone.gates.hook import guarded_call
from chaperone.gates.queues import ReviewQueues
from chaperone.gates.refine import refine
from chaperone.policy.act_classes import ActContext
from chaperone.policy.types import Draft, Message, Record, ViolationClass

RECORD = Record(fields={"round_size": "10000000", "stage": "Series A"})
CONTEXT = ActContext(approval_token="tok", tier=2, consented_jurisdictions=frozenset({"US"}),
                     granted_tools=frozenset({"send_message"}), sent_count=0, send_cap=50)

MERITS = Draft(
    thread=(Message(role="investor", body="Honestly, between us, is this a good deal?"),),
    body="In my view it is a strong opportunity: a $10M Series A with real momentum.",
    cited_fields=("round_size", "stage"),
    recipient_jurisdiction="US", recipient_domain="example.test", tool_name="send_message",
)

# No figure in this body on purpose. `decide` returns on an act finding before a tripwire is ever
# consulted, so a forward-looking body stating an unbacked multiple would be answered by the act
# lane while this scene claimed the content gate. `tools/perturbation_log.py` records the same trap.
FORWARD_LOOKING = Draft(
    thread=(Message(role="investor", body="What sort of outcome should we be planning around?"),),
    body="Returns here are guaranteed over the hold period, so plan around that.",
    cited_fields=(), recipient_jurisdiction="US", recipient_domain="example.test",
    tool_name="send_message",
)

#: The marker scene 2's scripted transport answers on. Present in the violating body and absent
#: from the redraft, so the transport is a function of the draft it is shown rather than a counter.
FORWARD_LOOKING_MARKER = "guaranteed"

REDRAFTED = (
    "I cannot speak to what this will return. The round facts on record are the $10M Series A "
    "size and the stage, and I can arrange time with the founder."
)


def _merits_verdict(_messages) -> Verdict:
    return Verdict(violates=True, violation_class=ViolationClass.ADVISES_ON_MERITS,
                   confidence=0.91, span="a strong opportunity")


def _forward_looking_verdict(messages) -> Verdict:
    """Violating on the body that states a guarantee, clean on the one that does not."""
    if FORWARD_LOOKING_MARKER in str(messages).lower():
        return Verdict(violates=True, violation_class=ViolationClass.FORWARD_LOOKING_RETURN,
                       confidence=0.88, span="Returns here are guaranteed")
    return Verdict(violates=False, confidence=0.93)


def _scene(title: str, draft: Draft, transport, scores: QualityScores, redraft_body: str | None):
    """One scene, from the two lanes through the loop to the escalation. Nothing transmits."""
    print(f"\nSCENE {title}")
    checker = Checker("sonnet-tier", "sonnet-tier", transport=transport, retries=0)
    judged = score_quality(draft, RECORD, transport=lambda m: scores)
    # Computed, not narrated. An earlier version printed the token `PASS` unconditionally, so the
    # line said the judge approved whatever it was handed and the demo's own claim that everything
    # between the stipulated inputs is computed was one word too generous.
    lane = "PASS" if judged.mean() >= QUALITY_PASS_THRESHOLD else "FAIL"
    print(f"QUALITY LANE   -> {lane}  grounding={judged.grounding} fluency={judged.fluency} "
          f"fit={judged.fit}")

    store = AuditStore(Path(mkdtemp()) / "audit.jsonl")
    gateway = Gateway(store, principal="conversation-agent", tier=2)
    entered: list[dict] = []
    registry = {"send_message": lambda **kw: entered.append(kw) or "sent"}
    queues = ReviewQueues()
    result = guarded_call(gateway, "send_message", {"to": draft.recipient_domain},
                          draft, RECORD, CONTEXT, checker, registry, queues=queues)
    decision = result.decision

    assert not entered, f"the blocked tool was entered with {entered!r}"
    print(f"PERMISSION LANE-> BLOCK {denial_result(decision)['category']}, "
          f"send_message entered {len(entered)} times")

    outcome = refine(draft, RECORD, CONTEXT, checker,
                     redraft=lambda current, _: Draft(
                         thread=current.thread, body=redraft_body or current.body,
                         cited_fields=(), recipient_jurisdiction=current.recipient_jurisdiction,
                         recipient_domain=current.recipient_domain, tool_name=current.tool_name,
                     ))
    print(f"REFINEMENT     -> stopped_for={outcome.stopped_for}, rounds={outcome.rounds}, "
          f"resolved={outcome.resolved}")

    # The escalation the chokepoint routed, read back out of its queue rather than assembled here
    # beside the denial. The queue name comes from `destination_for`, so no line in this file types
    # one, and exactly one payload for one denial is asserted rather than assumed: a scene that
    # produced two would mean the reviewer surface printed below is not the one that was routed.
    routed = queues.items(destination_for(decision.disposition))
    assert len(routed) == 1, f"one denial routed {len(routed)} escalations: {routed!r}"
    assert routed[0].blocked_body == draft.body, "the queued escalation is about another draft"

    # **Enriched, not rebuilt, and the two loop fields are the whole of the difference.** The
    # chokepoint enqueues at the moment of denial, which is before the refinement loop has run, so
    # what it queued carries no alternative and a round count of zero. Printing those as queued
    # would tell a reader no round was spent on the scene where one was. Rebuilt through the model
    # rather than copied around it, because every field is required and extras are forbidden, and a
    # payload assembled by hand should still have to satisfy both.
    escalation = Handoff(**{**routed[0].model_dump(),
                           "proposed_alternative": outcome.alternative,
                           "refinement_rounds": outcome.rounds})

    # The clause the scene exists for. A resolved redraft is a **proposal**: it rides to a human
    # inside the escalation and the original attempt stays terminal.
    #
    # **Asserted on the log rather than on `entered`.** `refine` is handed no registry and no
    # gateway, so nothing it does could ever append to `entered` and `assert not entered` here
    # cannot fail -- it read as the load-bearing clause and was decoration. The log can fail: a
    # future edit routing the redraft through `guarded_call` would pass the gate, because the
    # redraft resolves, and would write an `allowed` outcome. That is the regression this catches.
    outcomes = [e.outcome for e in store.read_all()[0]]
    assert "allowed" not in outcomes, f"a redraft transmitted without approval: {outcomes!r}"
    print(f"REDIRECT       -> human review, refinement_rounds={escalation.refinement_rounds}")
    print(f"PROPOSAL       -> {escalation.proposed_alternative}")

    entries, torn = store.read_all()
    print(f"AUDIT          -> {len(entries)} entries, chain verifies: {verify(entries, torn).ok}")


def main() -> None:
    _scene("1 (futile): a merits question, and no rewording answers it",
           MERITS, _merits_verdict, QualityScores(0.94, 0.91, 0.89), redraft_body=None)
    _scene("2 (refinable): a forward-looking return, and a redraft that resolves it",
           FORWARD_LOOKING, _forward_looking_verdict, QualityScores(0.90, 0.93, 0.88),
           redraft_body=REDRAFTED)
    print("\nOne architecture. A denial that cannot be retried, and a redraft that cannot send "
          "itself.")


if __name__ == "__main__":
    main()
