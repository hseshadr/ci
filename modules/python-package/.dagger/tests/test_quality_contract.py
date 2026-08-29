from __future__ import annotations

import ast
import tomllib
from pathlib import Path

MODULE = Path(__file__).parents[1]


def test_should_keep_every_source_function_at_most_fifteen_lines() -> None:
    # Given every Python function shipped by the package Lego
    functions = _source_functions()

    # When their physical sizes are measured
    oversized = tuple(_function_size(item) for item in functions if _function_size(item)[1] > 15)

    # Then orchestration remains decomposed into reviewable units
    assert oversized == ()


def test_should_enforce_python_quality_schema_and_audit_gates() -> None:
    # Given the module-owned quality contract
    project = tomllib.loads((MODULE / "pyproject.toml").read_text())
    tasks = project["tool"]["poe"]["tasks"]

    # When release gates are inventoried
    gate = tuple(tasks["gate"])

    # Then quality, branch coverage, and generated schema are mandatory
    assert gate == ("lint", "typecheck", "complexity", "test", "branchrate", "schema")
    assert tasks["audit"] == "pip-audit"
    assert "--cov-branch" in tasks["test"]


def _source_functions() -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]:
    result: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for path in (MODULE / "src").rglob("*.py"):
        tree = ast.parse(path.read_text())
        result.extend(
            item
            for item in ast.walk(tree)
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
    return tuple(result)


def _function_size(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, int]:
    end = node.end_lineno or node.lineno
    return node.name, end - node.lineno + 1
