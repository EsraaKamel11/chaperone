from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Sequence

from chaperone.audit.entry import AuditEntry
from chaperone.policy.canonical import canonical_json

GENESIS_HASH = "0" * 64


def link(prev_hash: str, payload: dict) -> str:
    material = prev_hash + canonical_json(payload)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    broken_at: int | None
    torn_tail: bool = False


def verify(entries: Sequence[AuditEntry], torn_tail: bool = False) -> VerifyResult:
    prev = GENESIS_HASH
    for index, entry in enumerate(entries):
        if entry.prev_hash != prev:
            return VerifyResult(ok=False, broken_at=index, torn_tail=torn_tail)
        if entry.entry_hash != link(prev, entry.payload()):
            return VerifyResult(ok=False, broken_at=index, torn_tail=torn_tail)
        prev = entry.entry_hash
    return VerifyResult(ok=True, broken_at=None, torn_tail=torn_tail)
