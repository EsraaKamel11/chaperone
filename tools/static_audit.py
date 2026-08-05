from __future__ import annotations

import ast
import sys
from pathlib import Path

FORBIDDEN_IN_POLICY = {
    "anthropic", "openai", "pydantic_ai", "claude_agent_sdk", "httpx", "requests",
    "sqlite3", "socket", "pathlib", "os", "io", "time", "datetime", "random",
}


def _module_roots(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name.split(".")[0] for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module.split(".")[0]]
    return []


def audit_policy_purity(package_root: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            for root in _module_roots(node):
                if root in FORBIDDEN_IN_POLICY:
                    violations.append(f"{path}:{node.lineno}: policy/ imports {root!r}")
    return violations


def audit_send_references(src_root: Path, send_symbol: str, allowed_module: str) -> list[str]:
    allowed_suffix = allowed_module.replace(".", "/") + ".py"
    violations: list[str] = []
    for path in sorted(src_root.rglob("*.py")):
        if str(path).replace("\\", "/").endswith(allowed_suffix):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(a.name == send_symbol for a in node.names):
                violations.append(f"{path}:{node.lineno}: imports {send_symbol!r} outside {allowed_module}")
                break
            elif isinstance(node, ast.Name) and node.id == send_symbol:
                violations.append(f"{path}:{node.lineno}: references {send_symbol!r} outside {allowed_module}")
                break
    return violations


def main() -> int:
    src = Path(__file__).resolve().parents[1] / "src" / "chaperone"
    violations = audit_policy_purity(src / "policy")
    violations += audit_send_references(src, send_symbol="transmit", allowed_module="audit.gateway")
    for line in violations:
        print(line)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
