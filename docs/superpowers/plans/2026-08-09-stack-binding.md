# Stack Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the three declared frameworks true in the tree: bind pydantic-ai as the checker's
transport, give the redirect a destination and the reviewer an outage marker, back the Agent SDK
claims with an SDK-free callback plus a demo, and move everything unbound to named optional
extras, so the manifest, the import graph and the README agree at every commit.

**Architecture:** The binding is a transport factory behind the existing `Checker` seam; prompt
assembly, independence tests and the recorded verdicts are untouched. The queue and outage marker
land at the executor chokepoint and in `Decision`/`Handoff`. The SDK callback is a plain async
function in `gates/` that no SDK import touches; only `demo/sdk_hook.py` imports
`claude_agent_sdk`. Spec: `docs/superpowers/specs/2026-08-09-stack-binding-design.md`.

**Tech Stack:** Python 3.11+, pydantic 2.x, pydantic-ai >=2.23 (TestModel/FunctionModel offline),
claude-agent-sdk >=0.2.130 (demo only), pytest.

## Global Constraints

- `corpus/` is read-only. No live model call over any corpus row. Recorded verdicts are never
  re-run.
- After every task: `python tools/report.py` regenerates `docs/RESULTS.md` with **no diff**.
- `policy/` imports no LLM client, no I/O, no clock (`tools/static_audit.py` enforces; the
  denylist includes `pydantic_ai` and `claude_agent_sdk`). Bindings land in `gates/` only.
- The suite stays offline and keyless. The only non-offline path is the demo's live lane
  (Task 13), which is **flag-gated on `--live`, never credential-gated**, is never a test and
  never in CI. A credential gate would skip silently forever on a machine whose SDK rides the
  Claude Code CLI login, and the ledger would record the lane as landed without it ever running.
