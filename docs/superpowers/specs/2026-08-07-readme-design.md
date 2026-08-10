# README and docs design

**Superseded, 2026-08-10.** This spec was executed on its date and the README it designed exists;
the pages have since been maintained against the tree directly, and the tree has outgrown the spec.
Its snapshot claims are now wrong in both directions -- section 8 mandates deleting
`src/chaperone/matching/`, which was subsequently built and tested, and section 1.3's absent-list
names five subsystems the tree now ships. Do not re-execute it. What remains live is section 7's
acceptance idea, which grew into `tests/test_readme_claims.py`.

**Design document. Original status: designed, not written.** This spec governs `README.md`, four `docs/` pages,
and a new guard test. It **supersedes the README instructions in
`docs/superpowers/plans/2026-08-05-chaperone.md` Task 26 Steps 1 and 2**, which mandate content the
tree cannot back: the matching ablation, the "smallest production v1" framing, and a demo calling
`refine()`. Drafting from Task 26 would produce a README claiming three subsystems that do not exist.

**The reader, in order.** A senior engineer pointed at the repository from a direct conversation, with
limited time, arriving to check four specific claims: the enforcement spine is built and green; the
quality gate and the permission gate never share a mechanism; an eval score is not an authorization;
the policy layer cannot import an LLM client. Then a deeper reader who runs the suite and probes the
limits. Then strangers with none of the context. Every structural choice below optimizes for the first
reader without lying to the third.

**Written to be true of the tree at push time.** The repository pushes after `evals/calibration.py` and
`evals/discrimination.py` land, and not before. No sentence below claims a subsystem the tree does not
contain. Where a subsystem is designed and absent, it appears only in the Designed-vs-Built table,
never in prose as though it exists.

---

## 1. Fixed constraints

1. **No organisation is named**, anywhere in the repository. The survive-wrongness test applies: strip
   the context and a coherent general pattern remains; restore one paragraph and it is obviously
   aimed. Failure modes are written as failure modes of *the class of system*, an agent carrying
   high-stakes outbound conversations, never of any firm.
2. **Em-dash-free** in `README.md` and every `docs/*.md` reader-facing page. The lint is scoped to
   those files only. `PREREGISTRATION.md` and the superpowers specs predate the rule and are not
   rewritten.
3. **Every claim true at push time**, mechanically guarded (§7). What remains designed, each item
   verified absent from the tree rather than inherited from an earlier note: matching, the refinement
   loop, **capability-ladder promotion and demotion mechanics**, **attribution arm 1**, the
   `recovery.resume` pass carrying crash-recovery branches (b) and (c), the thread-scope pass, and the
   Pydantic AI checker binding. The README says so in a table rather than implying otherwise by
   silence.

   **Two different ladders, and only one of them is unbuilt.** The **attribution ladder is built**:
   `Ladder` and `run_arm` in `src/chaperone/evals/harness.py` run arms 2, 3 and 4 over a frozen
   corpus, with arm 1 held in `ABSENT_ARMS` and reported missing rather than filled in. What is not
   built is the **capability ladder's** promotion and demotion mechanics, which exist as policy in
   design spec §7. Writing "the ladder is designed, not built" would mislabel a shipped subsystem on
   the one table whose entire purpose is exactness. Likewise the `recovery` field exists on the audit
   entry and records branch (b) `aborted` and branch (c) `unknown`; the `recovery.resume` pass that
   would write them does not exist (`src/chaperone/audit/recovery.py` is absent).
4. **The empty `src/chaperone/matching/` package is deleted before push** (§8).
5. **Numbers travel with denominators.** `3/50 eval`, never `6%`. This matches the repository's own
   convention: a rate never travels without its denominator, per the `ArmResult` docstring in
   `src/chaperone/evals/harness.py`.
6. **Prediction outcomes are reported held-or-failed** per `PREREGISTRATION.md`, whatever they turn out
   to be. No prose is drafted that assumes a prediction held.
7. **The register is practitioner, plain, failure-first.** Lead with what breaks, not with an
   architecture diagram.

