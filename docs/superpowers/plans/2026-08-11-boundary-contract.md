# Boundary Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the import contract this package already has but never declared, and hold it to the
specification in both directions so the two cannot drift apart.

**Architecture:** Five changes at the packaging boundary, executed in order. Tasks 2, 4 and 5 all
consume artifacts Task 1 creates, so they are sequential rather than independent. Nothing under `policy/`,
`gates/`, `audit/` or `matching/` changes behaviour; no published number moves; no frozen recording
is re-run. Four tasks fix a packaging defect a consumer can hit, and the fifth makes the contract
self-enforcing using the same both-directions shape the repository already uses for its
designed-versus-built table and its census of uncalled layers.

**Tech Stack:** Python >=3.11, hatchling, pytest. No new dependencies.

**Source of truth:** `docs/superpowers/specs/2026-08-10-boundary-specification.md`, section 8. Section
2 of that document is the contract this plan publishes; where this plan and that section disagree,
the specification wins and this plan is amended in a review round.

## Global Constraints

- `policy/` imports no LLM client, no I/O, no clock. Enforced by `tools/static_audit.py` and by an
  edit-time hook. If a predicate needs a value, pass it in as an argument.
- **"Zero by construction" is claimable for act-classes only.** Never write it about a content-class,
  in code, comments, docstrings, or documentation.
- **No organisation name and no person's name anywhere in this repository.**
- **A test must assert the property, not a proxy for it. Assert effects, never invocations.**
- No network in tests. Everything runs offline and keyless.
- CI asserts invariants only. Probabilistic rates are measured and reported, never asserted.
- `corpus/` is read-only. The 160 recorded verdicts are never re-run.
- `python tools/report.py` must regenerate `docs/RESULTS.md` with no diff.
- `python tools/static_audit.py` must exit 0.
- No em dashes in reader-facing documents.
- Conventional commit prefix; no trailers. There is no remote; never push.
- Run pytest with `--deselect tests/test_mutations.py`. **Never run that file unfiltered and never
  run the mutation sweep** unless the task says so: it mutates `src/` and an interrupted run strands
  a mutation. Never run either demo with `--live`; it spends money.
- TDD throughout: failing test, watch it fail, minimal implementation, watch it pass, commit.
  **Never skip watching it fail.**

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `src/chaperone/__init__.py` | The single declaration of what the package publishes | 1 |
| `tests/test_contract.py` | Holds the export surface, the marker, and the specification to each other | 1, 2, 5 |
| `src/chaperone/py.typed` | PEP 561 marker so annotations are honoured downstream | 2 |
| `src/chaperone/evals/{corpus,harness,discrimination}.py` | Stop binding unreadable paths at import | 3 |
| `pyproject.toml` | Dependency declaration matching the import graph; wheel contents | 3, 4 |

---

### Task 1: The package publishes its contract

**Files:**
- Modify: `src/chaperone/__init__.py` (currently 0 bytes)
- Test: `tests/test_contract.py` (create)

**Interfaces:**
- Produces: `chaperone.__all__: tuple[str, ...]`, and every name in it importable as
  `from chaperone import <name>`.

The names come from section 2 of the specification. They are listed here in full so this task needs
no other document open.

**Three names in section 2 are deliberately NOT in `__all__`, and each for a stated reason.** Do not
add them; adding the first one halts this task.

- **`transmit`.** `tools/static_audit.py` fails the build if any file inside `src/chaperone` other
  than `audit/gateway.py` names it, and the package init is inside that root. Importing it here
  trips the reservation on the init's first line, and step 5 would exit 1. It is published at
  `chaperone.audit.gateway.transmit` and reached only there. Specification section 2.4 says so.
- **`CHECKER_INSTRUCTIONS`.** Frozen bytes. The recorded verdicts were produced under its exact
  content, so it is not a consumer tuning surface.
- **`build_checker_messages`.** `Checker.check` calls it; a consumer supplying a transport never
  does.

- [ ] **Step 1: Write the failing test**

Create `tests/test_contract.py`:

```python
"""The package's declared surface, held to the specification that publishes it.

`src/chaperone/__init__.py` was empty for the whole of this repository's history, so every consumer
import was a submodule path found by reading source. A downstream project derived its own contract
that way and recorded that this library published none, having first guessed `ActContext` into the
wrong module. This module is what stops the next consumer having to guess.
"""
import chaperone


def test_every_published_name_is_importable_from_the_package_root():
    """The contract is a promise about `from chaperone import X`, so that is what is asserted.

    Asserting `__all__`'s contents alone would pass over a name that is listed and not bound, which
    is the failure a consumer meets rather than the one a maintainer sees.
    """
    assert chaperone.__all__, "the package declares no contract"
    missing = [name for name in chaperone.__all__ if not hasattr(chaperone, name)]
    assert missing == [], f"declared in __all__ and not importable: {missing}"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/test_contract.py -q`
Expected: FAIL with `AttributeError: module 'chaperone' has no attribute '__all__'`.

- [ ] **Step 3: Write the package init**

Replace the contents of `src/chaperone/__init__.py`:

```python
"""chaperone: a deterministic authorization boundary for an agent acting under stated constraints.

The contract below is the one published surface. It is held to
`docs/superpowers/specs/2026-08-10-boundary-specification.md` section 2 in both directions by
`tests/test_contract.py`: a name exported and not specified fails, and a name specified and not
exported fails.

Everything not listed here is internal, including `chaperone.testing`, whose recorded and scripted
transports exist for this repository's own suite and may change without notice. A consumer wanting a
replaying transport writes one against the seam in section 2.3.
"""
from chaperone.audit.chain import GENESIS_HASH, VerifyResult, link, verify
from chaperone.audit.entry import INDETERMINATE_OUTCOMES, AuditEntry
from chaperone.audit.gateway import Gateway, GatewayResult
from chaperone.audit.store import AuditStore
from chaperone.gates.checker import (
    MODEL_STRENGTH,
    Checker,
    CheckerResult,
    CheckerUnavailable,
    FlagForReview,
    Verdict,
    assert_checker_not_weaker,
)
from chaperone.gates.engine import decide, denial_result, destination_for, disposition_for
from chaperone.gates.handoff import Handoff, build_handoff
from chaperone.gates.hook import HookOutcome, guarded_call, pre_tool_use
from chaperone.gates.queues import ReviewQueues
from chaperone.gates.sdk_callback import pre_tool_use_deny
from chaperone.matching.filters import Candidate, Eligibility, Mandate, classify
from chaperone.matching.rank import rank
from chaperone.matching.relationship import relationship_score
from chaperone.policy.act_classes import ActContext, evaluate_act_classes
from chaperone.policy.arguments import unsendable_finding, unsendable_in
from chaperone.policy.citations import validate_citations
from chaperone.policy.payload import (
    CONSENTED,
    CONSUMED_KEYS,
    GRANTED,
    UNENFORCEABLE_HERE,
    build_act_inputs,
)
from chaperone.policy.tripwires import TRIPWIRE_CLASSES, evaluate_tripwires
from chaperone.policy.types import (
    Decision,
    Disposition,
    Draft,
    Family,
    Finding,
    Message,
    Record,
    ViolationClass,
)

__all__ = (
    "ActContext",
    "AuditEntry",
    "AuditStore",
    "CONSENTED",
    "CONSUMED_KEYS",
    "Candidate",
    "Checker",
    "CheckerResult",
    "CheckerUnavailable",
    "Decision",
    "Disposition",
    "Draft",
    "Eligibility",
    "Family",
    "Finding",
    "FlagForReview",
    "GENESIS_HASH",
    "GRANTED",
    "Gateway",
    "GatewayResult",
    "Handoff",
    "HookOutcome",
    "INDETERMINATE_OUTCOMES",
    "MODEL_STRENGTH",
    "Mandate",
    "Message",
    "Record",
    "ReviewQueues",
    "TRIPWIRE_CLASSES",
    "UNENFORCEABLE_HERE",
    "Verdict",
    "VerifyResult",
    "ViolationClass",
    "assert_checker_not_weaker",
    "build_act_inputs",
    "build_handoff",
    "classify",
    "decide",
    "denial_result",
    "destination_for",
    "disposition_for",
    "evaluate_act_classes",
    "evaluate_tripwires",
    "guarded_call",
    "link",
    "pre_tool_use",
    "pre_tool_use_deny",
    "rank",
    "relationship_score",
    "unsendable_finding",
    "unsendable_in",
    "validate_citations",
    "verify",
)
```

- [ ] **Step 4: Run it and watch it pass**

