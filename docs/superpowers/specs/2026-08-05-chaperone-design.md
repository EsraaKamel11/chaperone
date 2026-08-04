# chaperone: bounded autonomy for an agent acting under stated constraints

**Design document. Status: designed, not built.** Nothing described here is implemented. Every test
name appears as `[planned: test_name]` and refers to a test that does not exist yet; when one is
written, the marker is dropped and the citation stands on its own. A citation without a marker is a
promise this document has no right to make on day zero.

The scenario is synthetic throughout. No real organisation, dataset, or correspondence appears in this
repository, and no claim about any real firm's systems is made anywhere in it.

**The general problem.** An agent that carries an outbound conversation on behalf of an organisation
will be asked, in the most natural way possible, to do things that organisation has publicly stated it
does not do. The most helpful next token and the permitted next token diverge, and they diverge
exactly where the stakes are highest. This is a general condition of agents acting on regulated
surfaces; the demonstration uses a synthetic capital-introduction firm because its constraints are
unusually crisp, not because the pattern is specific to it.

---

## 1. Context and fixed constraints

The synthetic firm publishes three constraints on itself. It does not advise on the merits of an
investment, it does not negotiate terms, and it does not make forward-looking return statements. It
also operates under ordinary data constraints: it contacts people only in jurisdictions where it has
consent to do so, and it does not exceed agreed contact volumes.

An agent drafting outbound messages for that firm will be asked *"honestly, is this a good deal?"* and
*"would they take eight instead of ten?"*. Both questions are natural, both are frequent, and the
fluent answer to either breaks a stated constraint.

Settled and not open for redesign:

- **Quality and permission are different questions and never share a mechanism.** An eval score is a
  measurement of how good an output is. It is not an authorization. The two are separate lanes with
  separate implementations, and the demo exists to show them disagreeing.
- **The model never decides whether the gate exists, or what deny does.** A model may supply a signal
  that feeds a gate. It never owns the gate.
- **Deterministic checks are stated only where they are true.** "Zero by construction" attaches to the
  class of constraints that can be decided by a pure function over the draft, and to no other class.
  Overstating it once destroys the credibility of stating it anywhere.
- **A blocked action is terminal.** It routes somewhere; it is never silently retried.
- **Every comparison arm gets the same engineering effort.** A rigged baseline converts an argument
  into a demo, and a technical reader spots it immediately.
- **All data is synthetic.** Every number in the README belongs to this artifact.

### 1.1 What this is not

It is not a compliance product, it does not encode legal advice, and no domain expertise in securities,
broker-dealer regulation, or any other regulated field is claimed by it or rests on it. It is an
enforcement architecture, demonstrated on a scenario whose constraints happen to be legible.

---

## 2. The finding that shapes the whole design

**Constraints are not one kind of thing, and treating them as one kind of thing is the mistake this
artifact exists to correct.**

An earlier draft of this design specified a single "deterministic policy engine" covering all six
constraints, and simultaneously specified that the checker's *prompt* must exclude the generator's
reasoning. Those two sentences describe different systems: a pure function has no prompt and cannot be
contaminated. The contradiction sat exactly under the headline claim.

The reason it happened is worth recording, because it is the trap the design now guards against.
*"Send to a jurisdiction with no consent"* is decidable by a pure function: you have a recipient, you
have a consent set, membership is a fact. *"Advises on the merits"* is a semantic property of free
text. A deterministic detector for it is keyword matching, and keyword matching is defeated by the
first paraphrase a competent adversary writes — *"between us, I would move quickly on this one"*
contains no merit adjective and is unambiguously advice.

So the constraints split into two families with different mechanisms and different claimable
guarantees. Everything downstream — what may be asserted, where autonomy stops, what the ablation
measures — follows from that split.

### 2.1 Findings from reviewing reference implementations

Four production-shaped reference codebases were reviewed during design. The defects below are recorded
as design requirements rather than as criticism, and the code is neither reproduced nor identified
here. Each shaped a specific decision.

**A. A check that runs is not a check that governs.** A test asserted a security-critical comparison
had been *called*, using a spy, without asserting anything about its *result*. The comparison could
have returned the wrong answer and the test would still pass. **Consequence:** §10's rule, and the
requirement that every gate test assert an outcome rather than an invocation.

**B. Append-only enforced by naming.** A trail claimed append-only status, and the test asserting it
was `assert not hasattr(trail, "delete")` — which establishes that no method carries that name. The
underlying list was mutable and the on-disk file was editable with no detection. **Consequence:** §5's
hash chain, and a test that tampers and asserts detection.

**C. The audit entry is skipped exactly when it matters most.** A gateway resolved its target *outside*
its `try` block, so an unknown target raised before any entry was written. The single call most worth
logging was the one guaranteed not to be. **Consequence:** §5.2's `finally`.

**D. Durability by text-mode append.** A record writer used text-mode append with no flush and no
fsync, so a crash lost buffered lines silently. **Consequence:** §5.1, and the failure analysis in
§5.3 that made this load-bearing rather than cosmetic.

