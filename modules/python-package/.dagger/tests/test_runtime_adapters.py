from __future__ import annotations

import asyncio
import json
from typing import cast

import dagger
import pytest

from python_package import runtime
from python_package.identity import CandidateIdentity, PackageIdentity


class FakeContainer:
    def __init__(self, events: list[tuple[object, ...]], output: str = "") -> None:
        self.events = events
        self.output = output

    def from_(self, image: str) -> FakeContainer:
        self.events.append(("from", image))
        return self

    def file(self, path: str) -> dagger.File:
        self.events.append(("file", path))
        return cast(dagger.File, object())

    def directory(self, path: str) -> dagger.Directory:
        self.events.append(("result-directory", path))
        return cast(dagger.Directory, object())

    def with_file(self, path: str, source: dagger.File) -> FakeContainer:
        self.events.append(("with-file", path, source))
        return self

    def with_directory(self, path: str, source: dagger.Directory) -> FakeContainer:
        self.events.append(("with-directory", path, source))
        return self

    def with_workdir(self, path: str) -> FakeContainer:
        self.events.append(("workdir", path))
        return self

    def with_exec(self, command: list[str]) -> FakeContainer:
        self.events.append(("exec", tuple(command)))
        return self

    def with_user(self, user: str) -> FakeContainer:
        self.events.append(("user", user))
        return self

    def with_env_variable(self, name: str, value: str) -> FakeContainer:
        self.events.append(("env", name, value))
        return self

    async def stdout(self) -> str:
        return self.output

    async def sync(self) -> None:
        self.events.append(("sync",))


class FakeModuleSource:
    def directory(self, path: str) -> dagger.Directory:
        assert path == "src"
        return cast(dagger.Directory, object())


class FakeCurrentModule:
    def source(self) -> FakeModuleSource:
        return FakeModuleSource()


class FakeGreen:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    async def serialization(self) -> str:
        return self.payload


class FakeFoundation:
    def __init__(self, events: list[tuple[object, ...]], payload: str) -> None:
        self.events = events
        self.payload = payload

    def green_main(self, *, github_token: dagger.Secret, repository: str) -> FakeGreen:
        self.events.append(("green", github_token, repository))
        return FakeGreen(self.payload)

    def source(
        self, *, source: dagger.Directory, repository: str, commit_sha: str
    ) -> dagger.Directory:
        self.events.append(("source", source, repository, commit_sha))
        return source

    def guard(self, *, source: dagger.Directory, repository: str, commit_sha: str) -> FakeContainer:
        self.events.append(("guard", source, repository, commit_sha))
        return FakeContainer(self.events)


class FakeGitRef:
    def __init__(self, commit: str) -> None:
        self.value = commit

    async def commit(self) -> str:
        return self.value


class FakeGit:
    def __init__(self, events: list[tuple[object, ...]], commit: str) -> None:
        self.events = events
        self.commit_sha = commit

    def tag(self, value: str) -> FakeGitRef:
        self.events.append(("tag", value))
        return FakeGitRef(self.commit_sha)


class FakeDag:
    def __init__(self, payload: str = "", output: str = "", commit: str = "a" * 40) -> None:
        self.events: list[tuple[object, ...]] = []
        self.payload = payload
        self.output = output
        self.commit_sha = commit

    def container(self, platform: dagger.Platform | None = None) -> FakeContainer:
        self.events.append(("container", platform))
        return FakeContainer(self.events, self.output)

    def current_module(self) -> FakeCurrentModule:
        return FakeCurrentModule()

    def foundation(self) -> FakeFoundation:
        return FakeFoundation(self.events, self.payload)

    def git(self, url: str) -> FakeGit:
        self.events.append(("git", url))
        return FakeGit(self.events, self.commit_sha)


def test_should_construct_fixed_nonroot_audit_and_build_graphs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a graph recorder and one already-guarded source
    fake = FakeDag()
    monkeypatch.setattr(runtime, "dag", cast(dagger.Client, fake))
    source = cast(dagger.Directory, object())

    # When audit and build adapters construct their command graphs
    runtime.dependency_audit_container(source)
    runtime.build_container(source)
    commands = _commands(fake.events)

    # Then only fixed frozen commands run, and the project build switches to non-root
    assert any(command[:3] == ("uv", "export", "--frozen") for command in commands)
    assert any(command[:3] == ("uv", "tool", "run") for command in commands)
    assert any("pip-audit==2.10.0" in command for command in commands)
    sync = next(command for command in commands if command[:3] == ("uv", "sync", "--frozen"))
    assert "--no-install-project" in sync
    build = next(command for command in commands if command[:2] == ("uv", "build"))
    assert "--no-sources" in build
    assert "--no-build-isolation" in build
    assert ("user", runtime.NONROOT_USER) in fake.events
    assert ("env", "HOME", "/work") in fake.events
    assert ("env", "UV_TOOL_DIR", "/work/tools") in fake.events
    assert ("env", "VIRTUAL_ENV", "/work/venv") in fake.events
    assert any(
        item[:2] == ("env", "PATH") and "/work/venv/bin" in cast(str, item[2])
        for item in fake.events
    )
    assert all(item[0] != "secret" for item in fake.events)


