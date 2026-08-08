# Architecture

Why the pieces are shaped the way they are. This page is the "why not the obvious thing" companion to
the [README](../README.md), which covers what the system does.

---

## 1. The finding everything else follows from

**Constraints are not one kind of thing, and treating them as one kind of thing is the mistake this
artifact exists to correct.**

An earlier design specified a single deterministic policy engine covering all six constraints, and
simultaneously specified that the checker's prompt must exclude the generator's reasoning. Both cannot
be true. If a pure function can decide every constraint, there is no checker and the prompt discipline
is meaningless. If a model is needed for some of them, then those constraints are not decided by a pure
function and "zero by construction" does not apply to them.

The resolution is two families with two different epistemic statuses, and the discipline is refusing to
let the stronger claim leak onto the weaker family:

- **Act-classes** ask whether an act occurred outside its bounds. Was there an approval token at this
  tier? Is this jurisdiction consented? Is this tool in the grant? Does this figure appear in the
  record? Is the send cap exceeded? Every one is a pure function over the draft, the record, and the
  context. The violation is impossible, not improbable **for the four whose inputs a shipped caller
  supplies**. The fifth, `act:send_cap_exceeded`, is a live predicate over a count no shipped path
  derives from the log, so in the shipped shape its violation is not impossible; see
  [B0 in the failure-mode catalog](failure-modes.md).
- **Content-classes** ask whether a piece of text does a forbidden thing. Does it advise on the merits,
  negotiate terms, or make a forward-looking return claim? No pure function decides these. A model
  checker decides, with lexical tripwires as a second disjunct, and the result is a measured rate.

`test_act_classes_and_content_classes_report_their_family` holds the split at the type level, and
`test_tripwires_only_cover_content_classes` holds that the lexical layer never claims act-class
territory. `test_every_content_class_has_both_a_checker_and_a_tripwire` holds that no content-class
rests on a single detector.

---

## 2. Why a `PreToolUse` hook and not a permission callback

The Claude Agent SDK offers a `can_use_tool` callback, which is the obvious place to put a permission
decision, and it is the wrong place for this one.

The permission pipeline resolves through several stages before a tool call executes, and a callback is
one participant in that resolution rather than the final word. Two consequences matter here:

1. **A `PreToolUse` hook survives `bypassPermissions`.** A permission mode that skips the callback does
   not skip the hook. If the enforcement boundary is the thing that must not be optional, it cannot
   live in the layer designed to be configurable.
2. **The callback can be shadowed.** When an environment variable or a competing registration takes
   precedence, a `can_use_tool` implementation can be silently inert: still present in the source,
   never consulted at runtime. An enforcement layer that fails silently is worse than one that is
   absent, because the absent one is visible.

A hook has the opposite failure signature. It is out of process, it is declared rather than injected,
and when it is not running you notice.

### 2.1 Three layers, and what each one actually enforces

The same predicate set is enforced at three points, and
`test_the_same_policy_denies_at_all_three_layers` holds that they agree, while
`test_the_two_enforcement_layers_reach_the_same_verdict_and_the_same_category` holds that they agree on
*which* class fired rather than merely on the outcome.

| Layer | What it is | What it enforces |
|---|---|---|
| Out-of-process hook | A `PreToolUse` hook, separate process | **The pure half only.** Act-classes and tripwires. |
| In-process hook | `pre_tool_use` in `src/chaperone/gates/hook.py` | The full predicate set, including the checker. |
| Executor chokepoint | `guarded_call` in the same module | The full set, bound to the reviewed draft. |

**The first row is the one that needs saying out loud.** An out-of-process hook cannot call the model
checker, so it enforces a strict subset: everything decidable by a pure function, and nothing that
requires a content-class judgement. Claiming "the same policy is enforced at three layers" without that
qualifier would be the overclaim this repository is built to avoid, so the suite states the gap by
name in `test_the_out_of_process_layer_enforces_a_strict_subset_and_names_the_gap`, and
`test_only_the_pure_layer_ports_out_of_process` holds that only the portable half ports.

The ordering is what makes the last layer binding. Earlier layers can be bypassed by a caller who
reaches the executor directly; the chokepoint cannot, because it is the thing that performs the call.