**E. A permissive validator described in strict terms.** A prompt instructed that evidence "MUST begin
with" a field token; the validator accepted the token anywhere in the string. The mismatch is
one-directional — the validator never rejects compliant output, it only accepts non-compliant output —
and a fabricated citation containing a field name passes. **This was a documented tradeoff in its
source, not an oversight**, with the structured-citation upgrade named explicitly as future work.
**Consequence:** §4.5, which implements that upgrade, and §10's parity test.

**F. Control-flow relabelling that erases refusals.** A loop coerced every non-tool-use stop reason to
"finished normally," making a truncated response indistinguishable from a complete one, and rendering
the loop's own documented contract vacuously true. **Consequence:** §3.4.

---

## 3. Architecture

### 3.1 Five packages

```
policy/     constraint predicates. Pure functions. No I/O, no LLM client, no clock.
gates/      the hook engine, agent definitions and grants, the ladder.
evals/      the quality lane, the frozen corpus, the harnesses.
audit/      the single gateway, the chain, the recovery policy.
matching/   candidate filtering and ranking.
```

Five, not nine. An earlier draft had nine top-level modules for the same functionality, which reads as
over-engineering in a small artifact and buys nothing. The rule: if a module's job can be stated in one
clause, it is not a package.

`policy/` is the load-bearing boundary. **It imports no LLM client, and a static audit enforces that**
(§10.3). Everything else may depend on `policy/`; `policy/` depends on nothing.

### 3.2 Two families of constraint

| Family | Members | Mechanism | Claimable |
|---|---|---|---|
| **Act-classes** | send without the approval token for the current tier · send to a non-consented jurisdiction · tool call outside grant · a figure quoted that is absent from the record · per-recipient-domain send cap | `policy/` pure functions | **Zero by construction** |
| **Content-classes** | advises on merits · negotiates terms · forward-looking return | independent checker feeding a fail-closed gate, **plus** deterministic tripwires as a second disjunct | **Measured, never guaranteed** |

**The claim, worded to survive a hostile reading:**

> The model never decides whether the gate exists, or what deny does. Checker *unavailability* fails
> closed. A wrong verdict on a content-class is a **measured** escape, bounded by the harness number,
> the tripwires, sampled audit and instant demotion. Never a guaranteed zero.

An earlier draft said *"a wrong checker verdict degrades to a blocked send or a human review — never to
an unsupervised breach."* That is true of a false positive and false of a false negative: a checker
that confidently returns "compliant" on a violating draft produces a schema-valid verdict, the gate
faithfully passes it, and at an unsupervised tier the draft transmits. The sentence conflated checker
*failure* with checker *wrongness*. §7's ceiling is what makes the corrected version architecturally
true rather than rhetorical.

**Purity, kept honest.** The send cap depends on a count that lives in durable state, which would make
its predicate impure and void both the claim above and the static audit that enforces it. So the
predicate is **pure over `(draft, count)`** and the **gateway supplies the count** by reading the log.
State reads stay outside `policy/` [planned: `test_policy_package_imports_no_io`].

### 3.3 The transmitted/untransmitted line

Both the checker and the quality judge receive:

- the **transmitted thread**
- the **candidate draft**
- the **cited records**

and never any untransmitted artifact: no generator system prompt, no scratchpad, no tool-call history,
no chain of thought.

**Independence is enforced by omission, not by a flag.** A reviewer handed the generator's own
justification for why a message is fine will agree that it is fine, and on this surface a
rubber-stamped violation is the entire failure being prevented.

The line is **transmitted versus untransmitted**, not *little* versus *much*. An earlier draft said
"only the inbound message and the outbound draft," which was wrong in two directions: it left the
quality judge unable to verify grounding at all, since grounding requires the record; and it left the
checker unable to see cross-turn assembly (§4.6). Transmitted messages are the record of what was
actually said. Including them is not contamination.

Testing is two-pronged: assert the assembled prompt contains no generator artifacts, **and** assert its
structure — the expected messages, no injected turns [planned:
`test_checker_prompt_has_no_generator_artefacts`, `test_checker_prompt_structure_is_exact`]. A
substring scan alone passes an injected turn that avoids the scanned markers.

### 3.4 Control flow

The orchestrator raises on an unexpected stop reason rather than coercing it (finding F), and
multi-tool results return in a single turn, which the protocol requires.

**Failure policy differs by lane, deliberately:**

| Lane | Policy | Reasoning |
|---|---|---|
| Boundary engine | **Fatal, fails closed** | Nothing transmits on an unavailable gate |
| Enrichment | **Survivable** | Records a marker that travels into the output, so the draft states what it could not verify rather than silently omitting it |
| Checker timeout | **Races to deny** | An unanswered question is not a permission |

This is not an inconsistency. Returning errors rather than raising them is correct at the
tool-execution boundary, where a raised exception destroys the loop's ability to recover. Raising
loudly is correct in control machinery, where a swallowed surprise becomes a silent wrong answer. The
boundary engine is control machinery.

