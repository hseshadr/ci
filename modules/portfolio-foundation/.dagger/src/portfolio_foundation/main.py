"""Public Dagger API for reusable portfolio foundations."""

from __future__ import annotations

import dagger
from dagger import dag, function, object_type

from .identity import CommitIdentity, FullSha, RepositoryRef
from .source import bind_dagger_source


@object_type
class PortfolioFoundation:
    """Expose typed placeholders for unprivileged portfolio operations."""

    @function
    async def source(
        self, source: dagger.Directory, repository: str, commit_sha: str
    ) -> dagger.Directory:
        """Bind a supplied workspace to a repository identity."""
        identity = CommitIdentity(RepositoryRef.parse(repository), FullSha(commit_sha))
        history = (
            dag.git(identity.repository.github_url)
            .commit(identity.commit.value)
            .tree(depth=0, include_tags=True)
        )
        return (await bind_dagger_source(source, history, identity)).source

    @function
    def guard(self, source: dagger.Directory, repository: str, commit_sha: str) -> dagger.Container:
        """Apply repository security checks to a bound source."""
        raise NotImplementedError

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
