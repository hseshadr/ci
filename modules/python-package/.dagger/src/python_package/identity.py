"""Canonical repository, package, and workflow identities."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Protocol

SHA_PATTERN: Final = re.compile(r"[0-9a-f]{40}")
OWNER_PATTERN: Final = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
REPOSITORY_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
PROJECT_PATTERN: Final = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
RUN_ID_PATTERN: Final = re.compile(r"[1-9][0-9]*")
MAX_RUN_ID: Final = (2**64) - 1
MAX_RUN_ATTEMPT: Final = 1_000


@dataclass(frozen=True)
class FullSha:
    """A full lowercase Git SHA-1 identifier."""

    value: str

    def __post_init__(self) -> None:
        if SHA_PATTERN.fullmatch(self.value) is None:
            raise ValueError("SHA must be a lowercase 40-character hexadecimal value")


@dataclass(frozen=True)
class RepositoryRef:
    """A canonical public GitHub owner/repository pair."""

    owner: str
    name: str

    def __post_init__(self) -> None:
        valid_owner = OWNER_PATTERN.fullmatch(self.owner) is not None
        valid_name = REPOSITORY_PATTERN.fullmatch(self.name) is not None
        if not valid_owner or not valid_name or self.name.endswith(".git"):
            raise ValueError("repository must use canonical GitHub owner/repository components")

    @classmethod
    def parse(cls, value: str) -> RepositoryRef:
        """Parse only the canonical owner/repository spelling."""
        if value.count("/") != 1:
            raise ValueError("repository must be owner/repository")
        return cls(*value.split("/", maxsplit=1))

    @property
    def value(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def github_url(self) -> str:
        return f"https://github.com/{self.value}.git"


@dataclass(frozen=True)
class ProjectName:
    """One PEP 503 canonical distribution name."""

    value: str

    def __post_init__(self) -> None:
        if PROJECT_PATTERN.fullmatch(self.value) is None:
            raise ValueError("project name must use its PEP 503 canonical spelling")


@dataclass(frozen=True)
class WorkflowIdentity:
    """One bounded GitHub Actions run attempt."""

    run_id: str
    attempt: int

    def __post_init__(self) -> None:
        valid_run = RUN_ID_PATTERN.fullmatch(self.run_id) is not None
        if not valid_run or int(self.run_id) > MAX_RUN_ID:
            raise ValueError("workflow run ID must be a positive uint64 decimal")
        if not 1 <= self.attempt <= MAX_RUN_ATTEMPT:
            raise ValueError("workflow run attempt is outside the supported bound")


@dataclass(frozen=True)
class SourceIdentity:
    """One exact repository commit accepted by unprivileged operations."""

    repository: RepositoryRef
    commit: FullSha

    @classmethod
    def parse(cls, repository: str, commit_sha: str) -> SourceIdentity:
        return cls(RepositoryRef.parse(repository), FullSha(commit_sha))


class RepositoryCommit(Protocol):
    """Structural repository/commit identity consumed by Foundation adapters."""

    @property
    def repository(self) -> RepositoryRef: ...

    @property
    def commit(self) -> FullSha: ...


@dataclass(frozen=True)
class PackageIdentity:
    """One exact source and canonical Python distribution name."""

    repository: RepositoryRef
    commit: FullSha
    project: ProjectName

    @classmethod
    def parse(cls, repository: str, commit_sha: str, project_name: str) -> PackageIdentity:
        return cls(RepositoryRef.parse(repository), FullSha(commit_sha), ProjectName(project_name))


@dataclass(frozen=True)
class CandidateIdentity:
    """All immutable identities required for one release candidate."""

    repository: RepositoryRef
    commit: FullSha
    project: ProjectName
    central_module: FullSha
    workflow: WorkflowIdentity

    @classmethod
    def from_package(
        cls,
        package: PackageIdentity,
        central_module_sha: str,
        workflow_run_id: str,
        run_attempt: int,
    ) -> CandidateIdentity:
        """Validate every scalar at the Dagger boundary."""
        return cls(
            package.repository,
            package.commit,
            package.project,
            FullSha(central_module_sha),
            WorkflowIdentity(workflow_run_id, run_attempt),
        )

    @property
    def consumer_identity(self) -> str:
        return f"{self.repository.value}@{self.commit.value}"

    @property
    def producing_identity(self) -> str:
        return f"{self.central_module.value}:{self.workflow.run_id}"

    @property
    def artifact_suffix(self) -> str:
        return f"{self.commit.value}-{self.workflow.run_id}-{self.workflow.attempt}"
