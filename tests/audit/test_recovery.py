"""The three §5.4 recovery branches, and the digest as an idempotency key."""
from __future__ import annotations

from pathlib import Path

from chaperone.audit.gateway import DIGEST_UNAVAILABLE, Gateway
from chaperone.audit.recovery import Branch, requires_approval_for, resume
from chaperone.audit.store import AuditStore
from chaperone.policy.types import Decision, Disposition

ALLOW = Decision(allowed=True, findings=(), disposition=Disposition.ALLOW)

#: Two key types, so `sort_keys` cannot order them and `arg_digest` raises. Named by
#: `gateway._safe_digest`'s own docstring as the shape that reaches the degraded path -- so the
#: fixture below is the real producer's output, not a hand-typed constant resembling it.
UNDIGESTIBLE = {"a": 1, 2: "b"}


def _row(seq, kind, outcome, digest):
    return dict(seq=seq, kind=kind, tool="send_message", principal="agent", tier=2,
                scope="send", outcome=outcome, arg_digest=digest, seed=None)


def test_a_matched_intent_and_outcome_needs_no_recovery(tmp_path: Path):
    store = AuditStore(tmp_path / "a.jsonl")
    store.append(_row(0, "intent", "pending", "d1"))
    store.append(_row(1, "outcome", "allowed", "d1"))
    actions = resume(store, side_effect_absent=lambda d: True)
    assert actions == []


def test_a_stale_intent_with_the_side_effect_verifiably_absent_is_aborted(tmp_path: Path):
    store = AuditStore(tmp_path / "a.jsonl")
    store.append(_row(0, "intent", "pending", "d1"))
    actions = resume(store, side_effect_absent=lambda d: True)
    assert [a.branch for a in actions] == [Branch.ABORTED]
    assert actions[0].counts_against_cap is False


def test_an_indeterminate_intent_stays_counted_against_the_cap(tmp_path: Path):
    store = AuditStore(tmp_path / "a.jsonl")
    store.append(_row(0, "intent", "pending", "d1"))
    actions = resume(store, side_effect_absent=lambda d: None)
    assert actions[0].branch is Branch.UNKNOWN
    assert actions[0].counts_against_cap is True


def test_an_unknown_outcome_is_written_back_so_the_state_is_durable(tmp_path: Path):
    store = AuditStore(tmp_path / "a.jsonl")
    store.append(_row(0, "intent", "pending", "d1"))
    resume(store, side_effect_absent=lambda d: None)
    entries, _ = store.read_all()
    assert entries[-1].outcome == "unknown"


def test_a_digest_with_an_unknown_outcome_requires_approval_on_re_attempt(tmp_path: Path):
    """The argument digest doubles as an idempotency key. This is what prevents the double-send."""
    store = AuditStore(tmp_path / "a.jsonl")
    store.append(_row(0, "intent", "pending", "d1"))
    resume(store, side_effect_absent=lambda d: None)
    assert requires_approval_for(store, "d1") is True
    # Kept from the brief and correct under the prefix rule -- "d2" carries no marker and has no
    # unknown outcome. It is *blind* to the contract rather than wrong: it passes with or without
    # the prefix check, which is why the two tests below exist beside it rather than in place of it.
    assert requires_approval_for(store, "d2") is False


def test_a_degraded_digest_requires_approval_even_where_the_log_says_the_send_was_allowed(
    tmp_path: Path,
):
    """The gateway's interface contract, driven from the producer that makes it necessary.

    A digest that could not be canonicalised is a hash of `repr(args)`, which embeds object
    addresses and follows insertion order -- so a genuine re-attempt of the same logical send can
    hash differently and equality returns `False` exactly where §5.4(c) needs `True`. The prefix
    needs no stability at all. There is no `unknown` outcome anywhere in this log and the recorded
    outcome is `allowed`: every equality-shaped route to `True` is closed, so only the prefix rule
    can answer.
    """
    store = AuditStore(tmp_path / "a.jsonl")
    gateway = Gateway(store, principal="agent", tier=2)
    gateway.call("send_message", UNDIGESTIBLE, decide=lambda: ALLOW, execute=lambda: "sent",
                 effectful=True)
    entries, _ = store.read_all()
    digest = entries[-1].arg_digest
    assert digest.startswith(DIGEST_UNAVAILABLE), "the fixture never reached the degraded path"
    assert entries[-1].outcome == "allowed"
    assert [e.outcome for e in entries if e.outcome == "unknown"] == []

    assert requires_approval_for(store, digest) is True