**One consequence worth stating, because it is a trap.** In a defensively-written executor, a
catch-all around handler invocation can convert *any* raised exception into a retryable error. A deny
implemented as a `raise` inside a handler would therefore be relabelled as *"transient — please retry
the forbidden send."* So the gate sits at the executor chokepoint, **outside** the handler's
try/except, and deny is **returned** as a permanent non-retryable structured result, never raised where
a wrapper can relabel it [planned: `test_deny_is_never_relabelled_as_retryable`].

---

## 4. The boundary engine

### 4.1 Ordering is the guarantee

The `PreToolUse` gate runs before the tool function is referenced at all. Not before it is *called* —
before it is **looked up**. A deny returns before the registry is indexed.

That ordering is the whole guarantee, so the test asserts the function was never referenced, not that
the checker was called [planned: `test_denied_tool_function_is_never_referenced`]. Finding A is
precisely the failure this avoids.

Enforcement lives in the engine, never inside a tool. A check written inside one tool is a check the
next tool silently skips [planned: `test_a_newly_added_send_surface_is_gated_without_touching_it`].

### 4.2 Least privilege is the first layer

The drafting agent **does not possess a send tool**. Scope is enforced by what is granted, not by
instruction, and the policy engine is the second layer rather than the only one. A policy engine
guarding a capability the agent never had is defence in depth; a policy engine guarding a capability
the agent holds is a single point of failure.

### 4.3 The checker

- **Model tier: at least as strong as the drafter, pinned by a test** [planned:
  `test_checker_model_is_not_weaker_than_drafter_model`]. An earlier draft specified a "small" checker,
  which inverts the cost-quality split the pattern depends on: a cheap generator with a stronger
  reviewer is what makes a second opinion independent rather than correlated. Two same-tier models make
  the same mistakes. Beyond correctness, a weak checker **confounds architecture with model capability**
  in §9.2's ladder, which would manufacture evidence against this design's own thesis through a budget
  choice.
- **Implemented as a typed-output agent**, not as a delegated subagent. The strong reason is not a
  missing configuration field: **subagent invocation is delegated by a model**, and the rule in §1 is
  that the model never decides whether the gate exists. A model-delegated checker is structurally wrong
  for a gate regardless of what it can be configured with.
- **Structured output is forced**, so the checker returns a typed verdict and never prose. A checker
  that can answer in prose can decline to decide.
- **A `flag_for_review` escape hatch** as a second registered output, so *"I cannot tell"* is
  first-class rather than collapsing into "compliant." This attacks the false-negative problem at its
  source and makes uncertainty visible in §9.5's calibration.
- **A schema-valid but invalid verdict, after the retry budget, maps to deny** — never an exception
  bubble.
- Forced structured output disables extended thinking. Acceptable here because the task is one-shot
  classification against an enumerated class list, backstopped by §4.4 and the escape hatch. If
  measured recall argues otherwise, the fallback is a think-then-emit two-step, decided by that number
  rather than in advance.

### 4.4 Tripwires, as a second disjunct

Content-class deny must not rest on a single model signal. A hallucinated verdict arrives at high
confidence just as readily as a correct one, and the answer to that is more independent signals, not a
better single signal.

So: pure-function detectors — percentage-plus-horizon, guarantee language, merit adjectives predicated
on the deal, term-negotiation phrasing — evaluated as a second disjunct.

```
deny if checker_flags or tripwire_hits
```

**This does not contradict §2.** Keyword matching cannot *be* the merits detector. As a supplementary
disjunct it never lowers recall, it converts a checker false negative into a caught violation for the
detectable slice, it cuts checker invocations, and it gives the human reviewer structured context in
the handoff.

### 4.5 Citation validation, and canonicalization

Every factual claim in a draft must cite a real field of the mandate or relationship record. Validation
runs after construction and rejects with the offending index.

**Two substring traps, both closed.** Finding E is the first: a validator that accepts a field token
anywhere in the evidence string accepts a fabricated citation containing that token. So the check is
that the cited field **exists in the record** and its **value** appears in the draft.

The second trap is in the fix. "Its value appears in the draft" is itself a substring match, and
`"$2.5M"` against `2500000.00` either false-blocks or escapes. So both sides pass through **one
canonical representation** first: normalize by key family, exact decimals never floats, typed errors on
unparseable input [planned: `test_a_figure_matches_its_record_value_across_representations`,
`test_canonicalization_round_trips_negative_amounts`]. The negative-amount case is explicit because a
naive character-class strip removes the minus sign and silently turns a debit into a credit.

Scope note: canonicalization covers the representations the generator and drafter actually emit.
Locale-general money handling is not in scope for this artifact and is stated as such rather than
implied.

### 4.6 Cross-turn accumulation