Run: `python -m pytest tests/test_contract.py -q`
Expected: PASS.

- [ ] **Step 5: Confirm the purity audit still exits 0**

Run: `python tools/static_audit.py`
Expected: exit 0. The new init imports `chaperone.policy.*`, which is a `policy/`-inward import and
not a `policy/`-outward one, so the denylist is unaffected. **If it does not exit 0, the guard is
right and this step is wrong: report it rather than editing the audit.**

- [ ] **Step 6: Run the suite**

Run: `python -m pytest --deselect tests/test_mutations.py -q`
Expected: exit 0. Report the collected count before and after; adding a tracked file under `tests/`
changes repo-wide parametrized guards at `git commit`, not before.

- [ ] **Step 7: Commit**

```bash
git add src/chaperone/__init__.py tests/test_contract.py
git commit -m "feat: the package declares the contract a consumer had to derive by reading source"
```

---

### Task 2: The annotations are declared to downstream type checkers

**Files:**
- Create: `src/chaperone/py.typed` (empty file)
- Modify: `tests/test_contract.py`

**Interfaces:**
- Consumes: `tests/test_contract.py` from Task 1.
- Produces: nothing importable. The marker is packaging metadata.

The package is annotated throughout. Without this marker, PEP 561 tells a downstream type checker to
treat it as untyped, so a consumer gets no benefit from annotations that already exist.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_contract.py`:

```python
from pathlib import Path

PACKAGE_ROOT = Path(chaperone.__file__).resolve().parent


def test_the_package_ships_a_pep_561_marker():
    """Annotated and unmarked is worse than unannotated: it looks typed and checks as untyped.

    The marker must also be inside the wheel target, which is why this asserts its location rather
    than its existence anywhere in the repository.
    """
    marker = PACKAGE_ROOT / "py.typed"
    assert marker.is_file(), (
        f"no PEP 561 marker at {marker}; a downstream type checker ignores every annotation here"
    )
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/test_contract.py::test_the_package_ships_a_pep_561_marker -q`
Expected: FAIL, "no PEP 561 marker at ...".

- [ ] **Step 3: Create the marker**

Create `src/chaperone/py.typed` as an empty file. PEP 561 specifies an empty marker; do not write
content into it.

- [ ] **Step 4: Run it and watch it pass**

Run: `python -m pytest tests/test_contract.py::test_the_package_ships_a_pep_561_marker -q`
Expected: PASS.

- [ ] **Step 5: Make hatchling ship it**

A wheel target naming `packages = ["src/chaperone"]` includes package data by default under
hatchling, so no `pyproject.toml` change is expected. **Verify rather than assume:**

Git Bash, from the repository root:

```bash
python -m pip install --quiet build
python -m build --wheel --outdir dist .
python -c "import zipfile,glob;z=sorted(glob.glob('dist/*.whl'))[-1];print([n for n in zipfile.ZipFile(z).namelist() if 'py.typed' in n])"
```

Expected: a list containing `chaperone/py.typed`. If it is empty, add to `pyproject.toml` under
`[tool.hatch.build.targets.wheel]`:

```toml
[tool.hatch.build.targets.wheel.force-include]
"src/chaperone/py.typed" = "chaperone/py.typed"
```

and re-run the build to confirm. Delete the built wheel and the temporary directory afterwards;
nothing from the build is committed. Record in the report which branch was taken.

- [ ] **Step 6: Commit**

```bash
git add src/chaperone/py.typed tests/test_contract.py
git commit -m "feat: mark the package typed, so its annotations reach a consumer"
```

---

### Task 3: Shipped modules stop binding paths that do not ship

**Files:**
- Modify: `src/chaperone/evals/corpus.py:116-119`
- Modify: `src/chaperone/evals/harness.py:71-72`
- Modify: `src/chaperone/evals/discrimination.py:18-19`
- Test: `tests/evals/test_corpus_paths.py` (create)

**Interfaces:**
- Produces: `chaperone.evals.corpus.corpus_root() -> Path`, raising `CorpusError` when the corpus is
  not reachable. `CORPUS_PATH`, `BLIND_DRAFTS_PATH`, `LABELS_PATH`, `RECORDED_VERDICTS_PATH` and
  `QUALITY_SCORES_PATH` are removed as module-level constants and become module-level functions of
  the same name in lowercase: `corpus_path()`, `blind_drafts_path()`, `labels_path()`,
  `recorded_verdicts_path()`, `quality_scores_path()`.

**The defect.** All three modules compute `_ROOT = Path(__file__).resolve().parents[3]` and build
`corpus/` paths from it at import. In a checkout that lands on the repository root and works.
Installed under `site-packages` it climbs above the packages directory and the corpus is not there.
The constants bind without error either way, so the failure appears only on read, far from its cause.

**Callers to update.** Find them before editing:

```bash
grep -rn "CORPUS_PATH\|BLIND_DRAFTS_PATH\|LABELS_PATH\|RECORDED_VERDICTS_PATH\|QUALITY_SCORES_PATH" --include=*.py src/ tools/ tests/ demo/
```

Every hit becomes a call. This is mechanical, and the suite is the check.

- [ ] **Step 1: Write the failing test**

Create `tests/evals/test_corpus_paths.py`:

```python
"""The corpus is data this package reads, not data it ships.

`chaperone/evals/` travels in the wheel because it sits under `src/chaperone`; `corpus/` does not,
because it is a sibling of `src/`. Module-level constants computed from `__file__` bound silently to
a directory that exists in a checkout and does not exist in an installation, so the failure surfaced
on read rather than at the boundary that caused it.
"""
import pytest

