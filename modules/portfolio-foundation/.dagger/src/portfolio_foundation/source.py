"""Fail-closed source inventories and Dagger workspace binding."""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

import dagger
from dagger import dag

from .identity import CommitIdentity, FullSha

SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class SourceMismatchError(ValueError):
    """Raised when supplied workspace bytes differ from the claimed commit."""


SourceMismatch = SourceMismatchError


class EntryType(Enum):
    """The only node kinds accepted into a source inventory."""

    DIRECTORY = "directory"
    REGULAR = "regular"
    SYMLINK = "symlink"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class InventoryEntry:
    """A content-addressed relative workspace file."""

    path: str
    sha256: str
    entry_type: EntryType

    def __post_init__(self) -> None:
        """Reject paths and hashes that cannot safely form a manifest."""
        if not _is_safe_path(self.path) or SHA256_PATTERN.fullmatch(self.sha256) is None:
            raise ValueError("inventory entries require a relative path and SHA-256 hash")


@runtime_checkable
class SourceInventory(Protocol):
    """A typed adapter that exposes all nodes in a source tree."""

    async def entries(self) -> tuple[InventoryEntry, ...]:
        """Return every source node with its type and regular-file hash."""


@dataclass(frozen=True)
class SourceBinding[SourceT, HistoryT]:
    """A verified workspace plus separate exact Git history input."""

    source: SourceT
    history: HistoryT
    identity: FullSha
    manifest_sha256: str


@dataclass(frozen=True)
class DaggerSourceInventory:
    """Inventory adapter that hashes Dagger regular files without host access."""

    directory: dagger.Directory

    async def entries(self) -> tuple[InventoryEntry, ...]:
        """Return a complete typed inventory for this Dagger directory."""
        paths = await _dagger_paths(self.directory, "")
        entries = await asyncio.gather(*(_dagger_entry(self.directory, path) for path in paths))
        return _file_entries(entries)


def bind_source[SourceT, HistoryT](
    source: SourceT, history: HistoryT, identity: FullSha, manifest: tuple[str, ...]
) -> SourceBinding[SourceT, HistoryT]:
    """Bind distinct source and history values to a canonical manifest."""
    return SourceBinding(source, history, identity, manifest_sha256(manifest))


async def bind_dagger_source(
    source: dagger.Directory, history: dagger.Directory, identity: CommitIdentity
) -> SourceBinding[dagger.Directory, dagger.Directory]:
    """Reject a workspace unless every hosted byte matches the exact commit."""
    expected, actual = await asyncio.gather(
        canonical_inventory(DaggerSourceInventory(history)),
        canonical_inventory(DaggerSourceInventory(source)),
    )
    require_same_inventory(expected, actual)
    return bind_source(source, history, identity.commit, expected)


async def canonical_inventory(inventory: SourceInventory) -> tuple[str, ...]:
    """Create a sorted path-and-SHA-256 inventory from regular files only."""
    entries = await inventory.entries()
    _require_regular_entries(entries)
    lines = tuple(_manifest_line(entry) for entry in entries)
    _require_unique_paths(lines)
    return tuple(sorted(lines))


def manifest_sha256(manifest: tuple[str, ...]) -> str:
    """Hash a canonical text representation of a source inventory."""
    content = "\n".join(sorted(manifest)).encode()
    return hashlib.sha256(content).hexdigest()


def require_same_inventory(expected: tuple[str, ...], actual: tuple[str, ...]) -> None:
    """Fail closed with the first missing, unexpected, or changed path."""
    mismatch = _first_mismatch(expected, actual)
    if mismatch is not None:
        raise SourceMismatch(f"workspace does not match exact commit at {mismatch}")


async def _dagger_paths(directory: dagger.Directory, parent: str) -> tuple[str, ...]:
    """Recursively list every path, retaining nodes until their type is checked."""
    names = await directory.entries(path=parent or None)
    return await _paths_with_descendants(directory, parent, names)


async def _paths_with_descendants(
    directory: dagger.Directory, parent: str, names: list[str]
) -> tuple[str, ...]:
    """Join direct child names and append their recursively discovered paths."""
    paths = _child_paths(parent, names)
    nested = await asyncio.gather(*(_nested_paths(directory, path) for path in paths))
    return paths + _flatten_paths(nested)


async def _nested_paths(directory: dagger.Directory, path: str) -> tuple[str, ...]:
    """Return descendants only when a Dagger node is a directory."""
    entry_type = await _dagger_entry_type(directory, path)
    return await _dagger_paths(directory, path) if entry_type is EntryType.DIRECTORY else ()


async def _dagger_entry(directory: dagger.Directory, path: str) -> InventoryEntry:
    """Convert one Dagger node to an inventory entry without following links."""
    entry_type = await _dagger_entry_type(directory, path)
    sha256 = await _dagger_sha256(directory, path) if entry_type is EntryType.REGULAR else "0" * 64
    return InventoryEntry(path, sha256, entry_type)


