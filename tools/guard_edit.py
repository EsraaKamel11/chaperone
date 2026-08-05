"""PreToolUse guard. Exit 2 blocks the edit and feeds the reason back to the model.

This is the project's own thesis applied to its construction: the two constraints that must never
be violated are enforced deterministically, at the layer where the action happens, rather than
stated as an instruction and hoped for.

Policy purity means two things here: no forbidden module, and no dependency outside `policy/`.
`tools/static_audit.py` enforces the same policy over the whole tree in CI, and a parity test holds
the two forbidden-module lists identical -- two layers enforcing one policy, not two policies.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
import textwrap

FORBIDDEN_IN_POLICY = (
    "anthropic", "openai", "pydantic_ai", "claude_agent_sdk", "httpx", "requests",
    "sqlite3", "socket", "os", "io", "time", "datetime", "random", "pathlib",
    "importlib",
)

_PACKAGE = "chaperone"
_POLICY_PACKAGE = f"{_PACKAGE}.policy"

# The module list of an import statement: the dotted names after `import`/`from`, including a
# comma-separated series and any `as` clause on each element. Capturing the whole list rather than
# one name is what lets the fallback see `import chaperone.policy.types as t, chaperone.gates`,
# whose offending name is neither first nor adjacent to the comma the naive pattern looked for.
_IMPORT_LINE = re.compile(
    r"^[ \t]*(?:import|from)[ \t]+"
    r"([\w.]+(?:[ \t]+as[ \t]+\w+)?(?:[ \t]*,[ \t]*[\w.]+(?:[ \t]+as[ \t]+\w+)?)*)",
    re.MULTILINE,
)


def _inside_policy(module: str) -> bool:
    """True for `chaperone.policy` and its submodules, false for `chaperone.policyholder`."""
    return module == _POLICY_PACKAGE or module.startswith(f"{_POLICY_PACKAGE}.")


def _imported_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module]
    return []


def _reason(module: str) -> str | None:
    """Why `module` may not be imported from policy/, or None if it may.

    Mirrors `_reason` in tools/static_audit.py; a parity test holds the two verdicts identical.
    """
    root = module.split(".")[0]
    if root in FORBIDDEN_IN_POLICY:
        return f"refusing an import of {root!r}. Pass the value in as an argument instead."
    if root == _PACKAGE and not _inside_policy(module):
        return f"refusing a dependency on {module!r}. policy/ depends on nothing outside policy/."
    return None


def _fallback_refusal(content: str) -> str | None:
    """Check every module named by every import line, not merely the first.

    The pattern matches allowed imports as well as forbidden ones, so stopping at the first match
    would let `from chaperone.policy.types import Draft` vouch for an offending import below it.
    """
    for match in _IMPORT_LINE.finditer(content):
        for token in match.group(1).split(","):
            words = token.split()  # `os as o` -> the module is the first word, the alias is not
            if not words:
                continue
            # Leading dots make a relative import; the AST path reports `.os` as `os`, so strip
            # them here too rather than let the two paths reach different verdicts.
            reason = _reason(words[0].lstrip("."))
            if reason:
                return reason
    return None


def _refusal(content: str) -> str | None:
    """Return the reason this content may not enter policy/, or None.

    Parses content as Python and walks real import nodes, so `from X import Y` is caught (including
    multi-alias forms like `import json, os`) and prose that merely mentions a module name (a
    docstring, a comment) is not. `content` may be a fragment (an Edit's new_string need not parse
    standalone) -- an indented fragment is retried dedented, since that alone makes most same-level
    inserted lines parseable and keeps them on the precise AST path. Only if both parses fail does
    this fall back to a line-anchored regex over each import statement's module list -- every
    comma-separated element, its `as` clause dropped and any leading dots stripped -- so a
    non-import fragment is never blocked.

    The two paths must reach the same verdict, and a differential test over a corpus of import
    shapes is what holds them to it rather than this sentence. Neither path examines imported
    *symbols*, so `from x import os` is allowed by both.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        try:
            tree = ast.parse(textwrap.dedent(content))
        except SyntaxError:
            return _fallback_refusal(content)

    for node in ast.walk(tree):
        for module in _imported_modules(node):
            reason = _reason(module)
            if reason:
                return reason
    return None


def _say(line: str) -> None:
    """Write the reason, or write nothing. The exit code is what blocks; the text only explains.

    With stderr unwritable the shutdown flush fails and the process exits 120, which is neither 0
    nor 2 -- and only 2 blocks. Measured on Windows under CPython 3.11.15 and 3.13.9, and
    **unverified on Linux**, where CI runs; the test asserts its own premise so a platform that
    behaves differently fails loudly instead of passing vacuously. Swallowing the write is not enough, measured: the failed write stays
    in the stream buffer and shutdown retries it, so the handler replaces the stream. Identical to
    `tools/policy_hook.py`, and a parity test holds the two to the same treatment.
    """
    try:
        print(line, file=sys.stderr)
        sys.stderr.flush()
    except Exception:
        try:
            sys.stderr = open(os.devnull, "w")
        except Exception:
            pass


def _block(reason: str) -> int:
    _say(reason)
    return 2


def main() -> int:
    # An unreadable payload is a refusal, not an allow. This returned 0 -- so an edit arriving with
    # malformed or empty stdin was waved through unexamined, and the guard that exists to keep
    # policy/ pure had not looked at it. `tools/policy_hook.py` closed the identical line, and the
    # same defect surviving in the sibling that runs at edit time is the drift the project forbids.
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        return _block(f"refusing an edit whose payload could not be read as JSON: {exc}")

    if not isinstance(payload, dict):
        return _block(f"refusing an edit whose payload is a {type(payload).__name__}, not an object")

    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return _block(f"refusing an edit whose tool_input is a {type(tool_input).__name__}")

    path = (tool_input.get("file_path") or "").replace("\\", "/")
    content = tool_input.get("content") or tool_input.get("new_string") or ""

    if "src/chaperone/policy/" in path:
        reason = _refusal(content)
        if reason:
            return _block(f"policy/ must stay pure: {reason}")

    tokens = [t.strip().lower() for t in os.environ.get("CHAPERONE_FORBIDDEN_TOKENS", "").split(",") if t.strip()]
    lowered = content.lower()
    for token in tokens:
        if token in lowered:
            return _block(
                "refusing a write containing a forbidden organisation token. "
                "This repository describes a synthetic scenario only."
            )

    return 0


if __name__ == "__main__":
    try:
        code = main()
    # Fail-closed by construction beats enumerating exception types. Exit 1 does not block a
    # PreToolUse hook, so any raise here was an edit allowed by a guard that crashed on it.
    except BaseException as exc:
        code = _block(f"refusing an edit the guard could not evaluate: {type(exc).__name__}: {exc}")
    sys.exit(code)
