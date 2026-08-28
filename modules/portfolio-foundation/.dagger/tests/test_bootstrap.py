import shutil
import subprocess
import sys
from pathlib import Path

MODULE = Path(__file__).parents[2]
IGNORED = (
    ".coverage",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "sdk",
)


def _copy_clean_module(tmp_path: Path) -> Path:
    destination = tmp_path / "portfolio-foundation"
    shutil.copytree(MODULE, destination, ignore=shutil.ignore_patterns(*IGNORED))
    return destination


def _run_poe(module: Path, task: str) -> subprocess.CompletedProcess[str]:
    poe = Path(sys.executable).with_name("poe")
    return subprocess.run(
        (str(poe), task), cwd=module / ".dagger", capture_output=True, check=False, text=True
    )


def test_should_bootstrap_clean_module_before_frozen_sync(tmp_path: Path) -> None:
    module = _copy_clean_module(tmp_path)
    result = _run_poe(module, "bootstrap")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (module / ".dagger/sdk").is_dir()


def test_should_fail_lint_without_rewriting_source(tmp_path: Path) -> None:
    module = _copy_clean_module(tmp_path)
    source = module / ".dagger/src/portfolio_foundation/main.py"
    source.write_text(f"{source.read_text()}\nvalue=1\n")
    before = source.read_text()
    result = _run_poe(module, "lint")
    assert result.returncode != 0, result.stdout
    assert source.read_text() == before
