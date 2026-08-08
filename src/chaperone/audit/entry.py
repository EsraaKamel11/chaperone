from __future__ import annotations

from pydantic import BaseModel, ConfigDict

#: The outcome words that mean **nobody knows whether the side effect happened**. One definition,
#: read by the consumer that has to act on it, because the two drifted: `recovery.
#: requires_approval_for` looked for `"unknown"` alone, and `"error"` -- which `gateway.call` has
#: always written for a tool that was entered and did not return cleanly -- is paired by
#: `pair_intents`, so `resume` does not visit that intent and writes no `unknown` for it. The one
#: outcome that most needs a human before a re-attempt was the one that got none.
#:
#: `"aborted"` is deliberately absent: it is the only word carrying the verification that the
#: effect did *not* happen, which is why `counted_sends` lets it and nothing else lower the count.
#: `"unattempted"` is absent for the same reason -- the tool was never entered.
#:
#: Named here rather than in `recovery.py` because this module owns the vocabulary comment below,
#: and a set that lives beside the definition cannot be renamed away from its reader in silence.
#: `test_the_indeterminate_vocabulary_is_one_definition_and_both_layers_read_it` holds
#: `Branch.UNKNOWN.value` inside it.
INDETERMINATE_OUTCOMES = frozenset({"error", "unknown"})


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
    #
    # **And who reads them**, because grouping by writer alone is how the gap above stayed open:
    # the list showed where each word came from and nothing showed which words were being acted on.
    #   recovery.counted_sends:        releases on "aborted" and on nothing else.
    #   recovery.requires_approval_for: demands a human on every word in INDETERMINATE_OUTCOMES.
    # A word added above with no line here is a word this note claims no consumer for. That was
    # "error"'s state, and it was true; check before assuming it of the next one.
    outcome: str
    arg_digest: str
    seed: int | None
    prev_hash: str
    entry_hash: str

    def payload(self) -> dict:
        return self.model_dump(exclude={"prev_hash", "entry_hash"})
