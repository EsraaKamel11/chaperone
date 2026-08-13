"""A bound on the gate's wait at the checker transport seam. The mechanism half of deny-on-timeout.

`gates/engine.py` owns the policy half and always has: `decide`'s `CheckerUnavailable` branch is
the one place that turns "no usable answer" into a denial carrying an outage. The half that was
missing is mechanical -- `Checker.check` is a blocking call, and a deadline consulted after it
returns stops no hang, so the bound has to live where the waiting happens. `bounded_transport`
wraps a transport so the wait for each answer is bounded, and expiry raises `CheckerUnavailable`:
the same declaration a transport makes for a recorded unavailability, which `Checker.check`
re-raises untouched rather than spending two more attempts on a question the clock closed. The
retry budget stays what it was, a budget for failing transports, never for silent ones -- a
failure inside the bound is re-raised as itself and costs an attempt exactly as it did before.

**The asymmetry is a decision, stated rather than implied.** A transport that raises its own
`TimeoutError` inside the bound is a failure and is retried on the availability budget; wrapper
expiry is terminal. The alternative -- expiry as a generic exception -- would make the gate's
total wait `(retries + 1) * timeout_seconds`, stacking the two budgets multiplicatively, which is
the composition `gates/binding.py` already refuses for the same pair one seam down. And the bound
covers each transport call *as a whole*: wrapping `pydantic_ai_transport` bounds up to its
`retries + 1` internal model calls in one window, not one window per attempt.

**The wrapper bounds the gate's wait, not the transport's execution.** A blocking call cannot be
cancelled from outside, so an expired wait abandons the in-flight call: the worker thread carries
on, and the transport may still complete, spend its tokens, and have its answer dropped. The
abandoned in-flight call survives the deny. What is guaranteed is only that the gate stops
waiting; a transport holding sockets or locks open past the bound is its caller's residual, each
abandoned call pins a daemon thread and its stack until the transport returns or the process
exits, and the daemon flag is what keeps those threads from blocking interpreter exit.

The bound is an argument with a default, chosen by the caller that constructs the transport and
read from no environment. Nothing under `policy/` imports this module or ever may: that package
runs no clock, so the timing mechanism lives here at the transport seam.
"""
from __future__ import annotations

import math
import threading
from typing import Callable

from chaperone.gates.checker import CheckerResult, CheckerUnavailable

#: How long the gate waits for a checker answer when the caller does not choose. Generous against
#: a real model round trip with the binding's own retries inside it; a caller that knows its
#: transport passes its own bound.
DEFAULT_TIMEOUT_SECONDS = 30.0


def bounded_transport(
    transport: Callable[[list[dict]], CheckerResult],
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Callable[[list[dict]], CheckerResult]:
    """`transport` with the wait for each call bounded by `timeout_seconds`.

    An answer inside the bound is returned as given and an exception inside the bound is re-raised
    as itself -- including a transport-declared `CheckerUnavailable`, so a closed question stays
    closed. Only silence changes: at the bound this raises `CheckerUnavailable`, whose message
    names the bound and states the residual, and survives as `Decision.outage` on the denial
    `decide` builds from it.
    """
    # Both halves matter. A non-positive bound waits for nothing and denies every draft, an
    # outage of the whole gate dressed as policy; a non-finite one is worse, because `inf <= 0`
    # is `False`, so a sign check alone accepts the one value that unbounds the wrapper entirely
    # and leaves the call site reading as covered. NaN fails every comparison the same way.
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError(
            f"timeout_seconds must be a positive finite number, got {timeout_seconds}: a bound "
            "that waits for nothing denies every draft, and one that waits forever is the "
            "unwrapped transport wearing the wrapper's name"
        )

    def bounded(messages: list[dict]) -> CheckerResult:
        # One slot, appended exactly once by the worker. Read after `join` rather than asking the
        # thread whether it is alive: the question is whether an answer arrived inside the bound,
        # and the list is the answer's own arrival, not a proxy for it.
        outcome: list[tuple[bool, object]] = []

        def run() -> None:
            try:
                outcome.append((True, transport(messages)))
            except BaseException as exc:
                outcome.append((False, exc))

        worker = threading.Thread(target=run, name="chaperone-checker-wait", daemon=True)
        worker.start()
        worker.join(timeout_seconds)
        if not outcome:
            raise CheckerUnavailable(
                f"checker transport gave no answer within {timeout_seconds} seconds; the gate "
                "stops waiting and fails closed. The in-flight call was abandoned, not cancelled"
            )
        answered, value = outcome[0]
        if answered:
            return value  # type: ignore[return-value]
        raise value  # type: ignore[misc]

    return bounded
