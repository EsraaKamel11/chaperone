"""The reader-facing layer is held to the tree, not to the author's memory.

The README and the `docs/` pages promise, for each failure mode, "the named test that attacks it".
A named test that does not exist turns that promise into decoration, and nothing in an ordinary suite
would notice. So every backticked test name and every source path in those files is resolved here,
and the pasted demo output is compared byte for byte against a fresh run.

**Why this file and not a review checklist.** The claims in those documents go stale in exactly one
direction: the tree moves and the prose does not. A reviewer catches that on the day they look. CI
catches it on the commit that causes it.
"""

from __future__ import annotations

import ast
import hashlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"
DOCS = sorted((REPO / "docs").glob("*.md"))
READER_FACING = [README, *DOCS]
RESULTS_PAGE = REPO / "docs" / "RESULTS.md"

TEST_NAME = re.compile(r"`(test_[a-z0-9_]+)`")
SOURCE_PATH = re.compile(r"`((?:src|tests|tools|demo|corpus)/[A-Za-z0-9_./]+)`")


def _reader_facing_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in READER_FACING)


def _unwrapped(text: str) -> str:
    """Collapse whitespace so a phrase match survives a markdown line wrap.

    Without this, every prose assertion below is a hostage to where the paragraph happens to break,
    and reflowing a sentence turns a green suite red for no reason a reader would recognise.
    """
    return re.sub(r"\s+", " ", text.lower())


def _tracked(*patterns: str) -> list[Path]:
    """The files git tracks under `patterns`, or `[]` where git cannot answer.

    **Tracked is what "would publish" means**, and it is the rule the walk this replaces was
    reaching for and could not state: it excluded six directory *names*, so a nested worktree or a
    `.pytest_cache/README.md` in the checkout silently joined the denominator.

    An empty list is returned rather than a fallback walk, and it is not silent: `git` absent, or a
    tarball with no `.git`, makes every parametrized guard below collect zero cases, which reads
    exactly like a clean pass. `test_the_published_file_enumeration_is_not_silently_empty` is what
    turns that into a red build, following `tools/static_audit.py`'s "audited nothing" rather than
    a `pytest.skip` a reader would mistake for a pass.
    """
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z", "--", *patterns],
            cwd=REPO, capture_output=True, check=False,
        )
    except OSError:
        return []
    if completed.returncode != 0:
        return []
    return sorted(REPO / name for name in completed.stdout.decode("utf-8").split("\0") if name)


def _all_markdown() -> list[Path]:
    """Every markdown file that would publish, not only the reader-facing five.

    `docs/superpowers/` ships with this repository, so a guard scoped to `docs/*.md` would leave the
    design documents unchecked while claiming to cover the repository.
    """
    return _tracked("*.md")


def test_the_published_file_enumeration_is_not_silently_empty():
    """Every parametrized guard below is a no-op over an empty enumeration.

    `git ls-files` returning nothing -- no git on PATH, an exported tarball, a renamed root -- would
    otherwise collect zero cases and report green, which is this repository's most-met failure shape.
    The dependency on git is real and is stated here in executable form rather than in a comment.
    """
    markdown = _all_markdown()
    assert markdown, (
        "no tracked markdown was enumerated; `git ls-files` answered nothing, so every "
        "published-file guard in this module would pass over an empty set"
    )
    assert README in markdown, "the README is not among the tracked markdown files"


def test_an_untracked_markdown_file_is_not_scanned_as_published():
    """"Would publish" means tracked by git, and a filesystem walk cannot express that.

    The walk this replaces excluded six directory *names*, so its denominator moved with whatever
    happened to be sitting in the checkout: a nested worktree and a `.pytest_cache/README.md` took
    it from 14 files to 29, and the parametrized guards below reported a different test count on
    the two machines. A completeness guard whose denominator depends on the machine is a guard that
    can be argued with.

    Asserted as an effect on a real untracked file rather than by comparing the enumeration against
    `git ls-files` a second time, which would compare the code against itself.
    """
    probe = REPO / ".chaperone-untracked-probe.md"
    probe.write_text("untracked scratch\n", encoding="utf-8")
    try:
        walked = [p for p in REPO.rglob("*.md")]
        assert probe in walked, "the probe was not written where a filesystem walk would find it"
        assert probe not in _all_markdown(), (
            "an untracked file is enumerated as published, so the denominator is whatever the "
            "checkout happens to contain"
        )
    finally:
        probe.unlink()


def _defined_test_names() -> set[str]:
    names: set[str] = set()
    for path in (REPO / "tests").rglob("test_*.py"):
        names.update(re.findall(r"^\s*def (test_[a-z0-9_]+)", path.read_text(encoding="utf-8"), re.M))
    return names


def test_the_docs_directory_is_not_silently_empty():
    """Every other test here passes vacuously if the pages are missing.

    `RESULTS.md` is named separately because one guard below is conditional on it.
    `test_no_reader_facing_page_denies_the_results_this_tree_publishes` asks whether a page denies
    results *that exist*, so deleting the page makes every denial true again and silences it. That
    is the fail-open shape this repository has met six times, and it is closed here rather than by
    citing the link checker, because the edit that deletes a page plausibly deletes the link to it
    in the same commit.
    """
    assert README.exists(), "README.md is missing"
    assert len(DOCS) >= 4, f"expected the four docs pages, found {[p.name for p in DOCS]}"
    assert RESULTS_PAGE.exists(), (
        "docs/RESULTS.md is missing; regenerate it with `python tools/report.py`"
    )


