"""Offline-checkable wiring for the in-process hook. Run: python demo/sdk_hook.py --show-wiring

The one file in this repository that imports the Agent SDK. `src/chaperone/gates/sdk_callback.py`
holds the decision and deliberately imports no SDK, so the gate stays legible to a static reader who
has none; the import is confined here, to the wiring, which is the only part that actually needs a
runtime. Nothing but the registration lives in this file for that reason.

**Nothing is stipulated here, because nothing is called.** `demo/day2.py` and `demo/full.py` script
their transports and say so in their own docstrings, because they run a draft past a checker. This
starts no client, submits no draft and consults no model: it builds the options object a client
would be constructed with and prints what got registered on it. So there is no scripted verdict to
declare, and `--show-wiring` connects nowhere.

**Imported by no test, and run by neither the suite nor the build.** The SDK is an optional extra,
`pip install -e ".[sdk]"`, and `.github/workflows/ci.yml` installs `.[dev]`, so a build step that
imported this file would fail on a dependency this repository deliberately does not require. What
the suite pins is the callback rather than its wiring:
`tests/gates/test_sdk_callback.py` holds it to importing no SDK, and
`tests/gates/test_hook.py` is where its decisions meet the command hook's, running both over the
same corpus payloads and comparing verdict, category and detail row by row. That corpus carries
allows as well as denials, which is what earns the comparison in both directions.

**`setting_sources=[]` is the load-bearing argument, not boilerplate.** Left at its default the
runtime reads hook settings off the filesystem, and a `PreToolUse` deny contributed by some file in
the checkout would be indistinguishable, at the point of refusal, from one this repository decided.
Emptying it makes `pre_tool_use_deny` the only source of a deny on this options object, which is
what lets the printed registration be read as the whole of the policy in force.

Pinned against claude-agent-sdk 0.2.130.
"""
from __future__ import annotations

import sys

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

from chaperone.gates.sdk_callback import pre_tool_use_deny


def build_options() -> ClaudeAgentOptions:
    """The registration, as an options object a client would be constructed with."""
    return ClaudeAgentOptions(
        setting_sources=[],  # isolation: no filesystem hook can contribute a deny
        hooks={"PreToolUse": [HookMatcher(matcher="send_message", hooks=[pre_tool_use_deny])]},
    )


if __name__ == "__main__":
    if "--show-wiring" in sys.argv:
        options = build_options()
        print("PreToolUse matchers:", [m.matcher for m in options.hooks["PreToolUse"]])
        print("setting_sources:", options.setting_sources)
        sys.exit(0)
    print("the live lane is not built here; --show-wiring prints the registration")
    sys.exit(0)
