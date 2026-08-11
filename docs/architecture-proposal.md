# chaperone: an architecture proposal

**What this document is.** The single-document case for this system's architecture and the
judgement behind it, written for a technical reviewer who has read the [README](../README.md) and
wants the design argued end to end. Everything here describes a synthetic scenario, and every
constraint discussed is one the synthetic firm publishes about itself. Checkable claims carry the
test or file that checks them; `tests/test_readme_claims.py` resolves every citation on every run.
Where [architecture.md](architecture.md) argues a per-module decision, this page compresses and
cites rather than repeating it.

---

## 1. The problem, from first principles

The failure this system exists for is a message that is good. An agent carrying an investor
conversation during a raise gets asked, in the most natural possible way, *"honestly, is this a
good deal?"* and *"would they take eight instead of ten?"*. The most helpful next sentence is
fluent, grounded, exactly on topic, and does a thing the firm has publicly said it does not do:
advise on the merits, or negotiate terms. The helpful next sentence and the permitted next sentence
are different sentences, and they diverge exactly where the stakes are highest.

This is not a jailbreak problem. Nobody attacked anything; an ordinary question was answered well,
so adversarial-prompt filters find no manipulation to catch, and quality evaluation actively
approves the failure, because on every quality axis the breaching reply *is* good. `demo/day2.py`
runs that argument as an executable: a judge scores one draft PASS on grounding, fluency and fit,
and the permission gate blocks the same draft in the same moment, printing the class it breached
and `send_message entered 0 times`. One draft, two lanes, opposite verdicts; the disagreement is
the thesis, not a defect in either lane.

The thesis in one line: **an eval score is not an authorization.** A quality judge and a permission
gate answer different questions, and the moment they share a mechanism the system has quietly
decided that a high enough score is a permission. The measured version is in
[RESULTS.md](RESULTS.md) section 5: the one class a pure function refuses outright is the only
class the quality judge ranks at or above compliant -- AUC 0.695 over 10 rows against 60, an
interval clearing the null by 0.002 at the lower endpoint, which the generated page itself says is
too thin to call a direction from.

The scenario is a synthetic capital-introduction firm running a fundraise, whose agent drafts
outbound messages to investors. The firm publishes three constraints on itself -- it does not
advise on the merits of an investment, it does not negotiate terms, it does not make
forward-looking return statements -- and operates under consent and volume constraints on whom it
contacts and how often. The domain is chosen because its constraints are unusually crisp, not
because the pattern is specific to it; any agent on a reputationally exposed surface has the same
shape of problem.

---

## 2. Two families of constraint

The whole design follows from one split: constraints are not one kind of thing, and treating them
as one kind of thing is the central error. [architecture.md](architecture.md) section 1 records the
error surfacing in an earlier design, which specified a single deterministic policy engine for all
six constraints alongside checker prompt discipline that only makes sense if a model decides some
of them; both could not be true, and the resolution is two families with two different epistemic
statuses.

