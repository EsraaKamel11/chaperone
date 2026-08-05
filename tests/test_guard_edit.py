import ast
import json
import os
import subprocess
import sys
from pathlib import Path

from tools import guard_edit, static_audit
from tools.guard_edit import FORBIDDEN_IN_POLICY as HOOK_FORBIDDEN
from tools.static_audit import FORBIDDEN_IN_POLICY as AUDIT_FORBIDDEN

GUARD = Path(__file__).resolve().parents[1] / "tools" / "guard_edit.py"

_MODULE_SAMPLES = [
    "chaperone.policy", "chaperone.policy.types", "chaperone.policyholder",
    "chaperone.gates", "chaperone", "os", "chaperone_extras",
]


def _run(payload: dict, env: dict | None = None) -> subprocess.CompletedProcess:
    import os
    merged = {**os.environ, **(env or {})}
    return subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload),
                          capture_output=True, text=True, env=merged)


def test_the_edit_guard_and_the_ci_audit_forbid_the_same_modules():
    """The two layers enforce one policy, so they must forbid one set of modules.

    They hold separate literal lists -- the hook runs with tools/ on sys.path[0] and cannot
    cleanly import the audit at startup -- so this test is what binds them.
    """
    hook, audit = set(HOOK_FORBIDDEN), set(AUDIT_FORBIDDEN)
    assert hook == audit, (
        f"only in the edit-time hook: {sorted(hook - audit)}; "
        f"only in the CI audit: {sorted(audit - hook)}"
    )


_DIFFERENTIAL_CORPUS = [
    "import os", "import json", "import anthropic", "from anthropic import Client",
    "import os.path", "from os import path", "from os.path import join",
    "import os as o", "import json as j", "import chaperone.gates as g",
    "import json, os", "import json,os", "import  json ,  os",
    "import json as j, os", "import json as j, os as o", "import os, json",
    "import json, os  # trailing comment",
    "import chaperone.gates", "import chaperone.policy.types",
    "import chaperone.policy.types as t, chaperone.gates",
    "from chaperone.gates import engine", "from chaperone.policy.types import Draft",
    "from chaperone.policy import types, canonical", "from chaperone import policy",
    "import chaperone", "import chaperone.policyholder",
    "from chaperone_extras import x", "import chaperone_extras",
    "from x import os", "from x import time, random",
    "from . import canonical", "from .gates import x", "from .os import x",
    "from ..audit import gateway",
]


def _ast_path_refuses(statement: str) -> bool:
    for node in ast.walk(ast.parse(statement)):
        for module in guard_edit._imported_modules(node):
            if guard_edit._reason(module):
                return True
    return False


def test_the_two_paths_through_the_guard_reach_the_same_verdict():
    """The fallback runs only on fragments the AST path could not parse, so nothing else ever
    compares them. Either path can drift, and only the shape nobody thought to test would show it.
    """
    by_ast = {s: _ast_path_refuses(s) for s in _DIFFERENTIAL_CORPUS}
    by_fallback = {s: guard_edit._fallback_refusal(s) is not None for s in _DIFFERENTIAL_CORPUS}
    assert by_ast == by_fallback


def test_the_edit_guard_and_the_ci_audit_reach_the_same_verdict_on_every_module():
    """Equal lists are not equal behaviour: the two layers also duplicate the decision itself.

    The wording of the two refusals differs by design, so this compares the verdict -- refused or
    allowed -- which is the thing the two layers must agree on.
    """
    hook = {m: guard_edit._reason(m) is None for m in _MODULE_SAMPLES}
    audit = {m: static_audit._reason(m) is None for m in _MODULE_SAMPLES}
    assert hook == audit


def test_a_dynamic_import_helper_into_policy_is_blocked():
    result = _run({"tool_input": {"file_path": "src/chaperone/policy/x.py", "content": "import importlib\n"}})
    assert result.returncode == 2


def test_a_dependency_outside_policy_is_blocked():
    result = _run({
        "tool_input": {
            "file_path": "src/chaperone/policy/x.py",
            "content": "from chaperone.gates import engine\n",
        }
    })
    assert result.returncode == 2
    assert "chaperone.gates" in result.stderr


def test_an_import_from_within_policy_is_allowed():
    result = _run({
        "tool_input": {
            "file_path": "src/chaperone/policy/x.py",
            "content": "from chaperone.policy.types import Draft\n",
        }
    })
    assert result.returncode == 0


def test_a_package_merely_prefixed_with_policy_is_blocked():
    result = _run({
        "tool_input": {
            "file_path": "src/chaperone/policy/x.py",
            "content": "import chaperone.policyholder\n",
        }
    })
    assert result.returncode == 2


def test_a_dependency_outside_policy_in_an_unparseable_fragment_is_blocked():
    result = _run({
        "tool_input": {
            "file_path": "src/chaperone/policy/x.py",
            "new_string": "from chaperone.gates import engine\n    y = (\n",
        }
    })
    assert result.returncode == 2


def test_an_offending_import_after_an_allowed_one_in_a_fragment_is_blocked():
    result = _run({
        "tool_input": {
            "file_path": "src/chaperone/policy/x.py",
            "new_string": "from chaperone.policy.types import Draft\nfrom chaperone.gates import engine\n    y = (\n",
        }
    })
    assert result.returncode == 2
    assert "chaperone.gates" in result.stderr


def test_an_offending_alias_after_an_allowed_one_in_a_fragment_is_blocked():
    result = _run({
        "tool_input": {
            "file_path": "src/chaperone/policy/x.py",
            "new_string": "import chaperone.policy.types, chaperone.gates\n    y = (\n",
        }
    })
    assert result.returncode == 2
    assert "chaperone.gates" in result.stderr