@pytest.mark.parametrize("path", READER_FACING, ids=lambda p: p.name)
def test_every_test_name_cited_in_the_reader_facing_docs_exists(path: Path):
    defined = _defined_test_names()
    cited = set(TEST_NAME.findall(path.read_text(encoding="utf-8")))
    missing = sorted(cited - defined)
    assert not missing, f"{path.name} cites tests that do not exist: {missing}"


@pytest.mark.parametrize("path", READER_FACING, ids=lambda p: p.name)
def test_every_source_path_cited_in_the_reader_facing_docs_exists(path: Path):
    cited = set(SOURCE_PATH.findall(path.read_text(encoding="utf-8")))
    missing = sorted(p for p in cited if not (REPO / p).exists())
    assert not missing, f"{path.name} cites paths that do not exist: {missing}"


@pytest.mark.parametrize("path", READER_FACING, ids=lambda p: p.name)
def test_every_relative_link_in_the_reader_facing_docs_resolves(path: Path):
    links = re.findall(r"\]\((?!https?:)([^)#]+)", path.read_text(encoding="utf-8"))
    broken = sorted(t for t in links if not (path.parent / t).resolve().exists())
    assert not broken, f"{path.name} has broken links: {broken}"


def test_the_demo_output_pasted_in_the_readme_matches_a_fresh_run():
    """A stale paste resolves every name it cites and still tells the reader something false.

    Name resolution cannot catch this: the demo could change its verdict, its category or its entry
    count and every backticked name in the README would still resolve. This is the only mechanism
    that keeps a verbatim paste verbatim, and it closes the half of the gap `demo/day2.py` records
    in its own docstring, where CI guards the headline invariant and nothing guards the printed text.
    """
    fenced = re.findall(r"```\n(QUALITY LANE.*?)```", README.read_text(encoding="utf-8"), re.S)
    assert len(fenced) == 1, "expected exactly one pasted demo transcript in the README"

    completed = subprocess.run(
        [sys.executable, str(REPO / "demo" / "day2.py")],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=True,
    )
    assert completed.stdout.strip() == fenced[0].strip(), (
        "the README's pasted demo output no longer matches a fresh run of demo/day2.py"
    )


#: How close the phrase and a content-class name have to sit before this reads as one claim about
#: the other. Wide enough to survive a markdown wrap or a docstring reflow; narrow enough that a
#: file mentioning both several paragraphs apart is not accused of anything.
CLAIM_WINDOW = 120


def zero_by_construction_beside_a_content_class(text: str) -> list[str]:
    """The excerpts where "zero by construction" sits within `CLAIM_WINDOW` of a content class.

    Whitespace-collapsed first, because the check it replaces was per-line while `_unwrapped`
    already existed in this module and six other assertions used it: the same sentence wrapped
    across a line break evaded a guard the file below it did not.
    """
    flat = _unwrapped(text)
    hits = []
    for match in re.finditer("zero by construction", flat):
        window = flat[max(0, match.start() - CLAIM_WINDOW): match.end() + CLAIM_WINDOW]
        if "content:" in window:
            hits.append(window)
    return hits


#: Everything the rule applies to. `CLAUDE.md` says the phrase is forbidden outside act-classes
#: "in code, comments, docstrings, or documentation", and the guard read the six reader-facing
#: pages only -- so adding *"content:negotiates_terms is zero by construction"* to any docstring
#: under `src/` broke the repository's one non-negotiable and broke no test. The phrase already
#: appears in `src/chaperone/gates/engine.py` and `tools/coverage_map.py`, both correctly, and
#: nothing read either.
CLAIM_SCOPE = [
    *READER_FACING,
    *[p for p in _tracked("src/*.py", "tools/*.py", "demo/*.py") if p.suffix == ".py"],
]


def test_the_claim_scan_reads_the_source_tree_and_not_only_the_pages():
    """The scope is the guard, so the scope is asserted rather than assumed."""
    scanned = {p.suffix for p in CLAIM_SCOPE}
    assert scanned == {".md", ".py"}, f"the claim scope covers only {scanned}"
    assert any(p.name == "engine.py" for p in CLAIM_SCOPE), (
        "src/chaperone/gates/engine.py carries the phrase and is not in scope"
    )
    assert any(p.name == "coverage_map.py" for p in CLAIM_SCOPE), (
        "tools/coverage_map.py carries the phrase and is not in scope"
    )


def test_a_wrapped_claim_beside_a_content_class_is_detected():
    """The detector itself, on text this repository does not contain.

    A tree scan alone cannot show that the rule catches anything: it is green on a clean tree and
    green on a broken detector. These are the two shapes that got past the version this replaces --
    a line break between the phrase and the class, and the phrase inside a docstring.
    """
    assert zero_by_construction_beside_a_content_class(
        '"""Whether the draft negotiates terms.\n\ncontent:negotiates_terms is zero by\n'
        'construction here, decided by a pure function.\n"""'
    )
    assert zero_by_construction_beside_a_content_class(
        "act:send_cap_exceeded is zero by construction. So is content:advises_on_merits."
    )
    assert zero_by_construction_beside_a_content_class(
        "The claim is zero by construction.\n" + "filler line\n" * 40 + "content:negotiates_terms\n"
    ) == [], "the window is not bounded, so any file naming both is accused"
    assert zero_by_construction_beside_a_content_class(
        "act:tool_outside_grant is zero by construction, decided by a pure function."
    ) == []


