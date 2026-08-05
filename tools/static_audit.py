"""Static audits that make policy purity and send containment structural facts, not claims.

Two audits, both parsing with `ast` rather than grepping text:

- `audit_policy_purity`: `policy/` imports no LLM client, no I/O and no clock, and depends on
  nothing outside `policy/`.
- `audit_send_references`: the send symbol is referenced only from the gateway module.

Both fail loud on a missing or empty root. An audit that examined no files reports that as a
violation rather than reporting clean, so a renamed directory cannot disarm the guard in silence.

These are denylists over a statically visible import graph, not a sandbox. A determined evader
beats any AST check: `__import__("time")`, a relative import escaping upward, the builtin
`open()`, a module absent from the denylist, `getattr(module, "transmit")` and a shadowing
redefinition all pass. The job here is to make an accidental violation impossible and a
deliberate one conspicuous -- not to prove that none exists.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

FORBIDDEN_IN_POLICY = {
    "anthropic", "openai", "pydantic_ai", "claude_agent_sdk", "httpx", "requests",
    "sqlite3", "socket", "pathlib", "os", "io", "time", "datetime", "random",
    "importlib",
}

_PACKAGE = "chaperone"
_POLICY_PACKAGE = f"{_PACKAGE}.policy"


def _imported_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module]
    return []


def _inside_policy(module: str) -> bool:
    """True for `chaperone.policy` itself and its submodules, false for `chaperone.policyholder`."""
    return module == _POLICY_PACKAGE or module.startswith(f"{_POLICY_PACKAGE}.")


def _files_to_audit(root: Path, what: str) -> tuple[list[Path], list[str]]:
    """The Python files under `root`, or a violation explaining why there are none.

    An audit that examined no files must never report clean. A renamed, moved or mistyped root
    would otherwise disarm the guard in silence and leave CI green over an enforcement that ran
    on nothing -- the fail-open failure this tool exists to prevent.
    """
    if not root.is_dir():
        return [], [f"{root}: {what} root not found -- audited nothing"]
    paths = sorted(root.rglob("*.py"))
    if not paths:
        return [], [f"{root}: no Python files under the {what} root -- audited nothing"]
    return paths, []


def audit_policy_purity(package_root: Path) -> list[str]:
    paths, violations = _files_to_audit(package_root, "policy")
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            for module in _imported_modules(node):
                root = module.split(".")[0]
                if root in FORBIDDEN_IN_POLICY:
                    violations.append(f"{path}:{node.lineno}: policy/ imports {root!r}")
                elif root == _PACKAGE and not _inside_policy(module):
                    violations.append(f"{path}:{node.lineno}: policy/ depends on {module!r} outside policy/")
    return violations


def audit_send_references(src_root: Path, send_symbol: str, allowed_module: str) -> list[str]:
    allowed_path = src_root.joinpath(*allowed_module.split(".")).with_suffix(".py")
    paths, violations = _files_to_audit(src_root, "source")
    for path in paths:
        if path == allowed_path:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(a.name == send_symbol for a in node.names):
                violations.append(f"{path}:{node.lineno}: imports {send_symbol!r} outside {allowed_module}")
            elif (isinstance(node, ast.Name) and node.id == send_symbol) or (
                isinstance(node, ast.Attribute) and node.attr == send_symbol
            ):
                violations.append(f"{path}:{node.lineno}: references {send_symbol!r} outside {allowed_module}")
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
