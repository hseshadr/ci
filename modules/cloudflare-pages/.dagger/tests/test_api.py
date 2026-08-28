"""Cloudflare Pages raw API parsing and convergence contracts."""

from __future__ import annotations

import json

import pytest

from cloudflare_pages.api import (
    CloudflareApiError,
    CloudflarePolicyError,
    deployment_path,
    disable_git_payload,
    parse_deployments_response,
    parse_project_response,
    project_path,
    provider_error_message,
    require_project_binding,
    sanitize_error,
    select_deployment,
)
from cloudflare_pages.models import ApiProblem, PagesTarget

FULL_SHA = "a" * 40
PROJECT_ID = "7b162ea7-7367-4d4a-a28a-cb84f88f6"


def _target() -> PagesTarget:
    return PagesTarget("hseshadr/edge-reco", "edge-reco", "main", "edge-reco.com", "dist")


def _problem() -> dict[str, object]:
    return {
        "code": 1000,
        "message": "message",
        "documentation_url": "https://developers.cloudflare.com/",
        "source": {"pointer": "/source"},
    }


def _project_payload() -> str:
    return json.dumps(
        {
            "errors": [],
            "messages": [],
            "result": {
                "id": "7b162ea7-7367-4d4a-a28a-cb84f88f6",
                "name": "edge-reco",
                "production_branch": "main",
                "domains": ["edge-reco.pages.dev", "edge-reco.com"],
                "source": {
                    "type": "github",
                    "config": {
                        "owner": "hseshadr",
                        "repo_name": "edge-reco",
                        "production_branch": "main",
                        "production_deployments_enabled": True,
                        "preview_deployment_setting": "all",
                        "ignored_documented_field": True,
                    },
                },
                "created_on": "2026-08-27T20:00:00Z",
            },
            "success": True,
        }
    )


def _deployment(commit_sha: str = FULL_SHA) -> dict[str, object]:
    return {
        "id": "f64788e9-fccd-4d4a-a28a-cb84f88f6",
        "short_id": "f64788e9",
        "url": "https://f64788e9.edge-reco.pages.dev",
        "project_id": "7b162ea7-7367-4d4a-a28a-cb84f88f6",
        "project_name": "edge-reco",
        "environment": "production",
        "latest_stage": {"name": "deploy", "status": "success"},
        "deployment_trigger": {
            "type": "ad_hoc",
            "metadata": {
                "branch": "main",
                "commit_hash": commit_sha,
                "commit_dirty": False,
            },
        },
    }


def _deployments_payload(*deployments: dict[str, object]) -> str:
    return json.dumps(
        {
            "errors": [],
            "messages": [],
            "result": list(deployments),
            "success": True,
            "result_info": {
                "count": len(deployments),
                "page": 1,
                "per_page": 10,
                "total_count": len(deployments),
                "total_pages": 1,
            },
        }
    )


def test_should_query_documented_deployments_endpoint() -> None:
    # Given / When
    path = deployment_path("account", _target())

    # Then
    assert path == (
        "/accounts/account/pages/projects/edge-reco/deployments?env=production&per_page=10"
    )


def test_should_disable_both_git_deployment_modes_in_one_patch() -> None:
    # Given / When
    payload = disable_git_payload(_target())

    # Then
    assert payload == {
        "production_branch": "main",
        "source": {
            "type": "github",
            "config": {
                "production_deployments_enabled": False,
                "preview_deployment_setting": "none",
            },
        },
    }


def test_should_bind_read_only_project_preflight() -> None:
    # Given / When
    project = parse_project_response(_project_payload())

    # Then
    require_project_binding(project, _target())


def test_should_reject_malformed_project_schema() -> None:
    # Given
    payload = json.loads(_project_payload())
    payload["result"]["domains"] = "edge-reco.com"

    # When / Then
    with pytest.raises(CloudflarePolicyError, match="schema"):
        parse_project_response(json.dumps(payload))


def test_should_wait_for_exact_full_sha_and_successful_deploy_stage() -> None:
    # Given
    response = parse_deployments_response(_deployments_payload(_deployment()))

    # When
    deployment = select_deployment(response, _target(), FULL_SHA, PROJECT_ID)

    # Then
    assert deployment is not None
    assert deployment.deployment_trigger.metadata.commit_hash == FULL_SHA


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("environment", "preview"),
        ("project_name", "almamesh"),
        ("latest_stage", {"name": "build", "status": "success"}),
        (
            "deployment_trigger",
            {
                "type": "ad_hoc",
                "metadata": {"branch": "foreign", "commit_hash": FULL_SHA, "commit_dirty": False},
            },
        ),
    ),
)
def test_should_reject_foreign_or_incomplete_success(field: str, value: object) -> None:
    # Given
    payload = _deployment()
    payload[field] = value
    response = parse_deployments_response(_deployments_payload(payload))

    # When / Then
    with pytest.raises(CloudflarePolicyError, match="deployment identity"):
        select_deployment(response, _target(), FULL_SHA, PROJECT_ID)


def test_should_ignore_wrong_sha_while_provider_converges() -> None:
    # Given
    response = parse_deployments_response(_deployments_payload(_deployment("b" * 40)))

    # When / Then
    assert select_deployment(response, _target(), FULL_SHA, PROJECT_ID) is None


