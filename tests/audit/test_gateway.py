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

    `outcome_seq` is read from `self._seq` inside the `return` expression, which Python evaluates
    *before* the `finally` clause allocates the outcome entry's seq. They agree only because
    `_next_seq` hands back the value it then increments -- an off-by-one on either side detaches
    the number the caller is given from the number on disk, and a caller holding a seq that names
    a different entry is worse than one holding none.
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
