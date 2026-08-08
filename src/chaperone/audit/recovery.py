from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from chaperone.audit.entry import INDETERMINATE_OUTCOMES, AuditEntry
from chaperone.audit.gateway import DIGEST_UNAVAILABLE
from chaperone.audit.store import AuditStore


class Branch(str, Enum):
    COMPLETE = "complete"
    ABORTED = "aborted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ResumeAction:
    """What one unresolved intent was decided to be. A report, never a control input.

    `counts_against_cap` is derived -- it is `branch is not Branch.ABORTED` and nothing else -- and
    **the cap does not read it.** `counted_sends` derives release from the durable outcome word in
    the log, because a field on an in-memory object cannot survive the crash the cap has to be
    right across. It is kept because the task's interface pins it and two tests assert it directly:
    `test_a_stale_intent_with_the_side_effect_verifiably_absent_is_aborted` on `is False` and
    `test_an_indeterminate_intent_stays_counted_against_the_cap` on `is True`. Read it as this
    pass's account of what it did, and read the log for what is enforced.
    """

    intent_seq: int
    digest: str
    branch: Branch
    counts_against_cap: bool


def pair_intents(entries: list[AuditEntry]) -> list[tuple[AuditEntry, AuditEntry | None]]:
    """Every intent with the outcome that resolved it, or `None`, in seq order.

    **One implementation, because two layers read this pairing.** `resume` needs the intents with
    no outcome; `counted_sends` needs the ones whose outcome released them from the cap. Two
    walks of the log held together by a test would drift the moment either was tuned, and the
    disagreement would be a cap that counts a send the recovery pass believes it released.

    **Not a set membership test on the outcome digests.** The same arguments digest to the same
    value, so a send that crashed and was re-attempted puts two intents and one outcome in the log
    under one digest. A set says both are resolved and recovery reports nothing -- an unresolved
    intent silently treated as §5.4 branch (a), which is the fail-open direction in the one shape
    the idempotency key exists for. It also lets a non-effectful call, which writes an outcome and
    no intent, absorb a later effectful call's dangling intent.

    **The nearest preceding intent, not the oldest.** `gateway.call` writes its outcome in a
    `finally` immediately after its own intent, so an outcome belongs to the most recent unresolved
    intent carrying its digest; anything opened before that was abandoned by a crash. Pairing with
    the oldest instead reports the surviving run's intent as the dangling one and lets the
    abandoned one pass as resolved -- the same count, the wrong record.

    Seq is the order, per `gateway.__init__`: a tear removes a record without removing its number,
    so file position and seq can disagree, and seq is what both allocators agree on.
    """
    pending: dict[str, list[int]] = {}
    paired: list[list] = []
    for entry in sorted(entries, key=lambda e: e.seq):
        if entry.kind == "intent":
            paired.append([entry, None])
            pending.setdefault(entry.arg_digest, []).append(len(paired) - 1)
        elif entry.kind == "outcome" and pending.get(entry.arg_digest):
            paired[pending[entry.arg_digest].pop()][1] = entry
    return [(intent, outcome) for intent, outcome in paired]


def unresolved_intents(entries: list[AuditEntry]) -> list[AuditEntry]:
    """Intents with no outcome of their own, in seq order."""
    return [intent for intent, outcome in pair_intents(entries) if outcome is None]


def counted_sends(store: AuditStore) -> int:
    """The number of sends the cap must charge for. **The gateway's `sent_count` is this.**

    An intent counts unless recovery durably released it. §5.4(b) releases branch (b) and nothing
    else, so the test is the exact word `resume` wrote and not "has any outcome": `unattempted`
    means the tool was never entered, `error` means it was and nobody knows, and `unknown` is
    §5.4(c)'s "keeps consuming the cap". Only `aborted` carries the verification that the side
    effect did not happen, and lowering the count is the permissive direction, so it is the only
    word allowed to do it. `Branch.ABORTED.value` rather than a second literal -- `resume` writes
    the same expression, so the reader and the writer of this word cannot disagree.

    **A torn line adds one.** `AuditStore.count` reports a number and drops `torn`, so a caller
    that only ever sees the number cannot learn a record was lost -- §5.3's "enforcement predicate
    failing open" exactly, which is why this reads `read_all` and not `count`, and why it reads it
    fresh rather than trusting `Gateway.log_torn`, a snapshot taken at construction. The store
    fsyncs per entry, so at most the in-flight record is missing and `+1` is the correction for the
    ordinary crash. It is a **floor, not an exact repair**: `read_all` sets `torn` for any
    unparseable line, so two corrupted lines still add one. **It is also never reclaimed:** the tear
    stays on disk by design, so the extra send is charged for the log's whole life, including after
    `resume` has resolved every dangling intent. The residual is an under-correction of
    an under-count, which is strictly closer to the truth than not correcting -- and the direction
    it can still err in is the one this whole module is arranged around, so it is stated rather
    than implied.

    **This counts every intent, whatever tool it named, and the spec words the cap per recipient
    domain.** A global count over all tools refuses earlier than a per-domain one and never later,
    so the divergence is in the safe direction; but it is a divergence and not an implementation of
    3.2 as written. Per-domain is unbuildable from this log for the same reason branch (c) files no
    handoff -- the log holds a digest and never a recipient -- and would need the same kind of
    outside resolver.

    **One fail-open this cannot close: a log that is not there counts zero.** `read_all` over a
    missing path returns `([], False)`, so a store whose file was deleted is indistinguishable from
    a store that has never been written, and this returns 0 for both -- the cap fully reset, with
    no tear to see. It is not closable inside this module: telling the two apart needs state the
    log does not hold, such as a durable high-water mark kept somewhere the same delete does not
    reach. Named here because "the count is the log" is the design, and this is the one input that
    makes the log lie without damaging it.
    """
    entries, torn = store.read_all()
    released = sum(
        1 for _, outcome in pair_intents(entries)
        if outcome is not None and outcome.outcome == Branch.ABORTED.value
    )
    intents = sum(1 for entry in entries if entry.kind == "intent")
    return intents - released + (1 if torn else 0)


