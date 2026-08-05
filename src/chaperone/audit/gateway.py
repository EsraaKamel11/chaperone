from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from chaperone.audit.store import AuditStore
from chaperone.policy.canonical import arg_digest
from chaperone.policy.types import Decision, Disposition


@dataclass(frozen=True)
class GatewayResult:
    allowed: bool
    value: object | None
    decision: Decision
    intent_seq: int | None
    outcome_seq: int


class Gateway:
    """The single chokepoint. Exactly one outcome entry per call, written in `finally`."""

    def __init__(self, store: AuditStore, principal: str, tier: int) -> None:
        self.store = store
        self._principal = principal
        self._tier = tier
        self._seq = len(store.read_all()[0])

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
        digest = arg_digest(args)
        intent_seq = self._write("intent", tool_name, "pending", digest, seed) if effectful else None

        decision = decide()
        outcome = "allowed"
        value = None
        try:
            if not decision.allowed:
                outcome = "redirected" if decision.disposition is not Disposition.ALLOW else "denied"
                return GatewayResult(False, None, decision, intent_seq, self._seq)
            value = execute()
            return GatewayResult(True, value, decision, intent_seq, self._seq)
        except Exception:
            outcome = "error"
            raise
        finally:
            outcome_seq = self._write("outcome", tool_name, outcome, digest, seed)
            object.__setattr__(self, "_last_outcome_seq", outcome_seq)


def transmit(gateway: Gateway, tool_name: str, args: dict, decide, execute) -> GatewayResult:
    """The one send symbol. `tools/static_audit.py` fails the build if it is referenced elsewhere."""
    return gateway.call(tool_name, args, decide=decide, execute=execute, effectful=True)
