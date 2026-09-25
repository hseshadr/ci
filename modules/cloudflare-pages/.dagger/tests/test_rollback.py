"""Production rollback policy: explicit targets, refusals, and verified convergence."""

from __future__ import annotations

import ast
import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import dagger
import pytest

import cloudflare_pages.main as main_module
from cloudflare_pages.api import (
    CloudflareApiError,
    CloudflarePolicyError,
    live_production_deployment,
    rollback_production,
)
from cloudflare_pages.main import CurlRollbackOperations
from cloudflare_pages.models import RollbackEvidence

PROJECT = "edge-reco"
PROJECT_ID = "7b162ea7-7367-4d4a-a28a-cb84f88f6"
LIVE = "11111111"
PREVIOUS = "22222222"
FAILED = "33333333"
OLDER = "44444444"
SECRET_TYPE = "dagger." + "Secret"


def _id(short_id: str) -> str:
    return f"{short_id}-0000-4000-8000-000000000000"


def _row(
    short_id: str,
    *,
    status: str = "success",
    environment: str = "production",
    stage: str = "deploy",
) -> dict[str, object]:
    return {
        "id": _id(short_id),
        "short_id": short_id,
        "url": f"https://{short_id}.{PROJECT}.pages.dev",
        "project_id": PROJECT_ID,
        "project_name": PROJECT,
        "environment": environment,
        "latest_stage": {"name": stage, "status": status},
        "deployment_trigger": {
            "type": "ad_hoc",
            "metadata": {"branch": "main", "commit_hash": "", "commit_dirty": False},
        },
    }


def _project(canonical: str | None, name: str = PROJECT) -> str:
    live = None if canonical is None else {"id": _id(canonical)}
    result = {
        "id": PROJECT_ID,
        "name": name,
        "production_branch": "main",
        "domains": [f"{PROJECT}.pages.dev"],
        "source": None,
        "canonical_deployment": live,
    }
    return json.dumps({"errors": [], "messages": [], "success": True, "result": result})


def _deployments(*rows: dict[str, object]) -> str:
    info = {"count": len(rows), "page": 1, "per_page": 10, "total_count": 30, "total_pages": 3}
    payload = {"errors": [], "messages": [], "success": True, "result": list(rows)}
    return json.dumps(payload | {"result_info": info})


def _rollback_response(short_id: str) -> str:
    return json.dumps({"errors": [], "messages": [], "success": True, "result": _row(short_id)})


def _history() -> tuple[dict[str, object], ...]:
    return (_row(LIVE), _row(FAILED, status="failure"), _row(PREVIOUS), _row(OLDER))


CloudflareError = CloudflareApiError | CloudflarePolicyError


@dataclass
class FakeRollbackOperations:
    """Stubbed Pages API that switches production only when told to converge."""

    rows: tuple[dict[str, object], ...] = field(default_factory=_history)
    live: str | None = LIVE
    converge: bool = True
    project_name: str = PROJECT
    rollback_error: CloudflareError | None = None
    response_id: str | None = None
    events: list[str] = field(default_factory=list)

    async def get_project(self) -> str:
        self.events.append("get-project")
        return _project(self.live, self.project_name)

    async def get_deployments(self) -> str:
        self.events.append("get-deployments")
        return _deployments(*self.rows)

    async def rollback(self, deployment_id: str) -> str:
        self.events.append(f"rollback:{deployment_id}")
        if self.rollback_error is not None:
            raise self.rollback_error
        short_id = deployment_id.split("-", maxsplit=1)[0]
        if self.converge:
            self.live = short_id
        return _rollback_response(self.response_id or short_id)

    async def sleep(self, seconds: int) -> None:
        self.events.append(f"sleep:{seconds}")


def _rollbacks(operations: FakeRollbackOperations) -> list[str]:
    return [event for event in operations.events if event.startswith("rollback:")]


@pytest.mark.asyncio
async def test_should_roll_back_to_explicit_production_deployment_and_verify_live() -> None:
    operations = FakeRollbackOperations()

    evidence = await rollback_production(operations, PROJECT, _id(PREVIOUS))

    assert evidence == RollbackEvidence(
        PROJECT,
        _id(LIVE),
        _id(PREVIOUS),
        _id(PREVIOUS),
        f"https://{PREVIOUS}.{PROJECT}.pages.dev",
    )
    assert operations.events == [
        "get-project",
        "get-deployments",
        f"rollback:{_id(PREVIOUS)}",
        "get-project",
    ]


