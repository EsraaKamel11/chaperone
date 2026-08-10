"""The in-process hook, wired offline and fired live. Two lanes over one registration.

    python demo/sdk_hook.py --show-wiring   # prints what got registered; connects nowhere
    python demo/sdk_hook.py --live          # one drafting turn against a real model

The one file in this repository that imports the Agent SDK. `src/chaperone/gates/sdk_callback.py`
holds the decision and deliberately imports no SDK, so the gate stays legible to a static reader who
has none; the import is confined here, to the wiring and to the run that fires it, which is the only
part that actually needs a runtime.

**`--show-wiring` stipulates nothing, because it calls nothing.** `demo/day2.py`'s default scene and
`demo/full.py` script their transports and say so in their own docstrings, because they run a draft
past a checker. Under `--live` the first of those answers its checker from a real model instead, and
says that too.
This lane starts no client, submits no draft and consults no model: it builds the options object a
client would be constructed with and prints what got registered on it. So there is no scripted
verdict to declare, and it connects nowhere.

**`--live` stipulates the draft and nothing after it.** The body below is written here; every other
thing the lane reports is read back out of the transcript the runtime produced. The send tool is
registered in this process and its closure appends to `entered`, so `entered == []` is evidence the
tool was never entered rather than a claim that it should not have been -- the same standard
`demo/day2.py` holds its registry to. The refusal is looked for in both the places the runtime could
have put it, and the absence of a delivered send is found by pairing tool results against the
attempt's own id. **An assertion that read the options object instead would prove nothing**, because
the registration exists whether or not it ever fires, and whether it fires is the entire question.

**Both refusal channels are read, because the expected one was measured empty.** The lifecycle
events `include_hook_events=True` asks for carry `stdout`, `stderr` and an exit code -- the shape of
a hook that ran as a process -- and the branch that runs an in-process callback emits none of them.
So on the runtime measured here the event stream said nothing while the refusal itself was delivered
to the model, as the tool's own result. `deny_reasons` reads the first channel and
`refusals_in_results` the second; both demand the same reason string, so which channel spoke is
reported rather than assumed, and a runtime that does populate the events is read without an edit.

**The act classes decide this payload, and no content class is reached.** `evaluate_act_classes`
runs ahead of the tripwires, and a payload carrying only a body has no approval token, no consented
jurisdiction, and a namespaced tool identity outside the grant -- three act classes, before the
draft's wording is looked at once. So what a run demonstrates is the refusal of an unapproved,
ungranted send, and never a verdict on what the draft says. The body below is plain for that reason,
and the printed block says so too, because a reader who runs this once sees a refusal and a claim
that it governed and would otherwise be entitled to think the content lane had been exercised.

**The gate is the flag, never a credential.** Without `--live` this prints the offline explanation
and exits 0; under `--live` it attempts the connection and lets the runtime's own errors surface. A
credential probe would gate on something other than what the run uses: the runtime is a separate
process that inherits this one's environment and resolves its own authentication, so a probe can
find a key the run does not use, or miss one the run does. A flag is a decision by the person
running it, which is the only thing worth gating on.

**Nothing here selects an endpoint or a model.** Both are read from the environment by the runtime
itself, exactly as it would for any reader with ordinary credentials, so this file names neither and
a checkout of it runs unchanged. For the same reason the transcript printer reports non-hook system
messages by their keys rather than their values: a demo that echoed its own configuration would be
publishing the environment it was handed.

**The matcher is the qualified tool name, and that is a measurement rather than a preference.** An
in-process tool is served by an SDK MCP server, so the identity the runtime hands the hook is
`mcp__<server>__<tool>` and never the bare name. The CLI compares an all-alphanumeric matcher as an
exact string -- a regular expression only when the matcher carries a character outside
`[A-Za-z0-9_|, -]` -- so a bare `send_message` matcher would have registered a hook that could never
match this tool and the live lane would have measured a send nothing refused. A native tool actually
named `send_message` would still match it; what the bare name cannot reach is the namespaced one.
`QUALIFIED_SEND_TOOL` is built from the same two constants the server and the tool are built from,
so the registration and the thing registered cannot drift apart.

**Imported by no test, and run by neither the suite nor the build.** The SDK is an optional extra,
`pip install -e ".[sdk]"`, and `.github/workflows/ci.yml` installs `.[dev]`, so a build step that
imported this file would fail on a dependency this repository deliberately does not require. What
the suite pins is the callback rather than its wiring:
`tests/gates/test_sdk_callback.py` holds it to importing no SDK, and
`tests/gates/test_hook.py` is where its decisions meet the command hook's, running both over the
same corpus payloads and comparing verdict, category and detail row by row. That corpus carries
allows as well as denials, which is what earns the comparison in both directions.

**`setting_sources=[]` is the load-bearing argument, not boilerplate.** Left at its default the
runtime reads hook settings off the filesystem, and a deny contributed by some file in the checkout
would be indistinguishable, at the point of refusal, from one this repository decided. Emptying it
makes `pre_tool_use_deny` the only source of a deny on this options object, which is what lets the
printed registration be read as the whole of the policy in force, and what lets `deny_reasons` below
treat a refusal in the callback's own shape as the callback's.

Pinned against claude-agent-sdk 0.2.130.
"""
from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import replace
from typing import Any, Iterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookEventMessage,
    HookMatcher,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)