def _probe(side_effect_absent: Callable[[str], bool | None], entry: AuditEntry) -> bool | None:
    """The probe's answer, or `None` where it could not give one.

    The probe reaches the outside world -- it is the part of recovery most likely to fail -- and a
    failure that propagates ends the pass part-way. Every intent it had not yet reached is then
    left with no durable outcome, so `requires_approval_for` answers `False` for each of them whose
    digest canonicalised -- a digest carrying `DIGEST_UNAVAILABLE` still answers `True`, from the
    prefix, with no log consulted -- and the double-send guard is off for the rest of the digests
    recovery never got to. `None` is the indeterminate answer branch (c) already exists for, so the
    failure becomes the most conservative answer rather than an exception.

    Broad on purpose: the exception a probe raises is the caller's business, not this module's, and
    an enumerated list would let the type nobody listed through as a crash.
    """
    try:
        return side_effect_absent(entry.arg_digest)
    except Exception:
        return None


def resume(
    store: AuditStore,
    side_effect_absent: Callable[[str], bool | None],
    stale_after_seq: int = 0,
) -> list[ResumeAction]:
    """Three branches. The durable record and this policy are separate; this is swappable.

    (a) An intent with an outcome of its own is complete and is not visited at all.
    (b) **Stale and** verifiably absent -> `aborted`, released from the cap.
    (c) Anything else -> `unknown`, still counted, and the digest becomes an approval key.

    **`stale_after_seq` marks a prefix stale: an intent is stale when `seq <= stale_after_seq`.**
    The conjunction in (b) exists to exclude an intent that may still be running, and an in-flight
    intent sits at the tail because **every writer numbers from the log as it is at write time**:
    `Gateway._next_seq` re-derives `max + 1` on each write and this function does the same, so a
    later record always carries a higher seq. That claim was false when this module was written --
    the gateway cached its counter at construction, this function was the second writer that made
    the cache stale, and sends issued after a recovery pass were numbered back over the top of it,
    which put a live intent inside a later pass's boundary and released it from the cap. Scoping
    the claim to "one writer" did not save it, because `resume` *is* the second writer. The
    numbering rule is what saves it. A prefix is then the only shape that can exclude a tail;
    widening the
    boundary therefore reaches *forward*, and the default of 0 is the narrowest release rather than
    the widest: a caller that knows where the previous run ended names that seq, and a caller that
    names nothing releases at most the log's first intent. Reading the parameter the other way --
    `seq >= stale_after_seq`, so 0 means the whole log -- passes every test the brief wrote, whose
    fixtures all sit at seq 0, while inverting both the default and the direction of the knob.

    **The caller protocol for the boundary, because getting it wrong is silent.** `stale_after_seq`
    is the previous run's maximum seq, captured *before this run's first send*. Capturing it after
    the current run has begun writing puts live intents inside the boundary, and they are then
    released from the cap by a predicate behaving exactly as specified -- the dangerous direction,
    and not one any assertion here can catch, because the input is what is wrong.

    **Branch (c)'s recipient-scoped needs-verification handoff is not filed here, and the reason is
    a missing resolver rather than an impossible one.** The log holds a digest and `scope=tool`,
    never a recipient, so nothing in this function can name who a send was going to. That does not
    make the handoff unbuildable: this function already takes an outside-world callback keyed by
    digest, and a parallel `recipient_for(digest)` resolver would supply the field without the log
    ever storing it. It is undesigned, not forbidden. Until it exists, `requires_approval_for` is
    the whole of the branch (c) mechanism.

    Only branch (b) is permissive, so only branch (b) is guarded. Every other answer, including a
    probe that raises or returns something that is not exactly `True`, lands in (c).

    **A non-stale intent skips the probe but still receives a durable `unknown`, and that is not
    free.** If the send it belongs to then completes, `gateway.call`'s `finally` writes its real
    outcome -- and `pair_intents` has already paired the intent with this `unknown`, so the genuine
    `allowed` finds no pending intent and is discarded rather than recorded as resolving anything.
    The consequence is not merely an untidy log: `requires_approval_for` answers `True` for that
    digest **permanently**, for a send that in fact went through. Permanently is meant literally
    and there is no clearing mechanism to appeal to: the log is append-only, and this gate fires on
    *any* entry carrying that digest with an `unknown` outcome, so no later append can retract it.
    A human can approve each re-attempt out of band; nobody can clear the state. Kept anyway,
    because the alternative -- writing nothing -- leaves a live intent with no idempotency key at
    all, and a permanent demand for approval errs in the direction this module is arranged around
    while a missing one does not.
    """
    entries, _ = store.read_all()
    actions: list[ResumeAction] = []

    for entry in unresolved_intents(entries):
        absent = _probe(side_effect_absent, entry) if entry.seq <= stale_after_seq else None
        branch = Branch.ABORTED if absent is True else Branch.UNKNOWN
        counts = branch is not Branch.ABORTED
        # `store.next_seq()` per write, and never a number carried across an iteration. The probe
        # above reaches the outside world, every outward call in this system goes through the
        # gateway, and the gateway appends here -- so the log moves *inside* this loop, with no
        # concurrency involved. A number derived once before the loop lands on a seq the probe has
        # already taken. The metadata is copied from the intent so the outcome carries the same
        # tool, principal, tier, scope and seed as the record it resolves.
        store.append(dict(seq=store.next_seq(), kind="outcome", tool=entry.tool,
                          principal=entry.principal, tier=entry.tier, scope=entry.scope,
                          outcome=branch.value, arg_digest=entry.arg_digest, seed=entry.seed))
        actions.append(ResumeAction(entry.seq, entry.arg_digest, branch, counts))
    return actions


