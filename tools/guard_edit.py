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


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    path = (tool_input.get("file_path") or "").replace("\\", "/")
    content = tool_input.get("content") or tool_input.get("new_string") or ""

    if "src/chaperone/policy/" in path:
        reason = _refusal(content)
        if reason:
            print(f"policy/ must stay pure: {reason}", file=sys.stderr)
            return 2

    tokens = [t.strip().lower() for t in os.environ.get("CHAPERONE_FORBIDDEN_TOKENS", "").split(",") if t.strip()]
    lowered = content.lower()
    for token in tokens:
        if token in lowered:
            print(
                "refusing a write containing a forbidden organisation token. "
                "This repository describes a synthetic scenario only.",
                file=sys.stderr,
            )
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
