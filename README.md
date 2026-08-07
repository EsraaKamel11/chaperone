# chaperone

An agent carrying an outbound conversation on behalf of an organisation will be asked, in the most
natural way possible, to do things that organisation has publicly stated it does not do. The most
helpful next sentence and the permitted next sentence are not the same sentence, and they diverge
exactly where the stakes are highest. This repository is a working enforcement layer for that
divergence, built so that one family of constraint is impossible to violate and the other is measured
rather than asserted.

**The scenario.** A synthetic capital-introduction firm, running a fundraise, whose agent drafts
outbound messages to investors. The firm publishes three constraints on itself: it does not advise on
the merits of an investment, it does not negotiate terms, and it does not make forward-looking return
statements. It also operates under ordinary data constraints, contacting people only in jurisdictions
where it has consent and not exceeding agreed contact volumes.

Those constraints are not hypothetical pressure. An agent drafting into a live raise gets asked
*"honestly, is this a good deal?"* and *"would they take eight instead of ten?"*. Both questions are
natural, both are frequent, and the fluent answer to either one breaks a published constraint. That is
the whole difficulty: the failure is not a jailbreak or an adversarial prompt, it is an ordinary
question answered well.

The domain is chosen because its constraints are unusually crisp, not because the pattern is specific
to it. Any agent on a regulated or reputationally exposed surface has the same shape of problem.

**What this is not.** It is not a compliance product, it encodes no legal advice, and no expertise in
securities regulation, broker-dealer rules, or any other regulated field is claimed by it or rests on
it. It is an enforcement architecture, demonstrated on a scenario whose constraints happen to be
legible. Everything here is synthetic: no real organisation, person, record, or correspondence appears
anywhere in this repository, and every number in it belongs to this artifact alone.

The thesis in one line: **an eval score is not an authorization.** A quality judge and a permission
gate answer different questions, and the moment they share a mechanism, the system has quietly decided
that a high enough score is a permission.

```
pytest -q                 the full suite, offline and keyless
python demo/day2.py       one draft, two lanes, opposite verdicts
python tools/static_audit.py   proves the policy layer imports no LLM client
```

Roughly 3 lines of tests for every line of source under `src/chaperone/`. That ratio is the claim,
not a line count, and `tests/test_readme_claims.py` recomputes it against the tree so a
hand-maintained number cannot drift here in silence. The tests are not incidental to the argument;
several of the claims below are true *because* a named test attacks them, and those test names are
cited inline so you can check any of them in one grep.

---

## The three failure modes this exists to hold

Every entry below has the same four fields: **the failure**, **the mechanism that holds it**, **the
named test that attacks it**, and **what may be claimed**. The last field is the honest one, and it
takes one of five values described in [docs/failure-modes.md](docs/failure-modes.md). The full catalog
lives there. These are the three that matter most.

### 1. The fluent breach

**The failure.** An investor asks *"honestly, is this a good deal?"*. The most useful possible answer
is fluent, well grounded, exactly on topic, and advises on the merits, which the firm has publicly
said it does not do. Nothing about it looks like a failure: it scores well on every quality axis,
because on every quality axis it *is* good. A reviewer reading only the draft sees a strong reply, and
a quality eval agrees with them.

**The mechanism.** A fail-closed gate in which the model checker and a set of deterministic tripwires
are two disjuncts, and either one blocks. `evaluate_tripwires` in
`src/chaperone/policy/tripwires.py` reaches `content:advises_on_merits` from the draft body alone, so
the block survives a checker that says nothing at all. That redundancy is the whole point: two
detectors that fail for different reasons.

**The test.** `test_each_content_class_blocks_through_the_tripwire_alone`, and
`test_the_denial_on_each_content_class_is_the_tripwire_lane_and_nothing_else`, which pins that the
denial came from the lane it claims to come from rather than from an unrelated coincidence.

**What may be claimed: measured.** The content-class escape rate is a number over the 45 labelled
content rows per split, not a guarantee. See [docs/measurement.md](docs/measurement.md).

