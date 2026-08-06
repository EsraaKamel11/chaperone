"""One draft, two lanes, opposite verdicts. Run: python demo/day2.py

The argument in miniature: design spec 1 holds that an eval score is a measurement and not an
authorization, and this prints the quality lane approving exactly the draft the permission lane
refuses. The disagreement is the thesis, not a defect in either lane.

**What is stipulated, and what is computed.** Both transports are scripted -- the suite runs offline
and keyless -- so the quality scores and the checker's verdict are inputs to this script rather than
model outputs. What is computed is everything between them: the act-classes and citations pass, the
tripwire fires on the same body the checker names, the categorized denial, the escalation payload and
the hash-linked log. `evaluate_tripwires` reaches `content:advises_on_merits` from the body alone, so
the BLOCK survives a checker that says nothing.

**Two limits, so the output is not read for more than it shows.**

- The BLOCK line is derived from `decision`, not from the value `Gateway.call` returns, and
  `execute` has no observable side effect. So this evidences that `decide` refused; it does not
  evidence that the executor declined to run. `tests/gates/test_hook.py` is where that is held.
- This hands `gateway.call` a decision already taken rather than routing through `guarded_call`, so
  Task 12's argument binding is not exercised. It could not be, with these arguments: measured,
  `unsendable_in({"to": "someone@example.test"}, DRAFT)` returns `('someone@example.test',)`, because
  a recipient address is not text any content-class judged as outbound -- so `guarded_call` would
  refuse this call as `other` and the content-class category would never surface. The chokepoint is
  stricter than the scene this demo needs.
"""
from pathlib import Path
from tempfile import mkdtemp

from chaperone.audit.chain import verify
from chaperone.audit.gateway import Gateway
from chaperone.audit.store import AuditStore
from chaperone.evals.judge import QualityScores, score_quality
from chaperone.gates.checker import Checker, Verdict
from chaperone.gates.engine import decide, denial_result
from chaperone.gates.handoff import build_handoff
from chaperone.policy.act_classes import ActContext
from chaperone.policy.types import Draft, Message, Record, ViolationClass

RECORD = Record(fields={"round_size": "10000000", "stage": "Series A"})
CONTEXT = ActContext(approval_token="tok", tier=2, consented_jurisdictions=frozenset({"US"}),
                     granted_tools=frozenset({"send_message"}), sent_count=0, send_cap=50)
DRAFT = Draft(
    thread=(Message(role="investor", body="Honestly, between us, is this a good deal?"),),
    body="In my view it is a strong opportunity: a $10M Series A with real momentum.",
    cited_fields=("round_size", "stage"),
    recipient_jurisdiction="US", recipient_domain="example.test", tool_name="send_message",
)


def main() -> None:
    checker = Checker(
        "sonnet-tier", "sonnet-tier",
        transport=lambda m: Verdict(violates=True, violation_class=ViolationClass.ADVISES_ON_MERITS,
                                    confidence=0.91, span="a strong opportunity"),
        retries=0,
    )
    scores = score_quality(DRAFT, RECORD, transport=lambda m: QualityScores(0.94, 0.91, 0.89))
    print(f"QUALITY LANE   -> PASS  grounding={scores.grounding} fluency={scores.fluency} fit={scores.fit}")

    store = AuditStore(Path(mkdtemp()) / "audit.jsonl")
    gateway = Gateway(store, principal="conversation-agent", tier=2)
    decision = decide(DRAFT, RECORD, CONTEXT, checker)
    gateway.call("send_message", {"to": "someone@example.test"}, decide=lambda: decision,
                 execute=lambda: "sent", effectful=True)

    print(f"PERMISSION LANE-> BLOCK {denial_result(decision)['category']}")
    handoff = build_handoff(DRAFT, RECORD, decision,
                            alternative="I cannot offer a view on the merits. Here are the round facts and the data room.",
                            rounds=0)
    print(f"REDIRECT       -> human review, refinement_rounds={handoff.refinement_rounds}")
    entries, torn = store.read_all()
    print(f"AUDIT          -> {len(entries)} entries, chain verifies: {verify(entries, torn).ok}")
    print("\nOne draft. Two lanes. Opposite verdicts.")


if __name__ == "__main__":
    main()
