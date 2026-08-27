from importlib import import_module
from pathlib import Path

MODULE = Path(__file__).parents[2]


def test_should_expose_stable_public_functions() -> None:
    schema = (MODULE / "dagger.json").read_text()
    for name in ("portfolio-foundation", "v0.21.8"):
        assert name in schema


def test_should_reject_public_arbitrary_command_escape_hatch() -> None:
    source = (MODULE / ".dagger/src/portfolio_foundation/main.py").read_text()
    assert "command: str" not in source
    assert "script: str" not in source


def test_should_expose_only_typed_foundation_entrypoints() -> None:
    main = MODULE / ".dagger/src/portfolio_foundation/main.py"
    assert main.is_file()
    entrypoints = ("source", "guard", "envelope", "green_main")
    foundation = import_module("portfolio_foundation").PortfolioFoundation
    assert all(callable(getattr(foundation, entrypoint, None)) for entrypoint in entrypoints)
