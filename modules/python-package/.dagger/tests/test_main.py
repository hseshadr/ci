from __future__ import annotations

import asyncio
from typing import cast

import dagger
import pytest

from python_package import main
from python_package.candidate import manifest_from
from python_package.distributions import DistributionObservation, DistributionSet
from python_package.main import (
    BuiltPythonPackage,
    PythonPackage,
    PythonPackageCandidate,
)
from python_package.orchestration import BuildResult


def test_should_return_lazy_guarded_dependency_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the public module and an observable audit plan
    package = PythonPackage.__new__(PythonPackage)
    source = cast(dagger.Directory, object())
    expected = cast(dagger.Container, object())

    async def audit(value: dagger.Directory, identity: object) -> dagger.Container:
        assert value is source and identity is not None
        return expected

    monkeypatch.setattr(main, "audit_release_source", audit)

    # When the closed audit function is called
    actual: dagger.Container = asyncio.run(
        package.dependency_audit(source, "hseshadr/example", "a" * 40)
    )

    # Then the guarded typed graph crosses the public boundary unchanged
    assert actual is expected


def test_should_project_validated_build_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given an internal build result containing actual distribution observations
    package = PythonPackage.__new__(PythonPackage)
    result = BuildResult(cast(dagger.Directory, object()), _products())

    async def build(source: dagger.Directory, identity: object) -> BuildResult:
        assert source is not None and identity is not None
        return result

    monkeypatch.setattr(main, "build_release", build)

    # When the public build operation completes
    actual: BuiltPythonPackage = asyncio.run(
        package.build(
            cast(dagger.Directory, object()), "hseshadr/edgeproc-core", "a" * 40, "edgeproc-core"
        )
    )

    # Then consumers receive only typed product bytes and evidence
    assert actual.directory is result.directory
    assert (actual.project, actual.version) == ("edgeproc-core", "0.4.2")
    assert actual.wheel_filename == result.products.wheel.filename
    assert actual.sdist_sha256 == result.products.sdist.sha256


def test_should_project_created_retry_bound_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a validated internal candidate result
    package = PythonPackage.__new__(PythonPackage)
    identity = main._candidate_identity(
        "hseshadr/edgeproc-core", "a" * 40, "edgeproc-core", "b" * 40, "6100", 2
    )
    result = BuildResult(cast(dagger.Directory, object()), _products())
    manifest = manifest_from(identity, result.products)
    envelope = cast(dagger.Directory, object())

    async def create(
        source: dagger.Directory, token: dagger.Secret, actual: object
    ) -> tuple[dagger.Directory, object, BuildResult]:
        assert source is not None and token is not None and actual == identity
        return envelope, manifest, result

    monkeypatch.setattr(main, "create_candidate", create)

    # When the live candidate operation is called
    actual: PythonPackageCandidate = asyncio.run(
        package.candidate(
            cast(dagger.Directory, object()),
            cast(dagger.Secret, object()),
            "hseshadr/edgeproc-core",
            "a" * 40,
            "edgeproc-core",
            "b" * 40,
            "6100",
            2,
        )
    )

    # Then the result is source-free, attempt-bound, and digest-addressed
    assert actual.envelope is envelope
    assert actual.tag == "v0.4.2" and actual.run_attempt == 2
    assert len(actual.manifest_sha256) == 64


def test_should_project_source_free_candidate_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given one Foundation envelope and its expected retry identity
    package = PythonPackage.__new__(PythonPackage)
    identity = main._candidate_identity(
        "hseshadr/edgeproc-core", "a" * 40, "edgeproc-core", "b" * 40, "6100", 2
    )
    result = BuildResult(cast(dagger.Directory, object()), _products())
    manifest = manifest_from(identity, result.products)
    envelope = cast(dagger.Directory, object())

    async def verify(value: dagger.Directory, actual: object) -> tuple[object, BuildResult]:
        assert value is envelope and actual == identity
        return manifest, result

    monkeypatch.setattr(main, "verify_candidate_envelope", verify)

    # When the credential-free verifier is called
    actual: PythonPackageCandidate = asyncio.run(
        package.verify_candidate(
            envelope,
            "hseshadr/edgeproc-core",
            "a" * 40,
            "edgeproc-core",
            "b" * 40,
            "6100",
            2,
        )
    )

    # Then it returns the original envelope with revalidated public evidence
    assert actual.envelope is envelope
    assert actual.repository == "hseshadr/edgeproc-core"
    assert actual.version == "0.4.2"


def test_should_serialize_nonsecret_candidate_handoff() -> None:
    # Given a projected candidate object
    candidate = PythonPackageCandidate.__new__(PythonPackageCandidate)
    candidate.repository = "hseshadr/edgeproc-core"
    candidate.commit_sha = "a" * 40
    candidate.tag = "v0.4.2"
    candidate.workflow_run_id = "6100"
    candidate.run_attempt = 2
    candidate.manifest_sha256 = "d" * 64

    # When its scalar handoff is serialized
    value = cast(str, candidate.serialization())

    # Then no source, credential, path, or registry value is exposed
    assert value == f"hseshadr/edgeproc-core@{'a' * 40}:v0.4.2:6100:2:{'d' * 64}"
    assert "token" not in value and "pypi" not in value


def _products() -> DistributionSet:
    observed = (
        DistributionObservation(
            "edgeproc_core-0.4.2-py3-none-any.whl",
            "d" * 64,
            "wheel",
            "edgeproc-core",
            "0.4.2",
            20,
            40_000,
        ),
        DistributionObservation(
            "edgeproc_core-0.4.2.tar.gz",
            "e" * 64,
            "sdist",
            "edgeproc-core",
            "0.4.2",
            30,
            50_000,
        ),
    )
    return DistributionSet.parse(observed, "edgeproc-core")