@pytest.mark.parametrize("path", CLAIM_SCOPE, ids=lambda p: str(p.relative_to(REPO)))
def test_zero_by_construction_is_never_claimed_beside_a_content_class(path: Path):
    """CLAUDE.md forbids the phrase outside act-classes, in code as much as in documentation.

    Guarding the README alone would leave five of six reader-facing files unguarded, which is where
    the claim is most likely to drift: a docs page has room to explain, and explaining is where an
    act-class guarantee gets generalised into a sentence about the whole gate. Guarding the pages
    alone left every docstring in the tree unguarded, which is the other half of the same rule.
    """
    hits = zero_by_construction_beside_a_content_class(path.read_text(encoding="utf-8"))
    assert not hits, (
        f"{path.relative_to(REPO)} claims zero by construction beside a content class: {hits[0]!r}"
    )


# SHA-256 of lowercased organisation tokens that must never appear. Digests rather than words, so
# the guard does not publish the thing it forbids.
FORBIDDEN_TOKEN_DIGESTS = frozenset({
    "9c47cc0516ad9697ab008e9cc8c8d4809b8a70ba4772498d272c60c8fca97fcc",
    "99c1b86d9af149787a785566c258daa2cd6e5e302943e23ed46221f3927e5731",
    "5179b248d77e94d5ad9128646c23481d37bbf2ac7e7df39783b434c85500aeb2",
    "517e3b1717b976866f79cb524f0609bb839cb8651fea31c08e4260547569904b",
    "5c098a6b741ddb9973291fcbbc8110c0561859ec626171d82ff889f9328e9387",
    "ce8ee03c7e11715305d0577b47e84163161dbdf9f78dc153a1fdc0cea2c31194",
    "1bac97bea50395419d7d4b78d0508c58859ba189665d7c36b76c59b5f8e36967",
    "03445216968ae994a46c8afd64d0f7c87e3fb37f78ac31269540ae4bda02c47e",
    "3a4ad37e305ff3bb775fb38a93345aa1f29961fcf88e9415a093d9e6eec8c65a",
    "927a3aed189d610b2e151c4208913b3ed0cb38f6be613756819b1513c8924d7f",
})


#: The longest organisation name this can recognise, in words. Single-token hashing could never
#: match a multi-word name at all, so a two-word firm was outside the guard by construction while
#: the guard's own docstring claimed the repository.
MAX_NAME_WORDS = 3


def forbidden_hits(text: str, digests: frozenset[str] = FORBIDDEN_TOKEN_DIGESTS) -> list[str]:
    """The digests of any forbidden name appearing in `text`, as words rather than as characters.

    Normalised to lowercase alphanumeric runs joined by single spaces, then hashed at every length
    up to `MAX_NAME_WORDS`. Normalising is what makes punctuation, casing and a line break between
    the words irrelevant, so "Acme  Holdings", "ACME-Holdings" and a name wrapped across two lines
    all reduce to the same string before hashing.

    Digests are returned, never the matching text. A guard that printed the forbidden word on
    failure would publish it into every CI log, which is the thing it was written to prevent.
    """
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    hits = []
    for length in range(1, MAX_NAME_WORDS + 1):
        for start in range(len(tokens) - length + 1):
            digest = hashlib.sha256(" ".join(tokens[start:start + length]).encode()).hexdigest()
            if digest in digests:
                hits.append(digest)
    return hits


def test_a_multi_word_organisation_name_is_recognised_however_it_is_spelled():
    """The detector, driven on names this repository does not contain.

    Synthetic digests rather than the committed ones, because the committed list must never be
    reversible from the suite -- and because a scan over a clean tree is equally green with a
    working detector and a broken one. The multi-word case had **no** killer and could not have
    had one: the guard hashed single `[A-Za-z0-9]+` tokens, so a two-word name was unmatchable.
    """
    two_words = hashlib.sha256(b"northwind partners").hexdigest()
    one_word = hashlib.sha256(b"northwind").hexdigest()
    digests = frozenset({two_words})

    assert forbidden_hits("Advised by Northwind Partners in 2024.", digests) == [two_words]
    assert forbidden_hits("Advised by NORTHWIND   partners.", digests) == [two_words]
    assert forbidden_hits("Advised by Northwind\nPartners.", digests) == [two_words]
    assert forbidden_hits("Northwind-Partners advised.", digests) == [two_words]
    assert forbidden_hits("Northwind advised. Partners followed.", digests) == [], (
        "two words in separate sentences are not the name; normalisation must not join them"
    )
    assert forbidden_hits("Northwind advised.", digests) == []
    assert forbidden_hits("Northwind advised.", frozenset({one_word})) == [one_word]


#: Everything git tracks that is worth reading as text. `CLAUDE.md` says *anywhere in this
#: repository*, and the guard read `*.md` only -- so `corpus/drafts.jsonl` and
#: `corpus/blind-drafts.jsonl`, 160 synthetic messages about deals and rounds and the likeliest
#: place a real firm name would land, were never scanned, and neither were `src/`, `tests/` or
#: `tools/`.
COMMITTED_TEXT = _tracked("*.md", "*.py", "*.jsonl", "*.json", "*.yml", "*.toml")


