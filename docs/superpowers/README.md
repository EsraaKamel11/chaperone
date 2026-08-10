# Process records, dated

Everything under `specs/` and `plans/` is a dated working document: the design or plan as it stood
on the day in its filename, kept as the record of how this repository was built and reviewed. These
pages are not maintained against the tree afterwards. Where one has been overtaken by later work,
it carries a dated annotation or a superseded header rather than a rewrite, because a process
record that is quietly edited to match the outcome stops being a record.

The pages maintained against the tree are the reader-facing ones: the root `README.md` and the
markdown pages directly under `docs/`. Those are held to the tree by `tests/test_readme_claims.py`
and its sibling guards; the documents here are held only to their dates.

Reading order, if the process is what you came for: `specs/2026-08-05-chaperone-design.md` is the
original design, `plans/2026-08-05-chaperone.md` its build plan,
`specs/2026-08-07-readme-design.md` the reader-layer design (since superseded, and marked so),
`specs/2026-08-09-stack-binding-design.md` the stack-binding design with its amendment ledger, and
`specs/2026-08-09-framework-audit.md` the bind-or-keep audit of what deliberately stayed
hand-rolled.