@pytest.mark.asyncio
async def test_should_default_to_previous_successful_production_deployment() -> None:
    operations = FakeRollbackOperations()

    evidence = await rollback_production(operations, PROJECT, None)

    assert evidence.to_deployment_id == _id(PREVIOUS)
    assert _rollbacks(operations) == [f"rollback:{_id(PREVIOUS)}"]


@pytest.mark.asyncio
async def test_should_refuse_when_no_previous_successful_deployment_exists() -> None:
    operations = FakeRollbackOperations(rows=(_row(LIVE), _row(FAILED, status="failure")))

    with pytest.raises(CloudflarePolicyError, match="no previous successful production"):
        await rollback_production(operations, PROJECT, None)

    assert _rollbacks(operations) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "reason"),
    (
        (_row(PREVIOUS, environment="preview"), "not a production deployment"),
        (_row(PREVIOUS, status="failure"), "not a successful deployment"),
        (_row(PREVIOUS, status="active"), "not a successful deployment"),
        (_row(PREVIOUS, stage="build", status="success"), "not a successful deployment"),
    ),
)
async def test_should_refuse_preview_or_unsuccessful_target(
    row: dict[str, object], reason: str
) -> None:
    operations = FakeRollbackOperations(rows=(_row(LIVE), row))

    with pytest.raises(CloudflarePolicyError, match=reason):
        await rollback_production(operations, PROJECT, _id(PREVIOUS))

    assert _rollbacks(operations) == []


@pytest.mark.asyncio
async def test_should_refuse_target_absent_from_recent_production_history() -> None:
    operations = FakeRollbackOperations(rows=(_row(LIVE),))

    with pytest.raises(CloudflarePolicyError, match="not in recent production history"):
        await rollback_production(operations, PROJECT, _id(PREVIOUS))

    assert _rollbacks(operations) == []


@pytest.mark.asyncio
async def test_should_refuse_foreign_project_target() -> None:
    foreign = _row(PREVIOUS) | {"project_id": "other-project"}
    operations = FakeRollbackOperations(rows=(_row(LIVE), foreign))

    with pytest.raises(CloudflarePolicyError, match="identity differs"):
        await rollback_production(operations, PROJECT, _id(PREVIOUS))


@pytest.mark.asyncio
async def test_should_refuse_explicit_noop_when_target_is_already_live() -> None:
    operations = FakeRollbackOperations()

    with pytest.raises(CloudflarePolicyError, match="already live"):
        await rollback_production(operations, PROJECT, _id(LIVE))

    assert _rollbacks(operations) == []


@pytest.mark.asyncio
async def test_should_refuse_rollback_without_a_live_production_deployment() -> None:
    operations = FakeRollbackOperations(live=None)

    with pytest.raises(CloudflarePolicyError, match="no live production deployment"):
        await rollback_production(operations, PROJECT, _id(PREVIOUS))

    assert _rollbacks(operations) == []


@pytest.mark.asyncio
async def test_should_refuse_mismatched_project_binding() -> None:
    operations = FakeRollbackOperations(project_name="other")

    with pytest.raises(CloudflarePolicyError, match="project binding differs"):
        await rollback_production(operations, PROJECT, _id(PREVIOUS))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    (
        CloudflareApiError("Cloudflare 8000009: rollback rejected"),
        CloudflareApiError("Cloudflare API request failed"),
    ),
)
async def test_should_fail_closed_when_rollback_api_rejects(error: CloudflareApiError) -> None:
    operations = FakeRollbackOperations(rollback_error=error)

    with pytest.raises(CloudflareApiError):
        await rollback_production(operations, PROJECT, _id(PREVIOUS))

    assert operations.events[-1] == f"rollback:{_id(PREVIOUS)}"


@pytest.mark.asyncio
async def test_should_refuse_rollback_response_for_different_deployment() -> None:
    operations = FakeRollbackOperations(response_id=OLDER)

    with pytest.raises(CloudflarePolicyError, match="rollback response identity differs"):
        await rollback_production(operations, PROJECT, _id(PREVIOUS))


@pytest.mark.asyncio
async def test_should_fail_closed_when_production_does_not_serve_rollback_target() -> None:
    operations = FakeRollbackOperations(converge=False)

    with pytest.raises(CloudflarePolicyError, match="does not serve the rollback target"):
        await rollback_production(operations, PROJECT, _id(PREVIOUS))

    sleeps = [event for event in operations.events if event.startswith("sleep:")]
    assert sleeps == ["sleep:1", "sleep:2", "sleep:4", "sleep:8"]


