"""Public Dagger API for reusable portfolio foundations."""

from __future__ import annotations

import dagger
from dagger import dag, function, object_type

from .guard import build_guard
from .identity import CommitIdentity, FullSha, RepositoryRef
from .source import SourceBinding, bind_dagger_source


@object_type
class PortfolioFoundation:
    """Expose typed foundations for unprivileged portfolio operations."""

    @function
    async def source(
        self, source: dagger.Directory, repository: str, commit_sha: str
    ) -> dagger.Directory:
        """Bind a supplied workspace to a repository identity."""
        return (await _source_binding(source, repository, commit_sha)).source

    @function
    async def guard(
        self, source: dagger.Directory, repository: str, commit_sha: str
    ) -> dagger.Container:
        """Apply repository security checks to a bound source."""
        binding = await _source_binding(source, repository, commit_sha)
        return build_guard(binding)

    @function
    def envelope(
        self, artifact: dagger.Directory, repository: str, commit_sha: str
    ) -> dagger.Directory:
        """Wrap a typed artifact with deterministic evidence."""
        raise NotImplementedError

    @function
    def green_main(self, github_token: dagger.Secret, repository: str) -> str:
        """Resolve exact-green main evidence using a typed secret."""
        raise NotImplementedError


async def _source_binding(
    source: dagger.Directory, repository: str, commit_sha: str
) -> SourceBinding[dagger.Directory, dagger.Directory]:
    identity = CommitIdentity(RepositoryRef.parse(repository), FullSha(commit_sha))
    history = _history(identity)
    return await bind_dagger_source(source, history, identity)


def _history(identity: CommitIdentity) -> dagger.Directory:
    return (
        dag.git(identity.repository.github_url)
        .commit(identity.commit.value)
        .tree(depth=0, include_tags=True)
    )
