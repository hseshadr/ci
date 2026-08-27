import ast
from pathlib import Path
from typing import cast

import dagger
import pytest

from portfolio_foundation import PortfolioFoundation

MODULE = Path(__file__).parents[2]
type Parameter = tuple[str, str]
type PublicSignature = tuple[str, tuple[Parameter, ...], str]
type PublicFunction = ast.FunctionDef | ast.AsyncFunctionDef
EXPECTED_PUBLIC_SCHEMA: tuple[PublicSignature, ...] = (
    (
        "source",
        (("source", "dagger.Directory"), ("repository", "str"), ("commit_sha", "str")),
        "dagger.Directory",
    ),
    (
        "guard",
        (("source", "dagger.Directory"), ("repository", "str"), ("commit_sha", "str")),
        "dagger.Container",
    ),
    (
        "envelope",
        (("artifact", "dagger.Directory"), ("repository", "str"), ("commit_sha", "str")),
        "dagger.Directory",
    ),
    (
        "green_main",
        (("github_token", "dagger.Secret"), ("repository", "str")),
        "str",
    ),
)


def _directory() -> dagger.Directory:
    return cast(dagger.Directory, None)


def _secret() -> dagger.Secret:
    return cast(dagger.Secret, None)


def _main_tree(source: str | None = None) -> ast.Module:
    text = source or (MODULE / ".dagger/src/portfolio_foundation/main.py").read_text()
    return ast.parse(text)


def _public_methods(tree: ast.Module) -> tuple[PublicFunction, ...]:
    foundation = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    return tuple(
        node
        for node in foundation.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _is_function(node)
    )


def _is_function(node: PublicFunction) -> bool:
    decorators = (ast.unparse(decorator) for decorator in node.decorator_list)
    return any(name in {"function", "dagger.function"} for name in decorators)


def _annotation(node: ast.expr | None) -> str:
    assert node is not None
    return ast.unparse(node)


def _signature(node: PublicFunction) -> PublicSignature:
    parameters = tuple(
        (argument.arg, _annotation(argument.annotation)) for argument in node.args.args[1:]
    )
    return node.name, parameters, _annotation(node.returns)


def test_should_expose_stable_public_functions() -> None:
    schema = (MODULE / "dagger.json").read_text()
    for name in ("portfolio-foundation", "v0.21.8"):
        assert name in schema


def test_should_keep_exact_typed_dagger_public_schema() -> None:
    actual = tuple(_signature(node) for node in _public_methods(_main_tree()))
    assert actual == EXPECTED_PUBLIC_SCHEMA


@pytest.mark.parametrize("escape_hatch", ("command", "script", "cmd", "shell"))
def test_should_reject_public_arbitrary_command_escape_hatch(escape_hatch: str) -> None:
    source = (MODULE / ".dagger/src/portfolio_foundation/main.py").read_text()
    altered = source.replace("repository", escape_hatch, 1)
    schema = tuple(_signature(node) for node in _public_methods(_main_tree(altered)))
    assert schema != EXPECTED_PUBLIC_SCHEMA


def test_should_expose_source_as_async_runtime_adapter() -> None:
    source = next(node for node in _public_methods(_main_tree()) if node.name == "source")
    assert isinstance(source, ast.AsyncFunctionDef)


def test_should_expose_guard_as_async_runtime_adapter() -> None:
    guard = next(node for node in _public_methods(_main_tree()) if node.name == "guard")
    assert isinstance(guard, ast.AsyncFunctionDef)


def test_should_raise_until_envelope_adapter_is_implemented() -> None:
    with pytest.raises(NotImplementedError):
        PortfolioFoundation().envelope(_directory(), "owner/repository", "a" * 40)


def test_should_raise_until_green_main_adapter_is_implemented() -> None:
    with pytest.raises(NotImplementedError):
        PortfolioFoundation().green_main(_secret(), "owner/repository")
