from datetime import datetime
from pathlib import Path

import pytest

from chaperone.audit.chain import verify
from chaperone.audit.gateway import Gateway, transmit
from chaperone.audit.store import AuditStore
from chaperone.policy.types import Decision, Disposition, Finding, ViolationClass

ALLOW = Decision(allowed=True, findings=(), disposition=Disposition.ALLOW)
DENY = Decision(
    allowed=False,
    findings=(Finding(ViolationClass.ADVISES_ON_MERITS, "merits", "a good deal"),),
    disposition=Disposition.REDIRECT_FUTILE,
)


def _gateway(tmp_path: Path) -> Gateway:
    return Gateway(AuditStore(tmp_path / "audit.jsonl"), principal="agent", tier=2)


def test_an_allowed_call_writes_exactly_one_outcome_entry(tmp_path: Path):
    gateway = _gateway(tmp_path)
    gateway.call("read_record", {"id": "x"}, decide=lambda: ALLOW, execute=lambda: "value")
    entries, _ = gateway.store.read_all()
    assert [e.kind for e in entries] == ["outcome"]
    assert entries[0].outcome == "allowed"


def test_an_effectful_send_writes_an_intent_entry_before_the_outcome(tmp_path: Path):
    gateway = _gateway(tmp_path)
    gateway.call("send_message", {"to": "a@example.test"}, decide=lambda: ALLOW,
                 execute=lambda: "sent", effectful=True)
    entries, _ = gateway.store.read_all()
    assert [e.kind for e in entries] == ["intent", "outcome"]


def test_a_denied_call_never_references_the_tool_function(tmp_path: Path):
    """Ordering is the guarantee. Not 'was not called' - 'was never looked up'."""
    gateway = _gateway(tmp_path)
    referenced = False

    def execute():
        nonlocal referenced
        referenced = True
        return "sent"

    result = gateway.call("send_message", {"to": "a@example.test"},
                          decide=lambda: DENY, execute=execute, effectful=True)
    assert result.allowed is False
    assert referenced is False


def test_an_outcome_entry_is_written_even_when_the_tool_raises(tmp_path: Path):
    gateway = _gateway(tmp_path)

    def boom():
        raise KeyError("no such tool")

    with pytest.raises(KeyError):
        gateway.call("missing", {}, decide=lambda: ALLOW, execute=boom)
    entries, _ = gateway.store.read_all()
    assert entries[-1].outcome == "error"


def test_the_entry_records_an_argument_digest_and_never_the_raw_arguments(tmp_path: Path):
    gateway = _gateway(tmp_path)
    gateway.call("send_message", {"to": "someone@example.test"},
                 decide=lambda: ALLOW, execute=lambda: "sent", effectful=True)
    raw = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "someone@example.test" not in raw
    assert len(raw.splitlines()) == 2


def test_the_entry_records_principal_and_tier(tmp_path: Path):
    gateway = _gateway(tmp_path)
    gateway.call("read_record", {}, decide=lambda: ALLOW, execute=lambda: None)
    entries, _ = gateway.store.read_all()
    assert entries[0].principal == "agent"
    assert entries[0].tier == 2


def test_intent_precedes_outcome_in_the_recorded_order(tmp_path: Path):
    """The chain proves integrity, not sequence semantics. Assert order separately."""
    gateway = _gateway(tmp_path)
    gateway.call("send_message", {}, decide=lambda: ALLOW, execute=lambda: "sent", effectful=True)
    entries, _ = gateway.store.read_all()
    assert entries[0].seq < entries[1].seq
    assert entries[0].kind == "intent" and entries[1].kind == "outcome"


def test_a_denied_effectful_call_still_writes_both_entries(tmp_path: Path):
    gateway = _gateway(tmp_path)
    gateway.call("send_message", {}, decide=lambda: DENY, execute=lambda: "sent", effectful=True)
    entries, _ = gateway.store.read_all()
    assert [e.kind for e in entries] == ["intent", "outcome"]
    assert entries[1].outcome == "redirected"


