"""The send cap counts off the audit log, so the log's durability is an enforcement property."""
from __future__ import annotations

from pathlib import Path

import pytest

from chaperone.audit.gateway import Gateway
from chaperone.audit.recovery import Branch, resume
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
    """§5.4's conservative default, stated as arithmetic: an unresolved intent is a send.

    Green against the count as the brief wrote it -- disclosed, not presented as a caught defect.
    It is the baseline the two tests below are read against: without it, "the abort released one"
    and "the unknown released none" are two numbers with nothing to be a difference from.
    """
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


class _StoreWhoseOutcomeWriteCanFail(AuditStore):
    """A store whose outcome append fails while `dying` is set, leaving the intent dangling.

    Not a stub for anything under test: it injects the one crash `Gateway`'s own docstring names as
    its remaining finding-C-shaped hole -- "`store.append` can fail on a full disk; the outcome
    entry is then lost". It is how a live in-flight intent is produced through the real `call` path
    rather than hand-written into the log, which is the whole point of the test below.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.dying = False

    def append(self, payload: dict):
        if self.dying and payload["kind"] == "outcome":
            raise OSError("no space left on device")
        return super().append(payload)


def test_a_send_issued_after_recovery_is_not_released_from_the_cap_by_the_next_recovery(
    tmp_path: Path,
):
    """The ordering recovery exists for, driven end to end: crash, restart, resume, keep sending.

    The gateway numbers its entries and so does `resume`. If the gateway's counter is cached at
    construction, the sends that follow a recovery pass are numbered over the top of the entries
    recovery just wrote -- and a live intent stops being at the log's tail. The next restart hands
    `resume` the previous run's maximum seq as its staleness boundary, that live intent now sits at
    or below it, the probe finds no delivery record because the send is still in flight, and
    §5.4(b) fires: **aborted, released from the cap, for a message that may already have gone out.**

    Asserted as the release, not as a seq comparison. The numbering is the mechanism; the cap is
    what the numbering is load-bearing for.
    """
    store = _StoreWhoseOutcomeWriteCanFail(tmp_path / "a.jsonl")
    store.append(dict(seq=0, kind="intent", tool="send_message", principal="agent", tier=2,
                      scope="send_message", outcome="pending", arg_digest="crashed", seed=None))

    gateway = Gateway(store, principal="agent", tier=2)
    resume(store, side_effect_absent=lambda d: True, stale_after_seq=0)
    boundary = max(e.seq for e in store.read_all()[0])

    store.dying = True                                   # the second run dies mid-send
    with pytest.raises(OSError):
        _send(gateway, 9)
    store.dying = False                                  # and the process restarts

    actions = resume(store, side_effect_absent=lambda d: True, stale_after_seq=boundary)
    assert [(a.branch, a.counts_against_cap) for a in actions] == [(Branch.UNKNOWN, True)]
    assert gateway.sent_count() == 1


def test_a_send_made_before_recovery_does_not_let_the_next_send_reuse_recovery_s_numbers(
    tmp_path: Path,
):
    """The same release, reached by the ordering a *lazily* cached counter also gets wrong.

    Its sibling above starts recovery before the gateway has written anything, so a counter that
    fills itself on first use happens to fill it after the recovery pass and numbers correctly. Here
    an ordinary send goes first, which is what fills such a counter early; `resume` then appends an
    outcome for the intent a previous run left dangling, and the next send is numbered back over it.
    From there it is the same failure: the live intent is no longer at the tail, the next pass finds
    it inside its boundary, and §5.4(b) releases it from the cap.

    Both orderings are kept because they are killed by different mutants -- this is the one
    `the_gateway_numbers_from_a_cached_counter` reaches, and without it the reviewed defect's own
    property has no mutant behind it at all.
    """
    store = _StoreWhoseOutcomeWriteCanFail(tmp_path / "a.jsonl")
    store.append(dict(seq=0, kind="intent", tool="send_message", principal="agent", tier=2,
                      scope="send_message", outcome="pending", arg_digest="crashed", seed=None))

    gateway = Gateway(store, principal="agent", tier=2)
    _send(gateway, 0)                                    # one ordinary send, before any recovery
    resume(store, side_effect_absent=lambda d: True, stale_after_seq=0)
    boundary = max(e.seq for e in store.read_all()[0])

    store.dying = True
    with pytest.raises(OSError):
        _send(gateway, 9)
    store.dying = False

    actions = resume(store, side_effect_absent=lambda d: True, stale_after_seq=boundary)
    assert [(a.branch, a.counts_against_cap) for a in actions] == [(Branch.UNKNOWN, True)]
    assert gateway.sent_count() == 2