def test_should_reject_wrong_page_size() -> None:
    # Given
    payload = json.loads(_deployments_payload(_deployment()))
    payload["result_info"]["per_page"] = 20
    response = parse_deployments_response(json.dumps(payload))

    # When / Then
    with pytest.raises(CloudflarePolicyError, match="pagination"):
        select_deployment(response, _target(), FULL_SHA, PROJECT_ID)


def test_should_sanitize_provider_error() -> None:
    # Given
    error = ApiProblem.model_validate(
        _problem() | {"code": 10000, "message": "auth failed", "request": "secret"}
    )

    # When / Then
    assert sanitize_error(error) == "Cloudflare 10000: auth failed"


def test_should_reject_foreign_project_id() -> None:
    # Given
    deployment = _deployment()
    deployment["project_id"] = "foreign-project-id"
    response = parse_deployments_response(_deployments_payload(deployment))

    # When / Then
    with pytest.raises(CloudflarePolicyError, match="deployment identity"):
        select_deployment(response, _target(), FULL_SHA, PROJECT_ID)


def test_should_reject_credential_bearing_deployment_url() -> None:
    # Given
    deployment = _deployment()
    deployment["url"] = "https://secret@f64788e9.edge-reco.pages.dev"
    response = parse_deployments_response(_deployments_payload(deployment))

    # When / Then
    with pytest.raises(CloudflarePolicyError, match="deployment identity"):
        select_deployment(response, _target(), FULL_SHA, PROJECT_ID)


def test_should_reject_hostname_not_equal_to_documented_short_id() -> None:
    deployment = _deployment()
    deployment["url"] = "https://foreign.edge-reco.pages.dev"
    response = parse_deployments_response(_deployments_payload(deployment))
    with pytest.raises(CloudflarePolicyError, match="deployment identity"):
        select_deployment(response, _target(), FULL_SHA, PROJECT_ID)


def test_should_select_latest_when_same_sha_has_prior_deployments() -> None:
    # Given
    response = parse_deployments_response(_deployments_payload(_deployment(), _deployment()))

    # When / Then
    selected = select_deployment(response, _target(), FULL_SHA, PROJECT_ID)
    assert selected is not None
    assert selected.id == "f64788e9-fccd-4d4a-a28a-cb84f88f6"


@pytest.mark.parametrize("status", ("failure", "canceled"))
def test_should_reject_failed_deployment_stage(status: str) -> None:
    # Given
    deployment = _deployment()
    deployment["latest_stage"] = {"name": "deploy", "status": status}
    response = parse_deployments_response(_deployments_payload(deployment))

    # When / Then
    with pytest.raises(CloudflarePolicyError, match="deployment failed"):
        select_deployment(response, _target(), FULL_SHA, PROJECT_ID)


@pytest.mark.parametrize(
    ("mutation", "value"),
    (
        ("source", None),
        ("domains", ["edge-reco.pages.dev"]),
        ("production_branch", "release"),
    ),
)
def test_should_reject_foreign_project_preflight(mutation: str, value: object) -> None:
    # Given
    payload = json.loads(_project_payload())
    payload["result"][mutation] = value
    project = parse_project_response(json.dumps(payload))

    # When / Then
    with pytest.raises(CloudflarePolicyError, match="target binding"):
        require_project_binding(project, _target())


@pytest.mark.parametrize(
    ("mutation", "value"),
    (
        ("owner", "foreign"),
        ("production_branch", "release"),
    ),
)
def test_should_reject_foreign_git_source(mutation: str, value: str) -> None:
    # Given
    payload = json.loads(_project_payload())
    payload["result"]["source"]["config"][mutation] = value
    project = parse_project_response(json.dumps(payload))

    # When / Then
    with pytest.raises(CloudflarePolicyError, match="target binding"):
        require_project_binding(project, _target())


def test_should_reject_malformed_account_reference() -> None:
    # Given / When / Then
    with pytest.raises(CloudflarePolicyError, match="account identity"):
        project_path("../../foreign", _target())


def test_should_report_sanitized_api_problem_only() -> None:
    # Given
    payload = json.dumps(
        {"errors": [_problem() | {"code": 10000, "message": "bad\nBearer token-secret"}]}
    )

    # When / Then
    assert provider_error_message(payload) == ("Cloudflare 10000: bad Bearer [redacted]")


@pytest.mark.parametrize("payload", ("not-json", "{}", '{"errors":{}}'))
def test_should_hide_malformed_provider_error(payload: str) -> None:
    # Given / When / Then
    assert provider_error_message(payload) == "Cloudflare API request failed"


def test_should_reject_unsuccessful_api_response_without_details() -> None:
    # Given
    payload = json.loads(_project_payload())
    payload["success"] = False

    # When / Then
    with pytest.raises(CloudflareApiError, match="API request failed"):
        parse_project_response(json.dumps(payload))


def test_should_reject_unsuccessful_api_response_with_sanitized_details() -> None:
    # Given
    payload = json.loads(_project_payload())
    payload["success"] = False
    payload["errors"] = [_problem() | {"code": 10000, "message": "auth failed"}]

    # When / Then
    with pytest.raises(CloudflareApiError, match="Cloudflare 10000: auth failed"):
        parse_project_response(json.dumps(payload))


@pytest.mark.parametrize(
    "payload",
    (
        "[]",
        '{"errors":{},"messages":[],"result":{},"success":true}',
        '{"errors":[],"messages":[],"success":true}',
    ),
)
def test_should_reject_malformed_response_shapes(payload: str) -> None:
    # Given / When / Then
    with pytest.raises(CloudflarePolicyError, match="schema"):
        parse_project_response(payload)