def test_transmit_routes_through_the_gateway_as_an_effectful_send(tmp_path: Path):
    """`transmit` is the one symbol `tools/static_audit.py` contains to this module.

    Nothing under `src/chaperone/` calls it -- that containment is the point -- so without this
    test the audit's only real subject is a function no test exercises, and emptying its body
    would leave the suite green. Write-ahead is what is asserted, not the return value alone: a
    `transmit` that forgot `effectful=True` would still hand back an allowed result, and the whole
    reason a send is routed through this symbol is that a send is the call that must be logged
    before it happens.
    """
    gateway = _gateway(tmp_path)
    result = transmit(gateway, "send_message", {"to": "a@example.test"},
                      decide=lambda: ALLOW, execute=lambda: "sent")
    entries, _ = gateway.store.read_all()
    assert result.allowed is True
    assert result.value == "sent"
    assert [e.kind for e in entries] == ["intent", "outcome"]


def test_the_result_names_the_seqs_of_the_entries_that_were_actually_written(tmp_path: Path):
    """Two mechanisms report the same numbers. This is the input that would separate them.

    `outcome_seq` is asked for inside the `return` expression, which Python evaluates *before* the
    `finally` clause allocates the outcome entry's seq -- so it is a prediction. They agree only
    because both sides ask `_next_seq`, which reads the answer off the log and consumes nothing. A
    counter that handed back a number and incremented itself agreed only while nothing else wrote
    between the two calls, and `recovery.resume` is something else that writes; a caller holding a
    seq that names a different entry is worse than one holding none.
    """
    gateway = _gateway(tmp_path)

    effectful = gateway.call("send_message", {}, decide=lambda: ALLOW, execute=lambda: "sent",
                             effectful=True)
    intent, outcome = gateway.store.read_all()[0]
    assert (effectful.intent_seq, effectful.outcome_seq) == (intent.seq, outcome.seq)

    plain = gateway.call("read_record", {}, decide=lambda: ALLOW, execute=lambda: "v")
    entries, _ = gateway.store.read_all()
    assert plain.intent_seq is None
    assert plain.outcome_seq == entries[-1].seq


def test_a_refusal_carrying_an_allow_disposition_is_recorded_as_denied(tmp_path: Path):
    """The one arm no other test reaches, so `"denied"` is a literal nothing currently produces.

    Every refusal elsewhere in this file carries a redirect disposition, so the gateway's `"denied"`
    branch never executes and the literal could be replaced with anything at all while the suite
    stayed green. A refusal whose disposition is nonetheless ALLOW is the input that separates the
    two arms -- and the two are not interchangeable downstream, because a redirect tells the
    operator a refinement exists and a denial tells them it does not.
    """
    gateway = _gateway(tmp_path)
    refused = Decision(allowed=False, findings=(), disposition=Disposition.ALLOW)

    result = gateway.call("send_message", {}, decide=lambda: refused, execute=lambda: "sent")

    entries, _ = gateway.store.read_all()
    assert result.allowed is False
    assert entries[-1].outcome == "denied"


def test_an_interrupted_send_leaves_its_intent_pending_and_its_outcome_an_error(tmp_path: Path):
    """Design spec 5.4 branch (a): the pair survives a failure, and the two must not agree.

    An intent that optimistically recorded `"allowed"` would make a send that blew up read back as
    a completed one -- and the interrupted case is precisely the one where the process may never
    return to correct it. Both literals are reached only here: the brief's raising test is not
    effectful, so it never writes an intent at all.
    """
    gateway = _gateway(tmp_path)

    def boom():
        raise KeyError("no such tool")

    with pytest.raises(KeyError):
        gateway.call("send_message", {}, decide=lambda: ALLOW, execute=boom, effectful=True)

    entries, _ = gateway.store.read_all()
    assert [(e.kind, e.outcome) for e in entries] == [("intent", "pending"), ("outcome", "error")]