---

## 2. The shape

A lean `README.md` of roughly 400 lines, plus four `docs/` pages carrying the depth. The README is the
ninety-second read; the pages are the hour. This is chosen over a single long file because the reader
who is confirmed will not scroll a monolith, and the thesis must not drown in a table of contents.

```
README.md                    the 90-second read
docs/failure-modes.md        the full catalog, five-value claim field
docs/architecture.md         first principles, the Never table, agent grants
docs/measurement.md          pre-registration through results, what numbers do not mean
docs/audit-walkthrough.md    one denied send, annotated, framed as log-as-enforcement-input
```

That is four pages plus the README. A fifth page, `disciplines.md`, is **not** written. It would
re-index the catalog's own triple (mechanism, module, test) by a different key, go stale in lockstep
with the catalog, and read as coursework-signalling to this particular reviewer. Anything unique it
would have held folds into `architecture.md`.

---

## 3. The claim vocabulary

Every failure-mode entry and every capability statement carries exactly one of **five** labels. Three
could not honestly label the rows this artifact most needs to publish, and the shorter vocabulary would
have forced an overclaim on exactly the sharpest entries.

| Label | May be used when |
|---|---|
| **zero by construction** | **act-class rows only**; a pure function decides and the failure is impossible, not improbable |
| **structural invariant, tested** | the property is enforced by construction (prompt omission, tool grants, package purity) and a named test attacks it |
| **measured** | a number exists, with its denominator, and the spec names where it lives |
| **detection only** | caught after the fact, not prevented |
| **designed, not built** | the mechanism is specified and absent from the tree |

**The ratio of measured to zero-by-construction rows is the thesis**, so the field does real per-row
work rather than diluting a taxonomy. Two rules on it:

- The phrase "zero by construction" appears in the repository **only** on act-class rows.
  `CLAUDE.md` already forbids it elsewhere in code, comments, docstrings and documentation; the guard
  in §7 extends that scan to `docs/*.md`.
- Every **measured** row names where its number lives, either a `docs/measurement.md` cell or a
  `PREREGISTRATION.md` prediction, and travels with its denominator.

### 3.1 Entry shape

Every catalog entry has four fixed fields:

> **the failure** → **the mechanism that holds it** → **the named test that attacks it** →
> **what may be claimed**

No cost, blast-radius or escalation field is added. Blast radius lives once in the agent-grants table
in `architecture.md`; escalation is shown once as a verbatim `denial_result` payload; price folds into
the claim cell where it exists.

---

## 4. README.md, section by section

Roughly 400 lines, in this order. The order is an argument: problem, proof it is real, proof it is
handled, the honest residual.

**4.1 Title and the general problem, three sentences.** The most helpful next sentence and the
permitted next sentence diverge, and they diverge where the stakes are highest. Synthetic-scenario
declaration in the opening paragraph.

**4.2 Proof header, directly under the title.** This is the first ninety seconds and it answers the
"built and green" claim before anything else:
- source and test line counts, read from the tree at push time, not fixed here
- `pytest -q`, offline and keyless
- `python demo/day2.py`, the run command inline rather than buried in a trailing quickstart

**4.3 Top three failure modes, as three four-field rows.** The arc is problem → mechanism → residual:

1. **The fluent breach.** The most helpful answer to a direct question scores high on quality and
   breaches a stated constraint. This is the money demo, and it evidences two of the four claims at
   once. Held by the fail-closed gate, where the checker and the tripwire are two disjuncts and
   either one blocks: `evaluate_tripwires` reaches `content:advises_on_merits` from the body alone,
   so the block survives a checker that says nothing. Test:
   `test_each_content_class_blocks_through_the_tripwire_alone`. Claim: **measured**, and the number
   is the content-class escape rate in `docs/measurement.md`, carried with its denominator of 45
   rows per split under `scope="content-classes-only"`.
