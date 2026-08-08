import ast
from pathlib import Path

TESTS_ROOT = Path(__file__).parent


#: Both function forms. `ast.FunctionDef` alone does not match `async def`, so an async test would
#: have been outside this guard entirely -- latent today, since no test here is async, and closed
#: rather than left to the commit that adds one. `tests/gates/test_engine.py` already walks the pair.
FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _asserts_something(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
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
            if isinstance(node, FUNCTION_NODES) and node.name.startswith("test_"):
                if not _asserts_something(node):
                    offenders.append(f"{path.name}::{node.name}")
    assert offenders == [], f"tests with no assertion: {offenders}"


def test_an_async_test_with_no_assertion_is_caught_too():
    """The guard above is green on a clean tree whether or not it reads `async def`.

    So the node coverage is exercised directly rather than inferred. Without
    `ast.AsyncFunctionDef` in `FUNCTION_NODES` this fails, and the sweep above would have walked
    past every async test in the suite the day one was written.
    """
    tree = ast.parse("async def test_nothing():\n    pass\n")
    found = [n for n in ast.walk(tree) if isinstance(n, FUNCTION_NODES)]
    assert [n.name for n in found] == ["test_nothing"], "an async test is invisible to the walk"
    assert not _asserts_something(found[0])