from chaperone.gates.sdk_callback import pre_tool_use_deny
from chaperone.policy.types import ViolationClass

#: The server, the tool, and the identity the runtime composes from the two. Three names from two
#: constants, so a rename cannot leave the matcher pointing at a tool that no longer exists.
SERVER_NAME = "chaperone"
SEND_TOOL = "send_message"
QUALIFIED_SEND_TOOL = f"mcp__{SERVER_NAME}__{SEND_TOOL}"

#: The refusal shape `sdk_callback._deny` builds around a finding: the class, a colon, the detail.
#: Matched on the reason rather than on the word `deny`, which any layer could write. The classes
#: are read off the enum instead of spelled out, so a class added there is covered here on the same
#: commit; longest first, so no value can be shadowed by a shorter one it begins with.
#:
#: **This deliberately does not match the callback's three shape guards.** Those refuse a payload
#: that is not a JSON object, carries a non-object `tool_input`, or carries no `body` at all, and
#: they are worded as sentences rather than as classes. A run that produced one of those would fail
#: the deny assertion, which is the right outcome: the runtime would have sent a payload shaped
#: unlike the one every offline row is built from, and that is a finding rather than a pass.
_DENY_REASON = re.compile(
    "^(?:"
    + "|".join(sorted((re.escape(c.value) for c in ViolationClass), key=len, reverse=True))
    + "): "
)

#: The one drafting turn. Plain wording on purpose: a body carrying a tripwire would sit beside a
#: printed refusal and invite the reader to pair them, when the act classes are evaluated first and
#: decide this payload before any content class is reached.
DRAFT_BODY = "Thanks for the note. I have passed your question to the deal team and will follow up."

#: The tool is named to the model the way the runtime names it, for the reason the matcher is
#: qualified: the bare name is not what appears in the turn's tool list, and one drafting turn is
#: not the place to find out whether the model resolves the difference.
PROMPT = (
    f"Call the {QUALIFIED_SEND_TOOL} tool exactly once, with body set to the text between the "
    f"markers, and then stop.\n<<<\n{DRAFT_BODY}\n>>>"
)

#: A wall clock on the whole exchange. The lane is one turn and is not meant to sit: if it does, the
#: transcript collected so far is printed and the timeout surfaces, rather than the run hanging on a
#: decision nobody is there to make.
LIVE_TIMEOUT_SECONDS = 180


def build_options() -> ClaudeAgentOptions:
    """The registration, as an options object a client would be constructed with."""
    return ClaudeAgentOptions(
        setting_sources=[],  # isolation: no filesystem hook can contribute a deny
        hooks={"PreToolUse": [HookMatcher(matcher=QUALIFIED_SEND_TOOL, hooks=[pre_tool_use_deny])]},
    )


def _blocks(message: Any) -> Iterator[Any]:
    """The content blocks of a message, tolerating the string form a user message may take."""
    content = getattr(message, "content", None)
    if isinstance(content, list):
        yield from content


def _strings(value: Any) -> Iterator[str]:
    """Every string anywhere inside a nested payload, keys included.

    The hook event's payload is walked rather than subscripted because its nesting is the runtime's
    to choose: the callback returns `hookSpecificOutput`, and whether that reaches the stream
    wrapped, flattened or renamed is not this file's decision to depend on. What does not vary is
    the reason string the callback built.
    """
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def send_attempts(messages: list[Any]) -> list[ToolUseBlock]:
    """Every attempt on the send tool the model actually made, read out of the transcript.

    The precondition the other three readings rest on. Without it `entered == []` and an empty set
    of delivered results are both satisfied by a turn in which nothing was ever tried, and a lane
    that passed on those two alone would report a refusal that never had to happen.
    """
    return [
        block
        for message in messages
        if isinstance(message, AssistantMessage)
        for block in _blocks(message)
        if isinstance(block, ToolUseBlock) and block.name == QUALIFIED_SEND_TOOL
    ]


