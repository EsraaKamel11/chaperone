"""PreToolUse guard. Exit 2 blocks the edit and feeds the reason back to the model.

This is the project's own thesis applied to its construction: the two constraints that must never
be violated are enforced deterministically, at the layer where the action happens, rather than
stated as an instruction and hoped for.
"""
from __future__ import annotations

import json
import os
import sys

FORBIDDEN_IN_POLICY = (
    "anthropic", "openai", "pydantic_ai", "claude_agent_sdk", "httpx", "requests",
    "sqlite3", "socket", "os", "io", "time", "datetime", "random", "pathlib",
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    path = (tool_input.get("file_path") or "").replace("\\", "/")
    content = tool_input.get("content") or tool_input.get("new_string") or ""

    if "src/chaperone/policy/" in path:
        for module in FORBIDDEN_IN_POLICY:
            if f"import {module}" in content:
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
