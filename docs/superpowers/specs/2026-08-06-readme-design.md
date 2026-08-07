# chaperone README and docs — design

**Design document. Status: designed, not built.** This specifies the public reader-facing layer of the
repository: one lean `README.md` and five `docs/` pages. It supersedes **Task 26 Steps 1-2** of
`docs/superpowers/plans/2026-08-05-chaperone.md` and the matching-ablation / smallest-production-v1
requirements of that task's `test_readme_claims.py`. The organisation-token, synthetic-scenario,
zero-by-construction, tier-ceiling and cross-turn guards from that test are retained and extended.

**Written to be true of the tree at push time.** The repository pushes *after* `calibration.py` and
`discrimination.py` land, and *not* before. Everything below is drafted so that no sentence claims a
subsystem the tree does not contain. Where a subsystem is designed and unbuilt, it appears only in the
Designed-vs-Built table, never in prose as though it exists.

---

## 1. Who reads this, and what they must have absorbed by the time they stop

Three readers, in order, and the README is optimised for the first without failing the others.

1. **A GTM engineer who volunteered twenty minutes.** A real engineer with a Python pipeline of his
   own, including hash-chained audit logging and PII redaction. He arrives from a message that made
   four specific claims: the enforcement spine is built and green; the quality gate and the permission
   gate never share a mechanism; an eval score is not an authorization; the policy layer cannot import
   an LLM client. **His first ninety seconds must land the evidence for those four claims**, because
   they are the promises he is checking.
2. **A VP-level engineer, later, deeper.** Reads the `docs/` pages, runs the suite, probes the limits.
3. **A stranger.** Must find a coherent general pattern even with every trace of the target context
   stripped.

**The register, non-negotiable:** practitioner, plain, failure-first. Lead with what breaks, not with
an architecture diagram. Em-dash-free. No project name from any reference material. No company named
anywhere in the repository.

---

## 2. The shape

A lean `README.md` of roughly 400 lines, plus five `docs/` pages carrying the depth. The README is the
ninety-second read; the pages are the hour. This is chosen over a single long file because the one
reader who is confirmed will not scroll a monolith, and the thesis must not drown in a table of
contents.

```
README.md                    the 90-second read
docs/failure-modes.md        the full catalog, five-value claim field
docs/architecture.md         first principles, the Never table, agent grants
docs/measurement.md          pre-registration through results, what numbers do not mean
docs/audit-walkthrough.md    one denied send, annotated, framed as log-as-enforcement-input
```

`disciplines.md` is **not** written. It would re-index the catalog's own triple (mechanism, module,
test) by a different key, go stale in lockstep with the catalog, and read as coursework-signalling to
exactly this reviewer. Anything unique it would have held folds into `architecture.md`.

---

## 3. The failure-mode catalog: entry shape

Every catalog entry has four fixed fields:

> **the failure** → **the mechanism that holds it** → **the named test that attacks it** →
> **what may be claimed**

The fourth field takes one of **five** values, not three. Three could not honestly label the rows this
artifact most needs to publish, and the format would have forced an overclaim on exactly the sharpest
entries.

| Claim value | Meaning | Where it is allowed |
|---|---|---|
| **zero by construction** | a pure function decides; the failure is impossible, not improbable | **act-class rows only** |
| **structural invariant, tested** | a property of how the code is assembled, proven by a test, but not an act-class | independence, single-gateway, purity |
| **measured** | a rate, reported with its denominator and where the number lives | content-class detection, ablation arms |
| **detection only** | caught after the fact, not prevented | tamper on the chain |
| **designed, not built** | the mechanism is specified and absent from the tree | matching, refinement, promotion, arm 1 |

**The ratio of "measured" to "zero by construction" rows is the thesis**, so the field is doing real
per-row work rather than diluting a taxonomy. Two rules on it:

- The phrase "zero by construction" appears in the repository **only** on act-class rows, in code and
  in every doc page. `CLAUDE.md` already forbids it elsewhere; the guard in §7 extends that scan to
  `docs/*.md`.
- Every **measured** row names where its number lives (`docs/measurement.md` cell or a
  `PREREGISTRATION.md` prediction) and travels with its denominator: **`3/50 eval`, never `6%`**. This
  is the repository's own convention — a rate never travels without its denominator, per the
  `ArmResult` docstring in `src/chaperone/evals/harness.py`.

