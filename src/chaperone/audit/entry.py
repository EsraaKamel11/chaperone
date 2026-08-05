from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AuditEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    seq: int
    kind: str            # "intent" | "outcome"
    tool: str | None
    principal: str
    tier: int
    scope: str
    outcome: str         # "allowed" | "denied" | "redirected" | "aborted" | "unknown" | "pending"
    arg_digest: str
    seed: int | None
    prev_hash: str
    entry_hash: str

    def payload(self) -> dict:
        return self.model_dump(exclude={"prev_hash", "entry_hash"})
