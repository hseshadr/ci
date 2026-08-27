from __future__ import annotations

from pathlib import Path

from ci.fleet_policy import SourceFile, validate_workflow

ROOT = Path(__file__).parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def _source(name: str) -> SourceFile:
    path = WORKFLOWS / name
    return SourceFile(path=str(path.relative_to(ROOT)), text=path.read_text())


def test_should_keep_all_central_execution_behind_thin_dagger_ingress() -> None:
    # Given the three workflows that remain after central cutover
    names = ("dagger.yml", "consumer-drift.yml", "dagger-security.yml")

    # When every authored job is evaluated by the fleet policy itself
    findings = tuple(item for name in names for item in validate_workflow(_source(name)))

    # Then GitHub contains transport only; Dagger owns execution
    assert findings == ()


def test_should_bind_hosted_tokens_and_exact_commit_to_typed_dagger_calls() -> None:
    # Given the protected, fleet, and scheduled-security entry points
    dagger = _source("dagger.yml").text
    fleet = _source("consumer-drift.yml").text
    security = _source("dagger-security.yml").text

    # When their Dagger arguments and event boundaries are inspected
    exact_commit = "--commit-sha=${{ github.sha }}"

    # Then CI/security scan exact source and fleet secrets never cross a PR event
    assert all("--github-token=env:GITHUB_TOKEN" in text for text in (dagger, fleet, security))
    assert exact_commit in dagger and exact_commit in security
    assert "pull_request:" in dagger
    assert "pull_request:" not in fleet
    assert "secrets.CONSUMER_DRIFT_TOKEN" in fleet
    assert "--include-central" in fleet


def test_should_run_main_fleet_only_after_exact_dagger_success() -> None:
    # Given central protection requires the Dagger check for the new main commit
    dagger = _source("dagger.yml").text
    fleet = _source("consumer-drift.yml").text

    # When the fleet proof is triggered after a merge
    required_boundaries = (
        "fleet:",
        "needs: dagger",
        "github.event_name == 'push'",
        "github.ref == 'refs/heads/main'",
        "secrets.CONSUMER_DRIFT_TOKEN",
        "fleet --github-token=env:GITHUB_TOKEN --include-central",
    )

    # Then the scheduled/manual ingress cannot race the exact-main Dagger check
    assert "push:" not in fleet
    assert "workflow_run:" not in fleet
    assert all(boundary in dagger for boundary in required_boundaries)


def test_should_delete_every_retired_central_execution_surface() -> None:
    # Given the fleet no longer executes a central reusable control
    remaining = {path.name for path in WORKFLOWS.glob("*.yml")}

    # When central execution surfaces are inventoried
    expected = {"dagger.yml", "consumer-drift.yml", "dagger-security.yml"}

    # Then only thin Dagger ingress remains; classifiers/templates are gone
    assert remaining == expected
    retired = (ROOT / ".github" / "actions", ROOT / "examples", ROOT / "tests")
    authored = (
        path for root in retired for path in root.rglob("*") if "__pycache__" not in path.parts
    )
    assert not any(path.is_file() for path in authored)
