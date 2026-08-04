# chaperone

Bounded autonomy for an agent acting under stated constraints. All data is synthetic.

## Non-negotiable

- `policy/` imports no LLM client, no I/O, no clock. Enforced by a PreToolUse hook at edit time and
  by `tools/static_audit.py` in CI. If a predicate needs a value, pass it in as an argument.
- **"Zero by construction" is claimable for act-classes only.** Never write it about a content-class,
  in code, comments, docstrings, or documentation.
- **No organisation name appears anywhere in this repository.** This describes a synthetic scenario.
- **A test must assert the property, not a proxy for it.** Assert effects, never invocations. A spy
  counting that a checker was called proves the checker ran, not that it governed.
- **No network in tests.** Everything runs offline and keyless via recorded transports.
- CI asserts invariants only. Probabilistic rates are measured and reported, never asserted.

## Workflow

- TDD throughout: failing test, watch it fail, minimal implementation, watch it pass, commit.
  **Never skip watching it fail.** A test that was never observed failing may be asserting nothing.
- Run `/verify` before every commit.

## When a guard fires

Three guards will block you. In every case **the guard is right and the work is wrong.**

- **The PreToolUse hook refuses an edit.** Do not disable it, do not edit `tools/guard_edit.py` to
  pass, do not route around it with a shell command. Fix the edit.
- **A mutant survives `tests/test_mutations.py`.** Do not weaken or delete the mutant. Find the proxy
  test it exposed and strengthen it — that is the mutant's entire purpose.
- **`tools/static_audit.py` reports an impurity.** Do not add the module to the allowed list. Pass the
  value in as an argument instead.

A guard that can be argued with is a suggestion. If one of these is genuinely wrong, stop and raise it
rather than editing it silently.
