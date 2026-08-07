# Failure modes

The full catalog. The [README](../README.md) carries the three that matter most; this is everything.

Every entry has the same four fields: **the failure**, **the mechanism that holds it**, **the named
test that attacks it**, and **what may be claimed**.

## The claim vocabulary

The fourth field is the honest one. It takes one of five values, and the ratio between them is the
argument this repository is making.

| Claim | Meaning |
|---|---|
| **Zero by construction** | A pure function decides. The failure is impossible, not improbable. Act-classes only. |
| **Structural invariant, tested** | A property of how the code is assembled, proven by a test. Not a rate. |
| **Measured** | A number, reported with its denominator. Not a guarantee. |
| **Detection only** | Caught after the fact, not prevented. |
| **Designed, not built** | The mechanism is specified and absent from this tree. |

Count the **measured** rows against the **zero by construction** rows before reading further. Most
failure modes in a system like this one are measured, and a document that labels them all as
guarantees is telling you it has not looked closely.

---

## A. Content-class failures

### A1. The fluent breach

**Failure.** An investor asks *"honestly, is this a good deal?"*. The best answer is fluent, grounded,
on topic, and advises on the merits.

**Mechanism.** Fail-closed gate with two disjuncts: the model checker and lexical tripwires. Either
blocks. `evaluate_tripwires` reaches `content:advises_on_merits` from the body alone.

**Test.** `test_each_content_class_blocks_through_the_tripwire_alone`;
`test_the_denial_on_each_content_class_is_the_tripwire_lane_and_nothing_else`.

**Claim: measured.** Content-class escape rate over 45 labelled content rows per split.

### A2. The rubber stamp

**Failure.** The checker reads the generator's justification and agrees with it. A second opinion that
is the first opinion restated.

**Mechanism.** Independence by prompt omission. The checker receives the draft and the constraint set,
never the generator's reasoning, confidence, or self-assessment.

**Test.** `test_the_checker_prompt_has_no_generator_artefacts`;
`test_the_checker_prompt_structure_is_exact`.

**Claim: structural invariant, tested.**

### A3. The confident wrong checker

**Failure.** A false negative arrives at high confidence. A gate that knew it was uncertain could
escalate; a confidently wrong one hands back a clean verdict.

**Mechanism.** The tripwire disjunct, which does not consult checker confidence, plus the tier-2
ceiling so a false negative meets a human before it becomes an outbound act.

**Test.** `test_the_tripwire_half_of_arm_four_blocks_a_row_the_checker_calls_clean`.

**Claim: measured.** Calibration is pre-registered prediction 5, scored per cell with Brier.

### A4. The weaker reviewer

**Failure.** Drafting with the strong model and reviewing with the cheap one, so the reviewer can be
out-argued by fluency.

**Mechanism.** The checker model must be at least as strong as the drafter, refused at construction.

**Test.** `test_a_checker_model_weaker_than_the_drafter_is_refused`.

**Claim: structural invariant, tested.**

### A5. Checker unavailable, treated as pass

**Failure.** The checker errors or returns something unparseable, and the gate degrades to allow. The
system is then most degraded and least supervised at the same moment.

**Mechanism.** Fail-closed. After a bounded retry budget, it raises rather than allowing.

**Test.** `test_an_unparseable_response_after_the_retry_budget_raises_checker_unavailable`.

**Claim: structural invariant, tested.**

### A6. Single-detector content classes

**Failure.** A content class covered by the checker alone, or by a tripwire alone, so one detector's
blind spot is the system's blind spot.

**Mechanism.** Every content class carries both.

**Test.** `test_every_content_class_has_both_a_checker_and_a_tripwire`.

**Claim: structural invariant, tested.**

### A7. Tripwires claiming act-class territory

**Failure.** A lexical detector appears to cover an act-class, and the stronger claim silently attaches
to a regex.

**Mechanism.** Tripwire classes are restricted to the content family.

**Test.** `test_tripwires_only_cover_content_classes`;
`test_act_classes_and_content_classes_report_their_family`.

**Claim: structural invariant, tested.**

### A8. Self-policing collapse

**Failure.** The drafting model polices its own output. The obvious cheap design, and the one with no
independence at all.

**Mechanism.** None here. This is arm 1 of the attribution ladder, and no honest verdict source for it
exists in this artifact. Rather than approximate it from another arm, `ABSENT_ARMS` records it as
missing and the ladder reports three rungs as three rungs.

