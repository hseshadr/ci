from __future__ import annotations

import asyncio
import http.client
import json
import os
import shutil
import subprocess
import time
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

import dagger
import pytest
from pydantic import ValidationError

from portfolio_foundation import github as github_module
from portfolio_foundation.github import (
    APP_ID,
    CHECK_NAME,
    WORKFLOW_NAME,
    WORKFLOW_PATH,
    ApiTarget,
    BranchPayload,
    CheckRunsPayload,
    DuplicateGreenCheckError,
    GitHubApiError,
    GitHubCheckRun,
    GitHubCredentialError,
    GitHubNetworkError,
    GitHubPolicyError,
    GitHubResponseError,
    HttpPage,
    RepositoryPayload,
    WorkflowRunPayload,
    parse_branch_response,
    parse_check_runs_response,
    parse_repository_response,
    parse_workflow_response,
    resolve_green_main,
    resolve_green_main_from_api,
    select_green_dagger,
)
from portfolio_foundation.identity import RepositoryRef

MODULE = Path(__file__).parents[2]
REPOSITORY = RepositoryRef("owner", "repository")
SHA = "a" * 40
RUN_ID = 456
LIVE_REPOSITORY = "hseshadr/ci"
LIVE_SHA = "1b2b18a38fc52801bdd2f3eb89d6616d847ef1fe"


def _app(app_id: int = APP_ID) -> dict[str, object]:
    return {"id": app_id}


def _check_payload(
    *,
    check_id: int = 123,
    status: str = "completed",
    conclusion: str | None = "success",
    app_id: int = APP_ID,
) -> dict[str, object]:
    return {
        "id": check_id,
        "name": CHECK_NAME,
        "head_sha": SHA,
        "status": status,
        "conclusion": conclusion,
        "details_url": f"https://github.com/owner/repository/actions/runs/{RUN_ID}/job/789",
        "app": _app(app_id),
    }


def _check(**changes: object) -> GitHubCheckRun:
    payload = _check_payload()
    payload.update(changes)
    return GitHubCheckRun.model_validate(payload)


def _repository_payload(**changes: object) -> str:
    payload: dict[str, object] = {"full_name": "owner/repository", "default_branch": "main"}
    payload.update(changes)
    return json.dumps(payload)


def _branch_payload(**changes: object) -> str:
    payload: dict[str, object] = {"name": "main", "commit": {"sha": SHA}}
    payload.update(changes)
    return json.dumps(payload)


def _checks_payload(*checks: dict[str, object], total_count: int | None = None) -> str:
    count = len(checks) if total_count is None else total_count
    return json.dumps({"total_count": count, "check_runs": checks})


def _workflow_payload(**changes: object) -> str:
    payload: dict[str, object] = {
        "id": RUN_ID,
        "name": WORKFLOW_NAME,
        "path": WORKFLOW_PATH,
        "head_branch": "main",
        "head_sha": SHA,
        "status": "completed",
        "conclusion": "success",
        "event": "push",
        "repository": {"full_name": "owner/repository"},
    }
    payload.update(changes)
    return json.dumps(payload)


def _targets() -> tuple[ApiTarget, ApiTarget, ApiTarget, ApiTarget]:
    base = "/repos/owner/repository"
    return (
        ApiTarget(base),
        ApiTarget(f"{base}/branches/main"),
        ApiTarget(f"{base}/commits/{SHA}/check-runs?filter=all&per_page=100&page=1"),
        ApiTarget(f"{base}/actions/runs/{RUN_ID}"),
    )


class FakeApi:
    def __init__(self, pages: Mapping[ApiTarget, HttpPage]) -> None:
        self._pages = pages

    async def get(self, target: ApiTarget) -> HttpPage:
        return self._pages[target]


class FakeSecret:
    def __init__(self, plaintext: str) -> None:
        self._plaintext = plaintext

    async def plaintext(self) -> str:
        return self._plaintext


class FakeHttpResponse:
    def __init__(self, status: int, body: str = "{}", link: str | None = None) -> None:
        self.status = status
        self._body = body
        self._link = link

    def read(self) -> bytes:
        return self._body.encode()

    def getheader(self, name: str) -> str | None:
        return self._link if name == "Link" else None


type HttpOutcome = FakeHttpResponse | OSError


