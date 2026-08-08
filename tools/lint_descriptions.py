"""Tool descriptions are an enforcement surface, so they are linted rather than reviewed.

Design spec 4.3: a description is the only thing standing between a model and the wrong tool, and
the three failures below are the ones a reviewer stops noticing after the second read. `main()` is
what CI runs, so **the exit code is the property**.

**The capability rule, stated rather than left to the letters.** A description must not claim a
capability its tool was not granted. Two decisions make that checkable in both directions:

- **One vocabulary, minus a per-tool grant** -- never a per-tool list of forbidden words. A
  forbidden list checks each tool only for the words somebody thought to write down for it, and it
  checks a tool nobody wrote down for **nothing at all**: `_CAPABILITY_WORDS.get(name, ())` was the
  shape, and under it `read_policy` could have promised to send while the linter agreed, because
  its entry named `writes` and `updates` alone. Measured, and
  `test_a_capability_word_is_forbidden_for_every_tool_that_was_not_granted_it` is that measurement.
  A grant defaults to **empty**, so an unrecognised tool is checked against the whole vocabulary
  rather than none of it. That is the fail-closed direction, and it is the direction a permissive
  fallback always gets wrong.
- **The vocabulary holds inflected verb forms only, never a bare stem, and the match is a
  substring match.** Substring, because a spurious finding beats a missed one:
  `\bsends\b` would read `resends` as a word the vocabulary does not hold and let the promise
  through, while the substring rule catches it. Inflected forms only, because that is what makes a
  legitimate description safe by rule instead of by luck: `transmitted thread`, `no send
  capability` and the identifier `send_message` all carry a capability stem, none of them promises
  anything, and none of them contains `sends`, `transmits`, `writes` or `updates`. Adding a bare
  stem to `CAPABILITY_WORDS` would flag all three.

**What the rule does not reach, so nobody has to discover it.** A promise phrased in any other
form -- `will send`, `can transmit`, `is able to write` -- is a promise this linter does not see. It
catches the indicative, which is the mood a tool description is written in, and it is a lexical rule
over three hand-written strings rather than a claim about descriptions in general.

**This module makes no claim about what a description causes a model to do.** It reads text and
reports on its shape. Whether a boundary clause changes a tool selection is a measured question and
is not measured here.
"""
from __future__ import annotations

import sys
from typing import Mapping, Sequence

TOOL_DESCRIPTIONS: Mapping[str, str] = {
    "draft_message": (
        "Composes a candidate outbound message from the mandate record and the transmitted thread. "
        "Do not use this to transmit anything; it has no send capability. Use send_message for that."
    ),
    "send_message": (
        "Transmits an approved message to a consented recipient. "
        "Do not use this to compose; use draft_message first. Every call passes the boundary gate."
    ),
    # Named no sibling when this file was first written, so the shipped set failed the linter's own
    # first rule. The description was corrected rather than the rule: a linter its own corpus fails
    # is a rigged baseline, and a rule bent to fit the corpus is a rigged linter.
    "read_policy": (
        "Returns the current constraint set as data so a redraft can target the actual rule. "
        "Do not use this to change policy; no tool can. Compose the redraft with draft_message."
    ),
}

#: The whole capability vocabulary. Every tool is checked against all of it except what it is
#: granted below.
CAPABILITY_WORDS = frozenset({"sends", "transmits", "writes", "updates"})

#: What each tool may say about itself. **Absent means granted nothing**, which is why the lookup
#: below defaults to the empty set: the tool nobody entered here is the tool this check exists for.
GRANTED_CAPABILITIES: Mapping[str, frozenset[str]] = {
    "draft_message": frozenset(),
    "send_message": frozenset({"transmits", "sends"}),
    "read_policy": frozenset(),
}

MINIMUM_WORDS = 12


def forbidden_words(name: str) -> frozenset[str]:
    """The vocabulary minus this tool's grant. An unrecognised tool is granted nothing."""
    return CAPABILITY_WORDS - GRANTED_CAPABILITIES.get(name, frozenset())


def lint_description(name: str, text: str, siblings: Sequence[str]) -> list[str]:
    """Every reason this description is not fit to ship, in a stable order."""
    issues: list[str] = []
    lowered = text.lower()
    if len(text.split()) < MINIMUM_WORDS:
        issues.append(f"{name}: too short to disambiguate")
    if siblings and not any(sibling in text for sibling in siblings):
        issues.append(f"{name}: no boundary clause naming a sibling tool")
    if "do not use this" not in lowered:
        issues.append(f"{name}: no boundary clause naming what it is not for")
    for forbidden in sorted(forbidden_words(name)):
        if forbidden in lowered:
            issues.append(f"{name}: promises a capability it lacks ({forbidden!r})")
    return issues


def main(descriptions: Mapping[str, str] | None = None) -> int:
    """Lint every description against its siblings and report the exit code CI reads.

    The parameter exists so the failing path has a test. Without it the only reachable input is the
    shipped set, which passes, and an exit code that has only ever been observed at 0 is an exit
    code nobody has seen work.

    **A run that linted nothing is reported, not reported clean.** `1 if issues else 0` over an
    empty set is 0, which is indistinguishable from three descriptions that passed, so an emptied or
    renamed description set would disarm this tool in silence. Same treatment as
    `tools/static_audit.py` gives an audit over no file and `tools/coverage_map.py` gives a registry
    with no class: it travels the same path to the same exit code as any other finding.
    """
    descriptions = TOOL_DESCRIPTIONS if descriptions is None else descriptions
    names = list(descriptions)
    issues: list[str] = []
    if not descriptions:
        issues.append("no tool description to lint -- linted nothing")
    for name, text in descriptions.items():
        issues += lint_description(name, text, [n for n in names if n != name])
    for issue in issues:
        print(issue)
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
