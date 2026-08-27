"""Read-only, fail-closed GitHub evidence for an exact green default branch."""

from __future__ import annotations

import asyncio
import http.client
import re
import time
from dataclasses import dataclass
from typing import Final, Protocol
from urllib.parse import parse_qs, urlsplit

import dagger
from dagger import field, object_type
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

from .identity import FullSha, RepositoryRef

API_HOST: Final = "api.github.com"
API_ORIGIN: Final = f"https://{API_HOST}"
API_VERSION: Final = "2022-11-28"
APP_ID: Final = 15368
CHECK_NAME: Final = "Dagger"
DEFAULT_BRANCH: Final = "main"
MAX_PAGES: Final = 10
MAX_RETRIES: Final = 3
PAGE_SIZE: Final = 100
REQUEST_TIMEOUT_SECONDS: Final = 10.0
RETRY_DELAY_SECONDS: Final = 0.25
WORKFLOW_EVENT: Final = "push"
WORKFLOW_NAME: Final = "Dagger"
WORKFLOW_PATH: Final = ".github/workflows/dagger.yml"
TARGET_PATTERN: Final = re.compile(
    r"/repos/[A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]+(?:"
    r"|/branches/main"
    r"|/commits/[0-9a-f]{40}/check-runs\?filter=all&per_page=100&page=[1-9][0-9]*"
    r"|/actions/runs/[1-9][0-9]*"
    r")"
)
NEXT_LINK_PATTERN: Final = re.compile(r'<([^>]+)>;\s*rel="next"')
DETAILS_PATTERN: Final = re.compile(
    r"https://github\.com/([^/]+)/([^/]+)/actions/runs/([1-9][0-9]*)/job/[1-9][0-9]*"
)
CLOSED_MODEL: Final = ConfigDict(extra="forbid", frozen=True, strict=True)
JSON_ADAPTER: Final[TypeAdapter[JsonValue]] = TypeAdapter(JsonValue)


class GitHubError(RuntimeError):
    """Base error for sanitized GitHub evidence failures."""


class GitHubApiError(GitHubError):
    """Raised when GitHub returns an unusable response."""

    def __init__(self) -> None:
        super().__init__("GitHub API request failed")


class GitHubNetworkError(GitHubError):
    """Raised when the bounded provider request cannot complete."""

    def __init__(self) -> None:
        super().__init__("GitHub network request failed")


class GitHubCredentialError(GitHubError):
    """Raised when GitHub rejects the supplied typed secret."""

    def __init__(self) -> None:
        super().__init__("GitHub credential was rejected")


class GitHubResponseError(GitHubError):
    """Raised when a provider payload violates the closed response contract."""

    def __init__(self) -> None:
        super().__init__("GitHub response did not match the required schema")


class GitHubPolicyError(GitHubError):
    """Raised when provider state is valid but not exact-green evidence."""


class DuplicateGreenCheckError(GitHubPolicyError):
    """Raised when more than one check could authorize the same commit."""


@dataclass(frozen=True)
class ApiTarget:
    """A validated relative path for one of the four read-only GitHub queries."""

    value: str

    def __post_init__(self) -> None:
        if TARGET_PATTERN.fullmatch(self.value) is None:
            raise GitHubPolicyError("GitHub API target is outside the read-only contract")


@dataclass(frozen=True)
class HttpPage:
    """One response body and its optional pagination header."""

    body: str
    link: str | None = None


class GitHubApi(Protocol):
    """Read-only provider behavior injected into deterministic policy logic."""

    async def get(self, target: ApiTarget) -> HttpPage:
        """Fetch one validated official API target."""


class ClosedPayload(BaseModel):  # type: ignore[explicit-any]  # Pydantic v2 base stub
    """Strict immutable base for projected provider response contracts."""

    model_config = CLOSED_MODEL


class RepositoryPayload(ClosedPayload):  # type: ignore[explicit-any]  # Pydantic v2 base stub
    """Projected canonical repository fields from the GitHub REST response."""

    full_name: str
    default_branch: str


