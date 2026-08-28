from __future__ import annotations

import subprocess
from pathlib import Path

REPOSITORY = "hseshadr/ci"
COMMIT_SHA = "9a4830a3d8444e49d32e2408ef31ea592f2087b0"
NESTED_FILE = Path(".dagger/src/ci/fleet.py")
MODULE = Path(__file__).parents[2]
REMOTE_URL = f"https://github.com/{REPOSITORY}.git"
DAGGER_SOURCE = ("dagger", "-m", ".", "call", "source")


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, check=False, text=True)


def _require_fixture_step(result: subprocess.CompletedProcess[str], step: str) -> None:
    assert result.returncode == 0, (
        f"fixture {step} failed before product validation: {result.stderr}"
    )


def _materialize(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    _require_fixture_step(_run("git", "init", "-q", cwd=source), "initialization")
    remote = _run("git", "remote", "add", "origin", REMOTE_URL, cwd=source)
    _require_fixture_step(remote, "remote setup")
    fetch = _run("git", "fetch", "--depth=1", "origin", COMMIT_SHA, cwd=source)
    _require_fixture_step(fetch, "network/fetch")
    checkout = _run("git", "checkout", "--detach", COMMIT_SHA, cwd=source)
    _require_fixture_step(checkout, "exact checkout")
    return source


def _sha(source: Path) -> str:
    result = _run("git", "rev-parse", "HEAD", cwd=source)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _source_call(source: Path, sha: str) -> subprocess.CompletedProcess[str]:
    return _run(
        *DAGGER_SOURCE,
        f"--source={source}",
        f"--repository={REPOSITORY}",
        f"--commit-sha={sha}",
        "entries",
        cwd=MODULE,
    )


def _tamper_one_byte(path: Path) -> None:
    original = path.read_bytes()
    assert original, f"pinned nested fixture is unexpectedly empty: {path}"
    tampered = bytes((original[0] ^ 1,)) + original[1:]
    path.write_bytes(tampered)
    assert len(tampered) == len(original)
    assert tampered[0] != original[0]
    assert tampered[1:] == original[1:]


def _assert_mismatch(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode != 0, "one-byte nested tamper was accepted"
    assert "workspace does not match exact commit" in result.stderr, (
        f"Dagger failed without fail-closed mismatch evidence: {result.stderr}"
    )


def _assert_exact(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, (
        f"exact-tree Dagger call failed after fixture fetch: {result.stderr}"
    )


def test_should_bind_exact_nested_public_tree_and_reject_tampering(tmp_path: Path) -> None:
    # Given
    source = _materialize(tmp_path)
    assert _sha(source) == COMMIT_SHA
    nested_file = source / NESTED_FILE
    assert nested_file.is_file()

    # When
    exact = _source_call(source, COMMIT_SHA)
    _tamper_one_byte(nested_file)
    tampered = _source_call(source, COMMIT_SHA)

    # Then
    _assert_exact(exact)
    _assert_mismatch(tampered)
