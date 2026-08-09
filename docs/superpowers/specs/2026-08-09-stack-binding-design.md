# Stack binding: make the declared stack true in the tree

**Date:** 2026-08-09. **Status:** locked pending review. **Base:** `feat/chaperone` at `9a0666d`,
1,193 tests green on `C:\Users\hp\anaconda3\python.exe` (Python 3.13.9, pydantic 2.12.5,
pydantic-ai 2.23.0, claude-agent-sdk 0.2.130). **Supersedes** `2026-08-07-readme-design.md` §4.9 on
the manifest question only; that spec's sentence was true on its date and stays unedited.

## 0. Motivation and ground truth

`pyproject.toml` declares `pydantic-ai>=0.0.20`, `pydantic-evals>=0.0.20` and
`claude-agent-sdk>=0.1.59`. Nothing under `src/` imports any of the three; the only pydantic
imports are the validator itself, in `gates/checker.py`, `audit/entry.py` and `gates/handoff.py`.
The README discloses half of this gap (the Designed-vs-Built row "Pydantic AI binding for the
checker: Designed, not built"); the manifest overstates the other half. A declared dependency that
nothing imports is a claim the tree does not back. The fix goes in the tree: bind what should be
bound, move to optional extras what is not yet bound, and document the contracts that already
exist under their exact names.

Verified before design, not assumed: the `DESIGNED_VS_BUILT` registry in `tests/test_readme_claims.py`
holds nine path-existence entries and none is the Pydantic AI row (the row is checked by reading);
`.claude/settings.json` wires exactly two command hooks (`tools/guard_edit.py` on `Write|Edit`,
`tools/scan_secrets.py` on `Stop`); `tools/policy_hook.py` implements the out-of-process exit-2
PreToolUse contract and is registered in no settings file; the README contains no occurrence of
"Agent SDK", "hook protocol", "exit 2" or "out-of-process"; and the purity denylist
(`tools/static_audit.py`, `tools/guard_edit.py`) forbids `pydantic_ai` and `claude_agent_sdk`
inside `policy/`, which nothing here touches. Bindings land in `gates/` and `evals/` only.

Frozen-measurement constraints, binding on every section: `corpus/` is read-only and no live model
call is made over any corpus row; the recorded verdicts predate every binding and are never re-run;
`python tools/report.py` must regenerate `docs/RESULTS.md` with no diff after every section; the
arithmetic behind every published number remains `src/chaperone/evals/`'s own.

Review provenance: each section below carried its own independent design review round, and the
retry question additionally carried a full second-opinion round with the four course corpora read
end to end. Amendments from those rounds are folded in, not appended.

## 1. The checker binding

A new module `src/chaperone/gates/binding.py` provides the factory

    pydantic_ai_transport(model, checker_tier, drafter_tier, retries=2)
        -> Callable[[list[dict]], CheckerResult]

`model` is a pydantic-ai model spec or `Model` instance (`FunctionModel` in tests);
the two tier names feed `assert_checker_not_weaker(checker_tier, drafter_tier)` inside the factory,
so the strength ordering is enforced at both construction points from one predicate. The returned
transport is accepted by `Checker` unchanged; `gates/checker.py` gains exactly one clause (below).

**Prompt assembly does not move.** `build_checker_messages` remains the sole assembler. The
transport converts each `{"role": "user", "content": c}` dict to
`ModelRequest(parts=[UserPromptPart(c)])` and runs the Agent with `message_history=...` and
`user_prompt=None`; pydantic-ai 2.23 pops the trailing `ModelRequest` and reuses its parts as the
live request, and sets `instructions` to `None` when the Agent carries none (verified at
`_agent_graph.py:553-562` and `:1475-1478` on the installed package). A wire-fidelity test pins the
conversion: `FunctionModel` captures the first request; the test asserts exactly one
`ModelRequest`, exactly one `UserPromptPart` whose content string-equals the assembled message
content, and `instructions is None`. The independence properties stay string properties, now
asserted on the wire rather than one layer before it.

**The retry split.** Validation failures are failures a model can fix if told; transport failures
are not. The binding owns the first category: `Agent(model, output_type=Verdict | FlagForReview,
retries=N)` feeds each `ValidationError` back through `RetryPromptPart`. The semantic rule
("a verdict that reports a violation must name a class") is extracted to one pure predicate with
two enforcement points, per the house pattern: `_reject_unusable` keeps calling it for scripted and
recorded transports, and the binding registers it as an output validator that raises `ModelRetry`
with the error text so the model sees what to fix. It is shared, not moved: a validator on the
`Verdict` type itself would make frozen recorded rows raise at construction, and the recordings are
never re-run.

**The span rule.** Plan-time correction, recorded here so spec and plan agree: at the transport
seam the draft is out of scope (`Checker.check` builds messages and calls `transport(messages)`;
no draft reaches the binding), so the span rule cannot be an output validator closing over the
draft. It is instead a shared pure predicate in `gates/checker.py`, enforced in `Checker.check`
after `_reject_unusable` for every transport alike: a non-null `Verdict.span` that is not a
verbatim substring of `draft.body` is refused, and the refusal fails closed through the existing
loop. Feedback retry applies to the semantic rule only; span violations deny rather than being
fed back, and the README states that scope. The predicate returns `None` for a `FlagForReview`
(no span to check) and for a null span. This rule cannot live on the `Verdict` type (no draft in
scope there). Before the rule is claimed anywhere,
a permanent corpus test asserts substring-ness across all 160 recorded rows (spot-checks pass
today); if that test ever finds a paraphrase, the rule is scoped to the live binding and the README
says so.

**No budget multiplication.** Validation exhaustion inside pydantic-ai surfaces as
`UnexpectedModelBehavior` (verified: raised from `tool_manager.py:225-234` at 2.23 when the output
retry budget is exceeded; the base class is caught so `IncompleteToolCall` rides along). The
binding maps it to `CheckerUnavailable`. `Checker.check` gains one clause, `except
CheckerUnavailable: raise`, above its bare `except Exception`, so a transport that declares
terminal failure is never availability-retried. Worst case is N+1 model calls, not
(R+1) times (N+1). The budget is also structural: the Agent runs under
`UsageLimits(request_limit=N+1)`, and `UsageLimitExceeded` maps to `CheckerUnavailable` too, so the
bound holds even if a future pydantic-ai changes retry semantics. One `FunctionModel` counter test
pins budget and non-multiplication together: always-invalid output, assert `CheckerUnavailable`
and total model calls equal to N+1.

`Checker.check`'s loop keeps its current meaning for scripted and recorded transports, where there
is no model to feed back to and same-prompt retry is the only retry there is. All 22 existing
checker tests survive unedited; every change is additive.

**Claims.** The README row flips to Built in the same commit that adds
`src/chaperone/gates/binding.py` to the `DESIGNED_VS_BUILT` registry, and ships with this sentence:
the 160 recorded verdicts predate the binding and were not re-run through it; the binding is
exercised offline against `FunctionModel`, and no published rate was measured
through it.

The clause naming what exercises the binding is written from the tree at ship time, never quoted
forward from here — see A3.

## 2. The redirect destination and the outage marker

Two changes at the gate layer; neither touches `policy/` semantics or `corpus/`.

**The destination.** Today `Disposition.REDIRECT_FUTILE` and `REDIRECT_REFINABLE` name a redirect
and nothing routes; the only queue in the tree is a tier-verb string in `gates/ladder.py`. The
design adds `ReviewQueues` (shape: named lists of `Handoff`) passed to `guarded_call` as a
**required keyword-only parameter with no default**; a defaulted parameter would be the unrouted
gap reborn as the default path. Roughly 21 call sites update mechanically (18 in
`tests/gates/`, `demo/day2.py`, `demo/full.py`, `src/chaperone/testing/scripted.py`). The
destination is derived in one place, `destination_for(disposition)` in `gates/engine.py` beside
`disposition_for`; both redirect dispositions route to `"human-review"` (the residual direction
favours a spurious queue item over a lost one), and no call site may name a queue literal. On a
denied effectful call, `guarded_call` appends the built `Handoff` before returning the denial
result: one control, two effects, stop and route.

Tests assert effects only: after a denied send the payload is retrievable from the named queue;
a scripted run that never violates leaves every queue empty; a refinable denial and a futile denial
both land (pinned separately). The queue becomes the single reviewer surface: `demo/day2.py`,
`demo/full.py` and the scripted runs read the queue rather than calling `build_handoff`
themselves, so a denial produces one handoff artifact, not two; both demos run in CI, so a second
artifact would be a red gate, not a style point.

One mechanical consequence, priced here because the guard is right when it fires: the
`CheckerUnavailable` branch in `gates/engine.py` is the anchor line for the
`checker_unavailable_fails_open` mutant in `tools/mutate.py`, and `apply_mutant` refuses a stale
anchor. The commit that adds `Decision.outage` re-derives that anchor in the same change, per the
precedent already recorded in `mutate.py`'s own comments.

Scope, stated in the module docstring and README: this is in-process routing that makes the
redirect real at the chokepoint. The audit log proves a redirect happened (`outcome=redirected`);
the queued payload is not reconstructible from the log, and durable delivery to an external inbox
is deployment wiring, out of scope. What ships here: a futile denial at `guarded_call` queues a
handoff with `refinement_rounds=0` and no alternative; the refine-enriched handoff remains
caller-built, and wiring it into the queue is the caller's line, shown in `demo/full.py`.

**The outage marker.** `decide` currently records checker unavailability as a
`Finding(OTHER, ...)` whose nature lives in a prose detail. `Decision` gains a structured field,
`outage: str | None = None`, set only on the `CheckerUnavailable` branch in `gates/engine.py`
(`policy/` stays pure; the value is passed in). `Handoff` gains `detector_outage: str | None` as a
**required field with no default**, copied from `Decision.outage` by `build_handoff`. The reviewer
can now distinguish "the checker judged this violating" from "the checker was down and the gate
failed closed" without parsing prose. Because the field is required, the existing
drop-each-field `Handoff` test covers it with zero edits. A new pair pins both values from the
predicate's effect: an unavailable-transport run produces a handoff naming the outage; a judged
violation produces `None`. No serialized `Handoff` fixture exists anywhere in the tree, so the
schema change breaks nothing recorded.

## 3. The Agent SDK backing

**The contract, documented (unconditional).** The README and `docs/architecture.md` gain a
subsection stating exactly what exists, in the README's uncalled-layers register:
`tools/policy_hook.py` implements the out-of-process PreToolUse command-hook contract (JSON on
stdin, exit code 2 blocks, structured reason on stderr) and is wired to no runtime; the hooks that
are wired in `.claude/settings.json` are the edit-time purity guard and the Stop-time secret scan;
these are different contracts, and every sentence names which one it means. Citations, split by
what each source can actually carry: `claude_agent_sdk/types.py:1704` for the SDK's own statement
that `bypassPermissions` auto-approves before `can_use_tool` and its recommendation "To gate every
tool call, use a PreToolUse hook instead" (quoted; it is the SDK recommending this repository's
choice); the permissions documentation for enforcement behaviour; `types.py:1990-2000` for
`setting_sources=None` loading all filesystem sources and `[]` being isolation mode.

