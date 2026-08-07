from __future__ import annotations

from typing import Callable, Sequence

from chaperone.matching.filters import Candidate, Eligibility, Mandate, classify
from chaperone.matching.relationship import relationship_score

RELATIONSHIP_WEIGHT = 0.6
EMBEDDING_WEIGHT = 0.4

# Where each eligibility state goes. A table rather than an `if`/`elif` chain, because a chain's
# implicit final branch is a silent drop: a state matching neither arm leaves the candidate in
# neither returned bucket and nothing says so. Here an unrouted state raises on the lookup, and
# `ROUTED_STATES` is what a test holds against `Eligibility` so the gap is caught before an input
# reaches it. "excluded" is a destination like the other two -- named, and then not returned.
_DESTINATION = {
    Eligibility.ELIGIBLE: "ranked",
    Eligibility.NEEDS_VERIFICATION: "needs_verification",
    Eligibility.INELIGIBLE: "excluded",
}
ROUTED_STATES = frozenset(_DESTINATION)


def rank(
    candidates: Sequence[Candidate],
    mandate: Mandate,
    embed_score: Callable[[Candidate], float],
) -> tuple[list[Candidate], list[Candidate]]:
    """Filter, then relationship, then embedding re-rank inside the filtered set. Never retrieval."""
    buckets: dict[str, list[Candidate]] = {name: [] for name in _DESTINATION.values()}
    for candidate in candidates:
        eligibility, _ = classify(candidate, mandate)
        buckets[_DESTINATION[eligibility]].append(candidate)

    ranked = sorted(
        buckets["ranked"],
        key=lambda c: RELATIONSHIP_WEIGHT * relationship_score(c) + EMBEDDING_WEIGHT * embed_score(c),
        reverse=True,
    )
    return ranked, buckets["needs_verification"]
