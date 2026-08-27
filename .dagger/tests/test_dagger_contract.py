from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import cast

import dagger
import pytest

from ci import main as main_module
from ci.main import PYTHON_IMAGE, SOURCE_EXCLUDES, Ci

ROOT = Path(__file__).parents[2]


class FakeSync:
    async def sync(self) -> None:
        return None


class FakeFoundation:
    def __init__(self) -> None:
        self.guard_call: tuple[dagger.Directory, str, str] | None = None

    def guard(self, source: dagger.Directory, repository: str, commit_sha: str) -> FakeSync:
        self.guard_call = (source, repository, commit_sha)
        return FakeSync()


class FakeGitRef:
    async def commit(self) -> str:
        return "b" * 40


class FakeGitRepository:
    def branch(self, name: str) -> FakeGitRef:
        assert name == "main"
        return FakeGitRef()


class FakeDag:
    def __init__(self, foundation: FakeFoundation) -> None:
        self._foundation = foundation

    def foundation(self) -> FakeFoundation:
        return self._foundation

    def git(self, url: str) -> FakeGitRepository:
        assert url == "https://github.com/hseshadr/ci.git"
        return FakeGitRepository()


def test_should_require_explicit_workspace_and_typed_fleet_secret() -> None:
    # Given the public central Dagger object
    create = inspect.signature(Ci.create)
    ci = inspect.signature(Ci.ci)
    fleet = inspect.signature(Ci.fleet)

    # When its source and hosted credential boundaries are inspected
    workspace_type = str(create.parameters["workspace"].annotation)
    token_type = str(fleet.parameters["github_token"].annotation)
    ci_token_type = str(ci.parameters["github_token"].annotation)

    # Then workspace bytes and the fleet credential are explicit typed inputs
    assert "Workspace" in workspace_type
    assert "Secret" in token_type
    assert "Secret" in ci_token_type
    assert "include_central" in fleet.parameters


def test_should_pin_every_base_image_by_digest() -> None:
    # Given every container image executing repository-authored policy
    images = (PYTHON_IMAGE,)

    # When immutable identity is checked
    pinned = tuple("@sha256:" in image for image in images)

    # Then no mutable image tag controls CI execution
    assert all(pinned)


def test_should_give_zizmor_explicit_workflow_inputs() -> None:
    # Given Zizmor's Dagger adapter
    adapter = inspect.getsource(Ci._zizmor)

    # When its audit inputs are inspected
    required_inputs = ("../.github/workflows", "../.github/dependabot.yml")

    # Then collection cannot silently depend on repository-root discovery
    assert all(path in adapter for path in required_inputs)
    assert '"--min-severity", "medium"' in " ".join(adapter.split())


def test_should_not_require_generated_sdk_inside_explicit_source() -> None:
    # Given hosted Workspace bytes, which never contain Dagger's generated SDK
    repository = inspect.getsource(Ci._repository)

    # When the inner Python environment is assembled
    generated_sdk = ("current_module", 'directory("sdk")', '"/src/.dagger/sdk"')

    # Then caller source stays explicit while Dagger supplies its generated toolchain
    assert ".dagger/sdk" in SOURCE_EXCLUDES
    assert all(fragment in repository for fragment in generated_sdk)
    assert '"--frozen"' in repository
    assert '"--all-groups"' in repository


def test_should_install_foundation_from_exact_local_module() -> None:
    # Given
    config = json.loads((ROOT / "dagger.json").read_text())

    # When
    dependency = next(
        item for item in config.get("dependencies", ()) if item["name"] == "foundation"
    )

    # Then
    assert dependency == {"name": "foundation", "source": "modules/portfolio-foundation"}
    assert "@" not in dependency["source"]


def test_should_delegate_security_guard_with_exact_source_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    source = cast(dagger.Directory, object())
    shared = FakeFoundation()
    central = Ci.__new__(Ci)
    central.source = source
    monkeypatch.setattr(main_module, "dag", FakeDag(shared))
    monkeypatch.setattr(central, "_dependency_audit", FakeSync)
    monkeypatch.setattr(central, "_zizmor", lambda _: FakeSync())

    # When
    asyncio.run(central._security("a" * 40, cast(dagger.Secret, object())))

    # Then
    assert shared.guard_call == (source, "hseshadr/ci", "a" * 40)


def test_should_preserve_main_resolution_when_commit_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    source = cast(dagger.Directory, object())
    shared = FakeFoundation()
    central = Ci.__new__(Ci)
    central.source = source
    monkeypatch.setattr(main_module, "dag", FakeDag(shared))

    # When
    asyncio.run(central._repository_guard(""))

    # Then
    assert shared.guard_call == (source, "hseshadr/ci", "b" * 40)