def test_one_outcome_resolves_one_intent_and_not_every_intent_sharing_its_digest(tmp_path: Path):
    """The repeated digest is the double-send's own shape, so it cannot be the case that is missed.

    A send crashes leaving `intent(d1)` unresolved; the process restarts and re-attempts the same
    arguments, which digest to the same `d1`, and that attempt completes. The log now holds two
    intents and one outcome. Matching an intent against the *set* of outcome digests calls both
    resolved and recovers nothing -- an unresolved intent silently reported as branch (a), which is
    the fail-open direction and exactly the case the idempotency key exists for.
    """
    store = AuditStore(tmp_path / "a.jsonl")
    store.append(_row(0, "intent", "pending", "d1"))
    store.append(_row(1, "intent", "pending", "d1"))
    store.append(_row(2, "outcome", "allowed", "d1"))
    actions = resume(store, side_effect_absent=lambda d: None)
    assert [(a.intent_seq, a.branch) for a in actions] == [(0, Branch.UNKNOWN)]


def test_an_outcome_written_with_no_intent_before_it_resolves_nothing(tmp_path: Path):
    """A non-effectful call writes an outcome and no intent. Counting outcomes rather than pairing
    them in order lets that entry absorb a later effectful call's unresolved intent."""
    store = AuditStore(tmp_path / "a.jsonl")
    store.append(_row(0, "outcome", "allowed", "d1"))
    store.append(_row(1, "intent", "pending", "d1"))
    actions = resume(store, side_effect_absent=lambda d: None)
    assert [(a.intent_seq, a.branch) for a in actions] == [(1, Branch.UNKNOWN)]


def test_a_non_stale_intent_is_not_aborted_even_with_the_side_effect_verifiably_absent(
    tmp_path: Path,
):
    """§5.4(b) is a conjunction: stale **and** verifiably absent. Only the second is probed here.

    An intent past the boundary may still be in flight -- the probe answering "absent" then means
    "not yet", not "never". Releasing it from the cap on that answer is the fail-open direction, so
    it takes branch (c) instead. One test pins both sides of the boundary, because a predicate that
    called everything stale and one that called nothing stale would each satisfy half of it.
    """
    store = AuditStore(tmp_path / "a.jsonl")
    store.append(_row(0, "intent", "pending", "d0"))
    store.append(_row(1, "intent", "pending", "d1"))
    actions = resume(store, side_effect_absent=lambda d: True, stale_after_seq=0)
    assert [(a.intent_seq, a.branch, a.counts_against_cap) for a in actions] == [
        (0, Branch.ABORTED, False),
        (1, Branch.UNKNOWN, True),
    ]


def test_the_default_boundary_does_not_hand_the_whole_log_to_the_release_branch(tmp_path: Path):
    """The default is the conservative end of the parameter, not the permissive one.

    `stale_after_seq=0` marks the shortest possible prefix stale. A caller that knows where the
    previous run ended widens it by naming that seq; a caller that names nothing releases at most
    the log's first intent. The opposite convention -- 0 meaning "the entire log predates this
    run" -- makes the *default* the widest release, and cannot narrow toward the tail at all,
    because raising the boundary would then keep only the newest intents eligible and those are
    precisely the ones that may still be running.
    """
    store = AuditStore(tmp_path / "a.jsonl")
    store.append(_row(5, "intent", "pending", "d5"))
    actions = resume(store, side_effect_absent=lambda d: True)
    assert [(a.branch, a.counts_against_cap) for a in actions] == [(Branch.UNKNOWN, True)]


def test_a_probe_that_raises_is_indeterminate_rather_than_fatal(tmp_path: Path):
    """The probe reaches the outside world, so it is the part of recovery most likely to fail.

    Letting it propagate aborts the pass part-way: the intents it had not reached keep no durable
    outcome, `requires_approval_for` answers `False` for every one of them, and the double-send
    guard is off for exactly the digests recovery never got to. The failure is an *answer* -- the
    most conservative one -- not an exception.
    """
    def probe(digest):
        raise RuntimeError("the delivery service did not answer")

    store = AuditStore(tmp_path / "a.jsonl")
    store.append(_row(0, "intent", "pending", "d0"))
    store.append(_row(1, "intent", "pending", "d1"))
    actions = resume(store, side_effect_absent=probe, stale_after_seq=1)
    assert [(a.intent_seq, a.branch, a.counts_against_cap) for a in actions] == [
        (0, Branch.UNKNOWN, True),
        (1, Branch.UNKNOWN, True),
    ]
    entries, _ = store.read_all()
    assert [e.outcome for e in entries if e.kind == "outcome"] == ["unknown", "unknown"]
    assert requires_approval_for(store, "d1") is True