**Test.** `test_the_missing_first_rung_is_named_as_absent_rather_than_built_from_another_arm`.

**Claim: designed, not built.** The artifact declines to measure this rather than faking a baseline.
A fabricated arm 1 would have flattered every arm above it.

### A9. Cross-turn accumulation

**Failure.** Every individual turn passes the gate, and the conversation as a whole still arrives
somewhere it should not. Nothing is a breach; the sequence is.

**Mechanism.** The checker receives the thread, so the information is present. No thread-scope
predicate exists.

**Test.** None yet. This is the residual, named rather than hidden.

**Claim: designed, not built.** Per-draft gating is what is evidenced here, which is a narrower claim
than "the conversation is governed".

---

## B. Act-class failures

All five are decided by pure functions in `src/chaperone/policy/act_classes.py` over the draft, the
record, and an explicit context.

| Class | Failure | Claim |
|---|---|---|
| `act:figure_not_in_record` | The draft states a number that appears nowhere in the record. The classic confabulation, in the place it costs most. | **Zero by construction** |
| `act:no_approval_token` | An action at tier 2 or above proceeds without approval. | **Zero by construction** |
| `act:jurisdiction_not_consented` | Contact reaches a jurisdiction with no consent on file. | **Zero by construction** |
| `act:tool_outside_grant` | A tool outside the agent's grant is invoked. | **Zero by construction** |
| `act:send_cap_exceeded` | Agreed contact volume is exceeded. | **Zero by construction**, with the caveat in B1. |

**Test.** `test_act_class_escape_rate_is_zero_over_the_scripted_suite`;
`test_arm_four_has_zero_act_class_escapes`. This is the one rate CI asserts, because it is the one that
is an invariant rather than an observation.

**The denominator, stated with the claim.** Four of the five carry no labelled corpus row, because the
corpus varies the record and nothing else. The measured zero rests on the 5 `act:figure_not_in_record`
rows per split. The other four are zero by construction from the code, not from the corpus, and the
distinction matters.

### B1. The send cap, and the one way it fails open

**Failure.** The send cap counts intent entries in the audit log. A lost line is therefore not merely a
gap in the record, it is a **predicate failing open**: the count comes back lower than the truth and
the cap permits a send it should have refused.

**Mechanism.** The log is treated as an enforcement input rather than as a report. Entries are written
before the effect, fsynced, appended in binary mode, and a torn tail is detected rather than silently
truncating the count.

**Test.** `test_append_fsyncs_the_store_files_descriptor_after_the_bytes_are_written`;
`test_an_append_after_a_tear_is_readable_and_the_count_does_not_undercount`;
`test_the_store_opens_in_binary_append_mode`.

**Claim: structural invariant, tested.** This is the one act-class whose guarantee depends on
durability rather than on arithmetic alone, and saying so is the difference between the claim being
true and being nearly true.

---

## C. Gate and permission architecture

### C1. An eval score used as an authorization

**Failure.** The quality lane and the permission lane share a mechanism, so a high enough score becomes
a permission. Usually arrives as an optimisation: the judge is already running, so why run a gate too.

**Mechanism.** Two lanes, two implementations, no shared path. `demo/day2.py` exists to show them
disagreeing on one draft.

**Test.** The demo's `assert not entered`, run by CI as the `Day-2 demo` step.

**Claim: structural invariant, tested.**

### C2. Policy importing a model client

**Failure.** A predicate in `policy/` calls a model, a clock, or the network. Every act-class claim
silently downgrades from a guarantee to a rate.

**Mechanism.** Enforced at three moments: an edit-time `PreToolUse` hook, `tools/static_audit.py` in
CI, and a test against the real package.

**Test.** `test_the_real_policy_package_is_pure`.

**Claim: structural invariant, tested.**

### C3. The retry loop on a terminal denial

**Failure.** A denial is marked retryable, an agent retries the identical call, and the loop reads as a
transient failure rather than a policy boundary.

**Mechanism.** `is_retryable` is false on every denial without exception. Whether a *different* draft
could pass is a separate field, `disposition`.

**Test.** `test_deny_is_never_relabelled_as_retryable`; `test_a_refinable_denial_is_still_not_retryable`;
`test_disposition_is_derived_from_category_in_one_place`.

**Claim: structural invariant, tested.**

