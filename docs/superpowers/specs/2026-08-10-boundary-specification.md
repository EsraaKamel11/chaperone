# chaperone as a published boundary: specification of record

**Date 2026-08-10. Status: written, pending review round and user review.**

**What this document is.** A single current statement of what this repository is, what it guarantees,
how each guarantee is held, and what it publishes to a program that imports it. It consolidates four
earlier design specs and the amendments taken during their execution (A1 through A4) into their
settled form.

**What it does not replace.** The earlier specs and plans under `docs/superpowers/` stay exactly as
written. They are dated records of what was designed and planned before the work, and rewriting them
to match the outcome would destroy their only value. Where this document and one of them disagree,
this one describes the tree and that one describes a day. `docs/superpowers/README.md` states that
division.

**Why now.** A separate system now imports this repository as a vendored wheel and had to derive its
own import contract by reading this library's source, recording that "the library never published
one." Deriving it surfaced a real placement discrepancy. A boundary that another program depends on
should publish what it offers rather than leave a consumer to infer it.

**Every claim below was measured against the tree on the date above**, not recalled. Where a property
is held by nothing but discipline, this document says so; a specification that presents structural
guarantees and habits in the same voice is worth less than one that separates them.

---

## 1. What this library is

A deterministic authorization boundary for an agent that takes actions on a person's behalf, plus the
evidence apparatus for deciding how much autonomy such an agent has earned. All data in the
repository is synthetic.

The whole design rests on one distinction:

**Act-classes** are properties of the action: whether an approval token is present, whether the
recipient's jurisdiction was consented to, whether the tool is inside the grant, whether a figure
appears in the record, whether a send cap is exceeded. They are decided by pure functions over
arguments the caller supplies. No model is consulted.

**Content-classes** are properties of the text: whether a draft advises on merits, negotiates terms,
or projects a forward-looking return. They are decided by an independent model checker with
deterministic lexical tripwires beside it. They are measured and reported, never guaranteed.

The two never share a mechanism, and the second never gates the first. A measurement is not an
authorization.

---

## 2. The contract to publish

Today `src/chaperone/__init__.py` is empty, as are all six subpackage inits, and the package declares
no `__all__` anywhere. Every consumer import is therefore a submodule path discovered by reading
source, `from chaperone import Draft` cannot work, and a wildcard over `chaperone.matching` binds
nothing. This section states what the package should publish. Section 8 records the delta.

### 2.1 Enforcement

| Name | Module | Kind |
|---|---|---|
| `guarded_call` | `gates.hook` | the executor chokepoint; nine required arguments, `queues` keyword-only |
| `pre_tool_use` | `gates.hook` | full predicate set including the checker, no execution |
| `pre_tool_use_deny` | `gates.sdk_callback` | async; returns a deny dict, imports no SDK |
| `HookOutcome` | `gates.hook` | what `pre_tool_use` returns |

### 2.2 Policy vocabulary

`Draft`, `Message`, `Record`, `Decision`, `Finding`, `ViolationClass`, `Disposition`, `Family` from
`policy.types`; `ActContext` and `evaluate_act_classes` from `policy.act_classes`;
`validate_citations` from `policy.citations`; `evaluate_tripwires` and `TRIPWIRE_CLASSES` from
`policy.tripwires`; `unsendable_in` and `unsendable_finding` from `policy.arguments`.

**`ActContext` is defined in `act_classes`, not `types`.** A consumer guesses `types` because
`act_classes` imports four of the eight names `types` defines, and handles them alongside it. The
publication in section 8 removes the guess.

**`Message` is not optional to know.** `Draft.thread` is `tuple[Message, ...]`, so a consumer without
`Message` cannot construct the first argument to anything.

**The enums are closed, and their members are the contract.** `ViolationClass` has nine members,
`Disposition` three, `Family` three.

### 2.3 The checker seam

All of the following live in `gates.checker`: `Checker`, `MODEL_STRENGTH`, `assert_checker_not_weaker`,
`CheckerResult`, `Verdict`, `FlagForReview`, `CheckerUnavailable`.

`Checker` is a plain concrete class, not an abstract base and not a protocol. A consumer constructs
it and supplies a transport; it does not implement it.

    Checker(model: str, drafter_model: str,
            transport: Callable[[list[dict]], CheckerResult], retries: int = 2)

`model` and `drafter_model` must both be members of `MODEL_STRENGTH`, and a checker weaker than the
drafter is refused at construction. `MODEL_STRENGTH` therefore belongs in the contract: without it a
consumer learns the legal set from an exception.

The transport is the seam, and it has no published type name today, so a contract that mentions "the
transport seam" names a hole and no way to fill it. Publishing `CheckerResult` gives the callable's
return type a name a consumer can annotate against. `Verdict`, `FlagForReview` and
`CheckerUnavailable` complete it: the first two are what a transport returns, the third is what a
consumer catches. `CheckerResult` is a type alias, not a class. On the Python versions this package supports today it
cannot be used with `isinstance` directly; a consumer that needs a runtime check tests against the two
members.

