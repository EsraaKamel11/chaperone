---
description: Run the full verification gate
---

Run these in order and report any failure verbatim:

```bash
git status --porcelain src/
python -m pytest
python tools/scan_secrets.py
python tools/static_audit.py
python tools/coverage_map.py
python tools/lint_descriptions.py
python tools/report.py
python tools/perturbation_log.py
python demo/day2.py
python demo/full.py
git status --porcelain
```

**`git status --porcelain src/` runs first and must print nothing.** The mutation sweep rewrites
files under `src/` in place and restores them in a `finally`; a run killed with SIGTERM skips that
`finally` and strands a mutant -- twice a fail-open one -- which `git commit -a` would then commit.
Checking before the suite catches a mutant stranded by the *previous* run, which is the one nobody
is watching for.

**Run `python -m pytest`, not `pytest -q`.** `addopts = "-q"` is already set in `pyproject.toml`, so
a CLI `-q` makes it `-qq` and suppresses the summary line the run is being read for. Give it at
least 25 minutes: the sweep runs the whole suite once per mutant, and a contended lock adds more.
**Never kill it** -- see the paragraph above for what a killed run leaves behind.

The last `git status --porcelain` must also print nothing. `report.py` and `perturbation_log.py`
regenerate committed pages, so a dirty tree after they run means a published document disagrees with
what its generator produces.

This list is the same set `.github/workflows/ci.yml` runs, in the same order, plus the two working
tree checks. Where they differ, CI is the authority and this file is the stale one.
