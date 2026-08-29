"""Fail-closed composition of Foundation, build, and artifact verification."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import dagger
from dagger import dag

from .candidate import CandidateManifest, manifest_from, require_manifest_binding
from .distributions import DistributionSet
from .identity import CandidateIdentity, PackageIdentity, SourceIdentity
from .runtime import (
    build_container,
    check_distributions,
    dependency_audit_container,
    green_evidence,
    guarded_source,
    inspect_products,
    require_tag,
)


@dataclass(frozen=True)
class BuildResult:
    """Internal correlation of product bytes with parsed package evidence."""

    directory: dagger.Directory
    products: DistributionSet


async def audit_release_source(
    source: dagger.Directory, identity: SourceIdentity
) -> dagger.Container:
    """Return an audit graph after exact source identity and Foundation guard."""
    bound = await guarded_source(source, identity)
    return dependency_audit_container(bound)


async def build_release(
    source: dagger.Directory, identity: PackageIdentity | CandidateIdentity
) -> BuildResult:
    """Guard, audit, build, inspect, and check one package release unit."""
    bound = await guarded_source(source, identity)
    await dependency_audit_container(bound).sync()
    return await _build_bound(bound, identity)


async def create_candidate(
    source: dagger.Directory, github_token: dagger.Secret, identity: CandidateIdentity
) -> tuple[dagger.Directory, CandidateManifest, BuildResult]:
    """Create a Foundation envelope between two exact-green observations."""
    await green_evidence(github_token, identity)
    result = await build_release(source, identity)
    await require_tag(identity, result.products.tag)
    await green_evidence(github_token, identity)
    manifest = manifest_from(identity, result.products)
    envelope = await _envelope(result, manifest, identity)
    await verify_candidate_envelope(envelope, identity)
    return envelope, manifest, result


async def verify_candidate_envelope(
    envelope: dagger.Directory, identity: CandidateIdentity
) -> tuple[CandidateManifest, BuildResult]:
    """Verify Foundation evidence, candidate metadata, and actual distribution bytes."""
    artifact = dag.foundation().verify_envelope(
        envelope=envelope,
        consumer_identity=identity.consumer_identity,
        producing_identity=identity.producing_identity,
        allowed_roots=["dist", "metadata"],
    )
    manifest = await _candidate_manifest(artifact)
    result = await _verify_artifact(artifact, identity)
    require_manifest_binding(manifest, identity, result.products)
    return manifest, result


async def _candidate_manifest(artifact: dagger.Directory) -> CandidateManifest:
    value = await artifact.file("metadata/python-candidate.json").contents()
    return CandidateManifest.from_canonical_json(value)


async def _build_bound(
    source: dagger.Directory, identity: PackageIdentity | CandidateIdentity
) -> BuildResult:
    container = build_container(source)
    directory = container.directory("/work/dist")
    observations = await inspect_products(directory)
    products = DistributionSet.parse(observations, identity.project.value)
    await check_distributions(directory, products.observations)
    return BuildResult(directory, products)


async def _envelope(
    result: BuildResult, manifest: CandidateManifest, identity: CandidateIdentity
) -> dagger.Directory:
    artifact = dag.directory().with_directory("dist", result.directory)
    artifact = artifact.with_new_file(
        "metadata/python-candidate.json", manifest.canonical_json(), permissions=0o644
    )
    return dag.foundation().envelope(
        artifact=artifact,
        consumer_identity=identity.consumer_identity,
        producing_identity=identity.producing_identity,
        allowed_roots=["dist", "metadata"],
    )


async def _verify_artifact(artifact: dagger.Directory, identity: CandidateIdentity) -> BuildResult:
    directory = artifact.directory("dist")
    observations = await inspect_products(directory)
    products = DistributionSet.parse(observations, identity.project.value)
    await check_distributions(directory, products.observations)
    return BuildResult(directory, products)


def manifest_digest(manifest: CandidateManifest) -> str:
    """Return the candidate manifest's canonical SHA-256 identity."""
    return hashlib.sha256(manifest.canonical_json().encode()).hexdigest()