**The demo, timeboxed, split into two lanes.** `demo/sdk_hook.py` runs a drafting loop under
`ClaudeSDKClient` with the same outbound policy enforced in-process: a `PreToolUse` `HookMatcher`
whose callback runs the identical `decide()` chain and returns the verified deny shape

    {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": "<category>: <detail>"}}

(`hookEventName` is required; this is the `HookJSONOutput` contract, distinct from the
`can_use_tool` `PermissionResult` contract; deny-reason forwarding to the model is cited to the
CLI hooks documentation). The demo runs with `setting_sources=[]` so "the in-process hook is what
fired" is provable rather than assumed: no filesystem hook can have contributed.

The genuinely new surface is not `decide()` (1,193 tests cover it); it is the payload adapter.
The mapping from `tool_input` to `(Draft, Record, ActContext)` is extracted from
`tools/policy_hook.py` into one pure module, `src/chaperone/policy/payload.py`, imported by both
the exit-2 hook and the SDK callback, carrying `UNENFORCEABLE_HERE` and `CONSUMED_KEYS` with it.
The test consequences are priced, not assumed: the equality assertions survive via re-export, but
the two AST-derivation tests parse `policy_hook.py`'s file text and would go blind or red when the
logic moves, so they are repointed to parse `policy/payload.py`'s builder (its `tool_input`
parameter is the taint root) with their anti-vacuity floors kept. The callback itself is a plain
async function in an SDK-free module, `src/chaperone/gates/sdk_callback.py`: payload in, deny-dict
out (the deny shape is plain JSON and imports nothing). `demo/sdk_hook.py` is the only module in
the tree that imports `claude_agent_sdk`, it only wires the callback into a `HookMatcher`, and it
is imported by no test, so CI collection never touches the SDK and the static audit can read the
callback it is asked to trust. The callback receives `tool_input` from the model,
the same trust boundary as the exit-2 hook, so the same classes are caller-suppressible and the
demo's README claim states its deny surface as content classes plus the declared-enforceable act
classes only.

