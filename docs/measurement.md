# Measurement

What is measured, how, and what each number does not mean. The predictions themselves live in
[PREREGISTRATION.md](../PREREGISTRATION.md), committed before any of this ran.

**Status: the arms have not been run.** There is no `RESULTS.md` in this tree. This page describes the
measurement design and the corpus, both of which exist and are tested, and marks the results section as
pending rather than filling it with anything.

---

## 1. Why pre-registration, in an artifact nobody is auditing

Because the alternative is unfalsifiable. A repository that reports numbers chosen after the numbers
were seen can produce any conclusion its author preferred, and no reader can tell the difference from
the outside. Publishing predictions first is the only cheap way to make the later claims checkable.

The honest limitation, stated in the pre-registration itself: **no test can establish that a document
preceded a measurement.** Git history is the only witness. `tests/test_preregistration.py` holds every
corpus cell to `corpus/labels.jsonl` so the counts cannot drift in silence, but the ordering claim
rests on `git log` and on nothing in the suite. Saying so is part of the point. An artifact that
claimed its test suite proved the ordering would be making exactly the category error it exists to
argue against.

---

## 2. The corpus

160 drafts. Every body written by an author who could not read this repository, which is what makes the
drafts blind to the detectors that will judge them.

| | dev | eval | total |
|---|---|---|---|
| `content:advises_on_merits` | 15 | 15 | 30 |
| `content:forward_looking_return` | 15 | 15 | 30 |
| `content:negotiates_terms` | 15 | 15 | 30 |
| `act:figure_not_in_record` | 5 | 5 | 10 |
| **Labelled violating** | **50** | **50** | **100** |
| **Labelled compliant** | **30** | **30** | **60** |
| **Rows** | **80** | **80** | **160** |

Held cell by cell by `tests/test_preregistration.py`.

### 2.1 Labels come from provenance, not from text

A label records **the class the blind author set out to instance**, taken from the row's recorded
intent, with no reading of the body.

The alternative, deriving labels by scanning bodies for marker substrings, is wrong in both directions.
A compliant draft that happens to use a marker word is labelled violating. And, expensively, a
violation whose author never used the marker word is labelled compliant, so the escape it represents is
scored as a success. Worse, those errors correlate with the tripwires' own vocabulary, which biases the
measurement in the direction that flatters the best arm.

`test_every_body_and_intent_is_the_blind_authors_own` holds the blindness.

### 2.2 What the labels get wrong, stated in advance

Provenance records what an author set out to write, not what the text achieves. A draft intended as a
violation that lands innocuous is still labelled violating, and every arm that allows it is charged an
escape it did not commit.

Nothing detects this, because detecting it would mean reading the bodies with a detector, which is the
circularity the labels exist to avoid. The residual is real and unquantified. The trade is deliberate:
an author's misses land in no particular direction, while a substring rule's misses land in the
direction that flatters the system.

### 2.3 Four of five act-classes carry no row

The corpus varies the record and nothing else. `CONTROLLED_CONTEXT` holds a fixed approval token, a
consented jurisdiction, a granted tool, and a send count far below the cap, so
`act:no_approval_token`, `act:jurisdiction_not_consented`, `act:tool_outside_grant` and
`act:send_cap_exceeded` are silent on all 160 rows.

**The tier is not what silences the first of those.** `CONTROLLED_CONTEXT` sets `tier=2`, and tier 2 is
precisely the tier at which an approval token becomes required. That class is at its firing condition
on every single row and is held quiet by the token's presence alone. Naming the tier as the suppressor
would point a reader at the one field in that context that is *not* doing the work.

So any act-class claim from this corpus rests on the 5 `act:figure_not_in_record` rows per split and
on nothing wider. The other four are zero by construction from the code rather than from the corpus.

---

## 3. The attribution ladder

One verdict source per rung, over one frozen corpus. Arms do not produce drafts; each arm judges the
same fixed drafts at the same chokepoint.

| Arm | Verdict source | Differs from the previous by |
|---|---|---|
| 1 | The drafter's own model, tuned policing prompt | **Absent.** See 3.1. |
| 2 | Independent checker, model at least as strong as the drafter | Model identity |
| 3 | Arm 2 inside a fail-closed gate | Failure behaviour |
| 4 | Arm 3 plus act-classes and tripwires | The deterministic layer |

