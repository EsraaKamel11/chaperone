import json
import subprocess
import sys
from pathlib import Path

GUARD = Path(__file__).resolve().parents[1] / "tools" / "guard_edit.py"


def _run(payload: dict, env: dict | None = None) -> subprocess.CompletedProcess:
    import os
    merged = {**os.environ, **(env or {})}
    return subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload),
                          capture_output=True, text=True, env=merged)


def test_an_llm_import_into_policy_is_blocked():
    result = _run({"tool_input": {"file_path": "src/chaperone/policy/x.py", "content": "import anthropic\n"}})
    assert result.returncode == 2
    assert "pure" in result.stderr


def test_an_io_import_into_policy_is_blocked():
    result = _run({"tool_input": {"file_path": "src/chaperone/policy/x.py", "content": "import sqlite3\n"}})
    assert result.returncode == 2


def test_the_same_import_outside_policy_is_allowed():
    result = _run({"tool_input": {"file_path": "src/chaperone/gates/x.py", "content": "import anthropic\n"}})
    assert result.returncode == 0


def test_a_forbidden_organisation_token_is_blocked_anywhere():
    result = _run(
        {"tool_input": {"file_path": "README.md", "content": "Built for Acme Holdings.\n"}},
        env={"CHAPERONE_FORBIDDEN_TOKENS": "acme holdings"},
    )
    assert result.returncode == 2
    assert "organisation" in result.stderr


def test_no_token_list_means_no_organisation_check():
    result = _run(
        {"tool_input": {"file_path": "README.md", "content": "Built for Acme Holdings.\n"}},
        env={"CHAPERONE_FORBIDDEN_TOKENS": ""},
    )
    assert result.returncode == 0


def test_an_edit_payload_is_checked_as_well_as_a_write_payload():
    result = _run({"tool_input": {"file_path": "src/chaperone/policy/x.py", "new_string": "import time\n"}})
    assert result.returncode == 2


def test_a_clean_edit_passes():
    result = _run({"tool_input": {"file_path": "src/chaperone/policy/x.py", "content": "VALUE = 42\n"}})
    assert result.returncode == 0