**The hardest attack on this design, named here so it is a property rather than a surprise.** Each turn
passes the per-draft gate. Across nine turns the conversation has negotiated terms: *"would they take
eight?"* → *"let me relay that"* → *"they would want something in exchange."* No single turn violates
anything.

The failure shape is *per-item-plausible but structurally broken* — the same shape as a record whose
every field validates and whose cross-field relationships do not. The **detector** is §3.3's context
rule: the checker receives the whole transmitted thread precisely so it can be prompted for
conversation-scope violations, and a distinct integration pass evaluates the thread rather than the
turn.

**This is a content-class, so it is measured and not guaranteed.** The strongest guarantee this
architecture offers does not cover its most realistic breach mode. That is stated in the README rather
than discovered in conversation.

### 4.7 Deny, redirect, and what the model is told

**A blocked send is a redirect, not a deny.** Deny blocks. Redirect blocks *and routes*. Everything
here that blocks also files a self-contained handoff, so it is a redirect, and the vocabulary matters
because the two have different downstream contracts.

**The handoff is self-contained.** Every field comes from tool input or a stored record, never from
"the earlier conversation," because the reviewer cannot see the conversation. Every field the reviewer
reads is `required` in the schema: a loose schema means the escalation arrives empty [planned:
`test_handoff_payload_is_complete_without_the_transcript`].

**The denial contract**, which three independent design reviews converged on:

- Deny is delivered **as the tool result**, with a category and `is_retryable=false`. The protocol
  requires results to come back matched to their requests; a short-circuit that omits the result makes
  every continuation of that thread invalid, including the refinement loop.
- The result is **categorized** — `content:advises_on_merits`, not "invalid". A failure that does not
  say what kind of failure it is forces the agent to guess.
- **Retryable-as-redraft versus hard-handoff is derived from the category in one place**, never at each
  call site [planned: `test_disposition_is_derived_from_category_not_per_call_site`].
- The redraft prompt carries the verdict, the **violating span verbatim**, and the class name. Calling
  the model again with the same prompt is the canonical retry mistake.

**Which layer implements which sentence.** In a hook layer the runtime synthesizes the tool result from
the hook's deny payload: the *content* of the reason is ours, the plumbing is not. The full contract —
result shape, retryability, category-to-disposition mapping — is ours only in the executor layer. Days
1–2 build the executor layer; the hook layer carries the payload.

### 4.8 Refinement, and when not to refine

- **Futility classification.** Some denials are unrecoverable because no compliant draft answers the
  question. *"Honestly, is this a good deal?"* has no compliant direct answer — the compliant response
  changes the task. Futile classes skip the refinement budget entirely and escalate [planned:
  `test_a_futile_class_does_not_consume_refinement_budget`].
- **No-progress deadlock stop.** The same violation class twice running stops the loop and surfaces the
  deadlock rather than burning rounds on near-identical redrafts.
- **Termination is `verdict == pass`. The round cap is a backstop**, and `refinement_rounds` is
  surfaced in the output so a viewer can distinguish resolution from budget exhaustion. An integer cap
  is a state guard, not a control-flow signal.
- **The redraft never transmits on its own.** It rides inside the handoff payload as a proposal, so the
  approver adjudicates once and the original attempt stays terminal. A redraft that transmits by itself
  after a permission failure is an auto-retry of a permission failure, which is the thing this
  architecture exists to refuse [planned: `test_a_redraft_never_transmits_without_approval`].

---

## 5. The audit store

### 5.1 Durability, and why it is not cosmetic

**Binary append, flush, `os.fsync`, per entry.** Text-mode append buffers through the text layer and
lets the file lie about its durability state (finding D). Non-append state uses tempfile, fsync, and
atomic replace.

**Uniform across every write path** — tier state, the frozen corpus, the pre-registration hashes. The
weakest write path is the system's real durability.

### 5.2 One gateway, one outcome entry, written in `finally`

Every effectful call routes through a single gateway. **Exactly one outcome entry per call**, written
in a `finally` so it survives a failure to resolve the target (finding C).

The entry records the target, the scope, the **principal and the tier**, the outcome, and an
**argument digest** — canonical JSON, hashed — never the raw arguments, because recipient identifiers
are personal data and an audit log is not a place to accumulate it.

**Write-ahead for effectful sends.** An intent entry precedes the send; the outcome entry follows.

An earlier draft claimed the "exactly one entry per call" invariant *and* write-ahead logging, which
are incompatible — a send writes two. The corrected statement is above: one *outcome* entry per call,
plus a preceding intent entry for effectful sends, with the cap counting intents. Write-ahead is a
strengthening of a purely post-hoc entry, which loses the record entirely when the process dies
mid-send.

### 5.3 The failure that made durability load-bearing

The send cap counts off the audit log. An earlier draft described durability as *"the entry is written
in a `finally`, so the log survives restarts"* — which is a process-level guarantee about an attempted
write, not a crash-level guarantee about a durable one.