class BranchCommitPayload(ClosedPayload):  # type: ignore[explicit-any]  # Pydantic v2 base stub
    """Projected exact branch commit identity."""

    sha: str


class BranchPayload(ClosedPayload):  # type: ignore[explicit-any]  # Pydantic v2 base stub
    """Projected default branch state."""

    name: str
    commit: BranchCommitPayload


class GitHubAppPayload(ClosedPayload):  # type: ignore[explicit-any]  # Pydantic v2 base stub
    """Projected check publisher identity."""

    id: int


class GitHubCheckRun(ClosedPayload):  # type: ignore[explicit-any]  # Pydantic v2 base stub
    """Projected check-run state; conclusion is nullable while work is active."""

    id: int
    name: str
    head_sha: str
    status: str
    conclusion: str | None
    details_url: str
    app: GitHubAppPayload


class CheckRunsPayload(ClosedPayload):  # type: ignore[explicit-any]  # Pydantic v2 base stub
    """One strict projected check-runs page."""

    total_count: int = Field(ge=0)
    check_runs: tuple[GitHubCheckRun, ...]


class WorkflowRepositoryPayload(ClosedPayload):  # type: ignore[explicit-any]  # Pydantic v2 base stub
    """Projected workflow repository identity."""

    full_name: str


class WorkflowRunPayload(ClosedPayload):  # type: ignore[explicit-any]  # Pydantic v2 base stub
    """Projected workflow-run binding for the accepted check job."""

    id: int
    name: str
    path: str
    head_branch: str
    head_sha: str
    status: str
    conclusion: str | None
    event: str
    repository: WorkflowRepositoryPayload


@object_type
class CheckEvidence:
    """Non-secret exact-green evidence safe to pass between Dagger modules."""

    repository: str = field(init=False)
    branch: str = field(init=False)
    commit_sha: str = field(init=False)
    workflow_name: str = field(init=False)
    workflow_path: str = field(init=False)
    check_name: str = field(init=False)
    check_run_id: str = field(init=False)
    workflow_run_id: str = field(init=False)
    app_id: int = field(init=False)

    def __init__(self, evidence: _EvidenceData) -> None:
        identity, run = evidence.identity, evidence.run
        self.repository, self.branch, self.commit_sha = identity
        self.workflow_name = run[0]
        self.workflow_path = run[1]
        self.check_name = run[2]
        self.check_run_id = run[3]
        self.workflow_run_id = run[4]
        self.app_id = run[5]


@dataclass(frozen=True)
class _EvidenceData:
    identity: tuple[str, str, str]
    run: tuple[str, str, str, str, str, int]


@dataclass(frozen=True)
class _CheckCollection:
    total_count: int | None
    checks: tuple[GitHubCheckRun, ...]


class _RetryableStatusError(RuntimeError):
    def __init__(self, status: int) -> None:
        self.status = status


class _GitHubRestApi:
    """Bounded GET-only adapter for the fixed official GitHub API host."""

    def __init__(self, token: str) -> None:
        self._token = token

    async def get(self, target: ApiTarget) -> HttpPage:
        return await asyncio.to_thread(self._get_with_retries, target)

    def _get_with_retries(self, target: ApiTarget) -> HttpPage:
        for attempt in range(MAX_RETRIES):
            try:
                return self._get_once(target)
            except _RetryableStatusError:
                _retry_api(attempt)
            except (OSError, TimeoutError, http.client.HTTPException):
                _retry_network(attempt)
        raise GitHubNetworkError

    def _get_once(self, target: ApiTarget) -> HttpPage:
        connection = http.client.HTTPSConnection(API_HOST, timeout=REQUEST_TIMEOUT_SECONDS)
        try:
            connection.request("GET", target.value, headers=self._headers())
            response = connection.getresponse()
            body = response.read().decode("utf-8")
            return _accepted_page(response.status, body, response.getheader("Link"))
        finally:
            connection.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "portfolio-foundation",
            "X-GitHub-Api-Version": API_VERSION,
        }


