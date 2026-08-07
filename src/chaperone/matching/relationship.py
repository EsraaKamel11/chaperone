from __future__ import annotations

from chaperone.matching.filters import Candidate


def relationship_score(candidate: Candidate) -> float:
    """Recency and depth, from the touchpoint ledger. The signal a bought database cannot have."""
    if candidate.days_since_touch is None:
        return 0.0
    # Both terms are clamped at both ends, and each open end was measured rather than argued.
    # `max(0.0, 1.0 - d/365)` had no ceiling, so a touch date ahead of the clock scored 274.97
    # against a real row's 1.0; `min(0.9, 0.2 * n)` had no floor, so a pass count below zero turned
    # the penalty into a bonus, and the two together reached 3024.70. The filters run before this,
    # so neither readmitted an excluded party -- what it cost was the order of the shortlist, where
    # one corrupt ledger row sat above every genuine relationship.
    recency = min(1.0, max(0.0, 1.0 - candidate.days_since_touch / 365.0))
    penalty = min(0.9, max(0.0, 0.2 * candidate.prior_passes))
    return round(recency * (1.0 - penalty), 6)