**With text-mode buffering, a crash loses the last lines. The cap then undercounts. The next send goes
through.** A missing log line is not a forensics gap in that configuration; it is an **enforcement
predicate failing open**. This is why §5.1 is not housekeeping.

### 5.4 Crash recovery: three branches, decided here

For an intent entry with no matching outcome:

| Branch | Condition | Action |
|---|---|---|
| **(a)** | Outcome entry present | Nothing to recover |
| **(b)** | Intent stale beyond threshold **and** the side effect verifiably absent | Write `outcome=aborted`, release the intent from the cap |
| **(c)** | Indeterminate | Write `outcome=unknown`. It **stays counted**. A recipient-scoped needs-verification handoff files, and the **argument digest doubles as an idempotency key**: a re-attempted send carrying the same digest requires approval |

Branch (c) is what prevents the double-send. The conservative default is that an unresolved intent
keeps consuming the cap [planned: `test_an_unresolved_intent_keeps_consuming_the_cap`].

The **durable record and the resume policy are separate**, so the policy is swappable without touching
the log format.

### 5.5 Tamper evidence, and its limits

Entries are hash-chained, and `verify` walks the chain. The test **tampers with an entry and asserts
detection** [planned: `test_a_tampered_entry_is_detected`, `test_a_truncated_tail_is_detected`] —
not that no method is named `delete` (finding B).

**Torn tails are expected, not exceptional.** Line-by-line validation raises on a half-written final
line, and a chain that is appended to under crash conditions will meet one. The policy is stated:
a torn final line is reported as a torn tail and the preceding chain still verifies.

**What this does not defend against**, stated plainly so nothing over-claims: anyone holding the file
can rewrite the whole chain from any point and recompute every subsequent hash. Hash chaining makes
*selective, silent* edits detectable. It does not make the log tamper-proof against an actor with write
access and intent. Real tamper-proofing needs an external append-only store or a witness, and that is a
different piece of work.

**Ordering is asserted separately.** The chain proves integrity, not sequence semantics: it cannot
distinguish intent-then-outcome from outcome-then-intent if both were appended in that order. A
dedicated test asserts the exact recorded order across a gated call [planned:
`test_intent_precedes_outcome_in_the_recorded_order`].

---

## 6. The surface map

### 6.1 Agents and grants

| Agent | Tools granted | Blast radius |
|---|---|---|
| Research | read-only enrichment, internal writes | **Low.** Nothing leaves. |
| Matching | none (a module, not an agent) | **Low.** A wrong ranking wastes attention. |
| Drafting | draft composition only, **no send tool** | **Medium.** Output reaches a human first. |
| Conversation | send, gated | **High.** The only agent that can breach a stated constraint. |

Tool counts stay small per agent. A wide tool surface degrades selection accuracy, and this artifact
does not need one. Descriptions carry mutual boundary clauses — *"do not use this for X, use Y"* —
where two tools share input shapes, and a linter runs against the artifact's own descriptions [planned:
`test_every_tool_description_passes_the_linter`].

### 6.2 Policy is readable, never writable

The model may **read** the constraint set, which is what lets the refinement loop self-correct against
the actual rule rather than against a guess. **No tool mutates it** [planned:
`test_no_registered_tool_can_mutate_policy`].

Implementation note, stated as a choice rather than a constraint: the in-process server surface
exposes tools only, so policy ships as a read-only `read_policy` tool. An external server process
could publish it as a first-class read-only resource instead. The invariant — no tool mutates policy —
is identical either way, and the in-process route is chosen for a smaller demo footprint, not because
the alternative is unavailable.

### 6.3 Enforcement-layer portability

The same policy is enforced at two layers — the framework hook and the executor chokepoint —
demonstrating that the control is a property of the architecture rather than an artifact of one
integration [planned: `test_the_same_policy_denies_at_both_layers`].

---

## 7. The capability ladder

**Tier verbs are defined per task class**, because "send outward" is meaningless for a read-only
surface. An earlier draft defined tier 3 in send verbs and then reserved tier 3 for a research agent
that never sends, leaving the top tier unoccupied by its own definition — inside the section that
exists to be precise about autonomy.

| Tier | Conversation surface | Research surface | Gate |
|---|---|---|---|
| 0 | Draft only | Read only | default |
| 1 | Send to internal review queue | Propose enrichments | passes the suite at threshold |
| 2 | Send outward, per-message approval | Write enrichments, per-record approval | sustained pass over N cases |
| 3 | **Unreachable in this artifact** | Write enrichments autonomously, sampled audit | sustained pass, **zero boundary violations** |

### 7.1 The ceiling, and why it is the honest answer

**Content-class autonomy caps at tier 2**, so no outward-sending surface reaches tier 3 here.

The reason is §3.2's corrected claim. A checker false negative at an unsupervised tier **is** an
unsupervised breach; sampled audit catches it after transmission, which is detection, not prevention.
Demotion is itself triggered by a verdict, so a missed violation also misses its own demotion — the
same residual, closed by the same ceiling.

