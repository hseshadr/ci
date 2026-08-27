"""Public Dagger API for reusable portfolio foundations."""

from __future__ import annotations

import dagger
from dagger import dag, function, object_type

from .artifact import envelope_directory, parse_consumer_identity, parse_producing_identity
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
    async def envelope(
        self,
        artifact: dagger.Directory,
        consumer_identity: str,
        producing_identity: str,
        allowed_roots: list[str],
    ) -> dagger.Directory:
        """Wrap a typed artifact with deterministic evidence."""
        identity = parse_consumer_identity(consumer_identity)
        module_sha, run_id = parse_producing_identity(producing_identity)
        return await envelope_directory(
            artifact, identity, module_sha, tuple(allowed_roots), run_id
        )

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