from chaperone.evals.corpus import CorpusError, corpus_path, corpus_root


def test_the_corpus_root_resolves_in_a_checkout():
    """The working case, asserted so the refusal below cannot pass by refusing everything."""
    assert corpus_root().is_dir()
    assert corpus_path().is_file()


def test_an_unreachable_corpus_is_refused_by_name_rather_than_bound_silently(monkeypatch):
    """A path that cannot exist must raise where it is asked for, naming what is missing.

    The production change this catches: reverting `corpus_root` to a module-level constant computed
    from `__file__`, which binds an unreadable path at import and defers the error to the first read.
    """
    import chaperone.evals.corpus as corpus_module

    monkeypatch.setattr(corpus_module, "_SEARCH_FROM", corpus_module.Path("/nonexistent-root"))
    with pytest.raises(CorpusError, match="corpus"):
        corpus_root()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/evals/test_corpus_paths.py -q`
Expected: FAIL with `ImportError: cannot import name 'corpus_root'`.

- [ ] **Step 3: Implement in `corpus.py`**

Replace lines 116 to 119 of `src/chaperone/evals/corpus.py`:

```python
#: Where the search for the corpus begins. A module attribute rather than a literal inside the
#: function so a test can point it somewhere that cannot exist, which is the only way to assert the
#: refusal without depending on where this checkout happens to live.
_SEARCH_FROM = Path(__file__).resolve()


def corpus_root() -> Path:
    """The `corpus/` directory, or a refusal naming it.

    Walks upward from this module rather than counting parents. Counting bound correctly in a
    checkout and silently wrongly in an installation, where the same count climbs above
    `site-packages`. Walking finds the directory where it exists and finds nothing where it does
    not, which is the honest answer for a consumer who installed the wheel: the corpus is not part
    of it.
    """
    for parent in _SEARCH_FROM.parents:
        candidate = parent / "corpus"
        # `is_dir()` alone would bind any `corpus/` above an installed package -- a consumer whose
        # own project happens to have one gets a stranger's directory and a failure deferred to
        # read, which is the shape this function exists to end. The marker file is what makes it
        # ours.
        if (candidate / "drafts.jsonl").is_file():
            return candidate
    raise CorpusError(
        "no corpus/ directory above "
        f"{_SEARCH_FROM}: the corpus is not shipped in the wheel, so an installed copy of this "
        "package cannot read it. Run from a checkout, or pass the rows in yourself."
    )


def corpus_path() -> Path:
    return corpus_root() / "drafts.jsonl"


def blind_drafts_path() -> Path:
    return corpus_root() / "blind-drafts.jsonl"


def labels_path() -> Path:
    return corpus_root() / "labels.jsonl"
```

- [ ] **Step 4: Run it and watch it pass**

Run: `python -m pytest tests/evals/test_corpus_paths.py -q`
Expected: PASS.

- [ ] **Step 5: Do the same in the other two modules**

In `src/chaperone/evals/harness.py`, replace lines 71 to 72:

```python
def recorded_verdicts_path() -> Path:
    """The frozen verdicts. See `corpus_root` for why this walks rather than counts."""
    return corpus_root() / "recorded_verdicts.json"