---

## 3. Why the checker is independent, and why it is the stronger model

### 3.1 Independence by omission

The checker never receives the drafting model's reasoning, its confidence, or its self-assessment. It
receives the draft and the constraint set.

This is a design choice with a testability payoff. "The checker ignored the generator's justification"
is a claim about model behaviour and is nearly untestable. "The checker's prompt does not contain the
generator's justification" is a claim about a string, and
`test_the_checker_prompt_has_no_generator_artefacts` checks it in milliseconds.
`test_the_checker_prompt_structure_is_exact` pins the whole shape, so a future edit that helpfully
threads context through cannot land quietly.

A justification is written to persuade. A reviewer holding one is not a second opinion, it is the first
opinion with extra steps.

### 3.2 The cost and quality split

The checker model must be at least as strong as the drafter, held by
`test_a_checker_model_weaker_than_the_drafter_is_refused`.

The tempting inversion is to draft with the strong model and review with the cheap one, because review
"just" classifies. But the review is the step that decides whether an action is permitted, and a
reviewer weaker than the thing it reviews is a reviewer that can be out-argued by fluency. Spend on the
gate.

### 3.3 What happens when the checker is unavailable

The gate is fail-closed. An unparseable response, after a bounded retry budget, raises rather than
degrading to allow, held by `test_an_unparseable_response_after_the_retry_budget_raises_checker_unavailable`.

The alternative, treating an unavailable checker as a pass, converts an outage into an unreviewed
send. Every fail-open path in a permission system is a path where the system is most degraded and least
supervised at the same moment.

---

## 4. Denial is terminal, and why `is_retryable` is always false

`denial_result` returns `is_retryable: false` on every denial without exception, held by
`test_deny_is_never_relabelled_as_retryable` and, for the case that looks like an exception,
`test_a_refinable_denial_is_still_not_retryable`.

**Retryability and redraftability are different axes**, and collapsing them is the tidy-looking edit
that reopens the hole:

- **Retry** means making the same call again. A policy denial will refuse it identically, forever. An
  agent that believes a denial is retryable will loop, and the loop looks like a transient failure.
- **Redraft** means producing different content and submitting that. Sometimes viable, sometimes not.

So the second question gets its own field, `disposition`, with three values: `allow`,
`redirect_refinable`, and `redirect_futile`. A content-class breach is often refinable, because
different words may comply. An act-class breach usually is not, because no rewording creates an
approval token. `test_disposition_is_derived_from_category_in_one_place` holds that this mapping lives
in exactly one function rather than being re-derived at each call site, and
`test_the_futile_set_and_both_redirect_dispositions_are_named_only_in_disposition_for` holds that no
other module hardcodes the categories.

A refusal that carries an allow disposition is recorded as denied, not allowed:
`test_a_refusal_carrying_an_allow_disposition_is_recorded_as_denied`. The verdict wins over the
annotation.

---

## 5. Purity, enforced three ways

`src/chaperone/policy/` imports no LLM client, no I/O, and no clock. If a predicate needs a value, the
value arrives as an argument. This is why `ActContext` carries `sent_count` as a field rather than
counting anything, and why nothing in `policy/` knows what time it is.

Three mechanisms hold it, at three different moments:

1. **Edit time.** A `PreToolUse` hook (`tools/guard_edit.py`) refuses the edit as you write it.
2. **CI time.** `tools/static_audit.py` walks the import graph and fails the build.
3. **Test time.** `test_the_real_policy_package_is_pure` asserts it against the real package rather
   than a fixture.

Three mechanisms for one property is not redundancy for its own sake. The edit-time hook catches it
when fixing is cheapest, CI catches what was written around the hook, and the test states the property
in the place a reader looks for properties.

**Why it matters beyond tidiness:** the purity is what makes "zero by construction" a claim rather than
a hope. A pure function over explicit arguments has no hidden input that could vary, no network call
that could fail open, and no clock that could make yesterday's answer different from today's. The
moment `policy/` can call a model, every act-class claim downgrades to a measured rate.

---

## 6. Agents and grants

**Designed, not built.** There is no agent registry anywhere in `src/`. This section describes the
intended topology; the enforcement primitive beneath it is built and is described at the end.