**B-core** (about one and a half days once the derivation-test repointing is priced in, shippable
alone): the shared adapter, the repointed derivation tests, offline tests pinning the adapter's
five documented traps (absent body is not empty body; payload-level `tool_name` wins; no default
approval token; tier defaults to 2; unconsumed keys are refused), the existing three-layer parity
tests extended to feed the callback the same payloads (a fourth decision surface joins the drift
rule), the README claim, and the SDK version pin (0.2.130) in the demo docstring. **B-live** (final half day, the sole slip casualty inside B): the live run under a real
key, never in CI, asserting from effects: the send tool's closure sentinel is empty, a PreToolUse
`HookEventMessage` deny is present in the stream (`include_hook_events=True`), and no successful
`ToolResult` for the send tool exists in the transcript. Framing, backed by the Harness corpus's
own Layer-2-versus-Layer-3 judgment: the SDK path is a compatibility demonstration; primary
enforcement remains the hand-rolled executor chokepoint, because a static audit can prove
properties of code it can read and not of an SDK-managed callback.

## 4. The evals binding and the coupled-pair closures

**Coupled-pair closures (unconditional, small).** First: a
`CONTENT_CLASS_PHRASES: Mapping[ViolationClass, str]` ships beside the checker instruction
constant. Phrases are hand-chosen; coverage is derived: the keys equal the `Family.CONTENT`
members exactly (both directions), each phrase is a substring of the instruction text, and the
instruction's stated count ("three") equals `len(CONTENT_CLASS_PHRASES)`; that count equality is
what "no other class is described as checkable" operationalizes to. The constant is not recomposed
from the map: the recorded verdicts were produced under these exact instruction bytes. This closes,
in this repository, the drift its own source corpus shipped (an allowlist token absent from the
prompt it was coupled to). Second: `build_checker_messages` becomes keyword-only,
`(*, draft, record)`; twelve call sites update mechanically, emitted messages are byte-identical
so replay digests survive, and one README sentence records the signature change because
`testing/recorded.py` is a replay surface external callers key against. These closures land
**before** Section 1, so the binding's transport is written keyword-only from birth as the
thirteenth caller rather than a retrofit.