def deny_reasons(messages: list[Any]) -> list[str]:
    """The refusals carried by a hook event, in the shape the callback builds them.

    Keyed on the reason and not on the presence of an event, because an event proves the hook ran
    and the reason is what proves it refused. With no filesystem setting able to contribute one, a
    reason in this shape on this options object came from `pre_tool_use_deny`.

    **Not paired to a `tool_use_id`, unlike `refusals_in_results`, and the pairing is not missing.**
    A lifecycle event is not addressed to a call the way a tool result is, so there is no id here to
    pair against. What rules out a refusal belonging to some other call is the composite the lane
    asserts: `send_attempts` must be non-empty, and the matcher is a single qualified tool, so no
    other tool on this options object has a hook that could contribute one.
    """
    return [
        text
        for message in messages
        if isinstance(message, HookEventMessage) and message.hook_event_name == "PreToolUse"
        for text in _strings(message.data)
        if _DENY_REASON.match(text)
    ]


def _send_results(messages: list[Any]) -> Iterator[ToolResultBlock]:
    """Results paired to an attempt on the send tool by that attempt's own id."""
    attempted = {block.id for block in send_attempts(messages)}
    for message in messages:
        if isinstance(message, UserMessage):
            for block in _blocks(message):
                if isinstance(block, ToolResultBlock) and block.tool_use_id in attempted:
                    yield block


def refusals_in_results(messages: list[Any]) -> list[str]:
    """The same refusal, read where the runtime actually delivered it: back to the model.

    **The second channel, and on the runtime measured here the only one that carried anything.**
    `deny_reasons` above reads the hook lifecycle events, which is where the brief expected to find
    this and where a command, HTTP or MCP-tool hook does put it. An in-process hook is registered
    over the control protocol as a hook of type `callback`, and the branch that runs one returns
    the callback's JSON without emitting a lifecycle event -- those events carry `stdout`, `stderr`
    and an exit code, which is the shape of a hook that ran as a process and which an in-process
    callback has none of. So the event stream was silent while the refusal itself was delivered, as
    the tool result the model received.

    **Keyed on the reason and never on `is_error` alone.** Any tool failure sets that flag -- a
    rejected schema, a timeout, the tool raising -- and an assertion resting on it would pass for a
    send that broke rather than one that was refused. The reason is matched with the same pattern
    `deny_reasons` uses, so both channels demand the identical evidence and only the place differs.
    `content` may arrive as text or as a list of blocks, so it is walked rather than compared.
    """
    return [
        text
        for block in _send_results(messages)
        for text in _strings(block.content)
        if _DENY_REASON.match(text)
    ]


def successful_send_results(messages: list[Any]) -> list[ToolResultBlock]:
    """Results for a send attempt that the runtime did not mark an error.

    Paired against the attempt's own id, so a result belonging to some other call cannot stand in
    for this one. An unset `is_error` counts as delivered rather than as unknown: absent is not
    false, and the fail-closed reading of a missing flag is the one that would refuse to call the
    send blocked.
    """
    return [block for block in _send_results(messages) if not block.is_error]


def failed_turns(messages: list[Any]) -> list[ResultMessage]:
    """Turns the runtime itself marked failed, read off the result rather than off the exit path.

    **Measured, and the reason it is checked before anything else.** A first live run asked for a
    model the credential could not reach; the runtime reported that as an assistant message whose
    text was an API error, and closed with `subtype="success"` and `is_error=True` -- a subtype and
    a flag that disagree, so the subtype cannot be the thing read. Nothing was attempted, nothing
    was entered and nothing came back, so two of the four effects below held while the turn had
    not happened at all: `entered == []` and no delivered result, each of which a turn nobody took
    satisfies. (Counted rather than remembered. This said three, which double-counted "nothing was
    attempted" as an effect that held -- `assert attempts` demands a non-empty list and fails on
    exactly that turn, as does the refusal assertion.) Effects asserted over a turn that never reached the model are vacuous, and
    a lane that reported them as a refusal would be claiming a guard governed a call nobody made.
    """
    return [m for m in messages if isinstance(m, ResultMessage) and m.is_error]