@pytest.mark.parametrize("path", COMMITTED_TEXT, ids=lambda p: str(p.relative_to(REPO)))
def test_no_organisation_is_named_anywhere_this_repository_commits(path: Path):
    """CLAUDE.md: no organisation name appears anywhere in this repository.

    Scoped to every tracked text file rather than to the published markdown, because the rule is
    about the repository and a guard narrower than its own claim is the exact shape of overclaim
    this suite exists to catch.

    **This is the whole of the enforcement.** `tools/guard_edit.py` also checks, at edit time, but
    only when `CHAPERONE_FORBIDDEN_TOKENS` is set in the environment, and the shipped configuration
    sets it nowhere. `tests/test_guard_edit.py` exhibits that unarmed state as a limit rather than
    leaving it to be inferred. Setting the variable in a committed file would put the forbidden
    names *into* the repository, which is why the digests are committed and the words are not.
    """
    assert COMMITTED_TEXT, "no tracked text files were enumerated, so this guard scans nothing"
    hits = forbidden_hits(path.read_text(encoding="utf-8", errors="replace"))
    assert not hits, f"a forbidden organisation name appears in {path.relative_to(REPO)}"


SOURCE_ROOT = REPO / "src" / "chaperone"
TESTS_ROOT = REPO / "tests"
RATIO_CLAIM = re.compile(r"(\d+) lines of tests? for every line of source")


def _python_lines(root: Path) -> int:
    return sum(len(p.read_text(encoding="utf-8").splitlines()) for p in root.rglob("*.py"))


def test_the_readmes_test_to_source_ratio_holds_in_the_tree():
    """A hand-maintained line count drifts by construction, so the README states a ratio and this
    recomputes it.

    The absolute counts were stale in the underselling direction when this was written, which is the
    harmless direction and is exactly why nobody noticed. A ratio is the claim actually being made
    and it survives ordinary commits, so binding it costs no red builds while still failing on the
    day the suite stops outweighing the source.
    """
    stated = RATIO_CLAIM.findall(README.read_text(encoding="utf-8"))
    assert len(stated) == 1, f"expected exactly one test-to-source ratio claim in the README, found {stated}"

    source, tests = _python_lines(SOURCE_ROOT), _python_lines(TESTS_ROOT)
    assert source, "no source lines were counted, so this guard would pass vacuously"
    assert round(tests / source) == int(stated[0]), (
        f"the README claims {stated[0]} lines of tests per line of source; "
        f"the tree measures {tests} / {source} = {tests / source:.2f}"
    )


def test_the_readme_declares_the_scenario_synthetic():
    """A reader must not have to reach the docs to learn the data is not real."""
    opening = "\n".join(README.read_text(encoding="utf-8").splitlines()[:40]).lower()
    assert "synthetic" in opening, "the README's opening does not declare the scenario synthetic"


def test_the_readme_states_the_domain_the_constraints_come_from():
    """Stripped of the domain, the three content classes read as arbitrary choices.

    `content:advises_on_merits` is only legible once the reader knows the firm published that it does
    not advise on the merits. This guards the abstraction going too far, which is the opposite
    failure to naming an organisation and just as real.
    """
    text = _unwrapped(README.read_text(encoding="utf-8"))
    assert "capital-introduction" in text, "the README no longer names the scenario domain"
    for published in ("advise on the merits", "negotiate terms", "forward-looking return"):
        assert published in text, f"the README no longer states the published constraint: {published}"


def test_the_tier_two_ceiling_on_content_classes_is_stated():
    """The ceiling is the honest price of a model-based detector, and dropping it would leave the
    artifact claiming an autonomy it explicitly refuses."""
    text = _unwrapped(_reader_facing_text())
    assert "tier 2" in text, "the tier-2 ceiling is no longer stated"
    assert "detection, not prevention" in text, "the reason for the ceiling is no longer stated"


def test_the_cross_turn_residual_is_named_as_a_limit():
    """The one failure the per-draft gate does not catch. A limits section that omits it overclaims
    the scope of every measurement above it."""
    text = _unwrapped(_reader_facing_text())
    assert "cross-turn" in text, "the cross-turn residual is no longer named"


#: The modules whose designed-vs-built row can go stale, mapped to the text of their table row.
#:
#: **The registry is the guard's blind spot, and it had one.** This map covered five subsystems and
#: the table listed more, so the row reading "Matching: **Designed, not built**" stood while
#: `src/chaperone/matching/ablation.py` shipped and `tools/perturbation_log.py` imported its
#: sibling. The test below was correct and was asked the wrong question, which is the failure its
#: own docstring predicts one paragraph up. Every row whose subsystem has a module under `src/` is
#: entered here; a row naming no module cannot be checked this way and is checked by reading.
DESIGNED_VS_BUILT = {
    "src/chaperone/evals/calibration.py": "Checker calibration",
    "src/chaperone/evals/discrimination.py": "Discrimination",
    "src/chaperone/audit/recovery.py": "Crash-recovery",
    "src/chaperone/gates/refine.py": "Refinement loop",
    "src/chaperone/gates/ladder.py": "Capability ladder",
    "src/chaperone/matching/ablation.py": "Matching: shared eligibility predicates",
    "src/chaperone/policy/tripwires.py": "Lexical tripwires as a second disjunct",
    "src/chaperone/gates/hook.py": "Executor chokepoint",
    "src/chaperone/audit/chain.py": "Hash-linked audit",
}


