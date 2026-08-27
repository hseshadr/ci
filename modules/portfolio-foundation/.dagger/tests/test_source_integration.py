from __future__ import annotations

import subprocess
from pathlib import Path

REPOSITORY = "octocat/Spoon-Knife"
MODULE = Path(__file__).parents[2]


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, check=False, text=True)


def _clone(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    result = _run(
        "git",
        "clone",
        "--depth",
        "1",
        f"https://github.com/{REPOSITORY}.git",
        str(source),
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    return source


def _sha(source: Path) -> str:
    result = _run("git", "rev-parse", "HEAD", cwd=source)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _source_call(source: Path, sha: str) -> subprocess.CompletedProcess[str]:
    return _run(
        "dagger",
        "-m",
        ".",
        "call",
        "source",
        f"--source={source}",
        f"--repository={REPOSITORY}",
        f"--commit-sha={sha}",
        "entries",
        cwd=MODULE,
    )


def test_should_bind_exact_nested_public_tree_and_reject_tampering(tmp_path: Path) -> None:
    # Given
    source = _clone(tmp_path)
    sha = _sha(source)
    assert any(path.is_file() and path.parent != source for path in source.rglob("*"))

    # When
    exact = _source_call(source, sha)
    (source / "README.md").write_text("tampered\n")
    tampered = _source_call(source, sha)

    # Then
    assert exact.returncode == 0, exact.stderr
    assert "workspace does not match exact commit" in tampered.stderr
