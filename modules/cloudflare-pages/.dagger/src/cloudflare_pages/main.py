"""Public Dagger API for exact Cloudflare Pages direct delivery."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Final, Literal
from uuid import uuid4

import dagger
from dagger import dag, field, function, object_type
from pydantic import ValidationError

from .api import (
    CloudflareApiError,
    CloudflarePolicyError,
    deploy_verified_artifact,
    disable_git_payload,
    preflight_provider,
    provider_error_message,
    require_evidence_binding,
    verify_current_deployment,
)
from .models import (
    AttemptIdentity,
    GitHubEvidence,
    PagesTarget,
    ProviderDeploymentEvidence,
)

CURL_IMAGE: Final = (
    "curlimages/curl:8.16.0@sha256:463eaf6072688fe96ac64fa623fe73e1dbe25d8ad6c34404a669ad3ce1f104b6"
)
NODE_IMAGE: Final = (
    "node:24.6.0-bookworm-slim@sha256:"
    "9b741b28148b0195d62fa456ed84dd6c953c1f17a3761f3e6e6797a754d9edff"
)
WRANGLER_VERSION: Final = "4.103.0"
WRANGLER_NODE_RANGE: Final = ">=22.0.0"
WRANGLER_WORKERD_VERSION: Final = "1.20260617.1"
WRANGLER_MINIFLARE_VERSION: Final = "4.20260617.1"
API_RESPONSE_PATH: Final = "/work/cloudflare-response.json"
CURL_CONFIG_PATH: Final = "/work/cloudflare-curl.cfg"
REQUEST_PATH: Final = "/work/cloudflare-request.json"
CURL_DEADLINE_SECONDS: Final = 20
WRANGLER_PREFLIGHT_SECONDS: Final = 60
WRANGLER_UPLOAD_SECONDS: Final = 300
HTTP_STATUS_LENGTH: Final = 3
WRANGLER_REQUIRED_FLAGS: Final = (
    "--project-name",
    "--branch",
    "--commit-hash",
    "--commit-dirty",
    "--no-bundle",
    "--skip-caching",
)


@dataclass(frozen=True)
class ProviderContext:
    """Validated immutable target, foundation evidence, and caller attempt."""

    target: PagesTarget
    github: GitHubEvidence
    attempt: AttemptIdentity


@dataclass(frozen=True)
class TargetInputs:
    """Unparsed scalar target values from the explicit Dagger boundary."""

    repository: str
    project: str
    branch: str
    live_domain: str
    domains: tuple[str, ...]


@object_type
class DeploymentEvidence:
    """Non-secret Cloudflare deployment proof safe for downstream policy."""

    provider: str = field()
    deployment_id: str = field()
    deployment_url: str = field()
    project_id: str = field()
    project: str = field()
    repository: str = field()
    branch: str = field()
    source_sha: str = field()
    workflow_run_id: str = field()
    run_attempt: int = field()


@dataclass(frozen=True)
class CurlPagesOperations:
    """Pinned, secret-safe Dagger adapters for one Pages target."""

    api_token: dagger.Secret
    account_id: dagger.Secret
    target: PagesTarget
    api_service: dagger.Service | None = None
    ca_certificate: dagger.File | None = None

    async def get_project(self) -> str:
        return await self._request("GET", self._project_suffix())

    async def get_deployments(self) -> str:
        suffix = f"{self._project_suffix()}/deployments?env=production&per_page=10"
        return await self._request("GET", suffix)

    async def disable_git(self) -> str:
        body = json.dumps(disable_git_payload(self.target), separators=(",", ":"), sort_keys=True)
        return await self._request("PATCH", self._project_suffix(), body)

    async def wrangler_preflight(self) -> None:
        container = _wrangler_base().with_exec(["wrangler", "pages", "deploy", "--help"])
        try:
            output = await asyncio.wait_for(container.stdout(), WRANGLER_PREFLIGHT_SECONDS)
        except (TimeoutError, dagger.QueryError):
            raise CloudflarePolicyError("Pinned Wrangler preflight failed") from None
        _require_wrangler_help(output)

    async def upload(self, artifact: dagger.Directory, source_sha: str) -> None:
        try:
            await asyncio.wait_for(
                self._upload_container(artifact, source_sha).sync(), WRANGLER_UPLOAD_SECONDS
            )
        except (TimeoutError, dagger.QueryError):
            raise CloudflarePolicyError("Cloudflare Pages direct upload failed") from None

    async def sleep(self, seconds: int) -> None:
        await asyncio.sleep(seconds)

    async def _request(self, method: Literal["GET", "PATCH"], suffix: str, body: str = "") -> str:
        request = self._request_container(method, suffix, body)
        try:
            result = await asyncio.wait_for(_request_result(request), CURL_DEADLINE_SECONDS)
        except (TimeoutError, dagger.QueryError):
            raise CloudflareApiError("Cloudflare network request failed") from None
        return _require_http_success(*result)

    def _request_container(
        self, method: Literal["GET", "PATCH"], suffix: str, body: str
    ) -> dagger.Container:
        base = dag.container(platform=dagger.Platform("linux/amd64")).from_(CURL_IMAGE)
        base = base.with_entrypoint([]).with_user("0").with_workdir("/work")
        base = base.with_mounted_secret("/run/secrets/token", self.api_token)
        base = base.with_mounted_secret("/run/secrets/account", self.account_id)
        if self.api_service is not None:
            base = base.with_service_binding("api.cloudflare.com", self.api_service)
        if self.ca_certificate is not None:
            base = base.with_file("/work/mock-ca.pem", self.ca_certificate)
        command = ["/bin/sh", "-euc", _curl_script(), "--", method, suffix]
        return _uncached(base).with_exec(command, stdin=body)

    def _upload_container(self, artifact: dagger.Directory, source_sha: str) -> dagger.Container:
        base = _wrangler_base().with_mounted_directory("/artifact", artifact, read_only=True)
        base = base.with_secret_variable("CLOUDFLARE_API_TOKEN", self.api_token)
        base = base.with_secret_variable("CLOUDFLARE_ACCOUNT_ID", self.account_id)
        return _uncached(base).with_exec(wrangler_deploy_args(self.target, source_sha))

    def _project_suffix(self) -> str:
        return f"/pages/projects/{self.target.project}"


@object_type
class CloudflarePages:
    """Deploy only foundation-verified artifacts to one bound Pages target."""

    @function(cache="never")  # type: ignore[call-overload,untyped-decorator]  # SDK stub gap
    async def preflight(
        self,
        envelope: dagger.Directory,
        github_evidence: str,
        cloudflare_api_token: dagger.Secret,
        cloudflare_account_id: dagger.Secret,
        workflow_run_id: str,
        run_attempt: int,
        repository: str,
        project: str,
        production_branch: str,
        live_domain: str,
        domains: list[str],
        consumer_identity: str,
        producing_identity: str,
        allowed_roots: list[str],
    ) -> str:
        """Verify the envelope and run read-only project, deployment, and CLI checks."""
        inputs = TargetInputs(repository, project, production_branch, live_domain, tuple(domains))
        context = await _provider_context(github_evidence, workflow_run_id, run_attempt, inputs)
        await _verify_envelope(envelope, consumer_identity, producing_identity, allowed_roots)
        operations = CurlPagesOperations(
            cloudflare_api_token, cloudflare_account_id, context.target
        )
        await preflight_provider(operations, context.target)
        return "Cloudflare Pages preflight passed"

    @function(cache="never")  # type: ignore[call-overload,untyped-decorator]  # SDK stub gap
    async def deploy(
        self,
        envelope: dagger.Directory,
        github_evidence: str,
        cloudflare_api_token: dagger.Secret,
        cloudflare_account_id: dagger.Secret,
        workflow_run_id: str,
        run_attempt: int,
        repository: str,
        project: str,
        production_branch: str,
        live_domain: str,
        domains: list[str],
        consumer_identity: str,
        producing_identity: str,
        allowed_roots: list[str],
    ) -> DeploymentEvidence:
        """Direct-upload one verified artifact and return exact deployment evidence."""
        inputs = TargetInputs(repository, project, production_branch, live_domain, tuple(domains))
        context = await _provider_context(github_evidence, workflow_run_id, run_attempt, inputs)
        artifact = await _verify_envelope(
            envelope, consumer_identity, producing_identity, allowed_roots
        )
        operations = CurlPagesOperations(
            cloudflare_api_token, cloudflare_account_id, context.target
        )
        evidence = await deploy_verified_artifact(
            operations, artifact, context.target, context.github, context.attempt
        )
        return _public_evidence(evidence)

    @function(cache="never")  # type: ignore[call-overload,untyped-decorator]  # SDK stub gap
    async def verify(
        self,
        github_evidence: str,
        cloudflare_api_token: dagger.Secret,
        cloudflare_account_id: dagger.Secret,
        workflow_run_id: str,
        run_attempt: int,
        repository: str,
        project: str,
        production_branch: str,
        live_domain: str,
        domains: list[str],
    ) -> DeploymentEvidence:
        """Converge read-only production evidence for an exact source attempt."""
        inputs = TargetInputs(repository, project, production_branch, live_domain, tuple(domains))
        context = await _provider_context(github_evidence, workflow_run_id, run_attempt, inputs)
        operations = CurlPagesOperations(
            cloudflare_api_token, cloudflare_account_id, context.target
        )
        evidence = await verify_current_deployment(
            operations, context.target, context.github, context.attempt
        )
        return _public_evidence(evidence)


async def _provider_context(
    value: str,
    workflow_run_id: str,
    run_attempt: int,
    inputs: TargetInputs,
) -> ProviderContext:
    target = PagesTarget(
        inputs.repository, inputs.project, inputs.branch, inputs.live_domain, inputs.domains
    )
    attempt = AttemptIdentity(workflow_run_id, run_attempt)
    try:
        github = GitHubEvidence.model_validate_json(value)
    except ValidationError:
        raise CloudflarePolicyError("Foundation GitHub evidence schema differs") from None
    require_evidence_binding(target, github, attempt)
    return ProviderContext(target, github, attempt)


async def _verify_envelope(
    envelope: dagger.Directory,
    consumer_identity: str,
    producing_identity: str,
    allowed_roots: list[str],
) -> dagger.Directory:
    artifact = dag.foundation().verify_envelope(
        envelope=envelope,
        consumer_identity=consumer_identity,
        producing_identity=producing_identity,
        allowed_roots=allowed_roots,
    )
    await artifact.digest()
    return artifact


def wrangler_deploy_args(target: PagesTarget, source_sha: str) -> list[str]:
    """Return the fixed direct-upload command over the verified mount only."""
    return [
        "wrangler",
        "pages",
        "deploy",
        "/artifact",
        f"--project-name={target.project}",
        f"--branch={target.branch}",
        f"--commit-hash={source_sha}",
        "--commit-dirty=false",
        "--no-bundle",
        "--skip-caching",
    ]


def _wrangler_base() -> dagger.Container:
    install = [
        "npm",
        "install",
        "--global",
        "--omit=dev",
        "--no-audit",
        "--no-fund",
        "--loglevel=error",
        f"wrangler@{WRANGLER_VERSION}",
    ]
    base = dag.container(platform=dagger.Platform("linux/amd64")).from_(NODE_IMAGE)
    return base.with_env_variable("WRANGLER_SEND_METRICS", "false").with_exec(install)


def _uncached(container: dagger.Container) -> dagger.Container:
    return container.with_env_variable("DAGGER_CLOUDFLARE_REQUEST_NONCE", uuid4().hex)


async def _request_result(request: dagger.Container) -> tuple[str, str]:
    status, response = await asyncio.gather(
        request.stdout(), request.file(API_RESPONSE_PATH).contents()
    )
    return status.strip(), response


def _require_http_success(status: str, response: str) -> str:
    if len(status) == HTTP_STATUS_LENGTH and status.startswith("2"):
        return response
    raise CloudflareApiError(provider_error_message(response))


def _require_wrangler_help(output: str) -> None:
    if not all(flag in output for flag in WRANGLER_REQUIRED_FLAGS):
        raise CloudflarePolicyError("Pinned Wrangler Pages flags differ")


def _public_evidence(source: ProviderDeploymentEvidence) -> DeploymentEvidence:
    evidence = DeploymentEvidence.__new__(DeploymentEvidence)
    evidence.provider = "cloudflare-pages"
    evidence.deployment_id = source.deployment_id
    evidence.deployment_url = source.deployment_url
    evidence.project_id = source.project_id
    evidence.project = source.project
    evidence.repository = source.repository
    evidence.branch = source.branch
    evidence.source_sha = source.source_sha
    evidence.workflow_run_id = source.attempt_identity.workflow_run_id
    evidence.run_attempt = source.attempt_identity.run_attempt
    return evidence


def _curl_script() -> str:
    return f"""
