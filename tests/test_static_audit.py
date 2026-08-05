from pathlib import Path

from tools.static_audit import audit_policy_purity, audit_send_references, audit_tree, main

SRC = Path(__file__).resolve().parents[1] / "src" / "chaperone"


def test_the_real_policy_package_is_pure():
    assert audit_policy_purity(SRC / "policy") == []


def test_the_real_source_tree_has_no_send_references_outside_the_gateway():
    """Vacuous until Task 7 creates `transmit`, since the symbol exists nowhere yet.

    It stops being vacuous the instant that task lands, it puts the real tree under pytest rather
    than under a tool invocation only, and it fails loudly if `src/chaperone` is ever renamed.
    """
    assert audit_send_references(SRC, send_symbol="transmit", allowed_module="audit.gateway") == []


def test_the_audit_ci_runs_reports_both_halves_of_a_dirty_tree(tmp_path: Path):
    """Binds the arguments `main()` audits with, through the code path CI actually runs.

    A typo in `send_symbol`, a wrong `allowed_module`, or dropping either half disarms enforcement
    while leaving every other test green -- so this plants one violation of each kind and requires
    both to be reported.
    """
    src = tmp_path / "chaperone"
    (src / "policy").mkdir(parents=True)
    (src / "gates").mkdir(parents=True)
    (src / "audit").mkdir(parents=True)
    (src / "policy" / "p.py").write_text("import time\n", encoding="utf-8")
    (src / "audit" / "gateway.py").write_text("def transmit(): ...\ntransmit()\n", encoding="utf-8")
    (src / "gates" / "leak.py").write_text("transmit()\n", encoding="utf-8")
    violations = audit_tree(src)
    assert len(violations) == 2
    assert any("policy/ imports 'time'" in v for v in violations)
    assert any("leak.py" in v for v in violations)


def test_the_audit_exits_zero_and_says_nothing_on_the_real_tree(capsys):
    assert main() == 0
    assert capsys.readouterr().out == ""


def test_an_llm_client_import_in_policy_is_caught(tmp_path: Path):
    pkg = tmp_path / "policy"
    pkg.mkdir()
    (pkg / "bad.py").write_text("import anthropic\n", encoding="utf-8")
    violations = audit_policy_purity(pkg)
    assert len(violations) == 1
    assert "anthropic" in violations[0]


def test_a_from_import_of_an_llm_client_in_policy_is_caught(tmp_path: Path):
    pkg = tmp_path / "policy"
    pkg.mkdir()
    (pkg / "bad.py").write_text("from pydantic_ai import Agent\n", encoding="utf-8")
    assert len(audit_policy_purity(pkg)) == 1


def test_an_io_import_in_policy_is_caught(tmp_path: Path):
    pkg = tmp_path / "policy"
    pkg.mkdir()
    (pkg / "bad.py").write_text("import sqlite3\n", encoding="utf-8")
    assert len(audit_policy_purity(pkg)) == 1


def test_a_clock_read_in_policy_is_caught(tmp_path: Path):
    pkg = tmp_path / "policy"
    pkg.mkdir()
    (pkg / "bad.py").write_text("import time\n", encoding="utf-8")
    assert len(audit_policy_purity(pkg)) == 1


def test_a_dynamic_import_helper_in_policy_is_caught(tmp_path: Path):
    pkg = tmp_path / "policy"
    pkg.mkdir()
    (pkg / "bad.py").write_text("import importlib\n", encoding="utf-8")
    assert len(audit_policy_purity(pkg)) == 1


def test_a_dependency_on_a_sibling_package_is_caught(tmp_path: Path):
    pkg = tmp_path / "policy"
    pkg.mkdir()
    (pkg / "bad.py").write_text("from chaperone.gates.helper import check\n", encoding="utf-8")
    violations = audit_policy_purity(pkg)
    assert len(violations) == 1
    assert "chaperone.gates.helper" in violations[0]


