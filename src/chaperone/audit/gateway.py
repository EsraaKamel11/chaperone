from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from chaperone.audit.store import AuditStore
from chaperone.policy.canonical import arg_digest
from chaperone.policy.types import Decision, Disposition

#: Prefix marking a digest that could not be computed canonically. What follows it is still a hash
#: -- of a degraded rendering -- never the arguments themselves.
#:
#: **A digest carrying this prefix is not a stable idempotency key.** Any consumer that compares
#: digests for equality must treat the prefix as its own answer rather than the comparison; see the
#: interface contract on `Gateway` for the one consumer that exists (Task 24's
#: `requires_approval_for`, which must return `True` on sight of it).
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

    **The marker prefixes a hash rather than replacing it, and that closes only one direction.**
    Task 24's `resume` pairs an intent with its outcome by `arg_digest`, so a single shared
    sentinel would pair unrelated records: two calls that both resist canonicalisation must still
    receive two different digests, and a test pins that.

    **The other direction is not closed here, and cannot be: a degraded digest is not a stable
    idempotency key.** `repr` embeds an object address for anything without a stable `__repr__`,
    and it follows dict insertion order where `canonical_json` sorts keys, so two attempts at the
    same logical send can hash differently. Making `repr` canonical is a losing game. The
    obligation is discharged where the marker is *consumed* instead -- see the interface contract
    on `Gateway`.

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

    **INTERFACE CONTRACT FOR TASK 24 -- `requires_approval_for` must return `True` for any digest
    beginning with `DIGEST_UNAVAILABLE`, regardless of equality.** A degraded digest is a hash of
    `repr(args)` and `repr` is not canonical: it embeds an object address for anything without a
    stable `__repr__`, and it follows dict insertion order where `canonical_json` sorts keys. Two
    attempts at the same logical send can therefore hash differently, and `requires_approval_for`
    matches on **equality** -- so on the degraded path it would return `False` for a genuine
    re-attempt and the double-send that design spec 5.4(c) exists to prevent would proceed
    unapproved. Treating the prefix itself as demanding approval is fail-closed and needs no
    stability at all. This module owns the marker, so this module carries the obligation.

    **Nothing above the `try` in `call` may raise.** Three operations were found sitting there, and
    each skipped the audit entry exactly when it mattered most: digesting the arguments, consulting
    the gate, and -- in `__init__` -- numbering the entry. The inventory of what still runs before
    the `try` is deliberately short, and is meant to be re-read whenever a line is added to it:

    - `digest = DIGEST_UNAVAILABLE` -- binds a module constant to a local.
    - `intent_seq = None` -- binds `None` to a local.
    - `outcome = "unattempted"` -- binds a literal to a local.

    None of the three can raise. Two things are deliberately outside that inventory's scope, and
    saying so is the point of having one:

    - **`__init__`'s `store.read_all()`** runs outside any `try` and can raise `ValueError`, a
      pydantic `ValidationError` or `OSError`. It is not listed because no call is in flight and no
      entry is owed: a gateway that cannot read its log fails to *construct*, which is the correct
      outcome and is not a skipped entry.
    - **`self._write` in the `finally`** is the one remaining finding-C-shaped hole, and it is not
      above the `try` at all. `store.append` can fail on a full disk; the outcome entry is then
      lost *and* the in-flight exception is replaced by the store's (the original survives as
      `__context__`). Unclosable here -- writing the entry is the last thing that happens, so
      nothing remains to record that it did not.

    `outcome = "unattempted"` is a **fail-closed default**. An earlier version defaulted to
    `"allowed"`, so any path returning without assigning would have logged an allow; the default
    now names the state that is true before anything has run.
    """

    def __init__(self, store: AuditStore, principal: str, tier: int) -> None:
        self.store = store
        self._principal = principal
        self._tier = tier
        # **No seq is cached here, and that is the point.** `len(entries)` was the first wrong
        # answer -- a tear removes a record without removing its number, so counting re-issued a
        # number already in use (`[0, 2]` counted two and allocated 2 again). `max + 1` fixed the
        # arithmetic and left the caching, which was the second wrong answer: `recovery.resume`
        # appends to this same log under the same rule, so it is a second writer and any number
        # held here is stale the moment it runs. Sends issued after a recovery pass were then
        # numbered back over the entries recovery had just written, a live intent stopped being at
        # the log's tail, and the next pass found it inside its staleness boundary and released it
        # from the cap. Then `max + 1` derived once before `resume`'s loop went stale *inside* one
        # recovery pass, because the probe it calls reaches the outside world through this gateway
        # and so appends between iterations. Every version was one copy of the rule going stale
        # while another moved, so there is now one copy: `AuditStore.next_seq` is the only place a
        # seq is decided, `_next_seq` delegates to it, and `recovery.resume` calls it per write.
        _, torn = store.read_all()
        #: True when the log already held a torn record when this gateway opened it. Surfaced, not
        #: swallowed: `count` does not report `torn` either, so a caller that only ever sees a
        #: number cannot learn a record was lost. **What to do about it is Task 24's** -- the send
        #: cap counts intents, a tear may have taken one, and a cap check reading the count alone
        #: would then permit one send too many. This gateway does not decide that; it declines to
        #: hide the input the decision needs.
        self.log_torn = torn

    def _next_seq(self) -> int:
        """The next free number, read off the log. **One rule, one place, no cached counter.**

        `recovery.resume` writes to this log too and asks the same question of the same object, so a
        later record always carries a higher seq -- which is what lets `resume` treat a prefix as
        stale and the tail as possibly in flight. That invariant holds because there is one rule
        with one home, not because two writers are asked in a comment to agree. This method is a
        delegation and deliberately holds no arithmetic of its own: a second copy of `max + 1` here
        is a second thing to update, and every version of this defect so far has been one copy of
        the rule going stale while the other moved.

        No new failure class and no new cost: `store.append` already reads the whole log through
        `_last_hash()` on every append, so this is the same read of the same file.

        **Also the number `call` reports as `GatewayResult.outcome_seq`.** That is read inside the
        `return` expression, which Python evaluates before the `finally` allocates anything, so it
        has to be a prediction; deriving it here makes it the same prediction the `finally` will
        make, from the same log. A cached counter got this wrong for **plain** calls in particular,
        which allocate nothing before the return and so reported whatever was left over.

        The "nothing above the `try` in `call` may raise" inventory is untouched by the `read_all`
        this adds: every caller of this method is inside the `try` or inside the `finally` -- the
        latter being the finding-C-shaped hole that docstring already names and bounds.
        """
        return self.store.next_seq()

    def _write(self, kind: str, tool: str, outcome: str, digest: str, seed: int | None) -> int:
        seq = self._next_seq()
        self.store.append(dict(
            seq=seq, kind=kind, tool=tool, principal=self._principal, tier=self._tier,
            scope=tool, outcome=outcome, arg_digest=digest, seed=seed,
        ))
        return seq

    def sent_count(self) -> int:
        """The cap counts intents. An unresolved intent keeps consuming it.

        Design spec 3.2 keeps the cap predicate pure over `(draft, record, context)` and makes the
        **gateway** supply the count by reading the log; this is that read, and it is the whole of
        the state access the predicate is spared.

        **Delegated rather than re-derived, and imported inside the function on purpose.**
        `recovery.counted_sends` is where the intent/outcome pairing lives, and `recovery` imports
        this module for `DIGEST_UNAVAILABLE`, so a module-level import here would be a cycle.
        Re-deriving the count instead would put a second pairing beside that one, and the two would
        disagree about the intents recovery released -- the drift being a cap that charges for a
        send the recovery pass believes it abandoned. `store.count` is deliberately *not* used: it
        reports a number and drops `torn`.
        """
        from chaperone.audit.recovery import counted_sends

        return counted_sends(self.store)

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
                return GatewayResult(False, None, decision, intent_seq, self._next_seq())
            # No handler here, deliberately. `"error"` is set *before* the tool is entered and is
            # downgraded only by a clean return, so every way of leaving `execute()` other than
            # returning -- including `KeyboardInterrupt`, `SystemExit`, `GeneratorExit` and
            # `asyncio.CancelledError`, none of which derive from `Exception` -- leaves `"error"`
            # standing. An `except Exception` here missed all four and let the pre-`try` default
            # through, so a send interrupted by Ctrl-C was recorded as `"unattempted"`: the word
            # `entry.py` defines as "the tool was never entered, so no side effect occurred". The
            # tool *was* entered, nobody knows whether the message left, and an operator reading
            # that would re-send it. Fail-closed by construction beats enumerating exception types.
            outcome = "error"
            value = execute()
            outcome = "allowed"
            return GatewayResult(True, value, decision, intent_seq, self._next_seq())
        finally:
            outcome_seq = self._write("outcome", tool_name, outcome, digest, seed)
            object.__setattr__(self, "_last_outcome_seq", outcome_seq)


def transmit(gateway: Gateway, tool_name: str, args: dict, decide, execute) -> GatewayResult:
    """The one send symbol. `tools/static_audit.py` fails the build if it is referenced elsewhere."""
    return gateway.call(tool_name, args, decide=decide, execute=execute, effectful=True)
