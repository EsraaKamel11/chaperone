"""Emits docs/PERTURBATIONS.md: one row per safeguard, contrasting perturbed with unperturbed.

Design spec 10.6. Each row breaks exactly one input and records what the safeguard did beside what
it does when nothing is broken, because a denial with no baseline beside it is not evidence that
anything was detected.

**Three properties this generator has that the document cannot show on its own:**

- **It writes to the repository, never to the process's working directory.** `Path("docs/...")`
  resolves against cwd, so a run from anywhere else refreshes a document nobody reads while the
  committed one goes stale in silence.
- **A row whose two cells agree is refused.** That is the row that documents a safeguard firing
  when no safeguard fired, and it is the one failure a reader of a generated document cannot see.
  The same rule as `tools/coverage_map.py`'s "a run that classified nothing must never report
  clean", applied to a table instead of an exit code.
- **A table with no row is refused.** A header over an empty table reads exactly like a run where
  every safeguard held.

**No cell carries a rate, and that is a constraint rather than an observation.** CLAUDE.md's rule is
that CI asserts invariants only and probabilistic rates are measured and reported, never asserted.
`tests/test_perturbation_log.py` binds this document to this generator byte for byte, so any rate
written here would become an asserted rate. Every cell below is a boolean, an enum value or a count
that pure code decides.

**What a row is and is not.** Each row is one constructed input, so a row says *this input was
handled this way* and never *inputs like this are handled this way*. Nothing here is sampled and no
row is a measurement.
"""
from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Sequence

from chaperone.audit.chain import verify
from chaperone.audit.recovery import counted_sends
from chaperone.audit.store import AuditStore
from chaperone.gates.checker import Checker, Verdict
from chaperone.gates.engine import decide
from chaperone.matching.filters import Candidate, Mandate, classify
from chaperone.policy.act_classes import ActContext
from chaperone.policy.types import Draft, Message, Record

_ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_PATH = _ROOT / "docs" / "PERTURBATIONS.md"

RECORD = Record(fields={"round_size": "10000000"})
CONTEXT = ActContext(approval_token="tok", tier=2, consented_jurisdictions=frozenset({"US"}),
                     granted_tools=frozenset({"send_message"}), sent_count=0, send_cap=50)
CLEAN = Checker("sonnet-tier", "sonnet-tier",
                transport=lambda m: Verdict(violates=False, confidence=0.9), retries=0)


def _unavailable(_messages):
    raise TimeoutError("the checker did not answer")


BROKEN = Checker("sonnet-tier", "sonnet-tier", transport=_unavailable, retries=0)

Row = tuple[str, str, str]


class PerturbationError(ValueError):
    """A perturbation table that cannot be published as written."""


def _draft(body: str) -> Draft:
    return Draft(thread=(Message(role="investor", body="?"),), body=body, cited_fields=(),
                 recipient_jurisdiction="US", recipient_domain="example.test",
                 tool_name="send_message")


def _seeded_store(path: Path) -> AuditStore:
    """Three sends, so the count under perturbation has something to be wrong about."""
    store = AuditStore(path)
    for seq in range(3):
        store.append(dict(seq=seq, kind="intent", tool="send_message", principal="agent", tier=2,
                          scope="tool", outcome="allowed", arg_digest=f"{seq}" * 64, seed=None))
    return store


def _content_gate_rows() -> list[Row]:
    base = decide(_draft("The round is $10M."), RECORD, CONTEXT, CLEAN)
    # No figure in this body on purpose. `decide` returns on an act finding before a tripwire is
    # consulted, so a perturbed body stating a return the record does not back would be answered by
    # the act lane while this row claimed the content gate. Measured: "This will return 3x within
    # four years" published `act:figure_not_in_record` here.
    perturbed = decide(_draft("Returns are guaranteed."), RECORD, CONTEXT, CLEAN)
    down = decide(_draft("The round is $10M."), RECORD, CONTEXT, BROKEN)
    return [
        ("Content gate: a compliant draft mutated into a violating one",
         f"allowed={base.allowed}",
         f"allowed={perturbed.allowed}, {perturbed.findings[0].violation_class.value}"),
        ("Content gate: the checker stops answering",
         f"allowed={base.allowed}",
         f"allowed={down.allowed}, {down.findings[0].violation_class.value}, fails closed"),
    ]


