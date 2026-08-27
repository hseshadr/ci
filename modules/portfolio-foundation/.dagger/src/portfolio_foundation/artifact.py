"""Deterministic, exact-identity artifact envelope construction."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Final

import dagger
from dagger import dag

from .identity import CommitIdentity, FullSha, RepositoryRef
from .source import HASH_IMAGE, DaggerSourceInventory, EntryType, InventoryEntry

ENGINE_VERSION: Final = "v0.21.8"
IDENTITY_SEPARATOR_INDEX: Final = 40
MANIFEST_PATH: Final = "evidence/artifact-manifest.json"
MIN_PRODUCING_IDENTITY_LENGTH: Final = 42
SUMS_PATH: Final = "evidence/SHA256SUMS"
TOOLCHAIN: Final = ("dagger-engine:v0.21.8", f"artifact-hasher:{HASH_IMAGE}")
RUN_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")


class UnexpectedArtifactPathError(ValueError):
    """Raised when an artifact inventory path violates its declared contract."""


class EmptyArtifactError(ValueError):
    """Raised when an envelope would contain no product artifact files."""


class UnsupportedArtifactNodeError(ValueError):
    """Raised when an artifact contains a non-regular filesystem node."""


class ChecksumMismatchError(ValueError):
    """Raised when checksummed artifact evidence is modified or incomplete."""


class ToolchainMismatchError(ValueError):
    """Raised when evidence does not use this foundation's fixed toolchain."""


@dataclass(frozen=True)
class ArtifactFile:
    """One regular artifact file with its canonical content digest."""

    path: str
    sha256: str

    def __post_init__(self) -> None:
        """Reject unsafe paths and noncanonical SHA-256 digests."""
        if not _safe_path(self.path) or SHA256_PATTERN.fullmatch(self.sha256) is None:
            raise UnexpectedArtifactPath("artifact file requires a safe path and SHA-256 digest")


@dataclass(frozen=True)
class ArtifactManifest:
    """Canonical product-artifact evidence tied to exact immutable identities."""

    identity: CommitIdentity
    module_sha: FullSha
    engine_version: str
    toolchain: tuple[str, ...]
    producing_run_id: str
    allowed_roots: tuple[str, ...]
    files: tuple[ArtifactFile, ...]

    def to_json(self) -> str:
        """Serialize the sorted manifest as canonical, collision-safe JSON."""
        return json.dumps(
            _manifest_payload(self), ensure_ascii=True, separators=(",", ":"), sort_keys=True
        )


@dataclass(frozen=True)
class ArtifactEvidence:
    """A Dagger directory together with the immutable evidence describing it."""

    directory: dagger.Directory
    manifest: ArtifactManifest


@dataclass(frozen=True)
class ArtifactContext:
    """All immutable producer facts that bind a product artifact to its source."""

    identity: CommitIdentity
    module_sha: str
    allowed_roots: tuple[str, ...]
    producing_run_id: str


def build_manifest(files: tuple[ArtifactFile, ...], context: ArtifactContext) -> ArtifactManifest:
    """Build a canonical envelope manifest from already-hashed product files."""
    roots = canonical_roots(context.allowed_roots)
    validate_paths(tuple(file.path for file in files), roots)
    _require_run_id(context.producing_run_id)
    return ArtifactManifest(
        context.identity,
        FullSha(context.module_sha),
        ENGINE_VERSION,
        TOOLCHAIN,
        context.producing_run_id,
        roots,
        _sorted_files(files),
    )


def parse_consumer_identity(value: str) -> CommitIdentity:
    """Parse the public repository-at-SHA input into a full immutable identity."""
    repository, marker, commit_sha = value.rpartition("@")
    if not marker:
        raise ValueError("consumer identity must be owner/repository@full-sha")
    return CommitIdentity(RepositoryRef.parse(repository), FullSha(commit_sha))


