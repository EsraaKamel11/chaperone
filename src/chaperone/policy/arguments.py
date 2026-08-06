"""What a call may carry, given what the gate reviewed. Pure: no I/O, no clock, no LLM client.

This lives in `policy/` rather than beside either hook because **both** enforcement layers need it
and neither may import the other. `gates/hook.py` cannot be imported by `tools/policy_hook.py` --
it pulls in the model layer and would break the out-of-process guard's portability -- so a copy in
each was the alternative, and a predicate duplicated across two layers is the drift design spec 6.3
forbids. One function, imported twice, cannot drift.

**The rule.** The gate assesses a `Draft`; the executor ships `args`. Design spec 4.1's ordering
guarantee -- the gate runs before the tool is looked up -- is worth nothing unless the object
reviewed is the object sent, so every scalar in `args` must be text the gate judged as *outbound*.

**Reviewed as input is not reviewed as output, and the distinction is load-bearing.** An earlier
version admitted every thread role and body, on the grounds that the gate had seen them. It had --
as the incoming conversation. With a real thread that made every investor utterance shippable in
the body slot, text no content-class ever judged as something being sent. `sendable_text` is the
outbound surface only: the drafted body, the routing fields, the cited field names, the tool name.

**Two bounds, both stated because they are otherwise invisible.**

- **Keys are parameter names, checked as such.** A mapping key that is a Python identifier is
  accepted without being reviewed, because that is what `**args` requires of it. A key that is not
  an identifier is checked as content: `{"Returns are guaranteed.": "..."}` reaches a `**kwargs`
  tool intact, and `arg_digest` covers keys, so a key-only divergence produces exactly the audit
  entry describing a call nobody assessed. An identifier-shaped key can still carry prose in
  `snake_case`; the bandwidth is low and the bound is declared rather than hidden.
- **Routing values can still permute.** `sendable_text` is a set, so `{"to": "US"}` and
  `{"jurisdiction": "example.test"}` are both accepted. `BODY_KEYS` closes the case that matters --
  a routing token standing in for the message -- and what remains is that caller-controlled
  identifiers can swap slots among themselves. **No model-authored prose is in that surface**: the
  drafted body is pinned by `BODY_KEYS`, and the thread is not sendable at all.
"""
from __future__ import annotations

from collections.abc import Mapping

from chaperone.policy.types import Draft

#: Argument names that denote the outbound message itself. A value under one of these must be the
#: reviewed body exactly, not merely something the gate saw -- otherwise `{"body": "example.test"}`
#: passes on set membership and ships a routing token as the message. Declared as a constant so
#: widening it is a decision somebody makes on purpose.
#:
#: A body arriving under a name outside this set is caught only by the membership rule, which is
#: the residual named in the module docstring.
BODY_KEYS = frozenset({"body", "message", "text", "content", "markdown", "prose", "html"})


def sendable_text(draft: Draft) -> frozenset[str]:
    """Every string the gate judged as outbound. The thread is deliberately absent.

    The thread was reviewed as the incoming conversation -- context for the content classes, never
    a candidate for transmission -- so admitting it would let a call ship back text that was judged
    only as something received.
    """
    values = {draft.body, draft.recipient_domain, draft.recipient_jurisdiction}
    values.update(draft.cited_fields)
    if draft.tool_name is not None:
        values.add(draft.tool_name)
    return frozenset(values)


def _scalar(text: str, sendable: frozenset[str], body: str, key: str | None) -> tuple[str, ...]:
    if key in BODY_KEYS:
        return () if text == body else (text,)
    return () if text in sendable else (text,)


def _walk(value: object, sendable: frozenset[str], body: str, key: str | None) -> tuple[str, ...]:
    """`str` is tested first on purpose: a string is a sequence, so a container branch reached
    before it would take every body apart into characters and find each one unsendable."""
    if isinstance(value, str):
        return _scalar(value, sendable, body, key)
    if isinstance(value, Mapping):
        found: list[str] = []
        for name, inner in value.items():
            if not (isinstance(name, str) and name.isidentifier()):
                found.extend(_walk(name, sendable, body, None))
            found.extend(_walk(inner, sendable, body, name if isinstance(name, str) else None))
        return tuple(found)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(item for inner in value for item in _walk(inner, sendable, body, key))
    if value is None or isinstance(value, bool):
        return ()
    return _scalar(str(value), sendable, body, key)


def unsendable_in(value: object, draft: Draft) -> tuple[str, ...]:
    """Every scalar inside `value` the gate did not judge as outbound, walking nested containers.

    `None` and booleans are exempt: neither carries text a predicate could have judged. Numbers are
    not exempt -- `act:figure_not_in_record` exists because an unbacked figure matters, and a figure
    travelling as an argument reached no predicate at all, so exempting non-strings would reopen
    that class one layer over.
    """
    return _walk(value, sendable_text(draft), draft.body, None)
