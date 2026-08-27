import ast
from pathlib import Path


def test_should_keep_every_function_at_most_fifteen_lines() -> None:
    root = Path(__file__).parents[1] / "src"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                end = node.end_lineno or node.lineno
                assert end - node.lineno + 1 <= 15, f"{path}:{node.lineno}"