def test_a_forbidden_module_after_an_unlisted_one_in_a_fragment_is_blocked():
    result = _run({
        "tool_input": {
            "file_path": "src/chaperone/policy/x.py",
            "new_string": "import json, os\n    y = (\n",
        }
    })
    assert result.returncode == 2
    assert "'os'" in result.stderr


def test_a_forbidden_module_after_an_as_alias_in_a_fragment_is_blocked():
    result = _run({
        "tool_input": {
            "file_path": "src/chaperone/policy/x.py",
            "new_string": "import json as j, os\n    y = (\n",
        }
    })
    assert result.returncode == 2
    assert "'os'" in result.stderr


def test_a_relative_import_of_a_forbidden_name_in_a_fragment_is_blocked():
    """The AST path refuses `from .os import x`; the fallback must not disagree.

    Found by a differential probe over both paths, not by a failing edit.
    """
    result = _run({
        "tool_input": {
            "file_path": "src/chaperone/policy/x.py",
            "new_string": "from .os import sleep\n    y = (\n",
        }
    })
    assert result.returncode == 2
    assert "'os'" in result.stderr


def test_a_cross_package_import_after_an_as_alias_in_a_fragment_is_blocked():
    result = _run({
        "tool_input": {
            "file_path": "src/chaperone/policy/x.py",
            "new_string": "import chaperone.policy.types as t, chaperone.gates\n    y = (\n",
        }
    })
    assert result.returncode == 2
    assert "chaperone.gates" in result.stderr


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


def test_a_from_import_of_an_llm_client_into_policy_is_blocked():
    result = _run({"tool_input": {"file_path": "src/chaperone/policy/x.py", "content": "from anthropic import Client\n"}})
    assert result.returncode == 2


def test_a_docstring_mentioning_forbidden_module_names_in_prose_is_allowed():
    result = _run({
        "tool_input": {
            "file_path": "src/chaperone/policy/types.py",
            "content": '"""This module must not import os, io, or time."""\n',
        }
    })
    assert result.returncode == 0


def test_a_multi_alias_import_fragment_is_blocked():
    result = _run({"tool_input": {"file_path": "src/chaperone/policy/x.py", "new_string": "    import json, os\n    x = 1\n"}})
    assert result.returncode == 2


# ---------------------------------------------------------------------------
# The same fail-open sweep applied here as to tools/policy_hook.py.
#
# Exit 2 blocks a PreToolUse hook and exit 1 does not, so an unreadable payload returning 0 and an
# uncaught raise exiting 1 were both edits waved through by a guard that had not examined them.
# The identical line was closed in the policy hook; leaving it standing here is one defect fixed in
# one of two siblings, which is the drift this project treats as a failure in itself.
# ---------------------------------------------------------------------------


def _run_raw(text: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GUARD)], input=text, capture_output=True, text=True)


def test_an_unreadable_payload_refuses_the_edit():
    """`except json.JSONDecodeError: return 0` allowed every edit it could not parse."""
    assert _run_raw("not json at all").returncode == 2


def test_an_empty_payload_refuses_the_edit():
    """Empty stdin raises `JSONDecodeError` too, so it took the same allow."""
    assert _run_raw("").returncode == 2


def test_a_payload_that_is_not_an_object_refuses_the_edit():
    """`payload.get` on a list raised `AttributeError`, and an uncaught raise exits 1, which allows."""
    assert _run_raw("[1, 2, 3]").returncode == 2
    assert _run_raw('"a string"').returncode == 2


def test_a_payload_the_guard_cannot_evaluate_refuses_the_edit():
    """A non-string body reaches `ast.parse` and raises `TypeError` on the way to a verdict.

    The file path names `policy/`, so this is precisely an edit the guard exists to examine, and
    exiting 1 on it meant the purity check was skipped exactly where it applies.
    """
    result = _run({"tool_input": {"file_path": "src/chaperone/policy/x.py", "content": 5}})
    assert result.returncode == 2


def test_a_guard_that_cannot_explain_itself_still_refuses():
    """The verdict travels in the exit code, so losing stderr must not lose the verdict."""
    handle = os.open(os.devnull, os.O_RDONLY)
    try:
        # Measured on Windows across CPython 3.11.15 and 3.13.9; unverified on Linux, where CI runs.
        # The premise is asserted so a platform that permitted the write fails loudly here rather
        # than letting the assertion below pass vacuously.
        try:
            os.write(handle, b"x")
        except OSError:
            pass
        else:
            raise AssertionError("stderr redirect is writable here, so this test proves nothing")
        completed = subprocess.run([sys.executable, str(GUARD)], input="not json", text=True, stderr=handle)
    finally:
        os.close(handle)
    assert completed.returncode == 2


def test_both_command_hooks_refuse_an_unreadable_payload_identically():
    """One policy on malformed input, two guards -- asserted rather than assumed.

    These are the project's two command hooks. They guard different things, so their verdicts on a
    *readable* payload differ by design, and only the undecidable case is shared: neither can have
    examined anything, so neither may report clean. Comparing them here is what stops the next fix
    landing in one and not the other.
    """
    policy_guard = Path(__file__).resolve().parents[1] / "tools" / "policy_hook.py"
    for text in ("not json at all", "", "[1, 2, 3]"):
        edit = _run_raw(text)
        policy = subprocess.run([sys.executable, str(policy_guard)], input=text,
                                capture_output=True, text=True)
        assert (edit.returncode, policy.returncode) == (2, 2), text