### C4. The enforcement layer that can be bypassed

**Failure.** The permission decision sits in a `can_use_tool` callback, which `bypassPermissions` skips
and a competing registration can shadow. The layer is present in the source and inert at runtime.

**Mechanism.** A `PreToolUse` hook, out of process, plus an executor chokepoint that cannot be reached
around because it performs the call.

**Test.** `test_the_same_policy_denies_at_all_three_layers`;
`test_the_two_enforcement_layers_reach_the_same_verdict_and_the_same_category`.

**Claim: structural invariant, tested.**

### C5. Overclaiming the portable layer

**Failure.** Saying "the same policy runs at every layer" when the out-of-process layer cannot call a
model checker and therefore enforces a strict subset.

**Mechanism.** The gap is named in the suite rather than in a comment.

**Test.** `test_the_out_of_process_layer_enforces_a_strict_subset_and_names_the_gap`;
`test_only_the_pure_layer_ports_out_of_process`.

**Claim: structural invariant, tested.**

---

## D. Audit failures

### D1. Tampering

**Failure.** An entry is edited, removed, or reordered after the fact.

**Mechanism.** Hash-linked chain. Each entry's digest covers the previous hash, so any single change
invalidates every link after it, and the break is localised by index.

**Test.** `test_a_tampered_entry_is_detected_and_the_index_is_named`; `test_a_removed_entry_is_detected`;
`test_a_reordered_pair_is_detected`;
`test_the_prev_hash_is_hashed_in_so_the_same_payload_yields_a_different_digest`;
`test_the_link_digest_matches_an_independently_computed_sha256`.

**Claim: detection only.** Not prevention, and the difference is load-bearing. See D5.

### D2. The effect that outran its record

**Failure.** The send happens and the crash lands before the log line, so the record shows no attempt
where an attempt occurred. Because the cap counts intents, this also under-counts.

**Mechanism.** Write-ahead intent, then outcome. The intent is durable before the effect is attempted.

**Test.** `test_an_effectful_send_writes_an_intent_entry_before_the_outcome`;
`test_intent_precedes_outcome_in_the_recorded_order`;
`test_an_interrupted_send_leaves_its_intent_pending_and_its_outcome_an_error`.

**Claim: structural invariant, tested.**

### D3. The dangling intent

**Failure.** The gate raises, and an intent entry sits forever with no outcome, so a later reader
cannot tell a crash from an in-flight call.

**Mechanism.** A raising gate still writes an outcome.

**Test.** `test_a_gate_that_raises_still_writes_an_outcome_and_leaves_no_dangling_intent`.

**Claim: structural invariant, tested.**

### D4. The log as a second copy of the payload

**Failure.** The audit record contains the raw arguments, so the thing built for accountability becomes
a durable copy of the sensitive data.

**Mechanism.** `arg_digest`, a canonical-JSON SHA-256 over the arguments. The digest pairs an intent
with its outcome without either entry holding the values. An argument that cannot be digested is
marked as such and still holds no raw value.

**Test.** `test_arg_digest_is_stable_and_does_not_contain_the_raw_value`;
`test_a_digest_that_could_not_be_computed_is_marked_and_still_holds_no_raw_argument`;
`test_an_intent_and_its_outcome_carry_the_same_argument_digest`.

**Claim: structural invariant, tested.**

### D5. The whole-chain rewrite

**Failure.** An actor with write access rewrites every entry and recomputes every hash. The chain
verifies.

**Mechanism.** None in this repository. Append-only storage under separate credentials is the answer,
and it is infrastructure rather than code.

**Test.** None, and none is possible at this layer.

**Claim: detection only, with a stated limit.** What the design defeats is edits, deletions, and
reorderings by an actor who cannot rewrite the whole file.

---

## E. Measurement failures

These are the ones that produce a number which is wrong in a direction that flatters the author.

### E1. Labels that agree with the detector by construction

**Failure.** Deriving labels by scanning bodies for marker substrings. A compliant draft carrying a
marker word is labelled violating, and, expensively, a violation whose author never used the marker
word is labelled compliant, so the escape is scored as a success.

**Mechanism.** Labels derive from each row's recorded provenance, the class the blind author set out to
instance, and from no reading of the text.

**Test.** `test_every_body_and_intent_is_the_blind_authors_own`.

**Claim: structural invariant, tested**, with a residual stated in E2.