@pytest.mark.asyncio
async def test_should_accept_rollback_after_delayed_production_convergence() -> None:
    operations = DelayedOperations()

    evidence = await rollback_production(operations, PROJECT, _id(PREVIOUS))

    assert evidence.live_deployment_id == _id(PREVIOUS)
    assert "sleep:1" in operations.events


@pytest.mark.asyncio
async def test_should_bound_rollback_verification_with_hard_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(asyncio, "timeout", lambda _: ImmediateTimeout())
    operations = FakeRollbackOperations()

    with pytest.raises(CloudflarePolicyError, match="does not serve the rollback target"):
        await rollback_production(operations, PROJECT, _id(PREVIOUS))


@dataclass
class DelayedOperations(FakeRollbackOperations):
    """Production switches only after the first verification read."""

    pending: str | None = None

    async def rollback(self, deployment_id: str) -> str:
        self.events.append(f"rollback:{deployment_id}")
        self.pending = deployment_id.split("-", maxsplit=1)[0]
        return _rollback_response(self.pending)

    async def sleep(self, seconds: int) -> None:
        self.events.append(f"sleep:{seconds}")
        self.live = self.pending


class ImmediateTimeout:
    async def __aenter__(self) -> None:
        raise TimeoutError

    async def __aexit__(self, error_type: object, error: object, traceback: object) -> None:
        return None


@pytest.mark.asyncio
async def test_should_report_live_deployment_as_recorded_rollback_target() -> None:
    operations = FakeRollbackOperations()

    deployment = await live_production_deployment(operations, PROJECT)

    assert (deployment.id, deployment.url) == (_id(LIVE), f"https://{LIVE}.{PROJECT}.pages.dev")
    assert operations.events == ["get-project", "get-deployments"]


@pytest.mark.asyncio
async def test_should_refuse_to_record_target_without_live_deployment() -> None:
    with pytest.raises(CloudflarePolicyError, match="no live production deployment"):
        await live_production_deployment(FakeRollbackOperations(live=None), PROJECT)


@pytest.mark.asyncio
async def test_should_refuse_to_record_live_target_missing_from_history() -> None:
    operations = FakeRollbackOperations(rows=(_row(PREVIOUS),))

    with pytest.raises(CloudflarePolicyError, match="not in recent production history"):
        await live_production_deployment(operations, PROJECT)


@pytest.mark.asyncio
async def test_should_refuse_malformed_live_deployment_reference() -> None:
    operations = MalformedLiveOperations()

    with pytest.raises(CloudflarePolicyError, match="schema mismatch"):
        await live_production_deployment(operations, PROJECT)


@dataclass
class MalformedLiveOperations(FakeRollbackOperations):
    async def get_project(self) -> str:
        payload = json.loads(_project(LIVE))
        payload["result"]["canonical_deployment"] = {"id": 7}
        return json.dumps(payload)


@dataclass(frozen=True)
class RecordingRollbackTransport(CurlRollbackOperations):
    """Curl adapter double that records the exact method, suffix, and body."""

    requests: list[tuple[str, str, str]] = field(default_factory=list)

    async def _request(self, method: str, suffix: str, body: str = "") -> str:
        self.requests.append((method, suffix, body))
        return "response"


def _transport() -> RecordingRollbackTransport:
    secret = cast(dagger.Secret, object())
    return RecordingRollbackTransport(secret, secret, PROJECT)


@pytest.mark.asyncio
async def test_should_build_only_documented_rollback_requests() -> None:
    transport = _transport()

    await transport.get_project()
    await transport.get_deployments()
    await transport.rollback(_id(PREVIOUS))

    assert transport.requests == [
        ("GET", "/pages/projects/edge-reco", ""),
        ("GET", "/pages/projects/edge-reco/deployments?env=production&per_page=10", ""),
        ("POST", f"/pages/projects/edge-reco/deployments/{_id(PREVIOUS)}/rollback", ""),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ("", "../../projects/x", "ABC", "a" * 65, "a/b"))
async def test_should_refuse_malformed_rollback_id_before_transport(value: str) -> None:
    transport = _transport()

    with pytest.raises(CloudflarePolicyError, match="deployment id is malformed"):
        await transport.rollback(value)

    assert transport.requests == []


def test_should_refuse_malformed_project_before_transport() -> None:
    secret = cast(dagger.Secret, object())

    with pytest.raises(CloudflarePolicyError, match="project name is malformed"):
        CurlRollbackOperations(secret, secret, "../edge")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ("400", "403", "409", "500", "503"))
