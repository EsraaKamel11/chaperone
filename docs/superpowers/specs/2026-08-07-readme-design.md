# README and docs design

**Design document. Status: designed, not written.** This spec governs `README.md`, four `docs/` pages,
and the rewritten guard test. It **supersedes the README instructions in
`docs/superpowers/plans/2026-08-05-chaperone.md` Task 26 Steps 1 and 2**, which mandate content the
tree cannot back (the matching ablation, "smallest production v1", a demo calling `refine()`). Drafting
from Task 26 would produce a README claiming three subsystems that do not exist.

**The reader, in order.** An engineer at the target audience who volunteered roughly twenty minutes and
arrives holding four specific claims from a message: the enforcement spine is built and green; the
quality gate and the permission gate never share a mechanism; an eval score is not an authorization;
the policy layer cannot import an LLM client. Then a deeper senior reader. Then strangers. Every
structural choice below optimizes for the first reader without lying to the third.

---

## 1. Fixed constraints

1. **No organisation is named.** The survive-wrongness test applies: strip the context and a coherent
   general pattern remains; restore one paragraph and it is obviously aimed. Failure modes are written
   as failure modes of *the class of system* — an agent carrying high-stakes outbound conversations —
   never of any firm.
2. **Em-dash-free**, in the README and every docs page. Client-facing artifact rule. The lint is
   scoped to the new files only; `PREREGISTRATION.md` predates the rule and is not rewritten.
3. **Every claim true at push time**, mechanically guarded (§5). The push happens after calibration
   and discrimination land. Matching, refinement, ladder mechanics, arm 1, crash-recovery branches
   and the thread-scope pass remain designed, and the README says so in a table rather than implying
   otherwise by silence.
4. **The empty `matching/` package is deleted before push.** An empty package with no README mention
   is an overclaim by directory structure. The Designed-vs-Built row carries the design.
5. **Numbers travel with denominators.** "3/50 eval", never "6%". A rate never travels without its
   denominator, matching the suite's own convention.
6. **Prediction outcomes are reported held-or-failed** per `PREREGISTRATION.md`, whatever they turn
   out to be. No prose is drafted that assumes a prediction held.

## 2. The claim vocabulary

Every failure-mode entry and every capability statement carries exactly one of five labels:

| Label | May be used when |
|---|---|
| **zero by construction** | act-class rows only, enforced by the static audit and the CLAUDE.md rule |
| **structural invariant, tested** | the property is enforced by construction (prompt omission, grants) and a named test attacks it |
| **measured** | a number exists, with its denominator, and the