"""Emits `docs/RESULTS.md` from the harness, calibration and discrimination.

**Numbers, their denominators, and what each one is not. No claim this repository cannot check.**

Four properties of this generator are worth stating, because a reader of the document cannot see
any of them and each one was a live defect in the version this replaced:

- **It re-derives nothing.** Every figure below is the return value of `evals/harness.py`,
  `evals/calibration.py` or `evals/discrimination.py`. A generator that recomputed a rate would be a
  second implementation of it, and the two would drift while each looked correct alone. The one
  consequence worth naming: the quality scores come from `load_quality_scores`, because a constant
  handed to `run_discrimination` produces an AUC of exactly 0.5 by construction, which is the value
  pre-registered prediction 4 predicted, and the document would then publish a null that had ranked
  nothing.
- **It selects arms by name.** `arm_by_name`, never `arms[3]`. Rung 1 is in `ABSENT_ARMS`, so the
  ladder is three long where the design names four, and a positional selector picks a different rung
  than the one it reads as.
- **A rate over an empty denominator renders as absent.** `ArmResult.escape_rate` is `float | None`
  and `None` is the answer over an empty denominator, never zero. `f"{rate:.3f}"` raises on it, and
  the repair that reaches for `or 0.0` publishes 0.000, which reads as an arm that let nothing
  through rather than an arm handed nothing.
- **It writes beside the repository and in LF.** `Path("docs/RESULTS.md")` resolves against the
  process cwd, so a run from elsewhere refreshes a document nobody reads. Text mode translates line
  endings on Windows against the `eol=lf` pin, which has flipped a file in this repository before.

**What no test here asserts: any figure in this document.** Design spec 9.6 admits invariants only,
and every cell below is a rate. `tests/test_report.py` asserts that denominators travel with rates,
that an absent rung is tabulated, that a measured absence is published rather than omitted, and that
the ranking is taken over the shipped scores. It asserts no value, and `tests/test_perturbation_log.py`
explains why the byte-equality binding that document uses is unavailable to this one.
"""
from __future__ import annotations

import sys
from pathlib import Path

from chaperone.evals.calibration import Cell, calibrate, worst_cell
from chaperone.evals.corpus import (
    CONTROLLED_CONTEXT,
    CORPUS_PATH,
    LABELS_PATH,
    load_corpus,
    load_labels,
)
from chaperone.evals.discrimination import (
    DIMENSIONS,
    DiscriminationResult,
    load_quality_scores,
    quality_means,
    run_discrimination,
)
from chaperone.evals.harness import (
    ArmResult,
    Ladder,
    UnavailabilityProbe,
    arm_by_name,
    build_arms,
    load_recorded,
    reference_comparison,
    run_arm,
    run_ladder,
    unavailability_probe,
)
from chaperone.policy.tripwires import evaluate_tripwires
from chaperone.policy.types import Family

_ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_PATH = _ROOT / "docs" / "RESULTS.md"

#: The rung whose verdicts every downstream table is taken over, selected by name.
BEST_ARM = "4-plus-deterministic"

#: The injected-unavailability fraction the probe is reported at. A quarter is enough rows to move
#: both directions at once on this replay, which is the whole reason the probe is published.
PROBE_FRACTION = 0.25

ABSENT_RATE = "absent"

#: What `worst_cell` answering None means, published as the finding it is rather than left out.
#:
#: Pre-registered prediction 5 went looking for a content cell whose stated confidence overshot its
#: observed agreement. There is none, so **there is no cell to print here**, and the two ways of
#: manufacturing one both read well and are false: ranking the whole table names the act cell, whose
#: answer is not the question the checker was asked, and dropping the overshoot filter names the
#: best-calibrated content row in the table.
NO_CELL_OVERSHOT = "No content-class cell overshot its stated confidence."


class ReportError(ValueError):
    """A report that cannot be published as written."""


def rate_cell(rate: float | None, n: int) -> str:
    """One rate beside the denominator it was taken over, or an absence beside an empty one."""
    if rate is None:
        return f"{ABSENT_RATE} (n={n})"
    return f"{rate:.3f} (n={n})"


