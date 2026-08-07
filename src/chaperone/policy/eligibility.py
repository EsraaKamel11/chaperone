"""The eligibility predicates both surfaces answer with.

Design spec 8.2: *"the same eligibility code that decides whether a party may be **contacted**
decides whether they may be **surfaced**. An ineligible party is not a low-ranked result; they are
not a result."* The boundary asks it of a draft's recipient and matching asks it of a candidate, so
it lives here, in the package that depends on nothing, and both import it.

One predicate, not two agreeing ones. Task 12 closed this project's worst layer-drift defect on
exactly that distinction: a copy each *is* the drift, and a test binding two copies notices only
the disagreements someone thought to write down.

**What is shared, and what is not.** Jurisdiction consent is shared here, and cheque size is shared
through `normalize_money` in `canonical`. Stage, sector and geography are matching's alone: nothing
at the boundary decides them, and a helper with one caller is indirection rather than sharing. The
spec's sentence is wider than the code can honestly make it, and naming the gap is worth more than
three wrappers that would pretend otherwise.
"""
from __future__ import annotations


def jurisdiction_consented(jurisdiction: str, consented: frozenset[str]) -> bool:
    """Whether `jurisdiction` is one the principal has consented to.

    Membership, exactly: no case folding, no trimming, no aliasing. A spelling that is not in the
    set is not consented, and that includes every spelling nobody enumerated -- there is no branch
    here for an unrecognised input to reach, which is what keeps the permissive answer from being
    the default one. An empty set consents to nothing rather than to everything.

    Canonicalizing the value would belong upstream, where the record is read, and would have to be
    applied to the consent set by the same code on the same call -- doing it here to one side only
    is how "US " comes to mean something different at the two surfaces.
    """
    return jurisdiction in consented
