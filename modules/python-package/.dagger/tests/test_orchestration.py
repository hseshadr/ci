from __future__ import annotations

import asyncio
from typing import cast

import dagger
import pytest

from python_package import orchestration
from python_package.candidate import manifest_from
from python_package.distributions import DistributionObservation, DistributionSet
from python_package.identity import CandidateIdentity, PackageIdentity, SourceIdentity
from python_package.orchestration import BuildResult


class FakeContainer:
    def __init__(self, events: list[str], directory: dagger.Directory) -> None:
        self.events = events
        self.value = directory

    async def sync(self) -> None:
        self.events.append("sync")

    def directory(self, path: str) -> dagger.Directory:
        assert path == "/work/dist"
        self.events.append("directory")
        return self.value


class FakeFile:
    def __init__(self, contents: str) -> None:
        self.value = contents

    async def contents(self) -> str:
        return self.value


class FakeArtifact:
    def __init__(self, contents: str, directory: dagger.Directory) -> None:
        self.contents = contents
        self.value = directory

    def file(self, path: str) -> FakeFile:
        assert path == "metadata/python-candidate.json"
        return FakeFile(self.contents)

    def directory(self, path: str) -> dagger.Directory:
        assert path == "dist"
        return self.value


class FakeFoundation:
    def __init__(self, artifact: FakeArtifact) -> None:
        self.artifact = artifact
        self.arguments: tuple[str, str, tuple[str, ...]] | None = None

    def verify_envelope(
        self,
        *,
        envelope: dagger.Directory,
        consumer_identity: str,
        producing_identity: str,
        allowed_roots: list[str],
    ) -> FakeArtifact:
        assert envelope is not None
        self.arguments = consumer_identity, producing_identity, tuple(allowed_roots)
        return self.artifact


class FakeDag:
    def __init__(self, foundation: FakeFoundation) -> None:
        self.value = foundation

    def foundation(self) -> FakeFoundation:
        return self.value


class FakeArtifactBuilder:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def with_directory(self, path: str, directory: dagger.Directory) -> FakeArtifactBuilder:
        assert path == "dist" and directory is not None
        self.events.append("dist")
        return self

    def with_new_file(self, path: str, contents: str, *, permissions: int) -> FakeArtifactBuilder:
        assert path == "metadata/python-candidate.json" and contents.startswith("{")
        assert permissions == 0o644
        self.events.append("metadata")
        return self


class EnvelopeFoundation:
    def __init__(self, events: list[str], envelope: dagger.Directory) -> None:
        self.events = events
        self.value = envelope

    def envelope(
        self,
        *,
        artifact: dagger.Directory,
        consumer_identity: str,
        producing_identity: str,
        allowed_roots: list[str],
    ) -> dagger.Directory:
        assert artifact is not None and "@" in consumer_identity and ":" in producing_identity
        assert allowed_roots == ["dist", "metadata"]
        self.events.append("foundation")
        return self.value


class EnvelopeDag:
    def __init__(self, events: list[str], envelope: dagger.Directory) -> None:
        self.events = events
        self.value = envelope

    def directory(self) -> FakeArtifactBuilder:
        return FakeArtifactBuilder(self.events)

    def foundation(self) -> EnvelopeFoundation:
        return EnvelopeFoundation(self.events, self.value)


class BuildAdapters:
    def __init__(self, events: list[str], directory: dagger.Directory) -> None:
        self.events = events
        self.directory = directory

    async def guard(self, source: dagger.Directory, identity: object) -> dagger.Directory:
        self.events.append("guard")
        return source

    def audit(self, source: dagger.Directory) -> dagger.Container:
        self.events.append("audit")
        return cast(dagger.Container, FakeContainer(self.events, source))

    def build(self, source: dagger.Directory) -> dagger.Container:
        self.events.append("build")
        return cast(dagger.Container, FakeContainer(self.events, self.directory))

    async def probe(self, source: dagger.Directory) -> tuple[DistributionObservation, ...]:
        assert source is self.directory
        self.events.append("probe")
        return _products().observations

    async def twine(
        self, source: dagger.Directory, observed: tuple[DistributionObservation, ...]
    ) -> None:
        assert source is self.directory and len(observed) == 2
        self.events.append("twine")