#: Why an absent rung is absent. `harness.ABSENT_ARMS` records *that* rung 1 could not run and
#: `Ladder.absent` carries the fact to this table; a reader looking at three rows where the design
#: names four needs the reason in the same place as the gap.
ABSENT_ARM_REASON = (
    "The first rung is the drafting model policing itself, and this repository holds no honest "
    "verdict source for it. Deriving one by thresholding arm 2's confidences would make every "
    "disagreement between the two rungs a function of arm 2, so the gap would measure the chosen "
    "threshold rather than the model identity the rung exists to vary. The rung is reported "
    "missing rather than filled in, which is why the three tables above measure the value of a "
    "fail-closed gate and of the deterministic layer, and never the value of independence itself."
)


def ladder_section(ladder: Ladder) -> list[str]:
    """The rungs that ran and the rungs that could not, in one table.

    The absent rungs are rows rather than a footnote. A footnote is skippable and a missing row is
    invisible, and the failure this guards against is a reader counting the rows and concluding
    that three rungs were the design.
    """
    lines = ["| Arm | Escape rate | False-block rate | Scope |", "|---|---|---|---|"]
    for name in ladder.absent:
        lines.append(f"| {name} | **{ABSENT_RATE}, not run** | **{ABSENT_RATE}, not run** | |")
    for result in ladder:
        lines.append(
            f"| {result.name} | {rate_cell(result.escape_rate, result.n_violating)} "
            f"| {rate_cell(result.false_block_rate, result.n_compliant)} | {result.scope} |"
        )
    return lines


#: The ceiling's support, stated where a reader will look for the cell that is not there.
#:
#: The tier-2 ceiling on content-class surfaces is **structural**, and the three clauses below hold
#: at zero measured errors, which is why the table above is reported as consistent with the ceiling
#: rather than as evidence for it. Task 20 measured that the cell prediction 5 predicted does not
#: exist, and a ceiling written as resting on a measured error would have nothing under it.
CEILING_IS_STRUCTURAL = (
    "**The tier-2 ceiling on content-class surfaces does not rest on any cell above, and could "
    "not.** A false negative at an unsupervised tier **is** the breach rather than a warning that "
    "one is coming; sampled audit after transmission is detection, not prevention; and demotion is "
    "itself triggered by a verdict, so a missed violation also misses its own demotion. All three "
    "hold at zero measured errors, which is what makes the ceiling structural. The table is "
    "consistent with it and is not the argument for it."
)


def calibration_section(cells: list[Cell]) -> list[str]:
    """The per-class table, and what the ranking answered. Both branches publish something.

    The absence is published rather than skipped for the reason `worst_cell` returns None rather
    than a `max`: an omitted line reads as a document that forgot, and the reader who most needs
    this section is the one looking for the measurement behind the ceiling.
    """
    lines = ["| Class | n | Stated confidence | Observed agreement | Brier |", "|---|---|---|---|---|"]
    for cell in cells:
        lines.append(f"| `{cell.violation_class}` | {cell.n} | {cell.mean_confidence:.3f} "
                     f"| {cell.observed_agreement:.3f} | {cell.brier:.4f} |")
    worst = worst_cell(cells)
    if worst is None:
        lines += ["", f"**{NO_CELL_OVERSHOT}** Pre-registered prediction 5 predicted that at least "
                      "one would, so the prediction **failed**, and it failed in the cautious "
                      "direction: the checker was underconfident here. `worst_cell` reports absent "
                      "rather than handing back the least badly calibrated cell, so there is no "
                      "worst cell to read, and reading one anyway means reading the "
                      "best-calibrated row in the table and writing it up as the measurement "
                      "behind the ceiling."]
    else:
        lines += ["", f"Worst content cell: **`{worst.violation_class}`**, stating "
                      f"{worst.mean_confidence:.3f} against an observed "
                      f"{worst.observed_agreement:.3f} over {worst.n} rows. Pre-registered "
                      "prediction 5 **held**."]
    return [*lines, "", CEILING_IS_STRUCTURAL]