### 2.4 Audit

`AuditStore`, `Gateway`, `GatewayResult`, `transmit` from `audit.gateway` and `audit.store`; `link`,
`verify`, `VerifyResult`, `GENESIS_HASH` from `audit.chain`; **`AuditEntry` and
`INDETERMINATE_OUTCOMES` from `audit.entry`**.

`AuditEntry` is the currency of every audit call a consumer makes: `verify` takes a sequence of them,
`AuditStore.append` returns one, and writing a predicate for `AuditStore.count` requires knowing its
eleven fields. A contract listing the store and the chain but not the entry is the gap a consumer
meets on its first verification.

**`transmit` is published at its module path only, and never from the package root.** It is public,
and `tools/static_audit.py` fails the build if any file **inside the shipped package** other than
`audit/gateway.py` so much as names it. `src/chaperone/__init__.py` is inside that root, so
re-exporting `transmit` from the package would trip the reservation on its own first line. A
consumer reaches it at `chaperone.audit.gateway.transmit` and nowhere else.

That is the reservation working rather than an inconvenience: the symbol is published so a consumer
knows the send exists and where it lives, and is deliberately unreachable from the package surface
so no import can quietly widen the set of modules that name it. The audit is rooted at
`src/chaperone` and walks no further, which is why the suite references it freely.

### 2.5 Routing and escalation

`ReviewQueues` from `gates.queues`; `Handoff` and `build_handoff` from `gates.handoff`; `decide`,
`denial_result`, `disposition_for`, `destination_for` from `gates.engine`.

### 2.6 The payload adapter

`build_act_inputs`, `CONSUMED_KEYS`, `UNENFORCEABLE_HERE`, `CONSENTED` and `GRANTED` from
`policy.payload`.

Section 4 tells a consumer how to read `UNENFORCEABLE_HERE`, so the contract must publish it rather
than leave it to be found. The module anticipates further layers building their inputs through it,
and each inherits obligations the adapter does not discharge: it validates nothing, an absent `body`
raises, and a key outside `CONSUMED_KEYS` is checked as outbound content rather than ignored. A layer
that skips those guards fails open in a way the adapter cannot see.

If a consumer would rather not take that on, `pre_tool_use_deny` is the supported payload-shaped
surface and performs those guards itself.

### 2.7 Matching

`Mandate`, `Candidate`, `Eligibility`, `classify` from `matching.filters`; `relationship_score` from
`matching.relationship`; `rank` from `matching.rank`.

### 2.8 What a consumer must construct

`guarded_call` requires a `Gateway` (which requires an `AuditStore`, which mkdirs its parent at
construction), a tool registry, a `Checker` with a transport, and a `ReviewQueues`. None of these are
optional and none has a default.

**The registry's values are called as `registry[tool_name](**args)`.** A tool must therefore accept
the argument keys as keyword parameters. A callable written to take a single dict raises `TypeError`
at the chokepoint, and the annotation does not say so.

---

## 3. What travels, and what does not

The wheel target names `src/chaperone` only. `tests/`, `tools/`, `corpus/`, `demo/` and `docs/` are
siblings of `src/` and **do not travel**. That matters more than it looks: the purity audit, the
edit-time guard, the mutation sweep and the claims suite are the mechanisms behind most of section 5,
and none of them ships. **A consumer inherits the properties, not the enforcement.** Anything a
consumer needs held in its own tree, it holds itself.

`chaperone/evals/` and `chaperone/testing/` **do** travel, because they sit under `src/chaperone`.

`testing/` is accident rather than contract, and this specification does not publish it. It holds the
recorded and scripted transports this repository's own suite drives, and a consumer will find them in
the wheel and reasonably want to build tests on them. They are outside the contract, they may change
without notice, and a consumer that wants a replaying transport should write its own against the seam
in section 2.3, which is small and is the supported surface.

`evals/` travels for the same structural reason and is a defect rather than a choice. Section 8
records it.

Runtime dependencies are `pydantic>=2.7` and `pydantic-ai>=2.23`. Optional extras are `evals`
(`pydantic-evals`), `sdk` (`claude-agent-sdk`) and `dev` (`pytest`, `hypothesis`). Measured by
blocking each import in a subprocess: nothing in the package imports `claude_agent_sdk` or
`pydantic_evals` at all, and exactly one module imports `pydantic_ai`. All of `policy/` and
`matching/` import with neither pydantic nor pydantic-ai present.

---

## 4. The four decision surfaces

