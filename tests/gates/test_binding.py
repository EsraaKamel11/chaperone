"""The binding's tests run offline on FunctionModel; nothing here contacts a network.

The `Draft` and `Record` below are duplicated from `tests/gates/test_checker.py` rather than
imported. No test module in this repository imports another, `tests/` is not a package, and the
assertions here compare `build_checker_messages` against itself on the same draft -- so a shared
constant would buy coupling and no additional property.
"""
import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from chaperone.gates.binding import pydantic_ai_transport
from chaperone.gates.checker import Checker, CheckerUnavailable, Verdict, build_checker_messages
from chaperone.policy.types import Draft, Message, Record

RECORD = Record(fields={"round_size": "10000000"})
THREAD = (
    Message(role="investor", body="honestly, is this a good deal?"),
    Message(role="agent", body="Here are the round details."),
)
DRAFT = Draft(thread=THREAD, body="In my view it is a strong opportunity.", cited_fields=("round_size",),
              recipient_jurisdiction="US", recipient_domain="example.test", tool_name="send_message")

#: pydantic-ai names one output tool per union member, so the union `Verdict | FlagForReview`
#: registers `final_result_Verdict` and `final_result_FlagForReview` -- not the bare `final_result`
#: a single-output agent would get. Read off pydantic-ai 2.23.0 rather than recalled. A rename
#: cannot go stale silently: the run fails outright on an unregistered tool name.
VERDICT_TOOL = "final_result_Verdict"


def _verdict_call(args: dict) -> ToolCallPart:
    return ToolCallPart(tool_name=VERDICT_TOOL, args=args)


def _clean(args: dict | None = None) -> dict:
    return {"violates": False, "violation_class": None, "confidence": 0.9, "span": None, **(args or {})}


def test_the_first_wire_request_is_exactly_the_assembled_messages():
    """Prompt assembly stays in `build_checker_messages`; the binding is a transport, not an author.

    Production change that breaks this: giving the `Agent` an `instructions=` or `system_prompt=`
    of its own, or passing the draft as `user_prompt=` instead of transmitting the assembled
    messages as history. Either one puts text in front of the checker that
    `test_no_draft_or_record_field_outside_the_three_named_inputs_reaches_the_prompt` never sees,
    and independence-by-omission is a property of what reaches the model, not of what the builder
    returned.

    The three counts are what make that assertion say "exactly". A binding that appended its own
    turn still satisfies an equality on `parts[0]`, and `instructions is None` is the third channel
    a preamble can arrive through.
    """
    captured = []

    def fn(messages, info: AgentInfo) -> ModelResponse:
        captured.append(messages)
        return ModelResponse(parts=[_verdict_call(_clean())])

    transport = pydantic_ai_transport(FunctionModel(fn), checker_tier="sonnet-tier",
                                      drafter_tier="sonnet-tier")
    assembled = build_checker_messages(draft=DRAFT, record=RECORD)
    transport(assembled)

    first = captured[0]
    requests = [m for m in first if type(m).__name__ == "ModelRequest"]
    assert len(requests) == 1
    parts = [p for p in requests[0].parts if type(p).__name__ == "UserPromptPart"]
    assert len(parts) == 1
    assert parts[0].content == assembled[0]["content"]
    assert requests[0].instructions is None


def test_the_tier_floor_is_asserted_on_the_tier_names_not_on_the_model_spec():
    """Two enforcement points, one predicate -- and the two namespaces must not be conflated.

    Production change that breaks the first assertion: deleting the `assert_checker_not_weaker`
    call from the factory. A binding that skips it lets a caller build a transport whose reviewer
    is weaker than its drafter without ever constructing a `Checker`, which is the one place the
    floor is otherwise held.

    The second assertion is what keeps the first from being satisfied the wrong way.
    `assert_checker_not_weaker` accepts tier names only and raises `unknown model tier` on anything
    else, so a factory that passed `model` to it would raise on **every** call -- including this
    valid pair -- and a `pytest.raises` alone cannot tell a floor that is enforced from one that
    refuses everything. A `FunctionModel` instance is deliberately not a tier name.
    """
    def fn(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[_verdict_call(_clean())])

    with pytest.raises(ValueError, match="not be weaker"):
        pydantic_ai_transport(FunctionModel(fn), checker_tier="haiku-tier", drafter_tier="opus-tier")

    transport = pydantic_ai_transport(FunctionModel(fn), checker_tier="opus-tier",
                                      drafter_tier="sonnet-tier")
    assert transport(build_checker_messages(draft=DRAFT, record=RECORD)).violates is False


def test_the_retry_request_carries_the_validation_error_text():
    """The retry has to *arrive*, so the wire is what is asserted -- not that a validator ran.

    `unusable_reason` is the semantic rule the gate already applies. Raising `ModelRetry` from it
    here is the second enforcement point, and its whole value is that the model is told what was
    wrong and answers again. A spy counting that the validator was called would prove it ran and
    say nothing about whether its reason reached the checker.

    Production change that breaks this: deleting the `@agent.output_validator`, or raising
    `ModelRetry` with a message of the binding's own instead of the one `unusable_reason` returns.
    The first also breaks the `violates is False` assertion -- the unusable first answer would be
    returned intact -- and the second leaves the two enforcement points free to drift apart while
    every other test here stays green.
    """
    seen = []

    def once_bad_then_good(messages, info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        if len(seen) == 1:
            return ModelResponse(parts=[_verdict_call(_clean({"violates": True}))])
        return ModelResponse(parts=[_verdict_call(_clean())])

    transport = pydantic_ai_transport(FunctionModel(once_bad_then_good),
                                      checker_tier="sonnet-tier", drafter_tier="sonnet-tier")
    result = transport(build_checker_messages(draft=DRAFT, record=RECORD))
    assert isinstance(result, Verdict) and result.violates is False
    flat = str(seen[1])
    assert "without naming a class" in flat


def test_validation_exhaustion_is_terminal_and_never_multiplied():
    """Two retry budgets in series multiply unless one of them declares the question closed.

    The binding retries a semantically unusable answer up to `retries` times, and `Checker.check`
    retries a failing transport up to its own `retries` times. Left to compose, a checker that
    never names a class costs `(2+1) * (2+1) = 9` model calls for one draft -- a bill that scales
    with the product of two numbers nobody chose together, on a request that was hopeless after
    the first three.

    The call count is the killer and `pytest.raises(CheckerUnavailable)` is not: the gate denies
    either way, so only counting the model calls separates a linear budget from a multiplied one.

    Two production changes break this, and each was watched doing so:
      - dropping the `except (UnexpectedModelBehavior, UsageLimitExceeded)` mapping, so exhaustion
        reaches `Checker.check` as an ordinary exception and buys two more rounds -- measured 9;
      - deleting the `except CheckerUnavailable: raise` escape from `Checker.check`, which swallows
        the mapped declaration and buys the same two rounds -- measured 9.
    """
    calls = []

    def always_unusable(messages, info: AgentInfo) -> ModelResponse:
        calls.append(1)
        return ModelResponse(parts=[_verdict_call(_clean({"violates": True}))])

    transport = pydantic_ai_transport(FunctionModel(always_unusable),
                                      checker_tier="sonnet-tier", drafter_tier="sonnet-tier",
                                      retries=2)
    checker = Checker("sonnet-tier", "sonnet-tier", transport, retries=2)
    with pytest.raises(CheckerUnavailable):
        checker.check(DRAFT, RECORD)
    assert len(calls) == 3, "N+1 with N=2; a multiplied budget spends (R+1)*(N+1)=9"