async def _dagger_entry_type(directory: dagger.Directory, path: str) -> EntryType:
    """Translate Dagger file metadata to the closed source-node vocabulary."""
    file_type = await directory.stat(path, do_not_follow_symlinks=True).file_type()
    return _entry_type(file_type)


async def _dagger_sha256(directory: dagger.Directory, path: str) -> str:
    """Hash mounted bytes with a pinned utility image rather than text decoding."""
    output = await _sha256_container(directory, path).stdout()
    return output.split(maxsplit=1)[0]


def _sha256_container(directory: dagger.Directory, path: str) -> dagger.Container:
    """Create the fixed, read-only hashing step for one relative workspace file."""
    return (
        dag.container()
        .from_("alpine:3.22")
        .with_mounted_directory("/source", directory, read_only=True)
        .with_exec(["sha256sum", f"/source/{path}"])
    )


def _entry_type(file_type: dagger.FileType | None) -> EntryType:
    """Map all Dagger metadata values to an explicitly handled local value."""
    if file_type is dagger.FileType.DIRECTORY:
        return EntryType.DIRECTORY
    if file_type is dagger.FileType.REGULAR:
        return EntryType.REGULAR
    if file_type is dagger.FileType.SYMLINK:
        return EntryType.SYMLINK
    return EntryType.UNKNOWN


def _first_mismatch(expected: tuple[str, ...], actual: tuple[str, ...]) -> str | None:
    """Return the first path whose full manifest line is absent or different."""
    expected_by_path = _lines_by_path(expected)
    actual_by_path = _lines_by_path(actual)
    return _first_changed_path(expected_by_path, actual_by_path)


def _lines_by_path(lines: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """Keep manifest records ordered without introducing mutable record maps."""
    return tuple(sorted(_split_manifest_line(line) for line in lines))


def _first_changed_path(
    expected: tuple[tuple[str, str], ...], actual: tuple[tuple[str, str], ...]
) -> str | None:
    """Find the first path that is missing, unexpected, or has a new hash."""
    paths = tuple(sorted({path for path, _ in expected} | {path for path, _ in actual}))
    return next(
        (path for path in paths if _hash_at(expected, path) != _hash_at(actual, path)), None
    )


def _hash_at(lines: tuple[tuple[str, str], ...], path: str) -> str | None:
    """Read a manifest hash by path from its small sorted immutable sequence."""
    return next((digest for candidate, digest in lines if candidate == path), None)


def _is_safe_path(path: str) -> bool:
    """Allow only non-empty relative paths that cannot traverse their parent."""
    parts = tuple(path.split("/"))
    return (
        bool(path)
        and not path.startswith("/")
        and all(part not in {"", ".", ".."} for part in parts)
    )


def _join_path(parent: str, name: str) -> str:
    """Create a slash-separated relative child path."""
    return f"{parent}/{name}" if parent else name


def _child_paths(parent: str, names: list[str]) -> tuple[str, ...]:
    """Join a Dagger directory's direct child names to their parent path."""
    return tuple(_join_path(parent, name) for name in names)


def _flatten_paths(groups: list[tuple[str, ...]]) -> tuple[str, ...]:
    """Flatten immutable groups of descendants without changing their order."""
    return tuple(path for group in groups for path in group)


def _manifest_line(entry: InventoryEntry) -> str:
    """Render one regular-file inventory entry in its canonical form."""
    return f"{entry.path}:{entry.sha256}"


def _file_entries(entries: list[InventoryEntry]) -> tuple[InventoryEntry, ...]:
    """Omit traversed directories while retaining links for fail-closed rejection."""
    return tuple(entry for entry in entries if entry.entry_type is not EntryType.DIRECTORY)


def _require_regular_entries(entries: tuple[InventoryEntry, ...]) -> None:
    """Reject all directories, symlinks, and unknown nodes before comparison."""
    invalid = next(
        (entry.path for entry in entries if entry.entry_type is not EntryType.REGULAR), None
    )
    if invalid is not None:
        raise SourceMismatch(f"hosted source contains unsupported node at {invalid}")


def _require_unique_paths(lines: tuple[str, ...]) -> None:
    """Reject duplicated paths so no hash can be hidden by a repeated record."""
    paths = tuple(_split_manifest_line(line)[0] for line in lines)
    if len(paths) != len(frozenset(paths)):
        raise SourceMismatch("hosted source contains duplicate file paths")


def _split_manifest_line(line: str) -> tuple[str, str]:
    """Split the canonical path-and-digest representation once."""
    path, digest = line.rsplit(":", maxsplit=1)
    return path, digest
