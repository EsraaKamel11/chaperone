"""What a call may carry, given what the gate reviewed. Pure: no I/O, no clock, no LLM client.

This lives in `policy/` rather than beside either hook because **both** enforcement layers need it
and neither may import the other. `gates/hook.py` cannot be imported by `tools/policy_hook.py` --
it pulls in the model layer and would break the out-of-process guard's portability -- so a copy in
each was the alternative, and a predicate duplicated across two layers is the drift design spec 6.3
forbids. One function, imported twice, cannot drift. The **finding** lives here too, for the same
reason: the class and the detail wording were written out in both layers, and a corpus comparing
categories alone would not have noticed them diverging.

**The rule.** The gate assesses a `Draft`; the executor ships `args`. Design spec 4.1's ordering
guarantee -- the gate runs before the tool is looked up -- is worth nothing unless the object
reviewed is the object sent, so every scalar in `args` must be text the gate judged as *outbound*,
**in the slot it judged it for**.

**The body is reachable only through `BODY_KEYS`, and that is the whole point of the rule.**
`sendable_text` deliberately does *not* contain `draft.body`. It did, and membership alone then
accepted `{"subject": <the drafted body>}`, `{"filename": <the drafted body>}` and eight more
spellings -- the reviewed message reappearing as a subject line, a filename or a preview, none of
which any content-class judged it as. The docstring at the time claimed "no model-authored prose is
in that surface" while the drafted body was the one piece of model-authored prose in it. So the
body now has exactly one home: a `BODY_KEYS` slot, checked for equality.

**Reviewed as input is not reviewed as output.** An earlier version admitted every thread role and
body, on the grounds that the gate had seen them. It had -- as the incoming conversation. With a
real thread that made every investor utterance shippable, text no content-class ever judged as
something being sent.

**Bounds, stated because they are otherwise invisible.**

- **Keys are parameter names, checked as such.** A mapping key that is a Python identifier is
  accepted without being reviewed, because that is what `**args` requires of it. A key that is not
  an identifier is checked as content: `{"Returns are guaranteed.": "..."}` reaches a `**kwargs`
  tool intact, and `arg_digest` covers keys, so a key-only divergence produces exactly the audit
  entry describing a call nobody assessed. An identifier-shaped key can still carry prose in
  `snake_case`; the bandwidth is low and the bound is declared rather than hidden.
- **Routing values can still permute among themselves.** `sendable_text` is a set, so `{"to": "US"}`
  is accepted. What is no longer in that set is the drafted body and the thread -- so **no
  model-authored prose can permute**, which is the claim the previous version made falsely and this
  one makes only about what it has actually removed.
- **Depth is bounded by the interpreter, and exhausting it is a denial.** Cyclic or very deeply
  nested arguments raise `RecursionError`; that is caught and reported as unsendable rather than
  allowed to escape, because a raise at the executor boundary is design spec 3.4's named trap.
"""
from __future__ import annotations

from collections.abc import Mapping

from chaperone.policy.types import Draft, Finding, ViolationClass

#: Argument names that denote the outbound message itself. A value under one of these must be the
#: reviewed body exactly. Since `sendable_text` excludes the body, this is also the *only* way the
#: body may travel: outside these names it is unsendable like any other unjudged text.
#:
#: A body arriving under a name outside this set is refused, not admitted -- the residual runs the
#: other way, and is that a *routing token* may occupy a slot meant for another routing token.
BODY_KEYS = frozenset({"body", "message", "text", "content", "markdown", "prose", "html"})

#: Reported in place of the offending values when the arguments cannot be examined to the bottom.
#: Non-empty by construction, so every caller denies on it exactly as it denies on real findings.
TOO_DEEP = "<arguments too deeply nested to examine>"


def sendable_text(draft: Draft) -> frozenset[str]:
    """Every string the gate judged as outbound **other than the message itself**.

    The body is absent on purpose -- see `BODY_KEYS`. The thread is absent because it was reviewed
    as the incoming conversation and never as a candidate for transmission.
    """
    values = {draft.recipient_domain, draft.recipient_jurisdiction}
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
            # A body slot pins its **whole subtree**, so the outer key wins wherever it is a body
            # key. Taking the inner name unconditionally let the pin survive exactly one level:
            # `{"body": "example.test"}` was refused while `{"body": {"x": "example.test"}}` was
            # delivered, because `x` is not a body key and the value is sendable as a recipient.
            # The list branch below already propagated the outer key; only this one did not.
            inherited = key if key in BODY_KEYS else (name if isinstance(name, str) else None)
            found.extend(_walk(inner, sendable, body, inherited))
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

    **Exhausting the stack is a denial, not an escape.** A cyclic `args` -- or one nested past the
    recursion limit -- raised `RecursionError` out of `guarded_call`, and design spec 3.4 names that
    exact shape: a defensively-written executor wraps handler invocation in a catch-all and the deny
    arrives relabelled "transient -- please retry". The out-of-process layer contained it in its
    `BaseException` net; the in-process one did not. Caught here so both layers report a verdict.
    """
    try:
        return _walk(value, sendable_text(draft), draft.body, None)
    except RecursionError:
        return (TOO_DEEP,)


def unsendable_finding(value: object, draft: Draft) -> tuple[Finding, ...]:
    """The denial both layers report, built once so the class and the wording cannot diverge.

    `OTHER` because no existing class describes "the gate assessed a different object", and because
    `Finding` requires detail text for exactly that class -- which is what a reviewer needs here.
    `OTHER` is in the engine's futile set, so the disposition derives correctly: no redraft of the
    body answers a call that ships text nobody read.
    """
    unsendable = unsendable_in(value, draft)
    if not unsendable:
        return ()
    return (Finding(
        ViolationClass.OTHER,
        f"the call carries {len(unsendable)} argument value(s) the gate did not judge as "
        f"outbound, beginning {unsendable[0]!r}",
        None,
    ),)
