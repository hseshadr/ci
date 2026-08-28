from __future__ import annotations

import ast
import os
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict

REPOSITORY = "hseshadr/ci"
COMMIT_SHA = "9a4830a3d8444e49d32e2408ef31ea592f2087b0"
NESTED_FILE = Path(".dagger/src/ci/fleet.py")
MODULE = Path(__file__).parents[2]
REMOTE_URL = f"https://github.com/{REPOSITORY}.git"
DAGGER_SOURCE = ("dagger", "-m", ".", "call", "source")
PROBE = Path(__file__).parent / "fixtures" / "source_scale_probe.py.txt"
SOURCE_ROOT = MODULE / ".dagger" / "src"
SCALE_DEPTH = 12
SCALE_WIDTH = 128
SCALE_DIRECTORIES = 781
SCALE_FILES = 1_253
SCALE_NODES = 2_033
OLD_SOURCE_TERMINALS = SCALE_DIRECTORIES + (2 * SCALE_NODES) + SCALE_FILES


class ScaleProbeResult(BaseModel):  # type: ignore[explicit-any]  # Pydantic v2 base stub
    model_config = ConfigDict(extra="forbid", frozen=True)

    names_execs: int
    counter_probe_execs: int
    fixture_setup_execs: int
    scale_execs: int
    scale_count: int
    scale_complete: bool
    scale_depth: int
    scale_newline_seen: bool
    scale_backslash_seen: bool
    empty_execs: int
    empty_count: int
    exact_execs: int
    tamper_execs: int
    mode_execs: int
    symlink_execs: int
    inventory_count: int
    newline_seen: bool
    backslash_seen: bool
    complete_paths: bool
    tamper_rejected: bool
    mode_rejected: bool
    symlink_rejected: bool
    git_present: bool
    source_mode: int
    source_contents: str


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, check=False, text=True)


def _scale_parent(root: Path) -> Path:
    return root.joinpath(*(f"level-{index:02}" for index in range(SCALE_DEPTH)))


def _write_scale_tree(root: Path) -> None:
    parent = _scale_parent(root)
    parent.mkdir(parents=True)
    for index in range(SCALE_WIDTH):
        (parent / f"payload-{index:04}.bin").write_bytes(f"payload-{index}".encode())
    (parent / "line\nbreak.txt").write_text("newline", encoding="utf-8")
    (parent / "slash\\name.txt").write_text("backslash", encoding="utf-8")
    (root / "run.sh").write_text("#!/bin/sh\necho bounded\n", encoding="utf-8")
    (root / "run.sh").chmod(0o755)
    (root / ".git").mkdir()
    (root / ".git" / "marker").write_text("history", encoding="utf-8")


def _scale_fixture(tmp_path: Path) -> tuple[Path, Path]:
    history = tmp_path / "history"
    source = tmp_path / "source"
    _write_scale_tree(history)
    shutil.copytree(history, source, copy_function=shutil.copy2)
    (source / ".git" / "marker").write_text("source", encoding="utf-8")
    return source, history


def _uvx() -> str:
    executable = shutil.which("uvx")
    assert executable is not None, "uvx is required for the real Dagger integration"
    return executable


def _probe_command(source: Path, history: Path) -> tuple[str, ...]:
    tools = (_uvx(), "--from", "dagger-io==0.21.8", "--with", "pydantic==2.13.4")
    sizes = (str(SCALE_DEPTH), str(SCALE_WIDTH))
    return (*tools, "python", str(PROBE), str(source), str(history), *sizes)


def _scale_probe(source: Path, history: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ | {"PYTHONPATH": str(SOURCE_ROOT)}
    return subprocess.run(
        _probe_command(source, history),
        cwd=MODULE,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )


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


def _assert_old_inventory_cost() -> None:
    assert sum(5**level for level in range(5)) == SCALE_DIRECTORIES
    assert SCALE_FILES == (5**4 * 2) + 3
    assert SCALE_NODES == (SCALE_DIRECTORIES - 1) + SCALE_FILES
    assert OLD_SOURCE_TERMINALS == 6_100 and OLD_SOURCE_TERMINALS > 5_000


def _assert_scale_result(result: ScaleProbeResult) -> None:
    # Old path: D directory listings + 2N stats + F hash outputs = 781 + 4066 + 1253.
    _assert_old_inventory_cost()
    assert (result.fixture_setup_execs, result.scale_execs) == (1, 1)
    assert result.scale_count == SCALE_FILES and result.scale_complete
    assert result.scale_depth == 4
    assert result.scale_newline_seen and result.scale_backslash_seen
    assert (result.empty_execs, result.empty_count) == (1, 0)
    assert result.counter_probe_execs == 1
    assert (result.names_execs, result.exact_execs) == (1, 2)
    assert (result.tamper_execs, result.mode_execs, result.symlink_execs) == (2, 2, 1)
    assert result.inventory_count == SCALE_WIDTH + 3 and result.complete_paths
    assert result.tamper_rejected and result.mode_rejected and result.symlink_rejected
    assert result.newline_seen and result.backslash_seen and result.git_present
    assert (result.source_mode, result.source_contents) == (0o755, "#!/bin/sh\necho bounded\n")


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


def test_should_inventory_large_deep_real_directory_with_two_bounded_execs(
    tmp_path: Path,
) -> None:
    # Given
    source, history = _scale_fixture(tmp_path)

    # When
    completed = _scale_probe(source, history)

    # Then
    assert completed.returncode == 0, completed.stderr
    _assert_scale_result(ScaleProbeResult.model_validate_json(completed.stdout))


def test_should_keep_scale_probe_functions_at_most_fifteen_lines() -> None:
    tree = ast.parse(PROBE.read_text(encoding="utf-8"))
    functions = (
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    )
    long_functions = tuple(
        (node.name, node.end_lineno - node.lineno + 1)
        for node in functions
        if node.end_lineno is not None and node.end_lineno - node.lineno + 1 > 15
    )
    assert long_functions == ()
