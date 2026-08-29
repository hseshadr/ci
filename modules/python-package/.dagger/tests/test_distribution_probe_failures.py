from __future__ import annotations

import io
import stat
import struct
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import cast

import pytest

from python_package import distribution_probe as probe
from python_package.distributions import MAX_DISTRIBUTION_MEMBERS, DistributionObservation


class NullTar:
    def extractfile(self, member: tarfile.TarInfo) -> None:
        assert member.name == "package/PKG-INFO"


def test_should_reject_missing_or_extra_dist_products(tmp_path: Path) -> None:
    # Given an empty dist directory
    # When / Then no partial product set is accepted
    with pytest.raises(probe.ProbeError, match="exactly two regular files"):
        probe.inspect_directory(tmp_path)


def test_should_reject_unmanifested_top_level_distribution_node(tmp_path: Path) -> None:
    # Given apparent products plus an extra directory that would enter the envelope
    (tmp_path / "package-1.2.3-py3-none-any.whl").write_bytes(b"wheel")
    (tmp_path / "package-1.2.3.tar.gz").write_bytes(b"sdist")
    (tmp_path / "extra").mkdir()

    # When / Then the complete top-level inventory fails before archive parsing
    with pytest.raises(probe.ProbeError, match="exactly two regular files"):
        probe.inspect_directory(tmp_path)


def test_should_reject_two_regular_files_with_wrong_product_kinds(tmp_path: Path) -> None:
    # Given exactly two regular nodes that are not one wheel and one sdist
    (tmp_path / "first.whl").write_bytes(b"first")
    (tmp_path / "second.whl").write_bytes(b"second")

    # When / Then the logical product inventory fails before archive parsing
    with pytest.raises(probe.ProbeError, match="exactly one wheel and one sdist"):
        probe.inspect_directory(tmp_path)


def test_should_reject_streamed_sdist_without_root_metadata(tmp_path: Path) -> None:
    # Given a bounded sdist whose stream has no root PKG-INFO
    target = tmp_path / "package-1.2.3.tar.gz"
    with tarfile.open(target, "w:gz") as archive:
        archive.addfile(tarfile.TarInfo("package-1.2.3/src/module.py"))

    # When / Then the completed stream cannot produce candidate identity
    with pytest.raises(probe.ProbeError, match="sdist metadata inventory"):
        probe._inspect_sdist(target)


@pytest.mark.parametrize(
    ("state", "message"),
    (
        (probe._TarScanState(records=MAX_DISTRIBUTION_MEMBERS + 1), "member bound"),
        (probe._TarScanState(payload_bytes=probe.MAX_UNCOMPRESSED_BYTES + 1), "uncompressed"),
        (probe._TarScanState(extension_bytes=probe.MAX_TAR_EXTENSION_BYTES + 1), "extension"),
    ),
)
def test_should_reject_every_raw_tar_resource_bound(
    state: probe._TarScanState, message: str
) -> None:
    # Given / When / Then raw records, payloads, and hidden extensions have separate caps
    with pytest.raises(probe.ProbeError, match=message):
        probe._require_raw_tar_bounds(state)


def test_should_reject_unsupported_raw_tar_type_before_tarfile() -> None:
    # Given a checksum-valid symlink header hidden from the yielded-member loop
    header = _tar_header(b"2")

    # When / Then raw preflight rejects it before payload processing
    with pytest.raises(probe.ProbeError, match="unsupported archive member"):
        probe._observe_raw_tar_header(probe._TarScanState(), header)


def test_should_reject_truncated_or_trailing_raw_tar_records() -> None:
    # Given incomplete header/payload bytes and nonzero bytes after the terminator
    truncated = probe._BoundedTarReader(io.BytesIO(b"x"))
    trailing = probe._BoundedTarReader(io.BytesIO(bytes(512) + b"x"))

    # When / Then each raw framing violation fails closed
    with pytest.raises(probe.ProbeError, match="record is truncated"):
        probe._read_tar_block(truncated)
    with pytest.raises(probe.ProbeError, match="trailing archive data"):
        probe._require_tar_end(trailing)


def test_should_reject_raw_tar_terminator_and_payload_truncation() -> None:
    # Given a nonzero second terminator block and one short payload
    terminator = probe._BoundedTarReader(io.BytesIO(b"x" * 512))
    payload = probe._BoundedTarReader(io.BytesIO(b"x"))

    # When / Then structural EOF cannot be interpreted as a complete archive
    with pytest.raises(probe.ProbeError, match="terminator differs"):
        probe._require_tar_end(terminator)
    with pytest.raises(probe.ProbeError, match="payload is truncated"):
        probe._skip_tar_payload(payload, 512)


