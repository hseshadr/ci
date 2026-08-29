"""Public Dagger API for closed Python package release candidates."""

from __future__ import annotations

import dagger
from dagger import field, function, object_type

from .identity import CandidateIdentity, PackageIdentity, SourceIdentity
from .orchestration import (
    BuildResult,
    audit_release_source,
    build_release,
    create_candidate,
    manifest_digest,
    verify_candidate_envelope,
)


@object_type
class BuiltPythonPackage:
    """A validated unprivileged build result."""

    directory: dagger.Directory = field()
    project: str = field()
    version: str = field()
    wheel_filename: str = field()
    wheel_sha256: str = field()
    sdist_filename: str = field()
    sdist_sha256: str = field()


@object_type
class PythonPackageCandidate:
    """A Foundation envelope safe for a separate source-free publisher job."""

    envelope: dagger.Directory = field()
    repository: str = field()
    commit_sha: str = field()
    tag: str = field()
    project: str = field()
    version: str = field()
    workflow_run_id: str = field()
    run_attempt: int = field()
    manifest_sha256: str = field()

    @function
    def serialization(self) -> str:
        """Return the only non-secret candidate handoff metadata needed by a publisher."""
        return (
            f"{self.repository}@{self.commit_sha}:{self.tag}:"
            f"{self.workflow_run_id}:{self.run_attempt}:{self.manifest_sha256}"
        )


@object_type
class PythonPackage:
    """Build and verify packages without registry or publication authority."""

    @function
    async def dependency_audit(
        self, source: dagger.Directory, repository: str, commit_sha: str
    ) -> dagger.Container:
        """Audit the locked dependency graph in a bound source tree."""
        identity = SourceIdentity.parse(repository, commit_sha)
        return await audit_release_source(source, identity)

    @function
    async def build(
        self,
        source: dagger.Directory,
        repository: str,
        commit_sha: str,
        project_name: str,
    ) -> BuiltPythonPackage:
        """Build exactly one pure wheel and one source distribution."""
        identity = PackageIdentity.parse(repository, commit_sha, project_name)
        return _public_build(await build_release(source, identity))

    # fmt: off
    @function(cache="never")  # type: ignore[call-overload,untyped-decorator]  # SDK stub gap
    async def candidate(
        self, source: dagger.Directory, github_token: dagger.Secret,
        repository: str, commit_sha: str, project_name: str,
        central_module_sha: str, workflow_run_id: str, run_attempt: int,
    ) -> PythonPackageCandidate:
        """Create a retry-bound Foundation envelope after exact-green checks."""
        identity = _candidate_identity(repository, commit_sha, project_name,
                                       central_module_sha, workflow_run_id, run_attempt)
        return await _created_candidate(source, github_token, identity)
    # fmt: on

    # fmt: off
    @function
    async def verify_candidate(
        self, envelope: dagger.Directory, repository: str, commit_sha: str,
        project_name: str, central_module_sha: str,
        workflow_run_id: str, run_attempt: int,
    ) -> PythonPackageCandidate:
        """Revalidate a candidate without source, credentials, or provider access."""
        identity = _candidate_identity(repository, commit_sha, project_name,
                                       central_module_sha, workflow_run_id, run_attempt)
        return await _verified_candidate(envelope, identity)
    # fmt: on


def _candidate_identity(
    repository: str,
    commit_sha: str,
    project_name: str,
    central_module_sha: str,
    workflow_run_id: str,
    run_attempt: int,
) -> CandidateIdentity:
    package = PackageIdentity.parse(repository, commit_sha, project_name)
    return CandidateIdentity.from_package(
        package,
        central_module_sha,
        workflow_run_id,
        run_attempt,
    )


def _public_build(result: BuildResult) -> BuiltPythonPackage:
    value = BuiltPythonPackage.__new__(BuiltPythonPackage)
    value.directory = result.directory
    value.project = result.products.project
    value.version = result.products.version
    value.wheel_filename = result.products.wheel.filename
    value.wheel_sha256 = result.products.wheel.sha256
    value.sdist_filename = result.products.sdist.filename
    value.sdist_sha256 = result.products.sdist.sha256
    return value


def _public_candidate(
    envelope: dagger.Directory,
    digest: str,
    identity: CandidateIdentity,
    result: BuildResult,
) -> PythonPackageCandidate:
    value = PythonPackageCandidate.__new__(PythonPackageCandidate)
    value.envelope = envelope
    _set_candidate_identity(value, identity)
    _set_candidate_products(value, result)
    value.manifest_sha256 = digest
    return value


def _set_candidate_identity(value: PythonPackageCandidate, identity: CandidateIdentity) -> None:
    value.repository = identity.repository.value
    value.commit_sha = identity.commit.value
    value.workflow_run_id = identity.workflow.run_id
    value.run_attempt = identity.workflow.attempt


def _set_candidate_products(value: PythonPackageCandidate, result: BuildResult) -> None:
    value.tag = result.products.tag
    value.project = result.products.project
    value.version = result.products.version


async def _created_candidate(
    source: dagger.Directory, token: dagger.Secret, identity: CandidateIdentity
) -> PythonPackageCandidate:
    envelope, manifest, result = await create_candidate(source, token, identity)
    return _public_candidate(envelope, manifest_digest(manifest), identity, result)


async def _verified_candidate(
    envelope: dagger.Directory, identity: CandidateIdentity
) -> PythonPackageCandidate:
    manifest, result = await verify_candidate_envelope(envelope, identity)
    return _public_candidate(envelope, manifest_digest(manifest), identity, result)