def _accepted_page(status: int, body: str, link: str | None) -> HttpPage:
    if status == http.client.OK:
        return HttpPage(body, link)
    if status in {http.client.UNAUTHORIZED, http.client.FORBIDDEN}:
        raise GitHubCredentialError
    if status == http.client.TOO_MANY_REQUESTS or status >= http.client.INTERNAL_SERVER_ERROR:
        raise _RetryableStatusError(status)
    raise GitHubApiError


def _retry_api(attempt: int) -> None:
    if attempt == MAX_RETRIES - 1:
        raise GitHubApiError from None
    time.sleep(RETRY_DELAY_SECONDS * (attempt + 1))


def _retry_network(attempt: int) -> None:
    if attempt == MAX_RETRIES - 1:
        raise GitHubNetworkError from None
    time.sleep(RETRY_DELAY_SECONDS * (attempt + 1))


def parse_repository_response(raw: str) -> RepositoryPayload:
    """Project and validate canonical repository fields from a real response."""
    payload = _json_object(raw)
    return _model(RepositoryPayload, _project(payload, ("full_name", "default_branch")))


def parse_branch_response(raw: str) -> BranchPayload:
    """Project and validate the exact default-branch commit."""
    payload = _json_object(raw)
    commit = _project(_object(_required(payload, "commit")), ("sha",))
    projected = {"name": _required(payload, "name"), "commit": commit}
    return _model(BranchPayload, projected)


def parse_check_runs_response(raw: str) -> CheckRunsPayload:
    """Project and validate a check-runs page, including nullable conclusions."""
    payload = _json_object(raw)
    runs = tuple(_project_check(item) for item in _array(_required(payload, "check_runs")))
    projected = {"total_count": _required(payload, "total_count"), "check_runs": runs}
    return _model(CheckRunsPayload, projected)


def parse_workflow_response(raw: str) -> WorkflowRunPayload:
    """Project and validate the workflow run bound to an accepted check."""
    payload = _json_object(raw)
    repository = _project(_object(_required(payload, "repository")), ("full_name",))
    fields = _workflow_fields()
    projected = _project(payload, fields) | {"repository": repository}
    return _model(WorkflowRunPayload, projected)


def _workflow_fields() -> tuple[str, ...]:
    return (
        "id",
        "name",
        "path",
        "head_branch",
        "head_sha",
        "status",
        "conclusion",
        "event",
    )


def _project_check(value: JsonValue) -> dict[str, JsonValue]:
    payload = _object(value)
    app = _project(_object(_required(payload, "app")), ("id",))
    fields = ("id", "name", "head_sha", "status", "conclusion", "details_url")
    return _project(payload, fields) | {"app": app}


def _json_object(raw: str) -> dict[str, JsonValue]:
    try:
        return _object(JSON_ADAPTER.validate_json(raw))
    except (ValidationError, ValueError, UnicodeError):
        raise GitHubResponseError from None


