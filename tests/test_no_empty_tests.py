import ast
from pathlib import Path

TESTS_ROOT = Path(__file__).parent


def _asserts_something(fn: ast.FunctionDef) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"raises", "warns", "approx", "fail"}:
                return True
    return False


def test_every_test_function_asserts_something():
    offenders = []
    for path in TESTS_ROOT.rglob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                if not _asserts_something(node):
                    offenders.append(f"{path.name}::{node.name}")
    assert offenders == [], f"tests with no assertion: {offenders}"