class FakeHttpConnection:
    def __init__(self, outcomes: list[HttpOutcome], calls: list[tuple[str, str]]) -> None:
        self._outcomes = outcomes
        self._calls = calls

    def request(self, method: str, target: str, *, headers: dict[str, str]) -> None:
        assert headers["Authorization"].startswith("Bearer ")
        self._calls.append((method, target))

    def getresponse(self) -> FakeHttpResponse:
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, OSError):
            raise outcome
        return outcome

    def close(self) -> None:
        return None


def _install_http(
    monkeypatch: pytest.MonkeyPatch, outcomes: Sequence[HttpOutcome]
) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    queue = list(outcomes)

    def factory(_: str, *, timeout: float) -> FakeHttpConnection:
        assert timeout == 10.0
        return FakeHttpConnection(queue, calls)

    monkeypatch.setattr(http.client, "HTTPSConnection", factory)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    return calls


def _api(
    *,
    repository: str | None = None,
    branch: str | None = None,
    checks: str | None = None,
    workflow: str | None = None,
    link: str | None = None,
) -> FakeApi:
    repo_target, branch_target, checks_target, workflow_target = _targets()
    return FakeApi(
        {
            repo_target: HttpPage(repository or _repository_payload()),
            branch_target: HttpPage(branch or _branch_payload()),
            checks_target: HttpPage(checks or _checks_payload(_check_payload()), link),
            workflow_target: HttpPage(workflow or _workflow_payload()),
        }
    )


@pytest.mark.parametrize("conclusion", [None, "", "failure", "cancelled", "skipped"])
def test_should_not_accept_non_green_check(conclusion: str | None) -> None:
    # Given
    evidence = _check(status="in_progress", conclusion=conclusion)

    # When / Then
    assert select_green_dagger((evidence,), SHA) is None


def test_should_parse_nullable_conclusion_without_boundary_failure() -> None:
    # Given / When
    payload = CheckRunsPayload.model_validate(
        {"total_count": 1, "check_runs": (_check(conclusion=None).model_dump(),)}
    )

    # Then
    assert payload.check_runs[0].conclusion is None


def test_should_reject_green_check_from_wrong_app() -> None:
    # Given / When / Then
    assert select_green_dagger((_check(app={"id": 85455}),), SHA) is None


def test_should_reject_stale_or_wrong_named_green_check() -> None:
    # Given
    checks = (_check(head_sha="b" * 40), _check(name="Dagger fleet policy"))

    # When / Then
    assert select_green_dagger(checks, SHA) is None


def test_should_reject_duplicate_green_checks() -> None:
    # Given / When / Then
    with pytest.raises(DuplicateGreenCheckError, match="multiple exact-green"):
        select_green_dagger((_check(), _check(id=124)), SHA)


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (RepositoryPayload, {"full_name": "owner/repository", "default_branch": "main"}),
        (BranchPayload, {"name": "main", "commit": {"sha": SHA}}),
        (CheckRunsPayload, {"total_count": 0, "check_runs": []}),
        (WorkflowRunPayload, json.loads(_workflow_payload())),
    ],
)
def test_should_reject_unknown_external_response_fields(
    model: type[RepositoryPayload | BranchPayload | CheckRunsPayload | WorkflowRunPayload],
    payload: dict[str, object],
) -> None:
    # Given
    payload["unexpected"] = "value"

    # When / Then
    with pytest.raises(ValidationError, match="extra_forbidden"):
        model.model_validate(payload)


def test_should_keep_external_models_frozen() -> None:
    # Given
    payload = RepositoryPayload.model_validate(
        {"full_name": "owner/repository", "default_branch": "main"}
    )

    # When / Then
    with pytest.raises(ValidationError, match="frozen_instance"):
        payload.default_branch = "develop"


def test_should_project_documented_fields_from_realistic_provider_payloads() -> None:
    # Given
    repository = _repository_payload(id=1, url="https://api.github.com/repos/owner/repository")
    checks = json.loads(_checks_payload(_check_payload()))
    checks["check_runs"][0]["node_id"] = "node"

    # When / Then
    assert parse_repository_response(repository).full_name == "owner/repository"
    assert parse_branch_response(_branch_payload(protected=True)).commit.sha == SHA
    assert parse_check_runs_response(json.dumps(checks)).check_runs[0].app.id == APP_ID
    workflow = parse_workflow_response(_workflow_payload(created_at="2026-08-27T00:00:00Z"))
    assert workflow.id == RUN_ID


