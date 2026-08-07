"""Design spec 8.2: the filters share predicates with `policy/`.

*"The same eligibility code that decides whether a party may be contacted decides whether they may
be surfaced."* Two surfaces asking the same question is only worth stating if one answer serves
both, so this file holds them together from two directions:

- **behavioural** -- the boundary's verdict and the shortlist's verdict on the same jurisdiction
  value, over a table of values chosen to make two independent implementations disagree;
- **structural** -- both modules call the one predicate. Task 12's lesson is that a copy each *is*
  the drift, so the behavioural half is a regression net over a shared implementation rather than
  the thing that makes the two agree.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from chaperone.matching import filters as filters_module
from chaperone.matching.filters import Candidate, Eligibility, Mandate, classify
from chaperone.policy import act_classes as act_classes_module
from chaperone.policy.act_classes import ActContext, evaluate_act_classes
from chaperone.policy.eligibility import jurisdiction_consented
from chaperone.policy.types import Draft, Message, Record, ViolationClass

CONSENTED = frozenset({"US"})
RECORD = Record(fields={})
CONTEXT = ActContext(approval_token="tok", tier=2, consented_jurisdictions=CONSENTED,
                     granted_tools=frozenset({"send_message"}), sent_count=0, send_cap=50)
MANDATE = Mandate(check_size_min="5000000", stage="Series A", sector="fintech",
                  geography="US", consented_jurisdictions=CONSENTED)

# Values chosen because they are where two independently written membership checks part company:
# case folding, surrounding whitespace, a prefix, a superstring, the empty string, and a full-width
# spelling that is not the ASCII one. An exact-membership implementation says "not consented" to
# every row but the first, and so must both surfaces.
_JURISDICTIONS = ["US", "DE", "us", "Us", " US", "US ", "U", "USA", "", "ＵＳ", "US\n"]


def _draft(jurisdiction: str) -> Draft:
    return Draft(thread=(Message(role="investor", body="?"),), body="Thanks for the note.",
                 cited_fields=(), recipient_jurisdiction=jurisdiction,
                 recipient_domain="example.test", tool_name="send_message")


def _candidate(jurisdiction: str) -> Candidate:
    return Candidate(id="c1", check_size_max="25000000", stage="Series A", sector="fintech",
                     geography="US", jurisdiction=jurisdiction, days_since_touch=30, prior_passes=0)


@pytest.mark.parametrize("jurisdiction", _JURISDICTIONS, ids=repr)
def test_the_jurisdiction_the_boundary_blocks_is_the_one_the_shortlist_refuses_to_surface(jurisdiction):
    """An ineligible party is not a low-ranked result; they are not a result. Whichever way the
    boundary answers for a jurisdiction, matching answers the same way for the same value."""
    blocked_from_contact = ViolationClass.JURISDICTION_NOT_CONSENTED in {
        finding.violation_class for finding in evaluate_act_classes(_draft(jurisdiction), RECORD, CONTEXT)
    }
    refused_a_place_on_the_shortlist = (
        classify(_candidate(jurisdiction), MANDATE)[0] is Eligibility.INELIGIBLE
    )
    assert blocked_from_contact == refused_a_place_on_the_shortlist


@pytest.mark.parametrize("jurisdiction", _JURISDICTIONS, ids=repr)
def test_the_shared_predicate_is_what_both_surfaces_answer_with(jurisdiction):
    """The predicate's own verdict, held against both surfaces, so a table row on which all three
    happen to agree by coincidence cannot stand in for the predicate being the source of the answer."""
    consented = jurisdiction_consented(jurisdiction, CONSENTED)
    assert consented is (
        ViolationClass.JURISDICTION_NOT_CONSENTED not in {
            f.violation_class for f in evaluate_act_classes(_draft(jurisdiction), RECORD, CONTEXT)
        }
    )
    assert consented is (classify(_candidate(jurisdiction), MANDATE)[0] is not Eligibility.INELIGIBLE)


def test_a_jurisdiction_the_mandate_does_not_consent_to_is_excluded_rather_than_left_unverified():
    """The two non-eligible states are distinct: a jurisdiction that is *known* not to be consented
    is an exclusion, never a question routed to a human alongside the genuinely unknown."""
    assert classify(_candidate("DE"), MANDATE) == (Eligibility.INELIGIBLE, ())


@pytest.mark.parametrize("module", [act_classes_module, filters_module],
                         ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_neither_surface_keeps_its_own_copy_of_the_consent_check(module):
    """The structural half, in the shape `tests/test_coverage_map.py` already uses: the behavioural
    table above passes just as happily over two identical copies, and two copies are what drift.
    Re-inlining the membership test in either module drops the call and fails here.

    "Calls", not "is governed by" -- a statically visible reference cannot show that the branch
    around it runs. The behavioural half is what shows that.
    """
    called = {
        node.func.id for node in ast.walk(ast.parse(inspect.getsource(module)))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "jurisdiction_consented" in called


def test_the_predicate_answers_for_an_empty_consent_set_rather_than_waving_it_through():
    """A context consenting to no jurisdiction is a real configuration -- the evals build one. An
    empty set that read as "no restriction" is the fail-open shape this repository keeps finding."""
    assert jurisdiction_consented("US", frozenset()) is False
