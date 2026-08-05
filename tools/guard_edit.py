"""PreToolUse guard. Exit 2 blocks the edit and feeds the reason back to the model.

This is the project's own thesis applied to its construction: the two constraints that must never
be violated are enforced deterministically, at the layer where the action happens, rather than
stated as an instruction and hoped for.
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
)

_IMPORT_LINE = re.compile(
    r"^\s*(?:import|from)\s+(" + "|".join(re.escape(m) for m in FORBIDDEN_IN_POLICY) + r")\b",
    re.MULTILINE,
)


def _forbidden_import(content: str) -> str | None:
    """Return the forbidden module root actually imported by content, or None.

    Parses content as Python and walks real import nodes, so `from X import Y` is caught (including
    multi-alias forms like `import json, os`) and prose that merely mentions a module name (a
    docstring, a comment) is not. `content` may be a fragment (an Edit's new_string need not parse
    standalone) — an indented fragment is retried dedented, since that alone makes most same-level
    inserted lines parseable and keeps them on the precise AST path. Only if both parses fail does
    this fall back to a line-anchored regex, so a non-import fragment is never blocked.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        try:
            tree = ast.parse(textwrap.dedent(content))
        except SyntaxError:
            match = _IMPORT_LINE.search(content)
            return match.group(1) if match else None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_IN_POLICY:
                    return root
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root in FORBIDDEN_IN_POLICY:
                    return root
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
        module = _forbidden_import(content)
        if module:
            print(
                f"policy/ must stay pure: refusing an import of {module!r}. "
                "Pass the value in as an argument instead.",
                file=sys.stderr,
            )
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
