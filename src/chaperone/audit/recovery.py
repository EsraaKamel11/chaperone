from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from chaperone.audit.entry import AuditEntry
from chaperone.audit.gateway import DIGEST_UNAVAILABLE
from chaperone.audit.store import AuditStore


class Branch(str, Enum):
    COMPLETE = "complete"
    ABORTED = "aborted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ResumeAction:
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
    unparseable line, so two corrupted lines still add one. The residual is an under-correction of
    an under-count, which is strictly closer to the truth than not correcting -- and the direction
    it can still err in is the one this whole module is arranged around, so it is stated rather
    than implied.
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
    left with no durable outcome, so `requires_approval_for` answers `False` for each of them and
    the double-send guard is off for exactly the digests recovery never got to. `None` is the
    indeterminate answer branch (c) already exists for, so the failure becomes the most
    conservative answer rather than an exception.

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
    The conjunction in (b) exists to exclude an intent that may still be running. Under the one
    writer this log is built for, an in-flight intent sits at the tail -- `_next_seq` hands numbers
    out in order -- so a prefix is the only shape that can exclude one. Two gateways appending to
    one store would each allocate from their own `_seq` and the tail would stop meaning that, which
    is a limit of the ordering rather than of this parameter. Widening the
    boundary therefore reaches *forward*, and the default of 0 is the narrowest release rather than
    the widest: a caller that knows where the previous run ended names that seq, and a caller that
    names nothing releases at most the log's first intent. Reading the parameter the other way --
    `seq >= stale_after_seq`, so 0 means the whole log -- passes every test the brief wrote, whose
    fixtures all sit at seq 0, while inverting both the default and the direction of the knob.

    Only branch (b) is permissive, so only branch (b) is guarded. Every other answer, including a
    probe that raises or returns something that is not exactly `True`, lands in (c).
    """
    entries, _ = store.read_all()
    actions: list[ResumeAction] = []
    next_seq = max((e.seq for e in entries), default=-1) + 1

    for entry in unresolved_intents(entries):
        absent = _probe(side_effect_absent, entry) if entry.seq <= stale_after_seq else None
        branch = Branch.ABORTED if absent is True else Branch.UNKNOWN
        counts = branch is not Branch.ABORTED
        store.append(dict(seq=next_seq, kind="outcome", tool=entry.tool, principal=entry.principal,
                          tier=entry.tier, scope=entry.scope, outcome=branch.value,
                          arg_digest=entry.arg_digest, seed=entry.seed))
        next_seq += 1
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
    mechanism "what prevents the double-send". The prefix needs no stability at all, so the
    obligation is discharged here, at the consumer, rather than by trying to make `repr` canonical.

    The cost is a false demand for approval: two *different* undigestible sends both stop at a
    human. That is the direction an enforcement predicate is allowed to err in.
    """
    if digest.startswith(DIGEST_UNAVAILABLE):
        return True
    entries, _ = store.read_all()
    return any(e.arg_digest == digest and e.outcome == "unknown" for e in entries)