@pytest.mark.parametrize(
    "raw",
    ("not-json", "[]", "{}", '{"full_name": 3, "default_branch": "main"}'),
)
def test_should_sanitize_malformed_repository_responses(raw: str) -> None:
    # Given / When / Then
    with pytest.raises(GitHubResponseError, match="required schema"):
        parse_repository_response(raw)


@pytest.mark.parametrize("raw", ('{"check_runs": {}}', '{"check_runs": []}'))
def test_should_sanitize_malformed_check_pages(raw: str) -> None:
    # Given / When / Then
    with pytest.raises(GitHubResponseError, match="required schema"):
        parse_check_runs_response(raw)


def test_should_reject_api_target_outside_fixed_read_only_queries() -> None:
    # Given / When / Then
    with pytest.raises(GitHubPolicyError, match="read-only contract"):
        ApiTarget("/user/repos")


def test_should_use_get_and_return_only_response_body_and_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    link = '<https://api.github.com/repos/owner/repository?page=2>; rel="next"'
    calls = _install_http(monkeypatch, [FakeHttpResponse(http.client.OK, "{}", link)])
    api = github_module._GitHubRestApi("private-value")

    # When
    page = asyncio.run(api.get(ApiTarget("/repos/owner/repository")))

    # Then
    assert page == HttpPage("{}", link)
    assert calls == [("GET", "/repos/owner/repository")]
    assert "private-value" not in page.body + (page.link or "")


@pytest.mark.parametrize("status", (http.client.UNAUTHORIZED, http.client.FORBIDDEN))
def test_should_classify_rejected_credentials_without_response_leak(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    # Given
    _install_http(monkeypatch, [FakeHttpResponse(status, "provider-secret-body")])
    api = github_module._GitHubRestApi("private-value")

    # When / Then
    with pytest.raises(GitHubCredentialError, match="credential was rejected"):
        asyncio.run(api.get(ApiTarget("/repos/owner/repository")))


def test_should_retry_transient_status_then_return_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    outcomes = [FakeHttpResponse(http.client.INTERNAL_SERVER_ERROR), FakeHttpResponse(200)]
    calls = _install_http(monkeypatch, outcomes)

    # When
    page = asyncio.run(
        github_module._GitHubRestApi("private-value").get(ApiTarget("/repos/owner/repository"))
    )

    # Then
    assert page.body == "{}"
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("outcomes", "error"),
    [
        ([FakeHttpResponse(429) for _ in range(3)], GitHubApiError),
        ([OSError("private provider detail") for _ in range(3)], GitHubNetworkError),
    ],
)
def test_should_stop_after_bounded_provider_retries(
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[HttpOutcome],
    error: type[GitHubApiError | GitHubNetworkError],
) -> None:
    # Given
    _install_http(monkeypatch, outcomes)
    api = github_module._GitHubRestApi("private-value")

    # When / Then
    with pytest.raises(error):
        asyncio.run(api.get(ApiTarget("/repos/owner/repository")))


def test_should_reject_non_retryable_provider_status_without_body_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    _install_http(monkeypatch, [FakeHttpResponse(http.client.NOT_FOUND, "private detail")])

    # When / Then
    with pytest.raises(GitHubApiError, match="API request failed"):
        asyncio.run(
            github_module._GitHubRestApi("private-value").get(ApiTarget("/repos/owner/repository"))
        )


def test_should_reject_empty_typed_secret_before_provider_request() -> None:
    # Given
    secret = cast(dagger.Secret, FakeSecret(""))

    # When / Then
    with pytest.raises(GitHubCredentialError, match="credential was rejected"):
        asyncio.run(resolve_green_main(secret, REPOSITORY))


def test_should_keep_typed_secret_out_of_returned_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setattr(github_module, "_GitHubRestApi", lambda _: _api())
    secret = cast(dagger.Secret, FakeSecret("private-value"))

    # When
    evidence = asyncio.run(resolve_green_main(secret, REPOSITORY))

    # Then
    assert "private-value" not in repr(evidence)


def test_should_resolve_exact_main_and_app_bound_workflow_evidence() -> None:
    # Given / When
    evidence = asyncio.run(resolve_green_main_from_api(_api(), REPOSITORY))

    # Then
    assert evidence.repository == "owner/repository"
    assert evidence.branch == "main"
    assert evidence.commit_sha == SHA
    assert evidence.workflow_name == WORKFLOW_NAME
    assert evidence.workflow_path == WORKFLOW_PATH
    assert evidence.check_name == CHECK_NAME
    assert evidence.check_run_id == "123"
    assert evidence.workflow_run_id == str(RUN_ID)
    assert evidence.app_id == APP_ID


