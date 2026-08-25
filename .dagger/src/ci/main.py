"""Credential-free policy checks owned by the central Dagger module."""

from typing import Annotated, Final

import dagger
from dagger import DefaultPath, Ignore, check, dag, field, function, object_type

RUBY_IMAGE: Final = (
    "ruby:3.4.10-slim@sha256:a7226f12d55f877efed8db1a3f1624cebd04553220478eca0d3e5fed8357efa1"
)
SOURCE_INCLUDE: Final = [
    ".dagger/src/**",
    ".github/**",
    "dagger.json",
    "tests/dagger-control-plane*",
    "tests/lib/dagger*",
    "tests/lib/workflow-run-pin.rb",
]
SOURCE_EXCLUDE: Final = [".git", ".env", "**/.env", "**/*private*.key", "**/*.pem"]
POLICY_COMMAND: Final = [
    "ruby",
    "tests/lib/dagger-control-plane.rb",
    "--repo",
    "ci",
    "--path",
    ".",
    "--allowlist",
    "tests/dagger-control-plane-allowlist.txt",
]


@object_type
class Ci:
    """Run central control-plane policy from one explicit source snapshot."""

    source: Annotated[dagger.Directory, DefaultPath("/"), Ignore(SOURCE_EXCLUDE)] = field()

    @function
    @check
    def policy(self) -> dagger.Container:
        """Prove both polarities of the Dagger-only execution contract."""
        return (
            self._base()
            .with_exec(["bash", "-n", "tests/dagger-control-plane.sh"])
            .with_exec(["python3", "-m", "py_compile", "tests/lib/dagger_source_policy.py"])
            .with_exec(["tests/dagger-control-plane-cases.sh"])
            .with_exec(POLICY_COMMAND)
        )

    def _base(self) -> dagger.Container:
        source = self.source.filter(include=SOURCE_INCLUDE)
        return (
            dag.container()
            .from_(RUBY_IMAGE)
            .with_exec(["apt-get", "update"])
            .with_exec(
                ["apt-get", "install", "-y", "--no-install-recommends", "bash", "git", "python3"]
            )
            .with_directory("/src", source)
            .with_workdir("/src")
        )
