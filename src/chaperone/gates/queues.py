"""In-process routing, so that "redirected" is something that happens and not something said.

The gate can decide a draft must go to a human instead of going out. Until this module existed that
decision was a `Disposition` and nothing acted on it: every escalation payload in this tree was
assembled by whatever happened to call the gate, so a caller that never assembled one reached the
same disposition and produced no escalation at all. `guarded_call` now puts the payload in a queue
named by `destination_for`, at the chokepoint, before it returns.

**What this is scoped to, stated so that a reader does not assume the rest.**

- **Routing, in process.** A queue is a list in memory, living as long as the object its caller
  holds. That is enough for the thing being claimed: the redirect is real at the chokepoint, the
  payload exists, it is in a named queue, and a reviewer surface reads it from there instead of
  rebuilding it from whatever the caller still has to hand.
- **The audit log proves a redirect happened.** `Gateway.call` writes an intent and an outcome for
  every guarded call, hash linked, and a denied one carries the `redirected` outcome. So *"this
  draft was stopped and sent for review"* is answerable from the log by itself.
- **The queued payload is not reconstructible from that log.** An audit entry carries the fact and
  a digest, never the text. `Handoff` carries the blocked body, the offending span, the cited
  record values and the thread excerpt, and no audit entry holds any of them. Somebody holding the
  log and not the queue knows a redirect happened and cannot read what was redirected. That split
  is deliberate -- the log is evidence, the queue is the work item -- and it is exactly why losing
  the queue loses the work item rather than merely losing a copy of it.
- **Durable delivery is out of scope, and nothing here delivers anywhere.** There is no inbox, no
  ticket, no process boundary and no persistence. A `ReviewQueues` that goes out of scope takes its
  escalations with it, and a fresh one starts empty. Connecting this to something a human actually
  watches is deployment work that this repository has not done and does not claim to have done.
"""
from __future__ import annotations

from chaperone.gates.handoff import Handoff


class ReviewQueues:
    """Queues by name, created on first use. Nothing here decides which queue a draft belongs in.

    That is `destination_for`'s job, in `src/chaperone/gates/engine.py`, and leaving it there is
    what keeps a queue name from being typed at a call site.
    """

    def __init__(self) -> None:
        self._queues: dict[str, list[Handoff]] = {}

    def put(self, name: str, handoff: Handoff) -> None:
        self._queues.setdefault(name, []).append(handoff)

    def items(self, name: str) -> tuple[Handoff, ...]:
        """Everything routed to `name`, oldest first, and an empty tuple for a queue nothing used.

        A tuple rather than the list, so that reading a queue cannot append to it. An empty answer
        for an unknown name rather than a `KeyError`, because the question a caller is asking is
        "what is waiting here", and no queue and an empty queue are the same answer to it.
        """
        return tuple(self._queues.get(name, ()))

    def all_empty(self) -> bool:
        """Whether nothing was routed anywhere.

        Asked of every queue rather than of a named one. A caller checking that a compliant run
        escalated nothing wants to be told about a queue it did not think to name, which is the one
        a misrouted escalation would be sitting in.
        """
        return not any(self._queues.values())
