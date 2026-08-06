import pytest

from chaperone.gates.checker import Checker, Verdict
from chaperone.gates.engine import decide
from chaperone.policy.act_classes import ActContext, evaluate_act_classes
from chaperone.policy.citations import validate_citations
from chaperone.policy.tripwires import evaluate_tripwires
from chaperone.policy.types import Disposition, Draft, Message, Record, ViolationClass

# `prior_offer` is not decoration and the brief did not carry it. The brief's record held
# `round_size` alone, which leaves the $8M in the negotiation body below unbacked, so
# `evaluate_act_classes` emits `act:figure_not_in_record` and `decide` returns on act findings
# before `evaluate_tripwires` runs. Measured: that parametrization passed `allowed is False` on the
# act class and failed the class assertion, and had it passed both it would still never have
# reached the tripwire the test is named for. The engine's short-circuit is deliberate and pinned
# as a limit in `test_engine.py`; the fixture is what was wrong. Backing the figure rather than
# deleting it keeps the body a negotiation *about a number*, which is the only shape the first
# `NEGOTIATES_TERMS` pattern reads, and demonstrates the act lane satisfied rather than dodged.
RECORD = Record(fields={"round_size": "10000000", "prior_offer": "8000000"})
CONTEXT = ActContext(approval_token="tok", tier=2, consented_jurisdictions=frozenset({"US"}),
                     granted_tools=frozenset({"send_message"}), sent_count=0, send_cap=50)
PERMISSIVE = Checker("sonnet-tier", "sonnet-tier", transport=lambda m: Verdict(violates=False, confidence=0.99), retries=0)


def _draft(body: str) -> Draft:
    return Draft(thread=(Message(role="investor", body="?"),), body=body, cited_fields=(),
                 recipient_jurisdiction="US", recipient_domain="example.test", tool_name="send_message")


@pytest.mark.parametrize("body,expected", [
    ("Honestly, this is a strong deal.", ViolationClass.ADVISES_ON_MERITS),
    ("They would probably accept $8M instead of $10M.", ViolationClass.NEGOTIATES_TERMS),
    ("Returns are guaranteed.", ViolationClass.FORWARD_LOOKING_RETURN),
])
def test_each_content_class_blocks_through_the_tripwire_alone(body, expected):
    decision = decide(_draft(body), RECORD, CONTEXT, PERMISSIVE)
    assert decision.allowed is False
    assert expected in [f.violation_class for f in decision.findings]


def test_merits_is_futile_and_the_other_two_are_refinable():
    assert decide(_draft("Honestly, this is a strong deal."), RECORD, CONTEXT, PERMISSIVE).disposition is Disposition.REDIRECT_FUTILE
    assert decide(_draft("Returns are guaranteed."), RECORD, CONTEXT, PERMISSIVE).disposition is Disposition.REDIRECT_REFINABLE


# --- Guards below. The one is the measurement the brief's test above cannot make, and it is what
# --- caught the brief's own fixture. Watched failing against both mutants it names, applied to the
# --- shipped modules and measured rather than predicted:
# ---   test_the_denial_on_each_content_class_is_the_tripwire_lane_and_nothing_else
# ---       -- `evaluate_tripwires` returning `()`: all seven tests in this file fail, this one on
# ---          the class assertion. That the brief's own test fails too is a property of the fixed
# ---          record, not of the assertion: with the act lane clean there is nothing left to deny
# ---          on, so the two are indistinguishable under *this* mutant and only under it.
# ---       -- the record reverted to the brief's `{"round_size": ...}`: the negotiation row of the
# ---          brief's test above fails on its class assertion having already passed `allowed is
# ---          False`, while this one fails first on the act lane and names the pre-empting class.
# ---          That is the discrimination the `allowed is False` assertion cannot make.


@pytest.mark.parametrize("body,expected", [
    ("Honestly, this is a strong deal.", ViolationClass.ADVISES_ON_MERITS),
    ("They would probably accept $8M instead of $10M.", ViolationClass.NEGOTIATES_TERMS),
    ("Returns are guaranteed.", ViolationClass.FORWARD_LOOKING_RETURN),
])
def test_the_denial_on_each_content_class_is_the_tripwire_lane_and_nothing_else(body, expected):
    """"Through the tripwire alone" is a claim about *which lane denied*, and `allowed is False`
    cannot carry it. Three lanes can deny here and the brief's assertion above cannot tell them
    apart -- which is exactly how its own fixture shipped denying on `act:figure_not_in_record`,
    passing the `allowed` assertion and never reaching the tripwire the test is named for.

    So each of the other two lanes is excluded by effect rather than by inference:

    - **The act lane is empty**, asserted on the real predicates over this record. `decide` returns
      on act findings before the tripwires run, so a non-empty act lane means the tripwire result
      was never consulted whatever the decision says. This assertion is what fails first if anyone
      edits the record or a body back into an unbacked figure.
    - **The checker contributed nothing.** `PERMISSIVE` answers `violates=False`, and a checker
      finding would carry `detail="checker confidence 0.99"` where a tripwire finding carries
      `detail="tripwire"` -- so comparing the details discriminates the two producers, and the
      tuple equality then says the denial is the tripwire's own output with nothing added.

    The tuple equality alone would not be the measurement: `decide` calls `evaluate_tripwires`, so
    on its own it would be comparing the engine against the thing the engine calls. It earns its
    place beside the two exclusions, not instead of them, and the class is asserted against the
    parametrization's own literal rather than against anything either side computed.
    """
    draft = _draft(body)
    act_lane = evaluate_act_classes(draft, RECORD, CONTEXT) + validate_citations(draft, RECORD)
    assert act_lane == (), f"an act finding pre-empts the tripwire: {[f.violation_class.value for f in act_lane]}"

    tripwire_findings = evaluate_tripwires(draft)
    assert [f.violation_class for f in tripwire_findings] == [expected]

    decision = decide(draft, RECORD, CONTEXT, PERMISSIVE)
    assert decision.allowed is False
    assert [f.detail for f in decision.findings] == ["tripwire"]
    assert decision.findings == tripwire_findings
