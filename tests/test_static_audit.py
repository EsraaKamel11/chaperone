from pathlib import Path

from tools.static_audit import audit_policy_purity, audit_send_references

SRC = Path(__file__).resolve().parents[1] / "src" / "chaperone"


def test_the_real_policy_package_is_pure():
    assert audit_policy_purity(SRC / "policy") == []


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


def test_a_send_reference_outside_the_gateway_is_caught(tmp_path: Path):
    src = tmp_path / "chaperone"
    (src / "gates").mkdir(parents=True)
    (src / "audit").mkdir(parents=True)
    (src / "audit" / "gateway.py").write_text("def transmit(): ...\n", encoding="utf-8")
    (src / "gates" / "sneaky.py").write_text("from chaperone.audit.gateway import transmit\ntransmit()\n", encoding="utf-8")
    violations = audit_send_references(src, send_symbol="transmit", allowed_module="audit.gateway")
    assert len(violations) == 1
    assert "sneaky.py" in violations[0]
