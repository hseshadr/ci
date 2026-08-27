from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from portfolio_foundation.artifact import (
    ENGINE_VERSION,
    TOOLCHAIN,
    ArtifactContext,
    ArtifactFile,
    ArtifactManifest,
    ChecksumMismatch,
    EmptyArtifact,
    ToolchainMismatch,
    UnexpectedArtifactPath,
    UnsupportedArtifactNode,
    artifact_files,
    build_manifest,
    parse_consumer_identity,
    parse_producing_identity,
    require_compatible_toolchain,
    sha256_sums,
    validate_evidence,
    validate_paths,
)
from portfolio_foundation.identity import CommitIdentity, FullSha, RepositoryRef
from portfolio_foundation.source import EntryType, InventoryEntry

MODULE = Path(__file__).parents[2]
IDENTITY = CommitIdentity(RepositoryRef("owner", "consumer"), FullSha("a" * 40))
MODULE_SHA = "b" * 40
RUN_ID = "12345"
ROOTS = ("dist",)
CONTEXT = ArtifactContext(IDENTITY, MODULE_SHA, ROOTS, RUN_ID)


def _file(path: str, contents: bytes) -> ArtifactFile:
    return ArtifactFile(path, hashlib.sha256(contents).hexdigest())


def _manifest(files: tuple[ArtifactFile, ...]) -> ArtifactManifest:
    return build_manifest(files, CONTEXT)


def test_should_create_stable_immutable_manifest_when_input_order_differs() -> None:
    # Given
    first, second = _file("dist/index.html", b"index"), _file("dist/app.js", b"app")

    # When
    manifest = _manifest((second, first))

    # Then
    assert manifest.to_json() == _manifest((first, second)).to_json()
    assert manifest.files == (second, first)
    with pytest.raises(FrozenInstanceError):
        manifest.producing_run_id = "changed"  # type: ignore[misc]


def test_should_bind_full_identity_module_and_toolchain_when_manifest_is_built() -> None:
    # Given / When
    manifest = _manifest((_file("dist/index.html", b"index"),))

    # Then
    payload = json.loads(manifest.to_json())
    assert payload["repository"] == "owner/consumer"
    assert payload["consumer_sha"] == "a" * 40
    assert payload["module_sha"] == MODULE_SHA
    assert payload["engine_version"] == ENGINE_VERSION
    assert payload["toolchain"] == list(manifest.toolchain)


def test_should_record_digest_pinned_hasher_when_manifest_is_built() -> None:
    # Given / When / Then
    assert TOOLCHAIN[0] == f"dagger-engine:{ENGINE_VERSION}"
    assert "@sha256:" in TOOLCHAIN[1]


def test_should_parse_exact_consumer_and_producing_identities_when_enveloping() -> None:
    # Given / When
    consumer = parse_consumer_identity(f"owner/consumer@{'a' * 40}")
    module_sha, run_id = parse_producing_identity(f"{'b' * 40}:12345")

    # Then
    assert consumer == IDENTITY
    assert (module_sha, run_id) == (MODULE_SHA, RUN_ID)


@pytest.mark.parametrize("consumer", ("owner/consumer", f"owner/consumer@{'A' * 40}"))
def test_should_reject_malformed_consumer_identity_when_enveloping(consumer: str) -> None:
    # Given / When / Then
    with pytest.raises(ValueError):
        parse_consumer_identity(consumer)


@pytest.mark.parametrize("producer", ("short:12345", f"{'b' * 40}/12345", f"{'b' * 40}:"))
def test_should_reject_malformed_producing_identity_when_enveloping(producer: str) -> None:
    # Given / When / Then
    with pytest.raises(ValueError):
        parse_producing_identity(producer)


@pytest.mark.parametrize("paths", (("dist/index.html", "debug.log"), ("debug.log",)))
def test_should_reject_unexpected_path_when_not_under_an_allowed_root(
    paths: tuple[str, ...],
) -> None:
    # Given / When / Then
    with pytest.raises(UnexpectedArtifactPath, match=r"debug\.log"):
        validate_paths(paths, ROOTS)


@pytest.mark.parametrize(
    "paths", ((), ("dist/../debug.log",), ("dist/index.html", "dist/index.html"))
)
def test_should_reject_ambiguous_or_empty_artifact_inventory(paths: tuple[str, ...]) -> None:
    # Given / When / Then
    with pytest.raises((EmptyArtifact, UnexpectedArtifactPath), match="artifact"):
        validate_paths(paths, ROOTS)


def test_should_reject_symlink_when_collecting_artifact_files() -> None:
    # Given
    entry = InventoryEntry("dist/link", "0" * 64, EntryType.SYMLINK)

    # When / Then
    with pytest.raises(UnsupportedArtifactNode, match="dist/link"):
        artifact_files((entry,))


def test_should_checksum_structured_evidence_when_filename_has_legal_separators() -> None:
    # Given
    manifest = _manifest((_file("dist/line\nbreak: space.js", b"content"),))

    # When
    checksums = sha256_sums(manifest)

    # Then
    assert "\\n" in checksums
    validate_evidence(manifest, checksums)
    with pytest.raises(ChecksumMismatch):
        validate_evidence(manifest, checksums.replace("a", "b", 1))


def test_should_reject_incompatible_or_tampered_manifest_toolchain() -> None:
    # Given
    manifest = _manifest((_file("dist/index.html", b"index"),))

    # When / Then
    require_compatible_toolchain(manifest)
    tampered = ArtifactManifest(
        manifest.identity,
        manifest.module_sha,
        "v0.0.0",
        manifest.toolchain,
        manifest.producing_run_id,
        manifest.allowed_roots,
        manifest.files,
    )
    with pytest.raises(ToolchainMismatch):
        require_compatible_toolchain(tampered)


def _run_envelope(source: Path, output: Path) -> subprocess.CompletedProcess[str]:
    dagger = shutil.which("dagger")
    assert dagger is not None
    return subprocess.run(  # noqa: S603
        (
            dagger,
            "-m",
            ".",
            "call",
            "envelope",
            f"--artifact={source}",
            f"--consumer-identity=owner/consumer@{'a' * 40}",
            f"--producing-identity={'b' * 40}:12345",
            "--allowed-roots=dist",
            "export",
            f"--path={output}",
        ),
        cwd=MODULE,
        capture_output=True,
        check=False,
        text=True,
    )


def _assert_exported_envelope(output: Path) -> None:
    expected = {"artifact/dist/index.html", "artifact/dist/nested/app.js"}
    paths = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
    assert paths == expected | {"evidence/artifact-manifest.json", "evidence/SHA256SUMS"}
    manifest = json.loads((output / "evidence/artifact-manifest.json").read_text())
    for record in manifest["files"]:
        content = (output / "artifact" / record["path"]).read_bytes()
        assert record["sha256"] == hashlib.sha256(content).hexdigest()


def test_should_envelope_nested_directory_and_reject_unexpected_input(tmp_path: Path) -> None:
    # Given
    source, output = tmp_path / "artifact", tmp_path / "envelope"
    (source / "dist/nested").mkdir(parents=True)
    (source / "dist/index.html").write_text("index")
    (source / "dist/nested/app.js").write_text("app")

    # When
    result = _run_envelope(source, output)
    (source / "debug.log").write_text("debug")
    rejected = _run_envelope(source, tmp_path / "rejected")

    # Then
    assert result.returncode == 0, result.stderr
    _assert_exported_envelope(output)
    assert rejected.returncode != 0
    assert "debug.log" in rejected.stderr