def test_a_package_merely_prefixed_with_policy_is_caught(tmp_path: Path):
    pkg = tmp_path / "policy"
    pkg.mkdir()
    (pkg / "bad.py").write_text("import chaperone.policyholder\n", encoding="utf-8")
    assert len(audit_policy_purity(pkg)) == 1


def test_an_import_from_within_policy_is_allowed(tmp_path: Path):
    pkg = tmp_path / "policy"
    pkg.mkdir()
    (pkg / "ok.py").write_text("from chaperone.policy.types import Draft\n", encoding="utf-8")
    assert audit_policy_purity(pkg) == []


def test_a_send_reference_outside_the_gateway_is_caught(tmp_path: Path):
    src = tmp_path / "chaperone"
    (src / "gates").mkdir(parents=True)
    (src / "audit").mkdir(parents=True)
    (src / "audit" / "gateway.py").write_text("def transmit(): ...\n", encoding="utf-8")
    (src / "gates" / "sneaky.py").write_text("from chaperone.audit.gateway import transmit\ntransmit()\n", encoding="utf-8")
    violations = audit_send_references(src, send_symbol="transmit", allowed_module="audit.gateway")
    assert len(violations) == 2
    assert all("sneaky.py" in v for v in violations)
    assert any("sneaky.py:1:" in v for v in violations)
    assert any("sneaky.py:2:" in v for v in violations)


def test_an_attribute_call_through_a_module_alias_is_caught(tmp_path: Path):
    src = tmp_path / "chaperone"
    (src / "gates").mkdir(parents=True)
    (src / "audit").mkdir(parents=True)
    (src / "audit" / "gateway.py").write_text("def transmit(): ...\n", encoding="utf-8")
    (src / "gates" / "sly.py").write_text(
        "import chaperone.audit.gateway as g\ng.transmit()\n", encoding="utf-8"
    )
    violations = audit_send_references(src, send_symbol="transmit", allowed_module="audit.gateway")
    assert len(violations) == 1
    assert "sly.py:2:" in violations[0]


def test_the_gateway_is_exempt_but_a_lookalike_path_is_not(tmp_path: Path):
    src = tmp_path / "chaperone"
    (src / "audit").mkdir(parents=True)
    (src / "gates" / "vendor" / "audit").mkdir(parents=True)
    (src / "audit" / "gateway.py").write_text("def transmit(): ...\ntransmit()\n", encoding="utf-8")
    (src / "gates" / "vendor" / "audit" / "gateway.py").write_text("transmit()\n", encoding="utf-8")
    violations = audit_send_references(src, send_symbol="transmit", allowed_module="audit.gateway")
    assert len(violations) == 1
    assert "vendor" in violations[0]


def test_a_missing_policy_root_is_not_reported_clean(tmp_path: Path):
    violations = audit_policy_purity(tmp_path / "renamed_away")
    assert len(violations) == 1
    assert "audited nothing" in violations[0]


def test_a_policy_root_holding_no_python_is_not_reported_clean(tmp_path: Path):
    pkg = tmp_path / "policy"
    pkg.mkdir()
    (pkg / "README.md").write_text("no python here\n", encoding="utf-8")
    violations = audit_policy_purity(pkg)
    assert len(violations) == 1
    assert "audited nothing" in violations[0]


def test_a_missing_source_root_is_not_reported_clean(tmp_path: Path):
    violations = audit_send_references(
        tmp_path / "renamed_away", send_symbol="transmit", allowed_module="audit.gateway"
    )
    assert len(violations) == 1
    assert "audited nothing" in violations[0]


def test_a_source_root_holding_no_python_is_not_reported_clean(tmp_path: Path):
    src = tmp_path / "chaperone"
    src.mkdir()
    violations = audit_send_references(src, send_symbol="transmit", allowed_module="audit.gateway")
    assert len(violations) == 1
    assert "audited nothing" in violations[0]