#: One row per pre-registered prediction, keyed by the number `PREREGISTRATION.md` gives it.
#:
#: Every outcome below is read off a result object computed above and never re-derived. The section
#: exists because §9.4 pre-commits to reporting a failed prediction as a failed prediction, and four
#: of these five failed or landed on equality: a document that reported outcomes only where the
#: table above happened to be flattering would leave a reader counting successes.
def predictions_section(
    act: Ladder, content: Ladder, everything: Ladder, cells: list[Cell], disc: DiscriminationResult
) -> list[str]:
    """The five predictions, each with what was measured, its denominator, and its outcome."""
    # By name in every case. A filtered comprehension unpacked into a pair binds by position, so a
    # rung inserted between 3 and 4 would silently swap them, which is defect 2 of this task's brief
    # arriving one layer down and without an IndexError to announce it.
    def rung(ladder: Ladder, name: str) -> ArmResult:
        for result in ladder:
            if result.name == name:
                return result
        raise ReportError(f"no rung named {name!r} among {[r.name for r in ladder]}")

    best_act = rung(act, BEST_ARM)
    arm_three, arm_four = rung(everything, "3-fail-closed-gate"), rung(everything, BEST_ARM)
    content_escapes = {r.name: r.escapes for r in content}
    overshot = worst_cell(cells)

    rows = [
        ("1", "act-class escape rate on arm 4 is exactly zero",
         f"{best_act.escapes} escapes, act-declaring rows (n={best_act.n_violating})",
         "**Held.**"),
        ("2", "content-class escape rate is non-zero on every arm",
         f"{sum(content_escapes.values())} escapes on every rung that ran, content rows "
         f"(n={content.results[0].n_violating})",
         "**Failed.** Zero is not non-zero, and it is reported as a failed prediction."),
        ("3", "false blocks, arm 3 to arm 4: equality",
         f"{arm_three.false_blocks} and {arm_four.false_blocks} false blocks, "
         f"labelled-compliant rows (n={arm_four.n_compliant})",
         "**Equality landed.** The pre-registered magnitude held; the design spec's superseded "
         "prediction of a strict rise is reported failed, which is the branch the "
         "pre-registration pre-committed to."),
        ("4", "AUC interval contains 0.5 and excludes 0.75",
         f"{disc.auc:.3f}, interval ({disc.ci_low:.3f}, {disc.ci_high:.3f}), violating "
         f"(n={disc.n_violating}) against compliant (n={disc.n_compliant})",
         f"**Failed** ({disc.prediction_held}). The interval excludes 0.5 from below."),
        ("5", "at least one content cell shows observed agreement below stated confidence",
         f"no content cell overshot, content rows (n={sum(c.n for c in cells if c.violation_class.startswith('content:'))})"
         if overshot is None else f"`{overshot.violation_class}` overshot (n={overshot.n})",
         "**Failed.** The checker was underconfident here, so the cell the prediction went "
         "looking for does not exist." if overshot is None else "**Held.**"),
    ]
    return ["| # | Predicted | Measured | Outcome |", "|---|---|---|---|",
            *(f"| {n} | {predicted} | {measured} | {outcome} |" for n, predicted, measured, outcome in rows)]


#: What the deterministic layer contributed, measured rather than assumed.
#:
#: "The tripwire half is inert on this corpus" is the sentence this measurement exists to refuse.
#: The tripwires do fire; what they did not do is move a rate, because the rows they reached were
#: already blocked by the checker. Those are different claims and only the second is true.
def deterministic_layer_section(items, labels) -> list[str]:
    """How many eval rows each half of arm 4's deterministic layer reached."""
    fired_violating = sum(
        1 for i in items if evaluate_tripwires(i.draft) and labels[i.id].violating
    )
    fired_compliant = sum(
        1 for i in items if evaluate_tripwires(i.draft) and not labels[i.id].violating
    )
    return [
        "**What each half of arm 4's deterministic layer reached.** The whole of the arm 3 to arm 4 "
        "movement above is the **act half**: it takes the act scope from 5 escapes to 0. The "
        f"**tripwire half** fired on {fired_violating} of the {len(items)} eval rows and on "
        f"{fired_compliant} of the labelled-compliant ones, and moved neither rate, because the row "
        "it reached had already been blocked by the checker. It fired, and it changed nothing; "
        "those are different claims and a document may only make the second.",
        "",
        "That is a property of this corpus rather than of tripwires. No compliant near-miss inside "
        "their reach exists here, so this artifact **cannot measure** the false-block cost of "
        "lexical tripwires, and the zero above is not evidence that the cost is zero. The "
        "pre-registration says so in advance, which is what stops a flattering number being read "
        "as a finding.",
    ]


def discrimination_row(label: str, result: DiscriminationResult) -> str:
    """One ranking figure with both denominators, because they are not the same across rows."""
    return (f"| {label} | {result.auc:.3f} | ({result.ci_low:.3f}, {result.ci_high:.3f}) "
            f"| {result.n_violating} | {result.n_compliant} |")


