from __future__ import annotations

from typing import Callable, Union

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


MODEL_STRENGTH = {"haiku-tier": 1, "sonnet-tier": 2, "opus-tier": 3}


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
    """Independence is enforced by omission. Nothing untransmitted enters this prompt."""
    thread = "\n".join(f"[{m.role}] {m.body}" for m in draft.thread)
    cited = "\n".join(f"{name}: {record.get(name)}" for name in draft.cited_fields if record.get(name))
    content = (
        f"{CHECKER_INSTRUCTIONS}\n\n"
        f"<transmitted_thread>\n{thread}\n</transmitted_thread>\n\n"
        f"<candidate_draft>\n{draft.body}\n</candidate_draft>\n\n"
        f"<cited_records>\n{cited}\n</cited_records>"
    )
    return [{"role": "user", "content": content}]


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
                return self._transport(messages)
            except Exception as exc:
                last = exc
        raise CheckerUnavailable(str(last)) from last
