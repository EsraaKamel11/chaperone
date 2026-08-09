"""Pydantic AI bound as a Checker transport. Prompt assembly stays in build_checker_messages.

The recorded verdicts predate this module and were not re-run through it; no published rate was
measured through it. Every test that drives it uses `FunctionModel`, which answers from a local
function; nothing here contacts a network and no key is read.

**One rule, enforced at two points, and only one of the two rules travels.** `unusable_reason` is a
property of the verdict alone, so the output validator below can apply it and ask the model again
with the reason it returned. `span_absent_reason` is deliberately absent: it needs the draft body,
which at this seam is out of scope, so a span violation cannot be fed back here and only ever
denies inside `Checker.check`. Wiring it in would mean handing the draft to the transport, which is
the coupling the split exists to avoid.

**Two retry budgets in series must add, not multiply.** The binding retries a semantically unusable
answer, `Checker.check` retries a failing transport, and left to compose they cost `(R+1) * (N+1)`
model calls for one draft. Exhaustion here is therefore raised as `CheckerUnavailable`, which
`Checker.check` re-raises untouched rather than treating as one more failed attempt -- a
declaration that the question is closed, not a symptom. Measured at both ends: with the mapping
below removed, or with that escape removed from `Checker.check`, the same draft costs 9 model
calls instead of 3.

**`request_limit` is a second fence at the same position, and today it is unfalsifiable.** With
`retries=N` pydantic-ai permits exactly `N+1` requests before raising `UnexpectedModelBehavior`,
and `request_limit=N+1` permits exactly the same number, so `UsageLimitExceeded` cannot fire on
2.23.0 and no test here reaches it -- the observed count is 3 with the limit and 3 without it. It
is kept because the mapping it backs up is classified by an exception type the library owns: a
future version that recounted retries would revert the budget to the product silently, and a
request ceiling bounds that structurally rather than by exception classification. A disclosed
structural bound, not a tested property.
"""
from __future__ import annotations

from collections.abc import Callable

from pydantic_ai import Agent, ModelRetry
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.usage import UsageLimits

from chaperone.gates.checker import (
    CheckerResult, CheckerUnavailable, assert_checker_not_weaker, unusable_reason,
)


def _as_history(messages: list[dict]) -> list[ModelRequest]:
    return [ModelRequest(parts=[UserPromptPart(m["content"])]) for m in messages]


def pydantic_ai_transport(
    model,
    *,
    checker_tier: str,
    drafter_tier: str,
    retries: int = 2,
) -> Callable[[list[dict]], CheckerResult]:
    """Two enforcement points, one predicate: the tier ordering is asserted here and in `Checker`.

    `checker_tier`/`drafter_tier` and `model` are separate namespaces and the separation is
    load-bearing. `assert_checker_not_weaker` reads `MODEL_STRENGTH` and raises on anything that is
    not one of its three tier names, while `Agent` needs a model spec or a `Model` instance -- a
    `FunctionModel` in every test here. Passing `model` to the assertion would refuse every call.
    """
    assert_checker_not_weaker(checker_tier, drafter_tier)
    agent: Agent = Agent(model, output_type=CheckerResult, retries=retries)

    @agent.output_validator
    def _semantic(output: CheckerResult) -> CheckerResult:
        """`unusable_reason` called, never restated, so the two enforcement points cannot drift.

        The reason text is handed to `ModelRetry` unchanged: it is what reaches the model on the
        retry request, so the checker is told which rule it broke rather than merely asked again.
        """
        reason = unusable_reason(output)
        if reason is not None:
            raise ModelRetry(reason)
        return output

    limits = UsageLimits(request_limit=retries + 1)

    def transport(messages: list[dict]) -> CheckerResult:
        try:
            run = agent.run_sync(user_prompt=None, message_history=_as_history(messages),
                                 usage_limits=limits)
        except (UnexpectedModelBehavior, UsageLimitExceeded) as exc:
            raise CheckerUnavailable(str(exc)) from exc
        return run.output

    return transport
