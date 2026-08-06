from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from chaperone.policy.types import Decision, Draft, Record

#: What `cited_field_values` shows for a field the draft cited and the record does not hold.
#:
#: A citation that resolves to nothing is the reason for `act:figure_not_in_record`, so it is the
#: entry a reviewer most needs to see, and dropping it removed the only mention of the field from a
#: payload that carries no `detail`. Angle brackets because no record value is spelled that way.
#: **No consumer compares against this yet.** It is a constant rather than a literal so that the
#: first one imports it instead of retyping it.
FIELD_NOT_IN_RECORD = "<not in record>"


class Handoff(BaseModel):
    """Self-contained. Every field comes from tool input or a stored record.

    Design spec 4.7: the reviewer cannot see the conversation, so nothing here may be reachable
    only from there. Every field is `required` -- a default would let an escalation arrive with
    the field the reviewer needs quietly empty, and a test drops each field in turn to hold that.
    """

    # `extra="forbid"` for the same reason every field is required: a schema that quietly accepts
    # what it was not designed to carry is how an escalation arrives holding the wrong thing. A
    # misspelled field name is then a construction error rather than a silently ignored keyword
    # sitting beside the empty field the reviewer needed.
    model_config = ConfigDict(frozen=True, extra="forbid")

    reason_category: str
    violating_span: str
    blocked_body: str
    recipient_domain: str
    recipient_jurisdiction: str
    cited_field_values: dict[str, str]
    thread_excerpt: str
    proposed_alternative: str | None
    refinement_rounds: int


def build_handoff(
    draft: Draft, record: Record, decision: Decision, alternative: str | None, rounds: int
) -> Handoff:
    """The escalation payload. **Requires a finding**, exactly as `denial_result` does.

    On a `Decision(False, (), ...)` assembled by hand this raises `IndexError`. `decide` produces
    no such denial and a battery test in test_engine.py holds that; an empty handoff would be 4.7's
    own failure, so the boundary is pinned as a limit test rather than papered over.

    **The category and the span are read off different findings, deliberately.** `reason_category`
    is `findings[0]`'s, matching `denial_result`, so the two layers agree on what kind of failure
    this is. The span is the first one any finding carries, because `findings[0]` is often not the
    finding holding the offending text and `findings[0].span or ""` then showed a human an empty
    quote.

    **Three routes put a span-less finding ahead of a span-bearing one**, counted by grepping
    `+ tripwire_findings` in engine.py rather than from memory -- the first draft of this sentence
    described the `(Finding(OTHER, ..., None),) + tripwire_findings` shape, which is only two of
    them. Two prepend a plumbing failure with no span: the checker flagged for review, or reported
    a violation it declined to name. The third is `checker_findings + tripwire_findings`, where
    `Verdict.span` defaults to `None`, so a verdict that names a class without quoting anything
    sits at index 0 in front of a tripwire that did quote. All three are tested.

    `denial_result` still reads `findings[0].span`: it answers an agent being told to redraft,
    this answers a human deciding whether text may go out, and 4.7 is about the second.
    """
    primary = decision.findings[0]
    return Handoff(
        reason_category=primary.violation_class.value,
        violating_span=next((f.span for f in decision.findings if f.span), ""),
        blocked_body=draft.body,
        recipient_domain=draft.recipient_domain,
        recipient_jurisdiction=draft.recipient_jurisdiction,
        cited_field_values={
            name: FIELD_NOT_IN_RECORD if (value := record.get(name)) is None else value
            for name in draft.cited_fields
        },
        thread_excerpt="\n".join(f"[{m.role}] {m.body}" for m in draft.thread),
        proposed_alternative=alternative,
        refinement_rounds=rounds,
    )