- **Execution order** (amended after the plan was written; task numbers are stable, only the order
  moves): 1, 2, 3, 4, 5, 6, **8, 9, 10, 7**, 11, 12, then 13 if the timebox holds. Tasks 8 to 10
  consume `build_act_inputs` and the policy predicates and none consumes `ReviewQueues`, so Task 7
  (the plan's largest churn) moves behind the SDK work rather than in front of it. Task 9 Step 3
  and Task 7 both edit the parity test in `tests/gates/test_hook.py`; the edits commute.
- No new probabilistic rate is asserted anywhere. Counts over the frozen corpus are the only
  numeric assertions.
- Reader-facing docs (README, docs/*.md) are em-dash-free. No organisation name anywhere,
  including commit messages. Conventional commit prefixes, no trailers.
- The README's Designed-vs-Built row for the binding flips only in the same commit that adds the
  module path to `DESIGNED_VS_BUILT` in `tests/test_readme_claims.py`.
- Dependency promotions land in the same commit as the first test that imports the promoted
  package, so manifest and import graph never disagree in either direction.
- **Push batching:** Task 1 is never pushed alone. Push only at states where manifest, README
  claims and import graph agree and at least one binding is real (Task 5 is the first such
  boundary).
- Suite time is ~22 minutes on `C:\Users\hp\anaconda3\python.exe`. Run focused tests inside the
  loop; run the full `/verify` gate at each commit boundary. Never kill a running suite: a
  SIGTERM mid-sweep strands a fail-open mutant in `src/` (check `git diff`, not `git status`,
  after any interruption, and read `.pytest_cache/chaperone-mutation-sweep.lock/owner` before
  removing a lock).
- "G-core" names Task 2 (the coupled-pair closures). It lands before the binding so the
  transport is written keyword-only from birth.
- TDD throughout: failing test first, watch it fail, minimal implementation, watch it pass.

---

### Task 1: Manifest truth by declaration (extras and floors)

**Files:**
- Modify: `pyproject.toml`
- Test: none (packaging only; the suite run is the check)

**Interfaces:**
- Produces: optional-dependency groups `ai`, `evals`, `sdk` that later tasks promote from.

- [ ] **Step 1: Move the three unbound dependencies to named extras with verified floors**

Replace the `dependencies` and `[project.optional-dependencies]` blocks:

```toml
dependencies = [
    "pydantic>=2.7",
]

[project.optional-dependencies]
ai = ["pydantic-ai>=2.23"]
evals = ["pydantic-evals>=2.23"]
sdk = ["claude-agent-sdk>=0.2.130"]
dev = ["pytest>=8.0", "hypothesis>=6.100"]
```

- [ ] **Step 2: Run the full suite to prove nothing imported them**

Run: `python -m pytest`
Expected: 1,193 passed (no import of the moved packages exists under `src/`, `tests/`, `tools/`,
`demo/`).

- [ ] **Step 3: Regenerate the results page and confirm no diff**

Run: `python tools/report.py && git diff --exit-code docs/RESULTS.md`
Expected: exit 0.

- [ ] **Step 4: Commit (do not push; see push-batching constraint)**

```bash
git add pyproject.toml
git commit -m "build: move the three unbound frameworks to named optional extras at verified floors"
```

---

### Task 2: G-core, the coupled-pair closures

**Files:**
- Modify: `src/chaperone/gates/checker.py` (add `CONTENT_CLASS_PHRASES`; make
  `build_checker_messages` keyword-only)
- Modify: the twelve call sites: `src/chaperone/gates/checker.py:131`,
  `src/chaperone/testing/recorded.py:112`, `tests/gates/test_checker.py` (7 sites),
  `tests/testing/test_recorded.py` (3 sites)
- Test: `tests/gates/test_checker.py`

**Interfaces:**
- Produces: `CONTENT_CLASS_PHRASES: Mapping[ViolationClass, str]` in `gates/checker.py`;
  `build_checker_messages(*, draft, record)` keyword-only.

- [ ] **Step 1: Write the failing coverage test**

```python
def test_checker_instructions_name_every_content_class_and_nothing_else():
    """Coverage is derived from the enum; the phrases are hand-chosen.

    The instruction text itself is frozen (the recorded verdicts were produced under these
    exact bytes), so the map is checked against it, never composed into it. The count equality
    is what "no other class is described as checkable" operationalizes to.
    """
    from chaperone.gates.checker import CHECKER_INSTRUCTIONS, CONTENT_CLASS_PHRASES
    from chaperone.policy.types import Family, ViolationClass

    content = {m for m in ViolationClass if m.family is Family.CONTENT}
    assert set(CONTENT_CLASS_PHRASES) == content
    for member, phrase in CONTENT_CLASS_PHRASES.items():
        assert phrase in CHECKER_INSTRUCTIONS, member
    assert "three" in CHECKER_INSTRUCTIONS
    assert len(CONTENT_CLASS_PHRASES) == 3
```

- [ ] **Step 2: Run it, watch it fail**

Run: `python -m pytest tests/gates/test_checker.py::test_checker_instructions_name_every_content_class_and_nothing_else`
Expected: FAIL, `ImportError: cannot import name 'CONTENT_CLASS_PHRASES'`.

- [ ] **Step 3: Add the map beside the constant (do not touch the constant's bytes)**

In `gates/checker.py`, directly below `CHECKER_INSTRUCTIONS`. Read the constant first and pick
the three phrases as verbatim substrings of it, one per content class:

```python
#: Hand-chosen verbatim substrings of CHECKER_INSTRUCTIONS, one per content class. Coverage is
#: derived by test: keys equal the Family.CONTENT members both directions, each phrase appears
#: in the instruction text, and the instruction's stated count equals len() of this map. The
#: constant is never recomposed from this map: the recorded verdicts were produced under its
#: exact bytes.
CONTENT_CLASS_PHRASES: Mapping[ViolationClass, str] = MappingProxyType({
    ViolationClass.ADVISES_ON_MERITS: "advising on the merits",
    ViolationClass.NEGOTIATES_TERMS: "negotiating terms",
    ViolationClass.FORWARD_LOOKING_RETURN: "forward-looking return",
})
```

Adjust the three phrase strings to the constant's actual wording if it differs; the test is the
authority.

- [ ] **Step 4: Run it, watch it pass**

Expected: PASS.

- [ ] **Step 5: Write the failing keyword-only test**

```python
def test_build_checker_messages_is_keyword_only():
    """Independence enforced by the signature: a positional channel cannot exist."""
    import inspect
    from chaperone.gates.checker import build_checker_messages

    for name, param in inspect.signature(build_checker_messages).parameters.items():
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, name
```

- [ ] **Step 6: Watch it fail, then make the signature keyword-only and update all 12 call sites**

`def build_checker_messages(*, draft: Draft, record: Record) -> list[dict]:` and change every
caller to `build_checker_messages(draft=..., record=...)`. The emitted messages are
byte-identical, so replay digests survive.

- [ ] **Step 7: Focused run, then commit**

Run: `python -m pytest tests/gates/test_checker.py tests/testing/test_recorded.py`
Expected: all pass.

```bash
git add src/chaperone/gates/checker.py src/chaperone/testing/recorded.py tests/gates/test_checker.py tests/testing/test_recorded.py
git commit -m "feat: bind the checker instructions to the content enum, and close the positional channel"
```

---

### Task 3: The shared predicates and the span rule at the gate

**Files:**
- Modify: `src/chaperone/gates/checker.py` (`unusable_reason`, `span_absent_reason`,
  `_reject_unusable` delegates, `Checker.check` enforces the span rule and gains the terminal
  escape)
- Test: `tests/gates/test_checker.py`, `tests/evals/test_corpus.py` (the 160-row span test)

**Interfaces:**
- Consumes: `CheckerResult = Verdict | FlagForReview` (existing).
- Produces: `unusable_reason(result) -> str | None`; `span_absent_reason(result, body) -> str | None`;
  `Checker.check` raises `CheckerUnavailable` immediately when the transport raises it.

- [ ] **Step 1: Write the failing predicate tests**

```python
def test_unusable_reason_names_the_missing_class_and_passes_flags_through():
    from chaperone.gates.checker import FlagForReview, Verdict, unusable_reason

    bad = Verdict(violates=True, violation_class=None, confidence=0.9, span=None)
    assert unusable_reason(bad) is not None
    ok = Verdict(violates=False, violation_class=None, confidence=0.9, span=None)
    assert unusable_reason(ok) is None
    assert unusable_reason(FlagForReview(reason="unclear")) is None


def test_span_absent_reason_refuses_a_span_not_in_the_body():
    from chaperone.gates.checker import FlagForReview, Verdict, span_absent_reason

    v = Verdict(violates=True, violation_class=ViolationClass.ADVISES_ON_MERITS,
                confidence=0.9, span="great deal")
    assert span_absent_reason(v, "this is a great deal, honestly") is None
    assert span_absent_reason(v, "no such words here") is not None
    assert span_absent_reason(FlagForReview(reason="x"), "anything") is None
```

Match `Verdict`'s real constructor field names against `gates/checker.py:31-39` before running.

- [ ] **Step 2: Watch both fail (ImportError), then implement**

```python
def unusable_reason(result: CheckerResult) -> str | None:
    """One rule, two enforcement points: _reject_unusable here, ModelRetry in the binding."""
    if isinstance(result, Verdict) and result.violates and result.violation_class is None:
        return "verdict reports a violation without naming a class"
    return None


def span_absent_reason(result: CheckerResult, body: str) -> str | None:
    """Gate-side only: the transport seam has no draft, so no binding can feed this back."""
    if isinstance(result, Verdict) and result.span is not None and result.span not in body:
        return f"span {result.span!r} is not a verbatim substring of the draft body"
    return None
```

Rewire `_reject_unusable` to raise `ValueError(reason)` when `unusable_reason(result)` is not
None, keeping its existing type check first. In `Checker.check`, after `_reject_unusable`, add:

```python
reason = span_absent_reason(result, draft.body)
if reason is not None:
    raise ValueError(reason)
```

and add the terminal escape above the bare except:

```python
except CheckerUnavailable:
    raise
except Exception as exc:
    last = exc
```

- [ ] **Step 3: Write the failing 160-row corpus test**

In `tests/evals/test_corpus.py`:

```python
def test_every_recorded_span_is_a_verbatim_substring_of_its_row_body():
    """The span rule is claimed only because this holds over all 160 frozen rows."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    bodies = {}
    with (root / "corpus" / "drafts.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            bodies[row["id"]] = row["body"]
    recorded = json.loads((root / "corpus" / "recorded_verdicts.json").read_text(encoding="utf-8"))
    checked = 0
    for item_id, raw in recorded.items():
        span = (raw or {}).get("span")
        if span is not None:
            assert span in bodies[item_id], item_id
            checked += 1
    assert checked > 0, "a span rule proven over zero spans is not proven"
```

Match the drafts.jsonl field names against the file before running.

- [ ] **Step 4: Write the failing terminal-escape test**

The escape ships in this task, so its test ships here too; leaving it for Task 4 would put
untested behaviour on the fail-closed path if Task 4 slips. The call count is the killer: a
`match=` alone passes today, because the loop re-wraps the last exception with the same message.

```python
def test_a_transport_that_declares_terminal_failure_is_not_retried():
    calls = []

    def declares_terminal(messages):
        calls.append(1)
        raise CheckerUnavailable("declared terminal by the transport")

    checker = Checker("sonnet-tier", "sonnet-tier", declares_terminal, retries=2)
    with pytest.raises(CheckerUnavailable, match="declared terminal"):
        checker.check(DRAFT, RECORD)
    assert len(calls) == 1
```

Run it before adding the escape clause: expected FAIL at `assert len(calls) == 1` with 3, which
is the availability loop swallowing a terminal declaration. Then add the clause and watch it pass.

- [ ] **Step 5: Run all four tests, watch them pass; name the killers**

Killers: deleting the class-check breaks test 1; widening the substring rule breaks test 2;
emptying the corpus loop breaks the `checked > 0` floor; removing the escape clause breaks the
call count in test 4.

- [ ] **Step 6: Commit**

```bash
git add src/chaperone/gates/checker.py tests/gates/test_checker.py tests/evals/test_corpus.py
git commit -m "feat: shared unusable and span predicates, span enforced at the gate, terminal escape above the bare except"
```

---

### Task 4: The pydantic-ai transport binding

**Files:**
- Create: `src/chaperone/gates/binding.py`
- Modify: `pyproject.toml` (promote `pydantic-ai>=2.23` to runtime; this commit contains the
  first importing test)
- Test: `tests/gates/test_binding.py` (new)

**Interfaces:**
- Consumes: `build_checker_messages(*, draft, record)`, `unusable_reason`, `CheckerUnavailable`,
  `Verdict`, `FlagForReview` from `gates/checker.py`.
- Produces: `pydantic_ai_transport(model, *, checker_tier, drafter_tier, retries=2) ->
  Callable[[list[dict]], CheckerResult]`.

- [ ] **Step 1: Write the failing wire-fidelity test**

```python
"""The binding's tests run offline on FunctionModel; nothing here contacts a network."""
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart


def _verdict_call(args: dict):
    return ToolCallPart(tool_name="final_result", args=args)


def test_the_first_wire_request_is_exactly_the_assembled_messages():
    captured = []

    def fn(messages, info: AgentInfo) -> ModelResponse:
        captured.append(messages)
        return ModelResponse(parts=[_verdict_call(
            {"violates": False, "violation_class": None, "confidence": 0.9, "span": None})])

    transport = pydantic_ai_transport(FunctionModel(fn), checker_tier="sonnet-tier",
                                      drafter_tier="sonnet-tier")
    assembled = build_checker_messages(draft=DRAFT, record=RECORD)
    transport(assembled)

    first = captured[0]
    requests = [m for m in first if type(m).__name__ == "ModelRequest"]
    assert len(requests) == 1
    parts = [p for p in requests[0].parts if type(p).__name__ == "UserPromptPart"]
    assert len(parts) == 1
    assert parts[0].content == assembled[0]["content"]
    assert requests[0].instructions is None
```

Use the existing `DRAFT`/`RECORD` fixtures from `tests/gates/test_checker.py` (import or
duplicate the small constructors; read that file first). Adjust the ToolCallPart tool name if
pydantic-ai 2.23 names the output tool differently; the RED run tells you.

- [ ] **Step 2: Watch it fail (module does not exist), then implement the factory**

```python
"""Pydantic AI bound as a Checker transport. Prompt assembly stays in build_checker_messages.

The recorded verdicts predate this module and were not re-run through it; no published rate
was measured through it. Offline tests drive it with FunctionModel and TestModel only.
"""
from __future__ import annotations

from collections.abc import Callable

from pydantic_ai import Agent, ModelRetry
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.usage import UsageLimits

from chaperone.gates.checker import (
    CheckerResult, CheckerUnavailable, FlagForReview, Verdict,
    assert_checker_not_weaker, unusable_reason,
)


def _as_history(messages: list[dict]) -> list[ModelRequest]:
    return [ModelRequest(parts=[UserPromptPart(m["content"])]) for m in messages]


def pydantic_ai_transport(
    model,
    *,
    checker_tier: str,
    drafter_tier: str,
    retries: int = 2,
) -> Callable[[list[dict]], CheckerResult]:
    """Two enforcement points, one predicate: the tier ordering is asserted here and in Checker."""
    assert_checker_not_weaker(checker_tier, drafter_tier)
    agent: Agent = Agent(model, output_type=Verdict | FlagForReview, retries=retries)

    @agent.output_validator
    def _semantic(output: CheckerResult) -> CheckerResult:
        reason = unusable_reason(output)
        if reason is not None:
            raise ModelRetry(reason)
        return output

    limits = UsageLimits(request_limit=retries + 1)

    def transport(messages: list[dict]) -> CheckerResult:
        try:
            run = agent.run_sync(user_prompt=None, message_history=_as_history(messages),
                                 usage_limits=limits)
        except (UnexpectedModelBehavior, UsageLimitExceeded) as exc:
            raise CheckerUnavailable(str(exc)) from exc
        return run.output

    return transport
```

If any imported name differs on the installed 2.23 surface, the RED run names it; fix the import,
never the design.

- [ ] **Step 3: Write the failing budget test (N+1, not multiplied)**

```python
def test_validation_exhaustion_is_terminal_and_never_multiplied():
    calls = []

    def always_unusable(messages, info: AgentInfo) -> ModelResponse:
        calls.append(1)
        return ModelResponse(parts=[_verdict_call(
            {"violates": True, "violation_class": None, "confidence": 0.9, "span": None})])

    transport = pydantic_ai_transport(FunctionModel(always_unusable),
                                      checker_tier="sonnet-tier", drafter_tier="sonnet-tier",
                                      retries=2)
    checker = Checker("sonnet-tier", "sonnet-tier", transport, retries=2)
    with pytest.raises(CheckerUnavailable):
        checker.check(DRAFT, RECORD)
    assert len(calls) == 3  # N+1 with N=2; never (R+1)*(N+1)=9
```

- [ ] **Step 4: Watch it fail correctly, then pass**

If it fails with 9 calls, the terminal escape from Task 3 is missing; that is the exact defect
this test exists to catch. Expected after Task 3: PASS with 3.

- [ ] **Step 5: Write the feedback-arrives test (the effect, not the invocation)**

```python
def test_the_retry_request_carries_the_validation_error_text():
    seen = []

    def once_bad_then_good(messages, info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        if len(seen) == 1:
            return ModelResponse(parts=[_verdict_call(
                {"violates": True, "violation_class": None, "confidence": 0.9, "span": None})])
        return ModelResponse(parts=[_verdict_call(
            {"violates": False, "violation_class": None, "confidence": 0.9, "span": None})])

    transport = pydantic_ai_transport(FunctionModel(once_bad_then_good),
                                      checker_tier="sonnet-tier", drafter_tier="sonnet-tier")
    result = transport(build_checker_messages(draft=DRAFT, record=RECORD))
    assert isinstance(result, Verdict) and result.violates is False
    flat = str(seen[1])
    assert "without naming a class" in flat
```

- [ ] **Step 6: Full binding test file green, promote the dependency in the same commit**

In `pyproject.toml`: move `"pydantic-ai>=2.23"` from the `ai` extra into `dependencies`; delete
the now-empty `ai` group.

Run: `python -m pytest tests/gates/test_binding.py tests/gates/test_checker.py`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/chaperone/gates/binding.py tests/gates/test_binding.py pyproject.toml
git commit -m "feat: pydantic-ai bound as a checker transport with feedback retry and a structural call budget"
```

---

### Task 5: The README row flips with the registry entry

**Files:**
- Modify: `README.md` (the Designed-vs-Built row; the recorded-verdicts sentence)
- Modify: `tests/test_readme_claims.py` (`DESIGNED_VS_BUILT` gains
  `"src/chaperone/gates/binding.py": "Pydantic AI binding"`)
- Test: `tests/test_readme_claims.py`

**Interfaces:**
- Consumes: Task 4's module path.

- [ ] **Step 1: Add the registry entry and watch the both-directions test fail**

Add to `DESIGNED_VS_BUILT` in `tests/test_readme_claims.py`:

```python
"src/chaperone/gates/binding.py": "Pydantic AI binding",
```

Run: `python -m pytest tests/test_readme_claims.py -k designed_versus_built`
Expected: FAIL (the table still says Designed, not built; the path now exists). This failure is
the guard working.

- [ ] **Step 2: Flip the row and add the sentence**

In `README.md`, change the row to `| Pydantic AI binding for the checker | **Built.** ... |` and
append, in the same cell or the adjacent Limits paragraph, exactly:

> The 160 recorded verdicts predate the binding and were not re-run through it; the binding is
> exercised offline against FunctionModel and TestModel, and no published rate was measured
> through it.

- [ ] **Step 3: Full readme-claims suite green, then commit and push (first sendable boundary)**

Run: `python -m pytest tests/test_readme_claims.py`
Expected: all pass.

```bash
git add README.md tests/test_readme_claims.py
git commit -m "docs: the binding row flips in the same commit as its registry entry"
git push
```

---

### Task 6: Decision.outage and Handoff.detector_outage

**Files:**
- Modify: `src/chaperone/policy/types.py:76-80` (Decision gains `outage: str | None = None`)
- Modify: `src/chaperone/gates/engine.py` (the `CheckerUnavailable` branch sets it)
- Modify: `src/chaperone/gates/handoff.py` (`Handoff` gains required `detector_outage`;
  `build_handoff` copies it)
- Modify: `tools/mutate.py` (re-derive the `checker_unavailable_fails_open` anchor)
- Test: `tests/gates/test_handoff.py`, `tests/gates/test_engine.py`

**Interfaces:**
- Produces: `Decision.outage: str | None` (default None); `Handoff.detector_outage: str | None`
  (required, no default).

- [ ] **Step 1: Write the failing effect pair**

```python
def test_an_unavailable_checker_names_the_outage_on_the_decision():
    broken = Checker("sonnet-tier", "sonnet-tier",
                     transport=lambda m: (_ for _ in ()).throw(TimeoutError("down")), retries=0)
    decision = decide(DRAFT, RECORD, CONTEXT, broken)
    assert decision.allowed is False
    assert decision.outage is not None and "down" in decision.outage


def test_a_judged_violation_carries_no_outage():
    judging = Checker("sonnet-tier", "sonnet-tier",
                      transport=lambda m: Verdict(violates=True,
                                                  violation_class=ViolationClass.ADVISES_ON_MERITS,
                                                  confidence=0.9, span=None), retries=0)
    decision = decide(VIOLATING_DRAFT, RECORD, CONTEXT, judging)
    assert decision.allowed is False
    assert decision.outage is None
```

Use the existing fixtures in `tests/gates/test_engine.py`; match constructor shapes there.

- [ ] **Step 2: Watch both fail (`Decision` has no field `outage`), then implement**

`policy/types.py`: add `outage: str | None = None` as the fourth field of `Decision`.
`gates/engine.py`, the `except CheckerUnavailable as exc:` branch: construct the decision with
`outage=str(exc)`. Every other construction site is untouched (three positional fields plus the
default).

- [ ] **Step 3: Re-derive the mutation anchor in the same change**

`tools/mutate.py`'s `checker_unavailable_fails_open` mutant anchors on the exact source line of
that branch. Update the anchor string to the new line (per the precedent recorded in
`mutate.py`'s own comments), then run the anchor audit:

Run: `python -m pytest tests/test_mutations.py -k anchors`
Expected: PASS (`audit_anchors() == []`).

- [ ] **Step 4: Write the failing Handoff test, then wire `detector_outage`**

```python
def test_the_handoff_distinguishes_outage_from_judgment():
    outage_handoff = build_handoff(decision=OUTAGE_DECISION, draft=DRAFT, record=RECORD,
                                   proposed_alternative=None, refinement_rounds=0)
    assert outage_handoff.detector_outage is not None
    judged_handoff = build_handoff(decision=JUDGED_DECISION, draft=DRAFT, record=RECORD,
                                   proposed_alternative=None, refinement_rounds=0)
    assert judged_handoff.detector_outage is None
```

Match `build_handoff`'s real parameter list at `gates/handoff.py:42` before writing; add
`detector_outage: str | None` to `Handoff` as a required field (no default) and populate it from
`decision.outage` inside `build_handoff`. The existing drop-each-field test picks the new field
up automatically because it iterates `Handoff.model_fields`.

- [ ] **Step 5: Focused runs, then commit**

Run: `python -m pytest tests/gates/test_engine.py tests/gates/test_handoff.py tests/test_mutations.py -k "anchors or outage or handoff"`
Expected: all pass.

```bash
git add src/chaperone/policy/types.py src/chaperone/gates/engine.py src/chaperone/gates/handoff.py tools/mutate.py tests/gates/test_engine.py tests/gates/test_handoff.py
git commit -m "feat: a structured outage field from decision to handoff, so a reviewer can tell judgment from downtime"
```

---

### Task 7: ReviewQueues and the routed redirect

**Files:**
- Create: `src/chaperone/gates/queues.py`
- Modify: `src/chaperone/gates/engine.py` (`destination_for` beside `disposition_for`)
- Modify: `src/chaperone/gates/hook.py` (`guarded_call` gains required keyword-only `queues`)
- Modify: all ~21 `guarded_call` call sites (18 in `tests/gates/`, `demo/day2.py`,
  `demo/full.py`, `src/chaperone/testing/scripted.py`)
- Modify: `demo/day2.py`, `demo/full.py` (read the queue instead of building handoffs directly)
- Test: `tests/gates/test_hook.py`, `tests/gates/test_engine.py`

**Interfaces:**
- Produces: `ReviewQueues` with `put(name: str, handoff: Handoff) -> None`,
  `items(name: str) -> tuple[Handoff, ...]`, `all_empty() -> bool`;
  `destination_for(disposition: Disposition) -> str | None`;
  `guarded_call(..., *, queues: ReviewQueues)`.

- [ ] **Step 1: Write the failing destination test**

```python
def test_the_destination_is_derived_in_one_place():
    from chaperone.gates.engine import destination_for
    from chaperone.policy.types import Disposition

    routed = {d: destination_for(d) for d in Disposition}
    assert routed[Disposition.REDIRECT_FUTILE] == "human-review"
    assert routed[Disposition.REDIRECT_REFINABLE] == "human-review"
    assert all(dest is None for d, dest in routed.items()
               if d not in (Disposition.REDIRECT_FUTILE, Disposition.REDIRECT_REFINABLE))
```

Enumerate `Disposition`'s real members from `policy/types.py` first and adjust the exclusion set.

- [ ] **Step 2: Watch it fail, implement `destination_for` and `ReviewQueues`**

```python
# gates/engine.py, beside disposition_for
def destination_for(disposition: Disposition) -> str | None:
    """One derivation point. No call site may name a queue literal."""
    if disposition in (Disposition.REDIRECT_FUTILE, Disposition.REDIRECT_REFINABLE):
        return "human-review"
    return None
```

```python
# gates/queues.py
"""In-process routing at the chokepoint. The audit log proves a redirect happened; the queued
payload is not reconstructible from the log, and durable delivery is deployment wiring."""
from __future__ import annotations

from chaperone.gates.handoff import Handoff


class ReviewQueues:
    def __init__(self) -> None:
        self._queues: dict[str, list[Handoff]] = {}

    def put(self, name: str, handoff: Handoff) -> None:
        self._queues.setdefault(name, []).append(handoff)

    def items(self, name: str) -> tuple[Handoff, ...]:
        return tuple(self._queues.get(name, ()))

    def all_empty(self) -> bool:
        return not any(self._queues.values())
```

- [ ] **Step 3: Write the failing routed-denial effect tests**

```python
def test_a_denied_send_lands_in_the_named_queue():
    queues = ReviewQueues()
    result = guarded_call(gateway, "send_message", ARGS, VIOLATING_DRAFT, RECORD, CONTEXT,
                          judging_checker, registry, queues=queues)
    assert result.is_error
    queued = queues.items("human-review")
    assert len(queued) == 1
    assert queued[0].blocked_body == VIOLATING_DRAFT.body


def test_a_compliant_run_leaves_every_queue_empty():
    queues = ReviewQueues()
    guarded_call(gateway, "send_message", ARGS, COMPLIANT_DRAFT, RECORD, CONTEXT,
                 passing_checker, registry, queues=queues)
    assert queues.all_empty()
```

Match fixture names to `tests/gates/test_hook.py`'s existing ones.

- [ ] **Step 4: Watch them fail, then wire `guarded_call`**

Add the required keyword-only parameter and the enqueue on the deny path:

```python
def guarded_call(
    gateway: Gateway,
    tool_name: str,
    args: dict,
    draft: Draft,
    record: Record,
    context: ActContext,
    checker: Checker,
    registry: Mapping[str, object],
    *,
    queues: ReviewQueues,
) -> GatewayResult:
    result = gateway.call(
        tool_name, args,
        decide=lambda: _decide_for(tool_name, args, draft, record, context, checker),
        execute=lambda: registry[tool_name](**args),
        effectful=True,
    )
    decision = result.decision
    if decision is not None and not decision.allowed:
        destination = destination_for(decision.disposition)
        if destination is not None:
            queues.put(destination, build_handoff(
                decision=decision, draft=draft, record=record,
                proposed_alternative=None, refinement_rounds=0))
    return result
```

Match `build_handoff`'s real parameters. Update every call site mechanically
(`queues=ReviewQueues()` in tests that do not assert on it; a shared instance where they do).
Convert `demo/day2.py` and `demo/full.py` to read the queue for their reviewer surface instead
of calling `build_handoff` directly, so a denial produces one artifact; both demos are CI gates
and a second artifact is a red gate.

- [ ] **Step 5: Full gates suite plus both demos, then commit**

Run: `python -m pytest tests/gates/ && python demo/day2.py && python demo/full.py`
Expected: all pass; both demos print their scenes.

```bash
git add src/chaperone/gates/queues.py src/chaperone/gates/engine.py src/chaperone/gates/hook.py src/chaperone/testing/scripted.py demo/day2.py demo/full.py tests/gates/
git commit -m "feat: a denial routes its handoff to a named review queue at the chokepoint"
```

---

### Task 8: The payload adapter extraction

**Files:**
- Create: `src/chaperone/policy/payload.py`
- Modify: `tools/policy_hook.py` (imports from the adapter; re-exports `UNENFORCEABLE_HERE`,
  `CONSUMED_KEYS`)
- Modify: `tests/gates/test_hook.py` (the two AST-derivation tests repoint to
  `policy/payload.py`)
- Test: `tests/gates/test_hook.py`

**Interfaces:**
- Produces: `build_act_inputs(payload: dict) -> tuple[Draft, Record, ActContext]`,
  `CONSUMED_KEYS: frozenset[str]`, `UNENFORCEABLE_HERE: frozenset[ViolationClass]` in
  `policy/payload.py`; `policy_hook` re-exports all three.

- [ ] **Step 1: Move the builder verbatim**

Extract the payload-to-triple construction from `tools/policy_hook.py` (the block building
`Draft`, `Record(fields=...)` and `ActContext(...)` from `tool_input`, plus `CONSUMED_KEYS` and
`UNENFORCEABLE_HERE`) into `policy/payload.py` as `build_act_inputs(payload)`. Keep every trap
byte-for-byte: absent body is not empty body; payload-level `tool_name` wins; no default approval
token; `tier` defaults to 2; unconsumed keys are refused. `policy_hook.py` imports and re-exports
the three names so its public surface is unchanged.

- [ ] **Step 2: Watch the two derivation tests fail, then repoint them**

Run: `python -m pytest tests/gates/test_hook.py -k "deriv or consumed or unenforceable"`
Expected: FAIL, the AST walks find no `tool_input` reads in `policy_hook.py`. This failure is
the guard noticing the move; the guard is right, and the fix is repointing, not weakening.

Repoint both tests to parse `src/chaperone/policy/payload.py` (its `payload` parameter is the
taint root), keeping their anti-vacuity floors (`assert read, ...` and the evidence-predicate
floor) intact.

- [ ] **Step 3: Adapter trap tests (one effect assertion per documented trap)**

Write five separate tests against the extracted builder. The existing policy-hook tests in
`tests/gates/test_hook.py` are the authority on each trap's expected outcome; these four are the
shape (write the fifth, the absent-body trap, by reading how `policy_hook` distinguishes a
missing `body` key from an empty string and asserting that exact behaviour on the adapter):

```python
def test_the_payload_level_tool_name_wins():
    draft, _, _ = build_act_inputs(
        {"tool_name": "outer", "tool_input": {"tool_name": "inner", "body": "x"}})
    assert draft.tool_name == "outer"


def test_no_approval_token_is_invented():
    _, _, ctx = build_act_inputs({"tool_input": {"body": "x"}})
    assert ctx.approval_token is None


def test_tier_defaults_closed_to_two():
    _, _, ctx = build_act_inputs({"tool_input": {"body": "x"}})
    assert ctx.tier == 2


def test_an_unconsumed_key_is_refused_not_ignored():
    with pytest.raises(ValueError, match="surprise_key"):
        build_act_inputs({"tool_input": {"body": "x", "surprise_key": "y"}})
```

If the builder reports unconsumed keys through a finding rather than a raise, mirror the
policy-hook behaviour exactly and assert that finding instead; the move is verbatim, so the
adapter must do whatever `policy_hook` did, and the RED run arbitrates.

- [ ] **Step 4: Full hook suite green, then commit**

Run: `python -m pytest tests/gates/test_hook.py`
Expected: all pass, including the untouched equality assertions.

```bash
git add src/chaperone/policy/payload.py tools/policy_hook.py tests/gates/test_hook.py
git commit -m "refactor: one payload adapter, two enforcement layers, with the derivation tests repointed to its source"
```

---

### Task 9: The SDK-free callback, the fourth decision surface

**Files:**
- Create: `src/chaperone/gates/sdk_callback.py`
- Test: `tests/gates/test_sdk_callback.py` (new)

**Interfaces:**
- Consumes: `build_act_inputs` from `policy/payload.py`; `evaluate_act_classes`,
  `validate_citations`, `evaluate_tripwires` (the same predicates `policy_hook` runs).
- Produces: `async def pre_tool_use_deny(input_data: dict, tool_use_id, context) -> dict`
  returning `{}` on allow, or the deny dict on violation.

- [ ] **Step 1: Write the failing callback tests (offline, plain async function)**

```python
import asyncio


def test_a_violating_payload_is_denied_with_the_category_in_the_reason():
    out = asyncio.run(pre_tool_use_deny(
        {"tool_name": "send_message",
         "tool_input": {"body": "The round is $8M.", "record": {"round_size": "10000000"},
                        "cited_fields": ["round_size"]}},
        None, None))
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "deny"
    assert "act:figure_not_in_record" in spec["permissionDecisionReason"]


def test_a_compliant_payload_returns_an_empty_allow():
    out = asyncio.run(pre_tool_use_deny(
        {"tool_name": "send_message",
         "tool_input": {"body": "The round is $10M.", "record": {"round_size": "10000000"},
                        "cited_fields": ["round_size"]}},
        None, None))
    assert out == {}
```

- [ ] **Step 2: Watch them fail, then implement (no SDK import anywhere in this module)**

```python
"""The in-process PreToolUse callback: payload in, deny-dict out. Imports no SDK.

The deny shape is the Agent SDK's documented HookJSONOutput contract, produced here as plain
JSON so the static audit can read the gate it is asked to trust. The deny surface is the
content classes (via tripwires) plus the declared-enforceable act classes; the same payload
trust boundary as tools/policy_hook.py, carried by the shared adapter.
"""
from __future__ import annotations

from chaperone.policy.act_classes import evaluate_act_classes
from chaperone.policy.citations import validate_citations
from chaperone.policy.payload import UNENFORCEABLE_HERE, build_act_inputs
from chaperone.policy.tripwires import evaluate_tripwires


async def pre_tool_use_deny(input_data: dict, tool_use_id, context) -> dict:
    draft, record, act_context = build_act_inputs(input_data)
    findings = (
        tuple(evaluate_act_classes(draft, record, act_context))
        + tuple(validate_citations(draft, record))
        + tuple(evaluate_tripwires(draft))
    )
    findings = tuple(f for f in findings if f.violation_class not in UNENFORCEABLE_HERE)
    if not findings:
        return {}
    first = findings[0]
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": f"{first.violation_class.value}: {first.detail}",
    }}
```

Match the predicate call signatures against `tools/policy_hook.py`'s decision block; it is the
authority on order and arguments.

- [ ] **Step 3: Extend the three-layer parity tests to the fourth surface**

Find the existing parity tests in `tests/gates/test_hook.py` (`test_the_same_policy_denies_at
_all_three_layers` and the category-parity test) and extend each to feed the callback the same
payload, asserting the same category string appears in the callback's reason. Offline; the
callback is a plain function.

- [ ] **Step 4: Green, then commit**

Run: `python -m pytest tests/gates/test_sdk_callback.py tests/gates/test_hook.py`
Expected: all pass.

```bash
git add src/chaperone/gates/sdk_callback.py tests/gates/test_sdk_callback.py tests/gates/test_hook.py
git commit -m "feat: the in-process deny callback as a fourth decision surface, SDK-free and parity-pinned"
```

---

### Task 10: The demo wiring and the B-core claim

**Files:**
- Create: `demo/sdk_hook.py`
- Modify: `README.md` (the claim text from spec section 7.3, final sentence included now that
  the demo exists)
- Test: `tests/test_readme_claims.py` (path guard covers the new citation automatically)

**Interfaces:**
- Consumes: `pre_tool_use_deny` from `gates/sdk_callback.py`.

- [ ] **Step 1: Write the demo (the only module importing claude_agent_sdk)**

```python
"""Offline-checkable wiring for the in-process hook; the live lane is Task 13.

Pinned against claude-agent-sdk 0.2.130. Imported by no test: CI never touches the SDK.
Run `pip install .[sdk]` first; then `python demo/sdk_hook.py --show-wiring` prints the
HookMatcher registration without connecting anywhere.
"""
from __future__ import annotations

import sys

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

from chaperone.gates.sdk_callback import pre_tool_use_deny


def build_options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        setting_sources=[],  # isolation: no filesystem hook can contribute a deny
        hooks={"PreToolUse": [HookMatcher(matcher="send_message", hooks=[pre_tool_use_deny])]},
    )


if __name__ == "__main__":
    if "--show-wiring" in sys.argv:
        options = build_options()
        print("PreToolUse matchers:", [m.matcher for m in options.hooks["PreToolUse"]])
        print("setting_sources:", options.setting_sources)
        sys.exit(0)
    print("live lane not built in this task; see the plan's Task 13")
    sys.exit(0)
```

- [ ] **Step 2: Verify it imports and runs only with the extra installed**

Run: `python demo/sdk_hook.py --show-wiring`
Expected: prints the matcher list and `setting_sources: []` (the suite interpreter has the SDK
installed; a clean clone without `[sdk]` fails the import, which is the extras contract working).

- [ ] **Step 2b: Price what the new demo file trips, before writing any README text**

`tests/test_readme_claims.py` walks `demo/*.py` in six places: `_tracked(... "demo/*.py")` at
line 223 and rglob sweeps at 540, 765, 822, 904 and 1259. The file's own comments at 534-537 and
776-780 record `demo/full.py` breaking these audits when it landed, so this is the second time,
not a hypothetical.

Run: `python -m pytest tests/test_readme_claims.py`
Expected: unknown. Whatever fires is priced and fixed here, before a single README sentence is
written. Do not write README text against a red claim suite.

- [ ] **Step 3: Land the README claim text (spec 7.3, verbatim, em-dash-free)**

Add the blockquote claim from spec section 7.3 to the README's Agent SDK subsection, including
its final sentence, since `demo/sdk_hook.py` now exists and the path guard verifies it.

- [ ] **Step 4: Readme-claims suite green, then commit and push (sendable boundary)**

Run: `python -m pytest tests/test_readme_claims.py`
Expected: all pass.

```bash
git add demo/sdk_hook.py README.md
git commit -m "feat: the SDK demo wires the deny callback under isolation mode, offline-checkable"
git push
```

---

### Task 11: Contract documentation and the corrections

**Files:**
- Modify: `README.md` (policy_hook contract subsection; Limits gains the four delta sentences
  plus the evals-adapter sentence; naming discipline)
- Modify: `docs/architecture.md` (section 2 correction; the guardrail comparison with
  provenance)
- Test: `tests/test_readme_claims.py` (the existing guards must stay green; no new test)

**Interfaces:** none (documentation only; every claim names an existing test or file).

- [ ] **Step 1: The policy_hook contract subsection (README, uncalled-layers register)**

State: `tools/policy_hook.py` implements the out-of-process PreToolUse command-hook contract
(JSON on stdin, exit code 2 blocks, reason on stderr), exercised from `tests/gates/test_hook.py`
and wired to no runtime; the wired hooks are the edit-time purity guard and the Stop-time secret
scan, which enforce this repository's development rules, not the outbound policy. Every sentence
names which contract it means.

Three further items belong in this step. First, spec section 4 requires one README sentence
recording that `build_checker_messages` became keyword-only, because `testing/recorded.py` is a
replay surface external callers key against; Task 2 made the change and no task owned the
sentence until this one. Second, spec section 2 requires the queue's scope stated in
**both** the module docstring and the README; Task 7 writes the docstring, this step writes the
README sentence: in-process routing that makes the redirect real at the chokepoint, with the
audit log proving a redirect happened, the queued payload not reconstructible from it, and
durable delivery to an external inbox out of scope. Second, decide and record whether
`pre_tool_use_deny` joins `ENFORCEMENT_ROSTER` in `tests/test_readme_claims.py`. The roster is a
hand-maintained tuple, so nothing forces the decision, but its own contract is "the names the
documents put weight on" and the README now puts weight on this one. If it joins, it **will**
measure uncalled, because `_shipped_call_sites` counts `ast.Call` nodes and the demo passes the
function as a bare Name to `HookMatcher`; the accompanying disclosure sentence is therefore part
of the decision, not an afterthought.

- [ ] **Step 1b: Add the asymmetry paragraph to the Agent SDK claim**

Append to the section 7.3 blockquote, keeping its deny-surface sentence and compatibility framing
intact. This text is em-dash-free and ships verbatim:

> The two frameworks bind asymmetrically, and the asymmetry is the design. Pydantic AI binds at a
> seam this repository already owned, the Checker's transport parameter, so the framework is a
> replaceable implementation detail behind an interface the tests pin. The Agent SDK offers no
> such seam, because it is the runtime that drives the tools; enforcement placed inside it would
> live where a static import audit cannot follow. So the SDK's hook contract is implemented as
> this repository's own code: `src/chaperone/gates/sdk_callback.py` produces the HookJSONOutput
> deny shape as plain JSON and is exercised offline as the fourth decision surface in the same
> parity tests that pin the other three layers, while `demo/sdk_hook.py` confines the
> `claude_agent_sdk` import to wiring. The audit can read the gate; only the wiring needs the SDK.

Note the word deliberately not used: "first-class surface" was proposed and rejected, because it
collides with the spec's deliberate subordination of the SDK path to the executor chokepoint.
The tree's own vocabulary is "fourth decision surface."

- [ ] **Step 2: The architecture section 2 correction**

Replace the shadowing sentence per spec section 5: the property (anything auto-approving earlier
means the callback is never reached), the SDK's three shadowing sources with their `types.py`
citations, the six-step order attributed to the permissions documentation, the
`CanUseToolShadowedWarning` citation (`types.py:1671`, emitted `:1759`) with the category error
named (a warning code, not an environment variable), and the quoted SDK recommendation from
`types.py:1704-1710` ("To gate every tool call, use a PreToolUse hook instead").

- [ ] **Step 3: The guardrail comparison paragraph (architecture.md, beside the section 2
  discussion)**

Open with provenance: pydantic-ai-harness 0.18.0, read in a separate environment, not a
dependency of this repository, compared from its source and docstrings in 2026-08. Content per
spec section 5: `ToolGuardrail` (not `ToolGuard`); its guard sees tool arguments, so "gates by
tool name only" is withdrawn; a guardrail is a value on the Agent object, invisible to a static
import audit; its `block` verdict is the graceful path where this repository's denial is terminal
and non-retryable; the output-tools boundary is version- and layer-specific (harness 0.18.0
bypasses its hooks; core pydantic-ai 2.23 joins the output toolset to the combined toolset). One
line in the README points here.

- [ ] **Step 4: The Limits delta sentences (five, em-dash-free, exactly as drafted in spec 7.4
  plus the adapter sentence)**

Add to the README's Limits: the live judging arm sentence (cross-referencing the arm-1 section);
the audit timestamps and error categories sentence; the tiered-state sentence; the three-signal
routing sentence; and the evals-adapter sentence: "pydantic-evals is an optional extra and is
imported nowhere; every number in docs/RESULTS.md was produced by the hand-rolled harness in
src/chaperone/evals/, offline, from the frozen recordings." Use "the SDK demo's live lane" and
"the live judging arm" as distinct names throughout.

- [ ] **Step 5: Full verification gate, then commit and push**

Run: `/verify` (or: `python -m pytest && python tools/report.py && git diff --exit-code docs/RESULTS.md && python tools/static_audit.py && python tools/scan_secrets.py && python tools/coverage_map.py && python tools/lint_descriptions.py && python demo/day2.py && python demo/full.py`)
Expected: suite green, all tools exit 0, no RESULTS diff.

```bash
git add README.md docs/architecture.md
git commit -m "docs: the hook contracts named exactly, the shadowing correction cited, the guardrail comparison provenanced"
git push
```

---

### Task 12: The spec ledger closes

**Files:**
- Modify: `docs/superpowers/specs/2026-08-09-stack-binding-design.md` (a short "landed" ledger
  at the bottom: which sections shipped, which delta sentences shipped instead)
- Test: none

- [ ] **Step 1: Append the ledger**

A dated list: Step 0 through Task 11 landed; the evals adapter deliberately not built this week
(its delta sentence shipped in Limits); B-live landed under `--live` or its delta sentence
shipped. One line each, no promises. Record that spec amendments A1 (cut order superseded by the
B-core promotion) and A2 (the live lane is flag-gated, not credential-gated) were taken in a
review round before implementation, so the spec and the tree agree on what was built and why.

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-08-09-stack-binding-design.md
git commit -m "docs: close the stack-binding ledger against what actually landed"
```

---

### Task 13 (conditional, only inside the week's timebox): The demo's live lane

**Files:**
- Modify: `demo/sdk_hook.py` (the live `main`: a real `ClaudeSDKClient` run, key-gated)
- Test: none (never in CI; a demo, not a test)

**Interfaces:**
- Consumes: `build_options()` from Task 10.

- [ ] **Step 1: Add the live lane behind an explicit `--live` flag, never a credential check**

The live run registers an in-process send tool whose closure appends to `entered`, sets
`include_hook_events=True`, drives one drafting turn that attempts the send, then asserts three
effects and prints which contract fired: `entered == []`; a PreToolUse `HookEventMessage` deny
present in the stream; no successful `ToolResult` for the send tool in the transcript.

**The gate is the flag, not a credential.** Without `--live`, print the offline explanation and
exit 0. Under `--live`, attempt the connection and let the SDK's own errors surface
(`CLINotFoundError`, authentication failure). A silent skip under `--live` is forbidden: the
installed SDK discovers the CLI through `shutil.which("claude")` and inherits the process
environment, and its authentication may be the CLI's own OAuth credentials rather than
`ANTHROPIC_API_KEY`, so a credential-gated lane could never run on this machine while the ledger
recorded it as landed.

- [ ] **Step 2: Run it once live outside CI; keep the transcript out of the repo**

Run: `python demo/sdk_hook.py --live`
Expected: the three assertions pass and the script prints the deny event. If the SDK raises
instead, that error is the finding; do not convert it into a skip. Nothing from the live run is
committed.

- [ ] **Step 3: Commit the code only**

```bash
git add demo/sdk_hook.py
git commit -m "feat: the SDK demo's live lane asserts the deny from the transcript, never from configuration"
```

If this task does not land inside the timebox, ship instead the delta sentence in the README's
Agent SDK subsection: "The demo's live lane is not built; the offline wiring and the callback's
tests are the exercised surface, and nothing in CI contacts a model."