| Agent | Tools granted | Blast radius |
|---|---|---|
| Research | Read-only enrichment, internal writes | **Low.** Nothing leaves. |
| Matching | None. A module, not an agent. | **Low.** A wrong ranking wastes attention. |
| Drafting | Draft composition only, **no send tool** | **Medium.** Output reaches a human first. |
| Conversation | Send, gated | **High.** The only agent that can breach a stated constraint. |

The row that carries the idea is Drafting. An agent that cannot send is an agent whose worst output is
a bad draft. Most of the risk in this system is concentrated in one agent with one tool, which is what
makes the gate on that tool worth building carefully.

Tool counts stay small per agent, because a wide tool surface degrades selection accuracy. Where two
tools share an input shape, their descriptions carry mutual boundary clauses of the form *"do not use
this for X, use Y"*.

**What is actually built:** `granted_tools` on `ActContext`, checked by a pure function in
`src/chaperone/policy/act_classes.py` that returns `act:tool_outside_grant` for any tool outside the
grant, enforced at both `pre_tool_use` and `guarded_call`. That primitive is what makes the topology
above enforceable rather than advisory, and it is the part that exists today.

---

## 7. The capability ladder

**One transition, and a ceiling.** `src/chaperone/gates/ladder.py` holds the demotion transition, the
per-surface tier verbs, and the tier-2 content ceiling that `CONTENT_CEILING` names. **No promotion is
wired to any outcome**, and `test_nothing_under_src_wires_an_outcome_to_promotion` holds that by AST
scan rather than by memory. Tiers otherwise appear as an `ActContext` field that act-class predicates
read, and `act:no_approval_token` fires when tier 2 or above lacks a token.

A surface the ceiling table does not name is refused rather than given the top tier. The expression
that shipped first returned tier 3, the unsupervised rung, for every input that was not the exact
`Surface.CONVERSATION` member, and `Surface` subclasses `str`.

Tier verbs are defined **per task class**, because "send outward" is meaningless for a read-only
surface.

| Tier | Conversation surface | Research surface | Gate |
|---|---|---|---|
| 0 | Draft only | Read only | Default |
| 1 | Send to internal review queue | Propose enrichments | Passes the suite at threshold |
| 2 | Send outward, per-message approval | Write enrichments, per-record approval | Sustained pass over N cases |
| 3 | **Unreachable in this artifact** | Write enrichments autonomously, sampled audit | Sustained pass, zero boundary violations |

### 7.1 Why the ceiling is the honest answer

**Content-class autonomy caps at tier 2.** No outward-sending surface reaches tier 3 here.

A checker false negative at an unsupervised tier **is** an unsupervised breach. Sampled audit catches
it after transmission, which is detection, not prevention. And demotion is itself triggered by a
verdict, so a missed violation also misses its own demotion: the same residual, closed by the same
ceiling.

The asymmetry is the argument. With deterministic gating, a violation rate is zero by construction.
With a probabilistic detector, it is a non-zero rate you can measure and cannot guarantee. That is
precisely why probabilistically-gated surfaces cap at approval and unsupervised operation is reserved
for deterministically-gated ones.

**The ladder loses its top rung on the most interesting agent. That is the honest price of a
model-based detector on a constrained surface**, and it is the reason the measurement layer exists at
all rather than being decoration.

### 7.2 Promotion, if it were built

Production promotion is keyed to human-review outcomes on real cases, never to synthetic suite scores.
An artifact that promoted itself to autonomous operation on the strength of its own evals would model
exactly the judgment error it was built to argue against.

---

## 8. Policy is readable, never writable

The model may **read** the constraint set, which is what would let a refinement loop self-correct
against the actual rule rather than against a guess. No tool mutates it.

Stated as a choice rather than a constraint: the in-process surface exposes tools only, so policy would
ship as a read-only `read_policy` tool. An external server process could publish it as a first-class
read-only resource instead. The invariant, that no tool mutates policy, is identical either way, and
the in-process route is chosen for a smaller footprint rather than because the alternative is
unavailable.

**Status: designed, not built.** No tool surface exists in this tree.