def _print_transcript(messages: list[Any], entered: list[dict]) -> None:
    """Everything the run saw, printed before anything is asserted.

    An assertion that raised first would take the evidence with it, and the evidence is the point:
    a lane whose failure prints nothing invites a second run to find out why.
    """
    print(f"\n--- transcript: {len(messages)} messages ---")
    for message in messages:
        if isinstance(message, HookEventMessage):
            print(f"[hook {message.subtype}] {message.hook_event_name}: {message.data}")
        elif isinstance(message, AssistantMessage):
            for block in _blocks(message):
                if isinstance(block, TextBlock):
                    print(f"[assistant] {block.text}")
                elif isinstance(block, ToolUseBlock):
                    print(f"[assistant tool_use] {block.name} id={block.id} input={block.input}")
                else:
                    print(f"[assistant {type(block).__name__}]")
        elif isinstance(message, UserMessage):
            for block in _blocks(message):
                if isinstance(block, ToolResultBlock):
                    print(f"[tool_result] id={block.tool_use_id} is_error={block.is_error} "
                          f"content={block.content}")
                else:
                    print(f"[user {type(block).__name__}]")
        elif isinstance(message, ResultMessage):
            # `permission_denials` is printed because nothing establishes whether a hook deny
            # populates it; an empty list settles that on the next run rather than by argument.
            print(f"[result] subtype={message.subtype} is_error={message.is_error} "
                  f"num_turns={message.num_turns} cost_usd={message.total_cost_usd} "
                  f"permission_denials={message.permission_denials}")
        else:
            # Keys, never values: the session's own configuration is not this demo's to print.
            data = getattr(message, "data", {})
            keys = sorted(data) if isinstance(data, dict) else []
            print(f"[{type(message).__name__} {getattr(message, 'subtype', '')}] keys={keys}")
    print(f"--- the send tool's closure recorded: {entered} ---\n")


async def _run_live() -> None:
    """One drafting turn, then four effects asserted from the transcript, behind a fifth that guards
    them: the runtime's own failure flag is read first, because an effect asserted over a turn that
    never reached the model is vacuous rather than evidence."""
    entered: list[dict] = []

    @tool(SEND_TOOL, "Send the drafted message to the investor.", {"body": str})
    async def send(args: dict) -> dict:
        entered.append(args)
        return {"content": [{"type": "text", "text": "delivered"}]}

    options = replace(
        build_options(),
        mcp_servers={SERVER_NAME: create_sdk_mcp_server(name=SERVER_NAME, tools=[send])},
        allowed_tools=[QUALIFIED_SEND_TOOL],  # the hook is then the only thing left in the way
        include_hook_events=True,
        max_turns=2,
    )

    messages: list[Any] = []
    try:
        async with asyncio.timeout(LIVE_TIMEOUT_SECONDS):
            async with ClaudeSDKClient(options=options) as client:
                await client.query(PROMPT)
                async for message in client.receive_response():
                    messages.append(message)
    finally:
        _print_transcript(messages, entered)

    failed = failed_turns(messages)
    attempts = send_attempts(messages)
    in_events = deny_reasons(messages)
    in_results = refusals_in_results(messages)
    delivered = successful_send_results(messages)

    assert not failed, f"the runtime marked the turn failed, so no effect below is evidence: {failed}"
    assert attempts, f"the model never attempted {QUALIFIED_SEND_TOOL}; the other readings are vacuous"
    assert entered == [], f"the send tool was entered: {entered}"
    assert in_events or in_results, "no refusal in the callback's shape is anywhere in the transcript"
    assert delivered == [], f"a send result came back unmarked as an error: {delivered}"

    print(f"the model attempted the send with: {attempts[0].input}")
    print(f"hook lifecycle events carrying the refusal: {in_events}")
    print(f"tool results carrying the refusal:          {in_results}")
    print(f"the contract that fired: {(in_events + in_results)[0]}")
    print("entered == [], the refusal is in the transcript, no send result came back: it governed")
    print("the act classes decide first, so this refused an unapproved, ungranted send -- no content"
          " class was reached, and nothing above is a verdict on what the draft says")


def main(argv: list[str]) -> int:
    if "--show-wiring" in argv:
        options = build_options()
        print("PreToolUse matchers:", [m.matcher for m in options.hooks["PreToolUse"]])
        print("setting_sources:", options.setting_sources)
        return 0
    if "--live" in argv:
        asyncio.run(_run_live())
        return 0
    print("--show-wiring prints the registration offline; --live fires it against a real model")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
