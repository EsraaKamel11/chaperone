# Framework audit: what is hand-rolled, and which of it should bind

**Date:** 2026-08-09. **Status:** ledger, for review. **Base:** `feat/chaperone` at `7f8454d`.
**Runs before Task 11**, deliberately: Task 11 writes the contract documentation and the Limits
section, and a bind landing after it would leave Task 11 describing a tree about to change.

**This document proposes no edit to `policy/`.** The purity denylist at `tools/static_audit.py:33-37`
forbids `pydantic_ai` and `claude_agent_sdk` inside `policy/`, `audit_policy_purity` is the strongest
structural claim in the repository, and nothing below touches it.

**Settled decisions are not reopened.** `docs/superpowers/specs/2026-08-09-stack-binding-design.md`
closed the checker binding (bind, via transport factory) and closed `evals/harness.py`, `judge.py`,
`calibration.py` and `discrimination.py` as hand-rolled with stated reasons. No stated reason was
found to be factually wrong, so none is reopened here. The hard stop at Task 11 stands; this audit
does not extend it.

---

## 0. Ground truth

Read from the installed source in this repository's development environment, not from memory or
summary. Versions are the `dist-info` records, and every file path cited below is relative to the
installed package root:

| Package | Installed | Role here |
|---|---|---|
| `pydantic-ai` / `pydantic-ai-slim` | **2.23.0** | declared, bound at `gates/binding.py` |
| `pydantic-evals` | **2.23.0** | declared, optional extra, unbound |
| `claude-agent-sdk` | **0.2.130** | declared, optional extra, imported only by `demo/sdk_hook.py` |
| `pydantic` | 2.12.5 | imported at `gates/checker.py`, `audit/entry.py`, `gates/handoff.py` |
| `pydantic-ai-harness` | **not installed** | comparison only, never a dependency; see section 4 |

Every API named below was resolved against those files. Where a claim came from outside the
installed tree it is labelled as such and repeated in section 4 rather than absorbed silently.

### 0.1 The constraint that decides four of the nine rows

**A guard expressed as a framework callable is a value on an object.** A decision written as plain
code is legible to static analysis; a callable attached to an agent at runtime is legible only to a
runtime holding the framework. Stated precisely, because the tree is precise about it: `audit_policy_purity`
is rooted at `policy/` and `audit_send_references` walks `src/` for one symbol and ignores imports, so
**neither audits `gates/` imports today** and `sdk_callback.py:5-9` says so itself. The point here is
about what is readable in principle, not about which guard currently reads it. The comparison package makes the point sharper than the argument does on its own: its
guardrail policies return `None` from `get_serialization_name()`, documented as excluding callable
policy from serialised agent specs. That is **deliberate exclusion by design, not an analysis
difficulty** -- the library is not failing to serialise the guard, it is choosing not to.

So a bind that moves a deterministic decision from readable code into a runtime callable is a
**downgrade**, even where the framework offers the callable. This applies to rows 1, 4, 5 and 8.
It is stated once here and cited per row.

### 0.2 Two framework facts used, not re-derived

- **A pydantic-ai guard fails CLOSED; a shell hook fails OPEN.** Any exception raised by a
  pydantic-ai guard propagates as-is. In the out-of-process command-hook contract only exit code 2
  blocks; any other exit code is a non-blocking error and the action proceeds. Every sentence below
  about crash behaviour names which contract it means.
- **`ToolBlocked` is not privileged.** Any exception raised by the guard propagates as-is; the
  exception type is a convention, not a mechanism. Nothing here says the documentation requires a
  hard stop to raise.

### 0.3 Out of scope by instruction

