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


IGNORED_DIRS = frozenset({".git", ".superpowers", "__pycache__", ".venv", "venv", "node_modules"})


def _all_markdown() -> list[Path]:
    """Every markdown file that would publish, not only the reader-facing five.

    `docs/superpowers/` ships with this repository, so a guard scoped to `docs/*.md` would leave the
    design documents unchecked while claiming to cover the repository. `.superpowers/` is excluded
    because it is gitignored build output and does not publish.
    """
    return sorted(
        p for p in REPO.rglob("*.md") if not any(part in IGNORED_DIRS for part in p.parts)
    )


def _defined_test_names() -> set[str]:
    names: set[str] = set()
    for path in (REPO / "tests").rglob("test_*.py"):
        names.update(re.findall(r"^\s*def (test_[a-z0-9_]+)", path.read_text(encoding="utf-8"), re.M))
    return names


def test_the_docs_directory_is_not_silently_empty():
    """Every other test here passes vacuously if the pages are missing."""
    assert README.exists(), "README.md is missing"
    assert len(DOCS) >= 4, f"expected the four docs pages, found {[p.name for p in DOCS]}"


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


@pytest.mark.parametrize("path", READER_FACING, ids=lambda p: p.name)
def test_zero_by_construction_is_never_claimed_beside_a_content_class(path: Path):
    """CLAUDE.md forbids the phrase outside act-classes, in documentation as much as in code.

    Guarding the README alone would leave five of six reader-facing files unguarded, which is where
    the claim is most likely to drift: a docs page has room to explain, and explaining is where an
    act-class guarantee gets generalised into a sentence about the whole gate.
    """
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        lowered = line.lower()
        if "zero by construction" in lowered and "content:" in lowered:
            pytest.fail(f"{path.name}:{number} claims zero by construction beside a content class")


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


@pytest.mark.parametrize("path", _all_markdown(), ids=lambda p: str(p.relative_to(REPO)))
def test_no_organisation_is_named_in_any_published_markdown(path: Path):
    """CLAUDE.md: no organisation name appears anywhere in this repository.

    Scoped to every markdown file that publishes rather than to the reader-facing five, because the
    docstring's claim is about the repository and a guard narrower than its own claim is the exact
    shape of overclaim this suite exists to catch. `docs/superpowers/` ships, so it is checked.

    The failure message names no token. A guard that printed the forbidden word on failure would
    publish it into every CI log, which is the thing it was written to prevent.
    """
    for word in re.findall(r"[A-Za-z0-9]+", path.read_text(encoding="utf-8").lower()):
        digest = hashlib.sha256(word.encode()).hexdigest()
        assert digest not in FORBIDDEN_TOKEN_DIGESTS, (
            f"a forbidden organisation token appears in {path.relative_to(REPO)}"
        )


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
DESIGNED_VS_BUILT = {
    "src/chaperone/evals/calibration.py": "Checker calibration",
    "src/chaperone/evals/discrimination.py": "Discrimination",
    "src/chaperone/audit/recovery.py": "Crash-recovery",
    "src/chaperone/gates/refine.py": "Refinement loop",
    "src/chaperone/gates/ladder.py": "Capability ladder",
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
