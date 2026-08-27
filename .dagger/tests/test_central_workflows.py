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
    names = ("dagger.yml", "consumer-drift.yml", "security-audit.yml")

    # When every authored job is evaluated by the fleet policy itself
    findings = tuple(item for name in names for item in validate_workflow(_source(name)))

    # Then GitHub contains transport only; Dagger owns execution
    assert findings == ()


def test_should_bind_hosted_tokens_and_exact_commit_to_typed_dagger_calls() -> None:
    # Given the protected, fleet, and scheduled-security entry points
    dagger = _source("dagger.yml").text
    fleet = _source("consumer-drift.yml").text
    security = _source("security-audit.yml").text

    # When their Dagger arguments and event boundaries are inspected
    exact_commit = "--commit-sha=${{ github.sha }}"

    # Then CI/security scan exact source and fleet secrets never cross a PR event
    assert all("--github-token=env:GITHUB_TOKEN" in text for text in (dagger, fleet, security))
    assert exact_commit in dagger and exact_commit in security
    assert "pull_request:" in dagger
    assert "pull_request:" not in fleet
    assert "secrets.CONSUMER_DRIFT_TOKEN" in fleet
    assert "--include-central" in fleet