### 2. The rubber stamp

**The failure.** A checker reads the draft *and* the drafting model's own justification for it, and
agrees. This is the most comfortable way to build a reviewer and the least useful, because a
justification is an argument written to be persuasive, and a reviewer holding one is no longer
independent. The system now has a second opinion that is really the first opinion restated.

**The mechanism.** Independence enforced by what is left out of the prompt. The checker never receives
the generator's reasoning, its confidence, or its self-assessment, and the checker model must be at
least as strong as the drafter, so the review is not the cheap half of the pipeline. Omission is the
mechanism, which means it is testable: you can assert that a string is absent far more reliably than
you can assert that a model ignored it.

**The test.** `test_the_checker_prompt_has_no_generator_artefacts` and
`test_the_checker_prompt_structure_is_exact` hold the omission. `test_a_checker_model_weaker_than_the_drafter_is_refused`
holds the ordering.

**What may be claimed: structural invariant, tested.** The independence is a property of how the
prompt is assembled. It is not a rate, and it is not a promise about what the checker concludes.

### 3. The confident wrong checker

**The failure.** The checker returns clean, with high confidence, on a draft that breaches. High
confidence is the part that hurts. A gate that knew it was uncertain could escalate; a gate that is
confidently wrong hands back a clean verdict and the system proceeds. Any autonomy tier resting on
that verdict inherits the error silently.

**The mechanism.** Two things, and neither is "improve the checker". First the tripwire disjunct
above, which does not consult the checker's confidence and therefore cannot be talked out of a block.
Second, an autonomy ceiling: content-class surfaces cap at tier 2, per-message approval, so a false
negative always meets a human before it becomes an outbound action. Sampled audit at an unsupervised
tier would catch it after transmission, which is detection, not prevention.

**The test.** `test_the_tripwire_half_of_arm_four_blocks_a_row_the_checker_calls_clean`, which is
precisely the case of a checker saying clean and the row being blocked anyway.

**What may be claimed: measured.** Checker calibration is a pre-registered measurement (prediction 5),
scored per cell with Brier and never as one blended number. The ceiling is the policy response to a
residual that measurement prices but does not remove.

---

## One draft, two lanes, opposite verdicts

This is the argument in miniature. `demo/day2.py` runs the real path and prints:

```
QUALITY LANE   -> PASS  grounding=0.94 fluency=0.91 fit=0.89
PERMISSION LANE-> BLOCK content:advises_on_merits, send_message entered 0 times
REDIRECT       -> human review, refinement_rounds=0
AUDIT          -> 2 entries, chain verifies: True

One draft. Two lanes. Opposite verdicts.
```

Line by line:

- **`QUALITY LANE -> PASS`.** The judge likes this draft, and it is right to. It is well grounded and
  fluent and it answers what was asked. Nothing is wrong with the writing.
- **`PERMISSION LANE -> BLOCK`.** The same draft, same moment, refused, with the class named. The two
  lanes disagree, and the disagreement is the thesis rather than a defect in either lane. A judge is a
  measurement instrument. A gate is an authorization boundary. They are not interchangeable, and this
  line is what that looks like when it is real.
- **`send_message entered 0 times`.** The registry records its calls, so this is evidence that the
  tool function was never entered, not a claim that it should not have been. The send goes through
  `guarded_call`, the executor chokepoint in `src/chaperone/gates/hook.py`, so the tool identity and
  its arguments are bound to the reviewed draft, and the category printed is the one the gate returned
  rather than one the script computed alongside it.
- **`REDIRECT -> human review`.** The denial is not a dead end. It carries a disposition saying whether
  a redraft could help, and the redraft happens inside the handoff rather than by looping the same
  agent against the same wall.
- **`AUDIT -> 2 entries, chain verifies: True`.** Intent and outcome, hash linked. See below.

