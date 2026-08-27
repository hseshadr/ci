"""Authoritative, fail-closed GitHub evidence reader for the Dagger fleet."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from types import TracebackType
from typing import Final, Protocol, Self, cast
from urllib.error import HTTPError
from urllib.request import Request, build_opener

from pydantic import ConfigDict, TypeAdapter, ValidationError
from pydantic.dataclasses import dataclass as validated_dataclass

from ci.fleet_policy import CheckRun, Protection, RepositorySnapshot, RequiredCheck, SourceFile

BOUNDARY_CONFIG: Final = ConfigDict(frozen=True, extra="ignore")
WORKFLOW_PREFIX: Final = ".github/workflows/"
MODULE_PREFIXES: Final = (".dagger/src/", "dagger/src/")
HTTP_OK: Final = 200


@dataclass(frozen=True)
class HttpResponse:
    """One complete HTTP response returned by an injected transport."""

    status: int
    body: str


class GitHubTransport(Protocol):
    """Minimal behavior required from an authenticated GitHub transport."""

    def get(self, path: str) -> HttpResponse:
        """Read one repository-relative API path."""


class OpenedResponse(Protocol):
    """Response behavior used by the standard-library transport."""

    @property
    def status(self) -> int:
        """Return the HTTP response status."""

    def read(self) -> bytes:
        """Return the complete response body."""

    def __enter__(self) -> Self:
        """Enter the response resource scope."""

    def __exit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the response resource scope."""


class HttpOpener(Protocol):
    """Injectable HTTP request boundary."""

    def open(self, request: Request) -> OpenedResponse:
        """Open one fully-authenticated request."""


class DefaultHttpOpener:
    """Open GitHub API requests with a bounded timeout."""

    def open(self, request: Request) -> OpenedResponse:
        """Return one standard-library response resource."""
        if not request.full_url.startswith("https://api.github.com/"):
            raise FleetAccessError("GitHub transport requires the canonical HTTPS API origin")
        response = build_opener().open(request, timeout=30)
        return cast(OpenedResponse, response)


class GitHubHttpTransport:
    """Authenticate and execute exact GitHub REST API reads."""

    def __init__(self, token: str, opener: HttpOpener | None = None) -> None:
        self._token = token
        self._opener = DefaultHttpOpener() if opener is None else opener

    def get(self, path: str) -> HttpResponse:
        """Read one API path without exposing the bearer credential."""
        request = Request(f"https://api.github.com/{path}", headers=self._headers())
        try:
            with self._opener.open(request) as response:
                return HttpResponse(response.status, response.read().decode())
        except HTTPError as error:
            return HttpResponse(error.code, error.read().decode())

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }


class FleetAccessError(RuntimeError):
    """Authoritative fleet evidence could not be read or proved complete."""


@dataclass(frozen=True)
class SnapshotParts:
    """Validated evidence required to assemble one repository snapshot."""

    name: str
    sha: str
    sources: tuple[SourceFile, ...]
    protection: Protection
    checks: CheckRunsPayload
    codeql: CodeqlPayload


@validated_dataclass(config=BOUNDARY_CONFIG)
class CommitPayload:
    """GitHub commit identity response."""

    sha: str


@validated_dataclass(config=BOUNDARY_CONFIG)
class TreeEntry:
    """One recursive Git tree entry."""

    path: str
    type: str


@validated_dataclass(config=BOUNDARY_CONFIG)
class TreePayload:
    """A complete recursive exact-commit tree."""

    sha: str
    truncated: bool
    tree: tuple[TreeEntry, ...]


@validated_dataclass(config=BOUNDARY_CONFIG)
class ContentPayload:
    """One exact-commit file body."""

    type: str
    path: str
    encoding: str
    content: str


@validated_dataclass(config=BOUNDARY_CONFIG)
class EnabledPayload:
    """One enabled branch-protection flag."""

    enabled: bool


@validated_dataclass(config=BOUNDARY_CONFIG)
class ReviewsPayload:
    """Required review count for the protected branch."""

    required_approving_review_count: int


@validated_dataclass(config=BOUNDARY_CONFIG)
class StatusChecksPayload:
    """Strict app-bound required status checks."""

    strict: bool
    checks: tuple[RequiredCheck, ...]