2. **The rubber stamp.** A checker that has read the generator's own justification agrees with it.
   Independence is enforced by what is left out of the prompt. Tests:
   `test_the_checker_prompt_has_no_generator_artefacts`,
   `test_the_checker_prompt_structure_is_exact`,
   `test_a_checker_model_weaker_than_the_drafter_is_refused`. Claim:
   **structural invariant, tested**.
3. **The confident-wrong checker.** A false negative arrives at high confidence. Held by the tripwire
   disjunct and priced by the calibration that justifies the tier ceiling. Test:
   `test_the_tripwire_half_of_arm_four_blocks_a_row_the_checker_calls_clean`. Claim: **measured**.

Slot three is the confident-wrong checker and not the fail-open send cap, because the push waits for
the measurement layer and the reader arrives expecting measurement evidence, of which calibration is
the flagship. **Self-policing collapse does not lead**: `ABSENT_ARMS` in the tree explicitly declines
to measure it, so a README face built on it would contradict the repository. It gets a catalog row
whose fourth field is **designed, not built**, pointing at
`test_the_missing_first_rung_is_named_as_absent_rather_than_built_from_another_arm`, an artifact that
refuses to fake its own baseline.

**4.4 The money demo, with its output pasted verbatim.** Paste `demo/day2.py`'s printed output
exactly, annotated line by line. One sentence on the `assert not entered` line before the print: the
tool function is never referenced on deny. This section carries the "never share a mechanism" and "a
score is not an authorization" claims.

**Be exact about what guards that output, because the repository already is.** No test executes the
demo; `demo/day2.py`'s own docstring says so. What guards it is a pair: `assert not entered` runs
inside the file before the print, and `.github/workflows/ci.yml` runs the file as a step named
`Day-2 demo`, so a regression that let the send through fails CI rather than printing a different
number and exiting 0. That covers the **headline invariant** and not the **printed text**, which
nothing currently byte-compares. So the README says the invariant is asserted and CI runs the demo,
never the looser "CI-asserted output", and §7 closes the remaining half by byte-matching the paste.

**4.5 The two-family table**, with the static-audit line beneath it: `tools/static_audit.py`, the
edit-time hook, and `test_the_real_policy_package_is_pure`. This carries the "policy cannot import an
LLM client" claim.

**4.6 One verbatim `denial_result` JSON payload.** The six keys `denial_result` actually returns
(`denial_result` in `src/chaperone/gates/engine.py`), not an invented shape: `is_error`, `is_retryable`, `category`,
`detail`, `span`, `disposition`. `is_retryable` is `False` unconditionally, and the sentence that
earns the paste is that retryability and redraftability are two axes: whether a redraft could help is
`disposition`, never `is_retryable`.

**4.7 Headline numbers**, each with its denominator and split, prediction outcomes reported
held-or-failed.

**What this section can contain the day it is drafted.** No `RESULTS.md` exists and no arm has been
run, so there are no measured rates yet. Inventing them is out of the question and leaving the section
out would hide the discipline that makes the artifact interesting. So §4.7 ships in two halves:

- **Now: the corpus, and the predictions recorded before it was measured.** The corpus counts are
  knowable without running anything and are pinned cell by cell by `tests/test_preregistration.py`, so
  they cannot go stale in silence: 160 drafts, 80 per split, 50 labelled violating and 30 labelled
  compliant per split, with the content classes at 15 rows each per split (45 per split) and
  `act:figure_not_in_record` at 5 per split. Four of the five act-classes carry no labelled row, and
  the README says which and why rather than letting a reader infer a wider claim. Beside that, the
  five pre-registered predictions, stated as predictions, with `git log` named as the only witness
  that they preceded the numbers.
- **At push: the outcomes**, filled in when the arms run, each reported held-or-failed.

This ordering is itself the argument. A reader who has seen predictions published before results, with
a stated interpretation for each branch, knows what the later numbers are worth. A reader handed only
results does not.

**4.8 Limits.** Cross-turn accumulation as the named residual star: every turn passes the per-draft
gate while the conversation as a whole breaches, and the checker sees the thread but no test yet shows
a cross-turn breach caught. Plus a whole-chain rewrite by an actor with write access defeats the audit;
corpus and tripwire share an author; labels are synthetic.

