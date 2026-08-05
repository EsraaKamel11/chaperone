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
  wrong type, or a violation reported without a class -- and a refusal costs a retry and then
  becomes `CheckerUnavailable`, which callers fail closed on. Anything well-formed is returned as
  given, and whether it is right is a measured question, not a structural one.
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


def build_checker_messages(draft: Draft, record: Record) -> list[dict]:
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


def _reject_unusable(result: object) -> None:
    """Raise unless `result` is an answer the gate can act on. Neither case is a schema question.

    Both shapes below are ones pydantic accepts and the engine cannot use, which is why refusing
    them belongs here, beside the retry budget, rather than at the type boundary:

    - **Not one of the two registered outputs.** `None`, prose, a raw dict or the class itself all
      used to be returned unexamined and to raise `AttributeError` inside the caller instead.
    - **A violation with no class.** A `Finding` needs a class, so the engine builds one only when
      `violates` and `violation_class` are both set -- meaning an unnamed violation was
      indistinguishable from a clean draft and transmitted. `violates=False` with no class is the
      ordinary compliant answer and is deliberately not refused.

    Raised inside the retry loop, so an unusable answer costs an attempt and then becomes
    `CheckerUnavailable` rather than reaching a caller intact.
    """
    if not isinstance(result, (Verdict, FlagForReview)):
        raise TypeError(f"checker returned {type(result).__name__}, not a verdict or a review flag")
    if isinstance(result, Verdict) and result.violates and result.violation_class is None:
        raise ValueError("verdict reports a violation without naming a class")


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
        messages = build_checker_messages(draft, record)
        last: Exception | None = None
        for _ in range(self._retries + 1):
            try:
                result = self._transport(messages)
                _reject_unusable(result)
                return result
            except Exception as exc:
                last = exc
        raise CheckerUnavailable(str(last)) from last