@validated_dataclass(config=BOUNDARY_CONFIG)
class ProtectionPayload:
    """Effective classic branch-protection response."""

    required_status_checks: StatusChecksPayload
    enforce_admins: EnabledPayload
    required_pull_request_reviews: ReviewsPayload
    required_conversation_resolution: EnabledPayload
    required_linear_history: EnabledPayload
    allow_force_pushes: EnabledPayload
    allow_deletions: EnabledPayload


@validated_dataclass(config=BOUNDARY_CONFIG)
class AppPayload:
    """GitHub App identity attached to a check run."""

    id: int
    slug: str


@validated_dataclass(config=BOUNDARY_CONFIG)
class CheckPayload:
    """One exact-commit check run."""

    name: str
    head_sha: str
    conclusion: str | None
    app: AppPayload


@validated_dataclass(config=BOUNDARY_CONFIG)
class CheckRunsPayload:
    """One complete page of exact-commit check runs."""

    total_count: int
    check_runs: tuple[CheckPayload, ...]


@validated_dataclass(config=BOUNDARY_CONFIG)
class CodeqlPayload:
    """Managed CodeQL default-setup state."""

    state: str


def read_repository(transport: GitHubTransport, owner: str, name: str) -> RepositorySnapshot:
    """Read one fleet member exclusively from authoritative exact-main endpoints."""
    base = f"repos/{owner}/{name}"
    commit = read_model(transport, f"{base}/commits/main", CommitPayload)
    tree = read_tree(transport, base, commit.sha)
    paths = source_paths(tree)
    sources = tuple(read_source(transport, base, commit.sha, path) for path in paths)
    protection = read_protection(transport, base)
    checks = read_checks(transport, base, commit.sha)
    codeql = read_model(transport, f"{base}/code-scanning/default-setup", CodeqlPayload)
    parts = SnapshotParts(name, commit.sha, sources, protection, checks, codeql)
    return build_snapshot(parts)


def read_model[T](transport: GitHubTransport, path: str, model: type[T]) -> T:
    """Validate one successful GitHub response against its exact schema."""
    response = transport.get(path)
    if response.status != HTTP_OK:
        raise access_error(path, response.status)
    try:
        return TypeAdapter(model).validate_json(response.body)
    except ValidationError as error:
        raise FleetAccessError(f"invalid authoritative response for {path}: {error}") from error


def access_error(path: str, status: int) -> FleetAccessError:
    """Name the minimum fine-grained PAT permission for one failed endpoint."""
    scope = required_scope(path)
    return FleetAccessError(f"GitHub {status} for {path}; token requires {scope}")


def required_scope(path: str) -> str:
    """Map each authoritative endpoint to GitHub's minimum read permission."""
    if "/protection" in path or "/code-scanning/default-setup" in path:
        return "Administration:read"
    if "/check-runs" in path:
        return "Checks:read"
    return "Contents:read"


def read_tree(transport: GitHubTransport, base: str, sha: str) -> TreePayload:
    """Read and prove a complete recursive tree for the exact commit."""
    path = f"{base}/git/trees/{sha}?recursive=1"
    tree = read_model(transport, path, TreePayload)
    if tree.truncated or tree.sha != sha:
        raise FleetAccessError(f"incomplete authoritative source tree for {base}@{sha}")
    return tree


def source_paths(tree: TreePayload) -> tuple[str, ...]:
    """Select all authored workflow and Dagger module source files."""
    return tuple(entry.path for entry in tree.tree if is_policy_source(entry))


def is_policy_source(entry: TreeEntry) -> bool:
    """Return whether one blob is executable policy source."""
    authored = any((is_workflow_path(entry.path), is_module_path(entry.path)))
    return entry.type == "blob" and authored


def is_workflow_path(path: str) -> bool:
    """Return whether one path is an authored GitHub workflow."""
    return path.startswith(WORKFLOW_PREFIX) and path.endswith((".yml", ".yaml"))


def is_module_path(path: str) -> bool:
    """Return whether one path is an authored Dagger module source."""
    module_root = any(path.startswith(prefix) for prefix in MODULE_PREFIXES)
    return module_root and path.endswith((".py", ".ts"))


