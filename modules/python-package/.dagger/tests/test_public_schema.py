from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

MODULE = Path(__file__).parents[2]
MAIN = MODULE / ".dagger/src/python_package/main.py"
type Signature = tuple[str, tuple[tuple[str, str], ...], str]

EXPECTED: tuple[Signature, ...] = (
    (
        "dependency_audit",
        (("source", "dagger.Directory"), ("repository", "str"), ("commit_sha", "str")),
        "dagger.Container",
    ),
    (
        "build",
        (
            ("source", "dagger.Directory"),
            ("repository", "str"),
            ("commit_sha", "str"),
            ("project_name", "str"),
        ),
        "BuiltPythonPackage",
    ),
    (
        "candidate",
        (
            ("source", "dagger.Directory"),
            ("github_token", "dagger.Secret"),
            ("repository", "str"),
            ("commit_sha", "str"),
            ("project_name", "str"),
            ("central_module_sha", "str"),
            ("workflow_run_id", "str"),
            ("run_attempt", "int"),
        ),
        "PythonPackageCandidate",
    ),
    (
        "verify_candidate",
        (
            ("envelope", "dagger.Directory"),
            ("repository", "str"),
            ("commit_sha", "str"),
            ("project_name", "str"),
            ("central_module_sha", "str"),
            ("workflow_run_id", "str"),
            ("run_attempt", "int"),
        ),
        "PythonPackageCandidate",
    ),
)


def test_should_keep_exact_typed_public_schema() -> None:
    # Given the module's decorated Dagger functions
    functions = _public_functions(ast.parse(MAIN.read_text()))

    # When their signatures are normalized
    actual = tuple(_signature(item) for item in functions)

    # Then consumers receive a closed stable API without generic execution controls
    assert actual == EXPECTED


def test_should_disable_cache_for_live_candidate_evidence() -> None:
    # Given the provider-backed candidate operation
    candidate = next(item for item in _public_functions(_tree()) if item.name == "candidate")

    # When its Dagger cache policy is inspected
    decorator = next(item for item in candidate.decorator_list if isinstance(item, ast.Call))
    cache = next(item.value for item in decorator.keywords if item.arg == "cache")

    # Then GitHub evidence cannot be satisfied by a stale function result
    assert isinstance(cache, ast.Constant) and cache.value == "never"


def test_should_keep_static_probe_bootstrap_free_of_dagger_runtime() -> None:
    # Given the package is imported before the static probe submodule
    script = "import sys; import python_package; assert 'python_package.main' not in sys.modules"

    # When / Then package initialization does not load the Dagger application graph
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True)


def _tree() -> ast.Module:
    return ast.parse(MAIN.read_text())


def _public_functions(tree: ast.Module) -> tuple[ast.AsyncFunctionDef, ...]:
    package = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PythonPackage"
    )
    result: list[ast.AsyncFunctionDef] = []
    for node in package.body:
        if isinstance(node, ast.AsyncFunctionDef) and _decorated(node):
            result.append(node)
    return tuple(result)


def _decorated(node: ast.AsyncFunctionDef) -> bool:
    return any("function" in ast.unparse(item) for item in node.decorator_list)


def _signature(node: ast.AsyncFunctionDef) -> Signature:
    parameters = tuple(_parameter(argument) for argument in node.args.args[1:])
    assert node.returns is not None
    return node.name, parameters, ast.unparse(node.returns)


def _parameter(argument: ast.arg) -> tuple[str, str]:
    assert argument.annotation is not None
    return argument.arg, ast.unparse(argument.annotation)
