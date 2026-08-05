from chaperone.gates.checker import Checker, CheckerUnavailable, FlagForReview, Verdict
from chaperone.gates.engine import decide, denial_result, disposition_for
from chaperone.policy.act_classes import ActContext
from chaperone.policy.types import Disposition, Draft, Finding, Message, Record, ViolationClass

RECORD = Record(fields={"round_size": "10000000"})
CONTEXT = ActContext(approval_token="tok", tier=2, consented_jurisdictions=frozenset({"US"}),
                     granted_tools=frozenset({"send_message"}), sent_count=0, send_cap=50)


def _draft(body: str, **overrides) -> Draft:
    base = dict(thread=(Message(role="investor", body="?"),), body=body, cited_fields=(),
                recipient_jurisdiction="US", recipient_domain="example.test", tool_name="send_message")
    base.update(overrides)
    return Draft(**base)


def _checker(result):
    if isinstance(result, Exception):
        def transport(_):
            raise result
    else:
        def transport(_):
            return result
    return Checker("sonnet-tier", "sonnet-tier", transport=transport, retries=0)


CLEAN = Verdict(violates=False, confidence=0.9)


def test_a_clean_draft_with_a_clean_checker_is_allowed():
    decision = decide(_draft("The round is $10M."), RECORD, CONTEXT, _checker(CLEAN))
    assert decision.allowed is True
    assert decision.disposition is Disposition.ALLOW


def test_an_act_class_finding_blocks_without_consulting_the_checker():
    consulted = False

    def transport(_):
        nonlocal consulted
        consulted = True
        return CLEAN

    checker = Checker("sonnet-tier", "sonnet-tier", transport=transport, retries=0)
    decision = decide(_draft("hello", recipient_jurisdiction="DE"), RECORD, CONTEXT, checker)
    assert decision.allowed is False
    assert consulted is False


def test_a_checker_flag_blocks_even_when_no_tripwire_fires():
    verdict = Verdict(violates=True, violation_class=ViolationClass.ADVISES_ON_MERITS,
                      confidence=0.8, span="move quickly")
    decision = decide(_draft("Between us, I would move quickly on this one."), RECORD, CONTEXT, _checker(verdict))
    assert decision.allowed is False


def test_a_tripwire_blocks_even_when_the_checker_says_compliant():
    """The second disjunct. A checker false negative is caught for the detectable slice."""
    decision = decide(_draft("Returns are guaranteed."), RECORD, CONTEXT, _checker(CLEAN))
    assert decision.allowed is False
    assert ViolationClass.FORWARD_LOOKING_RETURN in [f.violation_class for f in decision.findings]


def test_checker_unavailability_fails_closed():
    decision = decide(_draft("The round is $10M."), RECORD, CONTEXT, _checker(TimeoutError("down")))
    assert decision.allowed is False
    assert decision.disposition is Disposition.REDIRECT_FUTILE


def test_flag_for_review_fails_closed_rather_than_passing():
    decision = decide(_draft("The round is $10M."), RECORD, CONTEXT, _checker(FlagForReview(reason="unclear")))
    assert decision.allowed is False


def test_advises_on_merits_is_futile_and_negotiates_terms_is_refinable():
    assert disposition_for((Finding(ViolationClass.ADVISES_ON_MERITS, "m", None),)) is Disposition.REDIRECT_FUTILE
    assert disposition_for((Finding(ViolationClass.NEGOTIATES_TERMS, "n", None),)) is Disposition.REDIRECT_REFINABLE


def test_a_futile_class_wins_when_mixed_with_a_refinable_one():
    findings = (Finding(ViolationClass.NEGOTIATES_TERMS, "n", None),
                Finding(ViolationClass.ADVISES_ON_MERITS, "m", None))
    assert disposition_for(findings) is Disposition.REDIRECT_FUTILE


def test_the_denial_result_is_categorized_and_not_retryable():
    decision = decide(_draft("Returns are guaranteed."), RECORD, CONTEXT, _checker(CLEAN))
    result = denial_result(decision)
    assert result["is_error"] is True
    assert result["is_retryable"] is False
    assert result["category"] == "content:forward_looking_return"


def test_the_denial_result_carries_the_violating_span_verbatim():
    decision = decide(_draft("Returns are guaranteed."), RECORD, CONTEXT, _checker(CLEAN))
    assert "guaranteed" in denial_result(decision)["span"]


def test_disposition_is_derived_from_category_in_one_place():
    """Not per call site. The mapping has exactly one definition."""
    import inspect
    from chaperone.gates import engine
    source = inspect.getsource(engine)
    assert source.count("FUTILE_CLASSES") == 2  # the definition and its single use
