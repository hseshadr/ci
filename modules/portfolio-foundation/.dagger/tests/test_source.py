import asyncio
import base64
import hashlib
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


SCALE_DEPTH = 12
SCALE_WIDTH = 512
SCALE_BYTES = b"hosted-scale-regression"
SCALE_DIGEST = hashlib.sha256(SCALE_BYTES).hexdigest()
INVENTORY_TRAILER = "portfolio-foundation-inventory-v1"


class ScaleQueryCounter:
    def __init__(self) -> None:
        self.terminals = 0

    def record(self) -> None:
        self.terminals += 1


class FakeScaleStat:
    def __init__(self, file_type: dagger.FileType, counter: ScaleQueryCounter) -> None:
        self._file_type = file_type
        self._counter = counter

    async def file_type(self) -> dagger.FileType:
        self._counter.record()
        return self._file_type


class FakeScaleDirectory:
    def __init__(self, counter: ScaleQueryCounter) -> None:
        self._counter = counter

    async def entries(self, *, path: str | None = None) -> list[str]:
        self._counter.record()
        if path is None:
            return ["level-00/"]
        if path == _deep_directory(SCALE_DEPTH - 1):
            return [f"file-{index:04}.bin" for index in range(SCALE_WIDTH)]
        return [f"level-{path.count('/') + 1:02}/"]

    def stat(self, path: str, *, do_not_follow_symlinks: bool = False) -> FakeScaleStat:
        assert do_not_follow_symlinks
        file_type = dagger.FileType.REGULAR if path.endswith(".bin") else dagger.FileType.DIRECTORY
        return FakeScaleStat(file_type, self._counter)


class FakeScaleContainer:
    def __init__(self, counter: ScaleQueryCounter, output: str | None) -> None:
        self._counter = counter
        self._output = output
        self._args: list[str] = []

    def from_(self, image: str) -> "FakeScaleContainer":
        assert image == source_module.HASH_IMAGE
        return self

    def with_mounted_directory(
        self, path: str, __: dagger.Directory, *, read_only: bool
    ) -> "FakeScaleContainer":
        assert (path, read_only) == ("/source", True)
        return self

    def with_exec(self, args: list[str]) -> "FakeScaleContainer":
        assert args == ["sh", "-ec", source_module.INVENTORY_SCRIPT]
        self._args = args
        return self

    async def stdout(self) -> str:
        self._counter.record()
        return self._output or _scale_inventory_output()


class FakeScaleDag:
    def __init__(self, counter: ScaleQueryCounter, output: str | None = None) -> None:
        self._counter = counter
        self._output = output

    def container(self) -> FakeScaleContainer:
        return FakeScaleContainer(self._counter, self._output)


def _deep_directory(depth: int) -> str:
    return "/".join(f"level-{index:02}" for index in range(depth + 1))


def _scale_inventory_output() -> str:
    directories = tuple(
        _inventory_record("directory", _deep_directory(index)) for index in range(SCALE_DEPTH)
    )
    parent = _deep_directory(SCALE_DEPTH - 1)
    files = tuple(
        _inventory_record("regular", f"{parent}/file-{index:04}.bin")
        for index in range(SCALE_WIDTH)
    )
    return _inventory_stream(*directories, *files)


def _inventory_record(entry_type: str, path: str, permissions: int = 0o644) -> str:
    digest = SCALE_DIGEST if entry_type == "regular" else "0" * 64
    encoded = base64.b64encode(path.encode()).decode()
    return f"{entry_type}\t{permissions:o}\t{digest}\t{encoded}\n"


def _inventory_stream(*records: str) -> str:
    content = "".join(records)
    digest = hashlib.sha256(content.encode()).hexdigest()
    return f"{content}{INVENTORY_TRAILER}\t{len(records)}\t{digest}\n"


def _small_inventory_stream() -> str:
    directory = _inventory_record("directory", "src")
    regular = _inventory_record("regular", "src/app.ts")
    return _inventory_stream(directory, regular)


def test_should_reject_workspace_when_inventory_differs_from_exact_commit() -> None:
    # Given
    expected = ("dagger.json:644:abc", "src/app.ts:644:def")
    actual = ("dagger.json:644:abc", "src/app.ts:644:changed")

    # When / Then
    with pytest.raises(SourceMismatch, match=r"src/app\.ts"):
        require_same_inventory(expected, actual)


def test_should_accept_workspace_when_inventory_matches_exact_commit() -> None:
    # Given
    inventory = ("dagger.json:644:abc",)

    # When
    require_same_inventory(inventory, inventory)

    # Then
    assert True


def test_should_reject_workspace_when_only_file_mode_differs() -> None:
    expected = (f"run.sh:755:{'a' * 64}",)
    actual = (f"run.sh:644:{'a' * 64}",)
    with pytest.raises(SourceMismatch, match=r"run\.sh"):
        require_same_inventory(expected, actual)


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
    expected = ("dagger.json:644:abc",)
    actual = ("dagger.json:644:abc", "notes.txt:644:def")

    # When / Then
    with pytest.raises(SourceMismatch, match=r"notes\.txt"):
        require_same_inventory(expected, actual)


def test_should_reject_symlink_when_hosted_inventory_contains_one() -> None:
    # Given
    inventory = FakeInventory((InventoryEntry("linked-source", "a" * 64, EntryType.SYMLINK),))

    # When / Then
    with pytest.raises(SourceMismatch, match="linked-source"):
        asyncio.run(canonical_inventory(inventory))