No cost, blast-radius or escalation field is added. Blast radius lives once in the agent-grants table
(`architecture.md`); escalation is shown once as a verbatim `denial_result` payload; price folds into
the claim cell where it exists.

---

## 4. README.md — section by section

Roughly 400 lines, in this order. The order is an argument: problem, proof it is real, proof it is
handled, the honest residual.

**4.1 Title and the general problem (3 sentences).** The most helpful next sentence and the permitted
next sentence diverge, and they diverge where the stakes are highest. Synthetic-scenario declaration
in the opening paragraph, matching the design spec's own lead.

**4.2 Proof header, directly under the title.** This is the reader's first ninety seconds and it
answers his "built and green" claim before anything else:
- source and test line counts (from the tree at push time, not asserted as fixed numbers here)
- `pytest -q` — offline, keyless
- `python demo/day2.py` — the run command inline, not buried in a trailing quickstart

**4.3 Top three failure modes, as three four-field rows.** The arc is problem → mechanism → residual:
1. **The fluent breach.** The most helpful answer to "is this a good deal?" scores high on quality and
   breaches a stated constraint. This is the money demo, and it evidences two of the four message
   claims at once. Claim value: **measured**.
2. **The rubber stamp.** A checker that has read the generator's own justification agrees with it.
   Independence is enforced by what is left out of the prompt.
   Tests: `test_the_checker_prompt_has_no_generator_artefacts`,
   `test_the_checker_prompt_structure_is_exact`,
   `test_a_checker_model_weaker_than_the_drafter_is_refused`. Claim value:
   **structural invariant, tested**.
3. **The confident-wrong checker.** A false negative arrives at high confidence. Held by the tripwire
   disjunct and priced by the calibration that justifies the tier ceiling.
   Test: `test_the_tripwire_half_of_arm_four_blocks_a_row_the_checker_calls_clean`. Claim value:
   **measured**.

Slot three is the confident-wrong checker, not the fail-open cap, because the message said the push
waits for the measurement layer, so the reviewer arrives expecting measurement evidence and
calibration is its flagship. **Self-policing collapse does not lead**: `ABSENT_ARMS` in the tree
explicitly declines to measure it, so a README face built on it would contradict the repository. It
gets a catalog row whose fourth field is **designed, not built**, pointing at
`test_the_missing_first_rung_is_named_as_absent_rather_than_built_from_another_arm` — an artifact that
refuses to fake its own baseline.

**4.4 The money demo, with verbatim CI-asserted output.** Paste `demo/day2.py`'s printed output
exactly (it is CI-asserted), annotated line by line in the reference README's annotation style applied
to real output. One sentence on the `assert not entered` line before the print: the tool function is
never referenced on deny. This section carries the "never share a mechanism" and "a score is not an
authorization" claims.

**4.5 The two-family table**, with the static-audit line beneath it: `tools/static_audit.py`, the
edit-time hook, and `test_the_real_policy_package_is_pure`. This carries the "policy cannot import an
LLM client" claim.

**4.6 One verbatim `denial_result` JSON payload.** Category, `is_retryable: false`, disposition, span.
The honest transplant of the reference README's owner-message column.

**4.7 Headline numbers**, each with its denominator and split, prediction outcomes reported
held-or-failed per `PREREGISTRATION.md` whatever they turn out to be. Drafted so no prose assumes a
prediction held.

**4.8 Limits.** Cross-turn accumulation as the named residual star — every turn passes the per-draft
gate while the conversation as a whole breaches; the checker sees the thread but no test yet shows a
cross-turn breach caught. Plus: a whole-chain rewrite by an actor with write access defeats the audit;
corpus and tripwire share an author; labels are synthetic.

**4.9 Designed-vs-Built table.** Verbatim from design spec §7.2, corrected to the tree: matching
(package deleted, see §8), refinement loop, promotion/demotion mechanics, attribution arm 1,
crash-recovery branches (b) and (c), the thread-scope pass, and **the Pydantic AI checker binding**
(declared dependency, checker currently built without it; the Pydantic-AI-agent form is designed).

**4.10 Quickstart.** Clone, install, the three commands. The run command already appeared inline in
4.2; this carries the rest.