def parse_producing_identity(value: str) -> tuple[str, str]:
    """Parse the fixed-width module-SHA and producing-run identity input."""
    if len(value) < MIN_PRODUCING_IDENTITY_LENGTH or value[IDENTITY_SEPARATOR_INDEX] != ":":
        raise ValueError("producing identity must be full-module-sha:run-id")
    module_sha = FullSha(value[:IDENTITY_SEPARATOR_INDEX]).value
    run_id = value[MIN_PRODUCING_IDENTITY_LENGTH - 1 :]
    _require_run_id(run_id)
    return module_sha, run_id


def canonical_roots(roots: tuple[str, ...]) -> tuple[str, ...]:
    """Reject ambiguous roots and return their canonical lexical ordering."""
    ordered = tuple(sorted(roots))
    if not _valid_roots(ordered):
        raise UnexpectedArtifactPath("artifact allowed roots must be unique safe relative paths")
    return ordered


def validate_paths(paths: tuple[str, ...], allowed_roots: tuple[str, ...]) -> None:
    """Reject empty, duplicated, unsafe, and out-of-contract artifact paths."""
    roots = canonical_roots(allowed_roots)
    _require_artifact_paths(paths)
    _require_unique_paths(paths)
    _require_allowed_paths(paths, roots)


def _require_artifact_paths(paths: tuple[str, ...]) -> None:
    """Require at least one regular product file in the supplied artifact."""
    if not paths:
        raise EmptyArtifact("artifact inventory is empty")


def _require_unique_paths(paths: tuple[str, ...]) -> None:
    """Reject duplicate path records that could hide a digest substitution."""
    if len(paths) != len(frozenset(paths)):
        raise UnexpectedArtifactPath("artifact inventory contains duplicate path")


def _require_allowed_paths(paths: tuple[str, ...], roots: tuple[str, ...]) -> None:
    """Require every product file to stay inside an explicit declared root."""
    invalid = next((path for path in paths if not _allowed_path(path, roots)), None)
    if invalid is not None:
        raise UnexpectedArtifactPath(f"artifact path is not allowed: {invalid}")


def artifact_files(entries: tuple[InventoryEntry, ...]) -> tuple[ArtifactFile, ...]:
    """Turn Dagger inventory entries into a strictly regular-file artifact list."""
    invalid = next(
        (entry.path for entry in entries if entry.entry_type is not EntryType.REGULAR), None
    )
    if invalid is not None:
        raise UnsupportedArtifactNode(f"artifact contains unsupported node at {invalid}")
    return tuple(ArtifactFile(entry.path, entry.sha256) for entry in entries)


async def collect_artifact_evidence(
    artifact: dagger.Directory,
    identity: CommitIdentity,
    module_sha: str,
    allowed_roots: tuple[str, ...],
    producing_run_id: str,
) -> ArtifactEvidence:
    """Hash every artifact byte through the fixed Dagger inventory adapter."""
    entries = await DaggerSourceInventory(artifact).entries()
    context = ArtifactContext(identity, module_sha, allowed_roots, producing_run_id)
    manifest = build_manifest(artifact_files(entries), context)
    return ArtifactEvidence(artifact, manifest)


async def envelope_directory(
    artifact: dagger.Directory,
    identity: CommitIdentity,
    module_sha: str,
    allowed_roots: tuple[str, ...],
    producing_run_id: str,
) -> dagger.Directory:
    """Create the only allowed artifact-and-evidence directory structure."""
    evidence = await collect_artifact_evidence(
        artifact, identity, module_sha, allowed_roots, producing_run_id
    )
    checksums = sha256_sums(evidence.manifest)
    validate_evidence(evidence.manifest, checksums)
    return _envelope_directory(evidence, checksums)


def sha256_sums(manifest: ArtifactManifest) -> str:
    """Render canonical checksums for every product file and its manifest evidence."""
    records = tuple((f"artifact/{file.path}", file.sha256) for file in manifest.files)
    manifest_digest = hashlib.sha256(manifest.to_json().encode()).hexdigest()
    all_records = (*records, (MANIFEST_PATH, manifest_digest))
    return "".join(_checksum_record(path, digest) for path, digest in all_records)