def test_should_reject_explicit_unknown_node_from_bulk_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Dagger v0.21.8 has no portable FIFO snapshot contract; UNKNOWN is its stable API boundary.
    counter = ScaleQueryCounter()
    directory = cast(dagger.Directory, object())
    output = _inventory_stream(_inventory_record("unknown", "named-pipe", 0))
    monkeypatch.setattr(source_module, "dag", FakeScaleDag(counter, output))
    inventory = source_module.DaggerSourceInventory(directory)
    with pytest.raises(SourceMismatch, match="named-pipe"):
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
    assert manifest == (f"dagger.json:644:{'a' * 64}", f"src/app.ts:644:{'b' * 64}")


def test_should_omit_directories_when_creating_dagger_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    counter = ScaleQueryCounter()
    directory = cast(dagger.Directory, object())
    monkeypatch.setattr(source_module, "dag", FakeScaleDag(counter, _small_inventory_stream()))

    # When
    manifest = asyncio.run(canonical_inventory(source_module.DaggerSourceInventory(directory)))

    # Then
    assert manifest == (f"src/app.ts:644:{SCALE_DIGEST}",)


def test_should_reject_duplicate_directories_before_inventory_filtering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = ScaleQueryCounter()
    directory = cast(dagger.Directory, object())
    output = _inventory_stream(
        _inventory_record("directory", "src"), _inventory_record("directory", "src")
    )
    monkeypatch.setattr(source_module, "dag", FakeScaleDag(counter, output))
    with pytest.raises(SourceMismatch, match="duplicate"):
        asyncio.run(source_module.DaggerSourceInventory(directory).entries())


def test_should_reject_directory_and_file_collision_before_filtering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = ScaleQueryCounter()
    directory = cast(dagger.Directory, object())
    output = _inventory_stream(
        _inventory_record("directory", "same"), _inventory_record("regular", "same")
    )
    monkeypatch.setattr(source_module, "dag", FakeScaleDag(counter, output))
    with pytest.raises(SourceMismatch, match="duplicate"):
        asyncio.run(source_module.DaggerSourceInventory(directory).entries())


def test_should_bound_graph_terminals_when_inventory_is_large_and_deep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    counter = ScaleQueryCounter()
    directory = cast(dagger.Directory, FakeScaleDirectory(counter))
    monkeypatch.setattr(source_module, "dag", FakeScaleDag(counter))

    # When
    entries = asyncio.run(source_module.DaggerSourceInventory(directory).entries())

    # Then
    assert len(entries) == SCALE_WIDTH
    assert entries[-1].path.endswith("file-0511.bin")
    assert counter.terminals <= 1


def test_should_accept_empty_bulk_inventory_output() -> None:
    assert source_module._parse_inventory(_inventory_stream()) == ()


def test_should_reject_bulk_inventory_when_complete_stream_trailer_is_missing() -> None:
    output = _inventory_record("regular", "app.py")
    with pytest.raises(SourceMismatch, match="complete-stream trailer"):
        source_module._parse_inventory(output)


def test_should_reject_bulk_inventory_when_trailer_digest_differs() -> None:
    record = _inventory_record("regular", "app.py")
    output = f"{record}{INVENTORY_TRAILER}\t1\t{'0' * 64}\n"
    with pytest.raises(SourceMismatch, match="complete-stream trailer"):
        source_module._parse_inventory(output)


def test_should_reject_bulk_inventory_when_trailer_count_differs() -> None:
    record = _inventory_record("regular", "app.py")
    digest = hashlib.sha256(record.encode()).hexdigest()
    output = f"{record}{INVENTORY_TRAILER}\t2\t{digest}\n"
    with pytest.raises(SourceMismatch, match="complete-stream trailer"):
        source_module._parse_inventory(output)


def test_should_reject_bulk_inventory_when_terminal_trailer_is_truncated() -> None:
    output = _inventory_stream(_inventory_record("regular", "app.py")).removesuffix("\n")
    with pytest.raises(SourceMismatch, match="complete-stream trailer"):
        source_module._parse_inventory(output)


def test_should_reject_bulk_inventory_when_trailer_is_duplicated() -> None:
    embedded = _inventory_stream()
    output = _inventory_stream(embedded)
    with pytest.raises(SourceMismatch, match="duplicate complete-stream trailer"):
        source_module._parse_inventory(output)


def test_should_reject_bulk_inventory_when_trailer_is_malformed() -> None:
    output = f"{INVENTORY_TRAILER}\t0\n"
    with pytest.raises(SourceMismatch, match="complete-stream trailer is malformed"):
        source_module._parse_inventory(output)


def test_should_reject_bulk_inventory_when_complete_record_is_truncated() -> None:
    first = _inventory_record("regular", "first.py")
    output = _inventory_stream(first, _inventory_record("regular", "second.py"))
    with pytest.raises(SourceMismatch, match="complete-stream trailer"):
        source_module._parse_inventory(output.removeprefix(first))


@pytest.mark.parametrize(
    "output",
    (
        "malformed",
        f"regular\t644\t{SCALE_DIGEST}\t%",
        f"regular\t644\t{SCALE_DIGEST}\tYQ===",
        f"regular\tinvalid\t{SCALE_DIGEST}\tc2FmZQ==",
        f"regular\t0644\t{SCALE_DIGEST}\tc2FmZQ==",
    ),
)
def test_should_reject_malformed_bulk_inventory_output(output: str) -> None:
    with pytest.raises(SourceMismatch, match="inventory output is malformed"):
        source_module._parse_inventory(_inventory_stream(f"{output}\n"))


def test_should_reject_invalid_inventory_entry_when_path_is_unsafe() -> None:
    with pytest.raises(ValueError, match="relative path"):
        InventoryEntry("../escape", "a" * 64, EntryType.REGULAR)


def test_should_reject_invalid_inventory_entry_permissions() -> None:
    with pytest.raises(ValueError, match="permissions"):
        InventoryEntry("safe", "a" * 64, EntryType.REGULAR, 0o10000)


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