class CandidateAdapters:
    def __init__(self, events: list[str], result: BuildResult, envelope: dagger.Directory) -> None:
        self.events = events
        self.result = result
        self.envelope = envelope

    async def green(self, token: dagger.Secret, identity: CandidateIdentity) -> None:
        assert token is not None and identity == _identity()
        self.events.append("green")

    async def build(self, source: dagger.Directory, identity: CandidateIdentity) -> BuildResult:
        assert source is not None and identity == _identity()
        self.events.append("build")
        return self.result

    async def tag(self, identity: CandidateIdentity, value: str) -> None:
        assert identity == _identity()
        self.events.append(f"tag:{value}")

    async def wrap(
        self, built: BuildResult, manifest: object, identity: CandidateIdentity
    ) -> dagger.Directory:
        assert built is self.result and manifest is not None and identity == _identity()
        self.events.append("envelope")
        return self.envelope

    async def verify(
        self, value: dagger.Directory, identity: CandidateIdentity
    ) -> tuple[object, object]:
        assert value is self.envelope and identity == _identity()
        self.events.append("verify")
        return object(), object()


class VerifyAdapters:
    async def probe(self, source: dagger.Directory) -> tuple[DistributionObservation, ...]:
        assert source is not None
        return _products().observations

    async def twine(
        self, source: dagger.Directory, observed: tuple[DistributionObservation, ...]
    ) -> None:
        assert source is not None and len(observed) == 2


def test_should_guard_audit_build_probe_and_check_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given observable adapters around one exact package identity
    events: list[str] = []
    source = cast(dagger.Directory, object())
    directory = cast(dagger.Directory, object())
    _patch_build(monkeypatch, events, directory)

    # When the unprivileged build composition runs
    result = asyncio.run(orchestration.build_release(source, _package()))

    # Then every security and product boundary runs before a result escapes
    assert events == ["guard", "audit", "sync", "build", "directory", "probe", "twine"]
    assert result.directory is directory
    assert result.products.version == "0.4.2"


def test_should_create_candidate_between_two_green_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given instrumented live evidence, build, tag, envelope, and verification adapters
    events: list[str] = []
    identity = _identity()
    result = BuildResult(cast(dagger.Directory, object()), _products())
    envelope = cast(dagger.Directory, object())
    _patch_candidate(monkeypatch, events, result, envelope)

    # When one retry-bound candidate is created
    actual, manifest, built = asyncio.run(
        orchestration.create_candidate(
            cast(dagger.Directory, object()), cast(dagger.Secret, object()), identity
        )
    )

    # Then exact-green is re-read after build and the finished envelope is self-verified
    assert events == ["green", "build", "tag:v0.4.2", "green", "envelope", "verify"]
    assert actual is envelope and built is result
    assert manifest.run_attempt == identity.workflow.attempt


def test_should_verify_foundation_envelope_against_actual_distribution_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a Foundation-verified artifact with canonical candidate metadata
    identity = _identity()
    directory = cast(dagger.Directory, object())
    manifest = manifest_from(identity, _products())
    foundation = FakeFoundation(FakeArtifact(manifest.canonical_json(), directory))
    monkeypatch.setattr(orchestration, "dag", cast(dagger.Client, FakeDag(foundation)))
    _patch_verifier(monkeypatch)

    # When source-free candidate verification runs
    parsed, result = asyncio.run(
        orchestration.verify_candidate_envelope(cast(dagger.Directory, object()), identity)
    )

    # Then Foundation identity, retry metadata, and probed products all agree
    assert parsed == manifest
    assert result.directory is directory
    assert foundation.arguments == (
        identity.consumer_identity,
        identity.producing_identity,
        ("dist", "metadata"),
    )