async def test_should_raise_api_error_for_rollback_http_failure(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    container = FailingContainer(f'{status}\n{{"errors":[],"messages":[],"success":false}}')
    monkeypatch.setattr(main_module, "_curl_request_container", lambda *_: container)
    secret = cast(dagger.Secret, object())

    with pytest.raises(CloudflareApiError, match="API request failed"):
        await CurlRollbackOperations(secret, secret, PROJECT).rollback(_id(PREVIOUS))


@dataclass
class FailingContainer:
    output: str

    async def stdout(self) -> str:
        return self.output


def test_should_send_post_with_empty_json_body_and_no_retry() -> None:
    script = main_module._curl_script()

    assert 'if [ "$method" = PATCH ] || [ "$method" = POST ]; then' in script
    assert "retry = 0" in script


def _public_methods(*names: str) -> list[ast.AsyncFunctionDef]:
    tree = ast.parse(Path(main_module.__file__).read_text())
    public = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "CloudflarePages"
    )
    return [
        node
        for node in public.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name in names
    ]


def test_should_expose_rollback_functions_uncached_with_secret_credentials() -> None:
    methods = _public_methods("rollback", "previous_production_deployment")

    assert {method.name for method in methods} == {"rollback", "previous_production_deployment"}
    for method in methods:
        decorator = next(item for item in method.decorator_list if isinstance(item, ast.Call))
        cache = next(item.value for item in decorator.keywords if item.arg == "cache")
        assert isinstance(cache, ast.Constant)
        assert cache.value == "never"
        arguments = {
            argument.arg: ast.unparse(argument.annotation or ast.Constant(None))
            for argument in method.args.args
        }
        credentials = (arguments["cloudflare_api_token"], arguments["cloudflare_account_id"])
        assert credentials == (SECRET_TYPE, SECRET_TYPE)
        assert not set(arguments).intersection({"url", "origin", "command", "cmd", "script"})


@pytest.mark.asyncio
async def test_should_raise_api_error_for_unsuccessful_rollback_body() -> None:
    operations = UnsuccessfulRollbackOperations()

    with pytest.raises(CloudflareApiError, match="API request failed"):
        await rollback_production(operations, PROJECT, _id(PREVIOUS))


@dataclass
class UnsuccessfulRollbackOperations(FakeRollbackOperations):
    async def rollback(self, deployment_id: str) -> str:
        self.events.append(f"rollback:{deployment_id}")
        return json.dumps({"errors": [], "messages": [], "success": False, "result": None})


@pytest.mark.asyncio
async def test_should_accept_rollback_served_after_final_bounded_delay() -> None:
    operations = LateOperations()

    evidence = await rollback_production(operations, PROJECT, _id(PREVIOUS))

    assert evidence.live_deployment_id == _id(PREVIOUS)
    assert [event for event in operations.events if event.startswith("sleep:")][-1] == "sleep:8"


@dataclass
class LateOperations(DelayedOperations):
    """Production switches only during the final bounded delay."""

    async def sleep(self, seconds: int) -> None:
        self.events.append(f"sleep:{seconds}")
        if seconds == 8:
            self.live = self.pending


def _patched_pages(
    monkeypatch: pytest.MonkeyPatch, operations: FakeRollbackOperations
) -> main_module.CloudflarePages:
    monkeypatch.setattr(main_module, "CurlRollbackOperations", lambda *_: operations)
    return main_module.CloudflarePages()


@pytest.mark.asyncio
async def test_should_return_public_rollback_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = cast(dagger.Secret, object())
    pages = _patched_pages(monkeypatch, FakeRollbackOperations())

    evidence = await pages.rollback(secret, secret, PROJECT)

    public = (evidence.project, evidence.from_deployment_id, evidence.to_deployment_id)
    assert public == (PROJECT, _id(LIVE), _id(PREVIOUS))
    live = (evidence.live_deployment_id, evidence.live_deployment_url)
    assert live == (_id(PREVIOUS), f"https://{PREVIOUS}.{PROJECT}.pages.dev")


@pytest.mark.asyncio
async def test_should_return_public_recorded_target(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = cast(dagger.Secret, object())
    operations = FakeRollbackOperations()
    pages = _patched_pages(monkeypatch, operations)

    recorded = await pages.previous_production_deployment(secret, secret, PROJECT)

    assert (recorded.project, recorded.deployment_id) == (PROJECT, _id(LIVE))
    assert recorded.deployment_url == f"https://{LIVE}.{PROJECT}.pages.dev"
    assert _rollbacks(operations) == []


@pytest.mark.asyncio
async def test_should_sleep_between_rollback_verification_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delays: list[float] = []

    async def record(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record)
    secret = cast(dagger.Secret, object())

    await CurlRollbackOperations(secret, secret, PROJECT).sleep(2)

    assert delays == [2]
