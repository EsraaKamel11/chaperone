from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, DecimalException, localcontext


class CanonicalizationError(ValueError):
    """Input could not be reduced to a canonical representation."""


_MULTIPLIERS = {"k": Decimal(1_000), "m": Decimal(1_000_000), "b": Decimal(1_000_000_000)}
# Two guards earn their keep here, and neither is decoration:
#   (?![A-Za-z])  a multiplier must not be the first letter of the next word, or "12 March"
#                 reads as twelve million. It sits INSIDE the optional group on purpose: if
#                 it constrained the character after the digits whenever no suffix matched,
#                 "$5MM" would match nothing at all and the figure would be dropped rather
#                 than truncated -- an escape, not a spurious finding.
#   (?(paren)\))  the parentheses must pair, but the two directions are enforced by different
#                 mechanisms. Structural: the conditional lets `paren` participate only when a
#                 ")" was also consumed, so a negative sign can never be built from a lone "("
#                 -- that is what stops "(500" reading as -500. Incidental: "500)" and "(500"
#                 are errors only because fullmatch refuses to leave a character unconsumed.
#                 search() matches a bare "500" out of both, so that half of the guarantee
#                 lives in the caller. Swap fullmatch for search and it evaporates silently.
#   \s*+ / ?+     the four groups before the required \d are possessive, and so is the currency
#                 symbol. Greedy, they made a failing match backtrack across every way of
#                 splitting a run of whitespace between them: 400 leading spaces before a
#                 non-figure took 31 seconds, roughly N**5.6. `figures_in` runs on every draft
#                 body and `evaluate_act_classes` calls it too, so that is a stall in the
#                 boundary engine, and design spec 3.4 makes that engine fail *closed* -- a hang
#                 is neither closed nor open, it is the gate not answering. Possessive is safe
#                 here because every group is disjoint from what follows it: whitespace can
#                 never help `-`, `[$£€]` or `\d` match, so giving any back cannot rescue a
#                 failing attempt. Equivalence was checked exhaustively over 111,111 strings
#                 across the whole alphabet this pattern reads, not sampled. Nothing else is
#                 possessive: `\(?` and the digit groups do need to give back.
_AMOUNT = re.compile(r"(?P<paren>\()?\s*+(?P<sign>-)?\s*+[$£€]?+\s*+(?P<digits>\d[\d,]*(?:\.\d+)?)\s*+(?:(?P<suffix>[kmb])(?![A-Za-z]))?(?(paren)\))", re.IGNORECASE)


def normalize_money(raw: str | int | float | Decimal) -> Decimal:
    if isinstance(raw, Decimal):
        if not raw.is_finite():
            raise CanonicalizationError(f"cannot canonicalize {raw!r}")
        return raw
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, float):
        value = Decimal(str(raw))
        if not value.is_finite():
            raise CanonicalizationError(f"cannot canonicalize {raw!r}")
        return value

    if not isinstance(raw, str):
        raise CanonicalizationError(f"cannot canonicalize {raw!r}")

    text = raw.strip()
    match = _AMOUNT.fullmatch(text)
    if match is None:
        raise CanonicalizationError(f"cannot canonicalize {raw!r}")

    # Both the parse and the scaling sit inside one clause, and the clause names the base class
    # rather than a member of it. Scaling is arithmetic, and arithmetic signals `Overflow`, which
    # descends from `ArithmeticError` rather than `ValueError` -- so it is not a
    # `CanonicalizationError`, and every `except CanonicalizationError` downstream would have
    # waved it through. `DecimalException` is the common ancestor of `Overflow`,
    # `InvalidOperation` and the rest, so no sibling is left to leak the next time this is edited.
    # Every caller may then rely on exactly one escaping exception type.
    suffix = match.group("suffix")
    digits = match.group("digits").replace(",", "")
    try:
        value = Decimal(digits)
        if suffix:
            # Multiplication is the one context operation in this function, and a context
            # operation rounds to `prec` -- 28 by default. A long figure was therefore scaled to
            # a *different number*, and that direction is the dangerous one: a rounded product
            # can land exactly on a record value the draft never stated, match it, and emit no
            # finding at all. A wrong figure that fails to match is only a spurious finding
            # routed to a human; a wrong figure that matches is silence.
            #
            # Widen the precision for the scaling so the product is exact. The multipliers are
            # powers of ten, so a coefficient of n digits grows to at most n + 9, and the digit
            # string is never shorter than its coefficient. `Emax` is not derived from `prec`,
            # so an amount too large to represent still overflows and still raises.
            with localcontext() as ctx:
                ctx.prec = len(digits) + 9
                value *= _MULTIPLIERS[suffix.lower()]
    except DecimalException as exc:
        raise CanonicalizationError(f"cannot canonicalize {raw!r}") from exc

    negative = bool(match.group("sign")) or bool(match.group("paren"))
    # Unary minus is an arithmetic operation: it consults the context and rounds. A negative amount
    # carrying more than the context's 28 significant digits therefore came back as a different
    # magnitude, while the positive spelling of the same digits stayed exact -- returning `value`
    # unchanged performs no operation and so never rounded. In a module whose whole job is sign and
    # magnitude fidelity that asymmetry is the defect, not the length that exposes it.
    # `copy_negate` flips the sign and touches nothing else.
    #
    # The guard keeps the one thing unary minus got right: it maps zero to +0, where `copy_negate`
    # yields -0. They are equal, but `Finding.detail` renders `str()` for a human to read, and
    # "-0.00" is not a thing anyone should be shown.
    if not negative or value.is_zero():
        return value
    return value.copy_negate()


def figures_in(text: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for match in _AMOUNT.finditer(text):
        try:
            found.add(normalize_money(match.group(0).strip()))
        except CanonicalizationError:
            # A multiplier that cannot be applied costs the multiplier, never the figure. An
            # unrecognised spelling never reaches here -- the pattern itself declines to consume
            # the suffix, so "$5MM" arrives as "$5" -- and a multiplier whose product overflows
            # the decimal context is that same situation one step later, arithmetic rather than
            # textual. Same situation, same answer: re-read the digits the drafter actually
            # wrote, sign included, and discard only the multiplier. The alternative is dropping
            # the figure, and a dropped figure is the one outcome that yields no finding at all
            # and lets the draft through.
            #
            # `normalize_money` stays strict and still raises on the same input: it reads a value
            # handed over as a record field, where guessing a magnitude would be a fabrication.
            # This is the lenient half of that split, scanning prose for candidates.
            digits = match.group("digits")
            sign = "-" if match.group("sign") or match.group("paren") else ""
            # Deliberately unguarded. This reconstruction cannot fail: `digits` is `_AMOUNT`'s own
            # group, so `sign + digits` always re-matches it; a digit string converts exactly and
            # signals nothing; and dropping the suffix removes the only arithmetic in the
            # function. Anyone editing `_AMOUNT` must preserve that.
            #
            # A handler here could only `continue`, silently dropping the figure -- the one
            # outcome that yields no finding at all, six lines under a comment saying exactly
            # that. Unreachable code that models the forbidden outcome teaches the next reader
            # the wrong lesson. If a future pattern edit does make this reachable, an exception
            # stops a test run immediately, where a dropped figure would pass one quietly.
            found.add(normalize_money(f"{sign}{digits}"))
    return found


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def arg_digest(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
