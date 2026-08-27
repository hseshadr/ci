from __future__ import annotations

import base64
import json
import textwrap
from dataclasses import dataclass
from types import TracebackType
from urllib.request import Request

import pytest

from ci.github_fleet import (
    FleetAccessError,
    GitHubHttpTransport,
    HttpResponse,
    OpenedResponse,
    read_repository,
)

SHA = "a" * 40


@dataclass(frozen=True)
class FakeTransport:
    """Return exact fixture responses for one authoritative read."""

    responses: dict[str, HttpResponse]

    def get(self, path: str) -> HttpResponse:
        return self.responses.get(path, HttpResponse(status=404, body="{}"))


def _json(value: object, status: int = 200) -> HttpResponse:
    return HttpResponse(status=status, body=json.dumps(value))


def _content(path: str, text: str) -> HttpResponse:
    encoded = base64.b64encode(text.encode()).decode()
    wrapped = textwrap.fill(encoded, width=12)
    return _json({"type": "file", "path": path, "encoding": "base64", "content": wrapped})


def _responses() -> dict[str, HttpResponse]:
    base = "repos/hseshadr/example"
    workflow = ".github/workflows/dagger.yml"
    module = "dagger/src/index.ts"
    return {
        f"{base}/commits/main": _json({"sha": SHA}),
        f"{base}/git/trees/{SHA}?recursive=1": _json(
            {
                "sha": SHA,
                "truncated": False,
                "tree": [
                    {"path": workflow, "type": "blob"},
                    {"path": module, "type": "blob"},
                ],
            }
        ),
        f"{base}/contents/{workflow}?ref={SHA}": _content(workflow, "name: Dagger"),
        f"{base}/contents/{module}?ref={SHA}": _content(module, "source: dagger.Directory"),
        f"{base}/branches/main/protection": _json(
            {
                "required_status_checks": {
                    "strict": True,
                    "checks": [{"context": "Dagger", "app_id": 15368}],
                },
                "enforce_admins": {"enabled": True},
                "required_pull_request_reviews": {"required_approving_review_count": 0},
                "required_conversation_resolution": {"enabled": True},
                "required_linear_history": {"enabled": True},
                "allow_force_pushes": {"enabled": False},
                "allow_deletions": {"enabled": False},
            }
        ),
        f"{base}/commits/{SHA}/check-runs?per_page=100": _json(
            {
                "total_count": 2,
                "check_runs": [
                    {
                        "name": "Dagger",
                        "head_sha": SHA,
                        "conclusion": "success",
                        "app": {"id": 15368, "slug": "github-actions"},
                    },
                    {
                        "name": "GitGuardian Security Checks",
                        "head_sha": SHA,
                        "conclusion": "success",
                        "app": {"id": 123, "slug": "gitguardian"},
                    },
                ],
            }
        ),
        f"{base}/code-scanning/default-setup": _json({"state": "not-configured"}),
    }


@dataclass(frozen=True)
class FakeOpenedResponse:
    """Behave like one successful urllib response."""

    status: int
    body: bytes

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> FakeOpenedResponse:
        return self

    def __exit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


@dataclass
class RecordingOpener:
    """Capture one request while returning deterministic bytes."""

    response: FakeOpenedResponse
    authorization: str = ""
    url: str = ""

    def open(self, request: Request) -> OpenedResponse:
        self.authorization = request.get_header("Authorization") or ""
        self.url = request.full_url
        return self.response


def test_should_build_exact_snapshot_when_authoritative_endpoints_are_complete() -> None:
    # Given complete exact-main source, protection, checks, and CodeQL responses
    transport = FakeTransport(_responses())

    # When the repository is read through the typed boundary
    snapshot = read_repository(transport, "hseshadr", "example")

    # Then identity and app-bound evidence remain exact
    assert snapshot.sha == SHA
    assert snapshot.protection.checks[0].app_id == 15368
    assert snapshot.check_runs[0].head_sha == SHA
    assert snapshot.codeql_default_state == "not-configured"
    assert snapshot.modules[0].path == "dagger/src/index.ts"


def test_should_parse_in_progress_checks_without_treating_them_as_green() -> None:
    # Given GitHub reports an in-progress exact-main check with no conclusion yet
    responses = _responses()
    path = f"repos/hseshadr/example/commits/{SHA}/check-runs?per_page=100"
    payload = json.loads(responses[path].body)
    payload["total_count"] = 3
    payload["check_runs"].append(
        {
            "name": "Dagger fleet policy",
            "head_sha": SHA,
            "conclusion": None,
            "app": {"id": 999, "slug": "untrusted-app"},
        }
    )
    responses[path] = _json(payload)

    # When the authoritative boundary parses the live check page
    snapshot = read_repository(FakeTransport(responses), "hseshadr", "example")

    # Then unfinished work cannot satisfy a green check, but its app remains observable
    assert all(run.name != "Dagger fleet policy" for run in snapshot.check_runs)
    assert "untrusted-app" in snapshot.check_apps


def test_should_name_minimal_scope_when_protection_read_is_forbidden() -> None:
    # Given a token that can read source but not effective protection
    responses = _responses()
    responses["repos/hseshadr/example/branches/main/protection"] = _json({}, status=403)

    # When the authoritative reader reaches the protected endpoint
    with pytest.raises(FleetAccessError, match="Administration:read"):
        read_repository(FakeTransport(responses), "hseshadr", "example")


def test_should_fail_closed_when_tree_or_check_page_is_incomplete() -> None:
    # Given GitHub signals that source or check evidence was truncated
    responses = _responses()
    responses[f"repos/hseshadr/example/git/trees/{SHA}?recursive=1"] = _json(
        {"sha": SHA, "truncated": True, "tree": []}
    )

    # When the repository read cannot prove completeness
    with pytest.raises(FleetAccessError, match="incomplete"):
        read_repository(FakeTransport(responses), "hseshadr", "example")


def test_should_authenticate_exact_api_path_when_transport_reads_github() -> None:
    # Given an injected HTTP opener and a credential held only by the transport
    opener = RecordingOpener(FakeOpenedResponse(status=200, body=b'{"sha":"abc"}'))
    transport = GitHubHttpTransport("secret-token", opener)

    # When one repository-relative path is read
    response = transport.get("repos/hseshadr/example/commits/main")

    # Then the request uses GitHub's versioned bearer boundary and exact URL
    assert response == HttpResponse(status=200, body='{"sha":"abc"}')
    assert opener.authorization == "Bearer secret-token"
    assert opener.url == "https://api.github.com/repos/hseshadr/example/commits/main"


def test_should_accept_additive_provider_fields_when_required_evidence_is_typed() -> None:
    # Given GitHub adds an unrelated response field while required evidence remains exact
    responses = _responses()
    responses["repos/hseshadr/example/commits/main"] = _json(
        {"sha": SHA, "provider-added-field": "ignored"}
    )

    # When the typed evidence reader selects its reviewed contract
    snapshot = read_repository(FakeTransport(responses), "hseshadr", "example")

    # Then provider evolution does not weaken or break the required identity
    assert snapshot.sha == SHA
