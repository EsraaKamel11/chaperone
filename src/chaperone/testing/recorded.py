"""Replay only. The suite is hermetic, offline and keyless.

Mutation testing over network-dependent tests is worthless, so this is a precondition for the
mutation harness rather than a convenience.

**There is one reader of `corpus/recorded_verdicts.json` and one decoder of a recorded row, and
neither of them is here.** `evals/harness.py` already shipped both: `load_recorded` reads the file
and raises rather than returning an empty mapping, and `recorded_verdict` turns one row into a
`Verdict` or into `None` meaning *the checker gave no usable answer*. A second implementation with
its own error handling is the drift CLAUDE.md forbids, and Task 22 measured that behavioural tests
do not catch it: re-inlining a behaviourally identical copy left 25 of 26 agreement rows green.
So this module holds a mapping it was handed, delegates every decode to `harness.recorded_verdict`,
and constructs no `Verdict` of its own. `test_the_transport_decodes_no_recorded_row_of_its_own`
reads this file with `ast` and holds it structurally, because that is the only kind of test that
sees a correct copy.

**Why the key is a digest of the messages and not a corpus id.** `Checker`'s transport is
`Callable[[list[dict]], CheckerResult]`: one argument, the messages, and nothing else. A two-argument
`__call__(messages, key)` cannot be a `Checker` transport at all, and the failure is not a loud one --
`Checker.check` catches every exception inside its retry loop, so the `TypeError` for the missing
argument is swallowed and re-raised as `CheckerUnavailable`, which callers fail closed on. A
transport that was never callable would then look exactly like a checker that was down. Measured
before this module was written. The key therefore has to come out of the messages, and
`build_checker_messages` is a pure function of the draft and the cited records, so a digest over the
messages is a stable name for the question that was asked.

**What this collapses, stated because `harness.recorded_verdict` refuses to collapse it.** There the
two ways of having no verdict are two values: an id the artifact does not hold raises, and a recorded
unavailability is a JSON `null`. Behind `Checker.check` they become one outcome, `CheckerUnavailable`,
because that loop catches everything. The distinction survives at this boundary and is tested here
directly -- `test_a_message_set_with_no_recording_raises_rather_than_answering` against
`test_a_recorded_unavailability_reaches_the_gate_as_a_closed_door` -- and it does not survive one
layer up. A caller who needs the two apart must call the transport, not the checker.

**`FlagForReview` is unrepresentable in this schema**, exactly as `evals/harness.py` records for the
ladder: the replay holds `violates`, `violation_class`, `confidence`, `span` and a JSON `null`, so
the third `CheckerResult` outcome cannot be recorded and no test driven from here reaches
`decide`'s flag path.

**Nothing here writes.** `tools/record_verdicts.py` is the only writer of the replay artifact, and a
second writer is the same drift in the other direction.
"""
from __future__ import annotations

import hashlib
from typing import Mapping, Sequence

from chaperone.evals.corpus import CorpusItem
from chaperone.evals.harness import HarnessError, load_recorded, recorded_verdict
from chaperone.gates.checker import CheckerUnavailable, Verdict, build_checker_messages


def key_for(messages: Sequence[Mapping[str, str]]) -> str:
    """A stable name for one checker question, digested from the messages that ask it."""
    material = "\n".join(f"{m['role']}\x00{m['content']}" for m in messages)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class RecordedTransport:
    """A `Checker`-compatible transport over a mapping of `key_for(messages)` to recorded rows."""

    def __init__(self, recordings: Mapping[str, dict | None]) -> None:
        if not recordings:
            raise HarnessError("a replay transport over no recorded verdict answers no question")
        self._recordings = dict(recordings)

    def __call__(self, messages: list[dict]) -> Verdict:
        key = key_for(messages)
        verdict = recorded_verdict(self._recordings, key)
        if verdict is None:
            raise CheckerUnavailable(f"the replay records no usable verdict for {key!r}")
        return verdict


def replay_over_corpus(
    items: Sequence[CorpusItem], recorded: Mapping[str, dict | None] | None = None
) -> RecordedTransport:
    """The shipped replay re-keyed by the question each row puts to the checker.

    A **view**, not a second artifact: no file is written, `harness.load_recorded` is the only
    reader of the one that exists, and the rows are passed through untouched. What changes is the
    key -- the artifact is keyed by corpus id, and a `Checker` transport can only be keyed by
    something the messages determine.

    Both refusals below are the fail-closed direction. A row the replay does not cover is a gap in
    the artifact and raises here, where the id is still in hand to name; taken with `.get` instead
    it becomes a recorded unavailability, and a fail-closed gate then denies the draft for a reason
    that has nothing to do with the draft. A key two rows share would silently drop one of them,
    and the row that lost would be graded against the verdict recorded for the row that won.
    """
    recorded = load_recorded() if recorded is None else recorded
    view: dict[str, dict | None] = {}
    seen: dict[str, str] = {}
    for item in items:
        if item.id not in recorded:
            raise HarnessError(f"the replay artifact holds no verdict for {item.id!r}")
        key = key_for(build_checker_messages(item.draft, item.record))
        if key in seen:
            raise HarnessError(
                f"{item.id!r} and {seen[key]!r} put the same checker question and would share one "
                "recorded verdict"
            )
        seen[key] = item.id
        view[key] = recorded[item.id]
    return RecordedTransport(view)