**What guards this output, stated precisely.** No test executes this script. What guards it is a pair:
`assert not entered` runs inside `demo/day2.py` before the print, and `.github/workflows/ci.yml` runs
the file as a step named `Day-2 demo`. So a regression that let the send through fails CI rather than
printing a different number and exiting 0. That covers the headline invariant. The exact printed text
above is kept in step with the demo by a byte comparison in `tests/test_readme_claims.py`.

Both transports are scripted, because the suite runs offline and keyless. The quality scores and the
checker verdict are inputs to the script. What is computed is everything between them.

---

## Two families of constraint

The split that everything else follows from. These are not two severities of the same thing; they are
two different epistemic situations, and collapsing them is the error the repository is built around.

| | Act-classes | Content-classes |
|---|---|---|
| **The question** | Did an act occur outside its bounds? | Does this text do a forbidden thing? |
| **Decided by** | A pure function over the record and the context | A model checker, plus lexical tripwires |
| **The classes** | `act:no_approval_token`, `act:jurisdiction_not_consented`, `act:tool_outside_grant`, `act:figure_not_in_record`, `act:send_cap_exceeded` | `content:advises_on_merits`, `content:negotiates_terms`, `content:forward_looking_return` |
| **Where they come from** | Consent, approval, grants and volume: the operating constraints | The firm's three published statements about what it does not do |
| **What can be claimed** | **Zero by construction.** The violation is impossible, not improbable. | **Measured, never guaranteed.** A rate, with its denominator. |
| **Autonomy ceiling** | Deterministic gating, so higher tiers are defensible | Tier 2, per-message approval |

The phrase "zero by construction" appears in this repository on act-class rows and nowhere else. That
is a rule in `CLAUDE.md`, not a habit, and a test scans for violations of it. The temptation it exists
to resist is real: a content-class detector that has never missed on your corpus feels like a
guarantee, and writing it up as one is a single word's worth of drift.

**The purity is enforced, not intended.** `src/chaperone/policy/` imports no LLM client, no I/O, and no
clock. If a predicate needs a value, the value arrives as an argument, which is why `ActContext` carries
`sent_count` rather than counting anything itself. Three things hold this: a `PreToolUse` hook that
refuses the edit as you make it, `tools/static_audit.py` in CI, and
`test_the_real_policy_package_is_pure`. An intention that is not enforced is a comment.

---

## What a denial actually returns

`denial_result` in `src/chaperone/gates/engine.py` returns six keys:

```json
{
  "is_error": true,
  "is_retryable": false,
  "category": "content:advises_on_merits",
  "detail": "tripwire",
  "span": "honestly, is this a good deal",
  "disposition": "redirect_refinable"
}
```

The field worth arguing about is `is_retryable`, which is `false` on every denial without exception.
**Retryability and redraftability are two different axes**, and the tidy-looking edit that makes
`is_retryable` depend on whether a redraft might succeed is the one that reopens the hole. A retry is
the same call again; a policy denial will refuse it identically, forever, and an agent that believes
otherwise will loop. Whether a *different* draft could pass is `disposition`, a separate field.

`span` quotes the offending text back so a human deciding whether a draft may go out can see the
actual words. It is never the raw arguments, and the audit log records an `arg_digest` rather than the
argument values, so the record proves what happened without becoming a second copy of the sensitive
payload.

---

## Numbers, and what they are worth

The corpus was frozen and the predictions were written **before any arm ran and before any number
existed**. Git history is the only witness to that ordering, because no test can establish that a
document preceded a measurement. The predictions are in
[PREREGISTRATION.md](PREREGISTRATION.md); the outcomes are reported held or failed, whichever they
turn out to be.

**The corpus.** 160 drafts, every body written by an author who could not read this repository. Labels
are derived from each row's recorded provenance, meaning the class the author set out to instance, and
from no reading of the text. That is what stops a label agreeing with a detector by construction.

