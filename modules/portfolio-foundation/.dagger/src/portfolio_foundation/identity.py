"""Canonical immutable identities for repository-bound inputs."""

from __future__ import annotations

import re
from dataclasses import dataclass

SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
OWNER_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


@dataclass(frozen=True)
class FullSha:
    """A canonical Git SHA-1 object identifier."""

    value: str

    def __post_init__(self) -> None:
        """Reject abbreviated, uppercase, and non-hexadecimal identifiers."""
        if SHA_PATTERN.fullmatch(self.value) is None:
            raise ValueError("SHA must be a lowercase 40-character hexadecimal value")


@dataclass(frozen=True)
class RepositoryRef:
    """A GitHub repository owner and name pair."""

    owner: str
    name: str

    def __post_init__(self) -> None:
        if OWNER_PATTERN.fullmatch(self.owner) is None or not _is_repository_name(self.name):
            raise ValueError("repository must use canonical GitHub owner and repository components")

    @classmethod
    def parse(cls, value: str) -> RepositoryRef:
        """Build a repository reference from canonical owner/repository text."""
        if value.count("/") != 1:
            raise ValueError("repository must be owner/repository")
        owner, name = value.split("/", maxsplit=1)
        return cls(owner, name)

    @property
    def github_url(self) -> str:
        """Return the only public Git URL this foundation supports."""
        return f"https://github.com/{self.owner}/{self.name}.git"


def _is_repository_name(value: str) -> bool:
    return NAME_PATTERN.fullmatch(value) is not None and not value.endswith(".git")


@dataclass(frozen=True)
class CommitIdentity:
    """An exact immutable commit in an approved public repository."""

    repository: RepositoryRef
    commit: FullSha