def test_an_intent_and_its_outcome_carry_the_same_argument_digest(tmp_path: Path):
    """The digest is what lets a recovery pass match an outcome back to the intent it resolves.

    Two sends with *different* arguments are used, so the equality across each pair cannot be
    satisfied by a gateway that writes one constant digest everywhere -- which would pair every
    intent with every outcome and make design spec 5.4's branch (b)/(c) split meaningless.
    """
    gateway = _gateway(tmp_path)
    gateway.call("send_message", {"to": "a@example.test"}, decide=lambda: ALLOW,
                 execute=lambda: "sent", effectful=True)
    gateway.call("send_message", {"to": "b@example.test"}, decide=lambda: ALLOW,
                 execute=lambda: "sent", effectful=True)

    first_intent, first_outcome, second_intent, second_outcome = gateway.store.read_all()[0]

    assert first_intent.arg_digest == first_outcome.arg_digest
    assert second_intent.arg_digest == second_outcome.arg_digest
    assert first_intent.arg_digest != second_intent.arg_digest


def test_the_gateway_writes_one_contiguous_verifiable_run_of_entries(tmp_path: Path):
    """Seq is an ordering the recovery pass reads, so gaps are not free.

    `test_intent_precedes_outcome_in_the_recorded_order` asserts `<` between two seqs, which a
    counter advancing by any stride satisfies. Contiguity across a mixed run of effectful and
    plain calls is what pins the stride, and `verify` is asserted alongside it because a payload
    the gateway assembles must chain in the store -- Task 6's D2 was exactly a log that read back
    perfectly and failed verification.
    """
    gateway = _gateway(tmp_path)
    gateway.call("send_message", {"to": "a@example.test"}, decide=lambda: ALLOW,
                 execute=lambda: "sent", effectful=True)
    gateway.call("read_record", {"id": "x"}, decide=lambda: ALLOW, execute=lambda: "v")

    entries, torn = gateway.store.read_all()

    assert [e.seq for e in entries] == [0, 1, 2]
    assert torn is False
    assert verify(entries, torn_tail=torn).ok is True


def test_a_gateway_reopened_on_an_existing_log_continues_the_sequence(tmp_path: Path):
    """A restart must not restart the numbering.

    `Gateway` caches its counter at construction from the log's length, so this is the only place
    the resumption is exercised. A gateway that began again at zero would write a second entry 0,
    and every later reader that treats seq as an ordering -- the recovery pass above all -- would
    read the restart as a reordering.
    """
    path = tmp_path / "audit.jsonl"
    first = Gateway(AuditStore(path), principal="agent", tier=2)
    first.call("send_message", {}, decide=lambda: ALLOW, execute=lambda: "sent", effectful=True)

    second = Gateway(AuditStore(path), principal="agent", tier=2)
    second.call("read_record", {}, decide=lambda: ALLOW, execute=lambda: "v")

    entries, _ = AuditStore(path).read_all()
    assert [e.seq for e in entries] == [0, 1, 2]


# --- Fix round 1: three pre-`try` operations that skipped the entry when it mattered most -------


def test_arguments_that_cannot_be_digested_still_produce_their_entries(tmp_path: Path):
    """Finding C's shape inside the code written to fix finding C.

    `arg_digest` is `json.dumps` underneath, and it raised *before* the first entry was written.
    No exotic input is needed to reach it: `{"a": 1, 2: "b"}` has keys of two types and
    `sort_keys` cannot order them, so a plain dict of plain scalars was enough to make a call
    vanish from the log entirely.
    """
    gateway = _gateway(tmp_path)

    gateway.call("send_message", {"a": 1, 2: "b"}, decide=lambda: ALLOW,
                 execute=lambda: "sent", effectful=True)

    entries, _ = gateway.store.read_all()
    assert [e.kind for e in entries] == ["intent", "outcome"]
    assert entries[-1].outcome == "allowed"