---

## 5. docs/ pages

**5.1 `failure-modes.md`.** The full catalog, every entry in the four-field shape with the five-value
claim field. The comprehensive piece.

**5.2 `architecture.md`.** First principles: why a `PreToolUse` hook and not a `can_use_tool` callback
(the six-step permission pipeline and the shadowing footgun); why the checker model is at least as
strong as the drafter; why quality and permission are two lanes and never one. Carries the **Never
table** (per-module "Never" clauses) and the **agent-grants / blast-radius table** from design spec
§6.1 — four rows, and "the drafting agent holds no send tool" is the cheapest strong sentence in the
repository. The capability ladder appears here **only** as policy, with its mechanics marked
designed-not-built.

**5.3 `measurement.md`.** Pre-registration through results. Frames each number by what it does *not*
mean beside what it does. Links to the generated `RESULTS.md`. The three-layer enforcement sentence
lives here in full and must carry "the pure half": the out-of-process layer enforces only the
deterministic classes, and the suite says so by name
(`test_the_out_of_process_layer_enforces_a_strict_subset_and_names_the_gap`).

**5.4 `audit-walkthrough.md`.** One denied send, annotated entry by entry, re-aimed away from "here is
a hash chain" — the reviewer builds those himself — toward **the log as an enforcement input**: the
send cap counts intent entries, so a lost line is a predicate failing open, not a forensics gap.
Punchline tests: `test_an_effectful_send_writes_an_intent_entry_before_the_outcome`,
`test_intent_precedes_outcome_in_the_recorded_order`, and the fsync / torn-append tests, plus the
arg-digest-never-raw-args line landing beside PII redaction. Crash-recovery branches (b) and (c) are
named designed-not-built here, because the three-branch recovery of design spec §5.4 is not in the
suite.

---

## 6. What is deliberately not built into the reader layer

Dropped from the reference README's feature set without regret:

- **The tech-stack justification table.** This stack is thin by design; the only why-not-X that
  matters (hook versus callback, checker versus self-policing) already lives in `architecture.md`.
- **The embedded CV section.** The repository must survive context-stripping as engineering. A CV
  section inside it collapses that property, and the aiming was already done in the outreach message.

---

## 7. The guard that binds the docs to the suite

A rewritten `tests/test_readme_claims.py`, and this is the differentiator made executable: the README
design's promise is "the named test that attacks it," so a named test that does not exist must fail
CI.

- **Extract every backticked `test_*` name and every `src/chaperone/...` path from `README.md` and
  every `docs/*.md`, and assert each resolves** against the collected suite and the tree. This converts
  the whole design's honesty from vigilance into red CI.
- **Extend the zero-by-construction scan to `docs/*.md`**, not just the README. `CLAUDE.md` says "in
  code, comments, docstrings, or documentation"; guarding one file of six is a hole.
- **Retain** the organisation-token, synthetic-scenario, tier-ceiling and cross-turn-residual guards.
- **Drop** the matching-ablation and smallest-production-v1 assertions from the superseded Task 26.
- **Scope the em-dash lint to the new reader-facing files only.** `PREREGISTRATION.md` already contains
  em-dashes and is committed; it is not rewritten.

---

## 8. One tree change this design requires

`src/chaperone/matching/__init__.py` is an empty package. An empty package with no README mention is an
overclaim by directory structure; a README mention of an empty package is an overclaim in prose. **The
package is deleted before push**, and matching appears only as a Designed-vs-Built row. If matching is
ever built it returns as a populated package with its own tests.

---

## 9. Assets (built after the reader layer, out of this design's scope)

An architecture animation and a terminal recording of `demo/day2.py` are a separate asset track. Two
constraints on them are recorded here so the reader layer and the assets stay consistent: the animation
must not show concrete numbers (for example a checker confidence or an arm pass-rate) until
`calibration.py` and `discrimination.py` land, and the animation illustrates while the recording
proves — the recording is the CI-asserted output, the animation is not evidence.

---

## 10. Honesty note

This is a design for a document. Nothing in it is written yet. When the README and docs are drafted,
every backticked test name is verified to exist, every number travels with its denominator, and every
subsystem absent from the tree appears only under Designed-vs-Built. The distinction between designed
and built stays exact, on the page whose whole purpose is to state it.
