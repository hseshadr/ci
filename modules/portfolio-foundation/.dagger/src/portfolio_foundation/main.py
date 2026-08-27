"""Public Dagger API for reusable portfolio foundations."""

from __future__ import annotations

import dagger
from dagger import function, object_type


@object_type
class PortfolioFoundation:
    """Expose typed placeholders for unprivileged portfolio operations."""

    @function
    def source(
        self, source: dagger.Directory, repository: str, commit_sha: str
    ) -> dagger.Directory:
        """Bind a supplied workspace to a repository identity."""
        raise NotImplementedError

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