One variable per rung is the whole design. An ablation where two things change at once cannot
attribute the difference to either.

### 3.1 Arm 1 is absent, and that is reported rather than patched

There is no honest verdict source here for the drafting model policing itself. Rather than approximate
it from another arm, `ABSENT_ARMS` records it as missing and `Ladder` reports three rungs as three
rungs. `test_the_missing_first_rung_is_named_as_absent_rather_than_built_from_another_arm` holds it.

The temptation is to synthesise arm 1 from arm 2's transcripts, which would produce a baseline that is
worse than every arm above it and would flatter all of them. An acknowledged gap is worth more than a
fabricated floor.

`ABSENT_ARMS` is a module constant a consumer has to know to go looking for, so `Ladder` surfaces
`absent` in its own result rather than leaving the omission to be discovered.

### 3.2 The prompting-only reference is not a rung

A generation-stage prompting arm with no chokepoint detector is reported separately and explicitly as a
**multi-variable** comparison. It differs from arm 4 in more than one way, so presenting it as a rung
would attribute to the chokepoint a difference that prompting placement also explains.
`test_the_reference_arm_measures_the_absence_of_a_chokepoint_and_not_the_failure_of_prompting` pins
what it measures.

### 3.3 Scopes partition the item space

Escape rates are reported per scope, and the scopes are disjoint by construction.
`scope="act-classes-only"` counts a content-violating row as **neither** violating nor compliant, so
its rate is over the act-declaring rows alone, 5 per split. `scope="content-classes-only"` is over the
45 content rows per split.

`test_the_act_class_scope_takes_its_answer_from_the_deterministic_layer_alone` and
`test_the_content_class_scope_takes_the_arms_whole_answer_and_not_only_its_act_layer` hold that each
scope reads the right half of an arm's verdict. Mixing them produces a blended rate that means nothing,
because it averages a guarantee with an estimate.

---

## 4. What CI asserts, and what it only reports

The rule: **CI asserts invariants only. Probabilistic rates are measured and reported, never
asserted.**

**Asserted:**
- Act-class escape rate is zero. `test_act_class_escape_rate_is_zero_over_the_scripted_suite`,
  `test_arm_four_has_zero_act_class_escapes`. This is an invariant because pure functions decide it.
- Monotone arm ordering over **frozen recorded replays**.
  `test_escape_rate_is_monotone_across_the_ladder_on_frozen_replays`.

**Reported, never asserted:** every content-class rate, calibration, and discrimination.

The distinction is the difference between a test suite and a wish. Asserting a content-class escape
rate would produce a test that fails when the world changes rather than when the code breaks, which is
the flaky assertion that trains a team to ignore red builds. The monotone ordering is asserted **only**
over frozen replays for exactly this reason: on a live re-run it is a reported observation.

---

## 5. The five predictions, and what each one is worth

Full text in [PREREGISTRATION.md](../PREREGISTRATION.md). What matters here is what each can support.

**1. Act-class escape rate on arm 4 is exactly zero.** Denominator: 5 rows per split. This is the one
rate CI asserts, because it is zero by construction rather than by observation. It says nothing about
content classes.

**2. Content-class escape rate is non-zero on every arm**, ordered `arm4 <= arm3 <= arm2 <= arm1`. The
ordering is an invariant only over frozen replays. **Non-zero on every arm is the point**: this is the
prediction that says a model-based detector does not reach zero, which is why content-class autonomy
caps at tier 2.

**3. False blocks, arm 3 to arm 4: the tripwire half is the only half that can move them.** The
direction is structural rather than predicted, since arm 4 evaluates every disjunct arm 3 does plus two
more and therefore blocks a superset. Only the magnitude is a prediction, and the prediction is
equality. The act half contributes exactly zero, held by
`test_the_act_lane_contributes_no_false_block_on_this_corpus`.

**This prediction supersedes an earlier one in the design spec**, which predicted a strict rise. The
dev-split evidence was against it, and recording a prediction one already holds evidence against is not
pre-registration. The earlier reasoning, that omitting the predicted cost of the best arm would read as
cherry-picking, is right and the direction is kept.