def test_a_digest_that_could_not_be_computed_is_marked_and_still_holds_no_raw_argument(
    tmp_path: Path
):
    """Design spec 5.2 binds the degraded path exactly as it binds the clean one.

    The entry records "an argument digest -- canonical JSON, hashed -- never the raw arguments,
    because recipient identifiers are personal data and an audit log is not a place to accumulate
    it." A fallback that wrote `repr(args)` into the log to keep the entry would satisfy the letter
    of "an entry lands" and breach the reason the field is a digest at all.

    Both absence assertions are on strings that **cannot be hex**, because every entry carries two
    64-character hex digests and any short digit string will occur inside one by coincidence -- an
    assertion like `"2026" not in raw` would pass today and start failing on an unrelated change,
    which is the same defect as passing for the wrong reason. `"datetime"` is what `repr` renders
    for the value that defeated canonicalisation, so its absence is a direct statement that the
    degraded rendering never reached the log.
    """
    gateway = _gateway(tmp_path)

    gateway.call("send_message", {"to": "someone@example.test", "when": datetime(2026, 1, 1)},
                 decide=lambda: ALLOW, execute=lambda: "sent", effectful=True)

    raw = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "someone@example.test" not in raw
    assert "datetime" not in raw
    entries, _ = gateway.store.read_all()
    assert all(e.arg_digest.startswith("unavailable:") for e in entries)


def test_two_calls_whose_arguments_resist_digesting_do_not_share_one_digest(tmp_path: Path):
    """A constant sentinel would collapse distinct calls onto one identity.

    Task 24's `resume` pairs an intent with its outcome **by `arg_digest`**, and
    `requires_approval_for` treats a repeated digest as an idempotency key. One shared "digest
    unavailable" marker would pair unrelated records and make an unrelated re-attempt look like a
    duplicate send, so the fallback has to stay a hash of something call-specific.
    """
    gateway = _gateway(tmp_path)
    gateway.call("send_message", {"a": 1, 2: "b"}, decide=lambda: ALLOW,
                 execute=lambda: "sent", effectful=True)
    gateway.call("send_message", {"c": 3, 4: "d"}, decide=lambda: ALLOW,
                 execute=lambda: "sent", effectful=True)

    entries, _ = gateway.store.read_all()
    assert len(entries) == 4
    assert len({e.arg_digest for e in entries}) == 2


def test_a_gate_that_raises_still_writes_an_outcome_and_leaves_no_dangling_intent(tmp_path: Path):
    """Design spec 3.4 keeps the raise; what must change is that the entry lands anyway.

    The boundary engine is "fatal, fails closed -- nothing transmits on an unavailable gate", and
    4.3 treats an unavailable checker as anticipated rather than exceptional. With `decide()`
    outside the `try` an outage left `[('intent', 'pending')]` and no outcome at all, which is the
    one shape Task 24's recovery pass cannot resolve on its own.

    The digest equality is the load-bearing assertion: `resume` pairs an intent with its outcome by
    `arg_digest`, so an outcome carrying a different digest would leave the intent dangling just as
    surely as writing no outcome at all.
    """
    gateway = _gateway(tmp_path)

    def gate_down():
        raise RuntimeError("checker transport unavailable")

    with pytest.raises(RuntimeError):
        gateway.call("send_message", {"to": "a@example.test"}, decide=gate_down,
                     execute=lambda: "sent", effectful=True)

    entries, _ = gateway.store.read_all()
    assert [e.kind for e in entries] == ["intent", "outcome"]
    assert entries[-1].outcome == "unattempted"
    assert entries[0].arg_digest == entries[-1].arg_digest


def test_the_outcome_names_whether_the_tool_was_ever_entered(tmp_path: Path):
    """`"error"` and `"unattempted"` are not interchangeable, and only the gateway can tell them apart.

    A tool that raised may or may not have had its side effect; a call that never reached the tool
    provably did not. Collapsing both onto `"error"` would throw away the one fact an auditor
    cannot reconstruct from anywhere else in the log.

    The third arm is the one that has to be `BaseException`, not `Exception`. `KeyboardInterrupt`,
    `SystemExit`, `GeneratorExit` and `asyncio.CancelledError` do not derive from `Exception`, so a
    handler catching `Exception` misses every one of them -- and Ctrl-C part-way through a network
    send is the ordinary instance, not an exotic one. It is also precisely the case where nobody
    knows whether the message left, which is what `"error"` exists to say. Recording it as
    `"unattempted"` -- a word `entry.py` defines as "the tool was never entered, so no side effect
    occurred" -- tells an operator the send definitely did not happen, and they re-send it.
    """
    gateway = _gateway(tmp_path)

    def boom():
        raise KeyError("no such tool")

    def gate_down():
        raise RuntimeError("checker transport unavailable")

    def interrupted():
        raise KeyboardInterrupt

    with pytest.raises(KeyError):
        gateway.call("send_message", {"n": 1}, decide=lambda: ALLOW, execute=boom)
    with pytest.raises(RuntimeError):
        gateway.call("send_message", {"n": 2}, decide=gate_down, execute=lambda: "sent")
    with pytest.raises(KeyboardInterrupt):
        gateway.call("send_message", {"n": 3}, decide=lambda: ALLOW, execute=interrupted)

    entries, _ = gateway.store.read_all()
    assert [e.outcome for e in entries] == ["error", "unattempted", "error"]