def _matching_row() -> Row:
    mandate = Mandate(check_size_min="5000000", stage="Series A", sector="fintech",
                      geography="US", consented_jurisdictions=frozenset({"US"}))
    full = Candidate(id="c1", check_size_max="25000000", stage="Series A", sector="fintech",
                     geography="US", jurisdiction="US", days_since_touch=30, prior_passes=0)
    blanked = Candidate(id="c1", check_size_max=None, stage="Series A", sector="fintech",
                        geography="US", jurisdiction="US", days_since_touch=30, prior_passes=0)
    eligible, _ = classify(full, mandate)
    unknown, missing = classify(blanked, mandate)
    return ("Matching: one eligibility field blanked",
            eligible.value,
            f"{unknown.value}, names {', '.join(missing)}")


def _audit_rows(directory: Path) -> list[Row]:
    path = directory / "audit.jsonl"
    store = _seeded_store(path)
    entries, torn = store.read_all()
    intact = verify(entries, torn)
    # An edit that keeps the line well-formed JSON and readable as an `AuditEntry`: only the chain
    # can tell, which is the whole point of the chain.
    tampered = list(entries)
    tampered[1] = tampered[1].model_copy(update={"outcome": "denied"})
    broken = verify(tampered, torn)

    whole_count = counted_sends(store)
    # Drop the final newline and part of the last record, which is what a crash mid-append leaves.
    path.write_bytes(path.read_bytes().rstrip(b"\n")[:-20])
    torn_store = AuditStore(path)
    torn_entries, torn_flag = torn_store.read_all()

    return [
        ("Audit chain: one entry edited in place",
         f"ok={intact.ok}, broken_at={intact.broken_at}",
         f"ok={broken.ok}, broken_at={broken.broken_at}"),
        ("Audit log: the last record truncated by a crash",
         f"torn_tail={torn}, {whole_count} sends charged against the cap",
         f"torn_tail={torn_flag}, {counted_sends(torn_store)} sends charged against the cap, "
         f"{len(torn_entries)} records readable"),
    ]


def rows() -> list[Row]:
    """Every perturbation, each one input broken and nothing else."""
    with TemporaryDirectory() as directory:
        audit = _audit_rows(Path(directory))
    return [*_content_gate_rows(), _matching_row(), *audit]


HEADER = (
    "# Perturbation log",
    "",
    "Each row breaks one input and records what the safeguard did, beside unperturbed behaviour.",
    "Generated by `tools/perturbation_log.py`, which is the only thing that should edit this file;",
    "`tests/test_perturbation_log.py` holds the two byte for byte.",
    "",
    "Every cell is a boolean, an enum value or a count that pure code decides. No cell is a rate:",
    "this document is asserted byte for byte, and CI asserts invariants only.",
    "",
    "Each row is one constructed input. A row says that this input was handled this way, and never",
    "that inputs like it are.",
    "",
    "| Perturbation | Unperturbed | Perturbed |",
    "|---|---|---|",
)


def render(table: Sequence[Row]) -> str:
    """The document, or a refusal. Both guards are the fail-closed direction for a document."""
    if not table:
        raise PerturbationError("no perturbation to publish: an empty table reads as a clean run")
    for name, unperturbed, perturbed in table:
        if unperturbed == perturbed:
            raise PerturbationError(
                f"{name!r}: unperturbed and perturbed read alike ({unperturbed!r}), so the row "
                "records a safeguard firing where nothing moved"
            )
    lines = [*HEADER, *(f"| {n} | {before} | {after} |" for n, before, after in table)]
    return "\n".join(lines) + "\n"


def write_perturbation_log(path: Path = DOCUMENT_PATH, table: Sequence[Row] | None = None) -> None:
    """Render first, write second, so a refused table leaves no document behind.

    `newline="\\n"` because Python's text mode translates on Windows and `.gitattributes` pins the
    repository to LF. `tools/record_verdicts.py` writes this way for the same reason: otherwise a
    regeneration on this platform differs from the committed file in every line, and the byte
    equality test that exists to catch drift fails for a reason that is not drift.
    """
    document = render(rows() if table is None else table)
    with Path(path).open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(document)


def main() -> int:
    write_perturbation_log()
    print(f"{DOCUMENT_PATH}: written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
