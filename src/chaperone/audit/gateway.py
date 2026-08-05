from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from chaperone.audit.store import AuditStore
from chaperone.policy.canonical import arg_digest
from chaperone.policy.types import Decision, Disposition

#: Prefix marking a digest that could not be computed canonically. What follows it is still a hash
#: -- of a degraded rendering -- never the arguments themselves.
DIGEST_UNAVAILABLE = "unavailable:"


def _safe_digest(args: object) -> str:
    """The canonical argument digest, or a *marked* hash of a degraded rendering.

    `arg_digest` is `json.dumps` underneath and raises on anything it cannot canonicalise. No
    exotic input is needed to reach that: `{"a": 1, 2: "b"}` has keys of two types and `sort_keys`
    cannot order them, so a dict of plain scalars was enough. Computed before the first entry, it
    made the call that could not even be described the one call guaranteed not to be recorded --
    finding C's exact shape, inside the module written to fix finding C.

    **The fallback is still a hash, never the arguments.** Design spec 5.2 records "an argument
    digest -- canonical JSON, hashed -- never the raw arguments, because recipient identifiers are
    personal data and an audit log is not a place to accumulate it", and that binds the degraded
    path exactly as it binds the clean one. `repr(args)` is hashed and discarded; it is never
    stored.

    **The marker prefixes a hash rather than replacing it.** Task 24's `resume` pairs an intent
    with its outcome by `arg_digest`, and `requires_approval_for` treats a repeated digest as an
    idempotency key. A single shared sentinel would pair unrelated records and make an unrelated
    re-attempt read as a duplicate send, so two calls that both resist canonicalisation must still
    receive two different digests.

    `arg_digest` cannot fail on the fallback: `degraded` is a `str`, and a string always
    canonicalises.
    """
    try:
        return arg_digest(args)
    except Exception:
        try:
            degraded = repr(args)
        except Exception:
            # A `__repr__` that raises is still a call that has to be logged.
            degraded = f"<{type(args).__name__} with no usable representation>"
        return DIGEST_UNAVAILABLE + arg_digest(degraded)


@dataclass(frozen=True)
class GatewayResult:
    allowed: bool
    value: object | None
    decision: Decision
    intent_seq: int | None
    outcome_seq: int


class Gateway:
    """The single chokepoint. Exactly one outcome entry per call, written in `finally`.

    **Nothing above the `try` in `call` may raise.** Three operations were found sitting there, and
    each skipped the audit entry exactly when it mattered most: digesting the arguments, consulting
    the gate, and -- in `__init__` -- numbering the entry. The inventory of what still runs before
    the `try` is deliberately short, and is meant to be re-read whenever a line is added to it:

    - `digest = DIGEST_UNAVAILABLE` -- binds a module constant to a local.
    - `intent_seq = None`, `value = None` -- bind `None` to a local.
    - `outcome = "unattempted"` -- binds a literal to a local.

    None of the four can raise. The one remaining finding-C-shaped hole is not above the `try` at
    all: `self._write` in the `finally` calls `store.append`, which can fail on a full disk, and
    when it does the outcome entry is lost *and* the in-flight exception is replaced by the store's
    (the original survives as `__context__`). That one is unclosable here -- writing the entry is
    the last thing that happens, so nothing remains to record that it did not.

    `outcome = "unattempted"` is a **fail-closed default**. An earlier version defaulted to
    `"allowed"`, so any path returning without assigning would have logged an allow; the default
    now names the state that is true before anything has run.
    """

    def __init__(self, store: AuditStore, principal: str, tier: int) -> None:
        self.store = store
        self._principal = principal
        self._tier = tier
        entries, torn = store.read_all()
        # `len(entries)` is the next seq only while the log has no holes. A tear removes a record
        # without removing its number, so counting re-issued a number already in use -- `[0, 2]`
        # counted two and allocated 2 again -- and seq is the ordering Task 24's recovery reads to
        # pair an intent with its outcome. The maximum is right with or without holes, and it is
        # what `recovery.resume` uses, so the two allocators cannot disagree.
        self._seq = max((entry.seq for entry in entries), default=-1) + 1
        #: True when the log already held a torn record when this gateway opened it. Surfaced, not
        #: swallowed: `count` does not report `torn` either, so a caller that only ever sees a
        #: number cannot learn a record was lost. **What to do about it is Task 24's** -- the send
        #: cap counts intents, a tear may have taken one, and a cap check reading the count alone
        #: would then permit one send too many. This gateway does not decide that; it declines to
        #: hide the input the decision needs.
        self.log_torn = torn

    def _next_seq(self) -> int:
        seq = self._seq
        self._seq += 1
        return seq

    def _write(self, kind: str, tool: str, outcome: str, digest: str, seed: int | None) -> int:
        seq = self._next_seq()
        self.store.append(dict(
            seq=seq, kind=kind, tool=tool, principal=self._principal, tier=self._tier,
            scope=tool, outcome=outcome, arg_digest=digest, seed=seed,
        ))
        return seq

    def call(
        self,
        tool_name: str,
        args: dict,
        decide: Callable[[], Decision],
        execute: Callable[[], object],
        effectful: bool = False,
        seed: int | None = None,
    ) -> GatewayResult:
        digest = DIGEST_UNAVAILABLE
        intent_seq = None
        value = None
        outcome = "unattempted"
        try:
            digest = _safe_digest(args)
            if effectful:
                intent_seq = self._write("intent", tool_name, "pending", digest, seed)
            # Inside the `try`. Design spec 3.4 keeps an unavailable gate fatal and failing closed,
            # and 4.3 treats it as anticipated rather than exceptional -- so the raise stays and the
            # entry has to survive it. Outside the `try`, an outage left an intent with no outcome:
            # the one shape the recovery pass cannot resolve on its own.
            decision = decide()
            if not decision.allowed:
                outcome = "redirected" if decision.disposition is not Disposition.ALLOW else "denied"
                return GatewayResult(False, None, decision, intent_seq, self._seq)
            # The inner `try` scopes `"error"` to the tool's own failure rather than to a position
            # in this function. `"error"` and `"unattempted"` are not interchangeable: a tool that
            # raised may or may not have had its side effect, a call that never reached the tool
            # provably did not, and the gateway is the only place that fact is known.
            try:
                value = execute()
            except Exception:
                outcome = "error"
                raise
            outcome = "allowed"
            return GatewayResult(True, value, decision, intent_seq, self._seq)
        finally:
            outcome_seq = self._write("outcome", tool_name, outcome, digest, seed)
            object.__setattr__(self, "_last_outcome_seq", outcome_seq)


def transmit(gateway: Gateway, tool_name: str, args: dict, decide, execute) -> GatewayResult:
    """The one send symbol. `tools/static_audit.py` fails the build if it is referenced elsewhere."""
    return gateway.call(tool_name, args, decide=decide, execute=execute, effectful=True)
