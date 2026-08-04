from pathlib import Path
from tools.scan_secrets import scan_tree

def test_a_planted_secret_in_a_text_file_is_found(tmp_path: Path):
    (tmp_path / "notes.md").write_text("key = sk-abc123def456ghi789jkl012mno345pqr", encoding="utf-8")
    findings = scan_tree(tmp_path)
    assert len(findings) == 1
    assert findings[0][0].name == "notes.md"

def test_a_planted_secret_in_a_source_file_is_found(tmp_path: Path):
    (tmp_path / "cfg.py").write_text('TOKEN = "ghp_0123456789abcdefghijklmnopqrstuvwxyz"\n', encoding="utf-8")
    findings = scan_tree(tmp_path)
    assert len(findings) == 1

def test_a_high_entropy_value_on_a_credential_named_key_is_found(tmp_path: Path):
    (tmp_path / "s.py").write_text('api_secret = "Zk3Lq9Xv2Rt8Wn5Yb1Hs4Jd7Fg0Mc6P"\n', encoding="utf-8")
    findings = scan_tree(tmp_path)
    assert len(findings) == 1

def test_a_clean_tree_produces_no_findings(tmp_path: Path):
    (tmp_path / "ok.py").write_text("VALUE = 42\n", encoding="utf-8")
    assert scan_tree(tmp_path) == []