def test_the_designed_versus_built_table_agrees_with_the_tree_in_both_directions():
    """The designed-vs-built table is the one place a stale row is most expensive.

    **This used to assert only that each module was still absent**, so building one failed the test
    and the table got corrected, which is the right direction of failure. But it fires exactly once:
    the entry then leaves the registry and nothing checks the row it just caused to be written. A
    row corrected to "Built" and later made wrong again would be invisible.

    Both directions instead. A module that exists may not be listed as designed, and a module listed
    as built may not be absent from the tree, which is the direction that would let the table
    advertise something the reader cannot find.
    """
    rows = [l for l in README.read_text(encoding="utf-8").splitlines() if l.startswith("| ")]
    assert rows, "no table rows found in the README, so this guard would pass vacuously"
    for path, label in DESIGNED_VS_BUILT.items():
        matching = [r for r in rows if label.lower() in r.lower()]
        assert len(matching) == 1, f"expected one table row naming {label!r}, found {matching}"
        says_designed = "designed, not built" in matching[0].lower()
        exists = (REPO / path).exists()
        assert says_designed is not exists, (
            f"{path} {'exists' if exists else 'is absent'} and its row reads "
            f"{'designed, not built' if says_designed else 'built'}: {matching[0].strip()}"
        )


#: Where an `ActContext` is built outside the suite. The cap predicate is only as strong as this.
SHIPPED_ROOTS = ("src", "tools", "demo")

#: Names that would mean a `sent_count` came from the audit log rather than from a literal.
_LOG_DERIVED = ("sent_count()", "counted_sends")


def _shipped_act_context_sites() -> dict[str, str]:
    """`{repo-relative path: the expression passed as sent_count}` for every shipped construction.

    Measured from each file's AST rather than by grep, because the enumeration this binds went
    stale exactly once already: a note enumerated "three in-tree callers", `demo/full.py` became
    the fourth, and the correction sentence outlived its own correctness.
    """
    sites: dict[str, str] = {}
    for root in SHIPPED_ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name != "ActContext":
                    continue
                for keyword in node.keywords:
                    if keyword.arg == "sent_count":
                        sites[path.relative_to(REPO).as_posix()] = ast.unparse(keyword.value)
    return sites


def test_the_send_cap_row_carries_the_qualifier_its_input_still_needs():
    """A row claiming the phrase must also say whether its inputs are fed in the shipped tree.

    `failure-modes.md` defines "zero by construction" as *the failure is impossible, not
    improbable*. The send-cap row carried it unqualified in effect: the qualifier pointed at B1,
    which is about a **lost log line** under-counting, and B1 nowhere says that no shipped path
    feeds `sent_count` at all. A reader following the pointer arrives at a durability argument and
    leaves believing the class is decided.

    The fact is measured here in both directions. Wire the cap up -- pass `gateway.sent_count()` or
    `counted_sends(...)` into a shipped `ActContext` -- and this goes red, which is the day the
    qualifier stops being true and has to come off.
    """
    sites = _shipped_act_context_sites()
    assert sites, "no shipped ActContext construction was found, so this guard measures nothing"
    derived = {path: expr for path, expr in sites.items() if any(n in expr for n in _LOG_DERIVED)}
    assert derived == {}, (
        f"a shipped caller now derives the send count from the log ({derived}); the send-cap "
        "qualifier in docs/failure-modes.md and README.md Limits describes a tree that has moved"
    )

    catalog = (REPO / "docs" / "failure-modes.md").read_text(encoding="utf-8")
    row = [l for l in catalog.splitlines() if l.startswith("| `act:send_cap_exceeded`")]
    assert len(row) == 1, f"expected one act:send_cap_exceeded row, found {row}"
    lowered = _unwrapped(row[0])
    assert "zero by construction" in lowered, "the send-cap row no longer carries a claim value"
    assert "unfed" in lowered or "no shipped" in lowered, (
        f"the send-cap row claims zero by construction without stating that its input is unfed "
        f"in the shipped tree: {row[0]}"
    )


def test_the_documented_callers_that_hand_the_cap_a_literal_are_the_ones_the_tree_holds():
    """An enumeration that says it was done by grep, held to a fresh one.

    `docs/ON_CALL.md` lists the shipped callers that pass a literal zero. The list was right when
    it was written and `demo/full.py` joined them afterwards, so a sentence whose whole point was
    that it had been measured rather than recalled had itself gone stale.
    """
    measured = {
        path for path, expr in _shipped_act_context_sites().items() if expr.strip() == "0"
    }
    assert measured, "no shipped caller passes a literal send count, so this guard measures nothing"

    on_call = (REPO / "docs" / "ON_CALL.md").read_text(encoding="utf-8")
    paragraph = [p for p in on_call.split("\n\n") if "literal zero" in p]
    assert len(paragraph) == 1, f"expected one paragraph naming the literal-zero callers, found {len(paragraph)}"
    named = set(SOURCE_PATH.findall(paragraph[0]))
    assert measured <= named, (
        f"callers hand the cap a literal and are not named on the page: {sorted(measured - named)}"
    )


def test_the_catalog_names_the_only_caller_of_the_count_the_cap_would_need():
    """The qualifier's supporting fact, measured rather than restated.

    `Gateway.sent_count()` is the one function that derives the cap's input from the log. If it
    acquires a caller outside `tests/audit/test_send_cap.py`, the claim that the predicate is
    unfed is no longer true and every page repeating it is wrong on the same commit.
    """
    here = Path(__file__).resolve()
    callers = {
        path.relative_to(REPO).as_posix()
        for root in (*SHIPPED_ROOTS, "tests")
        for path in (REPO / root).rglob("*.py")
        # This module names the call in `_LOG_DERIVED` in order to look for it, which is not a read
        # of the count. Excluded by path rather than by pattern, so no other file gets the exemption.
        if path.resolve() != here and "sent_count()" in path.read_text(encoding="utf-8")
    }
    assert callers == {"tests/audit/test_send_cap.py"}, (
        f"the derived send count is now read from {sorted(callers)}; the unfed-input caveat in "
        "README.md, docs/failure-modes.md and docs/ON_CALL.md describes a tree that has moved"
    )


