from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation


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
_AMOUNT = re.compile(r"(?P<paren>\()?\s*(?P<sign>-)?\s*[$£€]?\s*(?P<digits>\d[\d,]*(?:\.\d+)?)\s*(?:(?P<suffix>[kmb])(?![A-Za-z]))?(?(paren)\))", re.IGNORECASE)


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

    try:
        value = Decimal(match.group("digits").replace(",", ""))
    except InvalidOperation as exc:
        raise CanonicalizationError(f"cannot canonicalize {raw!r}") from exc

    suffix = match.group("suffix")
    if suffix:
        value *= _MULTIPLIERS[suffix.lower()]

    negative = bool(match.group("sign")) or bool(match.group("paren"))
    return -value if negative else value


def figures_in(text: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for match in _AMOUNT.finditer(text):
        try:
            found.add(normalize_money(match.group(0).strip()))
        except CanonicalizationError:
            continue
    return found


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def arg_digest(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
