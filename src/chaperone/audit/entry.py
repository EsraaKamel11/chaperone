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
    # The vocabulary is grouped by the module that writes it, because that is the part a comment
    # drifts on: the previous version omitted "error" -- which `gateway.call` has always written --
    # and listed "aborted"/"unknown", which only `recovery.resume` writes. The field is an
    # unvalidated `str`, so nothing but this note holds the two layers to one vocabulary.
    #   gateway: "pending" (on an intent), "allowed", "denied", "redirected",
    #            "error" (the tool was entered and raised),
    #            "unattempted" (the tool was never entered, so no side effect occurred)
    #   recovery: "aborted" (branch b), "unknown" (branch c)
    outcome: str
    arg_digest: str
    seed: int | None
    prev_hash: str
    entry_hash: str

    def payload(self) -> dict:
        return self.model_dump(exclude={"prev_hash", "entry_hash"})