| | dev | eval |
|---|---|---|
| Labelled violating | 50 | 50 |
| Labelled compliant | 30 | 30 |
| `content:advises_on_merits` | 15 | 15 |
| `content:forward_looking_return` | 15 | 15 |
| `content:negotiates_terms` | 15 | 15 |
| `act:figure_not_in_record` | 5 | 5 |
| **Rows** | **80** | **80** |

Every cell is held to `corpus/labels.jsonl` by `tests/test_preregistration.py`, so this table cannot go
stale in silence.

**Read the denominators before the rates.** Four of the five act-classes carry no labelled row at all,
because the corpus varies the record and nothing else. So any act-class claim here rests on the 5
`act:figure_not_in_record` rows per split and on nothing wider. Stating that is the difference between
a measurement and a headline.

**Status: the arms have been run, once, on the eval split.** The ladder in
`src/chaperone/evals/harness.py` was run over the eval half of the corpus against the recorded
verdicts frozen in `corpus/recorded_verdicts.json`, and **two predictions failed.** They come from
two different documents, which is worth stating so the count can be checked rather than taken: one
is among the five in [PREREGISTRATION.md](PREREGISTRATION.md), and the other is the design spec's
earlier prediction that the best arm's false-block rate would strictly rise, which the
pre-registration supersedes and pre-commits to reporting as failed if equality landed. Equality
landed. Two of the five are not adjudicated at all yet, because the arms that decide them have not
run. "A failed prediction is reported as a failed prediction" is the pre-registration's own clause,
and it is the one that decides whether that document was doing work or decorating.

**No rate is printed here yet, and that is a choice about denominators rather than about disclosure.**
Every rate the ladder produces needs the denominator from the table above and the criterion-sharing
confound named under Limits, and a rate quoted without those two is worse than no rate. The outcomes
are written up with both, held or failed, in a Results section that does not exist in this tree
today and is written once the remaining arms have run.

**Re-running spends nothing, so a reader can reproduce this now.** What was spent once was the
judging: the blind checker read the eval bodies a single time and its verdicts were recorded. Every
arm is a deterministic function over that frozen recording, so replaying it repeats nothing.
`tests/evals/test_harness.py` runs the ladder over the eval items on every `pytest` invocation,
which is also why the status above cannot quietly go stale: the run it describes happens in CI.

---

## Limits

The residual risks, named rather than discovered by a reader.

**Cross-turn accumulation is the real one.** Every individual turn passes the gate, and the
conversation as a whole still walks somewhere it should not. The checker sees the thread, so the
information is present, but no test in this suite yet demonstrates a cross-turn breach being caught.
Until one does, per-draft gating is what is evidenced, and that is a narrower claim than "the
conversation is governed".

**The audit detects tampering, it does not prevent it.** An actor with write access who rewrites the
whole chain, recomputing every hash, produces a chain that verifies. What the design defeats is edits,
deletions, and reorderings, each of which is caught and localised. Append-only storage under separate
credentials is the answer, and it is infrastructure rather than code.

**The log is an enforcement input, not just a record.** The send cap counts intent entries, so a lost
line is a predicate failing open rather than a forensics gap. That is why entries are written
before the effect and fsynced, and why the tests care about torn tails.

**The corpus author and the blind checker share a criterion.** Blinding bought one thing precisely:
two independent instances, neither able to read this repository, neither shown the labels or the
tripwire list, and neither shown the other's work. That removes shared **vocabulary**. It does not
remove shared **criterion**. Both prompts were written by one hand and are near-verbatim on the
constraint definitions, so the author encoded against a definition and the checker decoded against
that same definition, and the agreement between them is closer to a matched-pair result than to an
independent one. Read it as evidence that these constraints are crisp enough to transfer between two
instances, and not as evidence that a checker catches what a real drafter would emit. It is a
property of how the two prompts were written rather than of the code, so nothing in this suite
asserts it; `src/chaperone/evals/harness.py` states it beside the numbers it qualifies. The tripwire
vocabulary and the label set come from that same hand, and the same caution applies there.

