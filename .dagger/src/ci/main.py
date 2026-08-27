"""Central CI quality, security, and fleet-policy Dagger graph."""

from __future__ import annotations

from typing import Final, Self

import dagger
from dagger import check, dag, field, function, object_type

PYTHON_IMAGE: Final = (
    "python:3.13.14-slim@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6"
)
UV_IMAGE: Final = (
    "ghcr.io/astral-sh/uv:0.11.32@sha256:"
    "df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c"
)
ACTIONLINT_IMAGE: Final = (
    "rhysd/actionlint:1.7.10@sha256:"
    "ef8299f97635c4c30e2298f48f30763ab782a4ad2c95b744649439a039421e36"
)
GITLEAKS_IMAGE: Final = (
    "ghcr.io/gitleaks/gitleaks:v8.29.1@sha256:"
    "aa036a2f4bdfe3cc3c55fa4326308efabb4a6be498c883c864fd1d0d5585438a"
)
REPOSITORY_URL: Final = "https://github.com/hseshadr/ci.git"
SHA_LENGTH: Final = 40
SOURCE_EXCLUDES: Final = [
    ".git",
    ".env",
    "**/.env",
    "**/*.key",
    "**/*.pem",
    ".dagger/.venv",
    ".dagger/.coverage",
    ".dagger/.mypy_cache",
    ".dagger/.pytest_cache",
    ".dagger/.ruff_cache",
    ".dagger/sdk",
    "**/__pycache__",
]
GITLEAKS_SNAPSHOT: Final = [
    "gitleaks",
    "detect",
    "--source",
    "/snapshot",
    "--no-git",
    "--redact",
    "--no-banner",
]
GITLEAKS_HISTORY: Final = [
    "gitleaks",
    "detect",
    "--source",
    "/repo",
    "--log-opts=--all",
    "--redact",
    "--no-banner",
]


@object_type
class Ci:
    """Run central policy from one explicit typed workspace snapshot."""

    source: dagger.Directory = field()

    @classmethod
    def create(cls, workspace: dagger.Workspace) -> Self:
        """Construct the graph from caller-selected workspace bytes."""
        instance = cls.__new__(cls)
        instance.source = workspace.directory("/", exclude=SOURCE_EXCLUDES)
        return instance

    @function
    @check
    async def ci(self, github_token: dagger.Secret, commit_sha: str = "") -> str:
        """Run canonical quality, dependency, workflow, and secret gates."""
        await self._quality().sync()
        await self._security(commit_sha, github_token)
        return "central Dagger gate passed"

    @function
    async def security(self, github_token: dagger.Secret, commit_sha: str = "") -> str:
        """Run the complete scheduled security graph."""
        await self._security(commit_sha, github_token)
        return "central Dagger security gate passed"

    @function
    async def fleet(self, github_token: dagger.Secret, include_central: bool = False) -> str:
        """Fail closed on authoritative exact-main fleet evidence."""
        command = [
            "uv",
            "run",
            "--directory",
            ".dagger",
            "python",
            "-m",
            "ci.fleet_cli",
        ]
        if include_central:
            command.append("--include-central")
        scan = self._repository().with_secret_variable("GITHUB_TOKEN", github_token)
        await scan.with_exec(command).sync()
        return "authoritative Dagger fleet policy passed"

    async def _security(self, commit_sha: str, github_token: dagger.Secret) -> None:
        await self._dependency_audit().sync()
        await self._workflow_security().sync()
        await self._zizmor(github_token).sync()
        await self._secret_scan(commit_sha).sync()

    def _quality(self) -> dagger.Container:
        command = [
            "uv",
            "run",
            "--directory",
            ".dagger",
            "poe",
            "gate",
        ]
        return self._repository().with_exec(command)

    def _dependency_audit(self) -> dagger.Container:
        command = ["uv", "run", "--directory", ".dagger", "poe", "audit"]
        return self._repository().with_exec(command)

    def _workflow_security(self) -> dagger.Container:
        workflows = self.source.directory(".github/workflows")
        command = "find . -type f \\( -name '*.yml' -o -name '*.yaml' \\) -exec actionlint {} +"
        base = self._actionlint().with_directory("/repo", workflows)
        return base.with_exec(["sh", "-ceu", command])

    def _zizmor(self, github_token: dagger.Secret) -> dagger.Container:
        command = [
            "uv",
            "run",
            "--directory",
            ".dagger",
            "zizmor",
            "--pedantic",
            "--min-severity",
            "medium",
            "../.github/workflows",
            "../.github/dependabot.yml",
        ]
        base = self._repository().with_secret_variable("GH_TOKEN", github_token)
        return base.with_exec(command)

    def _secret_scan(self, commit_sha: str) -> dagger.Container:
        history = self._history(commit_sha)
        scan = self._gitleaks().with_directory("/snapshot", self.source)
        scan = scan.with_exec(["sh", "-ceu", 'test -n "$(find /snapshot -type f -print -quit)"'])
        scan = scan.with_exec(GITLEAKS_SNAPSHOT).with_directory("/repo", history)
        return scan.with_exec(GITLEAKS_HISTORY)

    def _repository(self) -> dagger.Container:
        uv = dag.container().from_(UV_IMAGE).file("/uv")
        sdk = dag.current_module().source().directory("sdk")
        base = dag.container(platform=dagger.Platform("linux/amd64")).from_(PYTHON_IMAGE)
        base = base.with_file("/usr/local/bin/uv", uv).with_directory("/src", self.source)
        base = base.with_directory("/src/.dagger/sdk", sdk)
        base = base.with_workdir("/src")
        base = base.with_mounted_cache("/root/.cache/uv", dag.cache_volume("ci-uv"))
        return base.with_exec(["uv", "sync", "--directory", ".dagger", "--frozen", "--all-groups"])

    @staticmethod
    def _history(commit_sha: str) -> dagger.Directory:
        if commit_sha:
            Ci._require_sha(commit_sha)
            return dag.git(REPOSITORY_URL).commit(commit_sha).tree(depth=0, include_tags=True)
        return dag.git(REPOSITORY_URL).branch("main").tree(depth=0, include_tags=True)

    @staticmethod
    def _actionlint() -> dagger.Container:
        return dag.container().from_(ACTIONLINT_IMAGE).with_entrypoint([]).with_workdir("/repo")

    @staticmethod
    def _gitleaks() -> dagger.Container:
        return dag.container().from_(GITLEAKS_IMAGE).with_entrypoint([])

    @staticmethod
    def _require_sha(commit_sha: str) -> None:
        valid = len(commit_sha) == SHA_LENGTH
        valid = valid and all(character in "0123456789abcdef" for character in commit_sha)
        if not valid:
            raise ValueError("commit_sha must be a lowercase 40-character Git SHA")