| Surface | Predicate set | Checker | Failure mode when it crashes |
|---|---|---|---|
| Out-of-process command hook (`tools/policy_hook.py`) | unconsumed keys, act-classes, citations, tripwires | no | fail-closed **by construction**: every escape becomes exit 2, catching `BaseException` |
| In-process deny callback (`gates/sdk_callback.py`) | the same four, same order, same adapter | no | fail-closed **by construction**: `except BaseException` returns a deny |
| In-process hook layer (`gates/hook.py::pre_tool_use`) | the full set | yes | no handler; fail-closed only derivatively, because `decide` returns rather than raises |
| Executor chokepoint (`gates/hook.py::guarded_call`) | the full set, bound to the reviewed draft | yes | no handler; fail-closed **by ordering**, because the decision runs before execution inside the gateway's `try` |

The first two enforce a **declared strict subset**. `UNENFORCEABLE_HERE` holds
`act:send_cap_exceeded`, `act:no_approval_token` and `act:figure_not_in_record`. Read it correctly:
the set marks classes that **cannot be relied on to fire** on that payload shape, never classes that
are suppressed. Neither surface filters findings by it. A consumer that read it the other way would
build on an inversion of the truth.

The out-of-process contract fails **open** by specification: only exit 2 blocks, and every other exit
is a non-blocking error the action continues through. The fail-closed behaviour above is this
repository's construction on top of that contract, not a property of it.

---

## 5. The invariant register

**T** held by a test asserting an effect · **S** held by a static tool with its own exit code ·
**H** held by an edit-time hook · **R** held by review discipline only.

| Property | Class | How |
|---|---|---|
| `policy/` imports no model client, no I/O, no clock | **S+H+T** | denylist walk over the import graph, exit code in CI; an edit-time hook with the same verdicts; tests in both directions and a test that auditing nothing is not clean |
| Act-class decisions consult no model | **S+T** | the purity audit makes a client unreachable from `policy/`; act findings return before any checker call |
| The gate fails closed when the checker is unusable or raising | **T** | after the retry budget the transport becomes an unavailability error, which becomes a denial carrying an outage marker; a mutant covers the fail-open inversion |
| Denial is terminal | **T** | never retryable, unconditionally; redraftability is a disposition derived in one place; a denied call never indexes the tool registry |
| The checker prompt carries no generator artefacts | **T** | field selection asserted in both directions, including the omission direction |
| Every send runs through one chokepoint | **S+T** | the send symbol is name-reserved to one module by static audit; one outcome entry per call in a `finally`, with a mutant on the `finally` |
| The audit chain is hash-linked and tamper-evident | **T** | six tamper shapes, an independently computed digest, and a test that the same payload under a different predecessor yields a different digest |
| The same predicate set at every surface | **T** | a corpus of rows compared across three surfaces on verdict, category and detail, including allows |
| The tests are not proxies | **T** | seventeen mutants, each required to be killed by a named failing test, with harness self-guards against reporting a kill it did not see |
| The reviewed object is the sent object | **T** | tool identity and arguments bound to the reviewed draft at the chokepoint |
| A draft's verdict carries no residue from preceding drafts | **T** | all orderings of three disagreeing drafts, with an explicit non-vacuity assertion |
| The tier-2 content ceiling cannot be exceeded | **T** | the ceiling is applied in the state's own constructor |
| No promotion is wired to any outcome | **T** | AST walk with a bite-check |

### 5.1 Held by discipline, not by mechanism

A specification of record earns its name here rather than in the table above. **This is a selection,
not the whole set.** The criterion is what a program importing this boundary would be wrong to
assume; parochial items that bind only this repository's own workflow are left to the source that
records them.

- **The checker prompt interpolates its inputs raw.** `build_checker_messages` selects which fields
  reach the prompt and is held to that selection in both directions, but thread roles, thread bodies
  and the draft body are copied in unescaped. **A consumer that puts untrusted counterparty text into
  `Draft.thread` is holding a prompt-injection boundary by discipline**, and nothing in this library
  holds it for them. Whoever assembles a `Draft` owns that line.

- **There is no timeout anywhere.** Fail-closed on an unusable checker does not cover a hang. Both
  modules say so and say that no test holds either half. A consumer that needs a bound supplies it in
  the transport.
- **Two of the four surfaces have no exception handler.** Their fail-closed behaviour is derivative,
  from ordering and from `decide` returning rather than raising. One containment case is tested.
- **"No network in tests" has no scanner.** It is true by absence today. Nothing would fail if
  someone added one.
- **The failure catalog's cited tests are name-checked, never relevance-checked.** Every backticked
  test name in a reader-facing page is verified to exist. Nothing verifies that the named test
  attacks the failure its row describes. Across the catalog this is the largest gap between what is
  structural and what is disciplined.
- **The name reservation on the send symbol is a name audit, not a call-graph proof**, and the
  reserved symbol has no shipped caller.