**The evals reporting adapter (conditional).** `evals/` gains an adapter, never a re-computation:
the frozen recordings wrapped as `pydantic_evals.Case` and `Dataset`, the task function replaying
through the existing arm logic, evaluators as thin wrappers over the existing booleans.
`dataset.evaluate_sync(...)` runs offline. The installed report surface exposes averages, which
are rates and stay out of assertions per the CI-asserts-invariants rule; therefore counts are
folded from per-case assertion results, keyed by evaluator name (never positionally; the
repository forbids positional selection twice already), with `report.failures == []` asserted
first since a failed case silently leaves the fold. The folded counts are asserted equal to the
published `ArmResult` counts, arm by arm and scope by scope: two layers, one arithmetic. The
adapter's `EvaluationReport` is never rendered into `docs/`; nothing on the published page derives
from pydantic-evals arithmetic. No `LLMJudge` anywhere: it would be a live call. The same commit
raises the `pydantic-evals` floor to the surface used (`>=2.23`) and promotes it from optional
extra to runtime dependency, so the manifest tracks the tree at every point in history.

## 5. Corrections and packaging truth

**The architecture.md section 2 correction.** The sentence attributing `can_use_tool` shadowing to
"an environment variable or a competing registration" is replaced by the property: any rule that
auto-approves earlier in the permission resolution means the callback is never reached. The SDK's
three shadowing sources are named with their citations (`bypassPermissions` at `types.py:1704`;
whole-tool `allowed_tools` entries; settings-file allow rules, per `types.py:1727-1728`); the
six-step resolution order is attributed to the permissions documentation explicitly rather than
counted in this repository's own voice. The Python-native citation lands:
`claude_agent_sdk.types.CanUseToolShadowedWarning` (`types.py:1671`, emitted at `:1759`), whose
docstring names the TypeScript process-warning code `CLAUDE_SDK_CAN_USE_TOOL_SHADOWED`; the
correction names the original category error (a warning code, not an environment variable) so it
cannot be reintroduced.

