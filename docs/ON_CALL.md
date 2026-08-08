# On-call note: which signal lies, and how

Read this beside [PERTURBATIONS.md](PERTURBATIONS.md), which shows what each safeguard did under one
broken input, and [measurement.md](measurement.md) section 7, which publishes every figure quoted
below with its denominator and its split.

That sentence used to read "where every number below comes from" while three of the figures here
were published nowhere else in the repository, and section 7 announced itself as pending and denied
that any arm had run. A citation a reader cannot follow is worse in this document than in any other,
because following it is the whole point. Both ends are fixed: every figure below now resolves in
section 7 and in the generated [RESULTS.md](RESULTS.md), and
`src/chaperone/evals/calibration.py` and `src/chaperone/evals/discrimination.py` recompute all of
them from the shipped artifacts offline.

Nothing here is a rate to act on. Every figure is a measurement over a frozen synthetic corpus of
160 drafts, and it bounds what this artifact observed, not what a deployment would.

---

## The act lane cannot be argued with, and that is not the same as being right

`src/chaperone/policy/act_classes.py` and `src/chaperone/policy/citations.py` are pure functions over
the draft, the record and the `ActContext`. No model is consulted, so no confidence can lower the bar
and no prompt can talk the lane out of a block.

**That is a claim about the mechanism, and it is not a claim about the answer.** The answer is
exactly as true as the two inputs it was handed, and there are three ways it is not:

- **A stale record.** "This figure is not in the record" means absent from `Record`, which is not the
  same as absent from the world. A false block here means the record is stale, or the canonicalizer
  met a representation it does not handle. Check `src/chaperone/policy/canonical.py` and the record
  before you doubt the gate.
- **A citation the canonicalizer could not read.** When `normalize_money` fails, `validate_citations`
  degrades to a whole-token text match on the record's own value, and that module records its own
  known residuals: a bare digit run in prose can satisfy a small numeric citation, and a currency
  symbol is not part of a canonical value. Those residuals are misses, not false blocks, so this
  lane's failures are not all in the loud direction.
- **A live predicate over an input nothing feeds.** `act:send_cap_exceeded` is a predicate over
  `sent_count`, and **no shipped path hands it a count derived from the log.**
  `Gateway.sent_count()` is the function that derives one, and it is called from
  `tests/audit/test_send_cap.py` and from nowhere else. Enumerated by grep rather than by memory,
  because the first version of this bullet said "every caller hands it a literal zero" and that was
  refutable in two ways. Three in-tree callers do pass a literal zero
  (`src/chaperone/evals/corpus.py`, `demo/day2.py`, `tools/perturbation_log.py`); the fourth,
  `tools/policy_hook.py`, takes the count out of the tool payload and **falls back to zero when the
  payload omits it**, which is the permissive direction. So the hook layer does run the cap
  predicate, against whatever count the caller asserted about itself. That module declares the class
  in `UNENFORCEABLE_HERE` for exactly this reason: it holds no log, so it cannot decide the cap and
  says so rather than letting silence imply parity. **If you are on call for a send cap: the
  predicate is live and its input is not.**

So: a false block points at the record or the canonicalizer. A block that never came can also mean
the context was assembled out of a constant.

---

## The checker was underconfident here, which is not the direction you would guess

Pre-registered prediction 5 went looking for a content-class cell whose stated confidence overshot
observed agreement. **There is none.** `worst_cell` reports absent rather than handing back the least
badly calibrated cell (`src/chaperone/evals/calibration.py`, and
`test_a_checker_that_overshoots_nowhere_has_no_worst_cell_rather_than_a_least_bad_one` is what stops
it degrading into a `max`). There is no worst cell to read first, and reading one anyway means
reading the best-calibrated row in the table and writing it up as the measurement behind the ceiling.

What the table does hold:

- **Zero boolean errors over the 90 content rows, both splits combined.** Zero measured bounds
  nothing about deployment. By the rule of three the error rate is bounded above at about 3.33%, at 95%
  confidence, and below at nothing at all, and a bound is not a rate.
- **All the miscalibration worth acting on sits in `act:figure_not_in_record`**, where the checker's
  answer is not the question it was asked: that cell is Brier 0.8591 at n=5 on the eval split,
  against content cells between 0.0082 and 0.0284. **Not "every unit of it"** -- the content cells
  are underconfident, which is measured miscalibration too, and a sentence claiming otherwise is
  contradicted by the heading above it and by the next bullet down. It is small, it is in the
  cautious direction, and it is not what you are paged about. The act cell is put in the table and
  kept out of the ranking by `is_content_cell`, because the checker is given three content
  constraints and nothing else, so on an act row its "no content violation" is right for the
  question asked and counted wrong against the label.
