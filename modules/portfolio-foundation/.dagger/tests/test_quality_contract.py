import ast
import tomllib
from pathlib import Path


def test_should_keep_every_function_at_most_fifteen_lines() -> None:
    root = Path(__file__).parents[1] / "src"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                end = node.end_lineno or node.lineno
                assert end - node.lineno + 1 <= 15, f"{path}:{node.lineno}"


def test_should_measure_public_adapter_and_load_dagger_schema() -> None:
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    tasks = project["tool"]["poe"]["tasks"]
    assert "coverage" not in project["tool"]
    assert tasks["schema"] == "dagger -m .. functions"
    assert "schema" in tasks["gate"]


def test_should_configure_non_mutating_lint() -> None:
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    lint = project["tool"]["poe"]["tasks"]["lint"]["shell"]
    assert "--fix" not in lint
    assert "format --check" in lint