**The guardrail comparison, placed and provenanced.** The comparison paragraph lives in
`docs/architecture.md` section 2 beside the `can_use_tool` discussion, not in the README (the
README carries at most a one-line pointer), and opens with its provenance: pydantic-ai-harness
0.18.0, read in a separate environment, not a dependency of this repository, compared from its
source and docstrings in 2026-08. Content, corrected against both packages: the class is
`ToolGuardrail` (not `ToolGuard`); its guard does see tool arguments including a draft body, so
"gates by tool name only" is withdrawn; what stands is that a guardrail is a value on the Agent
object, invisible to a static import audit; its documented `block` verdict is the graceful path
(the refusal text becomes the tool result and the agent may try another approach), where this
repository's denial is terminal and non-retryable by contract; and the output-tools boundary is
version- and layer-specific: in pydantic-ai-harness 0.18.0 output tools bypass its tool-execution
hooks, while in core pydantic-ai 2.23 the output toolset joins the combined toolset
(`agent/__init__.py:2884-2885`). The paragraph states where `ToolGuardrail` is the right tool
(in-process argument validation, workspace containment) and where this artifact's shape is the
point: when the deny must be terminal, the enforcement provable by reading, and the gate outside
the object the model drives.

**Step 0, one commit, before anything else.** All three unbound dependencies move to
`[project.optional-dependencies]` under named groups, `ai`, `evals` and `sdk`, each at the floor
the design verified (`pydantic-ai>=2.23`, `pydantic-evals>=2.23`, `claude-agent-sdk>=0.2.130`),
so a reader cloning at any point can run each surface from the manifest alone
(`pip install .[sdk]` for the demo). A raised runtime floor on an unimported package would replace
a stale claim with a fresher one; the extras state is the truthful one until each section restores
its own dependency: Section 1 promotes `pydantic-ai` to runtime, Section 3 documents the
`claude-agent-sdk` pin and keeps it an extra (the demo is the only importer and no test imports
the demo), Section 4 promotes `pydantic-evals`. Each promotion lands in the same commit as the
first test that imports the promoted package, so the manifest and the import graph never disagree
in either direction at any commit. The em-dash rule joins the Step 0
checklist: the README and `docs/architecture.md` contain zero em dashes today, and every new
reader-facing sentence must hold that line.