```

and import `corpus_root` from `chaperone.evals.corpus` alongside the existing imports from that
module.

In `src/chaperone/evals/discrimination.py`, replace lines 18 to 19:

```python
def quality_scores_path() -> Path:
    """The frozen quality scores. See `corpus_root` for why this walks rather than counts."""
    return corpus_root() / "quality-scores.jsonl"
```

and import `corpus_root` from `chaperone.evals.corpus`.

Delete the now-unused `_ROOT` assignment from each of the three modules.

- [ ] **Step 6: Update every caller, and do NOT mechanically substitute a call**

Re-run the grep from the preamble. Most hits are ordinary references that become calls. **Seven are
default parameter values, and substituting a call into a default recreates the exact defect this
task exists to remove**: a default is evaluated at import, so `def load(path=corpus_path())` binds
at import time again, and inside a wheel it turns `import chaperone.evals.corpus` into a raise.
Worse, in a checkout the walk succeeds, so the suite, the generators and the anchor audit all pass
over it. **Nothing in this plan's verification would catch it.**

The seven sites, measured:

| Site | Function |
|---|---|
| `src/chaperone/evals/corpus.py:210` | `load_corpus` |
| `src/chaperone/evals/corpus.py:350` | `load_labels` |
| `src/chaperone/evals/harness.py:147` | `load_recorded` |
| `src/chaperone/evals/discrimination.py:91` | `load_quality_scores` |
| `tools/build_corpus.py:179` | `build` |
| `tools/label_corpus.py:98` | `write_labels` |
| `tools/record_verdicts.py:147` | `record` |

Each takes a `None` sentinel and resolves in the body:

```python
def load_corpus(path: Path | None = None) -> list[CorpusItem]:
    path = corpus_path() if path is None else path
    ...
```

That keeps every existing call site working unchanged, keeps the caller's ability to pass a path,
and moves resolution to call time where the refusal belongs.

- [ ] **Step 6b: Prove the sentinel holds, rather than assuming it**

Add to `tests/evals/test_corpus_paths.py`:

```python
import inspect

from chaperone.evals import corpus as corpus_module
from chaperone.evals import discrimination, harness


def test_no_loader_resolves_a_corpus_path_in_a_default_argument():
    """A default is evaluated at import, which is the binding this task removes.

    The production change this catches: `def load_corpus(path=corpus_path())`. In a checkout the
    walk succeeds and every other check in this plan stays green, so only this test sees it.
    """
    loaders = [
        corpus_module.load_corpus, corpus_module.load_labels,
        harness.load_recorded, discrimination.load_quality_scores,
    ]
    for fn in loaders:
        for name, param in inspect.signature(fn).parameters.items():
            if "path" not in name:
                continue
            assert param.default is None, (
                f"{fn.__module__}.{fn.__qualname__} resolves {name} in a default argument, which "
                "binds at import; use a None sentinel and resolve in the body"
            )
```

Watch it fail first by giving one loader a resolved default, then restore.

- [ ] **Step 7: Run the suite and the generators**

Run: `python -m pytest --deselect tests/test_mutations.py -q`
Expected: exit 0.

Run: `python tools/report.py` then `git diff --exit-code -- docs/RESULTS.md`
Expected: no diff. **The published numbers must not move.** If they do, stop: this task changed how
a path is found, never what is read, and a moved number means something else changed.

- [ ] **Step 8: Run the anchor audit**

Run: `python -m pytest tests/test_mutations.py -k anchor -q`
Expected: PASS, and expected to be uneventful: all seventeen mutants anchor in `gates/`, `audit/`,
`policy/` and `matching/`, and **none anchors in `evals/`**, so this task cannot move one. The step
runs anyway because that is a measured fact about today's mutant list rather than a property of the
harness. That selection is read-only. **Do not run the file unfiltered.**

- [ ] **Step 9: Commit**

```bash
git add src/chaperone/evals tests/evals/test_corpus_paths.py tools/ tests/
git commit -m "fix: the corpus is found by walking, so an installed copy refuses instead of misreading"
```

---

### Task 4: The dependency declaration matches the import graph

**Files:**
- Modify: `pyproject.toml:5-13` (through the `dev` line inclusive; stopping at 12 leaves a second
  `dev` key below the new block and `tomllib` raises on the next parse)
- Test: `tests/test_contract.py`

**Interfaces:**
- Consumes: `tests/test_contract.py` from Task 1, and the module-level `PACKAGE_ROOT` that Task 2
  defines in it. If Task 2 has not run, define `PACKAGE_ROOT` here instead:
  `PACKAGE_ROOT = Path(chaperone.__file__).resolve().parent`.
- Produces: `pydantic-ai` moves from `dependencies` to a new `binding` extra.

**Measured:** exactly one module imports `pydantic_ai`, and it is `gates/binding.py`, which is not in
the contract. Blocking `pydantic_ai` in a subprocess leaves 39 of 40 modules importing cleanly, and
every module a consumer needs is among the 39. A hard runtime dependency for one optional module
forces the whole dependency tree on every consumer that never touches it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_contract.py`:

```python
import tomllib


def test_every_hard_dependency_is_imported_by_a_module_in_the_contract():
    """A dependency a consumer must install for code it cannot reach is a tax, not a requirement.

    The production change this catches: declaring a package under `dependencies` when the only
    module importing it is outside `__all__`'s reachable set, which is how `pydantic-ai` came to be
    mandatory for consumers that never construct a transport.
    """
    manifest = tomllib.loads((PACKAGE_ROOT.parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    hard = {d.split(">")[0].split("=")[0].strip() for d in manifest["project"]["dependencies"]}
    assert hard == {"pydantic"}, (
        f"hard dependencies are {sorted(hard)}; anything beyond pydantic must be an extra, because "
        "every module in the published contract imports without it"
    )
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/test_contract.py::test_every_hard_dependency_is_imported_by_a_module_in_the_contract -q`
Expected: FAIL, hard dependencies are `['pydantic', 'pydantic-ai']`.

- [ ] **Step 3: Move the dependency**

In `pyproject.toml`, change lines 5 to 13 inclusive to:

```toml
dependencies = [
    "pydantic>=2.7",
]

[project.optional-dependencies]
binding = ["pydantic-ai>=2.23"]
evals = ["pydantic-evals>=2.23"]
sdk = ["claude-agent-sdk>=0.2.130"]
dev = ["pytest>=8.0", "hypothesis>=6.100", "pydantic-ai>=2.23"]
```

`pydantic-ai` joins `dev` because the suite imports `gates/binding.py` and must keep doing so.

- [ ] **Step 4: Run it and watch it pass**

Run: `python -m pytest tests/test_contract.py::test_every_hard_dependency_is_imported_by_a_module_in_the_contract -q`
Expected: PASS.

- [ ] **Step 5: Update the reader-facing pages that state the dependency**

Run: `grep -rn "pydantic-ai" README.md docs/*.md`

Every sentence describing `pydantic-ai` as a runtime dependency becomes a sentence describing it as
the `binding` extra. **The README's designed-versus-built table and its census are guarded in both
directions; if a row moves, its registry entry moves in the same commit.**

**Measured warning: that grep's scope is probably empty, and the sentence that must move is outside
it.** No `README.md` or `docs/*.md` sentence currently calls `pydantic-ai` a runtime dependency; the
one that does is in the specification, at section 3, which the non-recursive glob cannot reach. An
implementer who runs only this grep will conclude there is nothing to do and leave a false sentence
in the document this plan derives from. Step 5b is where the real edit lives.

- [ ] **Step 5b: Amend the specification, in the A1 to A4 convention**

Executing this plan falsifies three sentences in
`docs/superpowers/specs/2026-08-10-boundary-specification.md`, and **no guard reads any of them**:

| Location | Goes false because | After |
|---|---|---|
| Section 2 opening | says the package init is empty | Task 1 published it |
| Section 3 | names `pydantic-ai` a runtime dependency | Task 4 moved it to an extra |
| Section 8 | describes a delta not yet closed | all five tasks |

Amend all three in place and add a dated entry recording what changed and why, following the
convention the stack-binding spec uses for A1 through A4. **In place, not appended**: a reader who
opens section 3 does not read an appendix, and the specification's own section 8 note says these
three must move together.

- [ ] **Step 6: Run the suite and the claims guards**