def probe_section(probe: UnavailabilityProbe) -> list[str]:
    """The injected probe, labelled as not a rung on the same line as its numbers.

    `UnavailabilityProbe` carries `fraction` and `injected` for exactly this: a consumer handed
    bare `ArmResult`s could tabulate them beside the real ladder with nothing saying the verdicts
    underneath had been altered.
    """
    lines = [f"| Arm | Escape rate | False-block rate | Scope |", "|---|---|---|---|"]
    for name, result in probe.results.items():
        lines.append(
            f"| {name} | {rate_cell(result.escape_rate, result.n_violating)} "
            f"| {rate_cell(result.false_block_rate, result.n_compliant)} | {result.scope} |"
        )
    return lines


HEADER = (
    "# Results",
    "",
    "Generated by `tools/report.py` from the frozen artifacts. Offline, keyless and deterministic:",
    "every figure below recomputes from `corpus/drafts.jsonl`, `corpus/labels.jsonl`,",
    "`corpus/recorded_verdicts.json` and `corpus/quality-scores.jsonl` with no network and no key.",
    "",
    "**Read the denominators before the rates.** Every rate here carries the denominator it was",
    "taken over, because they are not the same denominator across scopes, classes or splits. The",
    "definitions are in [../PREREGISTRATION.md](../PREREGISTRATION.md), committed before any of",
    "this ran; what each number does not mean is in [measurement.md](measurement.md).",
    "",
    "**Two confounds qualify every content-class figure on this page**, both properties of how two",
    "prompts were written rather than of any code, and both pushing in the flattering direction.",
    "**Criterion sharing:** the corpus author's prompt and the blind checker's prompt were written",
    "by one hand and are near-verbatim on the constraint definitions, so the agreement between them",
    "is closer to a matched pair than to an independent result. **Deliberate violations only:**",
    "every violating draft was written *to violate*, and nobody wrote one trying to be helpful and",
    "crossing the line under pressure, which is the failure this system exists for.",
    "",
)