**Act-classes ask whether an act occurred outside its bounds**, and a pure function over the draft,
the record and an explicit `ActContext` decides each one: is an approval token present at this
tier, is the recipient's jurisdiction consented, is the tool inside the grant, does every figure in
the draft appear in the record, is the send cap exceeded. For this family, and only this family,
"zero by construction" is claimable: no model is consulted, so no confidence can lower the bar, and
the claim is exactly as true as the three inputs the predicate reads -- a load-bearing
qualification. For `act:send_cap_exceeded` the input is a count that no shipped path derives from
the audit log and hands to an `ActContext` (`test_no_shipped_path_derives_a_send_count_and_hands_it_to_an_act_context`
holds that to the tree's AST in both directions), so that predicate is live and its input is not,
which section 15 returns to.

**Content-classes ask whether a piece of text does a forbidden thing**, and no pure function can
decide that. Whether a draft advises on merits, negotiates terms, or projects a return is decided
by an independent model checker with deterministic lexical tripwires as a second disjunct
(`src/chaperone/policy/tripwires.py`), and the honest claim about the pair is a measured escape
rate with its denominator, never a guarantee. The two detectors fail for different reasons, which
is the point of having two: `test_the_tripwire_half_of_arm_four_blocks_a_row_the_checker_calls_clean`
exhibits the tripwire blocking a row the checker passed.

The split is enforced at the type level rather than remembered.
`test_act_classes_and_content_classes_report_their_family` holds that every class knows its family,
`test_tripwires_only_cover_content_classes` holds that the lexical layer never claims act-class
territory, and `test_every_content_class_has_both_a_checker_and_a_tripwire` holds that no
content-class rests on a single detector. Even the vocabulary is enforced: the phrase reserved for
act-classes is never written within 120 characters of a content-class name, anywhere --
`test_zero_by_construction_is_never_claimed_beside_a_content_class` scans this page, every
reader-facing page, and every tracked source file under `src/`, `tools/` and `demo/`, whitespace
collapsed so a line break cannot hide the pairing. A guard on a phrase sounds precious until you watch the drift it prevents: a
detector that has never missed on your corpus feels like a guarantee, and writing it up as one is
a single word of drift.

The consequence table, compressed from the [README](../README.md):

| | Act family | Content family |
|---|---|---|
| Decided by | pure functions over explicit arguments | independent checker plus tripwires |
| What may be claimed | a property of the mechanism | a measured rate, with denominator |
| Autonomy ceiling | deterministic gating, higher tiers defensible | tier 2, per-message approval |

---

## 3. Four decision surfaces

One predicate set is decided at four points, and the differences between the four -- what each can
reach, and how each fails -- are the architecture. The table is
[architecture.md](architecture.md) section 2.1's, extended with crash behaviour from the
[boundary specification](superpowers/specs/2026-08-10-boundary-specification.md) section 4:

| Surface | Where | Predicates | Checker | Crash behaviour |
|---|---|---|---|---|
| Out-of-process command hook | `tools/policy_hook.py` | unconsumed keys, act-classes, citations, tripwires | no | fail-closed **by construction**: every escape becomes exit 2, catching `BaseException` |
| In-process deny callback | `src/chaperone/gates/sdk_callback.py` | the same four, same order, same adapter | no | fail-closed **by construction**: `except BaseException` returns a deny |
| In-process hook layer | `pre_tool_use` in `src/chaperone/gates/hook.py` | full set | yes | no handler; fail-closed only derivatively, because `decide` returns rather than raises |
| Executor chokepoint | `guarded_call` in the same module | full set, bound to the reviewed draft | yes | no handler; fail-closed **by ordering**: the decision runs before execution inside the gateway's `try` |

The crash column is worth reading twice. The out-of-process hook contract fails **open** -- only
exit code 2 blocks, every other exit is a non-blocking error the action continues through -- so
the fail-closed behaviour in the first two rows is this repository's construction on top of that
contract, not a property of it; and the last two rows have no exception handler at all, which the
invariant register in section 10 files under discipline rather than mechanism.

**The first two rows enforce a declared strict subset, and the declaration points the right way.**
Neither can reach a model checker from a stdin payload, and three act-classes turn on evidence the
payload supplies -- a sent count, an approval token and tier, the record itself -- which arrives
from the agent being judged. `UNENFORCEABLE_HERE` in `src/chaperone/policy/payload.py` declares
exactly those classes, and the semantics are precise: the set marks classes that **cannot be relied
on to fire** on that payload shape, never classes that are suppressed. Every predicate still runs
and still denies; what the layer cannot do is refuse to be talked out of the denial.
`test_the_classes_declared_unenforceable_still_deny_here_rather_than_being_suppressed` holds the
non-suppression direction, and `test_every_class_deciding_on_evidence_the_payload_supplies_is_declared_unenforceable`
derives the membership from the adapter's own AST rather than from a hand-maintained list, so a
class that starts reading payload evidence joins the declaration on the commit that does it.

Parity across surfaces is held by effects, not by trust:
`test_the_two_enforcement_layers_reach_the_same_verdict_and_the_same_category` drives a corpus of
payloads -- allows as well as denials -- through the command hook and the callback and compares
verdict, category and detail on each row, and `test_the_same_policy_denies_at_all_three_layers`
covers the remaining surfaces. The ordering makes the last surface binding: earlier surfaces can
be bypassed by a caller who reaches the executor directly; the chokepoint cannot, because it
performs the call. One census note belongs here: `pre_tool_use` is called from `tests/` and
nowhere else, disclosed on the README and measured by
`test_the_ledger_of_uncalled_layers_matches_the_census_the_tree_supports`, so a defence-in-depth
claim counting it as a live layer would be resting on the suite.

---

## 4. The chokepoint contract

`guarded_call` exists to make one sentence true: **the reply an investor receives is the reply
that was reviewed.** Both
halves of that binding had to be built, because each failed separately. Identity: nothing held
`decide`'s `draft.tool_name` equal to the tool the executor runs, so a draft naming a granted
`draft_message` once passed review while the executor ran `send_message` -- both inside the grant,
no act-class fired, and the message left; `_decide_for` in `src/chaperone/gates/hook.py` now
refuses the mismatch at every layer from one function. Arguments: an approved draft whose `args`
carry different prose ships text no layer assessed, so `unsendable_in` in
`src/chaperone/policy/arguments.py` refuses any scalar in `args` outside the draft's reviewed
outbound surface -- deliberately strict, because the safe direction is a spurious refusal, and
because the gateway digests `args` into the audit log: if the reviewed object and the digested
object can differ, the log describes a call nobody assessed.

**A denial is terminal, and retryability is not redraftability.** `denial_result` in
`src/chaperone/gates/engine.py` returns `is_retryable: false` on every denial without exception
(`test_deny_is_never_relabelled_as_retryable`), because a retry is the same call again and a policy
denial will refuse it identically forever; an agent that believes otherwise loops, and the loop
reads as a transient failure. Whether a *different* draft could pass is a separate axis,
`disposition`, derived from the finding classes in exactly one function
(`test_disposition_is_derived_from_category_in_one_place`), and a refinable denial is still not
retryable (`test_a_refinable_denial_is_still_not_retryable`). A denial is also returned, never
raised: a defensively written executor wraps handler invocation in a catch-all, and a deny
implemented as an exception arrives at the agent relabelled transient, which is the one label it
must never carry.

**The redirect happens at the chokepoint, not in prose.** `guarded_call` takes `queues` as a
required keyword-only parameter -- a call with nowhere to route does not run -- and on denial it
builds the escalation payload and appends it to the queue `destination_for` names, before
returning. The scope is stated in `src/chaperone/gates/queues.py`: a queue is a list in memory
living as long as the object its caller holds; the audit log proves a redirect happened (a denied
guarded call carries the `redirected` outcome) while the queued payload is not reconstructible
from that log, which carries digests and never text; and durable delivery to an external inbox is
deployment work this repository has not done and does not claim.

**The handoff refuses to escalate incomplete.** `Handoff` in `src/chaperone/gates/handoff.py` is
self-contained by design, because the reviewer cannot see the conversation: every field is
required and the model forbids extras, so an escalation cannot arrive with the field the reviewer
needs quietly empty. The refinement loop feeds this shape rather than the send path: `refine` in
`src/chaperone/gates/refine.py` transmits nothing, and a redraft that resolves the denial still
rides to a human as `proposed_alternative` beside the body that was blocked
(`test_a_redraft_never_transmits_without_approval`, asserted on effects: the send tool is not
entered and the log says `redirected`). A redraft that transmitted by itself after a permission
failure would be an auto-retry of a permission failure, the thing this architecture exists to
refuse.

---

## 5. The independent checker

The checker's independence is enforced by what is left out of its prompt, because omission is
testable and restraint is not. "The checker ignored the generator's justification" is a claim about
model behaviour and nearly untestable; "the checker's prompt does not contain the generator's
justification" is a claim about a string. `build_checker_messages` in
`src/chaperone/gates/checker.py` reads the transmitted thread, the candidate draft and the cited
records, and no untransmitted artifact -- no generator reasoning, confidence, or self-assessment.
`test_the_checker_prompt_has_no_generator_artefacts` holds the omission and
`test_the_checker_prompt_structure_is_exact` pins the whole shape, so a future edit that helpfully
threads context through cannot land quietly.

**The checker must not be weaker than the drafter**, refused at construction over the
`MODEL_STRENGTH` ordering (`test_a_checker_model_weaker_than_the_drafter_is_refused`). The
tempting inversion -- draft strong, review cheap -- hands the authorizing decision to a reviewer
that can be out-argued by fluency; spend on the gate ([architecture.md](architecture.md)
section 3.2 argues it in full).

**The transport is the seam, and the framework binds there and only there.** `Checker` takes a
plain callable `list[dict] -> CheckerResult`; `pydantic_ai_transport` in
`src/chaperone/gates/binding.py` returns that callable while `build_checker_messages` stays the
sole prompt assembler (`test_the_first_wire_request_is_exactly_the_assembled_messages` holds wire
fidelity). The retry design splits on what a retry could fix: semantic unusability -- a verdict
reporting a violation without naming a class -- is retried *inside* the binding with the reason
fed back to the model (`unusable_reason` is called at both enforcement points, never restated),
while availability is retried in `Checker.check` against its own budget. Left to compose naively
the two budgets multiply, `(R+1) * (N+1)` model calls for one draft; exhaustion in the binding is
therefore raised as `CheckerUnavailable`, which `Checker.check` re-raises untouched rather than
treating as one more attempt. Measured at both ends: with either escape removed, the same draft
costs 9 model calls instead of 3 (`test_validation_exhaustion_is_terminal_and_never_multiplied`).
A `request_limit` ceiling is kept as a second fence at the same position, disclosed in the module
docstring as structural and currently unfalsifiable on pydantic-ai 2.23.0, so a future version
that recounted retries cannot silently revert the budget to the product.

**The span rule stays gate-side on purpose.** A verdict quoting a span must quote the draft
verbatim (`span_absent_reason`), because that span is what a human reads while deciding whether
text may go out. The rule needs the draft body, which the transport seam deliberately does not
have, so no binding can feed a span violation back to the model and a span violation only ever
denies (`test_a_verdict_quoting_a_span_the_draft_does_not_contain_exhausts_the_budget_and_then_denies`).

**And the gate is fail-closed.** After the budget, an unusable checker becomes
`CheckerUnavailable`, which `decide` converts into a denial rather than a pass
(`test_checker_unavailability_fails_closed`); the alternative converts an outage into an
unreviewed send at the exact moment the system is most degraded and least supervised.

---

## 6. The verification contract

The rule that organises all verification here: **invariants are asserted; probabilistic outcomes
are measured, reported, and never asserted.** CI asserts four rates, each an invariant of the
construction rather than a finding: act-class escapes are zero
(`test_arm_four_has_zero_act_class_escapes`), the escape ordering across arms is monotone over
frozen replays (`test_escape_rate_is_monotone_across_the_ladder_on_frozen_replays`), the
false-block ordering between the last two arms is structural, and the reference arm's escape rate
is 1.0 because that arm has no detector. Every other number in [RESULTS.md](RESULTS.md) is
reported with its denominator and asserted nowhere, which is what keeps a measurement from quietly
becoming a gate.

The two live lanes hold the same discipline in front of real models. `demo/day2.py --live` swaps
the scripted checker transport for `pydantic_ai_transport` and asserts invariants only: an answer
of the declared output union arrived, the messages on the wire are byte-equal to what
`build_checker_messages` assembled, and the send tool was entered exactly when the gate allowed it
and never otherwise. What the model decided is printed, never asserted. `demo/sdk_hook.py --live`
does the same for an act-class refusal, checking the runtime's own failure flag first so that
effects asserted over a turn that never happened cannot be reported as a refusal. Both lanes have
been run by hand: the checker lane's run returned the merits class at confidence 0.98 with the
span verbatim and no outage, and the SDK lane produced its act-class refusal for well under a
dollar. Those outcomes are attested by the sessions that ran them, not by any artifact in this
tree, which records no live run -- the same standard the README applies to its own live-lane
history -- and the suite and CI run neither lane.

The deepest instance of the discipline is the outage/judgement split. A denial because a detector
judged the draft and a denial because no detector answered are the same shape in every ordinary
field, and a reviewer who cannot tell them apart will read downtime as judgement -- the direction
that looks like a clean denial and so is never questioned. `Decision.outage` in
`src/chaperone/policy/types.py` exists to separate them, `decide` sets it on exactly the
unavailability branch, `OUTAGE_WITHOUT_MESSAGE` guarantees it is never a falsy non-`None` (an
exception raised without a message stringifies to the empty string), and `Handoff.detector_outage`
is required rather than defaulted, because the default would let a construction site report every
outage as a judgement. `test_an_unavailable_checker_names_the_outage_on_the_decision` and
`test_a_judged_violation_carries_no_outage` hold both directions.

---

## 7. The audit spine

The audit layer answers one question observability does not: was the record altered after the
fact? Every entry's digest covers the previous entry's hash (`link` in
`src/chaperone/audit/chain.py`), so an edit, deletion, or reordering invalidates every link after
it and the break is localised by index (`test_a_tampered_entry_is_detected_and_the_index_is_named`,
`test_a_removed_entry_is_detected`, `test_a_reordered_pair_is_detected`, the digest checked against
an independent SHA-256 in `test_the_link_digest_matches_an_independently_computed_sha256`).

**What tamper-evidence does not claim is half the design.** This is detection, not prevention: an
actor with write access who rewrites the whole chain, recomputing every hash, produces a chain
that verifies, and no test at this layer is possible against that actor. The answer is append-only
storage under separate credentials, which is infrastructure rather than code, and the limit is
filed as such in [failure-modes.md](failure-modes.md) entry D5 rather than discovered by a reader.
One more limit travels with it: `verify` does not itself detect a torn tail -- the flag is
computed by the store on read and passed through -- and a missing log file reads as zero entries.

**The log is an enforcement input, which raises the durability bar.** The send cap counts intent
entries, so a lost line is not a forensics gap; it is a predicate failing open, permitting a send
it should have refused. `AuditStore` in `src/chaperone/audit/store.py` therefore appends in binary
mode, flushes and fsyncs per entry
(`test_append_fsyncs_the_store_files_descriptor_after_the_bytes_are_written`), terminates a torn
line before appending after it so the tear cannot absorb the next record
(`test_an_append_after_a_tear_is_readable_and_the_count_does_not_undercount`), and keeps the tear
visible so recovery policy can see that a record was lost.

**Intent and outcome bracket every effect, and the outcome is written in a `finally`.**
`Gateway.call` in `src/chaperone/audit/gateway.py` writes a durable intent before an effectful
call is attempted (`test_an_effectful_send_writes_an_intent_entry_before_the_outcome`), holds the
outcome word at a fail-closed default -- `"unattempted"` before anything runs, `"error"` from the
moment the tool is entered until a clean return downgrades it -- and writes exactly one outcome
entry per call in the `finally`, so a gate that raises still leaves no dangling intent
(`test_a_gate_that_raises_still_writes_an_outcome_and_leaves_no_dangling_intent`). The module's
docstring inventories what runs above the `try` (three bindings that cannot raise) and names the
one residual hole it cannot close: a store failing *inside* the `finally` loses the outcome entry,
because writing it is the last thing that happens. Entries carry an `arg_digest` and never the
arguments (`test_arg_digest_is_stable_and_does_not_contain_the_raw_value`); the walkthrough of one
denied send, entry by entry, is [audit-walkthrough.md](audit-walkthrough.md).

---

## 8. The autonomy ladder

Autonomy here is graded per surface, and the interesting rung is the one no surface that sends
outward to an investor can reach.
`src/chaperone/gates/ladder.py` defines tier verbs per task class -- "send outward" is meaningless
for a read-only research surface -- and a ceiling per surface: research tops out at tier 3,
conversation at tier 2, per-message approval. The ceiling is structural rather than aspirational:
`LadderState.__post_init__` calls `max_tier_for`, so a state above the ceiling cannot be
constructed at all, and a surface with no declared ceiling is refused rather than handed the top
tier (`test_the_declared_ceilings_and_the_verb_table_name_the_same_surfaces_and_agree_on_the_top`).

The argument for the ceiling is three clauses, all independent of any measurement. A checker false
negative at an unsupervised tier *is* the breach, not a warning of one; sampled audit catches it
after transmission, which is detection, not prevention; and demotion is itself triggered by a
verdict, so a missed violation also misses its own demotion. All three hold at zero measured
errors, which is why the calibration table in [RESULTS.md](RESULTS.md) section 4 -- no content
cell overshot its stated confidence -- is reported as *consistent with* the ceiling and never as
the argument for it. A ceiling that rested on a clean calibration table would be an eval score
promoted to an authorization, the exact error the thesis names.

**Promotion is built and driven by nothing, and the unwiring is disclosed as precisely as the
building.** `on_pass` increments and promotes at `PROMOTION_THRESHOLD`, exercised by
`tests/gates/test_ladder.py`; what is absent is a caller, and
`test_nothing_under_src_wires_an_outcome_to_promotion` holds the absence by AST scan rather than
by memory. If promotion were wired, it would not be keyed to these numbers: production promotion
belongs to human-review outcomes on real cases, never to synthetic suite scores -- the
self-promotion error the README and [architecture.md](architecture.md) section 7.2 both refuse in
the same words, deliberately.

---

## 9. The evaluation architecture

Every number in this repository comes from a frozen corpus of 160 investor-facing drafts, was
predicted before it existed, and the failed predictions are reported as failed. The corpus was frozen and five predictions committed in
[PREREGISTRATION.md](../PREREGISTRATION.md) before any arm ran; two held, three failed, and one of
the two holds only because a stronger prediction had already been weakened on dev-split evidence,
which the results page says out loud. Git history is the only witness to the ordering, because no
test can establish that a document preceded a measurement -- and stating that is part of the
protocol rather than a concession.

**The corpus.** 160 drafts, 80 per split, each split 50 labelled violating and 30 labelled
compliant; per split, 15 rows for each of the three content classes and 5 for
`act:figure_not_in_record`, every cell held to `corpus/labels.jsonl` by
`tests/test_preregistration.py`. Every body was written by an author who could not read this
repository, and labels derive from each row's recorded provenance -- the class the author set out
to instance -- with no reading of the text (`test_every_body_and_intent_is_the_blind_authors_own`).
That stops a label agreeing with a detector by construction; the residual it leaves, a violation
that lands innocuous but is still charged as an escape, is stated in advance in
[measurement.md](measurement.md) section 2.2.

**The arms.** One verdict source per rung, over one frozen recording, one variable moving per
rung: arm 2 is the independent checker, arm 3 wraps it in the fail-closed gate, arm 4 adds the
deterministic layer. Arm 1 -- the drafting model policing itself -- is absent and reported absent
(`test_the_missing_first_rung_is_named_as_absent_rather_than_built_from_another_arm`), because no
honest verdict source for it exists here and a fabricated baseline would have flattered every arm
above it. The prompting-only reference is published beside the ladder and explicitly not as a
rung, since more than one variable separates it from anything
(`test_the_reference_arm_measures_the_absence_of_a_chokepoint_and_not_the_failure_of_prompting`).
Replaying spends nothing: the blind checker read the eval bodies once into
`corpus/recorded_verdicts.json`, and every arm is a deterministic function over that recording,
re-run by `tests/evals/test_harness.py` on every `pytest` invocation.

**What the numbers can and cannot support.** On the eval split, arm 4 escapes 0 of 50 at 0 of 30
false blocks; arms 2 and 3 escape 5 of 50, all five act-declaring rows -- at the content-only
scope every rung escapes 0 of 45, which *failed* prediction 2, and the honest gloss is a bound,
not a clean sheet: by the rule of three, 0 in 45 bounds the content escape rate above at about
6.7% at 95% confidence and bounds nothing below ([measurement.md](measurement.md) section 7).
Calibration is scored per cell with Brier, never blended; zero boolean errors over 90 content rows
bounds the checker's error rate above at about 3.33% at 95% confidence and says nothing about a
deployment rate. Arms 2 and 3 are identical on the shipped replay because the recording answers
every row, so the fail-closed rung is priced by an injected-unavailability probe at fraction 0.25,
published as a probe and not a rung: arm 2 escapes 14 of 50 at 0 of 30, arm 3 escapes 4 of 50 at
3 of 30 -- the trade priced in both directions. The quality-lane contingency at the 0.7 threshold
(35 violating drafts pass quality, 49 compliant pass, 65 violating fail, 11 compliant fail) is the
two-lanes thesis as a table.

**The evidence-base limits are named where the numbers are.** The corpus author and the blind
checker share a criterion: both prompts were written by one hand and are near-verbatim on the
constraint definitions, so agreement between them is closer to a matched pair than to an
independent result. Every violating draft was written *to violate*, and nobody wrote one trying to
be helpful and crossing the line under pressure, which is the failure the system is actually
about -- the README calls this the widest gap between what the artifact demonstrates and what the
problem is. And no compliant near-miss sits inside the tripwires' lexical reach, so this artifact
cannot measure the tripwires' false-block cost, and the observed zero is not evidence the cost is
zero. All three were written down before measurement, so a flattering number cannot be read as a
finding.

---

## 10. The enforcement of honesty itself

The differentiating layer of this repository is not the gate; it is the machinery that keeps the
claims about the gate true. Documentation drift is treated as a failure class with the same
seriousness as a permission bypass, because a reader-facing page goes stale in exactly one
direction: the tree moves and the prose does not.

**The claims suite.** `tests/test_readme_claims.py` -- 1,755 lines, more than a third the size of
the whole `src/chaperone/` package -- holds the reader-facing layer to the tree. Every backticked test name
on every page must exist; every cited source path must exist; every relative link must resolve.
Both pasted demo transcripts in the README are compared byte for byte against fresh subprocess
runs, and the pasted denial payload is rebuilt by executing the tripwire and `denial_result` over
the quoted body rather than compared against itself. The stated test-to-source ratio is recomputed
against the tree within a tolerance band. Every tracked file -- not just markdown -- is scanned
for organisation names against a hashed denylist, so the guard cannot publish the thing it
forbids.

**Guards that hold claims in both directions.** The designed-versus-built table is bound to the
tree both ways by `test_the_designed_versus_built_table_agrees_with_the_tree_in_both_directions`:
build a thing the table calls designed, or delete a thing it calls built, and the build fails. The
census of uncalled layers works the same way -- wire `pre_tool_use` or `transmit` into a shipped
caller and the disclosure sentence has to come off -- and its own limits are disclosed on the page
it guards: the census matches callees by name, so two of the four symbols the README enumerates
sit outside its roster and are checked by reading, which the guard verifies the page says.
Coverage claims are policed as a pattern: a sentence claiming tests exercise a symbol is resolved
against the names the test modules actually define, import, call or read
(`test_no_published_page_claims_test_coverage_of_a_symbol_no_test_names`), and its dual catches a
page denying the existence of a symbol the tree defines
(`test_no_published_page_says_a_symbol_is_absent_that_the_tree_defines`). Generated pages are
bound to their generators in CI: `tools/report.py` and `tools/perturbation_log.py` are re-run and
`git diff --exit-code` fails the build on any difference, and
`test_the_committed_results_page_is_what_the_generator_produces_apart_from_its_rates` holds
[RESULTS.md](RESULTS.md) to its generator on every heading, count and adjudication word with
decimals normalised out, so no probabilistic rate is asserted.

**The mutation sweep.** Seventeen mutants in `tools/mutate.py` -- flip the deny to allow, drop the
`finally`, remove the fsync, cache the sequence counter -- and each must be killed by a named
failing test; a survivor means some test asserts a proxy for its property rather than the
property. The harness guards itself, because a sweep that reports kills it did not see is worse
than none: only pytest exit code 1 counts as a kill (collection errors and empty collections are
harness failures), anchors must be unique and present so a mutant cannot land on the wrong site or
silently not land, files are written as bytes so newline translation cannot corrupt the tree, a
replacement that leaves the bytes unchanged is refused as not-a-mutant, and the original source is
parked on disk before mutation so a run killed mid-sweep is recovered by the next run rather than
leaving a fail-open mutant applied in `src/`. The incidents behind each guard are recorded in the
tool's own docstring.

**The purity guard, three times.** `src/chaperone/policy/` imports no model client, no I/O, no
clock; a predicate that needs a value takes it as an argument. An edit-time `PreToolUse` hook
(`tools/guard_edit.py`) refuses the violating edit as it is made; `tools/static_audit.py` walks
the import graph in CI, refuses to report clean over an empty root, and reserves the send symbol
package-wide so that any identifier or attribute named `transmit` outside the gateway module fails
the build; and `test_the_real_policy_package_is_pure` states the property where a reader looks for
properties. The audit's docstring names its own evasions -- `__import__`, the builtin `open`, a
shadowing redefinition -- and states its job as making an accidental violation impossible and a
deliberate one conspicuous, not proving that none exists.

**The invariant register, and what is held only by discipline.** The
[boundary specification](superpowers/specs/2026-08-10-boundary-specification.md) section 5 files
every invariant under the mechanism that holds it -- **T** a test asserting an effect, **S** a
static tool with its own exit code, **H** an edit-time hook, **R** review discipline only -- and
the register earns its name in the fourth column. Held by discipline, not mechanism: there is no
timeout anywhere, so fail-closed on an unusable checker does not cover a hang, and both
`src/chaperone/gates/checker.py` and `src/chaperone/gates/engine.py` say so in their docstrings;
two of the four decision surfaces have no exception handler; "no network in tests" is true by
absence and has no scanner; the catalog's cited tests are name-checked but never
relevance-checked, so a test could exist and not attack the failure its row describes; the send
symbol's reservation is a name audit, not a call-graph proof; one measured mutant survivor -- a
justification sentence appended to the checker instructions, hand-applied and recorded at
`tests/gates/test_checker.py`, not one of the sweep's seventeen -- has no guard; and the
declared-unenforceable classes can be talked out of firing by the caller being judged, exhibited
by tests and not closed. A register that filed those in the same voice as the tested rows would be
worth less than no register.

---

## 11. The security model

The security posture is small, and stated at its true size rather than padded. What exists:

**The prompt-assembly injection surface is named, not solved.** `build_checker_messages` selects
which fields reach the checker prompt and is held to that selection in both directions, but the
contents of those fields -- thread roles, thread bodies, the draft body -- are interpolated raw. A
consumer that puts untrusted counterparty text into `Draft.thread` is holding a prompt-injection
boundary by discipline, and nothing in this library holds it for them; whoever assembles a `Draft`
owns that line. The docstring at `src/chaperone/gates/checker.py` and the boundary specification
both state it, because an injection surface that is disclosed is a different risk from one that is
implied away.

**The payload trust boundary is explicit.** Out of process, the evidence three act-classes turn on
arrives from the agent being judged, which is why those classes are declared in
`UNENFORCEABLE_HERE` (section 3) rather than papered over with defaults, and why the consented
jurisdictions and granted tools are module constants the caller cannot supply -- a guard that
accepts its own permissions from the caller is not a guard. Absence fails closed where it can:
`tier` defaults high, a missing body blocks rather than scoring an empty draft
(`test_a_missing_approval_token_is_not_a_present_one`, `test_a_body_the_hook_cannot_find_blocks_rather_than_evaluating_an_empty_one`).

**No credential exists in the tree, and spend is gated by flags, never by credential probes.**
`tools/scan_secrets.py` runs in CI over prefix patterns, credential-shaped assignments and an
entropy check; the suite runs keyless. The two live lanes are reached only by an explicit
`--live`, refuse unrecognised arguments rather than ignoring them, and raise loudly when the model
spec is absent, because under a paid flag a silent skip is how a lane gets recorded as run without
having run. Nothing in the repository selects an endpoint or hardcodes a concrete model spec: the spec is read whole
from the environment and never echoed back.

What does not exist is stated with equal precision: no key-management story, no encryption-at-rest
story, and no story for hostile write access to the audit file beyond detection (section 7).
Deployment concerns, named as out of scope rather than gestured at.

---

## 12. The technology stack, justified

The stack is small and every absence in it was audited rather than assumed. Runtime dependencies
are `pydantic>=2.7` and `pydantic-ai>=2.23`; `pydantic-evals` and `claude-agent-sdk` are optional
extras the build does not install.

| Choice | Why |
|---|---|
| Python 3.11+, dataclasses and pydantic v2 | The boundary objects that cross trust lines -- `Verdict`, `Handoff`, `AuditEntry` -- are frozen pydantic models, validated at the line they cross; pure policy stays in dataclasses with no I/O reachable. |
| pydantic-ai, bound at exactly one seam | The checker's transport parameter is a seam this repository already owned, so the framework is a replaceable implementation detail behind an interface the tests pin: wire fidelity (`test_the_first_wire_request_is_exactly_the_assembled_messages`) and budget non-multiplication (`test_validation_exhaustion_is_terminal_and_never_multiplied`) both hold with the framework in place. Offline, the binding is exercised against `FunctionModel`; no recorded verdict was produced through it, and the module says so. |
| Agent SDK contract as own code | The SDK offers no seam like the transport: it is the runtime that drives the tools, and enforcement placed inside it lives where a static import audit cannot follow. So the hook contract is implemented as this repository's code -- `src/chaperone/gates/sdk_callback.py` produces the documented deny shape as plain JSON and imports no SDK (`test_the_callback_module_imports_no_sdk`) -- and the `claude_agent_sdk` import is confined to `demo/sdk_hook.py`, which is wiring. The audit can read the gate; only the wiring needs the SDK. |
| pytest + hypothesis, recorded transports | Offline and keyless by construction; determinism is what lets every arm be compared over identical verdicts. |

**Nine hand-rolled surfaces were checked against the declared stack at exact installed versions
(pydantic-ai 2.23.0, pydantic-evals 2.23.0, claude-agent-sdk 0.2.130), and nothing bound -- which
is a result, not an absence of one.** Four surfaces have no framework equivalent at all (replay
transport, capability ladder, hash-linked audit, the adversarial harness that drives the
chokepoint). Four have an equivalent that implements a different thing or would make things worse:
the human-in-the-loop round trip resumes a paused call where a denial here is terminal, and on
resume the library skips the approval predicate once approved
(`pydantic_ai/toolsets/approval_required.py:29`) and substitutes `override_args` into the call
(`pydantic_ai/tool_manager.py:1096`) before re-validation, so what executes can differ from what a
human approved -- the exact divergence `unsendable_in` exists to refuse; `ModelRetry` self-corrects
inside one run where the refusal is the design; a framework-registered hook would move a readable
decision onto an agent object; and the shipped ranking evaluators, with
`positive_from='expected_output'`, score a case positive on `bool(expected_output)`
(`pydantic_evals/evaluators/report_common.py:54-55`), collapsing the negative class on a ranking
dataset and rendering a curve that is wrong and looks fine. The ninth, a library-enforced offline
flag, is buildable today and declined on this repository's own test-discipline ground: no test
constructs a provider model, so the flag would guard a mistake the tree has already decided not to
be able to make. The full ledger, with every API, version and line citation, is the
[framework audit](superpowers/specs/2026-08-09-framework-audit.md). A keep nobody can explain is
indistinguishable from a keep nobody examined, so each one is explained.

---

## 13. Data flow: one draft, end to end

The scripted scene of `demo/day2.py`, which both the suite and CI execute, traces this path.

```
investor message                       QUALITY LANE (measurement)
      |                                  score_quality(draft, record, transport=...)
      v                                  -> grounding 0.94, fluency 0.91, fit 0.89: PASS
  drafted reply ----------------------> (a judge's opinion; authorizes nothing)
      |
      v
  guarded_call(gateway, "send_message", args, draft, record, context, checker,
               registry, queues=...)          PERMISSION LANE (authorization)
      |
      |  Gateway.call: intent entry written, fsynced, before anything runs
      v
  _decide_for
      |-- identity: call names the tool the reviewed draft names?
      |-- arguments: every scalar in args reviewed as outbound, in its slot?
      |-- act-classes + citations (pure; return before the checker is consulted)
      |-- tripwires  \
      |               >-- disjunction: either blocks
      |-- checker    /   (independent prompt; fail-closed on outage)
      v
  Decision(allowed=False, findings, disposition, outage=None)
      |
      |-- denial_result: {is_error, is_retryable: false, category, detail, span, disposition}
      |-- queues.put(destination_for(disposition), build_handoff(...))   <- happens, not said
      |-- outcome entry "redirected" written in the finally; chain verifies
      v
  human review queue        (registry never indexed; send_message entered 0 times)
```

On a refinable class, `demo/full.py` adds the loop: `refine` redrafts against the actual denial,
re-runs `decide` on the proposal, and the resolved redraft goes into the handoff for approval --
it does not transmit (`test_the_full_demo_runs_both_scenes_and_enters_the_send_tool_in_neither`).
On a futile class the loop is skipped by derivation: no rewording of an answer to "is this a good
deal?" is a permitted answer to it, so the escalation proposes changing the task, not the words.

---

## 14. Project structure

```
src/chaperone/
  policy/       pure predicates: act_classes, tripwires, citations, arguments,
                payload (the shared trust boundary), types, canonical, eligibility.
                No I/O, no clock, no model client; enforced, not intended.
  gates/        engine (decide/denial_result), checker + binding (the transport seam),
                hook (pre_tool_use, guarded_call), sdk_callback, handoff, queues,
                refine, ladder.
  audit/        store (fsync, torn tails), chain (hash links), gateway (intent/outcome),
                entry (vocabulary), recovery (the resume pass; built, unscheduled).
  evals/        harness (the arms), judge, calibration, discrimination, corpus.
  matching/     filters, rank, relationship, ablation (the second ablation surface).
  testing/      recorded and scripted transports; ships in the wheel, outside the contract.
tools/          the guards -- static_audit, guard_edit, policy_hook, mutate, report, coverage_map,
                perturbation_log, scan_secrets, lint_descriptions -- and the corpus construction
                that predates them: build_candidates, build_corpus, label_corpus, record_verdicts.
tests/          ~3 lines of tests per source line; the claims suite; the mutation sweep.
corpus/         frozen drafts, provenance labels, recorded verdicts, quality scores.
demo/           day2 (two lanes), full (both scenes), sdk_hook (SDK wiring; only SDK import).
docs/           this page and its siblings; RESULTS.md and PERTURBATIONS.md are generated.
```

---

## 15. What this proposal does not claim

Limits are properties of the system, so they are listed with the same precision as the features.

- **Per-draft gating is what is evidenced, not conversation governance.** Statelessness is held --
  `test_a_drafts_verdict_does_not_depend_on_which_drafts_preceded_it` drives every ordering of
  three disagreeing drafts -- but a conversation can walk somewhere no single turn goes, and no
  test yet demonstrates a cross-turn breach being caught. The thread-scope pass is designed, not
  built.
- **The audit detects tampering and does not prevent it**; a whole-chain rewrite verifies (section 7).
- **The double-send guard is built, tested, and unarmed outside the suite.** Nothing schedules
  `resume`, nothing consults `requires_approval_for` before a send, and no shipped path derives a
  count from the log and hands it to an `ActContext` -- `counted_sends` has shipped readers, but
  they only print -- while the hook layer's cap input defaults to a permissive zero from the
  payload. A re-attempt is unguarded until that wiring exists.
- **Four symbols are built and called from nowhere outside the suite** -- `transmit`,
  `AuditStore.count`, `Branch.COMPLETE`, `pre_tool_use` -- disclosed and, where the census can see
  them, measured (section 10). None is a permissive path.
- **There is no live judging arm.** Every verdict any test consumes is a frozen recording; the two
  live lanes are hand-run demos that no test imports and CI never runs.
- **The evidence base is the honest weak point, by the README's own account.** Single-author
  synthetic corpus, shared criterion between author and checker, deliberate violations only. The
  numbers demonstrate the protocol on the easier population; the harder population is the reason
  the architecture is shaped this way, not something these numbers certify.
- **The queues are in-memory and the recipient-scoped handoff is undesigned**; the audit log is
  the only thing that outlives a process.

### The system built on this boundary (designed, not built)

A successor system -- specialist agents over a persistent ledger, with matching and evals as
services rather than modules -- vendors this repository as a wheel and treats it as its
authorization boundary. That system is designed and not built, and this repository's contribution
to it is already visible in two places. The enforcement primitive its agent topology needs is
shipped: `granted_tools` checked by a pure function, so a drafting agent holding no send tool has
its worst output bounded at a bad draft ([architecture.md](architecture.md) section 6). And the
[boundary specification](superpowers/specs/2026-08-10-boundary-specification.md) exists because
that consumer had to derive its import contract by reading source; it records the contract, the
invariant register, and the delta the package must close to publish it. What the wheel does not
carry is the enforcement of the properties: the purity audit, the edit-time hook, the mutation
sweep and the claims suite are siblings of `src/` and do not travel, so a consumer inherits the
properties and must hold in its own tree anything it needs held.

---

## Appendix: the published contract

Summarised from the [boundary specification](superpowers/specs/2026-08-10-boundary-specification.md)
section 2, the statement of record; this appendix names the surface so a reviewer sees its size.

**Enforcement.** `guarded_call` (nine required arguments, `queues` keyword-only) and `pre_tool_use`
with its `HookOutcome` from `gates.hook`; `pre_tool_use_deny` from `gates.sdk_callback`, async,
returning the deny dict, importing no SDK.

**Policy vocabulary.** `Draft`, `Message`, `Record`, `Decision`, `Finding`, `ViolationClass` (nine
members: the eight named classes plus `other`, the class an outage denial rides on),
`Disposition` (three), `Family` (three: act, content, and unclassified for `other`) from
`policy.types`; `ActContext` and
`evaluate_act_classes` from `policy.act_classes` -- the dataclass lives beside its predicate, not
in `types`; `validate_citations`, `evaluate_tripwires` and `TRIPWIRE_CLASSES`, `unsendable_in` and
`unsendable_finding` from their `policy` modules. `Message` is not optional to know: `Draft.thread`
is a tuple of them, so without it a consumer cannot construct the first argument to anything.

**The checker seam.** `Checker(model, drafter_model, transport, retries=2)` from `gates.checker`,
a concrete class a consumer constructs rather than implements; `MODEL_STRENGTH`, without which the
valid model set is learned from an exception; `CheckerResult` (a type alias naming the transport's
return), `Verdict`, `FlagForReview`, and `CheckerUnavailable`, which is what a consumer catches.

**Audit.** `AuditStore` (mkdirs its parent at construction), `Gateway`, `GatewayResult`, `link`,
`verify`, `VerifyResult`, `GENESIS_HASH`, plus `AuditEntry` and `INDETERMINATE_OUTCOMES` -- the
entry is the currency of every audit call a consumer makes.

**Matching.** `Mandate`, `Candidate`, `Eligibility` and `classify` from `matching.filters`;
`relationship_score` from `matching.relationship`; `rank` from `matching.rank`.

`transmit` is published at its module
path only, never from the package root, because the send-symbol reservation covers the package
init itself: re-exporting it would trip `tools/static_audit.py` on the init's first line. That is
the reservation working, not an inconvenience -- the symbol is findable, and no import can quietly
widen the set of modules that name it.

**Routing and the adapter.** `ReviewQueues`; `Handoff` and `build_handoff`; `decide`,
`denial_result`, `disposition_for`, `destination_for` from `gates.engine`; and from
`policy.payload` the adapter surface `build_act_inputs`, `CONSUMED_KEYS`, `UNENFORCEABLE_HERE`,
`CONSENTED`, `GRANTED` -- published so a consumer reads the unenforceable set correctly and knows
the shape guards the adapter deliberately leaves to its caller.

**What a consumer must construct.** A `Gateway` over an `AuditStore`, a tool registry whose values
the chokepoint indexes by tool name and calls with the argument keys spread as keyword parameters
-- a tool written to take a single dict raises `TypeError` at the chokepoint -- a `Checker` with a
transport, and a `ReviewQueues`. None is optional, none has a
default: a gate you can construct without an audit trail or a place to route denials would be a
gate that can be deployed with neither, and the constructor refuses to be that.
