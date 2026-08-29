from __future__ import annotations

import ast
import os
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict

MODULE = Path(__file__).parents[2]
PROBE = Path(__file__).parent / "fixtures" / "artifact_scale_probe.py.txt"
TEST_FILE = Path(__file__)
SOURCE_ROOT = MODULE / ".dagger" / "src"
SCALE_FILES = 822
SCALE_DIRECTORIES = 11
SCALE_NODES = SCALE_FILES + SCALE_DIRECTORIES
OLD_LIST_TERMINALS = SCALE_DIRECTORIES + 1
OLD_INVENTORY_TERMINALS = OLD_LIST_TERMINALS + (3 * SCALE_NODES) + SCALE_FILES
OLD_VERIFY_POLICY_TERMINALS = 4
OLD_ENVELOPE_VERIFY_TERMINALS = (2 * OLD_INVENTORY_TERMINALS) + OLD_VERIFY_POLICY_TERMINALS


class ArtifactScaleResult(BaseModel):  # type: ignore[explicit-any]  # Pydantic v2 base stub
    model_config = ConfigDict(extra="forbid", frozen=True)

    file_count: int
    directory_count: int
    depth: int
    newline_seen: bool
    backslash_seen: bool
    mode: int
    manifest_bytes: int
    sums_bytes: int
    maximum_selection_string_bytes: int
    manifest_scalar_ingress: bool
    sums_scalar_ingress: bool
    evidence_exact: bool
    same_input_deterministic: bool
    changed_byte_diverges: bool
    changed_context_diverges: bool
    oversized_context_rejected: bool
    oversized_context_execs: int
    create_execs: int
    verify_execs: int
    error: str


def _uvx() -> str:
    executable = shutil.which("uvx")
    assert executable is not None, "uvx is required for the real Dagger integration"
    return executable


def _probe_command() -> tuple[str, ...]:
    tools = (_uvx(), "--from", "dagger-io==0.21.8", "--with", "pydantic==2.13.4")
    return (*tools, "python", str(PROBE))


def _scale_probe() -> subprocess.CompletedProcess[str]:
    environment = os.environ | {"PYTHONPATH": str(SOURCE_ROOT)}
    return subprocess.run(  # noqa: S603
        _probe_command(), cwd=MODULE, env=environment, capture_output=True, check=False, text=True
    )


def _assert_old_graph_crosses_hosted_boundary() -> None:
    # Per inventory: D listings + 3N stats + F hashes; verify adds four policy reads.
    assert SCALE_NODES == 833
    assert OLD_INVENTORY_TERMINALS == 3_333
    assert OLD_ENVELOPE_VERIFY_TERMINALS == 6_670
    assert OLD_ENVELOPE_VERIFY_TERMINALS > 5_000


def _assert_scale_result(result: ArtifactScaleResult) -> None:
    _assert_old_graph_crosses_hosted_boundary()
    _assert_scale_shape(result)
    _assert_evidence_boundary(result)
    _assert_reproducibility(result)
    _assert_terminal_budget(result)


def _assert_scale_shape(result: ArtifactScaleResult) -> None:
    assert result.error == ""
    assert (result.file_count, result.directory_count) == (SCALE_FILES, SCALE_DIRECTORIES)
    assert result.depth == 11
    assert result.newline_seen and result.backslash_seen
    assert result.mode == 0o755


def _assert_evidence_boundary(result: ArtifactScaleResult) -> None:
    assert result.manifest_bytes > 65_536
    assert result.sums_bytes > 65_536
    assert result.maximum_selection_string_bytes < 65_536
    assert not result.manifest_scalar_ingress
    assert not result.sums_scalar_ingress
    assert result.evidence_exact
    assert result.oversized_context_rejected
    assert result.oversized_context_execs == 0


def _assert_reproducibility(result: ArtifactScaleResult) -> None:
    assert result.same_input_deterministic
    assert result.changed_byte_diverges
    assert result.changed_context_diverges


def _assert_terminal_budget(result: ArtifactScaleResult) -> None:
    # Create: inventory stdout + normalized directory ID + envelope sync.
    assert result.create_execs == 3
    # Verify: two layout reads + manifest + artifact ID + inventory + sums + return sync.
    assert result.verify_execs == 7


def test_should_envelope_and_verify_edge_scale_artifact_with_bounded_graph() -> None:
    # Given / When
    completed = _scale_probe()

    # Then
    assert completed.returncode == 0, completed.stderr
    _assert_scale_result(ArtifactScaleResult.model_validate_json(completed.stdout))


def test_should_keep_artifact_scale_probe_functions_at_most_fifteen_lines() -> None:
    # Given / When
    long_functions = _long_functions(PROBE)

    # Then
    assert long_functions == ()


def _long_functions(path: Path) -> tuple[tuple[str, int], ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = (
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    )
    return tuple(
        (node.name, node.end_lineno - node.lineno + 1)
        for node in functions
        if node.end_lineno is not None and node.end_lineno - node.lineno + 1 > 15
    )


def test_should_keep_artifact_test_functions_at_most_fifteen_lines() -> None:
    # Given / When
    long_functions = _long_functions(TEST_FILE)

    # Then
    assert long_functions == ()
