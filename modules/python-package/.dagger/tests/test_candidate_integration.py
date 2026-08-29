from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from python_package.distribution_probe import inspect_directory
from python_package.distributions import DistributionSet

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("name", "project", "version"),
    (
        ("static_package", "static-candidate", "1.2.3"),
        ("dynamic_hatch_package", "dynamic-candidate", "2.3.4"),
    ),
)
def test_should_build_and_verify_real_static_and_dynamic_packages(
    tmp_path: Path, name: str, project: str, version: str
) -> None:
    # Given a clean copy of a realistic locked package consumer
    source = tmp_path / name
    shutil.copytree(FIXTURES / name, source)
    output = tmp_path / f"{name}-dist"

    # When the official project builder creates both distributions
    _build_with_locked_backend(source, output)
    (output / ".gitignore").unlink()
    products = DistributionSet.parse(inspect_directory(output), project)

    # Then static and dynamic metadata produce the same closed release shape
    assert products.project == project
    assert products.version == version
    assert products.tag == f"v{version}"
    assert products.wheel.filename.endswith("-py3-none-any.whl")


def _build_with_locked_backend(source: Path, output: Path) -> None:
    sync = ("uv", "sync", "--frozen", "--all-extras", "--all-groups", "--no-install-project")
    _run(sync, source)
    environment = os.environ | {
        "PATH": f"{source / '.venv' / 'bin'}:{os.environ['PATH']}",
        "VIRTUAL_ENV": str(source / ".venv"),
    }
    command = ("uv", "build", "--no-sources", "--no-build-isolation", "--out-dir", str(output))
    _run(command, source, environment)


def _run(command: tuple[str, ...], source: Path, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=source, env=env, check=True, capture_output=True, text=True)