#: The enforcement and ladder entry points the reader-facing pages describe as layers or as
#: transitions. Every one of these is built and tested; the question this roster answers is which of
#: them anything outside `tests/` actually calls.
#:
#: A roster rather than a sweep of every public symbol, because a sweep is dominated by helpers
#: whose only caller is their own module and says nothing about a claim anyone made. These are the
#: names the documents put weight on.
ENFORCEMENT_ROSTER = (
    "pre_tool_use", "guarded_call", "on_pass", "on_violation", "verbs_for", "max_tier_for",
    "transmit", "resume", "requires_approval_for", "counted_sends",
)

#: Measured, not remembered: the roster members nothing under `src/`, `tools/` or `demo/` calls.
#: Each must be disclosed as uncalled on a reader-facing page, and each must stop being disclosed
#: on the commit that gives it a caller.
UNCALLED_IN_THE_SHIPPED_TREE = frozenset({
    "pre_tool_use", "on_pass", "on_violation", "verbs_for", "transmit", "resume",
    "requires_approval_for",
})


def _shipped_call_sites(name: str) -> list[str]:
    sites: list[str] = []
    for root in SHIPPED_ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                called = getattr(func, "id", None) or getattr(func, "attr", None)
                if called == name:
                    sites.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}")
    return sites


def test_the_ledger_of_uncalled_layers_matches_the_census_the_tree_supports():
    """A defence-in-depth claim resting on a layer nothing calls.

    `docs/architecture.md` lists `pre_tool_use` as one of three enforcement layers and both it and
    the README said `act:tool_outside_grant` is *enforced at both `pre_tool_use` and
    `guarded_call`*. `pre_tool_use` is referenced from `tests/` and from nowhere else, as are
    `LadderState.on_violation` and `verbs_for`, while the README disclosed only the promotion half.
    Not a permissive path -- `guarded_call` runs the identical `_decide_for` -- but the second layer
    of a two-layer claim was the suite.

    Both directions. Wire one of these into a shipped caller and it leaves the census, and the
    sentence disclosing it has to go with it.
    """
    measured = frozenset(name for name in ENFORCEMENT_ROSTER if not _shipped_call_sites(name))
    assert measured == UNCALLED_IN_THE_SHIPPED_TREE, (
        f"the census moved: no longer called {sorted(measured - UNCALLED_IN_THE_SHIPPED_TREE)}, "
        f"now called {sorted(UNCALLED_IN_THE_SHIPPED_TREE - measured)}"
    )
    assert _shipped_call_sites("guarded_call"), (
        "the chokepoint has no shipped caller either, so the roster is measuring nothing"
    )

    # Backticked, and only backticked. A bare-substring test passed `transmit` on the word
    # "transmitting" in an unrelated sentence about the refinement loop, which is a symbol
    # disclosure nobody wrote and no reader would find.
    disclosed = _unwrapped(_reader_facing_text())
    undisclosed = sorted(name for name in measured if f"`{name}`" not in disclosed)
    assert not undisclosed, (
        f"these are built and called from nowhere outside tests/, and no reader-facing page "
        f"names them: {undisclosed}"
    )


CANONICAL_CLAIMS = (
    "zero by construction",
    "structural invariant",
    "measured",
    "detection only",
    "designed, not built",
)

CLAIM = re.compile(r"\*\*Claim: ([^*]+)\*\*")


def test_every_claim_value_in_the_catalog_is_one_of_the_five():
    """The catalog declares a five-value vocabulary, so drift in it is drift in the thesis.

    The ratio of measured rows to zero-by-construction rows is the argument this repository makes.
    A vocabulary that quietly grows a sixth value, or that softens "measured" into something warmer
    on the entries where the result is inconvenient, breaks that argument without breaking anything
    a reader can see. Em-dashes had a test and this did not, which was the wrong way round.

    A qualifier after the base value is allowed and is documented as allowed: it is where an entry
    names what its own mechanism does not reach.
    """
    catalog = REPO / "docs" / "failure-modes.md"
    values = [v.strip().rstrip(".").lower() for v in CLAIM.findall(catalog.read_text(encoding="utf-8"))]
    assert values, "no claim values found in the catalog, so this guard is passing vacuously"

    unrecognised = sorted(
        {v for v in values if not any(v.startswith(base) for base in CANONICAL_CLAIMS)}
    )
    assert not unrecognised, f"claim values outside the five-value vocabulary: {unrecognised}"


def test_zero_by_construction_is_claimed_only_on_act_class_entries():
    """The one claim that is a guarantee, restricted to the one family that can support it.

    Complements the line-level scan above: this checks the catalog's own claim fields rather than
    incidental prose, so a content-class entry cannot acquire the strong claim by being written
    carefully enough to keep the words on separate lines.
    """
    catalog = (REPO / "docs" / "failure-modes.md").read_text(encoding="utf-8")
    for section in re.split(r"\n### ", catalog)[1:]:
        heading, _, body = section.partition("\n")
        claims = [c.strip().lower() for c in CLAIM.findall(body)]
        if any(c.startswith("zero by construction") for c in claims):
            assert "act" in heading.lower() or "act:" in body.lower(), (
                f"entry '{heading.strip()}' claims zero by construction without being an act-class entry"
            )


@pytest.mark.parametrize("path", READER_FACING, ids=lambda p: p.name)
def test_the_reader_facing_docs_carry_no_em_dashes(path: Path):
    """Client-facing artifact convention. Scoped to the reader layer: PREREGISTRATION.md and the
    superpowers specs predate the rule and are not rewritten."""
    text = path.read_text(encoding="utf-8")
    assert "—" not in text, f"{path.name} contains an em-dash"


