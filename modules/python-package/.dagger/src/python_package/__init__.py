"""Closed Dagger contracts for auditable Python package candidates."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .main import PythonPackage

__all__ = ("PythonPackage",)


def __getattr__(name: str) -> type[PythonPackage]:
    """Load the Dagger root only when its configured entry point requests it."""
    if name != "PythonPackage":
        raise AttributeError(name)
    from .main import PythonPackage  # noqa: PLC0415 -- keep static probe stdlib-only

    return PythonPackage