**The limitation, stated before the result.** This corpus supplies no compliant near-miss inside the
tripwires' reach. The artifact therefore **cannot measure the false-block cost of lexical tripwires**.
A near-zero false-block rate on arm 4 is a property of the corpus and is not evidence that the cost is
zero. That sentence exists so a flattering number cannot later be read as a finding.

**4. Discrimination.** AUC of the quality-judge mean score for violating versus compliant drafts has a
95% CI containing 0.5 and excluding 0.75.

If it fails and the CI excludes 0.5, **that is reported as a failed prediction**. The maxim it tests,
that a quality score is not a permission, is normative and survives either result: a judge that
correlates with violations is still a probabilistic instrument being asked to authorize an action.
Writing that down in advance is what stops it becoming a post-hoc rescue.

**5. Checker calibration.** At least one content-class cell shows observed agreement below the
checker's stated mean confidence, scored per cell with **Brier** and never as one blended number.

Per cell matters. A blended score averages a well-calibrated class with a badly-calibrated one and
reports something in between, which is exactly the cell you needed to see, hidden.

**Status: predictions 4 and 5 are both built and have both been measured.**
`src/chaperone/evals/discrimination.py` ranks `corpus/quality-scores.jsonl`, a blind judge's scores
over all 160 bodies, against the labels. `src/chaperone/evals/calibration.py` scores the recorded
verdicts per class with Brier.

**Prediction 5 failed, and the direction is the part to carry away.** No content-class cell showed
observed agreement below the checker's stated confidence, so the checker was underconfident on this
corpus rather than overconfident, and `worst_cell` reports absent rather than the least badly
calibrated cell. The tier-2 ceiling is unmoved by that result and was never resting on it: a false
negative at an unsupervised tier is the breach rather than a warning of one, sampled audit is
detection rather than prevention, and demotion is triggered by a verdict, so a missed violation
misses its own demotion. Finding no error bounds the checker's error rate from above and bounds
nothing about a deployment rate.

**A cell reading perfect agreement is agreement on the boolean, not on the class.** A verdict is
scored correct when it agrees with the label on whether the row violates, which is what the gate acts
on: `decide` blocks on that boolean and a verdict naming the wrong class still blocks the draft. One
eval row labelled `content:forward_looking_return` is caught and named `content:advises_on_merits`,
and it is scored correct inside a cell reading observed agreement 1.0. The class is not inert
downstream, because `disposition_for` reads it, so that row is redirected as futile rather than as
refinable. It is one row out of 45 and it is not a rate; it is stated because "the checker was right
on every content row" is true of the boolean and not of the class.

**The act-class cell is in the table and is excluded from the ranking.** The checker is asked about
three content constraints and about nothing else, so on an `act:figure_not_in_record` row its "no
content violation" answer is the answer it was asked for. Scoring it as an error would charge a
model for a question nobody put to it, and would name an act cell as the measurement behind a
content-class ceiling. `is_content_cell` is where that line is drawn, and the cell keeps its
denominator either way.

**The scores had to be real for this to measure anything.** Every caller of `score_quality` in this
repository hands it a constant transport, so an AUC taken over those scores would have been exactly
0.5 by construction and prediction 4's null would have passed having discriminated nothing. That is
the same defect class as a checker graded against a label derived from the checker.
`test_the_shipped_scores_are_not_one_repeated_value` is what keeps it from coming back, and it
asserts that the instrument has a scale rather than asserting where any label falls on it.

---

## 6. Measurement rules, fixed before the numbers

- The checker runs on **every** draft in the harness, even where production order short-circuits on a
  tripwire hit. Otherwise calibration would be computed on a tripwire-negative selection, which is a
  biased sample of exactly the cases calibration is meant to describe.
- Tripwires are tuned on **dev only**. The eval split is touched once.
- Checker verdicts are recorded once and replayed, so every arm is compared over identical verdicts
  rather than over independent samples of a stochastic process.
- A rate over an empty denominator is reported as **absent**, never as zero.
- Ranking and calibration metrics are computed against labels this repository generated. That is a
  limit on what any number here can support, and it is reported alongside them.

---

## 7. Results

**Pending.** No arm has been run and no rate has been computed. The eval split is touched once by
design, and publishing a figure before the measurement layer is complete would spend that once.

When the arms run, this section carries each prediction's outcome reported **held or failed**, each
rate with its denominator and split, and the calibration table per cell. A failed prediction is
reported as a failed prediction.