Run: `python -m pytest --deselect tests/test_mutations.py -q`
Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml tests/test_contract.py README.md docs/
git commit -m "fix: one module needs pydantic-ai, so one extra carries it"
```

---

### Task 5: The contract and the specification hold each other

**Files:**
- Modify: `tests/test_contract.py`

**Interfaces:**
- Consumes: `chaperone.__all__` from Task 1, and the module-level `PACKAGE_ROOT` that Task 2 defines
  in `tests/test_contract.py`.
- Produces: nothing importable.

This is the task the other four exist to make possible. A published contract that drifts from its
specification is worse than an unpublished one, because a consumer now has a document to trust. The
repository already holds its designed-versus-built table this way, and the docstring of that test
records why one direction was not enough: asserting only that a listed module is absent fires once,
and never checks the row it caused to be written.

**A regex over section 2's prose cannot work, and trying it corrupts the specification.** Measured:
that region contains the contract names plus nine legitimate backticked identifiers that are not
exports and never will be, including `__all__`, `act_classes`, `types`, `model`, `drafter_model`,
`isinstance`, `body` and `TypeError`. Every one sits in a sentence that is true and correctly
backticked. No honest edit removes them, and no lexical rule separates `model` from a contract name
like `rank` in the same register. An executor asked to make such a guard green will strip backticks
from prose, which is the corruption this task exists to prevent.

So the specification gains **one canonical table** that the guard reads exclusively, and the prose
stays prose. This follows the designed-versus-built precedent, where the registry is a Python
structure and the page is held against it rather than parsed as English.

- [ ] **Step 1: Add the canonical table to the specification**

Append to section 2 of `docs/superpowers/specs/2026-08-10-boundary-specification.md`, immediately
before section 3:

```markdown
### 2.9 The contract, enumerated

This table is the contract. The prose above explains it; this is what a guard reads, and what
`chaperone.__all__` is held to in both directions. A name in one and not the other is a defect in
whichever moved last.

| Name | Module |
|---|---|
| `ActContext` | `policy.act_classes` |
| `AuditEntry` | `audit.entry` |
...
```

Populate every row from `__all__` as Task 1 landed it, with the module each name is imported from.
`transmit` does **not** appear: it is published at its module path only, for the reason section 2.4
gives.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_contract.py`:

```python
import re

SPEC = PACKAGE_ROOT.parents[1] / "docs" / "superpowers" / "specs" / "2026-08-10-boundary-specification.md"
#: Rows of the canonical table in section 2.9: | `Name` | `module` |
_ROW = re.compile(r"^\|\s*`([A-Za-z_][A-Za-z0-9_]*)`\s*\|\s*`([A-Za-z_][A-Za-z0-9_.]*)`\s*\|", re.M)


def _specified_names() -> set[str]:
    text = SPEC.read_text(encoding="utf-8")
    start = text.index("### 2.9 The contract, enumerated")
    end = text.index("## 3. What travels")
    return {name for name, _module in _ROW.findall(text[start:end])}


def test_the_published_contract_and_the_specification_agree_in_both_directions():
    """Neither document may move without the other.

    One direction alone is the failure the designed-versus-built guard already records: asserting
    only that every exported name is specified passes a specification that grew a name nobody
    exported, and asserting only the reverse passes an export nobody wrote down. A consumer reads
    the specification and imports from the package, so a disagreement is a broken promise whichever
    side moved.
    """
    specified = _specified_names()
    assert specified, "the canonical table parsed to nothing, so this guard would pass vacuously"
    exported = set(chaperone.__all__)

    unspecified = sorted(exported - specified)
    assert unspecified == [], (
        f"exported and absent from the table in section 2.9: {unspecified}. Either add the row or "
        "stop exporting the name."
    )

    unexported = sorted(specified - exported)
    assert unexported == [], (
        f"in the table in section 2.9 and not exported: {unexported}. Either export it or remove "
        "the row."
    )
```

No filter is needed on either side, because the table contains only names. That is the point of
reading a table rather than prose.

- [ ] **Step 2b: Prove the table is not silently empty**

Also append:

```python
def test_the_canonical_table_is_not_trivially_small():
    """A table that parsed to two rows would make the guard above nearly vacuous.

    The production change this catches: a regex edit that stops matching most rows, which leaves
    both set differences empty for the wrong reason.
    """
    assert len(_specified_names()) >= 40, (
        f"only {len(_specified_names())} rows parsed from section 2.9; the contract is larger than "
        "that, so the row pattern has stopped matching"
    )
```

