import asyncio
from typing import cast

import dagger
import pytest

from portfolio_foundation import main as main_module
from portfolio_foundation import source as source_module
from portfolio_foundation.identity import CommitIdentity, FullSha, RepositoryRef
from portfolio_foundation.source import (
    EntryType,
    InventoryEntry,
    SourceMismatch,
    bind_source,
    canonical_inventory,
    manifest_sha256,
    require_same_inventory,
)


class FakeInventory:
    def __init__(self, entries: tuple[InventoryEntry, ...]) -> None:
        self._entries = entries

    async def entries(self) -> tuple[InventoryEntry, ...]:
        return self._entries


class FakeStat:
    def __init__(self, file_type: dagger.FileType) -> None:
        self._file_type = file_type

    async def file_type(self) -> dagger.FileType:
        return self._file_type


class FakeDaggerDirectory:
    async def entries(self, *, path: str | None = None) -> list[str]:
        return ["app.ts"] if path == "src" else ["src/"]

    def stat(self, path: str, *, do_not_follow_symlinks: bool = False) -> FakeStat:
        assert do_not_follow_symlinks
        file_type = dagger.FileType.DIRECTORY if path == "src" else dagger.FileType.REGULAR
        return FakeStat(file_type)


def test_should_reject_workspace_when_inventory_differs_from_exact_commit() -> None:
    # Given
    expected = ("dagger.json:abc", "src/app.ts:def")
    actual = ("dagger.json:abc", "src/app.ts:changed")

    # When / Then
    with pytest.raises(SourceMismatch, match=r"src/app\.ts"):
        require_same_inventory(expected, actual)


def test_should_accept_workspace_when_inventory_matches_exact_commit() -> None:
    # Given
    inventory = ("dagger.json:abc",)

    # When
    require_same_inventory(inventory, inventory)

    # Then
    assert True


def test_should_reject_duplicate_paths_when_inventory_is_ambiguous() -> None:
    inventory = FakeInventory(
        (
            InventoryEntry("same", "a" * 64, EntryType.REGULAR),
            InventoryEntry("same", "b" * 64, EntryType.REGULAR),
        )
    )
    with pytest.raises(SourceMismatch, match="duplicate"):
        asyncio.run(canonical_inventory(inventory))


@pytest.mark.parametrize(
    ("file_type", "expected"),
    ((dagger.FileType.SYMLINK, EntryType.SYMLINK), (None, EntryType.UNKNOWN)),
)
def test_should_classify_unsupported_dagger_nodes(
    file_type: dagger.FileType | None, expected: EntryType
) -> None:
    assert source_module._entry_type(file_type) is expected


def test_should_keep_source_and_history_as_distinct_inputs_when_bound() -> None:
    # Given
    fake_source = object()
    fake_history = object()
    manifest = ("dagger.json:abc",)

    # When
    identity = CommitIdentity(RepositoryRef("owner", "repository"), FullSha("a" * 40))
    binding = bind_source(fake_source, fake_history, identity, manifest)

    # Then
    assert binding.source is fake_source
    assert binding.history is fake_history
    assert binding.identity == identity


def test_should_create_stable_manifest_hash_when_inventory_order_differs() -> None:
    # Given
    unordered = ("src/app.ts:def", "dagger.json:abc")
    ordered = ("dagger.json:abc", "src/app.ts:def")

    # When
    unordered_hash = manifest_sha256(unordered)

    # Then
    assert unordered_hash == manifest_sha256(ordered)


def test_should_distinguish_manifest_collision_when_path_contains_separator() -> None:
    first = (f"x:{'a' * 64}\ny:{'b' * 64}",)
    second = (f"x:{'a' * 64}", f"y:{'b' * 64}")
    assert manifest_sha256(first) != manifest_sha256(second)


def test_should_reject_unexpected_file_when_workspace_has_more_files() -> None:
    # Given
    expected = ("dagger.json:abc",)
    actual = ("dagger.json:abc", "notes.txt:def")

    # When / Then
    with pytest.raises(SourceMismatch, match=r"notes\.txt"):
        require_same_inventory(expected, actual)


def test_should_reject_symlink_when_hosted_inventory_contains_one() -> None:
    # Given
    inventory = FakeInventory((InventoryEntry("linked-source", "a" * 64, EntryType.SYMLINK),))

    # When / Then
    with pytest.raises(SourceMismatch, match="linked-source"):
        asyncio.run(canonical_inventory(inventory))


def test_should_sort_relative_paths_when_creating_inventory() -> None:
    # Given
    inventory = FakeInventory(
        (
            InventoryEntry("src/app.ts", "b" * 64, EntryType.REGULAR),
            InventoryEntry("dagger.json", "a" * 64, EntryType.REGULAR),
        )
    )

    # When
    manifest = asyncio.run(canonical_inventory(inventory))

    # Then
    assert manifest == (f"dagger.json:{'a' * 64}", f"src/app.ts:{'b' * 64}")


def test_should_omit_directories_when_creating_dagger_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    directory = cast(dagger.Directory, FakeDaggerDirectory())

    async def fake_sha256(_: dagger.Directory, __: str) -> str:
        return "a" * 64

    monkeypatch.setattr(source_module, "_dagger_sha256", fake_sha256)

    # When
    manifest = asyncio.run(canonical_inventory(source_module.DaggerSourceInventory(directory)))

    # Then
    assert manifest == (f"src/app.ts:{'a' * 64}",)


def test_should_reject_invalid_inventory_entry_when_path_is_unsafe() -> None:
    with pytest.raises(ValueError, match="relative path"):
        InventoryEntry("../escape", "a" * 64, EntryType.REGULAR)


def test_should_delegate_source_when_identity_is_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    source = cast(dagger.Directory, object())
    history = cast(dagger.Directory, object())

    class FakeGit:
        def commit(self, _: str) -> "FakeGit":
            return self

        def tree(self, *, depth: int, include_tags: bool) -> dagger.Directory:
            assert (depth, include_tags) == (0, True)
            return history

    class FakeDag:
        def git(self, _: str) -> FakeGit:
            return FakeGit()

    async def bind(_: dagger.Directory, __: dagger.Directory, ___: CommitIdentity) -> object:
        return type("Binding", (), {"source": source})()

    monkeypatch.setattr(main_module, "dag", FakeDag())
    monkeypatch.setattr(main_module, "bind_dagger_source", bind)

    # When
    foundation = main_module.PortfolioFoundation()
    result: dagger.Directory = asyncio.run(foundation.source(source, "owner/repository", "a" * 40))

    # Then
    assert result is source