### E2. The labels' own error

**Failure.** Provenance records what an author set out to write, not what the text achieves. A draft
intended as a violation that lands innocuous is still labelled violating, and every arm that allows it
is charged an escape it did not commit.

**Mechanism.** None. Detecting it would mean reading bodies with a detector, which is the circularity
the labels exist to avoid.

**Test.** None. This is stated in `PREREGISTRATION.md` rather than discovered later.

**Claim: measured, with an unquantified residual.** The trade is deliberate: a substring rule's errors
correlate with the tripwires' own vocabulary and bias the measurement toward flattering arm 4, while an
author's misses land in no particular direction.

### E3. Tuning on the split you report

**Failure.** Tripwires are tuned until eval looks good, and the reported rate measures the tuning.

**Mechanism.** Tripwires are tuned on dev only. Eval is touched once.

**Test.** Procedural, fixed in `PREREGISTRATION.md` before any number existed.

**Claim: structural invariant**, witnessed by `git log` rather than by the suite.

### E4. The prediction revised after the result

**Failure.** A prediction that fails becomes a prediction that was always about something else.

**Mechanism.** Pre-registration with committed interpretations for both branches, including the
explicit statement that a failed prediction is reported as a failed prediction.

**Test.** `tests/test_preregistration.py` holds every corpus cell to `corpus/labels.jsonl`, so the
counts cannot drift. The ordering claim is witnessed by git history alone, which the document says
outright.

**Claim: structural invariant**, with the honest note that no test can prove a document preceded a
measurement.

### E5. The rate without a denominator

**Failure.** "6% escape rate" travels; "3 of 50 on the eval split" does not fit in a headline. The
first is unfalsifiable in conversation and the second is checkable.

**Mechanism.** A rate is never reported without its denominator, and a rate over an empty denominator
is reported as absent rather than as zero.

**Test.** `ArmResult` carries both denominators beside both counts as a matter of type, not of
convention.

**Claim: structural invariant, tested.**

### E6. The rigged baseline

**Failure.** The comparison arm gets less engineering effort than the proposed one, converting an
argument into a demo. A technical reader spots it immediately.

**Mechanism.** Every arm judges the same fixed drafts at the same chokepoint, over recorded verdicts
replayed identically. Arms differ by exactly one variable each. The prompting-only reference is
reported separately and explicitly as a multi-variable comparison, never as a rung of the ladder.

**Test.** `test_the_reference_arm_measures_the_absence_of_a_chokepoint_and_not_the_failure_of_prompting`;
`test_escape_rate_is_monotone_across_the_ladder_on_frozen_replays`.

**Claim: structural invariant, tested.**

### E7. The unmeasurable cost, reported as a measured zero

**Failure.** Arm 4 adds tripwires, tripwires can only add blocks, and the false-block rate comes back
at zero. Reported as "the tripwires cost nothing".

**Mechanism.** The corpus supplies no compliant near-miss inside the tripwires' reach, so this artifact
**cannot** measure the false-block cost of lexical tripwires. Written down in advance, so a flattering
result cannot be read as a finding.

**Test.** `test_the_act_lane_contributes_no_false_block_on_this_corpus` establishes that the act half
contributes exactly zero, which is what isolates the tripwire half as the whole of the question.

**Claim: measured, with a stated ceiling on what the measurement can support.**

---

## F. Testing failures

### F1. The proxy test

**Failure.** A spy counts that the checker was called and the test passes. It proves the checker ran,
not that it governed. The system can then ignore the verdict entirely and stay green.

**Mechanism.** A repository rule: assert the property, not a proxy for it. Assert effects, never
invocations. `demo/day2.py` asserts `not entered` against a recording registry rather than asserting
that the gate was consulted.

**Test.** `tests/test_no_empty_tests.py` catches the degenerate case of a test that asserts nothing at
all.

**Claim: structural invariant, tested**, for the degenerate case. The general case is a review
discipline, and calling it more than that would itself be a proxy claim.

### F2. Network in the suite

**Failure.** A test reaches a live model, so the suite is slow, flaky, keyed, and measures the provider
as much as the code.

**Mechanism.** Everything runs offline and keyless through recorded transports. This is also what lets
every arm be compared over identical verdicts rather than over independent samples.

**Test.** The suite runs with no credentials present.

**Claim: structural invariant, tested.**