def test_the_full_demo_runs_both_scenes_and_enters_the_send_tool_in_neither():
    """The README's money demo is two scenes, and only one of them is `demo/day2.py`.

    Scene 2 is the one a reader will not predict: the refinement loop resolves the denial, and the
    resolved redraft **still** goes into the handoff for approval rather than transmitting. A
    redraft that transmitted by itself after a permission failure would be an auto-retry of a
    permission failure, which is the thing this architecture exists to refuse.

    Production changes that break this: handing `refine`'s resolved body to the send registry
    instead of to `build_handoff`; a futile denial that spends a redraft round; and a scene 2 whose
    loop never runs, which would leave `refine` exercised by unit tests alone while the README
    described a loop nobody had watched turn.

    The script's own `assert not entered` runs before each print and `check=True` surfaces it, so
    the effect is asserted inside the run and the shape of the run is asserted here.
    """
    completed = subprocess.run(
        [sys.executable, str(REPO / "demo" / "full.py")],
        capture_output=True, text=True, cwd=REPO, check=True,
    )
    out = completed.stdout

    assert "SCENE 1" in out and "SCENE 2" in out, f"both scenes did not print:\n{out}"
    assert out.count("send_message entered 0 times") == 2, (
        f"the send tool was entered, or a scene did not route through the chokepoint:\n{out}"
    )
    assert "stopped_for=futile" in out, "scene 1 did not stop as futile"
    assert "refinement_rounds=0" in out, "scene 1 spent a redraft round on a futile denial"
    assert "stopped_for=resolved" in out, "scene 2 did not resolve"
    assert re.search(r"refinement_rounds=[1-9]", out), (
        f"scene 2's handoff carries no completed refinement round:\n{out}"
    )


#: Denials of coverage that the tree refutes, in the spellings that were actually on the page. Each
#: was written when it was true and each is paired below with the measurement that refutes it, so
#: this is a fact check rather than a list of banned words.
REFUTED_DENIALS = (
    "no test executes this script",
    "tests/ executes no script",
    "nothing in this tree derives from the log",
)

#: Where those sentences lived: the reader-facing pages and the scripts' own docstrings, since the
#: origin of two of them was `demo/day2.py` and a guard scoped to the pages alone would have left
#: the sentence in the file the pages were quoting.
DENIAL_SCOPE = (*READER_FACING, *sorted((REPO / "demo").glob("*.py")))


def _subprocess_targets() -> set[str]:
    """The scripts this module launches, read from its own AST rather than from memory."""
    targets: set[str] = set()
    for node in ast.walk(ast.parse(Path(__file__).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "run":
            unparsed = ast.unparse(node)
            targets.update(name for name in ("day2.py", "full.py") if name in unparsed)
    return targets


@pytest.mark.parametrize("path", DENIAL_SCOPE, ids=lambda p: p.name)
def test_no_page_or_script_repeats_a_denial_of_coverage_the_tree_refutes(path: Path):
    """Two sentences that outlived the state they described, and the fact that refutes each.

    *"No test executes this script"* sat under a heading promising precision while this module ran
    `demo/day2.py` under `check=True` a hundred lines below, and the README contradicted itself
    twice more on the same page. *"Nothing in this tree derives from the log"* was refuted by
    `recovery.counted_sends`, which `tools/perturbation_log.py` calls in shipped, non-test code.

    The refuting facts are measured first, so this cannot degrade into a word ban that stays green
    after the coverage it describes is deleted. Take the subprocess run out of this module and the
    first assertion fails, which is the day the sentence becomes true again and may be restored.
    """
    assert _subprocess_targets() == {"day2.py", "full.py"}, (
        "this module no longer runs both demo scripts, so 'no test executes this script' is no "
        "longer a false sentence and this guard is asserting the wrong thing"
    )
    derivers = {
        p.relative_to(REPO).as_posix()
        for root in SHIPPED_ROOTS
        for p in (REPO / root).rglob("*.py")
        if "counted_sends(" in p.read_text(encoding="utf-8")
    }
    assert derivers - {"src/chaperone/audit/recovery.py"}, (
        "no shipped module outside recovery.py derives a count from the log any more, so "
        "'nothing in this tree derives from the log' is no longer a false sentence"
    )

    # Backticks stripped as well as whitespace collapsed. The origin sentence was written
    # "`tests/` executes no script", so a guard matching the bare words would have passed over the
    # very file it was written for -- caught here by reinstating the sentence and watching it not
    # fail, which is the reason the cycle requires watching it fail.
    text = _unwrapped(path.read_text(encoding="utf-8")).replace("`", "")
    present = [phrase for phrase in REFUTED_DENIALS if phrase in text]
    assert not present, f"{path.name} carries denials the tree refutes: {present}"


#: "`X` does not exist in this tree", in the spellings the pages actually use. The symbol is
#: captured so the sentence can be checked against the tree rather than read.
ABSENCE_CLAIM = re.compile(
    r"`([A-Za-z_][A-Za-z0-9_.]*)`[^.!?]{0,160}?"
    r"(?:does not exist|is absent from|exists nowhere|is not in)[^.!?]{0,40}?in this tree"
)


def _top_level_definitions() -> set[str]:
    """Every function and class defined at module level under `src/`."""
    names: set[str] = set()
    for path in (REPO / "src").rglob("*.py"):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
    return names


@pytest.mark.parametrize("path", READER_FACING, ids=lambda p: p.name)
def test_no_reader_facing_page_says_a_symbol_is_absent_that_the_tree_defines(path: Path):
    """The denial that goes stale on the commit that builds the thing it denies.

    `docs/audit-walkthrough.md` said *the pass itself, `recovery.resume`, does not exist in this
    tree* while `resume` and `requires_approval_for` were both built, tested, and described
    correctly on two other pages. Nobody reads a page for the sentence that used to be true.

    Matched on the terminal name rather than on the dotted path, so `recovery.resume` is checked
    against `def resume`. That is deliberately loose in the direction of a spurious finding: a page
    denying `foo.bar` while some unrelated module defines `bar` fails here and has to be reworded.
    A guard that erred the other way would be the guard this one replaces.
    """
    defined = _top_level_definitions()
    assert defined, "no definitions were parsed, so this guard would pass vacuously"
    # Whitespace collapsed so a wrapped sentence still matches, but **not** lowercased: a class name
    # is compared against `ast`'s own spelling, and `LadderState` lowercased matches nothing.
    text = re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))
    stale = [
        symbol for symbol in ABSENCE_CLAIM.findall(text) if symbol.split(".")[-1] in defined
    ]
    assert not stale, (
        f"{path.name} says these do not exist in this tree, and src/ defines them: {stale}"
    )