@pytest.mark.parametrize("value", (b"", b"8"))
def test_should_reject_non_octal_tar_numeric_fields(value: bytes) -> None:
    # Given / When / Then empty and non-octal numeric fields cannot control reads
    with pytest.raises(probe.ProbeError, match="numeric field"):
        probe._tar_octal(value)


def test_should_reject_invalid_tar_text_and_checksum() -> None:
    # Given invalid UTF-8 and a mutated checksum-valid header
    header = bytearray(_tar_header(b"0"))
    header[0] ^= 1

    # When / Then neither can define raw archive identity
    with pytest.raises(probe.ProbeError, match="text field"):
        probe._tar_text(b"\xff")
    with pytest.raises(probe.ProbeError, match="checksum"):
        probe._require_tar_checksum(bytes(header))


def test_should_bound_negative_and_unlimited_tar_reads() -> None:
    # Given / When the reader receives exhausted and unlimited requests
    exhausted = probe._bounded_read_size(10, -1)
    unlimited = probe._bounded_read_size(-1, 5)

    # Then each request can inspect at most one byte beyond the remaining cap
    assert exhausted == 1 and unlimited == 6


def test_should_reject_malformed_and_multipart_wheel_end_records(tmp_path: Path) -> None:
    # Given one non-ZIP file and one multi-disk metadata projection
    invalid = tmp_path / "invalid.whl"
    invalid.write_bytes(b"not-a-zip")
    multipart: list[object] = [b"", 1, 0, 1, 1, 1]

    # When / Then neither can authorize central-directory materialization
    with pytest.raises(probe.ProbeError, match="end record"):
        probe._zip_end_record(invalid)
    with pytest.raises(probe.ProbeError, match="multipart"):
        probe._require_single_disk_zip(multipart, 1)


def test_should_reject_noninteger_wheel_end_record_field() -> None:
    # Given / When / Then a malformed stdlib EOCD projection fails closed
    with pytest.raises(probe.ProbeError, match="end record"):
        probe._zip_field(["not-an-integer"], 0)


def test_should_locate_normal_and_zip64_central_directories() -> None:
    # Given equivalent normal and ZIP64 end records with no concatenated prefix
    normal: list[object] = [b"PK\x05\x06", 0, 0, 1, 1, 100, 20, 0, b"", 120]
    zip64 = [*normal]
    zip64[0], zip64[9] = probe.ZIP64_END_SIGNATURE, 196

    # When / Then their different fixed trailers resolve the same directory offset
    assert probe._zip_directory_start(normal, 100) == 20
    assert probe._zip_directory_start(zip64, 100) == 20


def test_should_reject_concatenated_wheel_archive() -> None:
    # Given an EOCD location that implies an untrusted executable/prefix payload
    record: list[object] = [b"PK\x05\x06", 0, 0, 1, 1, 100, 20, 0, b"", 121]

    # When / Then only a standalone wheel archive enters materialization
    with pytest.raises(probe.ProbeError, match="concatenated"):
        probe._zip_directory_start(record, 100)


@pytest.mark.parametrize("header", (b"", b"wrong" + bytes(41)))
def test_should_reject_invalid_raw_zip_directory_header(header: bytes) -> None:
    # Given / When / Then truncated and signature-invalid records fail before allocation
    with pytest.raises(probe.ProbeError, match="record differs"):
        probe._consume_zip_record(io.BytesIO(header), probe.ZIP_CENTRAL_HEADER_BYTES)


def test_should_reject_truncated_raw_zip_variable_fields() -> None:
    # Given a central header whose declared filename lies outside the directory bound
    header = bytearray(probe.ZIP_CENTRAL_HEADER_BYTES)
    header[:4] = probe.ZIP_CENTRAL_SIGNATURE
    struct.pack_into("<HHH", header, probe.ZIP_CENTRAL_LENGTHS_OFFSET, 1, 0, 0)

    # When / Then scanning stops without following the declared region
    with pytest.raises(probe.ProbeError, match="record is truncated"):
        probe._consume_zip_record(io.BytesIO(header), probe.ZIP_CENTRAL_HEADER_BYTES)


def test_should_reject_raw_zip_count_that_differs_from_eocd(tmp_path: Path) -> None:
    # Given one bounded raw directory record but an EOCD projection claiming two
    target = tmp_path / "archive.whl"
    header = bytearray(probe.ZIP_CENTRAL_HEADER_BYTES)
    header[:4] = probe.ZIP_CENTRAL_SIGNATURE
    struct.pack_into("<HHH", header, probe.ZIP_CENTRAL_LENGTHS_OFFSET, 1, 0, 0)
    directory = bytes(header) + b"x"
    target.write_bytes(directory)
    record: list[object] = [b"PK\x05\x06", 0, 0, 2, 2, len(directory), 0, 0, b"", len(directory)]

    # When / Then actual raw inventory must equal the claimed bounded count
    with pytest.raises(probe.ProbeError, match="inventory differs"):
        probe._scan_zip_directory(target, record, 2, len(directory))