@pytest.mark.parametrize(
    ("api", "message"),
    [
        (_api(repository=_repository_payload(full_name="foreign/repository")), "repository"),
        (_api(repository=_repository_payload(default_branch="develop")), "default branch"),
        (_api(branch=_branch_payload(name="develop")), "branch"),
        (_api(branch=_branch_payload(commit={"sha": "short"})), "full SHA"),
        (_api(checks=_checks_payload(_check_payload() | {"head_sha": "b" * 40})), "exact-green"),
        (_api(checks=_checks_payload(_check_payload(app_id=85455))), "exact-green"),
        (_api(workflow=_workflow_payload(head_branch="feature")), "workflow"),
        (_api(workflow=_workflow_payload(head_sha="b" * 40)), "workflow"),
        (_api(workflow=_workflow_payload(path=".github/workflows/other.yml")), "workflow"),
        (_api(workflow=_workflow_payload(event="pull_request")), "workflow"),
        (_api(workflow=_workflow_payload(status="in_progress", conclusion=None)), "workflow"),
        (
            _api(workflow=_workflow_payload(repository={"full_name": "foreign/repository"})),
            "workflow",
        ),
    ],
)
def test_should_fail_closed_when_exact_policy_binding_differs(api: FakeApi, message: str) -> None:
    # Given / When / Then
    with pytest.raises(GitHubPolicyError, match=message):
        asyncio.run(resolve_green_main_from_api(api, REPOSITORY))


def test_should_follow_official_link_pagination_to_green_check() -> None:
    # Given
    repo, branch, checks, workflow = _targets()
    second = ApiTarget(
        f"/repos/owner/repository/commits/{SHA}/check-runs?filter=all&per_page=100&page=2"
    )
    link = f'<https://api.github.com{second.value}>; rel="next"'
    api = FakeApi(
        {
            repo: HttpPage(_repository_payload()),
            branch: HttpPage(_branch_payload()),
            checks: HttpPage(
                _checks_payload(_check_payload(check_id=122, status="in_progress"), total_count=2),
                link,
            ),
            second: HttpPage(_checks_payload(_check_payload(), total_count=2)),
            workflow: HttpPage(_workflow_payload()),
        }
    )

    # When
    evidence = asyncio.run(resolve_green_main_from_api(api, REPOSITORY))

    # Then
    assert evidence.commit_sha == SHA


@pytest.mark.parametrize("second_total", (3, 2))
def test_should_reject_inconsistent_or_duplicate_check_pages(second_total: int) -> None:
    # Given
    repo, branch, checks, workflow = _targets()
    second = ApiTarget(
        f"/repos/owner/repository/commits/{SHA}/check-runs?filter=all&per_page=100&page=2"
    )
    link = f'<https://api.github.com{second.value}>; rel="next"'
    first_check = _check_payload(status="in_progress")
    second_check = _check_payload(check_id=124 if second_total == 3 else 123)
    api = FakeApi(
        {
            repo: HttpPage(_repository_payload()),
            branch: HttpPage(_branch_payload()),
            checks: HttpPage(_checks_payload(first_check, total_count=2), link),
            second: HttpPage(_checks_payload(second_check, total_count=second_total)),
            workflow: HttpPage(_workflow_payload()),
        }
    )

    # When / Then
    with pytest.raises(GitHubPolicyError, match=r"count|duplicate"):
        asyncio.run(resolve_green_main_from_api(api, REPOSITORY))


def test_should_reject_foreign_pagination_origin() -> None:
    # Given
    target = f"/repos/owner/repository/commits/{SHA}/check-runs"
    link = f'<https://attacker.invalid{target}?filter=all&per_page=100&page=2>; rel="next"'

    # When / Then
    with pytest.raises(GitHubPolicyError, match="pagination"):
        asyncio.run(resolve_green_main_from_api(_api(link=link), REPOSITORY))


def test_should_reject_nonsequential_pagination() -> None:
    # Given
    target = f"/repos/owner/repository/commits/{SHA}/check-runs"
    link = f'<https://api.github.com{target}?filter=all&per_page=100&page=3>; rel="next"'

    # When / Then
    with pytest.raises(GitHubPolicyError, match="sequence"):
        asyncio.run(resolve_green_main_from_api(_api(link=link), REPOSITORY))


