"""Unit behavior for Python Dagger module source-boundary policy."""

from pathlib import Path

import dagger_source_policy
from pytest import CaptureFixture


def write_source(tmp_path: Path, source: str) -> Path:
    """Write one module fixture."""
    path = tmp_path / "main.py"
    path.write_text(source, encoding="utf-8")
    return path


def test_should_accept_explicit_directory_and_secret_when_typed(tmp_path: Path) -> None:
    # Given
    source = (
        "source: Annotated[Directory, DefaultPath('/')]\n"
        "def deploy(api_token: Secret) -> None: ...\n"
    )
    path = write_source(tmp_path, source)
    # When
    findings = dagger_source_policy.inspect_paths([path])
    # Then
    assert findings == []


def test_should_reject_implicit_workspace_and_string_secret(tmp_path: Path) -> None:
    # Given
    source = "def deploy(api_token: str) -> None:\n    dag.current_workspace()\n"
    path = write_source(tmp_path, source)
    # When
    findings = dagger_source_policy.inspect_paths([path])
    # Then
    assert len(findings) == 3


def test_should_reject_async_string_credential(tmp_path: Path) -> None:
    # Given
    source = (
        "source: Annotated[Directory, DefaultPath('/')]\n"
        "async def deploy(private_key: str) -> None: ...\n"
    )
    path = write_source(tmp_path, source)
    # When
    findings = dagger_source_policy.inspect_paths([path])
    # Then
    assert "not typed Secret" in findings[0]


def test_should_emit_findings_and_nonzero_when_main_rejects(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    # Given
    path = write_source(tmp_path, "def check(password: str) -> None: ...\n")
    # When
    result = dagger_source_policy.main([str(path)])
    # Then
    assert result == 1
    assert "password" in capsys.readouterr().out


def test_should_emit_nothing_and_zero_when_main_accepts(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    # Given
    path = write_source(tmp_path, "source: Annotated[Directory, DefaultPath('/')]\n")
    # When
    result = dagger_source_policy.main([str(path)])
    # Then
    assert result == 0
    assert capsys.readouterr().out == ""