def test_should_reject_malformed_zip64_extra_fields() -> None:
    # Given truncated values, truncated TLV framing, and no ZIP64 identifier
    with pytest.raises(probe.ProbeError, match="ZIP64 extra field is truncated"):
        probe._resolve_zip64_value(probe.ZIP32_SENTINEL, b"")
    with pytest.raises(probe.ProbeError, match="extra field is truncated"):
        probe._zip_extra_value(b"x", probe.ZIP64_EXTRA_IDENTIFIER)
    with pytest.raises(probe.ProbeError, match="extra field is truncated"):
        probe._zip_extra_value(b"\x01\0\x08\0x", probe.ZIP64_EXTRA_IDENTIFIER)
    with pytest.raises(probe.ProbeError, match="ZIP64 extra field differs"):
        probe._zip_extra_value(b"\x02\0\0\0", probe.ZIP64_EXTRA_IDENTIFIER)


def test_should_reject_unbounded_raw_zip_name_and_descriptor_fields() -> None:
    # Given malformed encoding, a truncated extra, and an unmatched data descriptor
    record = _raw_zip_record(flags=probe.ZIP_DATA_DESCRIPTOR_FLAG)
    with pytest.raises(probe.ProbeError, match="filename encoding"):
        probe._zip_name(b"\xff", 0)
    with pytest.raises(probe.ProbeError, match="extra field is truncated"):
        probe._read_zip_extra(io.BytesIO(b""), 1)
    with pytest.raises(probe.ProbeError, match="data descriptor"):
        probe._require_zip_record_coverage(io.BytesIO(b"x"), 0, 1, record)


def test_should_reject_invalid_raw_zip_local_records() -> None:
    # Given out-of-range, bad-signature, and payload-truncated local records
    with pytest.raises(probe.ProbeError, match="offset differs"):
        probe._require_zip_local_record(io.BytesIO(), 30, _raw_zip_record(local_offset=1))
    with pytest.raises(probe.ProbeError, match="local record differs"):
        probe._require_zip_local_record(io.BytesIO(bytes(30)), 30, _raw_zip_record())
    header = bytearray(30)
    header[:4] = probe.ZIP_LOCAL_SIGNATURE
    struct.pack_into("<HH", header, probe.ZIP_LOCAL_LENGTHS_OFFSET, 1, 0)
    with pytest.raises(probe.ProbeError, match="local record is truncated"):
        probe._require_zip_local_record(io.BytesIO(header), 30, _raw_zip_record(compressed_size=1))


def test_should_reject_empty_distribution_archive(tmp_path: Path) -> None:
    # Given a zero-byte archive
    archive = tmp_path / "empty.whl"
    archive.write_bytes(b"")

    # When / Then it fails before parser allocation
    with pytest.raises(probe.ProbeError, match="byte bound"):
        probe._require_archive_size(archive)


def test_should_reject_top_level_distribution_symlink(tmp_path: Path) -> None:
    # Given a build output that redirects an apparent product outside its own bytes
    backing = tmp_path / "backing.whl"
    backing.write_bytes(b"not an archive")
    product = tmp_path / "package-1.2.3-py3-none-any.whl"
    product.symlink_to(backing)

    # When / Then only an actual regular top-level archive can enter inspection
    assert not probe._is_file(product)


@pytest.mark.parametrize("name", ("", "/absolute", "../escape", "null\x00name", "safe/file"))
def test_should_classify_archive_member_paths(name: str) -> None:
    # Given / When archive-relative member paths are classified
    safe = probe._safe_member(name)

    # Then only a nonempty contained path is accepted
    assert safe is (name == "safe/file")


def test_should_reject_duplicate_and_excess_member_inventories() -> None:
    # Given duplicate names and a count beyond the fixed archive bound
    # When / Then both inventories fail before metadata parsing
    with pytest.raises(probe.ProbeError, match="duplicate or unsafe"):
        probe._require_member_inventory(("same", "same"), 2)
    with pytest.raises(probe.ProbeError, match="member bound"):
        probe._require_member_inventory(("one",), MAX_DISTRIBUTION_MEMBERS + 1)


def test_should_reject_uncompressed_and_special_wheel_members() -> None:
    # Given one oversized record and one symlink-shaped zip record
    huge = zipfile.ZipInfo("huge")
    huge.file_size = probe.MAX_UNCOMPRESSED_BYTES + 1
    link = zipfile.ZipInfo("link")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16

    # When / Then both fail before any archive bytes are read
    with pytest.raises(probe.ProbeError, match="uncompressed byte"):
        probe._require_zip_bounds([huge])
    with pytest.raises(probe.ProbeError, match="unsupported archive member"):
        probe._require_zip_bounds([link])


