"""The send cap counts off the audit log, so the log's durability is an enforcement property."""
from __future__ import annotations

from pathlib import Path

from chaperone.audit.gateway import Gateway
from chaperone.audit.recovery import resume
from chaperone.audit.store import AuditStore
from chaperone.policy.act_classes import ActContext, evaluate_act_classes
from chaperone.policy.types import (
    Decision,
    Disposition,
    Draft,
    Finding,
    Message,
    Record,
    ViolationClass,
)

RECORD = Record(fields={})
ALLOW = Decision(allowed=True, findings=(), disposition=Disposition.ALLOW)
DRAFT = Draft(thread=(Message(role="investor", body="?"),), body="Hello.", cited_fields=(),
              recipient_jurisdiction="US", recipient_domain="example.test", tool_name="send_message")


def _context(sent: int, cap: int) -> ActContext:
    return ActContext(approval_token="tok", tier=2, consented_jurisdictions=frozenset({"US"}),
                      granted_tools=frozenset({"send_message"}), sent_count=sent, send_cap=cap)


def _send(gateway: Gateway, n: int) -> None:
    gateway.call("send_message", {"n": n}, decide=lambda: ALLOW, execute=lambda: "sent",
                 effectful=True)


#: The seq a crash leaves free: two complete sends occupy 0..3.
_DANGLING_SEQ = 4


def _log_with_a_dangling_intent(path: Path) -> Gateway:
    """Two complete sends and one intent whose outcome never got written -- the crash's shape."""
    gateway = Gateway(AuditStore(path), principal="agent", tier=2)
    for i in range(2):
        _send(gateway, i)
    gateway.store.append(dict(seq=_DANGLING_SEQ, kind="intent", tool="send_message",
                              principal="agent", tier=2, scope="send_message", outcome="pending",
                              arg_digest="dangling", seed=None))
    return gateway


def test_the_cap_counts_intents_from_the_log(tmp_path: Path):
    gateway = Gateway(AuditStore(tmp_path / "a.jsonl"), principal="agent", tier=2)
    for i in range(3):
        _send(gateway, i)
    assert gateway.sent_count() == 3


def test_the_predicate_blocks_at_the_cap_using_the_counted_value(tmp_path: Path):
    """Equality on the findings, and on the detail the count reaches the predicate as.

    `in [...]` proves a cap finding fired, which a predicate hard-coded to fire would also satisfy.
    The detail is `sent/cap`, so `"2/2"` is the number read off the log arriving at the predicate
    -- and the equality closes the other side, that nothing else about this draft is being flagged.
    """
    gateway = Gateway(AuditStore(tmp_path / "a.jsonl"), principal="agent", tier=2)
    for i in range(2):
        _send(gateway, i)
    findings = evaluate_act_classes(DRAFT, RECORD, _context(gateway.sent_count(), cap=2))
    assert findings == (Finding(ViolationClass.SEND_CAP_EXCEEDED, "2/2", None),)


def test_a_lost_log_line_would_have_made_the_cap_fail_open(tmp_path: Path):
    """The reason durability is not housekeeping: the counter IS the log.

    This one does not assert correct behaviour. It demonstrates the failure that fsync, binary
    append and torn-tail detection exist to prevent, so the reason for them is recorded in the
    suite rather than only in prose. The bytes are cut on a line boundary, which is what a
    text-mode buffer losing its tail leaves behind: nothing is torn, nothing is detectable, and the
    count is simply lower than the truth.
    """
    path = tmp_path / "a.jsonl"
    gateway = Gateway(AuditStore(path), principal="agent", tier=2)
    for i in range(2):
        _send(gateway, i)
    assert gateway.sent_count() == 2
    lines = path.read_bytes().split(b"\n")
    path.write_bytes(b"\n".join(lines[:2]) + b"\n")
    truncated = Gateway(AuditStore(path), principal="agent", tier=2)
    assert truncated.log_torn is False, "a clean cut leaves nothing for the tear check to find"
    assert truncated.sent_count() < 2


def test_a_dangling_intent_keeps_consuming_the_cap_before_recovery_runs(tmp_path: Path):
    """§5.4's conservative default, stated as arithmetic: an unresolved intent is a send."""
    gateway = _log_with_a_dangling_intent(tmp_path / "a.jsonl")
    assert gateway.sent_count() == 3


def test_an_aborted_intent_is_released_from_the_cap(tmp_path: Path):
    """`ResumeAction.counts_against_cap` asserted where it has to be true -- at the predicate.

    §5.4(b) says an intent that was stale and whose side effect is verifiably absent is released.
    Read as a dataclass field, that claim is satisfied by a `False` nothing consumes; read here, it
    is the difference between the third send being refused and being permitted. Nothing but the
    recovery pass changes between this test and the one below it.
    """
    gateway = _log_with_a_dangling_intent(tmp_path / "a.jsonl")
    resume(gateway.store, side_effect_absent=lambda d: True, stale_after_seq=_DANGLING_SEQ)
    assert gateway.sent_count() == 2
    assert evaluate_act_classes(DRAFT, RECORD, _context(gateway.sent_count(), cap=3)) == ()


def test_an_unknown_intent_keeps_consuming_the_cap_after_recovery_runs(tmp_path: Path):
    """§5.4(c): "the conservative default is that an unresolved intent keeps consuming the cap".

    Green against the count as the brief wrote it, and stated anyway: it is what separates
    releasing on branch (b) from releasing on any *resolved* intent, which is the over-wide fix the
    test above would accept on its own.
    """
    gateway = _log_with_a_dangling_intent(tmp_path / "a.jsonl")
    resume(gateway.store, side_effect_absent=lambda d: None, stale_after_seq=_DANGLING_SEQ)
    assert gateway.sent_count() == 3
    assert evaluate_act_classes(DRAFT, RECORD, _context(gateway.sent_count(), cap=3)) == (
        Finding(ViolationClass.SEND_CAP_EXCEEDED, "3/3", None),
    )


def test_a_torn_log_counts_the_record_the_tear_took(tmp_path: Path):
    """`AuditStore.count` reports a number and drops `torn`; the cap is the caller that cannot.

    A crash mid-write leaves a partial line. The record it was going to be may have been an
    intent, so the count read alone comes back one short and the cap permits one send too many --
    §5.3's enforcement predicate failing open, at the layer that decides. The store's own docstring
    says a caller using `count` for a cap must read `torn` too; this is that read, asserted as the
    number the predicate receives rather than as a flag someone could have looked at.

    Two sends are on disk and intact. A third is torn away mid-record, and the cap of 3 must refuse
    rather than permit -- over-counting a send that may not have happened costs one send of
    headroom, under-counting one that did costs a breach.
    """
    path = tmp_path / "a.jsonl"
    gateway = Gateway(AuditStore(path), principal="agent", tier=2)
    for i in range(2):
        _send(gateway, i)
    with path.open("ab") as handle:
        handle.write(b'{"seq": 4, "kind": "int')

    torn_gateway = Gateway(AuditStore(path), principal="agent", tier=2)
    assert torn_gateway.store.count(lambda e: e.kind == "intent") == 2, "the count alone is short"
    assert torn_gateway.sent_count() == 3
    assert evaluate_act_classes(DRAFT, RECORD, _context(torn_gateway.sent_count(), cap=3)) == (
        Finding(ViolationClass.SEND_CAP_EXCEEDED, "3/3", None),
    )