def test_a_probe_answer_that_is_merely_truthy_does_not_release_the_cap(tmp_path: Path):
    """`is True`, not truthiness. Passes on the implementation as first written -- disclosed, not
    presented as a caught defect. Its target is the plausible later edit `if absent:`, which hands
    the release branch to any non-empty string a probe returns, including one that says no."""
    store = AuditStore(tmp_path / "a.jsonl")
    store.append(_row(0, "intent", "pending", "d0"))
    actions = resume(store, side_effect_absent=lambda d: "no delivery record found")
    assert [(a.branch, a.counts_against_cap) for a in actions] == [(Branch.UNKNOWN, True)]


def test_resume_is_idempotent_and_a_second_pass_writes_nothing(tmp_path: Path):
    """Recovery can itself crash part-way, so it is re-run. Passes on the first implementation --
    disclosed rather than presented as a caught defect. It is a guard against a later `resume`
    that stops writing the outcome back, or pairs by position instead of by digest.
    """
    store = AuditStore(tmp_path / "a.jsonl")
    store.append(_row(0, "intent", "pending", "d1"))
    resume(store, side_effect_absent=lambda d: None)
    before, _ = store.read_all()
    assert resume(store, side_effect_absent=lambda d: None) == []
    after, _ = store.read_all()
    assert [e.seq for e in after] == [e.seq for e in before]


def test_resume_allocates_seqs_that_collide_with_nothing_already_in_the_log(tmp_path: Path):
    """`gateway.__init__` claims "the two allocators cannot disagree". Asserted on the log rather
    than by reading both expressions, and over a log with a hole -- `len(entries)` is where the
    collision came from, and a hole is what makes it visible."""
    store = AuditStore(tmp_path / "a.jsonl")
    store.append(_row(0, "intent", "pending", "d1"))
    store.append(_row(7, "intent", "pending", "d2"))
    resume(store, side_effect_absent=lambda d: None)
    seqs = [e.seq for e in store.read_all()[0]]
    assert len(seqs) == len(set(seqs)), f"duplicate seq allocated: {seqs}"


def test_the_bare_unavailable_marker_requires_approval(tmp_path: Path):
    """`Gateway.call` binds the bare marker as its pre-`try` default, so an outcome entry can
    carry it literally -- the one digest value that names *no* arguments at all."""
    store = AuditStore(tmp_path / "a.jsonl")
    store.append(_row(0, "outcome", "allowed", DIGEST_UNAVAILABLE))
    assert requires_approval_for(store, DIGEST_UNAVAILABLE) is True


def test_a_seq_reported_to_the_caller_still_names_the_entry_that_was_written(tmp_path: Path):
    """`resume` is a second writer, and `GatewayResult.outcome_seq` is read before the write.

    `call` builds its result inside the `return` expression, which Python evaluates before the
    `finally` allocates the outcome's seq; the two agree only because the allocator's position is
    reported honestly. A **plain** call allocates nothing before that point, so its reported number
    is whatever was left over -- and a recovery pass appending in between makes it name a different
    entry. `test_the_result_names_the_seqs_of_the_entries_that_were_actually_written` in
    test_gateway.py holds the same property; this is the input recovery adds to it, and a caller
    holding a seq that names someone else's record is worse than one holding none.
    """
    store = AuditStore(tmp_path / "a.jsonl")
    store.append(_row(5, "intent", "pending", "d1"))
    gateway = Gateway(store, principal="agent", tier=2)
    resume(store, side_effect_absent=lambda d: None, stale_after_seq=5)

    result = gateway.call("read_record", {}, decide=lambda: ALLOW, execute=lambda: "v")

    entries, _ = store.read_all()
    assert result.intent_seq is None
    assert result.outcome_seq == entries[-1].seq
