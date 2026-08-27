"""Fleet-wide Dagger control-plane evaluation."""

from __future__ import annotations

from dataclasses import dataclass

from ci.fleet_policy import PolicyFinding, RepositoryExpectation, validate_repository
from ci.github_fleet import GitHubHttpTransport, read_repository


@dataclass(frozen=True)
class RepositoryResult:
    """Exact-main identity and policy result for one repository."""

    name: str
    sha: str
    findings: tuple[PolicyFinding, ...]


def repository_expectations(include_central: bool) -> tuple[RepositoryExpectation, ...]:
    """Return the reviewed fleet contract for the current rollout phase."""
    consumers = (
        expectation("almamesh"),
        expectation("aml-filter"),
        expectation("assay", linear_history=True),
        expectation("edge-proc"),
        expectation("edge-reco"),
        expectation("edgeproc-core"),
        expectation("privacy-core"),
    )
    central = expectation("ci", conversation_resolution=False)
    return consumers + ((central,) if include_central else ())


def expectation(
    name: str, *, linear_history: bool = False, conversation_resolution: bool = True
) -> RepositoryExpectation:
    """Build one sole-Dagger branch-protection expectation."""
    return RepositoryExpectation(
        name=name,
        required_contexts=("Dagger",),
        linear_history=linear_history,
        conversation_resolution=conversation_resolution,
    )


def scan_fleet(token: str, include_central: bool) -> tuple[RepositoryResult, ...]:
    """Read and evaluate each repository from authoritative exact-main evidence."""
    transport = GitHubHttpTransport(token)
    expectations = repository_expectations(include_central)
    return tuple(scan_repository(transport, item) for item in expectations)


def scan_repository(
    transport: GitHubHttpTransport, expectation_: RepositoryExpectation
) -> RepositoryResult:
    """Evaluate one exact-main repository against its reviewed contract."""
    snapshot = read_repository(transport, "hseshadr", expectation_.name)
    findings = validate_repository(snapshot, expectation_)
    return RepositoryResult(snapshot.name, snapshot.sha, findings)