method="$1"
suffix="$2"
token="$(tr -d '\\r\\n' < /run/secrets/token)"
account="$(tr -d '\\r\\n' < /run/secrets/account)"
case "$token" in ''|*[!A-Za-z0-9._-]*) exit 64;; esac
case "$account" in *[!A-Fa-f0-9]*) exit 64;; esac
[ "${{#account}}" -eq 32 ] || exit 64
umask 077
cat > {REQUEST_PATH}
{{
  printf 'url = "https://api.cloudflare.com/client/v4/accounts/%s%s"\\n' "$account" "$suffix"
  printf 'header = "Authorization: Bearer %s"\\n' "$token"
  printf 'request = "%s"\\n' "$method"
  printf 'output = "{API_RESPONSE_PATH}"\\n'
  printf 'write-out = "%%{{http_code}}"\\n'
  printf 'connect-timeout = 3\\nmax-time = 8\\nretry = 1\\n'
  printf 'retry-all-errors\\nretry-delay = 1\\nsilent\\n'
  if [ -f /work/mock-ca.pem ]; then printf 'cacert = "/work/mock-ca.pem"\\n'; fi
  if [ "$method" = PATCH ]; then
    printf 'header = "Content-Type: application/json"\\n'
    printf 'data-binary = "@{REQUEST_PATH}"\\n'
  fi
}} > {CURL_CONFIG_PATH}
exec curl --config {CURL_CONFIG_PATH}
"""