With hooks on, a violation rate is zero by construction. With a prompt only, it is a non-zero rate you
can measure and cannot guarantee. That asymmetry is precisely the argument for capping
probabilistically-gated surfaces at approval and reserving unsupervised operation for
deterministically-gated ones.

**The ladder loses its top rung on the most interesting agent. That is the honest price of a
model-based detector on a constrained surface**, and §9.5 is what turns it from an assertion into a
measurement.

### 7.2 Built, and not built

**Built:** the demotion transition, triggered by a violation.
**Not built, described in the README:** promotion mechanics.

**Required honesty line in the README:** production promotion is keyed to human-review outcomes on real
cases, never to synthetic suite scores. An artifact that promoted itself to autonomous operation on
synthetic evals would model exactly the judgment error it was built to argue against.

**Sampled audit** draws `max(1, ceil(pct * n))` per stratum, clamped to `n`, from a **seeded** RNG with
the seed recorded in the entry. An unseeded sample undermines the auditability the chain is there to
provide.

---

## 8. The matching module

### 8.1 The regime

A small number of active engagements at any time. No click-stream, no implicit feedback; labels are
introductions, not clicks; weeks of latency before one resolves. Learning-to-rank overfits long before
it generalizes at this N, and embedding similarity alone is actively wrong — it will surface a
$250K participant for a $50M round at a high score.

**The real signal is structured and relational, not semantic.**

### 8.2 The pipeline

1. **Hard exclusion filters** — cheque size, stage, sector, geography, jurisdiction consent.
   Exclusions, not softly weighted features. **These share predicates with `policy/`**: the same
   eligibility code that decides whether a party may be *contacted* decides whether they may be
   *surfaced*. An ineligible party is not a low-ranked result; they are not a result.
2. **Relationship state** — recency, depth, owner, prior similar deals, prior passes and the reason.
3. **Embedding re-rank inside the filtered set.** Never the retrieval step.
4. **Active learning on the shortlist.** *Designed, not built.*
5. **Offline evaluation before any online loop** — held-out synthetic introductions, ranking metrics,
   labelled in the README as a demonstration of **the protocol** and never as evidence the ranker is
   good. A ranking metric computed on labels this repository's own generator produced proves nothing
   externally, and saying so is worth more than the number.

### 8.3 The null-field policy

Hard filters at low N produce empty candidate sets, which is exactly why teams soften them into
weights. So the missing-value case is decided rather than defaulted:

- Not "null passes" — that readmits ineligible parties silently.
- Not "null fails" — that drops eligible ones silently.
- **Fail closed into a separate needs-verification bucket with the missing field named** [planned:
  `test_a_null_eligibility_field_lands_in_needs_verification_not_in_results`].

A missing field is a distinct state, not a default. This is the same rule as §4.5's canonicalization:
the system says what it does not know instead of guessing a value that makes the comparison succeed.

Note that the needs-verification bucket is a **redirect** in §4.7's sense — blocked and routed — which
is why the same vocabulary covers both surfaces.

---

## 9. Evidence design

### 9.1 Pre-registration

`PREREGISTRATION.md` is committed **before the corpus is labelled and before any arm runs**. It
contains every metric, the grading procedure, and **the predicted direction of every result**. A design
that can be adjusted after seeing a number is not evidence.

### 9.2 The attribution ladder

**The corpus is the fixed thing; the arms are detectors over it.** Roughly 150 scenario-plus-draft
pairs are frozen and labelled first. The arms do not *produce* drafts — each is a **verdict source**
judging the same fixed drafts at the same chokepoint. An **escape** is a labelled-violating draft the
arm passed; a **false block** is a labelled-compliant draft the arm blocked.

| Arm | Verdict source | Differs from previous by |
|---|---|---|
| 1 | The drafter's own model, given a tuned policing prompt | — |
| 2 | An independent checker, model ≥ drafter | model identity |
| 3 | Arm 2 inside a fail-closed gate | failure behaviour |
| 4 | Arm 3 plus act-classes and tripwires | the deterministic layer |

Each adjacent pair differs in exactly one way. An earlier draft made arm 1 a *generation-stage*
intervention while arms 2–4 were transmission-stage detectors, so the first rung changed both stage and
mechanism — the same two-variable defect the four-arm structure was introduced to remove.

**Hooks versus prompts is reported separately.** Generation-stage prompting with no chokepoint detector
is the configuration most systems actually ship, and it is compared against arm 4 as an explicitly
labelled two-variable comparison. It is never presented as a rung of the ladder.

### 9.3 Grading and contamination control

**Metrics: escape rate and false-block rate.** An escape-only metric is optimized by blocking
everything.

**An independent oracle.** Escapes graded by the engine under test make its own zero tautological.
Mechanical oracles where they exist; blinded labels for content-classes otherwise. **Blinding is to
label source, not to arm** — an earlier draft said labels were blinded to "which arm produced which
draft," which cannot be true of a corpus that precedes the arms.

