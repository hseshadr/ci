from __future__ import annotations

import asyncio
import re
from typing import cast

import dagger
import pytest

from portfolio_foundation import guard as guard_module
from portfolio_foundation import main as main_module
from portfolio_foundation.guard import (
    ACTIONLINT_IMAGE,
    GITLEAKS_IMAGE,
    actionlint_command,
    build_guard,
    secret_scan_command,
)
from portfolio_foundation.identity import CommitIdentity, FullSha, RepositoryRef
from portfolio_foundation.source import SourceBinding


class FakeDirectory:
    def __init__(self, snapshot: dagger.Directory | None = None) -> None:
        self.snapshot = snapshot

    def without_directory(self, path: str) -> dagger.Directory:
        assert path == ".git"
        assert self.snapshot is not None
        return self.snapshot


class FakeContainer:
    def __init__(self, operations: list[tuple[object, ...]]) -> None:
        self.operations = operations

    def from_(self, image: str) -> FakeContainer:
        self.operations.append(("from", image))
        return self

    def file(self, path: str) -> dagger.File:
        self.operations.append(("file", path))
        return cast(dagger.File, object())

    def with_entrypoint(self, entrypoint: list[str]) -> FakeContainer:
        self.operations.append(("entrypoint", tuple(entrypoint)))
        return self

    def with_file(self, path: str, file: dagger.File) -> FakeContainer:
        self.operations.append(("with_file", path, file))
        return self

    def with_mounted_directory(
        self, path: str, directory: dagger.Directory, *, read_only: bool
    ) -> FakeContainer:
        self.operations.append(("mount", path, directory, read_only))
        return self

    def with_exec(self, command: list[str]) -> FakeContainer:
        self.operations.append(("exec", tuple(command)))
        return self


class FakeDag:
    def __init__(self, operations: list[tuple[object, ...]]) -> None:
        self.operations = operations

    def container(self) -> FakeContainer:
        return FakeContainer(self.operations)


def test_should_scan_both_workflow_extensions() -> None:
    # Given
    command = actionlint_command()

    # When
    workflow_patterns = tuple(re.findall(r"\*\.ya?ml", command))

    # Then
    assert frozenset(workflow_patterns) == frozenset(("*.yml", "*.yaml"))


def test_should_require_nonempty_workflow_input() -> None:
    # Given
    command = actionlint_command()

    # When / Then
    assert "test -n" in command
    assert command.index("test -n") < command.index("actionlint")


def test_should_require_runtime_canary_detection_before_real_scan() -> None:
    # Given
    command = secret_scan_command("a" * 40)

    # When / Then
    assert command.index("/canary") < command.index("/snapshot")
    assert "--redact" in command
    assert "--exit-code" in command


def test_should_generate_canary_without_committing_detector_value() -> None:
    # Given
    command = secret_scan_command("a" * 40)

    # When
    committed_canary = re.search(r"ghp_[A-Za-z0-9]{36}", command)

    # Then
    assert committed_canary is None
    assert "git init" in command


def test_should_reject_empty_or_shallow_canonical_history() -> None:
    # Given
    command = secret_scan_command("b" * 40)

    # When / Then
    assert "test -d /repo/.git" in command
    assert "--is-shallow-repository" in command
    assert "rev-list --all" in command
    assert "b" * 40 in command


def test_should_scan_nonempty_snapshot_and_all_canonical_history() -> None:
    # Given
    command = secret_scan_command("c" * 40)

    # When / Then
    assert "find /snapshot -type f -print -quit" in command
    assert "--source /snapshot --no-git" in command
    assert "--source /repo --log-opts=--all" in command


def test_should_apply_repository_config_to_snapshot_and_history() -> None:
    # Given
    command = secret_scan_command("c" * 40)

    # When / Then
    assert "test -f /snapshot/.gitleaks.toml" in command
    assert "--config /snapshot/.gitleaks.toml" in command
    assert "test -f /repo/.gitleaks.toml" in command
    assert "--config /repo/.gitleaks.toml" in command


def test_should_pin_every_guard_tool_image_by_digest() -> None:
    # Given
    images = (ACTIONLINT_IMAGE, GITLEAKS_IMAGE)

    # When
    immutable = tuple("@sha256:" in image for image in images)

    # Then
    assert all(immutable)


def test_should_build_guard_from_complete_verified_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    operations: list[tuple[object, ...]] = []
    snapshot = cast(dagger.Directory, object())
    source = cast(dagger.Directory, FakeDirectory(snapshot))
    history = cast(dagger.Directory, object())
    identity = CommitIdentity(RepositoryRef("owner", "repository"), FullSha("d" * 40))
    binding = SourceBinding(source, history, identity, "e" * 64)
    monkeypatch.setattr(guard_module, "dag", FakeDag(operations))

    # When
    result = build_guard(binding)

    # Then
    assert isinstance(result, FakeContainer)
    assert ("mount", "/snapshot", snapshot, True) in operations
    assert ("mount", "/repo", history, True) in operations
    assert "d" * 40 in str(operations[-1])


def test_should_delegate_public_guard_with_full_source_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    source = cast(dagger.Directory, object())
    history = cast(dagger.Directory, object())
    identity = CommitIdentity(RepositoryRef("owner", "repository"), FullSha("f" * 40))
    binding = SourceBinding(source, history, identity, "0" * 64)
    expected = cast(dagger.Container, object())

    async def bind(_: dagger.Directory, __: str, ___: str) -> object:
        return binding

    def guard(actual: object) -> dagger.Container:
        assert actual is binding
        return expected

    monkeypatch.setattr(main_module, "_source_binding", bind)
    monkeypatch.setattr(main_module, "build_guard", guard)

    # When
    result: dagger.Container = asyncio.run(
        main_module.PortfolioFoundation().guard(source, "owner/repository", "f" * 40)
    )

    # Then
    assert result is expected
