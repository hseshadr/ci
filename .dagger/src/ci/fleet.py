"""Fleet-wide Dagger control-plane evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

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
        expectation("almamesh", grandfathered_until=date(2026, 12, 15)),
        expectation("aml-filter", grandfathered_until=date(2026, 12, 31)),
        expectation("assay", linear_history=True, grandfathered_until=date(2026, 11, 30)),
        expectation("edge-proc", grandfathered_until=date(2026, 10, 15)),
        expectation("edge-reco", grandfathered_until=date(2026, 9, 30)),
        expectation("edgeproc-core", grandfathered_until=date(2026, 10, 31)),
        expectation("privacy-core", grandfathered_until=date(2026, 11, 15)),
    )
    central = expectation("ci", conversation_resolution=False, shared_foundation_required=False)
    return consumers + ((central,) if include_central else ())


def expectation(
    name: str,
    *,
    linear_history: bool = False,
    conversation_resolution: bool = True,
    shared_foundation_required: bool = True,
    grandfathered_until: date | None = None,
) -> RepositoryExpectation:
    """Build one sole-Dagger branch-protection expectation."""
    protection = (name, ("Dagger",), linear_history, conversation_resolution)
    rollout = (shared_foundation_required, grandfathered_until)
    return RepositoryExpectation(*protection, *rollout)


def expectation_for(name: str) -> RepositoryExpectation:
    """Return one explicit rollout contract by repository name."""
    match = next((item for item in repository_expectations(True) if item.name == name), None)
    if match is None:
        raise ValueError(f"unknown fleet repository: {name}")
    return match


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