#: Sentences that assert this tree publishes no results. Each was true once, and each is the kind of
#: claim that stays on the page long after it stops being true, because nothing reads it.
RESULTS_DENIALS = (
    "the arms have not been run",
    "no arm has been run",
    "no rate has been computed",
    "there is no `results.md` in this tree",
    "a results section that does not exist",
    "no rate is printed here yet",
)


@pytest.mark.parametrize("path", READER_FACING, ids=lambda p: p.name)
def test_no_reader_facing_page_denies_the_results_this_tree_publishes(path: Path):
    """A page that denies its own results is worse than a page with no results.

    This exact drift has been corrected twice by hand, in the README and in `docs/measurement.md`
    section 7, and both times a reader following a citation is who would have found it. The correction
    is a guard rather than a third careful read.

    The direction that would otherwise stay open, and where it is actually closed: deleting
    `docs/RESULTS.md` makes every phrase below true again and silences this test. That is closed by
    the `RESULTS_PAGE.exists()` assertion in `test_the_docs_directory_is_not_silently_empty`, and
    **not** by the link resolver. An earlier version of this docstring cited the link resolver, which
    is a weaker closure, because the edit that deletes a page plausibly deletes the link to it in the
    same commit; a later reader trusting that sentence would have deleted the real guard believing
    another one covered it.
    """
    published = RESULTS_PAGE.exists()
    text = _unwrapped(path.read_text(encoding="utf-8"))
    for phrase in RESULTS_DENIALS:
        assert not (phrase in text and published), (
            f"{path.name} says {phrase!r} while docs/RESULTS.md is in this tree"
        )


def test_the_readme_states_the_smallest_production_v1_and_names_the_surface_it_would_ship():
    """A first-deployment section that names no surface is an opinion with a heading on it.

    Production change that breaks this: dropping the section, or softening it into a paragraph that
    recommends shipping the demo. The demo is an outbound surface at tier 2, and the whole point of
    the section is that the first rung is the read-only one.
    """
    text = _unwrapped(README.read_text(encoding="utf-8"))
    assert "smallest production v1" in text, "the README states no smallest production v1"
    for named in ("research agent", "read-only", "nothing leaves"):
        assert named in text, f"the smallest production v1 does not name {named!r}"


def test_the_readme_carries_the_ladder_honesty_line():
    """Promotion keyed to a suite score is the judgment error this artifact argues against, so the
    refusal is stated rather than left to be inferred from `on_pass` having no caller.

    Production change that breaks this: deleting the sentence, which costs nothing to do and is
    exactly what an artifact wanting to look finished would delete.
    """
    text = _unwrapped(README.read_text(encoding="utf-8"))
    assert "never to synthetic suite scores" in text, "the ladder honesty line is gone"


def test_the_second_scene_pasted_in_the_readme_is_verbatim_from_a_fresh_run():
    """The scene-2 block was an elided paste with no marker, beside a byte-guarded day-2 block.

    `test_the_demo_output_pasted_in_the_readme_matches_a_fresh_run` anchors on `QUALITY LANE`, so a
    fence opening with `SCENE 2` is invisible to it. The README advertises that guard a few lines
    above, so an alert reader assumes the same standard, runs the demo, and finds lines the paste
    omitted with nothing saying it was an excerpt.

    Production changes that break this: eliding a line again; a scene-2 verdict, category, round
    count or proposal that moves without the paste moving with it. `rounds=1` drifting to 2 is the
    specific one the earlier `[1-9]` guard would have accepted.

    Asserted as a contiguous substring rather than as whole-output equality, because the block is
    deliberately the second scene of a two-scene run. A substring assertion is still byte exact.
    """
    fenced = re.findall(r"```\n(SCENE 2.*?)```", README.read_text(encoding="utf-8"), re.S)
    assert len(fenced) == 1, "expected exactly one pasted scene-2 transcript in the README"

    completed = subprocess.run(
        [sys.executable, str(REPO / "demo" / "full.py")],
        capture_output=True, text=True, cwd=REPO, check=True,
    )
    assert fenced[0].strip() in completed.stdout, (
        "the README's scene-2 paste is not verbatim in a fresh run of demo/full.py"
    )