def test_should_reject_truncated_check_pagination() -> None:
    # Given
    checks = _checks_payload(_check_payload(), total_count=2)

    # When / Then
    with pytest.raises(GitHubPolicyError, match="count"):
        asyncio.run(resolve_green_main_from_api(_api(checks=checks), REPOSITORY))


def test_should_reject_check_details_outside_exact_repository_workflow() -> None:
    # Given
    checks = _checks_payload(
        _check_payload()
        | {"details_url": f"https://github.com/foreign/repository/actions/runs/{RUN_ID}/job/789"}
    )

    # When / Then
    with pytest.raises(GitHubPolicyError, match="details URL"):
        asyncio.run(resolve_green_main_from_api(_api(checks=checks), REPOSITORY))


def test_should_reject_duplicate_green_across_pages() -> None:
    # Given
    repo, branch, checks, workflow = _targets()
    second = ApiTarget(
        f"/repos/owner/repository/commits/{SHA}/check-runs?filter=all&per_page=100&page=2"
    )
    link = f'<https://api.github.com{second.value}>; rel="next"'
    api = FakeApi(
        {
            repo: HttpPage(_repository_payload()),
            branch: HttpPage(_branch_payload()),
            checks: HttpPage(_checks_payload(_check_payload(), total_count=2), link),
            second: HttpPage(_checks_payload(_check_payload(check_id=124), total_count=2)),
            workflow: HttpPage(_workflow_payload()),
        }
    )

    # When / Then
    with pytest.raises(DuplicateGreenCheckError):
        asyncio.run(resolve_green_main_from_api(api, REPOSITORY))


def test_should_distinguish_sanitized_transport_and_credential_errors() -> None:
    # Given / When / Then
    assert str(GitHubNetworkError()) == "GitHub network request failed"
    assert str(GitHubCredentialError()) == "GitHub credential was rejected"
    assert "token" not in str(GitHubApiError()).lower()


def test_should_declare_supported_pydantic_v2_dependency() -> None:
    # Given
    config = tomllib.loads((MODULE / ".dagger/pyproject.toml").read_text())

    # When
    dependencies = config["project"]["dependencies"]

    # Then
    assert "pydantic>=2.11,<3" in dependencies


@pytest.mark.skipif(os.environ.get("RUN_LIVE_GITHUB") != "1", reason="explicit live test")
def test_should_resolve_pinned_public_main_through_typed_secret() -> None:
    # Given
    gh, git, dagger_cli = _executable("gh"), _executable("git"), _executable("dagger")
    token = subprocess.run(  # noqa: S603 - fixed read-only credential lookup
        [gh, "auth", "token"], check=True, capture_output=True, text=True
    ).stdout.strip()
    remote = subprocess.run(  # noqa: S603 - fixed read-only Git query
        [git, "ls-remote", "origin", "refs/heads/main"],
        cwd=MODULE,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()[0]
    environment = os.environ | {"GITHUB_TOKEN": token}

    # When
    fields = ("commit-sha", "app-id", "repository", "branch", "check-name", "workflow-name")
    results = tuple(_call_green_field(dagger_cli, environment, field) for field in fields)
    evidence = tuple(result.stdout.strip() for result in results)

    # Then
    assert remote == LIVE_SHA
    for result in results:
        assert result.returncode == 0, _sanitized_cli_error(result.stderr, token)
        assert token not in result.stdout + result.stderr
        assert "greenMain CACHED" not in result.stderr
    assert evidence == (LIVE_SHA, str(APP_ID), LIVE_REPOSITORY, "main", CHECK_NAME, WORKFLOW_NAME)


def _call_green_field(
    dagger_cli: str, environment: dict[str, str], evidence_field: str
) -> subprocess.CompletedProcess[str]:
    command = [
        dagger_cli,
        "-m",
        str(MODULE),
        "call",
        "green-main",
        "--github-token=env:GITHUB_TOKEN",
        f"--repository={LIVE_REPOSITORY}",
        evidence_field,
    ]
    return subprocess.run(  # noqa: S603 - fixed read-only integration command
        command, cwd=MODULE, env=environment, capture_output=True, text=True, check=False
    )


def _sanitized_cli_error(stderr: str, token: str) -> str:
    return stderr.replace(token, "[REDACTED]")


def _executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        pytest.fail(f"required integration executable is unavailable: {name}")
    return executable