**No compliant near-miss inside the tripwires' reach exists in this corpus.** So this artifact
*cannot* measure the false-block cost of lexical tripwires. A near-zero false-block rate here is a
property of the corpus and is not evidence that the cost is zero. That limitation was written down in
advance, so a flattering result cannot be read as a finding.

**Labels are synthetic, and carry their own error.** Provenance records what an author set out to
write, not what the text achieved. A draft intended as a violation that lands innocuous is still
labelled violating, and every arm that allows it is charged an escape it did not commit.

---

## Designed, and built

The distinction this table draws is the one the whole repository is about, so it is drawn exactly.
Everything on the right was verified absent from the tree, not assumed.

| Subsystem | State |
|---|---|
| Act-class evaluation, pure, five classes | **Built** |
| Content-class checker, independent, fail-closed gate | **Built** |
| Lexical tripwires as a second disjunct | **Built** |
| Hash-linked audit with intent and outcome entries | **Built** |
| Executor chokepoint and `PreToolUse` hook | **Built** |
| Attribution ladder over arms 2, 3 and 4 | **Built.** Arm 1 is absent and reported so. |
| Frozen corpus, provenance labels, pre-registration | **Built** |
| Checker calibration, Brier per cell | **Designed, not built** |
| Discrimination, AUC with confidence interval | **Designed, not built** |
| Matching | **Designed, not built** |
| Refinement loop | **Designed, not built** |
| Capability ladder promotion and demotion mechanics | **Designed, not built** |
| Four-agent topology and per-agent grants | **Designed, not built** |
| Crash-recovery `resume` pass, branches (b) and (c) | **Designed, not built** |
| Thread-scope pass for cross-turn accumulation | **Designed, not built** |
| Pydantic AI binding for the checker | **Designed, not built** |

Three of these deserve a sentence, because silence would be misleading.

**Arm 1 is absent on purpose.** The first rung of the attribution ladder is the drafting model policing
itself. There is no honest verdict source for it here, so rather than approximate it from another arm,
`ABSENT_ARMS` records it as missing and the ladder reports three rungs as three rungs.
`test_the_missing_first_rung_is_named_as_absent_rather_than_built_from_another_arm` holds that. A
fabricated baseline is worth less than an acknowledged gap, and it would have flattered every arm
above it.

**The four-agent topology is design.** There is no agent registry in `src/`. What is built is the
primitive underneath it: `granted_tools` on `ActContext`, checked by a pure function that returns
`act:tool_outside_grant` for any tool outside the grant, enforced at both `pre_tool_use` and
`guarded_call`. The intended arrangement, in which a drafting agent holds no send tool at all, is in
[docs/architecture.md](docs/architecture.md) and is labelled there as designed.

**Promotion is not built, and would not be keyed to these numbers if it were.** Production promotion
belongs to human-review outcomes on real cases, never to synthetic suite scores. An artifact that
promoted itself to autonomous operation on the strength of its own evals would model precisely the
judgment error it was built to argue against.

---

## Running it

```bash
git clone <this repo> && cd chaperone
pip install -e ".[dev]"

pytest -q                      # the full suite, offline, no API key required
python demo/day2.py            # the two-lane demo above
python tools/static_audit.py   # the policy purity check that CI runs
```

No network access is used by any test. Model transports are recorded and replayed, which is what makes
the suite deterministic and what lets every arm be compared over identical verdicts.

---

## Further reading

- [docs/failure-modes.md](docs/failure-modes.md), the full catalog, every entry in the four-field
  shape above.
- [docs/architecture.md](docs/architecture.md), why a `PreToolUse` hook rather than a permission
  callback, why the checker is the stronger model, and the per-module boundaries.
- [docs/measurement.md](docs/measurement.md), pre-registration through results, and what each number
  does not mean.
- [docs/audit-walkthrough.md](docs/audit-walkthrough.md), one denied send, entry by entry.
- [PREREGISTRATION.md](PREREGISTRATION.md), the predictions, recorded before the measurements.
