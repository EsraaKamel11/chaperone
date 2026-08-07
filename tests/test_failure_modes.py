"""Five failure modes, each traceable to a defect catalogued in design spec 2.1.

These are the review findings the architecture was shaped around, driven end to end rather than at
the layer that owns them. Where a unit test already holds the same property, it is named in the
docstring and this test asserts something the unit test does not -- there is no value in a second
copy of an assertion, and every one of these five is here because the composed path can fail while
each layer passes.

- **A** -- a check that runs is not a check that governs. Held by
  `tests/audit/test_gateway.py::test_a_denied_call_never_references_the_tool_function`, and not
  restated here: it is an assertion about a call that never happens, which composes no further.
- **B** -- append-only enforced by naming. `test_the_gateways_own_log_is_tamper_evident` below.
- **C** -- the audit entry is skipped exactly when it matters most.
  `test_the_audit_entry_survives_a_tool_resolution_failure` below.
- **D** -- durability by text-mode append. Held on the bytes in `tests/audit/test_store.py`, and
  additionally by the `binary_append_becomes_text_append` and `torn_line_terminator_dropped`
  mutants in `tools/mutate.py`. Not restated.
- **E** -- a permissive validator described in strict terms.
  `test_money_parsing_round_trips_signs_and_parenthesised_debits` below.
- **F** -- control-flow relabelling that erases refusals.
  `test_a_refusal_is_not_indistinguishable_from_a_completion` below.

The fifth is design spec 4.6's cross-turn accumulation seen from the side the gate can actually
answer: the per-draft gate must carry no residue between drafts, or turn N's verdict is a function
of turns 1..N-1 and the sequence that lets a violation through is a sequence nobody tested.

**What the brief's version of this file asserted and this one does not.** Its money test compared
`normalize_money("-$500.00")` against `normalize_money(-500)` -- a reference computed by the code
under test, which agrees with itself whatever it does. Its prompt-artefact test is
`tests/gates/test_checker.py::test_the_checker_prompt_has_no_generator_artefacts` verbatim, over
the same two constants, and duplicating it would make two places to update and one to forget.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from chaperone.audit.chain import verify
from chaperone.audit.gateway import Gateway, transmit
from chaperone.audit.store import AuditStore
from chaperone.gates.checker import Checker
from chaperone.gates.engine import decide, denial_result
from chaperone.policy.act_classes import ActContext
from chaperone.policy.canonical import normalize_money
from chaperone.policy.types import Decision, Disposition, Draft, Finding, Message, Record, ViolationClass

RECORD = Record(fields={"round_size": "10000000"})
CONTEXT = ActContext(approval_token="tok", tier=2, consented_jurisdictions=frozenset({"US"}),
                     granted_tools=frozenset({"send_message"}), sent_count=0, send_cap=50)
ALLOW = Decision(allowed=True, findings=(), disposition=Disposition.ALLOW)
DENY = Decision(
    allowed=False,
    findings=(Finding(ViolationClass.ADVISES_ON_MERITS, "merits", "a good deal"),),
    disposition=Disposition.REDIRECT_FUTILE,
)


def _draft(body: str, **overrides) -> Draft:
    base = dict(thread=(Message(role="investor", body="?"),), body=body, cited_fields=(),
                recipient_jurisdiction="US", recipient_domain="example.test", tool_name="send_message")
    base.update(overrides)
    return Draft(**base)


def _gateway(tmp_path: Path) -> Gateway:
    return Gateway(AuditStore(tmp_path / "audit.jsonl"), principal="agent", tier=2)


# --------------------------------------------------------------------------------------------
# Finding C: the audit entry is skipped exactly when it matters most
# --------------------------------------------------------------------------------------------


def test_the_audit_entry_survives_a_tool_resolution_failure(tmp_path: Path):
    """A gateway that resolves its target outside the `try` never logs the call worth logging.

    `tests/audit/test_gateway.py::test_an_outcome_entry_is_written_even_when_the_tool_raises`
    holds the outcome. What is added here is the rest of the record an operator reads after the
    failure: this is the **effectful** path, so the intent must be there too, in order, and the
    chain across the pair must still verify. An outcome written outside the `finally` leaves an
    intent with no outcome -- design spec 5.4's one shape the recovery pass cannot resolve.
    """
    gateway = _gateway(tmp_path)
    with pytest.raises(KeyError):
        transmit(gateway, "missing", {"to": "x"}, decide=lambda: ALLOW, execute=lambda: {}["absent"])

    entries, torn = gateway.store.read_all()
    assert [entry.kind for entry in entries] == ["intent", "outcome"]
    assert entries[-1].outcome == "error"
    assert torn is False
    assert verify(entries, torn_tail=torn).ok is True


# --------------------------------------------------------------------------------------------
# Finding B: append-only enforced by naming
# --------------------------------------------------------------------------------------------


def test_the_gateways_own_log_is_tamper_evident_rather_than_append_only_by_naming(tmp_path: Path):
    """`assert not hasattr(trail, "delete")` establishes that no method carries that name.

    The store's own tamper test edits a line it wrote directly. This one edits the log the
    **gateway** wrote, which is the artifact an operator actually holds, and it flips the field
    that matters: a refusal relabelled as an allow. The relabelled entry is still well-formed JSON
    and still reads back as an `AuditEntry`, so nothing but the chain can tell.
    """
    gateway = _gateway(tmp_path)
    gateway.call("send_message", {"to": "x"}, decide=lambda: DENY, execute=lambda: "sent")
    gateway.call("send_message", {"to": "y"}, decide=lambda: ALLOW, execute=lambda: "sent")
    path = gateway.store._path

    lines = path.read_bytes().rstrip(b"\n").split(b"\n")
    tampered = json.loads(lines[0])
    assert tampered["outcome"] == "redirected", "the entry being relabelled must really be a refusal"
    tampered["outcome"] = "allowed"
    lines[0] = json.dumps(tampered, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.write_bytes(b"\n".join(lines) + b"\n")

    entries, torn = AuditStore(path).read_all()
    assert entries[0].outcome == "allowed", "the edit must survive the read, or nothing was tampered"
    assert torn is False, "a line that parses is an entry, not a tear"
    result = verify(entries, torn_tail=torn)
    assert result.ok is False
    assert result.broken_at == 0


# --------------------------------------------------------------------------------------------
# Finding E: a permissive validator described in strict terms
# --------------------------------------------------------------------------------------------


def test_money_parsing_round_trips_signs_and_parenthesised_debits():
    """Design spec 4.5 is most emphatic about a debit silently turned into a credit.

    Every reference below is a `Decimal` literal, never a second call to `normalize_money`: a
    parser compared against itself agrees with itself whatever it does, and the sign is exactly
    what would be dropped identically on both sides.
    """
    assert normalize_money("-$500.00") == Decimal("-500.00")
    assert normalize_money("($500)") == Decimal("-500")
    # `- $5m`, not `- $5MM`: the suffix is one letter with no letter after it, so "$5MM" is refused
    # outright and goes down `validate_citations`' degraded path. Measured, after this line was
    # first written as "$5MM" and failed for a reason that was not the sign.
    assert normalize_money("- $5m") == Decimal("-5000000")
    assert normalize_money("$500") == Decimal("500")
    # Sign is a property of the value and not of the spelling, so the two negatives above are the
    # one number and neither is the positive.
    assert normalize_money("-$500.00") == normalize_money("($500)") < normalize_money("$500")


def test_a_zero_debit_is_not_rendered_to_a_human_as_minus_zero():
    """`copy_negate` yields -0, and `Finding.detail` renders `str()` for a person to read."""
    assert str(normalize_money("-$0.00")) == "0.00"
    assert normalize_money("-$0.00") == Decimal("0")


# --------------------------------------------------------------------------------------------
# Finding F: control-flow relabelling that erases refusals
# --------------------------------------------------------------------------------------------


def test_a_refusal_is_not_indistinguishable_from_a_completion():
    """A relabelled stop reason makes a truncated reply look like a finished one.

    `tests/gates/test_engine.py::test_checker_unavailability_fails_closed` holds the boolean. What
    is added here is the envelope the agent is actually handed: 3.4's failure is not that the deny
    is lost, it is that the deny arrives wearing "transient -- please retry". So the categorized
    result must say error, must say *not* retryable, and must name the class rather than leaving
    the agent to guess -- and the timeout must not have been swallowed into an allow on the way.
    """
    def times_out(_messages):
        raise TimeoutError("truncated")

    checker = Checker("sonnet-tier", "sonnet-tier", transport=times_out, retries=0)
    decision = decide(_draft("The round is $10M."), RECORD, CONTEXT, checker)

    assert decision.allowed is False
    assert "unavailable" in decision.findings[0].detail
    result = denial_result(decision)
    assert result["is_error"] is True
    assert result["is_retryable"] is False
    assert result["category"] == ViolationClass.OTHER.value
    assert result["disposition"] == Disposition.REDIRECT_FUTILE.value


# --------------------------------------------------------------------------------------------
# Design spec 4.6: the per-draft gate must carry no residue between drafts
# --------------------------------------------------------------------------------------------


def test_a_drafts_verdict_does_not_depend_on_which_drafts_preceded_it():
    """Turn N's answer must be a function of turn N, or the sequence that leaks is untested.

    The brief's version called one pure function twice with identical arguments, which only a
    global could break. This drives `decide` -- the whole disjunction, including the checker
    boundary -- over three drafts that genuinely disagree, then over every ordering of them, and
    holds each draft's answer to the answer it gives alone. Accumulated findings, a mutable
    default, or a cache keyed on anything but the draft all show up here.
    """
    from itertools import permutations

    from chaperone.gates.checker import Verdict

    checker = Checker("sonnet-tier", "sonnet-tier", lambda _m: Verdict(violates=False, confidence=0.9))
    drafts = {
        "clean": _draft("The round is $10M."),
        "unconsented": _draft("The round is $10M.", recipient_jurisdiction="FR"),
        "unbacked": _draft("The round is $42M."),
    }
    alone = {name: decide(d, RECORD, CONTEXT, checker) for name, d in drafts.items()}

    # Non-vacuity: three identical answers would make every ordering below agree for free.
    assert len({(d.allowed, d.disposition, tuple(f.violation_class for f in d.findings))
                for d in alone.values()}) == 3

    for order in permutations(drafts):
        for name in order:
            assert decide(drafts[name], RECORD, CONTEXT, checker) == alone[name], (order, name)
