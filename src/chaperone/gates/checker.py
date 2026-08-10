"""The independent content-class checker. Its accuracy is measured, never assumed.

Independence is enforced by omission: `build_checker_messages` reads the transmitted thread, the
candidate draft and the cited records, and no untransmitted artifact -- no generator system prompt,
no scratchpad, no tool-call history, no chain of thought. That is a property of which fields are
read. The contents of those fields are copied in unescaped, so whoever assembles a `Draft` owns the
transmitted/untransmitted line for the text inside them.

Two things this layer does NOT do, written down because their absence is otherwise invisible:

- **It runs no clock.** An unanswered question is not a permission, but nothing here enforces that
  by timing: a transport that hangs hangs the gate, and deny-on-timeout exists only insofar as the
  transport raises. That property lives in the transport, and today in no test.
- **It does not judge whether a verdict is correct.** It refuses verdicts it cannot act on -- a
  wrong type, a violation reported without a class, or a span the draft does not contain verbatim
  -- and a refusal costs a retry and then becomes `CheckerUnavailable`, which callers fail closed
  on. Anything well-formed is returned as given, and whether it is right is a measured question,
  not a structural one.

Two of those three refusals are predicates rather than inline conditions, and they are split on
whether a caller other than this module could evaluate them. `unusable_reason` is a property of the
verdict alone, so a transport binding holding nothing but the model's answer can apply the same rule
and ask for another one; it is shared rather than moved, and `_reject_unusable` still calls it.
`span_absent_reason` needs the draft, which at the transport seam is out of scope, so no binding can
feed a span violation back and a span violation only ever denies.

**The retry budget is for availability, and a transport is allowed to close the question.**
`CheckerUnavailable` raised by the transport propagates out of `check` untouched rather than costing
two more attempts: the replay in `testing/recorded.py` raises it for a verdict recorded as
unavailable, and re-asking a settled question is not a retry. Every other exception is a candidate
for another attempt, which is why the escape names one type instead of breaking the loop.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Callable, Mapping, Union

from pydantic import BaseModel, ConfigDict, Field

from chaperone.policy.types import Draft, Record, ViolationClass


class Verdict(BaseModel):
    model_config = ConfigDict(frozen=True)
    violates: bool
    violation_class: ViolationClass | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    span: str | None = None


class FlagForReview(BaseModel):
    model_config = ConfigDict(frozen=True)
    reason: str


CheckerResult = Union[Verdict, FlagForReview]


class CheckerUnavailable(Exception):
    """The checker could not produce a usable answer. Callers fail closed."""


# Read-only: a plain dict here would let one assignment anywhere in the process lower the floor
# below, and the floor is the thing that keeps a budget choice from being mistaken for an
# architectural result in the attribution ladder. Every read form a dict supports still works.
MODEL_STRENGTH: Mapping[str, int] = MappingProxyType({"haiku-tier": 1, "sonnet-tier": 2, "opus-tier": 3})


def assert_checker_not_weaker(checker_model: str, drafter_model: str) -> None:
    checker = MODEL_STRENGTH.get(checker_model)
    drafter = MODEL_STRENGTH.get(drafter_model)
    if checker is None or drafter is None:
        raise ValueError(f"unknown model tier: {checker_model!r} or {drafter_model!r}")
    if checker < drafter:
        raise ValueError(
            f"checker model {checker_model!r} must not be weaker than drafter {drafter_model!r}: "
            "same-tier or weaker reviewers make correlated errors"
        )


CHECKER_INSTRUCTIONS = (
    "You judge whether an outbound message violates one of three stated constraints: "
    "advising on the merits of an investment, negotiating terms, or making a forward-looking "
    "return statement. You see only what was transmitted, the candidate message, and the cited "
    "records. Return a verdict, or flag for review if you cannot tell."
)


#: Hand-chosen verbatim substrings of CHECKER_INSTRUCTIONS, one per content class. Two derivations
#: and one pinned pair, which is worth separating because "derived" was claimed for all three.
#: Derived: the keys are compared against the Family.CONTENT members in both directions, and each
#: phrase is required to appear in the instruction text -- add a class and either the key set or a
#: missing phrase reddens. **Not derived: the count.** The instruction's stated "three" and this
#: map's len() are asserted as two separate literals in
#: `test_checker_instructions_name_every_content_class_and_nothing_else`, and nothing compares one
#: to the other, and **no single edit reddens both** -- measured. A fourth class with the map left
#: alone reddens the key comparison and neither count pin; extending the map to answer that reddens
#: the len() pin while the frozen instruction still says "three"; and an instruction that renamed
#: its own count reddens the substring pin while len() stays 3. So the property is held by two pins
#: facing opposite directions, never by one edit failing both. Deriving it would mean composing a number back into a string that
#: is frozen, which is the one thing this map must not do: the constant is never recomposed from
#: it, because the recorded verdicts were produced under its exact bytes.
CONTENT_CLASS_PHRASES: Mapping[ViolationClass, str] = MappingProxyType({
    ViolationClass.ADVISES_ON_MERITS: "advising on the merits",
    ViolationClass.NEGOTIATES_TERMS: "negotiating terms",
    ViolationClass.FORWARD_LOOKING_RETURN: "forward-looking return",
})


def build_checker_messages(*, draft: Draft, record: Record) -> list[dict]:
    """Independence is enforced by omission: no untransmitted *field* enters this prompt.

    The qualifier is load-bearing, and the unqualified sentence this one replaces was not true.
    What follows selects which fields are read; it does not sanitize what they hold. Thread roles,
    thread bodies and the draft body are all interpolated unescaped, so a forged role or a body
    carrying delimiter text still reads as part of the prompt. Whoever assembles the `Draft` owns
    the transmitted/untransmitted line for the text inside those fields.
    """
    thread = "\n".join(f"[{m.role}] {m.body}" for m in draft.thread)
    cited = "\n".join(f"{name}: {record.get(name)}" for name in draft.cited_fields if record.get(name))
    content = (
        f"{CHECKER_INSTRUCTIONS}\n\n"
        f"<transmitted_thread>\n{thread}\n</transmitted_thread>\n\n"
        f"<candidate_draft>\n{draft.body}\n</candidate_draft>\n\n"
        f"<cited_records>\n{cited}\n</cited_records>"
    )
    return [{"role": "user", "content": content}]


def unusable_reason(result: CheckerResult) -> str | None:
    """One rule, two enforcement points: `_reject_unusable` here, `ModelRetry` in the binding."""
    if isinstance(result, Verdict) and result.violates and result.violation_class is None:
        return "verdict reports a violation without naming a class"
    return None


def span_absent_reason(result: CheckerResult, body: str) -> str | None:
    """Gate-side only: the transport seam has no draft, so no binding can feed this back."""
    if isinstance(result, Verdict) and result.span is not None and result.span not in body:
        return f"span {result.span!r} is not a verbatim substring of the draft body"
    return None


def _reject_unusable(result: object) -> None:
    """Raise unless `result` is an answer the gate can act on. Neither case is a schema question.

    Both shapes below are ones pydantic accepts and the engine cannot use, which is why refusing
    them belongs here, beside the retry budget, rather than at the type boundary:

    - **Not one of the two registered outputs.** `None`, prose, a raw dict or the class itself all
      used to be returned unexamined and to raise `AttributeError` inside the caller instead.
    - **A violation with no class.** A `Finding` needs a class, so the engine builds one only when
      `violates` and `violation_class` are both set -- meaning an unnamed violation was
      indistinguishable from a clean draft and transmitted. `violates=False` with no class is the
      ordinary compliant answer and is deliberately not refused. The rule is `unusable_reason` and
      is called rather than restated, so this refusal and the one a transport binding raises from
      cannot drift apart.

    Raised inside the retry loop, so an unusable answer costs an attempt and then becomes
    `CheckerUnavailable` rather than reaching a caller intact.

    The span rule is deliberately **not** here. It needs the draft body, which this signature does
    not take, and `Checker.check` applies it in the same loop for the same budget.
    """
    if not isinstance(result, (Verdict, FlagForReview)):
        raise TypeError(f"checker returned {type(result).__name__}, not a verdict or a review flag")
    reason = unusable_reason(result)
    if reason is not None:
        raise ValueError(reason)


class Checker:
    def __init__(
        self,
        model: str,
        drafter_model: str,
        transport: Callable[[list[dict]], CheckerResult],
        retries: int = 2,
    ) -> None:
        assert_checker_not_weaker(model, drafter_model)
        self._model = model
        self._transport = transport
        self._retries = retries

    def check(self, draft: Draft, record: Record) -> CheckerResult:
        messages = build_checker_messages(draft=draft, record=record)
        last: Exception | None = None
        for _ in range(self._retries + 1):
            try:
                result = self._transport(messages)
                _reject_unusable(result)
                reason = span_absent_reason(result, draft.body)
                if reason is not None:
                    raise ValueError(reason)
                return result
            # A transport raising `CheckerUnavailable` has declared the question closed, not failed
            # an attempt at it. Swallowed by the bare except below, that declaration cost two more
            # calls and came back out carrying its own message, so no assertion on the message could
            # tell the wrapper from the original and only a call count saw the difference.
            except CheckerUnavailable:
                raise
            except Exception as exc:
                last = exc
        raise CheckerUnavailable(str(last)) from last
