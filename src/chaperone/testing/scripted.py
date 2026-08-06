"""The scripted adversary: a double for the loop, and an honest account of what it can prove.

Design spec 10.5 wants a model driver that **attempts** the forbidden action rather than a recorded
HTTP response, so that the deny path is asserted deterministically and act-class escape rate becomes
a per-class property test running in CI at zero cost instead of a statistical claim over a corpus.
That is what this is, and the conversion is genuinely worth having.

**Its bound, stated here rather than only in a report.** An independent review of this project put it
exactly: *the scripted adversary attempts what you script; it will never emit an input you did not
write.* So it is not evidence about unanticipated inputs, and no coverage number produced here is a
sample from the distribution a real model would draw from. What follows is therefore an enumeration
of what the committed suite reaches, measured by running `decide` over it rather than recalled:

- **Three of the five act-classes are attempted**: `act:tool_outside_grant`,
  `act:jurisdiction_not_consented`, `act:figure_not_in_record`. `act:no_approval_token` and
  `act:send_cap_exceeded` are **not** attempted, and an `Attempt` cannot express them: both are
  predicates over `ActContext`, which `drive` takes once and shares across every attempt, so varying
  them needs a different caller rather than a different attempt. **So 10.5's conversion of act-class
  escape rate into a per-class property test is three-fifths done, and those are the three.**
- **Two of the three content-classes are attempted**: `content:forward_looking_return` and
  `content:advises_on_merits`, the draft 10.5 names first. `content:negotiates_terms` is not in the
  committed suite. Both denials come from the lexical tripwire table -- a measured detector, not a
  construction -- so nothing here is claimable beyond act-classes.
- **Neither predicate Task 12 added is reachable from here.** `drive` uses one name for both the
  reviewed draft and the call, so `_decide_for`'s tool-identity check cannot fire; and it passes
  `args={}`, for which `unsendable_in` returns `()` by construction, so the argument binding cannot
  fire either. An adversary that cannot attempt an escape says nothing about whether it is closed;
  `demo/day2.py` drives the argument surface that this does not.
- Every committed attempt carries `cited_fields=()`, so `validate_citations` contributes nothing and
  the figure finding arrives from `evaluate_act_classes`'s figures path alone.

The counts above are prose and prose drifts, so
`test_the_scripted_suite_reaches_exactly_the_classes_it_names` asserts the exact class each attempt
reaches. Read the suite as a per-class property test over the classes it names, and as silence about
the rest.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from chaperone.audit.gateway import Gateway, GatewayResult
from chaperone.gates.checker import Checker
from chaperone.gates.hook import guarded_call
from chaperone.policy.act_classes import ActContext
from chaperone.policy.types import Draft, Message, Record


@dataclass(frozen=True)
class Attempt:
    body: str
    tool_name: str
    jurisdiction: str
    cited_fields: tuple[str, ...]


class ScriptedRunner:
    """A double for the loop, not a fake HTTP response.

    Its purpose is to prove enforcement holds regardless of what the model attempts.

    **Regardless of what the model attempts, within the surface an `Attempt` can express.** The
    module docstring enumerates that surface and what falls outside it. The sentence above is 10.5's
    own, and it is true of the checker -- every attempt below is driven past a checker that returns
    a clean verdict, so nothing here depends on the model lane agreeing -- but it is not a claim
    about inputs nobody wrote down.
    """

    def __init__(self, attempts: Sequence[Attempt]) -> None:
        self._attempts = list(attempts)

    def drive(
        self,
        gateway: Gateway,
        record: Record,
        context: ActContext,
        checker: Checker,
        registry: Mapping[str, object],
    ) -> list[GatewayResult]:
        results: list[GatewayResult] = []
        for attempt in self._attempts:
            draft = Draft(
                thread=(Message(role="investor", body="?"),),
                body=attempt.body,
                cited_fields=attempt.cited_fields,
                recipient_jurisdiction=attempt.jurisdiction,
                recipient_domain="example.test",
                tool_name=attempt.tool_name,
            )
            results.append(
                guarded_call(gateway, attempt.tool_name, {}, draft, record, context, checker, registry)
            )
        return results