def validate_evidence(manifest: ArtifactManifest, checksums: str) -> None:
    """Fail closed when evidence is tampered or produced by an incompatible toolchain."""
    require_compatible_toolchain(manifest)
    if checksums != sha256_sums(manifest):
        raise ChecksumMismatch("artifact evidence checksum does not match its manifest")


def require_compatible_toolchain(manifest: ArtifactManifest) -> None:
    """Require the immutable engine and hashing-toolchain tuple in every envelope."""
    if manifest.engine_version != ENGINE_VERSION or manifest.toolchain != TOOLCHAIN:
        raise ToolchainMismatch("artifact manifest toolchain is incompatible with this foundation")


def _manifest_payload(manifest: ArtifactManifest) -> object:
    """Return the complete structured payload used by canonical JSON serialization."""
    return {
        "allowed_roots": manifest.allowed_roots,
        "consumer_sha": manifest.identity.commit.value,
        "engine_version": manifest.engine_version,
        "files": [{"path": file.path, "sha256": file.sha256} for file in manifest.files],
        "module_sha": manifest.module_sha.value,
        "producing_run_id": manifest.producing_run_id,
        "repository": f"{manifest.identity.repository.owner}/{manifest.identity.repository.name}",
        "schema_version": 1,
        "toolchain": manifest.toolchain,
    }


def _safe_path(path: str) -> bool:
    """Accept every legal non-NUL Git filename except traversal components."""
    parts = tuple(path.split("/"))
    return bool(path) and "\x00" not in path and not path.startswith("/") and _safe_parts(parts)


def _allowed_path(path: str, roots: tuple[str, ...]) -> bool:
    """Check that a safe path is equal to or nested beneath one allowed root."""
    return _safe_path(path) and any(path == root or path.startswith(f"{root}/") for root in roots)


def _safe_parts(parts: tuple[str, ...]) -> bool:
    """Reject empty and traversal components from a slash-separated path."""
    return all(part not in {"", ".", ".."} for part in parts)


def _valid_roots(roots: tuple[str, ...]) -> bool:
    """Check nonempty unique root declarations before they become public evidence."""
    return bool(roots) and _unique_roots(roots) and all(_safe_path(root) for root in roots)


def _unique_roots(roots: tuple[str, ...]) -> bool:
    """Check root declarations do not repeat a path under multiple names."""
    return len(roots) == len(frozenset(roots))


def _sorted_files(files: tuple[ArtifactFile, ...]) -> tuple[ArtifactFile, ...]:
    """Return immutable file evidence in path order independent of input order."""
    return tuple(sorted(files, key=lambda file: file.path))


def _require_run_id(run_id: str) -> None:
    """Reject empty or nonportable producing-run identities before serialization."""
    if RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ValueError("producing run ID must use 1-128 portable identifier characters")


def _checksum_record(path: str, digest: str) -> str:
    """Encode each checksum path as JSON so separators and newlines stay unambiguous."""
    return f"{digest}  {json.dumps(path, ensure_ascii=True, separators=(',', ':'))}\n"


def _envelope_directory(evidence: ArtifactEvidence, checksums: str) -> dagger.Directory:
    """Wrap untouched artifact bytes with exactly the two canonical evidence files."""
    output = dag.directory().with_directory("artifact", evidence.directory)
    output = output.with_new_file(MANIFEST_PATH, evidence.manifest.to_json())
    return output.with_new_file(SUMS_PATH, checksums)


UnexpectedArtifactPath = UnexpectedArtifactPathError
EmptyArtifact = EmptyArtifactError
UnsupportedArtifactNode = UnsupportedArtifactNodeError
ChecksumMismatch = ChecksumMismatchError
ToolchainMismatch = ToolchainMismatchError