def test_should_compose_foundation_green_guard_and_exact_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given exact Foundation evidence and a matching metadata-derived tag
    identity = _identity()
    fake = FakeDag(_green_payload(), commit=identity.commit.value)
    monkeypatch.setattr(runtime, "dag", cast(dagger.Client, fake))
    source = cast(dagger.Directory, object())

    # When the three live identity adapters run
    evidence = asyncio.run(runtime.green_evidence(cast(dagger.Secret, object()), identity))
    bound = asyncio.run(runtime.guarded_source(source, identity))
    asyncio.run(runtime.require_tag(identity, "v0.4.2"))

    # Then all checks bind the same repository and commit
    assert evidence.commit_sha == identity.commit.value
    assert bound is source
    assert ("tag", "v0.4.2") in fake.events
    assert ("sync",) in fake.events


def test_should_probe_and_twine_check_observed_products(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a closed probe response and graph recorder
    output = json.dumps([_observation("wheel"), _observation("sdist")])
    fake = FakeDag(output=output)
    monkeypatch.setattr(runtime, "dag", cast(dagger.Client, fake))
    directory = cast(dagger.Directory, object())

    # When the pinned probe and Twine adapters evaluate products
    observed = asyncio.run(runtime.inspect_products(directory))
    asyncio.run(runtime.check_distributions(directory, observed))

    # Then the probe is typed and Twine receives only observed filenames
    assert tuple(item.kind for item in observed) == ("wheel", "sdist")
    commands = _commands(fake.events)
    assert any(command[:3] == ("uv", "tool", "run") for command in commands)
    assert any("twine==7.0.0" in command for command in commands)
    assert ("sync",) in fake.events


def test_should_reject_metadata_tag_resolving_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a tag whose Git object differs from the candidate commit
    fake = FakeDag(commit="c" * 40)
    monkeypatch.setattr(runtime, "dag", cast(dagger.Client, fake))

    # When / Then the release boundary fails before envelope construction
    with pytest.raises(ValueError, match="tag does not bind"):
        asyncio.run(runtime.require_tag(_identity(), "v0.4.2"))


def test_should_reject_foundation_green_schema_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given Foundation returns a payload outside its closed generated schema
    fake = FakeDag("{}")
    monkeypatch.setattr(runtime, "dag", cast(dagger.Client, fake))

    # When / Then live evidence cannot be partially interpreted
    with pytest.raises(ValueError, match="schema differs"):
        asyncio.run(runtime.green_evidence(cast(dagger.Secret, object()), _identity()))


@pytest.mark.parametrize(("run_id", "attempt"), (("6101", 2), ("6100", 3)))
def test_should_reject_foundation_green_attempt_drift(
    monkeypatch: pytest.MonkeyPatch, run_id: str, attempt: int
) -> None:
    # Given green evidence from a different workflow run or retry
    fake = FakeDag(_green_payload(run_id, attempt))
    monkeypatch.setattr(runtime, "dag", cast(dagger.Client, fake))

    # When / Then the claimed candidate operation cannot reuse that evidence
    with pytest.raises(ValueError, match="attempt identity"):
        asyncio.run(runtime.green_evidence(cast(dagger.Secret, object()), _identity()))


def _identity() -> CandidateIdentity:
    package = PackageIdentity.parse("hseshadr/edgeproc-core", "a" * 40, "edgeproc-core")
    return CandidateIdentity.from_package(package, "b" * 40, "6100", 2)


def _green_payload(run_id: str = "6100", attempt: int = 2) -> str:
    payload: dict[str, object] = {
        "app_id": 15368,
        "branch": "main",
        "check_completed_at": "2026-08-29T00:00:01Z",
        "check_name": "Dagger",
        "check_run_id": "1",
        "check_started_at": "2026-08-29T00:00:00Z",
        "check_suite_id": "2",
        "commit_sha": "a" * 40,
        "repository": "hseshadr/edgeproc-core",
        "run_attempt": attempt,
        "workflow_created_at": "2026-08-29T00:00:00Z",
        "workflow_job_id": "3",
        "workflow_name": "Dagger",
        "workflow_path": ".github/workflows/dagger.yml",
        "workflow_run_id": run_id,
        "workflow_started_at": "2026-08-29T00:00:00Z",
        "workflow_updated_at": "2026-08-29T00:00:01Z",
    }
    return json.dumps(payload)


def _observation(kind: str) -> dict[str, object]:
    filename = "edgeproc_core-0.4.2-py3-none-any.whl"
    if kind == "sdist":
        filename = "edgeproc_core-0.4.2.tar.gz"
    return {
        "filename": filename,
        "kind": kind,
        "member_count": 20,
        "project": "edgeproc-core",
        "sha256": "d" * 64,
        "size": 40_000,
        "version": "0.4.2",
    }


def _commands(events: list[tuple[object, ...]]) -> tuple[tuple[str, ...], ...]:
    values: list[tuple[str, ...]] = []
    for event in events:
        if event[0] == "exec":
            command = event[1]
            assert isinstance(command, tuple)
            values.append(cast(tuple[str, ...], command))
    return tuple(values)