def read_source(transport: GitHubTransport, base: str, sha: str, path: str) -> SourceFile:
    """Decode one exact-commit GitHub contents response."""
    payload = read_model(transport, f"{base}/contents/{path}?ref={sha}", ContentPayload)
    if payload.type != "file" or payload.encoding != "base64" or payload.path != path:
        raise FleetAccessError(f"invalid exact source identity for {base}/{path}")
    try:
        encoded = "".join(payload.content.splitlines())
        text = base64.b64decode(encoded, validate=True).decode()
    except (ValueError, UnicodeDecodeError) as error:
        raise FleetAccessError(f"invalid exact source bytes for {base}/{path}") from error
    return SourceFile(path=path, text=text)


def read_protection(transport: GitHubTransport, base: str) -> Protection:
    """Translate effective REST protection without losing app identity."""
    payload = read_model(transport, f"{base}/branches/main/protection", ProtectionPayload)
    return Protection(
        strict=payload.required_status_checks.strict,
        enforce_admins=payload.enforce_admins.enabled,
        approvals=payload.required_pull_request_reviews.required_approving_review_count,
        conversation_resolution=payload.required_conversation_resolution.enabled,
        linear_history=payload.required_linear_history.enabled,
        allow_force_pushes=payload.allow_force_pushes.enabled,
        allow_deletions=payload.allow_deletions.enabled,
        checks=payload.required_status_checks.checks,
    )


def read_checks(transport: GitHubTransport, base: str, sha: str) -> CheckRunsPayload:
    """Read one complete exact-main check-run page."""
    path = f"{base}/commits/{sha}/check-runs?per_page=100"
    payload = read_model(transport, path, CheckRunsPayload)
    if payload.total_count != len(payload.check_runs):
        raise FleetAccessError(f"incomplete authoritative check runs for {base}@{sha}")
    return payload


def build_snapshot(parts: SnapshotParts) -> RepositorySnapshot:
    """Assemble validated API evidence into the domain snapshot."""
    workflows = select_sources(parts.sources, WORKFLOW_PREFIX)
    modules = select_modules(parts.sources)
    runs = build_check_runs(parts.checks)
    apps = build_check_apps(parts.checks)
    references = legacy_references(workflows)
    return RepositorySnapshot(
        name=parts.name,
        sha=parts.sha,
        workflows=workflows,
        modules=modules,
        protection=parts.protection,
        check_runs=runs,
        check_apps=apps,
        codeql_default_state=parts.codeql.state,
        legacy_references=references,
    )


def select_sources(sources: tuple[SourceFile, ...], prefix: str) -> tuple[SourceFile, ...]:
    """Select one reviewed source family from exact-main bytes."""
    return tuple(item for item in sources if item.path.startswith(prefix))


def select_modules(sources: tuple[SourceFile, ...]) -> tuple[SourceFile, ...]:
    """Select authored module sources from either Dagger SDK layout."""
    return tuple(
        item for item in sources if any(item.path.startswith(prefix) for prefix in MODULE_PREFIXES)
    )


def build_check_runs(checks: CheckRunsPayload) -> tuple[CheckRun, ...]:
    """Translate only concluded checks into greenable domain evidence."""
    return tuple(to_check_run(item) for item in checks.check_runs if item.conclusion is not None)


def build_check_apps(checks: CheckRunsPayload) -> tuple[str, ...]:
    """Return the distinct check applications observed on exact main."""
    return tuple(sorted({item.app.slug for item in checks.check_runs}))


def to_check_run(payload: CheckPayload) -> CheckRun:
    """Preserve app and exact-commit identity for one check run."""
    if payload.conclusion is None:
        raise ValueError("an in-progress check cannot become domain evidence")
    return CheckRun(
        name=payload.name,
        app_id=payload.app.id,
        head_sha=payload.head_sha,
        conclusion=payload.conclusion,
    )


def legacy_references(workflows: tuple[SourceFile, ...]) -> tuple[str, ...]:
    """Locate live execution references to the retiring central controls."""
    return tuple(source.path for source in workflows if "uses: hseshadr/ci/" in source.text)