def test_should_reject_uncompressed_and_special_sdist_members() -> None:
    # Given one oversized file and one symlink tar record
    huge = tarfile.TarInfo("huge")
    huge.size = probe.MAX_UNCOMPRESSED_BYTES + 1
    link = tarfile.TarInfo("link")
    link.type = tarfile.SYMTYPE

    # When / Then neither can reach metadata parsing
    with pytest.raises(probe.ProbeError, match="uncompressed byte"):
        probe._require_tar_bounds([huge])
    with pytest.raises(probe.ProbeError, match="unsupported archive member"):
        probe._require_tar_bounds([link])


def test_should_reject_missing_wheel_and_sdist_metadata() -> None:
    # Given archive inventories without their one required metadata record
    empty_zip = cast(zipfile.ZipFile, object())
    empty_tar = cast(tarfile.TarFile, object())

    # When / Then both formats fail closed
    with pytest.raises(probe.ProbeError, match="wheel metadata"):
        probe._one_zip_record(empty_zip, [], ".dist-info/METADATA")
    with pytest.raises(probe.ProbeError, match="sdist metadata"):
        probe._one_tar_record(empty_tar, [])


def test_should_reject_unreadable_sdist_metadata() -> None:
    # Given a regular metadata record whose archive stream is unavailable
    member = tarfile.TarInfo("package/PKG-INFO")

    # When / Then absent bytes cannot become package identity
    with pytest.raises(probe.ProbeError, match="could not be read"):
        probe._read_tar_record(cast(tarfile.TarFile, NullTar()), member)


@pytest.mark.parametrize(
    "metadata",
    (
        b"Metadata-Version: 2.4\nVersion: 1.2.3\n",
        b"Metadata-Version: 2.4\nName: package\n",
        b"Metadata-Version: 2.4\nName: one\nName: two\nVersion: 1.2.3\n",
    ),
)
def test_should_reject_incomplete_or_duplicate_core_metadata(metadata: bytes) -> None:
    # Given / When / Then name and version each occur exactly once
    with pytest.raises(probe.ProbeError, match="core metadata"):
        probe._core_metadata(metadata)


@pytest.mark.parametrize("project", ("", "double__separator", "double..separator"))
def test_should_reject_ambiguous_project_metadata(project: str) -> None:
    # Given / When / Then metadata cannot normalize from multiple spellings
    with pytest.raises(probe.ProbeError, match="canonicalizable"):
        probe._canonical_project(project)


def test_should_emit_canonical_probe_payload(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given one CLI argument and a closed observation pair
    monkeypatch.setattr(sys, "argv", ["probe", "dist"])
    monkeypatch.setattr(probe, "inspect_directory", lambda path: _observations())

    # When the probe CLI succeeds
    probe.main()

    # Then it emits compact deterministic JSON only
    output = capsys.readouterr().out.strip()
    assert output.startswith('[{"filename"') and " \n" not in output


def test_should_sanitize_probe_cli_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given an invalid invocation and one bounded parser failure
    monkeypatch.setattr(sys, "argv", ["probe"])
    with pytest.raises(SystemExit, match="usage"):
        probe.main()
    monkeypatch.setattr(sys, "argv", ["probe", "dist"])
    monkeypatch.setattr(probe, "inspect_directory", _fail_probe)

    # When / Then the CLI returns only a fixed error prefix and sanitized cause
    with pytest.raises(SystemExit, match="distribution probe failed: invalid archive"):
        probe.main()


def _fail_probe(path: Path) -> tuple[DistributionObservation, DistributionObservation]:
    raise probe.ProbeError("invalid archive")


def _observations() -> tuple[DistributionObservation, DistributionObservation]:
    wheel = DistributionObservation(
        "package-1.2.3-py3-none-any.whl", "a" * 64, "wheel", "package", "1.2.3", 2, 20
    )
    sdist = DistributionObservation(
        "package-1.2.3.tar.gz", "b" * 64, "sdist", "package", "1.2.3", 2, 20
    )
    return wheel, sdist


def _raw_zip_record(
    *, flags: int = 0, local_offset: int = 0, compressed_size: int = 0
) -> probe._ZipRecord:
    return probe._ZipRecord(
        raw_name=b"x",
        name="x",
        flags=flags,
        compression=0,
        crc=0,
        compressed_size=compressed_size,
        uncompressed_size=0,
        local_offset=local_offset,
    )


def _tar_header(kind: bytes) -> bytes:
    info = tarfile.TarInfo("package/file")
    header = bytearray(info.tobuf())
    header[156:157] = kind
    header[148:156] = b" " * 8
    header[148:156] = f"{sum(header):06o}\0 ".encode()
    return bytes(header)
