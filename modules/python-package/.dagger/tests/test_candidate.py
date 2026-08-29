from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from python_package.candidate import CandidateManifest, manifest_from, require_manifest_binding
from python_package.distributions import DistributionObservation, DistributionSet
from python_package.identity import CandidateIdentity, PackageIdentity

SHA = "a" * 40
CENTRAL_SHA = "b" * 40


def test_should_serialize_closed_canonical_candidate_manifest() -> None:
    # Given a validated identity and built distribution set
    identity = _identity()

    # When the publication candidate manifest is created
    manifest = manifest_from(identity, _distributions())

    # Then it is deterministic, retry-bound, and contains no credential field
    serialized = manifest.canonical_json()
    assert serialized == json.dumps(json.loads(serialized), separators=(",", ":"), sort_keys=True)
    assert manifest.tag == "v0.4.2"
    assert manifest.run_attempt == 2
    assert "token" not in serialized and "secret" not in serialized


def test_should_reject_extra_candidate_manifest_fields() -> None:
    # Given one otherwise-valid canonical manifest payload
    payload = manifest_from(
        _identity(),
        _distributions(),
    ).model_dump()

    # When an unrecognized publication control is injected
    payload["registry"] = "https://upload.pypi.org"

    # Then the closed schema rejects it
    with pytest.raises(ValidationError):
        CandidateManifest.model_validate(payload)


def test_should_reparse_only_canonical_candidate_json() -> None:
    # Given a valid model rendered with noncanonical whitespace
    manifest = manifest_from(
        _identity(),
        _distributions(),
    )
    altered = json.dumps(manifest.model_dump(), indent=2)

    # When / Then envelope verification rejects alternate serializations
    with pytest.raises(ValueError, match="canonical"):
        CandidateManifest.from_canonical_json(altered)


def test_should_reject_manifest_bound_to_another_workflow_attempt() -> None:
    # Given valid candidate evidence for attempt two
    manifest = manifest_from(
        _identity(),
        _distributions(),
    )
    package = PackageIdentity.parse("hseshadr/edgeproc-core", SHA, "edgeproc-core")
    expected = CandidateIdentity.from_package(package, CENTRAL_SHA, "6100", 3)

    # When / Then a retry cannot consume the previous attempt's metadata
    with pytest.raises(ValueError, match="identity differs"):
        require_manifest_binding(manifest, expected, _distributions())


def _distributions() -> DistributionSet:
    records = (
        DistributionObservation(
            filename="edgeproc_core-0.4.2-py3-none-any.whl",
            sha256="d" * 64,
            kind="wheel",
            project="edgeproc-core",
            version="0.4.2",
            member_count=21,
            size=37_231,
        ),
        DistributionObservation(
            filename="edgeproc_core-0.4.2.tar.gz",
            sha256="e" * 64,
            kind="sdist",
            project="edgeproc-core",
            version="0.4.2",
            member_count=58,
            size=100_700,
        ),
    )
    return DistributionSet.parse(records, "edgeproc-core")


def _identity() -> CandidateIdentity:
    package = PackageIdentity.parse("hseshadr/edgeproc-core", SHA, "edgeproc-core")
    return CandidateIdentity.from_package(package, CENTRAL_SHA, "6100", 2)