`tools/static_audit.py`'s alias hole -- deprecated aliases resolve through module `__getattr__`, so a
name-keyed import audit misses code using an old name -- is **already disclosed in the tree** at
`tools/static_audit.py:21-25` ("a module absent from the denylist ... a shadowing redefinition all
pass"). It is neither fixed nor newly disclosed here, and no repository change is proposed for it.

---

## 1. The ledger

| # | Surface | What it hand-rolls | Framework equivalent (exact API, version) | Verdict | Justification |
|---|---|---|---|---|---|
| 1 | `gates/handoff.py` and the human-approval path | A self-contained escalation payload built at the chokepoint, every field required, routed to a named queue | `pydantic_ai.tools.DeferredToolRequests` / `DeferredToolResults` / `ToolApproved` / `ToolDenied` (`_deferred.py:27,100,110,155`, public on `pydantic_ai`), `Agent.tool(requires_approval=True)` (`agent/__init__.py:2355`), `AbstractToolset.approval_required` (`toolsets/abstract.py:232`), `pydantic_ai.exceptions.ApprovalRequired` (`exceptions.py:166`) -- pydantic-ai 2.23.0 | **keep-principled** | The library's round trip resumes a denied call; this repository's denial is terminal by contract and nothing in the tree resumes, so the two implement different halves of the same word. See 1.1: `ToolApproved.override_args` can substitute arguments between approval and execution, which is exactly the failure `unsendable_in` exists to refuse. |
| 2 | `testing/scripted.py` | An adversary that *attempts actions* through `guarded_call`, enumerating which classes it reaches | **None.** `pydantic_ai.models.test.TestModel` and `pydantic_ai.models.function.FunctionModel` are model doubles, not action drivers | **keep-forced** | `ScriptedRunner` never stands in for a model; it builds `Draft`s and calls the chokepoint. A model double is a different surface, not a cheaper version of this one. |
| 3 | `testing/recorded.py` | A `Checker` transport over frozen recordings, keyed by sha256 over the assembled messages, with "one reader of the artifact" held structurally by an AST test | **None.** Nothing in `pydantic_ai/models/` is a disk-replay or cassette surface (full listing read) | **keep-forced** | The library has no replay-from-artifact model. The digest key is forced by `Checker`'s one-argument transport signature, and the module docstring already measures why a two-argument transport fails silently. |
| 4 | `gates/refine.py` | A bounded loop over `decide`, terminating on verdict-pass, with four named stop reasons, that **transmits nothing** | `pydantic_ai.exceptions.ModelRetry` raised from an output validator against `output_retries` (`agent/__init__.py:194`), exhaustion surfacing as `UnexpectedModelBehavior` (`tool_manager.py:230-234`) -- pydantic-ai 2.23.0 | **keep-principled** | The split does not apply. `ModelRetry` feeds a failure back inside one run and the accepted output is then *used*; `refine` returns a proposal that a human reads beside the blocked body, and the original attempt stays terminal. Binding it would make the loop's output live, which is the auto-retry of a permission failure this architecture exists to refuse. |
| 5 | `gates/hook.py` | Two in-process enforcement points over one predicate set, plus a repository-defined `HookOutcome` shape | `claude_agent_sdk.types.HookMatcher` (`types.py:589`), `HookCallback` (`:577`), `PreToolUseHookSpecificOutput` (`:416-421`) -- claude-agent-sdk 0.2.130 | **keep-principled**, with a documentation finding | The SDK contract is already implemented, correctly, in `gates/sdk_callback.py`, which names it. `guarded_call` is the executor chokepoint and implements no framework contract, correctly. The finding is in 1.2: the module docstring names three layers where four exist, and `pre_tool_use` calls itself "the in-process framework hook" while returning a shape no framework defines. |
| 6 | `gates/ladder.py` | Tier promotion and demotion, declared ceilings, per-surface verbs | **None.** Verified by grep across pydantic-ai 2.23.0 and claude-agent-sdk 0.2.130: no such state machine exists in either. The words `promote` and `demote` do occur, only in unrelated surfaces (discriminated-union part promotion in `messages.py`, tool search), so a reader re-running the grep should expect hits that are not ladders | **keep-forced** | Expectation confirmed rather than assumed. Neither package models graded autonomy at all. |
| 7 | `audit/` (`chain.py`, `store.py`, `gateway.py`, `entry.py`, `recovery.py`) | Hash-linked tamper-evident append-only log, fsync per entry, torn-tail detection, argument digests never raw arguments, crash recovery | **None.** Verified: no `prev_hash`, `hash_chain` or tamper-evidence code in pydantic-ai 2.23.0, pydantic-evals 2.23.0 or claude-agent-sdk 0.2.130 (the single binary hit is the SDK's bundled CLI, not a Python surface). The observability counterpart is `pydantic_ai.models.instrumented.InstrumentedModel` over OpenTelemetry / Logfire | **keep-forced** | Logfire is observability: it tells you what happened if you trust the collector. A hash chain tells you whether the record was altered. This repository exceeds both libraries here, and that is a stronger README line than silence. |
| 8 | `matching/` | Ablation arms, contamination and recall-loss metrics over one population | `pydantic_evals.evaluators.ROCAUCEvaluator` (`report_common.py:234`), `PrecisionRecallEvaluator` (`:170`), `ConfusionMatrixEvaluator` (`:107`) -- pydantic-evals 2.23.0 | **keep-principled** | Two independent reasons. The frozen-measurement rule forbids recomputation, and separately **the library's version would be wrong on this data**: see 1.3. |
| 9 | Offline enforcement in the suite | Offline and keyless by construction plus inspection: no test constructs a provider model | `pydantic_ai.models.ALLOW_MODEL_REQUESTS = False` (`models/__init__.py:1331`), enforced by `check_allow_model_requests` (`:1365-1366`), scoped by `override_allow_model_requests` (`:1370`) -- pydantic-ai 2.23.0 | **keep-for-now** | Not a hand-roll at all: it would *add* a library-enforced guarantee, and an honest offline RED for it **does** exist via an injected `httpx.MockTransport`. It is recorded rather than recommended on Amendment A3's ground, that a property belonging to the library earns no test in this tree. See 1.4, which corrects two false claims made in a first pass of this audit. |

**Counts: 0 bind-now, 4 keep-principled, 4 keep-forced, 1 keep-for-now.**

---

### 1.1 Row 1: the approval round trip, stated precisely

The framing this audit was handed said "on resume the guard is re-evaluated, so approval is not a
policy bypass." Read against the installed source that is true of some guards and false of the one a
reader would assume. The precise statement, which is the one that should appear anywhere this is
described:

**On resume the tool call is re-validated through the toolset chain; the approval predicate itself is
not, and the arguments that execute may differ from the arguments a human approved.**

Three lines carry it:

- `pydantic_ai/toolsets/approval_required.py:29` --
  `if not ctx.tool_call_approved and self.approval_required_func(ctx, tool.tool_def, tool_args)`.
  On resume `ctx.tool_call_approved` is `True`, so the approval predicate short-circuits. It is not
  re-evaluated.
- `pydantic_ai/tool_manager.py:1093-1104` -- on `ToolApproved` the manager calls
  `validate_tool_call(validate_call, approved=True, ...)` and then `execute_tool_call(...)`. The
  full chain re-runs: pydantic schema validation, `args_validator_func`, and every wrapper toolset
  below the approval toolset. So a guard that is not the approval predicate **does** re-run.
- `pydantic_ai/_deferred.py:103` and `tool_manager.py:1095-1096` --
  `ToolApproved.override_args` is applied as `replace(call, args=override_args)` **before** that
  re-validation.

That third line is the one worth keeping. `ToolApproved(override_args=...)` is precisely the shape of
failure `gates/hook.py` documents at its module docstring and `policy/arguments.unsendable_in`
refuses: the reviewed object and the executed object diverging after the decision. The library makes
that divergence a first-class feature and relies on a downstream validator to catch it; this
repository refuses any scalar in `args` outside the draft's outbound surface, at the chokepoint,
before the executor is reached.

Why the verdict is keep and not bind: the two surfaces implement different halves of "approval".
`DeferredToolRequests` is a **resumption** mechanism -- the run pauses, a human answers, the same
call proceeds. `Handoff` is an **escalation** artifact -- the call is terminal, and what reaches the
human is a self-contained payload assembled from the draft, the record and the decision, because the
reviewer cannot see the conversation (`handoff.py:20-28`). Nothing in this tree resumes a denied
call, by design. Binding would require inventing a resumption path in order to use the library that
provides one, which is the tail wagging the dog. Per 0.1, it would also move the deny decision onto
an agent object where the static audit cannot read it.

### 1.2 Row 5: the fourth layer, and the one contract nobody names

Four decision surfaces exist over one predicate set:

| Layer | Module | Contract it implements | Does it say so? |
|---|---|---|---|
| out-of-process command hook | `tools/policy_hook.py` | exit 2 blocks, JSON on stdin, reason on stderr | **Yes**, at `tools/policy_hook.py:8-11`, with the fail-open asymmetry spelled out |
| in-process framework hook | `gates/hook.py::pre_tool_use` | none: returns `HookOutcome`, a repository dataclass matching no framework's shape | **No** |
| executor chokepoint | `gates/hook.py::guarded_call` | none, correctly: it is this repository's own boundary | Yes, by naming itself the executor layer |
| in-process deny callback | `gates/sdk_callback.py::pre_tool_use_deny` | the Agent SDK `HookJSONOutput` `PreToolUse` shape, produced as plain JSON, importing no SDK | **Yes**, at `sdk_callback.py:1-9` |

**Are all four earned? Yes, and the one worth testing is `pre_tool_use`.** It has the profile of a
layer that exists to be tested: it implements no framework contract, and `gates/hook.py` defines it
without calling it, so every call site is under `tests/` (`tests/gates/test_hook.py`, with the name
also carried in the registries at `tests/test_readme_claims.py:937,945,1002`). The tree already
discloses this at `docs/architecture.md:76` ("Called from `tests/` and from nowhere else") and holds
it by test at `tests/test_readme_claims.py:967-969`. What earns it is the discriminator in the
same table row: **it is the only layer that enforces the full predicate set, checker included,
without being the thing that performs the call.** The two hook-shaped layers that could stand in for
it cannot: `tools/policy_hook.py` and `gates/sdk_callback.py` both enforce the pure half only, because
neither can call the model checker from the payload it is handed. Delete `pre_tool_use` and the layer
set becomes two pure-half hooks plus the executor, at which point the portability claim weakens to
"the pure half ports, and the full set exists only where execution happens", and the ordering argument
at `docs/architecture.md:86-87` loses the earlier layer it contrasts the chokepoint against. So the
layer is principled, not incidental. Its **return shape** is the incidental part, and that is finding 2.

Two findings, both recorded, neither fixed here (this audit writes no source):

1. **`gates/hook.py`'s module docstring names three layers where four exist.** It reads
   "`pre_tool_use` is the in-process framework hook; `guarded_call` is the executor chokepoint. A
   third lives out of process in `tools/policy_hook.py`" and does not mention `sdk_callback.py`.
   The parity test is **not** stale: `test_the_same_policy_denies_at_all_three_layers`
   (`tests/gates/test_hook.py:174-218`) drives all four and states at `:183-184` why the name is kept
   at three, which is the safe direction. Only the module docstring understates the tree.
2. **`pre_tool_use` calls itself "the in-process framework hook" and names no framework.** It
   returns `HookOutcome`, which is neither the SDK's `HookJSONOutput` shape nor pydantic-ai's
   toolset return. The honest description is that it is a generic in-process hook layer showing the
   same predicate set is portable, and the SDK-shaped one is `sdk_callback.py`.

On crash behaviour, naming the contract each time: `pre_tool_use_deny` catches `BaseException` and
converts it to a deny (`sdk_callback.py:115-118`), so it fails closed **by construction in this
repository's code**, not because the in-process SDK hook contract guarantees it. `tools/policy_hook.py`
turns every escape into a deliberate exit 2 for the same reason, because in the out-of-process
contract only exit 2 blocks and every other exit is an allow.

### 1.3 Row 8: where the library's evaluator would be wrong

Beyond the frozen-measurement rule, there is a case where binding would produce a number that is
meaningless and looks fine.

`pydantic_evals/evaluators/report_common.py:49-66`, `_get_positive`:

```python
if positive_from == 'expected_output':
    return bool(case.expected_output) if case.expected_output is not None else None
```

`ROCAUCEvaluator` and `PrecisionRecallEvaluator` are **binary-classification** tools. On a ranking
dataset, where `expected_output` is a gold list, `bool(case.expected_output)` is `True` for every
case with a non-empty list. Every such case is scored positive, the negative class is empty or
accidental, and the resulting curve is not a discrimination measurement at all. It renders without
complaint.

So the live question the frozen-measurement rule leaves open -- would an adapter over the frozen
results be honest? -- has different answers on different data. For the classification-shaped frozen
results the stack-binding spec's Section 4 adapter design stands (folded counts asserted equal to
the published `ArmResult` counts, two layers and one arithmetic) and remains cut for the week. For
`matching/`, a ranking surface, the answer is no: the honest adapter would have to supply
`positive_from='labels'` with an explicit `positive_key` and define the negative class by hand,
at which point the library is contributing a curve-drawing routine over arithmetic this repository
still owns.

`ConfusionMatrixEvaluator` (`:107`) does not share the defect -- it takes `predicted_from` and
`expected_from` separately -- but it is a report renderer over results, and the published page is
regenerated by `tools/report.py` from this repository's own arithmetic. Nothing on the published page
may derive from library arithmetic, so the evaluator would render into a report nobody reads.

**No published number is recomputed and no frozen recording is re-run by anything in this ledger.**

### 1.4 Row 9: why the cheapest-looking addition is recorded rather than recommended

`ALLOW_MODEL_REQUESTS = False` was the single strongest bind-now candidate: small, self-contained,
touching no published number and no frozen recording, and now defensible on dependency grounds
because `tests/gates/test_binding.py` already imports pydantic-ai at runtime. It replaces nothing;
it adds a library-enforced guarantee where the suite currently relies on construction plus
inspection.

Verified, both directions:

- **It would not redden the suite.** `check_allow_model_requests` is called by provider models only
  (`models/anthropic.py:634`, `bedrock.py:745`, the `embeddings/*` surfaces, and their peers).
  `models/test.py` and `models/function.py` do not call it, so `TestModel` and `FunctionModel` are
  unaffected and every existing binding test stays green.
- **It sits before the network.** In `AnthropicModel.request` the call is the first statement, ahead
  of `_messages_create`.

**An honest offline RED does exist.** A first pass of this audit claimed it did not. That claim was
wrong, it was wrong for an instructive reason, and it is corrected here rather than quietly dropped:

- **The first error was a name-keyed enumeration.** Grepping for the literal call
  `check_allow_model_requests()` returns fifteen files and misses every **inheritor**. `OllamaModel`,
  `CerebrasModel`, `OpenRouterModel`, `ZaiModel` and `BedrockMantleChatModel` all subclass
  `OpenAIChatModel` and reach the check through `OpenAIChatModel.request`, where it is the first
  statement (`models/openai.py:985`); `BedrockMantleResponsesModel` reaches it through
  `OpenAIResponsesModel` the same way. **This is precisely the hole `tools/static_audit.py:21-25`
  discloses about itself** -- a name-keyed scan over a statically visible graph misses what arrives
  under another name, there by alias and here by inheritance. The audit tripped over its own subject.
- **The second error followed from the first.** "Every class that consults the flag needs a credential
  to construct" is false. `OllamaProvider` needs none: `providers/ollama.py:110-112` self-fills the
  placeholder `'api-key-not-set'`, with a comment saying locally served models do not always need a
  key. Only `base_url` is required.
- **A RED with zero sockets is available.** Every provider accepts an injected client
  (`http_client: httpx.AsyncClient | None` at `providers/ollama.py:83` and
  `providers/anthropic.py:132`). An `httpx.MockTransport` client makes the failing observation real
  and offline: **before** the flag, `request()` runs past its first statement, reaches the mock and
  returns, so a `pytest.raises(RuntimeError)` test fails and is watched failing honestly; **after**
  the flag, `request()` raises at that first statement, before the transport is touched. That asserts
  an effect rather than a flag value, and it is this repository's own recorded-transport discipline
  applied one layer down.

**So the verdict rests on a choice, not an obstacle.** The suite does not construct provider models
because it has decided not to, and the flag guards a class of mistake the suite has decided not to be
able to make. The supporting ground is **Amendment A3** of the stack-binding spec
(`2026-08-09-stack-binding-design.md:404-411`): a property that belongs to the library rather than to
this tree does not earn a test here, and the production change in this repository that such a test
would name is one line of a new `conftest.py` (the repository has none today, at either the root or
`tests/`).

**A3's ground is weaker here than in its original case, and that is stated rather than glossed.**
A3 rejected a `TestModel` test that had no honest RED at all. This one does have an honest RED. What
survives of A3 is the narrower half: the property being added -- that a library flag stops library
code from opening a socket -- is the library's property, and its RED would be watched against library
internals rather than against anything this repository wrote.

The residual value is real and worth naming: on a developer machine carrying a provider key in the
environment, the flag would convert an accidental live call into a loud refusal instead of a silent
one.

**The trigger, restated as a choice.** The earlier draft said "the first time a test constructs a
provider model", which is circular: the RED test *is* such a test. The honest statement is that this
is available today and is declined on A3's ground. It should be taken up when something else in the
tree first needs a provider model for its own reasons, because at that point the flag stops guarding
a hypothetical and the A3 objection no longer applies.

---

## 2. Bind-now items

**There are none.**

That is a result, not an absence of one. Six of the nine rows have no framework equivalent at all or
have one that implements a different thing; two would trade a readable deterministic decision for a
runtime callable, which section 0.1 rules a downgrade; one would produce a number that is wrong and
looks fine. The remaining candidate, row 9, is buildable today and is declined on the repository's
own test-discipline ground rather than blocked by anything, which section 1.4 states plainly along
with the two false claims a first pass of this audit made about it.

Since no bind is recommended, the bind bar is reported as checked rather than applied: no property
would have been lost silently, because no property is moved. No published number changes. No frozen
recording is re-run. No existing test changes meaning. No dependency promotion is proposed, so the
manifest and the import graph cannot disagree. The README's Designed-versus-Built table and its
registry at `tests/test_readme_claims.py:472-483` need no new row.

**One test-meaning observation, recorded because the bar says a changed test meaning is a finding
rather than a detail:** none of the tests examined would change meaning under any verdict in this
ledger, because every verdict is keep. The nearest thing to a change is documentation:
`test_the_same_policy_denies_at_all_three_layers` already drives four layers under a three-layer
name and explains the choice in its own docstring, so its meaning is current and only its name
understates it.

---

## 3. Drafted README sentences, one per keep

Written to ship as-is: **no em dashes**, no organisation name, no person's name, and nothing that
generalises an act-class guarantee onto a content-class, which is the rule
`test_zero_by_construction_is_never_claimed_beside_a_content_class` holds across the reader-facing
pages and the source tree. Each names a module that exists, so the path guard in
`tests/test_readme_claims.py` is satisfied. A keep nobody can explain is indistinguishable from a
keep nobody examined, so each of these is the explanation.

**Row 1, `gates/handoff.py` and the approval path.**

> Pydantic AI 2.23 ships a human-in-the-loop round trip: a tool marked `requires_approval`, a run
> ending in `DeferredToolRequests`, and resumption with explicit results. This repository does not
> use it, because it implements the other half of the word. That round trip resumes a paused call;
> a denial here is terminal by contract and nothing in the tree resumes one. What reaches the human
> is `Handoff` in `src/chaperone/gates/handoff.py`, self contained by design because the reviewer
> cannot see the conversation, with every field required so an escalation cannot arrive with the
> field the reviewer needs quietly empty. There is a second reason to keep the boundary outside the
> agent object. On resume the library re-validates the tool call through the toolset chain, but the
> approval predicate itself is skipped, and `ToolApproved` carries an `override_args` field that
> substitutes arguments after a human has approved the original ones. That divergence between the
> object reviewed and the object executed is exactly what `unsendable_in` in
> `src/chaperone/policy/arguments.py` refuses at the chokepoint.

**Row 2, `testing/scripted.py`.**

> Pydantic AI ships `TestModel` and `FunctionModel`, and this repository uses `FunctionModel` where
> a model double is what is wanted, in the binding's tests. `ScriptedRunner` in
> `src/chaperone/testing/scripted.py` is not a model double and could not be replaced by one: it
> drives attempted actions through the executor chokepoint, so what it proves is that the gate holds
> against an attempt, not that a model produced the attempt. Its own docstring enumerates which
> classes the committed suite reaches and states its bound, which is that it will never emit an
> input nobody wrote.

**Row 3, `testing/recorded.py`.**

> Pydantic AI has no replay-from-artifact model, so `RecordedTransport` in
> `src/chaperone/testing/recorded.py` is hand rolled because there is nothing to bind to, not
> because a choice was made against the library. It is keyed by a digest over the assembled checker
> messages rather than by a corpus identifier, because a `Checker` transport takes one argument and
> a two argument transport fails as a checker outage rather than as a loud error. It reads no file
> and decodes no recorded row of its own, and a test parses this module to hold that structurally.

**Row 4, `gates/refine.py`.**

> Pydantic AI has output validators that raise `ModelRetry` against a retry budget, and the split
> looks similar at first reading. It does not apply here. `ModelRetry` feeds a failure back inside
> one run, and the output that finally validates is then used. The refinement loop in
> `src/chaperone/gates/refine.py` transmits nothing: it returns a proposed body that rides to a human
> in the handoff beside the body that was blocked, and the original attempt stays terminal. A
> redraft that transmitted by itself after a permission failure would be an auto retry of a
> permission failure, which is the thing this architecture exists to refuse.

**Row 5, `gates/hook.py`.**

> The Agent SDK's in-process `PreToolUse` contract is implemented in this tree, at
> `src/chaperone/gates/sdk_callback.py`, which produces the SDK deny shape as plain JSON and imports
> no SDK so that the decision stays readable to static analysis. The other in-process layer,
> `pre_tool_use` in `src/chaperone/gates/hook.py`, implements no framework contract on purpose: it
> is the same predicate set behind a plain return value, and it is the only layer that enforces the
> full set, checker included, without being the thing that performs the call. Expressing the deny as
> a callable registered on an agent object would leave the decision legible only to a runtime
> holding that framework, rather than to anyone reading the code, which is a downgrade whichever
> framework offers it.

**Row 6, `gates/ladder.py`.**

> Neither Pydantic AI 2.23 nor the Agent SDK 0.2.130 models graded autonomy, so the capability
> ladder in `src/chaperone/gates/ladder.py` has no framework equivalent to bind to. The ceilings are
> declared rather than derived from the verb table, deliberately, so that adding a row cannot raise
> a ceiling by what looks like a documentation edit.

**Row 7, `audit/`.**

> Logfire and the OpenTelemetry instrumentation in Pydantic AI are observability: they record what
> happened, and they are trusted to the extent the collector is. The audit trail in
> `src/chaperone/audit/chain.py` answers a different question, which is whether the record was
> altered after the fact. Neither Pydantic AI 2.23 nor the Agent SDK 0.2.130 offers hash linking,
> tamper evidence, or torn tail detection, so this layer exceeds both rather than duplicating
> either. It stores an argument digest and never the arguments, because an audit log is not a place
> to accumulate personal data.

**Row 8, `matching/`.**

> Pydantic Evals ships `ROCAUCEvaluator`, `PrecisionRecallEvaluator` and `ConfusionMatrixEvaluator`
> as report evaluators, and they are the right tools for a binary classification dataset. The
> matching surface is a ranking dataset, and on one of those the first two are quietly wrong: with
> `positive_from='expected_output'` a case is scored positive when `bool(expected_output)` is true,
> so every case with a non empty gold list becomes a positive, the negative class disappears, and
> the curve renders without complaint. The metrics in `src/chaperone/matching/ablation.py` stay this
> repository's own, and every published number keeps the arithmetic it was measured with.

**Row 9, the offline guarantee.**

> Pydantic AI can enforce offline testing globally by setting `ALLOW_MODEL_REQUESTS` to false, which
> turns a model request into a loud refusal before any network call. This repository does not set it,
> and the reason is a judgement rather than an obstacle. The suite is offline and keyless because no
> test in it constructs a provider model at all, so the flag would guard a mistake this tree has
> already decided not to be able to make, and the property it adds belongs to the library rather than
> to anything written here. That is the same ground on which a generic model-stub test was rejected
> earlier in this project. It would be available to build: an injected mock transport gives a
> failing test that opens no socket. It is declined rather than blocked, and it should be taken up
> the first time something else in the tree needs a provider model for its own reasons.

---

## 4. Could not verify

Listed rather than dropped.

1. **`pydantic-ai-harness 0.18.0` is not installed** in this environment
   (`importlib.metadata` raises `PackageNotFoundError`; a filesystem search for it timed out and was
   not retried). The fact used in section 0.1 -- that its guardrail policies return `None` from
   `get_serialization_name()`, documented as excluding callable policy from serialised agent specs
   -- **was supplied to this audit and is not verified against installed source here.** This is
   consistent with how the tree already treats that package: the stack-binding spec section 5
   provenances its guardrail comparison as "read in a separate environment, not a dependency of this
   repository". The conclusion in 0.1 does not depend on it: a callable on an agent object is
   invisible to an AST import audit whether or not the library also excludes it from serialisation.
   The harness fact strengthens the point from "hard to analyse" to "excluded by design".
2. **Live documentation deltas were not checked.** Everything above is read from installed source at
   pydantic-ai 2.23.0, pydantic-evals 2.23.0 and claude-agent-sdk 0.2.130. Where a newer published
   surface exists, it is not reflected here, and no newer surface was silently adopted.
3. **The four-layer parity claim was read, not run.** `tests/gates/test_hook.py:174-218` was read and
   its structure confirmed; the suite was not executed for this audit, and
   `tests/test_mutations.py` and the mutation sweep were not run in any form.
4. **`ToolApproved.override_args` behaviour is read from source, not exercised.** The re-validation
   path at `tool_manager.py:1093-1104` and the short circuit at `approval_required.py:29` were read;
   no run was constructed to observe them, because doing so would need a model.
5. ~~Whether any keyless-constructible model class reaches `check_allow_model_requests`.~~
   **Answered during review, and the first answer was wrong.** It is kept here, struck, because how
   it was wrong is the useful part. The literal-call grep returns fifteen files
   (`models/__init__.py`, nine provider models, five `embeddings/*` surfaces) and **misses every
   inheritor**: the five `OpenAIChatModel` subclasses and the one `OpenAIResponsesModel` subclass
   reach the check through the parent's `request`. `models/mcp_sampling.py`, `models/fallback.py` and
   `models/wrapper.py` genuinely do not consult it, and neither do `models/test.py` or
   `models/function.py`, so those three conclusions stand. The conclusion that did not stand is
   "every class that reaches the flag needs a credential": `OllamaProvider` does not. See 1.4.

   The generalisable lesson, and the reason this entry is not simply deleted: **a name-keyed
   enumeration missed what arrived under another name**, which is the same failure mode
   `tools/static_audit.py:21-25` discloses about its own alias hole. An audit that cites that
   disclosure should be able to notice when it commits the equivalent error.

---

## 5. Sequencing

**Recommendation: land nothing from this ledger before Task 11. Land the sentences with Task 11.**

The reasoning is the same one that put this audit before Task 11 rather than after. Task 11 writes
the contract documentation and the Limits section. Every product of this audit is a sentence, and
sentences are exactly what Task 11 is for. Nine drafted README sentences arriving inside Task 11
cost one editing pass; the same nine arriving as a separate commit afterwards would mean Task 11
described a README about to change.

1. **Now, in this ledger only.** The two `gates/hook.py` documentation findings from 1.2. They are
   docstring corrections in a module the enforcement thesis rests on, they change no behaviour, and
   they are the only place in the audit where the tree currently says something narrower than what it
   does. They are recorded here and **not made**, because this task writes no source. Whoever holds
   Task 11 should fold them in there, where a docstring edit is already in scope.
2. **With Task 11.** The nine sentences of section 3, placed where each belongs: rows 1, 4 and 5
   beside the enforcement discussion, row 7 in the audit section, rows 2, 3 and 9 in the testing
   discipline or Limits section, rows 6 and 8 beside their existing Designed-versus-Built rows. No
   registry entry is added and no table row flips, because nothing is built.
3. **Not this week, and named so.** Row 9's flag, declined on A3's ground per 1.4 and available
   whenever that ground is reconsidered rather than waiting on an event. The `pydantic-evals`
   adapter over the classification-shaped frozen results, already cut by Amendment A1 of the
   stack-binding spec and unchanged by this audit, with 1.3 now added as a scoping constraint: the
   adapter covers the classification-shaped results and never `matching/`.

**What this costs against the remaining budget.** Nothing built, one editing pass folded into a task
that was already going to run, on a suite that does not need to be re-run for a documentation change
beyond the ordinary gate. A finding written down and not built costs nothing; the budget rule was
the deciding constraint on row 9 and it decided correctly.

**What this buys.** Seven surfaces move from unexamined to defended, one framework claim used
elsewhere in the tree is now stated precisely enough to survive a reader checking it (1.1), one
place where the tree understates itself is identified (1.2), and one library evaluator is documented
as wrong for this data before anyone reaches for it (1.3). The README benefits either way: a bind
would have made the declared stack true in more of the tree, and a keep, explained, converts an
unexamined choice into a defended one.
