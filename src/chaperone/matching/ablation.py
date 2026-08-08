"""The matching ablation: hard exclusions against tuned weighted features, on one population.

Design spec 1: *"every comparison arm gets the same engineering effort. A rigged baseline converts
an argument into a demo, and a technical reader spots it immediately."* The weighted arm here is
the baseline this repository argues against, which is exactly why it is the arm most worth
engineering honestly -- an under-built one would make the comparison prove nothing.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import Callable, Mapping, Sequence

from chaperone.matching.filters import ELIGIBILITY_FIELDS, Candidate, Mandate
from chaperone.matching.rank import rank
from chaperone.matching.relationship import relationship_score
from chaperone.policy.canonical import CanonicalizationError, normalize_money
from chaperone.policy.eligibility import jurisdiction_consented


class AblationError(Exception):
    """An ablation that cannot be run, or a measurement that cannot be taken, as asked."""


@dataclass(frozen=True)
class MatchResult:
    """One arm's two rates, each carried beside the denominator it was taken over.

    `contamination` and `recall_loss` are `None` over an empty denominator and never `0.0`.
    `PREREGISTRATION.md` item 5: *a rate over an empty denominator is reported as absent, never as
    zero.* Both directions of that were live here. An arm whose exclusions emptied the shortlist
    reported a contamination of `0.0` -- a perfect score for having surfaced nobody, and measured:
    the brief's own `rate=0.5, seed=11` draw left `n_top_k=0`, so the headline comparison between
    the two arms passed on a hard arm that had examined nothing. And a recall loss taken over
    `min(n_eligible, len(top_k))` divides by whatever the arm happened to return, so an arm that
    surfaced one eligible party out of twenty scored a recall loss of `0.0` -- the same flattering
    zero, aimed at the metric the hard arm is supposed to *pay* in.

    `n_reachable` is `min(n_eligible, k)` and is reported because it is not recoverable from the
    other two counts once an arm returns short.
    """

    arm: str
    contamination: float | None
    recall_loss: float | None
    n_top_k: int
    n_eligible: int
    n_reachable: int


def inject_missingness(candidates: Sequence[Candidate], rate: float, seed: int) -> list[Candidate]:
    """Real records have holes. Each declared eligibility axis is drawn for independently.

    The axes come from `filters.ELIGIBILITY_FIELDS` rather than from a tuple retyped here: an axis
    added to `classify` and forgotten in this population would leave the arm that excludes on it
    untested, and there is nothing to keep two copies of the list in step.
    """
    if not 0.0 <= rate <= 1.0:
        raise AblationError(f"rate {rate!r} is not a probability in [0.0, 1.0]")
    rng = random.Random(seed)
    out = []
    for candidate in candidates:
        updates = {f: None for f in ELIGIBILITY_FIELDS if rng.random() < rate}
        out.append(replace(candidate, **updates) if updates else candidate)
    return out


def hard_filter_arm(
    candidates: Sequence[Candidate],
    mandate: Mandate,
    embed_score: Callable[[Candidate], float],
    k: int,
) -> list[Candidate]:
    """What ships. `rank` excludes, so a party the mandate rules out is not a low result here."""
    ranked, _ = rank(candidates, mandate, embed_score)
    return ranked[:k]


def weighted_feature_arm(
    candidates: Sequence[Candidate],
    mandate: Mandate,
    embed_score: Callable[[Candidate], float],
    k: int,
) -> list[Candidate]:
    """The baseline, engineered to the same standard as the arm it is being compared against.

    Every constraint `classify` excludes on is priced here as a penalty on the same signal the
    shipped ranker uses -- check_size, stage, sector, geography, jurisdiction -- and the shared
    predicates decide the same questions on both arms: `jurisdiction_consented` for consent and
    `normalize_money` for the cheque floor. The arms differ in one thing only, which is the whole
    point of the ablation: a violation costs score here and costs membership there.

    The penalties are ordered by how badly a mismatch misleads a human reading the shortlist, and
    a missing value costs a small amount rather than excluding -- which is the softening this
    baseline exists to represent, and the reason it admits parties the filters do not.

    The five constraints are written out in this body rather than read from a shared table, and
    that is deliberate: `test_the_weighted_arm_is_tuned_not_a_strawman` scans this function's own
    code -- `ast.unparse` with the docstring and the comments dropped -- so hoisting them somewhere
    a reader of this function cannot see turns it red. **That sentence was false when written.**
    The scan read `inspect.getsource`, docstring included, and this paragraph names all five
    constraints; replacing the whole body with `sorted(candidates)[:k]` and keeping these words
    passed. The behavioural companion did the real work throughout, so nothing escaped, but a
    maintainer trusting this note would have trusted a guard that was reading prose.
    """
    def score(c: Candidate) -> float:
        total = 0.6 * relationship_score(c) + 0.4 * embed_score(c)
        if c.jurisdiction is None:
            total -= 0.05
        elif not jurisdiction_consented(c.jurisdiction, mandate.consented_jurisdictions):
            total -= 0.35
        if c.check_size_max is None:
            total -= 0.05
        else:
            try:
                if normalize_money(c.check_size_max) < normalize_money(mandate.check_size_min):
                    total -= 0.30
            except CanonicalizationError:
                total -= 0.05
        for field_name, wanted, weight in (("stage", mandate.stage, 0.25),
                                           ("sector", mandate.sector, 0.25),
                                           ("geography", mandate.geography, 0.20)):
            value = getattr(c, field_name)
            if value is None:
                total -= 0.05
            elif value != wanted:
                total -= weight
        return total

    return sorted(candidates, key=score, reverse=True)[:k]


def _measure(
    arm_name: str,
    top_k: Sequence[Candidate],
    candidates: Sequence[Candidate],
    truth: Mapping[str, bool],
    k: int,
) -> MatchResult:
    """Both rates for one arm, or `None` where the denominator is empty. See `MatchResult`."""
    unlabelled = [c.id for c in candidates if c.id not in truth]
    if unlabelled:
        raise AblationError(
            f"{unlabelled[0]!r} and {len(unlabelled) - 1} other candidates carry no ground-truth "
            "label; a population scored against a partial truth measures the labelled part only"
        )
    n_eligible = sum(1 for c in candidates if truth[c.id])
    n_reachable = min(n_eligible, k)
    surfaced_eligible = sum(1 for c in top_k if truth[c.id])
    contamination = (sum(1 for c in top_k if not truth[c.id]) / len(top_k)) if top_k else None
    # No `max(0.0, ...)` here. `surfaced_eligible <= min(n_eligible, k)` holds by construction, so
    # a clamp could only ever fire on a denominator that had gone wrong -- and would hide it.
    recall_loss = (1.0 - surfaced_eligible / n_reachable) if n_reachable else None
    return MatchResult(
        arm_name,
        None if contamination is None else round(contamination, 6),
        None if recall_loss is None else round(recall_loss, 6),
        len(top_k),
        n_eligible,
        n_reachable,
    )


def run_matching_ablation(
    candidates: Sequence[Candidate],
    mandate: Mandate,
    truth: Mapping[str, bool],
    k: int = 10,
    embed_score: Callable[[Candidate], float] | None = None,
) -> tuple[MatchResult, MatchResult]:
    """Both arms over one population, measured against one ground truth.

    Design spec 8.2 item 5: these are a demonstration of the protocol and never evidence that the
    ranker is good. The labels were generated in this repository, so a ranking metric taken over
    them says what the two architectures do to each other and nothing about the world.
    """
    embed = embed_score or (lambda c: 0.5)
    hard = _measure(
        "hard-exclusions", hard_filter_arm(candidates, mandate, embed, k), candidates, truth, k
    )
    weighted = _measure(
        "weighted-features", weighted_feature_arm(candidates, mandate, embed, k), candidates, truth, k
    )
    return hard, weighted