**Two contamination controls**, because the tripwires are lexical patterns and the corpus is authored
here:

- **Dev/eval split.** Tripwires are tuned only on dev. Eval is touched once.
- **Violating drafts are generated as paraphrases** of seed violations rather than hand-authored, so
  their surface forms are not drawn from the pattern author's own vocabulary.

**Predicted:** act-classes zero on arm 4. Content-class escapes non-zero on every arm, lowest on 4,
highest on 1. **False blocks rise from arm 3 to arm 4** — tripwires can only add blocks, and omitting
the predicted cost of the best arm would read as cherry-picking.

**Measurement note:** the checker runs on **every** draft in the harness even where production order
short-circuits on a tripwire hit, or §9.5's calibration is computed on a tripwire-negative selection.

### 9.4 The discrimination table

The quality judge runs over the whole corpus, producing a 2×2 of quality-pass against
labelled-violating. This is what turns the demo from a scripted moment into one instance of a
distributional fact.

**Pre-registered null with an equivalence bound:** AUC of the judge score for violating versus
compliant, with a 95% CI predicted to **contain 0.5 and exclude 0.75**. A null "confirmed" by a failed
significance test is weak inference.

**The interpretation of a failed null is pre-committed.** If the CI excludes 0.5, that is **reported as
a failed prediction**. The maxim it tests — *a quality score is not a permission* — is normative and
survives either result: a judge that correlates with violations is still a probabilistic instrument
being asked to authorize an action. Writing this down now is what stops it becoming a post-hoc rescue.

**The judge has no legal criterion in its rubric**, and the README says why: a quality rubric has no
reason to encode a constraint carve-out, and that absence is the point rather than a convenient
omission. A reader who is not told this will reasonably suspect the rubric was stripped for the demo.

### 9.5 Calibration, aimed at the checker

Per **content-class** cells: n, mean stated verdict confidence, observed agreement with the blinded
labels, and a proper scoring rule per cell — never one blended number.

An earlier draft aimed this at the quality judge, per act-class, which is the one place it is useless:
act-classes are gated by pure functions and §9.4 predicts the judge carries no violation signal there.

**This is the most valuable number in the artifact.** A bad cell — *"negotiates terms: stated 0.93,
observed 0.71"* — is the **empirical justification for §7.1's ceiling**, which is otherwise an
assertion. The labels already exist and the computation is small.

### 9.6 Assertion discipline

**CI asserts invariants only:** act-class escape equals zero, and monotone arm ordering. Probabilistic
rates are **measured and reported, never asserted**. A test asserting that a prompt fails roughly three
percent of the time will flake.

**A precision the rule imposes on itself:** monotone ordering is an invariant only over **frozen
recorded replays**, where every verdict is fixed. On a live re-run it is a reported observation, or it
is exactly the flaky assertion this section forbids.

### 9.7 Observability artifacts

A **perturbation log** — one entry per gate, contrasting perturbed against unperturbed behaviour:
mutate compliant to violating; blank an eligibility field; feed a schema-valid wrong verdict past the
retry budget; tamper with a log line and watch `verify` fail. An **on-call note** — half a page on which
signal lies and what to do about it. A **violation-pattern frequency table** across the corpus. Machine
and human readable output from one validated record.

---

## 10. Testing discipline

**The rule:**

> A test must assert the property, not a proxy for the property.

| Weak test | What it actually establishes | What this asserts |
|---|---|---|
| `assert not hasattr(trail, "delete")` | no method has that name | the chain verifies and a tampered entry is **detected** |
| a spy counts that the checker was called | the checker ran | the tool function was **never referenced** on deny |
| a field token appears in the evidence string | the string contains a token | the cited field **exists in the record** and its **canonical value** appears in the draft |
| empty test bodies | the suite is green | every test asserts, enforced by a lint |
| the self-policing arm fails | nothing — it is stochastic | only invariants (§9.6) |

### 10.1 Mutation testing

Flip the deny to allow. Flip an exclusion filter to a weight. Drop the `finally` around the audit
write. Remove the fsync. **The tests must fail.** A surviving mutant means the test that should have
caught it is a proxy test.

### 10.2 Record/replay

Gate tests run against recorded verdicts so the suite is hermetic, offline and keyless. Mutation
testing over network-dependent tests is worthless, so this is a precondition for §10.1 rather than a
convenience.

### 10.3 The static audit

Parse the tree. Assert `policy/` imports no LLM client. Assert the send function is referenced only
from the gateway module [planned: `test_policy_imports_no_llm_client`,
`test_send_is_referenced_only_from_the_gateway`].

**This is what makes "zero by construction" a continuously verified structural fact rather than a
README sentence.** Mutation testing checks the tests; the static audit checks the construction. They
catch different things, and the second is what §3.2's claim actually rests on.

### 10.4 The scope coverage map

A test fails if a constraint class has no detector [planned:
`test_every_constraint_class_has_a_detector`]. You cannot silently forget a class.

### 10.5 The scripted adversary