def test_should_construct_guarded_dependency_audit_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a source-only exact identity and observable Foundation/audit adapters
    events: list[str] = []
    source = cast(dagger.Directory, object())
    container = FakeContainer(events, source)

    async def guard(value: dagger.Directory, identity: SourceIdentity) -> dagger.Directory:
        assert value is source and identity.commit.value == "a" * 40
        events.append("guard")
        return source

    def audit(value: dagger.Directory) -> dagger.Container:
        assert value is source
        events.append("audit")
        return cast(dagger.Container, container)

    monkeypatch.setattr(orchestration, "guarded_source", guard)
    monkeypatch.setattr(orchestration, "dependency_audit_container", audit)

    # When the public audit plan is composed
    actual = asyncio.run(
        orchestration.audit_release_source(
            source, SourceIdentity.parse("hseshadr/edgeproc-core", "a" * 40)
        )
    )

    # Then the graph is guarded but remains lazy for the caller to evaluate
    assert cast(object, actual) is container
    assert events == ["guard", "audit"]


def test_should_hash_only_canonical_candidate_manifest() -> None:
    # Given canonical release-candidate metadata
    manifest = manifest_from(_identity(), _products())

    # When its public handoff digest is calculated
    first = orchestration.manifest_digest(manifest)
    second = orchestration.manifest_digest(manifest)

    # Then it is deterministic SHA-256 evidence
    assert first == second and len(first) == 64


def test_should_wrap_only_dist_and_candidate_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given built distributions, canonical metadata, and a Foundation graph recorder
    events: list[str] = []
    envelope = cast(dagger.Directory, object())
    identity = _identity()
    result = BuildResult(cast(dagger.Directory, object()), _products())
    manifest = manifest_from(identity, result.products)
    monkeypatch.setattr(orchestration, "dag", cast(dagger.Client, EnvelopeDag(events, envelope)))

    # When the internal candidate artifact is enveloped
    actual = asyncio.run(orchestration._envelope(result, manifest, identity))

    # Then no source tree or extra root enters the publisher handoff
    assert actual is envelope
    assert events == ["dist", "metadata", "foundation"]


def _patch_build(
    monkeypatch: pytest.MonkeyPatch, events: list[str], directory: dagger.Directory
) -> None:
    adapters = BuildAdapters(events, directory)
    monkeypatch.setattr(orchestration, "guarded_source", adapters.guard)
    monkeypatch.setattr(orchestration, "dependency_audit_container", adapters.audit)
    monkeypatch.setattr(orchestration, "build_container", adapters.build)
    monkeypatch.setattr(orchestration, "inspect_products", adapters.probe)
    monkeypatch.setattr(orchestration, "check_distributions", adapters.twine)


def _patch_candidate(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
    result: BuildResult,
    envelope: dagger.Directory,
) -> None:
    adapters = CandidateAdapters(events, result, envelope)
    monkeypatch.setattr(orchestration, "green_evidence", adapters.green)
    monkeypatch.setattr(orchestration, "build_release", adapters.build)
    monkeypatch.setattr(orchestration, "require_tag", adapters.tag)
    monkeypatch.setattr(orchestration, "_envelope", adapters.wrap)
    monkeypatch.setattr(orchestration, "verify_candidate_envelope", adapters.verify)


def _patch_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    adapters = VerifyAdapters()
    monkeypatch.setattr(orchestration, "inspect_products", adapters.probe)
    monkeypatch.setattr(orchestration, "check_distributions", adapters.twine)


def _package() -> PackageIdentity:
    return PackageIdentity.parse("hseshadr/edgeproc-core", "a" * 40, "edgeproc-core")


def _identity() -> CandidateIdentity:
    return CandidateIdentity.from_package(_package(), "b" * 40, "6100", 2)


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