def requires_approval_for(store: AuditStore, digest: str) -> bool:
    """A re-attempted send carrying a digest with an unknown outcome needs a human.

    **The prefix is checked before the log, and answers on its own.** `gateway.py` states this as
    an interface contract on this function: a digest beginning with `DIGEST_UNAVAILABLE` requires
    approval *regardless of equality*. That digest is a hash of `repr(args)` -- `repr` embeds an
    object address for anything without a stable `__repr__`, and it follows dict insertion order
    where `canonical_json` sorts keys -- so two attempts at the same logical send can hash
    differently. Equality then returns `False` for a genuine re-attempt, and §5.4(c) calls this
    mechanism "what prevents the double-send" -- which it does **when it is consulted before a
    re-attempt**, and nothing in this tree consults it yet. The prefix needs no stability at all, so the
    obligation is discharged here, at the consumer, rather than by trying to make `repr` canonical.

    The cost is a false demand for approval: two *different* undigestible sends both stop at a
    human. That is the direction an enforcement predicate is allowed to err in.

    **`INDETERMINATE_OUTCOMES`, not `unknown` alone.** `error` is the other word that means nobody
    knows, and it fell through here in silence: `gateway.call` writes it for a tool that was
    entered and did not return cleanly, and an intent carrying it **is paired** by `pair_intents`,
    so `resume` never visits it and no `unknown` is ever written for it. The one outcome that most
    needs a human before a re-attempt was the only indeterminate one this gate did not answer for.

    The set lives in `entry.py`, beside the vocabulary comment that owns these words, rather than
    here: `gateway` writes `error` and this function reads it, so a definition sitting in the
    consumer would be the same producer/consumer drift one layer over. It is the treatment
    `counted_sends` already gives `Branch.ABORTED.value` -- `resume` writes that word and
    `counted_sends` looks for it, and a rename touching only the writer would leave the cap
    silently permissive.

    **The same fail-open `counted_sends` names reaches here, and further.** `read_all` over a
    missing path returns `([], False)`, so a deleted log disarms this gate completely: every digest
    that is not marked `DIGEST_UNAVAILABLE` comes back `False` and no re-attempt is stopped. The
    prefix rule survives a deleted log because it never consults one; the `unknown` rule does not.
    Not closable inside this module, for the reason given there.
    """
    if digest.startswith(DIGEST_UNAVAILABLE):
        return True
    entries, _ = store.read_all()
    return any(
        e.arg_digest == digest and e.outcome in INDETERMINATE_OUTCOMES for e in entries
    )