**4.9 Designed-vs-Built table.** From design spec §7.2 (`Built, and not built`), **re-verified against
the tree rather than copied**, because §7.2 predates 20-odd commits and at least one of its rows has
since been built. Designed-not-built rows: matching (package deleted, §8), the refinement loop,
capability-ladder promotion and demotion mechanics, attribution arm 1, the `recovery.resume` pass
carrying branches (b) and (c), the thread-scope pass, and **the Pydantic AI checker binding**. The
attribution ladder itself is **built** and belongs on the built side, per §1.3. On the last row:
`pydantic-ai` and `pydantic-evals` are declared in `pyproject.toml`, but `src/` imports only
`pydantic` itself. The checker is specified as a Pydantic AI agent and is currently built without it.
A dependency in the manifest is not a dependency in use, and the table says so.

**4.10 Quickstart.** Clone, install, the three commands.

---

## 5. docs/ pages

**5.1 `failure-modes.md`.** The full catalog, every entry in the §3.1 shape with the five-value claim
field. The comprehensive piece.

**5.2 `architecture.md`.** First principles: why a `PreToolUse` hook and not a `can_use_tool` callback,
covering the six-step permission pipeline and the shadowing footgun; why the checker model is at least
as strong as the drafter; why quality and permission are two lanes and never one. Carries the **Never
table** of per-module "Never" clauses and the **agent-grants and blast-radius table** from design spec
§6.1.

**That table is design, and the page must say so.** There is no agent registry anywhere in `src/`, so
the four-row topology (research, matching, drafting, conversation) and the sentence "the drafting
agent holds no send tool" describe an intended arrangement rather than a shipped one. What **is**
built is the enforcement primitive beneath it: `granted_tools` on `ActContext`, checked by a pure
function at `src/chaperone/policy/act_classes.py`, which returns `act:tool_outside_grant` for any tool
outside the grant, plus the two enforcement points `pre_tool_use` and `guarded_call` in
`src/chaperone/gates/hook.py`. The page states the built primitive first and marks the topology
**designed, not built**. Presenting a four-agent topology as though it ships would be the single
easiest overclaim in this repository to make and the easiest for a reader to falsify, since the
absence is one `ls src/chaperone/` away.

**Do not cite the design spec's `[planned:` test names.** `test_every_tool_description_passes_the_linter`,
`test_no_registered_tool_can_mutate_policy` and `test_the_same_policy_denies_at_both_layers` are marked
planned in design spec §6.1 to §6.3 and were verified absent from the suite. The §7 guard would fail on
any of them, which is the guard working. The claims they would have backed are stated as designed or
dropped.

Design spec §7.2 says demotion is built and promotion is not. Neither is: no promotion or demotion
mechanics exist under `src/`. Both are designed-not-built rows. The capability ladder appears here
**only** as policy.

**5.3 `measurement.md`.** Pre-registration through results. Frames each number by what it does *not*
mean beside what it does. Links to the generated `RESULTS.md`. The three-layer enforcement sentence
lives here in full and must carry "the pure half": the out-of-process layer enforces only the
deterministic classes, and the suite says so by name in
`test_the_out_of_process_layer_enforces_a_strict_subset_and_names_the_gap`.

**5.4 `audit-walkthrough.md`.** One denied send, annotated entry by entry, aimed away from "here is a
hash chain", which this reader has built himself, and toward **the log as an enforcement input**: the
send cap counts intent entries, so a lost line is a predicate failing open, not merely a forensics gap.
Punchline tests: `test_an_effectful_send_writes_an_intent_entry_before_the_outcome` and
`test_intent_precedes_outcome_in_the_recorded_order`, plus the fsync and torn-append tests, with the
arg-digest-never-raw-args line landing beside PII redaction. Crash-recovery branches (b) and (c) are
named designed-not-built here, because the three-branch recovery of design spec §5.4 is not in the
suite.