- [ ] **Step 3: Make it pass by fixing the disagreement, never the guard**

Adjust `__all__` and the specification until both assertions hold. Record in the report every name
that moved and in which direction, because that record is the audit trail for the contract's first
publication.

- [ ] **Step 4: Watch it fail from the other side**

Temporarily add `"NotAContractName"` to `chaperone.__all__` and re-run. Expected: the first assertion
fires naming it. Remove it. This proves both directions bite rather than one.

- [ ] **Step 5: Run the suite**

Run: `python -m pytest --deselect tests/test_mutations.py -q`
Expected: exit 0.

- [ ] **Step 6: Run the full gate**

Run each and expect exit 0: `python tools/static_audit.py`, `python tools/scan_secrets.py`,
`python tools/coverage_map.py`, `python tools/lint_descriptions.py`. Then `python tools/report.py`
and `git diff --exit-code`.

- [ ] **Step 7: Commit**

```bash
git add tests/test_contract.py src/chaperone/__init__.py docs/superpowers/specs/2026-08-10-boundary-specification.md
git commit -m "test: the contract and its specification cannot drift apart in either direction"
```

---

## Verification

After Task 5, the whole gate on the final tree:

```bash
python -m pytest --deselect tests/test_mutations.py -q     # exit 0
python -m pytest tests/test_mutations.py -q                 # exit 0, run once, exclusively
git diff --exit-code                                        # clean after the sweep
python tools/static_audit.py && python tools/scan_secrets.py
python tools/coverage_map.py && python tools/lint_descriptions.py
python tools/report.py && git diff --exit-code -- docs/RESULTS.md
python demo/day2.py && python demo/full.py                  # both exit 0
```

The mutation sweep runs once here because Tasks 1 through 4 change `src/`, and an intact anchor does
not prove an intact kill. It applies mutants to the working tree, so **nothing else may run while it
does**, and a killed run strands a mutation: if that happens, `python -m tools.mutate` recovers
before it audits.

Then two installation checks. **They are different checks and the second is the one that matters.**
Commands are Git Bash; on win32 use a short path, because a deep one breaks `pip` on `MAX_PATH`.

**Check A, fresh clone, editable.** Proves a checkout still installs and passes:

```bash
git clone --branch main . /c/Users/$USER/cc && cd /c/Users/$USER/cc
python -m venv .venv && ./.venv/Scripts/python -m pip install -e ".[dev]"
./.venv/Scripts/python -m pytest -q --deselect tests/test_mutations.py
```

Expected: install clean, exit 0. The mutation sweep is deselected because it has already run once
above; running it again here is a second full sweep mid-check for no added evidence.

**Check B, built wheel, no extras. This is the consumer's path, and check A exercises none of it.**
An editable install puts the repository root above `src/`, so `corpus_root()` always succeeds and
its refusal never fires; `py.typed` is present in-tree whether or not it ships; and the `dev` extra
drags in `pydantic-ai`, so Task 4's property is never tested. All four packaging defects are
invisible to check A.

```bash
cd /c/Users/$USER/cc
./.venv/Scripts/python -m pip install build
./.venv/Scripts/python -m build --wheel --outdir dist .
python -m venv /c/Users/$USER/cw && cd /c/Users/$USER/cw
./Scripts/python -m pip install /c/Users/$USER/cc/dist/*.whl
./Scripts/python -c "
import chaperone, pathlib
missing = [n for n in chaperone.__all__ if not hasattr(chaperone, n)]
assert missing == [], missing
root = pathlib.Path(chaperone.__file__).parent
assert (root / 'py.typed').is_file(), 'py.typed did not ship'
import importlib; assert importlib.util.find_spec('pydantic_ai') is None, 'pydantic-ai came along'
from chaperone.evals.corpus import CorpusError, corpus_root
try:
    corpus_root(); raise SystemExit('corpus_root resolved inside a wheel')
except CorpusError:
    pass
print('wheel contract OK')
"
```

Expected: `wheel contract OK`. Each line proves one of the four defects fixed: every published name
imports, the marker shipped, the dependency is genuinely optional, and the corpus refuses by name
instead of misreading. Delete both temporary directories afterwards; nothing from either is
committed.
