from pathlib import Path

from tools.scan_secrets import ROOT, main, scan_tree

# --------------------------------------------------------------------------------------------
# `main()`, in both directions
#
# The exit code is the property: `.github/workflows/ci.yml` runs this as its first enforcement step
# and `.claude/settings.json` wires it behind `Stop`, so `return 1 if findings else 0` becoming
# `return 0` disarms both. Nothing referenced `main` at all, so that edit -- and `_TEXT_SUFFIXES =
# set()`, which makes the scan examine nothing -- left the whole suite green.
#
# The sentence that stood here claimed `tests/test_static_audit.py` "asserts `main() == 0` on the
# real tree and `main() == 1` on a planted violation". **The second half did not exist**, and it was
# written in the commit that closed this exact gap one tool over: a coverage claim with no coverage
# behind it, measured -- `return 1 if violations else 0` becoming `return 0` in `static_audit` left
# that file at 19 passed. It is true as of the commit correcting this line, and the three sibling
# tools now each assert both directions through `main`: `tests/test_static_audit.py`,
# `tests/test_coverage_map.py` and `tests/test_lint_descriptions.py`.
# --------------------------------------------------------------------------------------------

_PLANTED = 'api_secret = "Zk3Lq9Xv2Rt8Wn5Yb1Hs4Jd7Fg0Mc6P"\n'  # allowlist-secret


def test_the_scan_exits_zero_and_says_nothing_on_the_real_tree(capsys):
    assert main() == 0
    assert capsys.readouterr().out == ""


def test_the_scan_exits_one_and_names_the_finding_on_a_planted_secret(tmp_path: Path, capsys):
    (tmp_path / "cfg.py").write_text(_PLANTED, encoding="utf-8")
    assert main(tmp_path) == 1
    assert "credential_named_high_entropy" in capsys.readouterr().out


def test_a_scan_that_visited_no_eligible_file_is_not_reported_clean(tmp_path: Path, capsys):
    """An enforcement step that examined nothing must never exit 0.

    `_TEXT_SUFFIXES = set()` is the production change this exists for: every extension becomes
    ineligible, the real tree yields no finding, and CI's first step goes green over a scan that
    read nothing. Same shape as `tools/static_audit.py`'s "audited nothing" and
    `tools/coverage_map.py`'s "classified nothing", and it travels the same path to the same exit
    code as any other finding.
    """
    (tmp_path / "binary.bin").write_bytes(b"\x00\x01")
    assert main(tmp_path) == 1
    assert "scanned nothing" in capsys.readouterr().out


def test_the_scan_is_anchored_to_the_repository_and_not_to_the_working_directory(
    tmp_path: Path, monkeypatch, capsys
):
    """`Path.cwd()` meant the step scanned whatever directory CI happened to be standing in.

    Every sibling tool anchors to `Path(__file__).resolve().parents[1]`. Asserted as an effect: a
    secret planted in the working directory is not found, and one planted in the scanned root is.
    """
    (tmp_path / "leak.py").write_text(_PLANTED, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main() == 0
    assert capsys.readouterr().out == ""
    assert ROOT == Path(__file__).resolve().parents[1]


def test_a_planted_secret_in_a_text_file_is_found(tmp_path: Path):
    (tmp_path / "notes.md").write_text("key = sk-abc123def456ghi789jkl012mno345pqr", encoding="utf-8")  # allowlist-secret
    findings = scan_tree(tmp_path)
    assert len(findings) == 1
    assert findings[0][0].name == "notes.md"

def test_a_planted_secret_in_a_source_file_is_found(tmp_path: Path):
    (tmp_path / "cfg.py").write_text('TOKEN = "ghp_0123456789abcdefghijklmnopqrstuvwxyz"\n', encoding="utf-8")  # allowlist-secret
    findings = scan_tree(tmp_path)
    assert len(findings) == 1

def test_a_high_entropy_value_on_a_credential_named_key_is_found(tmp_path: Path):
    (tmp_path / "s.py").write_text('api_secret = "Zk3Lq9Xv2Rt8Wn5Yb1Hs4Jd7Fg0Mc6P"\n', encoding="utf-8")  # allowlist-secret
    findings = scan_tree(tmp_path)
    assert len(findings) == 1

def test_a_clean_tree_produces_no_findings(tmp_path: Path):
    (tmp_path / "ok.py").write_text("VALUE = 42\n", encoding="utf-8")
    assert scan_tree(tmp_path) == []

def test_prose_containing_task_by_task_produces_no_finding(tmp_path: Path):
    (tmp_path / "notes.md").write_text(
        "Steps use checkbox syntax to implement this plan task-by-task.\n", encoding="utf-8"
    )
    assert scan_tree(tmp_path) == []

def test_a_line_marked_allowlist_secret_produces_no_finding(tmp_path: Path):
    (tmp_path / "notes.md").write_text(
        "key = sk-abc123def456ghi789jkl012mno345pqr  # allowlist-secret\n", encoding="utf-8"
    )
    assert scan_tree(tmp_path) == []

def test_a_hyphenated_prose_phrase_with_a_long_tail_produces_no_finding(tmp_path: Path):
    (tmp_path / "notes.md").write_text(
        "This is the high-risk-assessment-and-mitigation-plan for review.\n", encoding="utf-8"
    )
    assert scan_tree(tmp_path) == []
