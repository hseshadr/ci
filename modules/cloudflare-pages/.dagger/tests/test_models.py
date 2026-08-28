"""Behavioral contracts for immutable Pages deployment identities."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cloudflare_pages.models import (
    AttemptIdentity,
    GitHubEvidence,
    PagesProject,
    PagesTarget,
    RepositoryIdentity,
)


def test_should_bind_repository_project_branch_and_domain() -> None:
    # Given / When
    target = PagesTarget(
        repository="hseshadr/edge-reco",
        project="edge-reco",
        branch="main",
        live_domain="edge-reco.com",
    )

    # Then
    assert target.repository.name == target.project


def test_should_reject_mixed_foreign_target() -> None:
    # Given / When / Then
    with pytest.raises(ValueError, match="target binding"):
        PagesTarget(
            repository="hseshadr/edge-reco",
            project="almamesh",
            branch="main",
            live_domain="edge-reco.com",
        )


def _github_evidence() -> dict[str, object]:
    return {
        "app_id": 15368,
        "branch": "main",
        "check_completed_at": "2026-08-27T20:00:01Z",
        "check_name": "Dagger",
        "check_run_id": "41",
        "check_started_at": "2026-08-27T20:00:00Z",
        "check_suite_id": "42",
        "commit_sha": "a" * 40,
        "repository": "hseshadr/edge-reco",
        "run_attempt": 2,
        "workflow_created_at": "2026-08-27T19:59:58Z",
        "workflow_job_id": "43",
        "workflow_name": "Dagger",
        "workflow_path": ".github/workflows/dagger.yml",
        "workflow_run_id": "44",
        "workflow_started_at": "2026-08-27T19:59:59Z",
        "workflow_updated_at": "2026-08-27T20:00:02Z",
    }


def test_should_require_full_source_and_attempt_identity() -> None:
    # Given / When
    evidence = GitHubEvidence.model_validate(_github_evidence())

    # Then
    assert evidence.commit_sha == "a" * 40
    assert evidence.attempt_identity == ("44", 2)


def test_should_reject_abbreviated_source_identity() -> None:
    # Given
    payload = _github_evidence() | {"commit_sha": "a" * 7}

    # When / Then
    with pytest.raises(ValidationError):
        GitHubEvidence.model_validate(payload)


def test_should_reject_unknown_external_project_field() -> None:
    # Given
    payload = {
        "id": "7b162ea7-7367-4d67-bcde-1160995d5",
        "name": "edge-reco",
        "production_branch": "main",
        "domains": ["edge-reco.pages.dev", "edge-reco.com"],
        "source": None,
        "foreign": "unexpected",
    }

    # When / Then
    with pytest.raises(ValidationError):
        PagesProject.model_validate(payload)


def test_should_keep_target_immutable() -> None:
    # Given
    target = PagesTarget("hseshadr/edge-reco", "edge-reco", "main", "edge-reco.com")

    # When / Then
    with pytest.raises(AttributeError):
        target.project = "foreign"  # type: ignore[misc]


@pytest.mark.parametrize("repository", ("missing-owner", "owner/repo/extra"))
def test_should_reject_noncanonical_repository_text(repository: str) -> None:
    # Given / When / Then
    with pytest.raises(ValueError, match="repository"):
        PagesTarget(repository, "edge-reco", "main", "edge-reco.com")


def test_should_reject_git_suffix_repository_component() -> None:
    # Given / When / Then
    with pytest.raises(ValueError, match="canonical GitHub"):
        RepositoryIdentity("hseshadr", "edge-reco.git")


@pytest.mark.parametrize(
    ("branch", "domain", "domains"),
    (
        ("bad branch", "edge-reco.com", ()),
        ("main", "https://edge-reco.com", ()),
        ("main", "edge-reco.com", ("edge-reco.com",)),
    ),
)
def test_should_reject_noncanonical_target_parts(
    branch: str, domain: str, domains: tuple[str, ...]
) -> None:
    # Given / When / Then
    with pytest.raises(ValueError):
        PagesTarget("hseshadr/edge-reco", "edge-reco", branch, domain, domains)


@pytest.mark.parametrize(("run_id", "attempt"), (("0", 1), ("44", 0)))
def test_should_reject_nonpositive_attempt_identity(run_id: str, attempt: int) -> None:
    # Given / When / Then
    with pytest.raises(ValueError, match="attempt identity"):
        AttemptIdentity(run_id, attempt)