---

## 6. Deliberately not built into the reader layer

- **The tech-stack justification table.** This stack is thin by design; the only why-not-X that matters
  (hook versus callback, checker versus self-policing) already lives in `architecture.md`.
- **The embedded CV section.** The repository must survive context-stripping as engineering. A CV
  section inside it collapses that property, and the aiming was already done in the outreach message.

---

## 7. The guard that binds the docs to the suite

A **new** `tests/test_readme_claims.py`. The file does not exist yet; Task 26 Step 2 specified one, and
this spec replaces that specification. The retain and drop clauses below refer to Task 26's specified
assertions, not to existing code.

This is the design's central promise made executable: the entry shape promises "the named test that
attacks it", so a named test that does not exist must fail CI.

- **Extract every backticked `test_*` name and every `src/chaperone/...` path from `README.md` and
  every `docs/*.md`, and assert each resolves** against the collected suite and the tree. This converts
  the whole design's honesty from vigilance into red CI.
- **Byte-match the demo output pasted in README §4.4 against a fresh `python demo/day2.py` run.**
  Name resolution cannot catch a stale paste: the demo could change its wording, its verdict or its
  counts and every backticked name in the README would still resolve. The demo is offline and keyless,
  so running it inside a test is cheap, and this is the only mechanism that keeps a verbatim paste
  verbatim. It also closes the half-gap the demo's own docstring records, where CI guards the headline
  invariant but nothing guards the printed text.
- **Extend the zero-by-construction scan to `docs/*.md`**, not just the README. `CLAUDE.md` says "in
  code, comments, docstrings, or documentation"; guarding one file of six is a hole.
- **Retain** Task 26's organisation-token, synthetic-scenario, tier-ceiling and cross-turn-residual
  guards.
- **Drop** Task 26's matching-ablation and smallest-production-v1 assertions.
- **Scope the em-dash lint** to `README.md` and `docs/*.md` only.

Every test name cited in §4 and §5 was verified present in the suite at the time this spec was
written, along with `denial_result`'s key list, the `ArmResult` denominator convention, `ABSENT_ARMS`,
the `Day-2 demo` CI step, and the absence of `refine`, promotion and demotion, `recovery.py`, the
thread-scope pass and any `pydantic_ai` import under `src/`. The suite was green at that point:
443 passed. None of this is asserted here on the strength of an earlier note; the point of the guard
is that the next reader does not have to take that on trust either.

---

## 8. One tree change this design requires

`src/chaperone/matching/__init__.py` is an empty package. An empty package with no README mention is an
overclaim by directory structure; a README mention of an empty package is an overclaim in prose. **The
package is deleted before push**, and matching appears only as a Designed-vs-Built row. If matching is
ever built it returns as a populated package with its own tests.

---

## 9. Assets, out of scope here

An architecture animation and a terminal recording of `demo/day2.py` are a separate track. Two
constraints are recorded so the reader layer and the assets stay consistent: **the animation must not
show concrete numbers**, for example a checker confidence or an arm pass-rate, until `calibration.py`
and `discrimination.py` land and the numbers are real; and **the animation illustrates while the
recording proves**, since the recording is CI-asserted output and the animation is not evidence.

---

## 10. Open decision for the author, not resolved here

`docs/superpowers/specs/` and `docs/superpowers/plans/` are committed. If the repository is pushed
public as-is, this file is readable by the reader described in the header. That is survivable, since it
describes honest engineering discipline rather than anything concealed, but it is a deliberate choice
rather than an oversight, and the alternative is to keep the superpowers directory out of the public
push. **Decide before pushing.** The reader layer itself is written to be indifferent to that choice.

---

## 11. Honesty note

This is a design for a document. Nothing in the reader layer is written yet. When the README and docs
are drafted, every backticked test name is verified to exist, every number travels with its
denominator, and every subsystem absent from the tree appears only under Designed-vs-Built. The
distinction between designed and built stays exact, on the pages whose whole purpose is to state it.