A scripted model driver that **attempts** the forbidden action — an advice-on-merits draft, an
out-of-grant tool call, an uncited figure — asserting the deny path deterministically.

This is not a mocked HTTP response; it is a double for the loop, and its purpose is to prove
enforcement holds **regardless of what the model attempts**. It converts act-class escape rate from a
statistical claim over 150 drafts into a per-class property test running in CI at zero cost, and it is
what makes §10.1 tractable.

### 10.6 Contract parity

A test asserts the verdict schema rejects exactly what the checker prompt forbids [planned:
`test_prompt_and_schema_forbid_the_same_things`]. The prompt and the validator are a coupled pair, and
finding E is what happens when they drift.

### 10.7 Secret scanning

**In CI from day one, tested against planted fixtures, scanning the whole tree.** Not a pre-publish
ritual.

The surface matters more than the pattern list. A scanner that walks only configuration files misses a
credential in a source file or a README, and a credential-shaped-name rule plus an entropy fallback
will catch almost anything *inside the paths it actually visits*. Scan surface first, patterns second.

---

## 11. Build sequence

Seven working days. **A complete, demonstrable artifact exists at the end of day 2**, and every later
day widens it.

| Day | Lands | Standalone |
|---|---|---|
| **1–2** | Act-class engine, canonicalization scoped to emitted representations · one content-class via checker **plus its tripwire** · fail-closed gate · denial contract in the executor layer · citation validation · binary-append + fsync + chain + tamper test · forced verdict · `flag_for_review` · parity test · static audit · scripted adversary · CI secret scanning · portability decision · minimal quality judge · demo | ✅ |
| **3** | Remaining content-classes and tripwires · frozen corpus with dev/eval split and paraphrased violations · blinded labels · **`PREREGISTRATION.md`** | ✅ |
| **4** | Four-arm ladder and the hooks-versus-prompts comparison · both rates · coverage map · mutation testing · failure-mode tests | ✅ |
| **5** | Quality judge over the corpus · discrimination table · **checker calibration** · refinement loop with futility and deadlock stops · demotion | ✅ |
| **6** | Matching module with missingness injection · contamination and recall loss · send cap with its durability tail · second-surface test | ✅ |
| **7** | README · Designed-vs-Built table · perturbation log · on-call note · description linter · pattern table · record/replay fixtures · recorded demo | ✅ |

**Cut in reverse: day 6 first, then day 5's refinement and demotion work.** The spine — act-classes
zero by construction, one content-class independently checked, redirected, durably audited, with the
scripted-adversary proof — is the artifact.

**One exception: §9.5's calibration survives any cut.** It is the only evidence converting §7.1's
ceiling from a stance into a measurement, and an uncalibrated ceiling is the weakest version of that
claim.

**The day 1–2 spine ships both disjuncts** for its one content-class, not the checker alone. A
checker-only spine would put the exact single-signal shape §4.4 exists to correct into the
most-viewed slice of the artifact.

**Locking rule:** anything without a day and a position in the cut order is out by default.

TDD throughout: a failing test first, minimal code to pass, a commit per step.

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| The checker is the artifact's weakest component and its false negatives are the residual the whole design admits | §7.1's ceiling bounds the blast radius; §4.4's tripwires catch the detectable slice; §9.5 measures the residual rather than hiding it |
| Corpus authorship and tripwire authorship share a mind, so arm 4 can flatter itself | §9.3's dev/eval split and paraphrase generation. Named as a limitation in the README regardless, because neither control fully removes it |
| A synthetic corpus proves nothing about real distributions | Stated in the README. The artifact demonstrates enforcement architecture and evaluation protocol, not domain performance |
| The matching module's ranking metrics are computed on labels generated here | §8.2 item 5. Reported as protocol demonstration only, never as ranker quality |
| Seven days against a spine that grew | §11's cut order and the calibration exception. A bad week still ends with a complete day-2 artifact |
| The framework's forced-output mode disables extended thinking | §4.3. Fallback is a think-then-emit two-step, triggered by measured recall rather than by anticipation |
| Long agent prompts can exceed platform command-line limits on some systems | Keep specialist prompts short or hold them in files |

---

## 13. Honesty note

This document is a design. Nothing in it is built. Every test name carries a `[planned:]` marker until
the test exists and passes, and no capability described here is claimed as working until it runs.

Six design reviews preceded this document, and several of its sections exist because an earlier draft
was wrong in a specific way: the single-policy-engine contradiction (§2), the false-negative gap in the
load-bearing claim (§3.2), the durability-as-`finally` fail-open (§5.3), the two-variable ablation rung
(§9.2), the calibration aimed at the wrong lane (§9.5), the tier defined in verbs its occupant lacks
(§7), and the purity claim quietly voided by a stateful predicate (§3.2). Those corrections are
recorded rather than smoothed over, because a design document that shows only its final position hides
the reasoning that makes the position trustworthy.

The distinction between designed and built stays exact everywhere this work is described.