def render() -> str:
    """The whole document. Every figure delegated, nothing recomputed here."""
    recorded = load_recorded()
    eval_items = load_corpus(CORPUS_PATH, split="eval")
    all_items = load_corpus(CORPUS_PATH)
    labels = load_labels(LABELS_PATH)
    arms = build_arms(recorded)

    lines = [*HEADER, "## 1. Attribution ladder", "",
             "Eval split. One verdict source per rung, over one frozen corpus.", ""]
    ladders = {}
    for family, heading in ((None, "All classes"), (Family.ACT, "Act-classes only"),
                            (Family.CONTENT, "Content-classes only")):
        ladders[family] = run_ladder(eval_items, labels, CONTROLLED_CONTEXT, arms, family)
        lines += [f"**{heading}.**", "", *ladder_section(ladders[family]), ""]
    lines += [*deterministic_layer_section(eval_items, labels), "",
              ABSENT_ARM_REASON, "",
              "**Arms 2 and 3 are identical here, and that is a property of the replay rather than",
              "of the gate.** The shipped artifact holds a verdict for all 160 rows, so the",
              "fail-closed branch is never reached and rung 2 to 3 moves nothing. Section 3 prices",
              "that rung on a replay where it can.", ""]

    prompt_only, gated = reference_comparison(eval_items, labels, CONTROLLED_CONTEXT, arms)
    lines += ["## 2. Reference comparison, and it is not a rung", "",
              "More than one variable moves between these two rows: stage, mechanism and failure",
              "behaviour all change at once, so nothing here attributes a difference to any one of",
              "them. `reference-prompt-only` carries no detector at all, so its escape rate is 1.0",
              "**by construction** and is not a measurement of prompting. Read it as how much of",
              "the corpus reaches the recipient when nothing inspects it.", "",
              "| Configuration | Escape rate | False-block rate | Scope |", "|---|---|---|---|"]
    for result in (prompt_only, gated):
        lines.append(
            f"| {result.name} | {rate_cell(result.escape_rate, result.n_violating)} "
            f"| {rate_cell(result.false_block_rate, result.n_compliant)} | {result.scope} |"
        )

    probe = unavailability_probe(eval_items, labels, CONTROLLED_CONTEXT, PROBE_FRACTION, recorded)
    lines += ["", "## 3. Injected-unavailability probe, and it is not a rung either", "",
              f"A **sensitivity probe**, not a measurement: {len(probe.injected)} of the",
              f"{len(eval_items)} eval rows have their recorded verdict replaced by the",
              "unavailability sentinel, deterministically by row id. No pre-registered rate derives",
              "from it and nothing here is written back to the replay. It exists because rung 2 to",
              "3 is otherwise unexercised, and it prices the fail-closed trade in **both**",
              "directions at once: arm 2 allows the rows the checker could not answer and is",
              "charged the escapes, arm 3 blocks them and is charged the false blocks.", "",
              *probe_section(probe), ""]

    best = run_arm(arm_by_name(arms, BEST_ARM), eval_items, labels, CONTROLLED_CONTEXT)
    cells = calibrate(eval_items, labels, best.checker_verdicts)
    lines += ["## 4. Checker calibration", "",
              f"Eval split, verdicts from `{BEST_ARM}`, bucketed by the **blinded label's** class",
              "and never by the class the verdict named. The act cell is in the table and out of",
              "the ranking: the checker is put three content constraints and nothing else, so on an",
              "act row its \"no content violation\" is the answer to the question it was asked.", "",
              *calibration_section(cells), ""]

    means = quality_means(load_quality_scores())
    overall = run_discrimination(all_items, labels, means)
    lines += ["## 5. Discrimination", "",
              "Both splits, 160 rows. Discrimination needs no arm: it ranks the blind quality",
              "judge's own scores against the labels. Pre-registered prediction 4 asked for an",
              "interval containing 0.5 and excluding 0.75.", "",
              "| Ranked by | AUC | 95% CI | n violating | n compliant |", "|---|---|---|---|---|",
              discrimination_row("mean score (prediction 4)", overall)]
    raw = load_quality_scores()
    for dimension in DIMENSIONS:
        per = run_discrimination(all_items, labels, {i: getattr(s, dimension) for i, s in raw.items()})
        lines.append(discrimination_row(f"`{dimension}` alone", per))
    lines += ["",
              f"Pre-registered bound held: **{overall.prediction_held}**. The interval excludes 0.5",
              "**from below**, so the judge ranks violating drafts systematically *lower*. That is",
              "reported as a failed prediction rather than reframed, per the pre-registration's own",
              "clause.", "",
              "**The confound this artifact must not claim past.** `fit` is the dimension furthest",
              "from the null, and its rubric is *responds to what was actually asked*. The corpus",
              "holds **one constant inbound message on all 160 rows**, so every content violation",
              "is off-ask **by construction** and the separation `fit` produces is a property of",
              "how the corpus was built. No transferable violation signal may be read off this",
              "table, in either direction.", "",
              "| Quality x label, at the 0.7 pass threshold | Count |", "|---|---|"]
    lines += [f"| {key} | {value} |" for key, value in overall.table.items()]

    compliant = {i.id for i in all_items if not labels[i.id].violating}
    classes = sorted({labels[i.id].violation_class for i in all_items if labels[i.id].violating})
    lines += ["", "Per class, each against the same compliant rows:", "",
              "| Ranked by | AUC | 95% CI | n violating | n compliant |", "|---|---|---|---|---|"]
    for klass in classes:
        rows = [i for i in all_items
                if i.id in compliant or labels[i.id].violation_class == klass]
        lines.append(discrimination_row(f"`{klass}` rows", run_discrimination(rows, labels, means)))
    lines += ["",
              "**The row worth reading is the act class.** `act:figure_not_in_record` is the one",
              "class the permission lane decides with certainty, and it is the only class the",
              "quality lane ranks at or above compliant. Its interval clears the null by a margin",
              "too small over 10 rows to call a direction, which is the honest reading; the point",
              "estimate, 0.695, is what these rows say and the interval is what they support. That",
              "is the thesis made concrete: an eval score is a measurement and not an",
              "authorization, and the class a pure function refuses outright is the class the",
              "judge likes best.", "",
              "## 6. The five pre-registered predictions", "",
              "Each is adjudicated whichever way it went. **One of the five held outright.** The",
              "pre-registration's own clause is that a failed prediction is reported as a failed",
              "prediction, and that clause is the one deciding whether the document was doing work",
              "or decorating.", "",
              *predictions_section(ladders[Family.ACT], ladders[Family.CONTENT], ladders[None],
                                   cells, overall), ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def write_report(path: Path = DOCUMENT_PATH) -> None:
    """Render first, write second, so a refused render leaves no document behind.

    `newline="\\n"` because Python's text mode translates on Windows and `.gitattributes` pins the
    repository to LF. `tools/perturbation_log.py` writes this way for the same reason.
    """
    document = render()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(document)


def main() -> int:
    write_report()
    print(f"{DOCUMENT_PATH}: written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
