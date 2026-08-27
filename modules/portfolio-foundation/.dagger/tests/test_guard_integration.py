from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GuardFixture:
    repository: str
    commit_sha: str


SUCCESS = GuardFixture("hseshadr/ci", "9a4830a3d8444e49d32e2408ef31ea592f2087b0")
INVALID_WORKFLOW = GuardFixture(
    "toomcruz/cemiterio-santana-5b3-test",
    "5e7a3baf9507a22913bc2fb9048281d8c58d2ce6",
)
HISTORY_SECRET = GuardFixture(
    "kristof-mattei/km-crates-publish-test",
    "711c3b50ce63192b88f22215793ca7f1eeb7b439",
)
MODULE = Path(__file__).parents[2]
DAGGER_GUARD = ("dagger", "--progress=logs", "-m", ".", "call", "guard")


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, text=True
    )


def _require(result: subprocess.CompletedProcess[str], step: str) -> None:
    assert result.returncode == 0, f"fixture {step} failed before guard validation: {result.stdout}"


def _materialize(tmp_path: Path, fixture: GuardFixture) -> Path:
    source = tmp_path / fixture.repository.replace("/", "-")
    source.mkdir()
    _require(_run("git", "init", "-q", cwd=source), "initialization")
    remote = f"https://github.com/{fixture.repository}.git"
    _require(_run("git", "remote", "add", "origin", remote, cwd=source), "remote setup")
    _require(_run("git", "fetch", "--depth=1", "origin", fixture.commit_sha, cwd=source), "fetch")
    _require(_run("git", "checkout", "--detach", fixture.commit_sha, cwd=source), "checkout")
    return source


def _guard(source: Path, fixture: GuardFixture) -> subprocess.CompletedProcess[str]:
    return _run(
        *DAGGER_GUARD,
        f"--source={source}",
        f"--repository={fixture.repository}",
        f"--commit-sha={fixture.commit_sha}",
        "sync",
        cwd=MODULE,
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout


def _assert_success_evidence(result: subprocess.CompletedProcess[str]) -> None:
    output = _output(result)
    assert result.returncode == 0, output
    markers = ("guard-canary-detected", "guard-snapshot-nonempty", "guard-history-verified")
    assert all(output.count(marker) >= 2 for marker in markers)
    positions = tuple(output.rindex(marker) for marker in markers)
    assert positions == tuple(sorted(positions))
    assert "57 commits scanned." in output
    assert output.count("no leaks found") >= 2


def _assert_invalid_workflow(result: subprocess.CompletedProcess[str]) -> None:
    output = _output(result)
    assert result.returncode != 0, "invalid workflow unexpectedly passed"
    assert "could not parse as YAML" in output
    assert "guard-canary-detected" not in output


def _assert_history_secret(result: subprocess.CompletedProcess[str]) -> None:
    output = _output(result)
    assert result.returncode != 0, "retained-history secret unexpectedly passed"
    snapshot = output.rindex("guard-snapshot-nonempty")
    history = output.rindex("guard-history-verified")
    assert output.index("no leaks found", snapshot) < history
    assert output.index("leaks found: 1", history) > history


def test_should_run_canary_snapshot_and_exact_retained_history(tmp_path: Path) -> None:
    # Given
    source = _materialize(tmp_path, SUCCESS)

    # When
    result = _guard(source, SUCCESS)

    # Then
    _assert_success_evidence(result)


def test_should_reject_invalid_workflow_before_secret_scanning(tmp_path: Path) -> None:
    # Given
    source = _materialize(tmp_path, INVALID_WORKFLOW)

    # When
    result = _guard(source, INVALID_WORKFLOW)

    # Then
    _assert_invalid_workflow(result)


def test_should_reject_secret_retained_only_in_history(tmp_path: Path) -> None:
    # Given
    source = _materialize(tmp_path, HISTORY_SECRET)

    # When
    result = _guard(source, HISTORY_SECRET)

    # Then
    _assert_history_secret(result)