**Naming discipline.** "The SDK demo's live lane" (B-live, conditional this week) and "the live
judging arm" (not this week; the suite has never contacted a model) are distinct and are never
abbreviated to "the live arm" in the same document. The live-lane delta sentence cross-references
the existing "Arm 1 is absent on purpose" section rather than standing beside it as a second
authority.

## 6. Testing discipline

TDD for every item: failing test first, watched failing, minimal implementation, watched passing.
The suite stays offline and keyless by default; the only key-gated path added by this design is
B-live, which is a demo, never a test, and never in CI. No new probabilistic rate is asserted
anywhere; counts over the frozen corpus are deterministic and remain the only numeric assertions.
Suite time is a named cost: about 22 minutes per full run on the recorded interpreter; the
discipline is focused tests inside the loop, full `/verify` at each commit boundary. After every
section, `python tools/report.py` must regenerate `docs/RESULTS.md` with no diff, and the
committed-page binding test keeps holding.

## 7. Required outputs

**7.1 Per binding point.**

| Binding point | Verdict | Justification that ships |
|---|---|---|
| Checker (`gates/checker.py`) | **Bind now** via transport factory (Section 1) | README row flips in the same commit as the registry entry; recorded-verdicts sentence is non-optional |
| `evals/harness.py` | **Stays hand-rolled**; adapter binds over it (Section 4) | The arithmetic behind published numbers is this module's and remains so; the adapter proves the binding without re-computing |
| `evals/judge.py` | **Stays hand-rolled** | Binding it would require `LLMJudge`, a live call; the judge's scores are frozen artifacts |
| `evals/calibration.py` | **Stays hand-rolled** | Per-cell Brier with refusal semantics (`worst_cell` returns `None`; empty input raises) has no library equivalent; wrapping would weaken it |
| `evals/discrimination.py` | **Stays hand-rolled** | Seeded bootstrap AUC behind a published number; the no-recomputation constraint decides this |

**7.2 The guardrail comparison** lands as the provenanced `docs/architecture.md` paragraph in
Section 5, with the withdrawn half stated as withdrawn.

**7.3 The Agent SDK backing decision.** The out-of-process contract is documented with the
wired-to-no-runtime qualifier (Section 3, unconditional); the in-process path is conditional,
B-core then B-live, per 7.4. Exact claim text for the README, with the final sentence shipping
only if B-core ships (the path guard in `tests/test_readme_claims.py` fails any README citing a
file that does not exist, so the sentence and the demo land together or not at all):

> `tools/policy_hook.py` implements the Agent SDK's out-of-process PreToolUse command-hook
> contract: JSON on stdin, exit code 2 blocks, the reason on stderr. It is exercised from
> `tests/gates/test_hook.py` and wired to no runtime; the hooks this repository does wire are the
> edit-time purity guard and the Stop-time secret scan, which enforce this repository's own
> development rules, not the outbound policy. The in-process demonstration in `demo/sdk_hook.py`
> registers the same policy as a PreToolUse hook under `ClaudeSDKClient` with filesystem settings
> disabled, and its offline tests pin the payload adapter and the callback's decisions; nothing
> in CI contacts a model.

**7.4 Feasibility.** Bound this week: Step 0, G-core (2h, lands first), Section 1 (2d),
Section 2 (1d), contract documentation (0.5d), Section 5 corrections (1h): about four days.
Conditional, in order: B-core (1.5d, derivation-test repointing included), B-live (0.5d), the
evals adapter (1d): three conditional days against roughly one remaining, so the week does not
hold all three, and the named casualties are B-live first, the evals adapter second, B-core last. Not this week, delta sentences
shipping in the README's Limits regardless:

- Live judging arm: "No test in this repository has ever contacted a model; there is no key-gated
  live arm, and every verdict any test consumes is a frozen recording." (Cross-references the
  arm-1 section.)
- Audit timestamps and error categories: "Audit entries carry a sequence number and no wall-clock
  timestamp, and every tool failure is logged under the single word `error`; the log orders
  events, it does not date them or classify their failures."
- Tiered state: "There is no persistent-state layer: this artifact governs each outbound draft at
  the boundary, and nothing in it carries relationship state across sessions."
- Three-signal routing: "The checker's stated confidence is measured for calibration and never
  consulted by the gate; no threshold on it routes, promotes, or auto-approves anything, by
  design."

**7.5 Corrected version floors**, from the installed surfaces the design was verified against:
`pydantic>=2.7` (unchanged, imported today); extras until their sections land:
`pydantic-ai>=2.23`, `pydantic-evals>=2.23`, `claude-agent-sdk>=0.2.130`.

**7.6 The architecture.md section 2 correction** is Section 5's first item, with its citations.

## 8. Cut order

B-live first, then the evals adapter, then B-core. Step 0, Section 1, Section 2, G-core, the
contract documentation and the Section 5 corrections are never cut: they are the truth-restoring
core, and every one is independently shippable. If Section 1 itself slips, the fallback is the
Step 0 extras state plus the drafted delta sentence for the unbuilt binding; the README row does
not flip and the registry entry does not land.

## 9. Amendments

**A1 (review round, 2026-08-09).** Section 8's cut order and section 7.4's conditional list are
superseded by the settled B-core promotion: B-core is unconditional; the evals adapter is cut for
the week and its Limits delta sentence ships instead; B-live is the sole conditional item and the
sole remaining casualty. Section 4's adapter design stands unchanged for a later week.

**A2 (review round, 2026-08-09).** The demo's live lane is gated on an explicit `--live` flag, not
on a credential. The installed SDK discovers the CLI through `shutil.which("claude")` and inherits
the process environment; its authentication is the CLI's own OAuth credentials, or
`CLAUDE_CODE_OAUTH_TOKEN`, or an environment key. No credential test is reliable, so a
credential-gated lane could skip silently forever on a CLI-login machine and be recorded as landed
without ever having run. Under `--live` the SDK's own errors surface; a silent skip under the flag
is forbidden.

**A3 (review round, 2026-08-09).** `TestModel` is struck from sections 0 and 1 and from the plan's
three matching lines. It named nothing: no section specified a `TestModel` test, and the plan
contradicted itself inside one task by mandating a test-file docstring reading "run offline on
FunctionModel" alongside a module docstring reading "FunctionModel and TestModel only". The word
survived in two prose asides and propagated from there into a claim sentence marked
verbatim-mandatory, which reached the README's shipping commit and was cut there by the
implementer, on the grounds that shipping it would have contradicted — in the same commit — the
docstring of the module the sentence describes. The README therefore shipped narrowed ahead of
this amendment; this entry ratifies it.

The amendment edits the body rather than appending, because a brief is generated from the body
and an appendix cannot stop the body from re-seeding the struck word.

**The rule this teaches.** A claim sentence marked verbatim-mandatory may fix invariants and
provenance — "predate the binding", "never re-run", "no published rate was measured" — because
those are properties of how the work is done and cannot drift as the tree changes. Any clause
enumerating tree contents ("exercised against X", "tests drive it with X") is written from the
tree at ship time. Design documents state intent; only the tree states what exists.

Rejected here: adding a `TestModel` test to make the sentence true. A test written to make a
sentence true has no honest RED, and the property it would add — that the output union is
satisfiable by a generic schema-driven generator — belongs to the library, not to this tree. Its
pass would hinge on the stub's filler values: a filler emitting `violates=True` with
`violation_class=None` is schema-valid, trips the semantic validator, and reddens the build from
library internals with nothing in this repository having changed. No sentence naming a production
change in this repository could be written for it, which is this project's own test for whether a
test earns its place.