def test_seqs_stay_strictly_increasing_when_the_log_has_a_hole(tmp_path: Path):
    """`len(entries)` was the next seq only while the log had no holes.

    A tear removes a record without removing its number, so counting re-allocated a number that was
    already used -- `[0, 2]` counted two and issued 2 again. Seq is the ordering Task 24's recovery
    pass reads to pair an intent with its outcome, so a duplicate corrupts exactly the field the
    pairing depends on, and nothing raises to say so.
    """
    path = tmp_path / "audit.jsonl"
    gateway = Gateway(AuditStore(path), principal="agent", tier=2)
    for i in range(3):
        gateway.call("read_record", {"n": i}, decide=lambda: ALLOW, execute=lambda: None)
    lines = path.read_bytes().rstrip(b"\n").split(b"\n")
    lines[1] = b'{"seq": 1, "kind": "outc'          # a crash took the middle record
    path.write_bytes(b"\n".join(lines) + b"\n")

    resumed = Gateway(AuditStore(path), principal="agent", tier=2)
    resumed.call("read_record", {"n": 9}, decide=lambda: ALLOW, execute=lambda: None)

    seqs = [entry.seq for entry in AuditStore(path).read_all()[0]]
    assert seqs == sorted(set(seqs)), f"seqs must be strictly increasing, got {seqs}"
    assert len(seqs) == 3


def test_a_gateway_opened_over_a_torn_log_surfaces_the_tear(tmp_path: Path):
    """`read_all` reports a tear and `__init__` was discarding it.

    `count` does not surface `torn` either, so a caller that only ever sees a number cannot learn a
    record was lost. What to *do* about it is Task 24's -- the send cap counts intents and a tear
    may have taken one -- but a gateway that silently proceeds as though the log were whole leaves
    that decision with no input to make it from.
    """
    path = tmp_path / "audit.jsonl"
    gateway = Gateway(AuditStore(path), principal="agent", tier=2)
    gateway.call("read_record", {}, decide=lambda: ALLOW, execute=lambda: None)
    assert gateway.log_torn is False

    with path.open("ab") as handle:
        handle.write(b'{"seq": 9, "kind": "outc')

    assert Gateway(AuditStore(path), principal="agent", tier=2).log_torn is True


def test_a_refused_call_reports_the_seq_of_the_outcome_it_actually_wrote(tmp_path: Path):
    """The third arm of `outcome_seq`, and the only one that returns before `execute`.

    `test_the_result_names_the_seqs_of_the_entries_that_were_actually_written` covers the two
    allowed arms. The refusal path returns from its own `return GatewayResult(False, ...)`, and
    both dispositions leave through it, so one test covers `denied` and `redirected` together --
    it is the same expression on the same line. The prediction is made before the `finally`
    allocates, exactly as it is on the allowed arms, so an off-by-one here would hand a reviewer
    chasing a refusal the seq of somebody else's entry.
    """
    gateway = _gateway(tmp_path)

    denied = gateway.call("send_message", {}, decide=lambda: Decision(allowed=False, findings=(), disposition=Disposition.ALLOW),
                          execute=lambda: "sent", effectful=True)
    entries, _ = gateway.store.read_all()
    assert entries[-1].outcome == "denied"
    assert denied.outcome_seq == entries[-1].seq

    redirected = gateway.call("send_message", {}, decide=lambda: DENY, execute=lambda: "sent",
                              effectful=True)
    entries, _ = gateway.store.read_all()
    assert entries[-1].outcome == "redirected"
    assert redirected.outcome_seq == entries[-1].seq