- **One measured mutant survivor has no guard**: a justification sentence appended to the checker
  instructions.
- **Two layers can be configured apart.** The consented-jurisdiction and granted-tool sets are module
  constants in the out-of-process adapter; only the predicate set is shared.
- **The declared-unenforceable classes can be talked out of firing** by the caller being judged,
  because their evidence arrives in the payload. Exhibited by tests, not closed.

---

## 6. Measured, never guaranteed

Every published rate comes from frozen recorded verdicts over a synthetic corpus of 160 drafts, 80 per
split, each split 50 labelled violating and 30 labelled compliant. The generator is bound to the
published page by a CI step that regenerates it and fails on any difference.

Three arithmetic rules are themselves enforced: a rate over an empty denominator renders absent rather
than zero, a calibration cell refuses a sample size below one, and every table publishing a rate
publishes its denominator.

**A rate is asserted only where it is an invariant of the construction rather than a finding.** Four
are: act-class escapes are zero; the escape-rate ordering across arms is monotone over frozen
replays; the false-block ordering between the last two arms is structural rather than predicted; and
the reference arm's escape rate is one, because that arm has no detector. Every other number is
reported and never asserted. That rule is what keeps a measurement from quietly becoming a gate.

---

## 7. Limits, as properties of the system

These are limits of the boundary, not of the corpus.

- The audit detects tampering; it does not prevent it. A whole-chain rewrite verifies.
- `verify` does not detect a torn tail. The flag is caller-passed and echoed. A missing log reads as
  zero entries and no tear.
- The log is an enforcement input, so a lost line is a predicate failing open.
- The double-send guard is built and unarmed: nothing schedules the resume pass, nothing consults the
  approval-requirement predicate, and no shipped path feeds a log-derived count into an `ActContext`.
  The cap's payload input defaults permissively.
- Symbols are built and called from nowhere outside the suite. How many is a question for the census
  in `tests/test_readme_claims.py` and not for this sentence, which is why no number appears here:
  the census measures call sites by name and cannot see two of the symbols the README enumerates.
  Among the uncalled is `pre_tool_use`, which section 2.1 publishes: a contract name with no shipped
  caller.
- No cross-turn breach is demonstrated caught. Per-draft statelessness **is** held; what is absent is
  the thread-scope pass.
- The checker's stated confidence routes nothing. No threshold promotes or auto-approves.
- Policy is read-only, and no tool surface exists, so that invariant is vacuous in this tree.
- Tier 3 is unreachable for any outward-sending surface. The ceiling is structural and rests on no
  measured cell.
- The static audits are denylists over the statically visible import graph, not sandboxes. A dynamic
  import, a builtin call, or a shadowing redefinition passes.

---

## 8. The delta this specification requires

Everything above describes the tree except the contract in section 2, which the tree does not publish.
Closing that is the work the accompanying plan sequences.

1. **Publish the export surface.** The package init is empty and there is no `__all__`. Publishing
   means deciding what is public and saying so once, in one place.
2. **Ship a `py.typed` marker.** The package is annotated throughout, and without the marker a
   downstream type checker treats it as untyped under PEP 561. The annotations exist; nothing tells a
   consumer they are meant.
3. **Resolve the shipped-but-unreadable paths.** Three modules under `evals/` compute a repository
   root by climbing four parents from their own file and build corpus paths from it. In a checkout
   that lands on the repository root and works. Installed under `site-packages` it climbs above it and
   the corpus is not there. The constants bind at import without error, so the failure appears only on
   read. Either these modules stop travelling, or the paths become injectable, or the failure becomes
   explicit at import.
4. **Reconcile the dependency declaration with the import graph.** `pydantic-ai` is a hard runtime
   dependency, exactly one module imports it, and that module is outside the contract. Every module a
   consumer needs imports without it.
5. **Guard the contract in both directions.** The export surface and this document must not drift
   apart: a name published without appearing here, or named here without being exported, should fail
   the build. The repository already uses this shape for its designed-versus-built table and for its
   census of uncalled layers.

None of these is a change to policy, to a published number, or to a frozen recording.

**Three sentences in this document go false the moment that work lands, and no guard reads any of
them.** Section 2's opening paragraph describes an empty package init; section 3 names `pydantic-ai`
a runtime dependency; and this section describes a delta that will be closed. The plan carries an
explicit amendment step for all three, in the A1 through A4 convention, because a specification that
its own implementation falsifies is worse than one that never specified anything. They are listed
here so the amendment cannot be forgotten by a reader who only opens section 8.

---

## 9. Non-goals

This specification does not restate `docs/architecture.md`, which carries the reasoning behind these
decisions, or `docs/measurement.md`, which carries the evaluation protocol. It carries the contract,
the register and the limits, and links rather than duplicates. Two documents describing one system in
parallel is the drift this repository spends its suite preventing.
