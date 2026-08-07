from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from chaperone.policy.canonical import CanonicalizationError, normalize_money
from chaperone.policy.eligibility import jurisdiction_consented

# The eligibility axes, declared once. A caller injecting missingness reads this rather than
# retyping the names, so a field added to `classify` cannot be left out of the population that
# exercises it.
ELIGIBILITY_FIELDS = ("check_size_max", "stage", "sector", "geography", "jurisdiction")


@dataclass(frozen=True)
class Mandate:
    check_size_min: str
    stage: str
    sector: str
    geography: str
    consented_jurisdictions: frozenset[str]


@dataclass(frozen=True)
class Candidate:
    id: str
    check_size_max: str | None
    stage: str | None
    sector: str | None
    geography: str | None
    jurisdiction: str | None
    days_since_touch: int | None
    prior_passes: int


class Eligibility(str, Enum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    NEEDS_VERIFICATION = "needs_verification"


def classify(candidate: Candidate, mandate: Mandate) -> tuple[Eligibility, tuple[str, ...]]:
    """Hard exclusions. A missing field is a distinct state, never a default.

    `NEEDS_VERIFICATION` is a redirect in design spec 4.7's sense -- blocked and routed, not
    ranked low and not dropped -- which is why one vocabulary covers the boundary and this surface.
    """
    # The floor is parsed once, outside the candidate's `try`. Inside it, a mandate nobody can read
    # raises where the candidate's cheque size is being handled, so the handler names
    # `check_size_max` on a candidate whose cheque size is perfectly readable and routes the whole
    # population to needs-verification against a floor that was never established. A mandate that
    # cannot be canonicalized is an error in the mandate, and it fails here rather than becoming
    # per-candidate uncertainty attributed to the wrong field.
    floor = normalize_money(mandate.check_size_min)

    missing: list[str] = []
    ineligible = False

    if candidate.jurisdiction is None:
        missing.append("jurisdiction")
    elif not jurisdiction_consented(candidate.jurisdiction, mandate.consented_jurisdictions):
        ineligible = True

    if candidate.check_size_max is None:
        missing.append("check_size_max")
    else:
        try:
            if normalize_money(candidate.check_size_max) < floor:
                ineligible = True
        except CanonicalizationError:
            missing.append("check_size_max")

    for field_name, wanted in (("stage", mandate.stage), ("sector", mandate.sector),
                               ("geography", mandate.geography)):
        value = getattr(candidate, field_name)
        if value is None:
            missing.append(field_name)
        elif value != wanted:
            ineligible = True

    if ineligible:
        return Eligibility.INELIGIBLE, tuple(missing)
    if missing:
        return Eligibility.NEEDS_VERIFICATION, tuple(missing)
    return Eligibility.ELIGIBLE, ()