- **A blended figure is a description of no cell.** Over the eval split the row-weighted Brier is
  0.0689, sitting between an act cell near 0.86 and content cells near 0.02. If somebody hands you
  one number for the checker, ask which cell it came from.
- **Agreement on the boolean is not agreement on the class.** One eval row labelled
  `content:forward_looking_return` is caught and named `content:advises_on_merits`. It blocks either
  way, so the cell reads perfect; but `disposition_for` reads the class, so that row is redirected as
  futile rather than as refinable, and a human sees the wrong reason.
- **Every violating draft in the corpus was written to violate.** Nobody wrote one trying to be
  helpful and crossing the line under pressure, which is the failure this system exists for. The
  clean table is measured on the easier population.

The tripwires are the second disjunct and do not consult the checker's confidence, which is the case
`test_the_tripwire_half_of_arm_four_blocks_a_row_the_checker_calls_clean` drives. When the checker
gives no usable answer at all the gate denies rather than allowing
(`test_a_refusal_is_not_indistinguishable_from_a_completion`,
`test_a_recorded_unavailability_reaches_the_gate_as_a_closed_door`), and the finding it denies with
carries the class `other`. That is an unanswered question, not a content finding.

---

## The quality judge is not a permission signal, in either direction

A high quality score on a blocked draft is the system working. **35 of the 100 violating drafts
scored above the 0.7 quality threshold**, so this is a thing that happens rather than an anomaly to
investigate.

The measured AUC of the judge's mean score for violating against compliant drafts is 0.224, with a
bootstrap interval that excludes 0.5 from below. Violating drafts scored **lower** on quality, which
is pre-registered prediction 4 reported as failed rather than reframed. **Do not invert it into a
detector.** A low quality score is not evidence of a violation and a high one is not evidence of
compliance, and the maxim survives whichever way the correlation had run: a probabilistic instrument
is being asked to authorize an action, and that is the objection.

---

## The audit chain misreports under tampering, under a tear it was not told about, and when it is gone

`verify` returns `ok=False` with `broken_at=N` when the entry at index N no longer hashes to what the
chain says. Read that as: an entry at or before N was edited, or one was removed. The relabelled
entry is still well-formed JSON and still reads back as an `AuditEntry`, so nothing but the chain can
see it (`test_the_gateways_own_log_is_tamper_evident_rather_than_append_only_by_naming`).

**`verify` does not detect a torn tail.** `torn_tail` is a flag the caller passes in and gets echoed
back, and it defaults to `False`, so a one-argument call reports an intact tail however the log was
read. `AuditStore.read_all` is what detects the tear; thread its flag through, or the report is a
default rather than a finding (`src/chaperone/audit/chain.py`, `src/chaperone/audit/store.py`,
`test_the_torn_tail_travels_with_the_entries_it_was_read_with`).

A tear is ordinary after a crash and is not tampering. `counted_sends` charges one extra send against
the cap for it. That is a floor and not a repair: two corrupted lines still add one, and the
correction is never reclaimed, so the extra charge stays for the log's whole life.

**A log that is not there counts zero.** `read_all` over a missing path returns no entries and no
tear, so a deleted log is indistinguishable from one that was never written: the cap resets to zero
and nothing reports a problem. If the log is missing, nothing in this section is being enforced.

---

## An unknown outcome is not a stuck job, and today nothing acts on it

An `unknown` outcome means we cannot prove whether a send happened. The digest stays counted against
the cap, and the recovery pass is deliberately biased that way: only a probe that verifiably says the
side effect is absent releases an intent, and a probe that raises, returns nothing, or returns
anything other than exactly that lands in the same conservative branch
(`src/chaperone/audit/recovery.py`).

**The guard is unarmed outside the test suite, and this is the first thing to know before you rely on
it.** Nothing schedules `resume`. Nothing consults `requires_approval_for` before a send. No caller
feeds an `ActContext` a send count derived from the log, per the third bullet at the top of this
page. The mechanism is built, tested and not wired in, so a note telling you that a re-attempt will
stop at a human would be describing something that will not happen. Wire it in, or treat a
re-attempt as unguarded.

When it is wired in, the second thing to know is that the demand does not go away. A durable
`unknown` makes `requires_approval_for` answer true for that digest **permanently**: the log is
append-only, the gate fires on any entry carrying that digest with an `unknown` outcome, and no later
append can retract it. There is no clearing mechanism to appeal to. Resolve it by confirming the side
effect and approving the re-attempt out of band. Do not wait for the row to clear, and do not edit the
log, which is the one action this whole subsystem is built to make visible.