def _object(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise GitHubResponseError
    return value


def _array(value: JsonValue) -> list[JsonValue]:
    if not isinstance(value, list):
        raise GitHubResponseError
    return value


def _required(payload: dict[str, JsonValue], name: str) -> JsonValue:
    try:
        return payload[name]
    except KeyError:
        raise GitHubResponseError from None


def _project(payload: dict[str, JsonValue], names: tuple[str, ...]) -> dict[str, JsonValue]:
    return {name: _required(payload, name) for name in names}


def _model[T: BaseModel](model: type[T], payload: object) -> T:
    try:
        return model.model_validate(payload)
    except ValidationError:
        raise GitHubResponseError from None


def select_green_dagger(
    checks: tuple[GitHubCheckRun, ...], commit_sha: str
) -> GitHubCheckRun | None:
    """Select exactly one app-bound completed successful Dagger check."""
    candidates = tuple(check for check in checks if _is_green_dagger(check, commit_sha))
    if len(candidates) > 1:
        raise DuplicateGreenCheckError("multiple exact-green Dagger checks found")
    return candidates[0] if candidates else None


def _is_green_dagger(check: GitHubCheckRun, commit_sha: str) -> bool:
    return (
        check.name == CHECK_NAME
        and check.head_sha == commit_sha
        and check.status == "completed"
        and check.conclusion == "success"
        and check.app.id == APP_ID
    )


async def resolve_green_main(
    github_token: dagger.Secret, repository: RepositoryRef
) -> CheckEvidence:
    """Resolve exact-green main using only a typed GitHub secret and canonical repository."""
    try:
        token = await github_token.plaintext()
    except dagger.QueryError:
        raise GitHubCredentialError from None
    if not token:
        raise GitHubCredentialError
    return await resolve_green_main_from_api(_GitHubRestApi(token), repository)


async def resolve_green_main_from_api(api: GitHubApi, repository: RepositoryRef) -> CheckEvidence:
    """Apply deterministic exact-green policy to a read-only API adapter."""
    repo = await _repository(api, repository)
    commit_sha = await _main_sha(api, repository, repo)
    checks = await _check_runs(api, repository, commit_sha)
    check = select_green_dagger(checks, commit_sha)
    if check is None:
        raise GitHubPolicyError("no exact-green Dagger check found")
    workflow = await _workflow(api, repository, check, commit_sha)
    return _evidence(repository, commit_sha, check, workflow)


async def _repository(api: GitHubApi, expected: RepositoryRef) -> RepositoryPayload:
    target = ApiTarget(f"/repos/{expected.owner}/{expected.name}")
    actual = parse_repository_response((await api.get(target)).body)
    if actual.full_name != _repository_name(expected):
        raise GitHubPolicyError("GitHub repository identity differs")
    if actual.default_branch != DEFAULT_BRANCH:
        raise GitHubPolicyError("GitHub default branch is not main")
    return actual


async def _main_sha(api: GitHubApi, repository: RepositoryRef, _: RepositoryPayload) -> str:
    base = _repository_path(repository)
    branch = parse_branch_response((await api.get(ApiTarget(f"{base}/branches/main"))).body)
    if branch.name != DEFAULT_BRANCH:
        raise GitHubPolicyError("GitHub branch identity differs")
    try:
        return FullSha(branch.commit.sha).value
    except ValueError:
        raise GitHubPolicyError("GitHub main commit is not a full SHA") from None


async def _check_runs(
    api: GitHubApi, repository: RepositoryRef, commit_sha: str
) -> tuple[GitHubCheckRun, ...]:
    target = _first_check_target(repository, commit_sha)
    collection = _CheckCollection(None, ())
    for _ in range(MAX_PAGES):
        page = await api.get(target)
        collection = _merge_checks(collection, parse_check_runs_response(page.body))
        next_target = _next_target(page.link, target)
        if next_target is None:
            return _complete_checks(collection)
        target = next_target
    raise GitHubPolicyError("GitHub pagination exceeded the bounded page limit")


def _merge_checks(collection: _CheckCollection, page: CheckRunsPayload) -> _CheckCollection:
    expected = page.total_count if collection.total_count is None else collection.total_count
    if expected != page.total_count:
        raise GitHubPolicyError("GitHub check count differs across pages")
    checks = collection.checks + page.check_runs
    if len({check.id for check in checks}) != len(checks):
        raise GitHubPolicyError("GitHub pagination contains duplicate check runs")
    return _CheckCollection(expected, checks)


def _complete_checks(collection: _CheckCollection) -> tuple[GitHubCheckRun, ...]:
    if collection.total_count != len(collection.checks):
        raise GitHubPolicyError("GitHub check count differs from paginated results")
    return collection.checks


def _first_check_target(repository: RepositoryRef, commit_sha: str) -> ApiTarget:
    query = f"filter=all&per_page={PAGE_SIZE}&page=1"
    return ApiTarget(f"{_repository_path(repository)}/commits/{commit_sha}/check-runs?{query}")


def _next_target(link: str | None, current: ApiTarget) -> ApiTarget | None:
    if link is None:
        return None
    match = NEXT_LINK_PATTERN.search(link)
    if match is None:
        return None
    parsed = urlsplit(match.group(1))
    candidate = ApiTarget(f"{parsed.path}?{parsed.query}")
    _require_next_page(parsed.scheme, parsed.netloc, current, candidate)
    return candidate


def _require_next_page(scheme: str, host: str, current: ApiTarget, candidate: ApiTarget) -> None:
    if scheme != "https" or host != API_HOST:
        raise GitHubPolicyError("GitHub pagination origin differs")
    current_path, current_page = _page_identity(current)
    candidate_path, candidate_page = _page_identity(candidate)
    if current_path != candidate_path or candidate_page != current_page + 1:
        raise GitHubPolicyError("GitHub pagination sequence differs")


def _page_identity(target: ApiTarget) -> tuple[str, int]:
    parsed = urlsplit(target.value)
    query = parse_qs(parsed.query, strict_parsing=True)
    if set(query) != {"filter", "per_page", "page"}:
        raise GitHubPolicyError("GitHub pagination query differs")
    if query["filter"] != ["all"] or query["per_page"] != [str(PAGE_SIZE)]:
        raise GitHubPolicyError("GitHub pagination policy differs")
    return parsed.path, int(query["page"][0])


async def _workflow(
    api: GitHubApi, repository: RepositoryRef, check: GitHubCheckRun, commit_sha: str
) -> WorkflowRunPayload:
    run_id = _run_id(check.details_url, repository)
    target = ApiTarget(f"{_repository_path(repository)}/actions/runs/{run_id}")
    workflow = parse_workflow_response((await api.get(target)).body)
    if not _matches_workflow(workflow, repository, commit_sha, run_id):
        raise GitHubPolicyError("GitHub workflow identity differs")
    return workflow


def _run_id(details_url: str, repository: RepositoryRef) -> int:
    match = DETAILS_PATTERN.fullmatch(details_url)
    if match is None or match.group(1, 2) != (repository.owner, repository.name):
        raise GitHubPolicyError("GitHub check details URL differs")
    return int(match.group(3))


def _matches_workflow(
    workflow: WorkflowRunPayload, repository: RepositoryRef, commit_sha: str, run_id: int
) -> bool:
    identity = _workflow_identity(workflow)
    expected = (run_id, WORKFLOW_NAME, WORKFLOW_PATH, DEFAULT_BRANCH, commit_sha)
    policy = workflow.status == "completed" and workflow.conclusion == "success"
    policy = policy and workflow.event == WORKFLOW_EVENT
    repository_matches = workflow.repository.full_name == _repository_name(repository)
    return identity == expected and policy and repository_matches


def _workflow_identity(workflow: WorkflowRunPayload) -> tuple[int, str, str, str, str]:
    return (
        workflow.id,
        workflow.name,
        workflow.path,
        workflow.head_branch,
        workflow.head_sha,
    )


def _evidence(
    repository: RepositoryRef,
    commit_sha: str,
    check: GitHubCheckRun,
    workflow: WorkflowRunPayload,
) -> CheckEvidence:
    identity = (_repository_name(repository), DEFAULT_BRANCH, commit_sha)
    run = _evidence_run(check, workflow)
    data = _EvidenceData(identity, run)
    return CheckEvidence(data)


def _evidence_run(
    check: GitHubCheckRun, workflow: WorkflowRunPayload
) -> tuple[str, str, str, str, str, int]:
    return (
        workflow.name,
        workflow.path,
        check.name,
        str(check.id),
        str(workflow.id),
        check.app.id,
    )


def _repository_path(repository: RepositoryRef) -> str:
    return f"/repos/{repository.owner}/{repository.name}"


def _repository_name(repository: RepositoryRef) -> str:
    return f"{repository.owner}/{repository.name}"
