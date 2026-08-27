# Dagger Lego Foundation and EdgeReco Canary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first exact-SHA reusable Dagger foundation in `hseshadr/ci`, consume it from EdgeReco, and prove the composition through hosted CI, production deployment, live verification, and rollback before touching another consumer.

**Architecture:** `hseshadr/ci` owns small reusable Dagger modules for typed source identity, repository security, artifact envelopes, GitHub evidence, and Cloudflare Pages delivery. EdgeReco keeps a thin local adapter for product build, parity, storefront, signed-bundle, and zero-egress behavior. GitHub Actions provides event ingress and secret storage; all computation and release decisions are Dagger functions.

**Tech Stack:** Dagger 0.21.8, Python 3.13, TypeScript composition fixture, Pydantic 2, pytest/pytest-cov, Poe, Ruff, strict mypy, Xenon Grade A, GitHub Actions, Cloudflare Pages, Wrangler 4.103.0.

**Spec:** `docs/superpowers/specs/2026-08-27-dagger-lego-architecture-design.md`

## Global Constraints

- Work in one project at a time. Complete the central module and EdgeReco canary before planning another consumer.
- Use red → green → refactor for every behavior change.
- Run Python validation only through Poe tasks.
- New and touched Python functions are at most 15 lines and Xenon Grade A.
- Core logic retains at least 90% branch coverage.
- Dagger engine remains exactly `v0.21.8` during this plan.
- Every remote Dagger dependency is pinned to a literal 40-character commit SHA.
- Every GitHub workflow action is pinned to a full commit SHA.
- Repository-authored workflow computation is Dagger-owned. Only approved artifact transport and official PyPI upload gateways may follow a Dagger decision.
- GitHub secret values are never read back, printed, committed, placed in CLI scalar values, or copied into artifacts/caches.
- Do not merge Dependabot PRs.
- Do not publish packages, create release tags, or mutate registries.
- Do not auto-merge module pin PRs.
- Preserve unrelated worktrees and changes.

## File Structure

### Central repository: `hseshadr/ci`

- `modules/portfolio-foundation/dagger.json` — reusable unprivileged module definition.
- `modules/portfolio-foundation/.dagger/pyproject.toml` — module dependencies and strict quality tasks.
- `modules/portfolio-foundation/.dagger/src/portfolio_foundation/main.py` — public Dagger API only.
- `modules/portfolio-foundation/.dagger/src/portfolio_foundation/identity.py` — repository and full-SHA value objects.
- `modules/portfolio-foundation/.dagger/src/portfolio_foundation/source.py` — exact source/history binding.
- `modules/portfolio-foundation/.dagger/src/portfolio_foundation/guard.py` — workflow and secret-scan primitives.
- `modules/portfolio-foundation/.dagger/src/portfolio_foundation/artifact.py` — canonical artifact envelope.
- `modules/portfolio-foundation/.dagger/src/portfolio_foundation/github.py` — exact-green GitHub evidence.
- `modules/portfolio-foundation/.dagger/tests/` — focused unit and Dagger adapter tests.
- `modules/cloudflare-pages/dagger.json` — privileged provider module definition.
- `modules/cloudflare-pages/.dagger/src/cloudflare_pages/main.py` — public Pages API.
- `modules/cloudflare-pages/.dagger/src/cloudflare_pages/models.py` — validated Pages target/evidence.
- `modules/cloudflare-pages/.dagger/src/cloudflare_pages/api.py` — raw documented API client and polling.
- `modules/cloudflare-pages/.dagger/tests/` — provider boundary and convergence tests.
- `tests/dagger/python_consumer/` — generated Python composition fixture.
- `tests/dagger/typescript_consumer/` — generated TypeScript composition fixture.
- `.dagger/src/ci/fleet_policy.py` — dependency/environment conformance rules.
- `.dagger/src/ci/github_fleet.py` — authoritative `dagger.json` and workflow source reader.
- `.dagger/tests/test_fleet_policy.py` — fleet policy behavior.
- `.dagger/tests/test_github_fleet.py` — GitHub payload and pagination behavior.
- `.github/workflows/module-canary.yml` — thin scheduled/manual Dagger fixture ingress.

### EdgeReco repository

- `dagger.json` — exact-SHA central module dependencies.
- `.dagger/src/edge_reco/main.py` — product adapter and composition.
- `.dagger/src/edge_reco/targets.py` — validated repository/Pages/live target constants.
- `.dagger/tests/test_public_contracts.py` — public API, target, source, and shared-module contracts.
- `.github/workflows/dagger.yml` — PR/push Dagger ingress.
- `.github/workflows/security-audit.yml` — scheduled/manual Dagger security ingress.
- `.github/workflows/deploy.yml` — exact-main, production-environment Dagger deployment ingress.
- `backend/tests/unit/test_workflow_security.py` — workflow and privilege boundaries.
- `frontend/app/scripts/deployment-contract.test.mjs` — provider/live deployment behavior.

---

### Task 1: Scaffold the reusable unprivileged module

**Files:**
- Create: `modules/portfolio-foundation/dagger.json`
- Create: `modules/portfolio-foundation/.dagger/pyproject.toml`
- Create: `modules/portfolio-foundation/.dagger/src/portfolio_foundation/__init__.py`
- Create: `modules/portfolio-foundation/.dagger/src/portfolio_foundation/main.py`
- Create: `modules/portfolio-foundation/.dagger/tests/test_public_schema.py`
- Create: `modules/portfolio-foundation/.dagger/tests/test_quality_contract.py`

**Interfaces:**
- Produces: Dagger module `portfolio-foundation` with public functions `source`, `guard`, `envelope`, and `green-main`.
- Consumes: caller-provided typed `Directory`, `Secret`, and scalar identity values.

- [ ] **Step 1: Write the failing schema test**

```python
from pathlib import Path

import pytest


MODULE = Path(__file__).parents[2]


def test_should_expose_stable_public_functions() -> None:
    schema = (MODULE / "dagger.json").read_text()
    for name in ("portfolio-foundation", "v0.21.8"):
        assert name in schema


def test_should_reject_public_arbitrary_command_escape_hatch() -> None:
    source = (MODULE / ".dagger/src/portfolio_foundation/main.py").read_text()
    assert "command: str" not in source
    assert "script: str" not in source
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
cd modules/portfolio-foundation
uv run --project .dagger pytest .dagger/tests/test_public_schema.py -q
```

Expected: failure because `dagger.json` and the module source do not exist.

- [ ] **Step 3: Initialize the module and strict Poe gate**

Run:

```bash
mkdir -p modules/portfolio-foundation
cd modules/portfolio-foundation
dagger init --name portfolio-foundation --sdk python --source .dagger
```

Set `engineVersion` to `v0.21.8`. Configure `.dagger/pyproject.toml` with the same Ruff, strict mypy, Xenon A, pytest coverage, and pip-audit contracts as the central root module. The public `main.py` initially contains typed methods that raise `NotImplementedError` and no arbitrary command parameters.

- [ ] **Step 4: Add the quality-structure regression**

```python
import ast
from pathlib import Path


def test_should_keep_every_function_at_most_fifteen_lines() -> None:
    root = Path(__file__).parents[1] / "src"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                end = node.end_lineno or node.lineno
                assert end - node.lineno + 1 <= 15, f"{path}:{node.lineno}"
```

- [ ] **Step 5: Run the module gate and verify GREEN**

Run:

```bash
cd modules/portfolio-foundation
uv sync --directory .dagger --frozen --all-groups
uv run --directory .dagger poe gate
uv run --directory .dagger poe audit
dagger functions
```

Expected: all tests pass, coverage is at least 90%, Xenon is Grade A, audit reports no known vulnerabilities, and the four intended public functions appear.

- [ ] **Step 6: Commit**

```bash
git add modules/portfolio-foundation
git commit -m "feat(ci): scaffold portfolio Dagger foundation"
```

---

### Task 2: Implement typed repository and source identity

**Files:**
- Create: `modules/portfolio-foundation/.dagger/src/portfolio_foundation/identity.py`
- Create: `modules/portfolio-foundation/.dagger/src/portfolio_foundation/source.py`
- Create: `modules/portfolio-foundation/.dagger/tests/test_identity.py`
- Create: `modules/portfolio-foundation/.dagger/tests/test_source.py`
- Modify: `modules/portfolio-foundation/.dagger/src/portfolio_foundation/main.py`

**Interfaces:**
- Produces: `FullSha`, `RepositoryRef`, `CommitIdentity`, and `SourceBinding`.
- Produces: `PortfolioFoundation.source(source: Directory, repository: str, commit_sha: str) -> Directory`.
- Consumes: exact public Git source at the claimed SHA and caller workspace bytes.

- [ ] **Step 1: Write failing value-object tests**

```python
import pytest

from portfolio_foundation.identity import FullSha, RepositoryRef


def test_should_accept_lowercase_full_sha() -> None:
    sha = "a" * 40
    assert FullSha(sha).value == sha


@pytest.mark.parametrize("value", ["abc1234", "A" * 40, "g" * 40, ""])
def test_should_reject_noncanonical_sha(value: str) -> None:
    with pytest.raises(ValueError, match="lowercase 40-character"):
        FullSha(value)


@pytest.mark.parametrize("value", ["edge-reco", "owner/repo/extra", "owner repo"])
def test_should_reject_invalid_repository(value: str) -> None:
    with pytest.raises(ValueError, match="owner/repository"):
        RepositoryRef.parse(value)
```

- [ ] **Step 2: Run identity tests and verify RED**

Run:

```bash
uv run --directory modules/portfolio-foundation/.dagger pytest \
  modules/portfolio-foundation/.dagger/tests/test_identity.py -q
```

Expected: import failure for `portfolio_foundation.identity`.

- [ ] **Step 3: Implement immutable identity records**

```python
from __future__ import annotations

import re
from dataclasses import dataclass


SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class FullSha:
    value: str

    def __post_init__(self) -> None:
        if SHA_PATTERN.fullmatch(self.value) is None:
            raise ValueError("SHA must be a lowercase 40-character hexadecimal value")


@dataclass(frozen=True)
class RepositoryRef:
    owner: str
    name: str

    @classmethod
    def parse(cls, value: str) -> "RepositoryRef":
        if REPOSITORY_PATTERN.fullmatch(value) is None:
            raise ValueError("repository must be owner/repository")
        owner, name = value.split("/", maxsplit=1)
        return cls(owner, name)
```

- [ ] **Step 4: Write failing exact-source tests**

Test these behaviors with a fake inventory adapter:

```python
def test_should_reject_workspace_when_inventory_differs_from_exact_commit() -> None:
    expected = ("dagger.json:abc", "src/app.ts:def")
    actual = ("dagger.json:abc", "src/app.ts:changed")
    with pytest.raises(SourceMismatch, match="src/app.ts"):
        require_same_inventory(expected, actual)


def test_should_keep_source_and_history_as_distinct_inputs() -> None:
    binding = bind_source(fake_source, fake_history, FullSha("a" * 40), manifest)
    assert binding.source is fake_source
    assert binding.history is fake_history
```

- [ ] **Step 5: Implement canonical inventory and source binding**

Create a frozen `SourceBinding` with `source`, `history`, `identity`, and `manifest_sha256`. Build manifests using sorted relative paths plus file SHA-256 values. Reject missing files, unexpected files, symlinks, and differing hashes in hosted mode.

The Dagger adapter must fetch history separately:

```python
history = dag.git(f"https://github.com/{repository}.git").commit(commit.value).tree(
    depth=0,
    include_tags=True,
)
```

- [ ] **Step 6: Run focused and full module gates**

Run:

```bash
uv run --directory modules/portfolio-foundation/.dagger pytest \
  modules/portfolio-foundation/.dagger/tests/test_identity.py \
  modules/portfolio-foundation/.dagger/tests/test_source.py -q
uv run --directory modules/portfolio-foundation/.dagger poe gate
```

Expected: all tests pass and coverage remains at least 90%.

- [ ] **Step 7: Commit**

```bash
git add modules/portfolio-foundation
git commit -m "feat(ci): bind workspaces to exact repository history"
```

---

### Task 3: Extract the repository guard lego

**Files:**
- Create: `modules/portfolio-foundation/.dagger/src/portfolio_foundation/guard.py`
- Create: `modules/portfolio-foundation/.dagger/tests/test_guard.py`
- Modify: `modules/portfolio-foundation/.dagger/src/portfolio_foundation/main.py`
- Modify: `.dagger/src/ci/main.py`
- Modify: `.dagger/tests/test_dagger_contract.py`

**Interfaces:**
- Produces: `PortfolioFoundation.guard(source: Directory, repository: str, commit_sha: str) -> Container`.
- Consumes: `SourceBinding` from Task 2.
- Runs: actionlint on `.yml` and `.yaml`, runtime-canary snapshot Gitleaks, and full canonical-history Gitleaks.

- [ ] **Step 1: Write failing non-vacuity tests**

```python
def test_should_scan_both_workflow_extensions() -> None:
    command = actionlint_command()
    assert "*.yml" in command
    assert "*.yaml" in command


def test_should_require_runtime_canary_detection_before_real_scan() -> None:
    command = secret_scan_command()
    assert command.index("canary") < command.index("/snapshot")
    assert "--redact" in command


def test_should_fail_when_canonical_history_is_missing() -> None:
    with pytest.raises(MissingHistory):
        require_git_history(empty_directory)
```

- [ ] **Step 2: Run guard tests and verify RED**

Run:

```bash
uv run --directory modules/portfolio-foundation/.dagger pytest \
  modules/portfolio-foundation/.dagger/tests/test_guard.py -q
```

Expected: import or assertion failure because the shared guard does not exist.

- [ ] **Step 3: Move only common mechanics**

Extract digest-pinned actionlint and Gitleaks containers from the proven central and EdgeReco implementations. Generate the detector-shaped canary at runtime in an ephemeral Git repository. Require the canary scan to return the detector's finding exit code before scanning real source and history.

Do not move EdgeReco CodeQL, parity, model, browser, or build commands.

- [ ] **Step 4: Make central CI consume the guard locally**

Install the module by local path while it is under development:

```bash
CENTRAL_ROOT="$(git rev-parse --show-toplevel)"
cd "$CENTRAL_ROOT"
dagger install ./modules/portfolio-foundation --name foundation
dagger develop
```

Replace central `_workflow_security` and `_secret_scan` construction with the generated dependency client. Keep public root functions `ci` and `security` unchanged.

- [ ] **Step 5: Verify shared and central gates**

Run:

```bash
uv run --directory modules/portfolio-foundation/.dagger poe gate
uv run --directory .dagger poe gate
GITHUB_TOKEN="$(gh auth token)" dagger call ci \
  --github-token=env:GITHUB_TOKEN \
  --commit-sha="$(git rev-parse HEAD)"
```

Expected: both Poe gates pass; the Dagger log proves nonempty snapshot scanning and complete current history with no leaks.

- [ ] **Step 6: Commit**

```bash
git add dagger.json .dagger modules/portfolio-foundation
git commit -m "refactor(ci): share repository security guard"
```

---

### Task 4: Implement deterministic artifact envelopes

**Files:**
- Create: `modules/portfolio-foundation/.dagger/src/portfolio_foundation/artifact.py`
- Create: `modules/portfolio-foundation/.dagger/tests/test_artifact.py`
- Modify: `modules/portfolio-foundation/.dagger/src/portfolio_foundation/main.py`

**Interfaces:**
- Produces: `ArtifactManifest`, `ArtifactEvidence`, and `PortfolioFoundation.envelope(...) -> Directory`.
- Consumes: product artifact `Directory`, `CommitIdentity`, shared-module SHA, and explicit allowed roots.

- [ ] **Step 1: Write failing artifact-boundary tests**

```python
def test_should_generate_stable_manifest_independent_of_input_order() -> None:
    first = build_manifest((file_b, file_a), identity)
    second = build_manifest((file_a, file_b), identity)
    assert first.to_json() == second.to_json()


def test_should_reject_unexpected_file() -> None:
    with pytest.raises(UnexpectedArtifactPath, match="debug.log"):
        validate_paths(("dist/index.html", "debug.log"), ("dist",))


def test_should_bind_consumer_and_module_sha_separately() -> None:
    manifest = artifact_manifest(consumer_sha="a" * 40, module_sha="b" * 40)
    assert manifest.consumer_sha != manifest.module_sha
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run --directory modules/portfolio-foundation/.dagger pytest \
  modules/portfolio-foundation/.dagger/tests/test_artifact.py -q
```

Expected: import failure for `portfolio_foundation.artifact`.

- [ ] **Step 3: Implement canonical manifest and envelope**

The envelope contains exactly:

```text
artifact/**
evidence/artifact-manifest.json
evidence/SHA256SUMS
```

`artifact-manifest.json` includes schema version `1`, consumer SHA, shared-module SHA, engine version, repository, producing run ID, and a sorted file inventory. Reject duplicate paths, path traversal, unsupported symlinks, missing artifacts, and unexpected evidence files.

- [ ] **Step 4: Add a Dagger real-directory test**

Create a test `Directory` with two files, call `envelope`, export it to a temporary path, and independently recompute every SHA-256. Then add a third unexpected file and require failure.

- [ ] **Step 5: Run full module gate**

```bash
uv run --directory modules/portfolio-foundation/.dagger poe gate
dagger -m modules/portfolio-foundation call envelope --help
```

Expected: gate passes and schema exposes typed artifact, identity, and allowed-root inputs without arbitrary commands.

- [ ] **Step 6: Commit**

```bash
git add modules/portfolio-foundation
git commit -m "feat(ci): add deterministic artifact envelope"
```

---

### Task 5: Implement exact-green GitHub evidence

**Files:**
- Create: `modules/portfolio-foundation/.dagger/src/portfolio_foundation/github.py`
- Create: `modules/portfolio-foundation/.dagger/tests/test_github.py`
- Modify: `modules/portfolio-foundation/.dagger/src/portfolio_foundation/main.py`

**Interfaces:**
- Produces: `CheckEvidence` and `resolve_green_main(...) -> CheckEvidence`.
- Consumes: typed `Secret`, `RepositoryRef`, expected workflow/check/app identity.
- Requires: status `completed`, conclusion `success`, full main SHA, GitHub Actions app ID `15368`.

- [ ] **Step 1: Write failing GitHub-state tests**

```python
@pytest.mark.parametrize("conclusion", [None, "", "failure", "cancelled", "skipped"])
def test_should_not_accept_non_green_check(conclusion: str | None) -> None:
    evidence = check_payload(status="in_progress", conclusion=conclusion)
    assert select_green_dagger((evidence,)) is None


def test_should_parse_nullable_conclusion_without_boundary_failure() -> None:
    payload = CheckRunsPayload.model_validate(check_runs_json(conclusion=None))
    assert payload.check_runs[0].conclusion is None


def test_should_reject_green_check_from_wrong_app() -> None:
    evidence = check_payload(status="completed", conclusion="success", app_id=85455)
    assert select_green_dagger((evidence,)) is None
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run --directory modules/portfolio-foundation/.dagger pytest \
  modules/portfolio-foundation/.dagger/tests/test_github.py -q
```

Expected: import failure because the shared GitHub evidence module is absent.

- [ ] **Step 3: Implement strict Pydantic boundaries and pagination**

Model external GitHub payloads with `ConfigDict(extra="forbid", frozen=True)`. Permit nullable conclusions at the I/O boundary. Convert only completed successful evidence from the expected workflow, check name, SHA, repository, and app ID into `CheckEvidence`.

Follow `Link` pagination until exhausted and reject duplicate green Dagger checks.

- [ ] **Step 4: Add a live read-only test**

Use the current central exact main and `gh auth token` through a typed Dagger secret. Assert that the resolved main SHA equals `git ls-remote origin refs/heads/main` and the accepted check app is `15368`. Do not log the token or raw authorization header.

- [ ] **Step 5: Run gates**

```bash
uv run --directory modules/portfolio-foundation/.dagger poe gate
GITHUB_TOKEN="$(gh auth token)" dagger -m modules/portfolio-foundation call green-main \
  --github-token=env:GITHUB_TOKEN \
  --repository=hseshadr/ci
```

Expected: tests pass and the command returns only typed non-secret evidence for the exact current main.

- [ ] **Step 6: Commit**

```bash
git add modules/portfolio-foundation
git commit -m "feat(ci): add exact-green GitHub evidence"
```

---

### Task 6: Build the Cloudflare Pages provider lego

**Files:**
- Create: `modules/cloudflare-pages/dagger.json`
- Create: `modules/cloudflare-pages/.dagger/pyproject.toml`
- Create: `modules/cloudflare-pages/.dagger/src/cloudflare_pages/__init__.py`
- Create: `modules/cloudflare-pages/.dagger/src/cloudflare_pages/main.py`
- Create: `modules/cloudflare-pages/.dagger/src/cloudflare_pages/models.py`
- Create: `modules/cloudflare-pages/.dagger/src/cloudflare_pages/api.py`
- Create: `modules/cloudflare-pages/.dagger/tests/test_models.py`
- Create: `modules/cloudflare-pages/.dagger/tests/test_api.py`
- Create: `modules/cloudflare-pages/.dagger/tests/test_deploy_contract.py`

**Interfaces:**
- Consumes: foundation `ArtifactEvidence`, typed Cloudflare secrets, `PagesTarget`, and exact GitHub evidence.
- Produces: `DeploymentEvidence` with provider deployment ID/URL and exact source identity.
- Public functions: `preflight`, `deploy`, and `verify`.

- [ ] **Step 1: Initialize the provider module and local dependency**

```bash
mkdir -p modules/cloudflare-pages
cd modules/cloudflare-pages
dagger init --name cloudflare-pages --sdk python --source .dagger
dagger install ../portfolio-foundation --name foundation
dagger develop
```

Set engine `v0.21.8` and copy the strict quality configuration from the foundation module.

- [ ] **Step 2: Write failing target-binding tests**

```python
def test_should_bind_repository_project_branch_and_domain() -> None:
    target = PagesTarget(
        repository="hseshadr/edge-reco",
        project="edge-reco",
        branch="main",
        live_domain="edge-reco.com",
    )
    assert target.repository.name == target.project


def test_should_reject_mixed_foreign_target() -> None:
    with pytest.raises(ValueError, match="target binding"):
        PagesTarget(
            repository="hseshadr/edge-reco",
            project="almamesh",
            branch="main",
            live_domain="edge-reco.com",
        )
```

- [ ] **Step 3: Run tests and verify RED**

```bash
uv run --directory modules/cloudflare-pages/.dagger pytest \
  modules/cloudflare-pages/.dagger/tests/test_models.py -q
```

Expected: import failure for `cloudflare_pages.models`.

- [ ] **Step 4: Write failing raw-API and convergence tests**

Use a fake HTTP service and require these exact behaviors:

```python
def test_should_query_documented_deployments_endpoint() -> None:
    assert deployment_path(target) == (
        "/accounts/account/pages/projects/edge-reco/deployments"
        "?env=production&per_page=10"
    )


def test_should_wait_for_exact_full_sha_and_successful_deploy_stage() -> None:
    responses = (pending_payload(), pending_payload(), success_payload("a" * 40))
    evidence = poll_deployment(responses, expected_sha="a" * 40, delays=(1, 2, 4))
    assert evidence.source_sha == "a" * 40


def test_should_sanitize_provider_error() -> None:
    error = sanitize_error(provider_error(code=10000, message="auth failed", request="secret"))
    assert error == "Cloudflare 10000: auth failed"
```

- [ ] **Step 5: Implement the provider sequence**

The public `deploy` function executes in this order:

1. Validate target and exact source/check evidence.
2. Revalidate the artifact envelope.
3. GET project and deployments API using secret stdin configuration.
4. PATCH production and preview Git deployment flags off.
5. Run pinned Wrangler `4.103.0` preflight.
6. Upload the exact mounted artifact directory.
7. Poll the raw API with bounded 1/2/4/8-second delays and a 60-second deadline.
8. Return evidence only for production, exact full SHA, `deploy`, `success`.

Every effectful function is explicitly non-cacheable. No source, credential, artifact, or provider response is stored in a cache volume.

- [ ] **Step 6: Run provider gate and fake-service integration**

```bash
uv run --directory modules/cloudflare-pages/.dagger poe gate
dagger -m modules/cloudflare-pages call preflight --help
dagger -m modules/cloudflare-pages call deploy --help
```

Expected: tests cover wrong endpoint, wrong page size, malformed schema, wrong SHA, pending timeout, sanitized errors, and exact success.

- [ ] **Step 7: Commit**

```bash
git add modules/cloudflare-pages
git commit -m "feat(ci): add reusable Cloudflare Pages delivery"
```

---

### Task 7: Add Python, TypeScript, and cold-cache composition fixtures

**Files:**
- Create: `tests/dagger/python_consumer/dagger.json`
- Create: `tests/dagger/python_consumer/.dagger/src/python_consumer/main.py`
- Create: `tests/dagger/typescript_consumer/dagger.json`
- Create: `tests/dagger/typescript_consumer/src/index.ts`
- Create: `.dagger/tests/test_module_fixtures.py`
- Create: `.github/workflows/module-canary.yml`
- Modify: `.dagger/src/ci/main.py`
- Modify: `.dagger/tests/test_central_workflows.py`

**Interfaces:**
- Produces: root function `module-fixtures` that executes both generated clients against exact local modules.
- Produces: scheduled/manual workflow containing only pinned checkout and pinned Dagger.

- [ ] **Step 1: Write failing clean-generation tests**

```python
def test_should_regenerate_python_and_typescript_clients_without_diff(tmp_path: Path) -> None:
    copied = copy_fixtures(tmp_path)
    run_dagger_develop(copied / "python_consumer")
    run_dagger_develop(copied / "typescript_consumer")
    assert generated_digest(copied) == generated_digest(FIXTURES)


def test_should_pin_every_dependency_to_full_sha_or_local_fixture_path() -> None:
    for dependency in fixture_dependencies():
        assert dependency.is_local or FULL_SHA.search(dependency.source)
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
uv run --directory .dagger pytest .dagger/tests/test_module_fixtures.py -q
```

Expected: failure because fixtures do not exist.

- [ ] **Step 3: Build both composition fixtures**

Each fixture must pass a `Directory`, `Secret`, and generated typed object through the dependency client. Neither fixture may expose an arbitrary command. Commit module configs and lockfiles; keep generated SDK directories ignored and prove clean regeneration.

- [ ] **Step 4: Add thin hosted canary ingress**

`module-canary.yml` triggers weekly and manually. Its only job steps are pinned checkout with `persist-credentials: false` and pinned Dagger calling `module-fixtures`. It has `contents: read` only.

- [ ] **Step 5: Run fixture and root gates**

```bash
uv run --directory .dagger poe gate
DAGGER_NO_NAG=1 dagger call module-fixtures
actionlint .github/workflows/module-canary.yml
```

Expected: both language fixtures pass from a clean generated client and no workflow security findings occur.

- [ ] **Step 6: Commit**

```bash
git add tests/dagger .dagger .github/workflows/module-canary.yml
git commit -m "test(ci): prove cross-language Dagger composition"
```

---

### Task 8: Extend fleet policy to enforce shared dependencies and environments

**Files:**
- Modify: `.dagger/src/ci/github_fleet.py`
- Modify: `.dagger/src/ci/fleet_policy.py`
- Modify: `.dagger/src/ci/fleet.py`
- Modify: `.dagger/tests/test_github_fleet.py`
- Modify: `.dagger/tests/test_fleet_policy.py`

**Interfaces:**
- Consumes: `dagger.json`, generated lock metadata, workflow documents, and GitHub environment metadata.
- Produces: findings for mutable dependencies, incompatible engine/action pins, wrong publisher identity, environment mis-scoping, and arbitrary command escape hatches.

- [ ] **Step 1: Write failing policy tests**

Add behavior cases:

```python
@pytest.mark.parametrize("source", [
    "github.com/hseshadr/ci/modules/portfolio-foundation@main",
    "github.com/hseshadr/ci/modules/portfolio-foundation@v1",
    "github.com/hseshadr/ci/modules/portfolio-foundation@latest",
])
def test_should_reject_mutable_dagger_dependency(source: str) -> None:
    assert "mutable-dagger-dependency" in finding_codes(snapshot_with_dependency(source))


def test_should_require_production_secrets_in_production_environment() -> None:
    snapshot = cloudflare_snapshot(environment=None)
    assert "unscoped-production-secret" in finding_codes(snapshot)


def test_should_allow_exact_shared_module_sha_independent_of_consumer_sha() -> None:
    snapshot = npm_publisher_snapshot(module_sha="b" * 40, consumer_sha="a" * 40)
    assert findings(snapshot) == ()
```

- [ ] **Step 2: Run focused tests and verify RED**

```bash
uv run --directory .dagger pytest \
  .dagger/tests/test_github_fleet.py \
  .dagger/tests/test_fleet_policy.py -q
```

Expected: the new cases fail because `dagger.json` and environment metadata are not read.

- [ ] **Step 3: Add typed GitHub payloads and policy rules**

Fetch `dagger.json`, dependency config, relevant lockfiles, environment names, deployment branch policies, and secret names only. Never request secret values. Recursively validate direct Dagger dependencies by literal SHA and reject cycles, missing configs, mutable refs, and incompatible engine/action tuples.

- [ ] **Step 4: Run full local and live fleet gates**

```bash
uv run --directory .dagger poe gate
GITHUB_TOKEN="$(gh auth token)" dagger call fleet \
  --github-token=env:GITHUB_TOKEN \
  --include-central
```

Expected: current fleet behavior remains green under an explicit temporary grandfathering entry for consumers that have not yet installed shared modules. The entry names each repository and expires after its serial migration; it cannot suppress workflow, protection, secret, or mutable-ref findings.

- [ ] **Step 5: Commit**

```bash
git add .dagger
git commit -m "feat(ci): enforce shared Dagger dependency identity"
```

---

### Task 9: Land the central shared-module release by exact commit

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Create: `docs/dagger-modules.md`

**Interfaces:**
- Produces: one reviewed central merge SHA used as the immutable consumer dependency.
- Consumes: all central gates from Tasks 1–8.

- [ ] **Step 1: Document a runnable consumer example**

Add a quickstart using a dynamically captured exact central SHA:

```bash
FOUNDATION_SHA="$(git ls-remote https://github.com/hseshadr/ci.git refs/heads/main | cut -f1)"
test "${#FOUNDATION_SHA}" -eq 40
dagger install \
  "github.com/hseshadr/ci/modules/portfolio-foundation@$FOUNDATION_SHA" \
  --name foundation
dagger develop
```

Explain that the resolved literal SHA—not `main`—is committed to `dagger.json`.

- [ ] **Step 2: Run the fresh complete verification**

```bash
uv run --directory .dagger poe gate
uv run --directory .dagger poe audit
uv run --directory modules/portfolio-foundation/.dagger poe gate
uv run --directory modules/portfolio-foundation/.dagger poe audit
uv run --directory modules/cloudflare-pages/.dagger poe gate
uv run --directory modules/cloudflare-pages/.dagger poe audit
GITHUB_TOKEN="$(gh auth token)" DAGGER_NO_NAG=1 dagger call ci \
  --github-token=env:GITHUB_TOKEN \
  --commit-sha="$(git rev-parse HEAD)"
DAGGER_NO_NAG=1 dagger call module-fixtures
git diff --check
```

Expected: every command exits zero; current history and snapshot scans are nonempty and clean.

- [ ] **Step 3: Push a human-authored PR and wait for hosted proof**

```bash
git push -u origin feat/dagger-lego-foundation
gh pr create \
  --repo hseshadr/ci \
  --base main \
  --head feat/dagger-lego-foundation \
  --title "feat: add reusable Dagger delivery legos" \
  --body $'## Outcome\n\nAdds exact-SHA reusable Dagger foundation and Pages modules.\n\n## Proof\n\n- Local root/module gates green\n- Python and TypeScript composition fixtures green\n- No package, tag, or registry mutation'
```

If the repository has no PR template, create the body in a temporary file outside the repository and remove it after `gh pr create`.

- [ ] **Step 4: Require hosted evidence before merge**

Require exact-head Dagger, module fixtures, security, and authoritative fleet scans to pass. Verify human author, mergeable clean state, zero unresolved review threads, and unchanged strict sole-Dagger protection.

- [ ] **Step 5: Guarded merge and exact-main proof**

Merge only with an exact-head guard. Record the resulting full SHA as:

```bash
FOUNDATION_SHA="$(gh api repos/hseshadr/ci/commits/main --jq .sha)"
test "${#FOUNDATION_SHA}" -eq 40
printf '%s\n' "$FOUNDATION_SHA" > /tmp/portfolio-foundation-sha
```

Wait for exact-main Dagger and dependent fleet jobs to pass. Do not create a tag or publish a package.

---

### Task 10: Close EdgeReco golden-reference gaps before importing shared code

**Files:**
- Create: `.dagger/src/edge_reco/targets.py`
- Create: `.github/workflows/security-audit.yml`
- Modify: `.dagger/src/edge_reco/main.py`
- Modify: `.dagger/tests/test_public_contracts.py`
- Modify: `backend/tests/unit/test_workflow_security.py`

**Interfaces:**
- Produces: validated local `EdgeRecoTarget` and scheduled/manual Dagger security ingress.
- Preserves: current nine checks, build artifact, SARIF, deploy, and live public APIs.

- [ ] **Step 1: Write failing target tests**

```python
def test_should_bind_edge_reco_repository_pages_and_domain() -> None:
    target = EdgeRecoTarget.production()
    assert target.repository == "hseshadr/edge-reco"
    assert target.project == "edge-reco"
    assert target.branch == "main"
    assert target.domain == "edge-reco.com"


def test_should_reject_unvalidated_repository_override() -> None:
    deploy = inspect.signature(EdgeReco.deploy)
    assert "repository" not in deploy.parameters
```

- [ ] **Step 2: Write failing workflow-extension and schedule tests**

```python
def test_should_actionlint_yml_and_yaml() -> None:
    source = inspect.getsource(EdgeReco.workflow_security)
    assert "*.yml" in source
    assert "*.yaml" in source


def test_should_have_dagger_owned_scheduled_security() -> None:
    workflow = load_workflow("security-audit.yml")
    assert set(workflow["jobs"]["security"]["steps"][0]) >= {"uses"}
    assert all("run" not in step for step in workflow["jobs"]["security"]["steps"])
```

- [ ] **Step 3: Run focused tests and verify RED**

```bash
uv run --directory .dagger poe test
uv run --directory backend poe test -- \
  tests/unit/test_workflow_security.py
```

Expected: failures for raw repository override, `.yaml` omission, and missing scheduled security ingress.

- [ ] **Step 4: Implement the minimal local hardening**

Add an immutable validated target. Remove the raw `repository` deploy parameter. Scan both workflow extensions. Add a weekly/manual workflow containing only pinned credentialless checkout and pinned Dagger calling the existing `workflow-security`, `secret-scan`, audits, and CodeQL functions through a local `security` composition.

- [ ] **Step 5: Run EdgeReco full gate**

```bash
uv run --directory .dagger poe lint
uv run --directory .dagger poe typecheck
uv run --directory .dagger poe complexity
uv run --directory .dagger poe test
DAGGER_NO_NAG=1 dagger check
actionlint .github/workflows/*.yml
git diff --check
```

Expected: all nine checks pass together and workflow tests prove no new privilege path.

- [ ] **Step 6: Commit**

```bash
git add .dagger .github/workflows backend/tests
git commit -m "refactor(ci): harden EdgeReco golden delivery target"
```

---

### Task 11: Install the exact central module and shadow common behavior

**Files:**
- Modify: `dagger.json`
- Modify: `.dagger/pyproject.toml`
- Modify: `.dagger/uv.lock`
- Modify: `.dagger/src/edge_reco/main.py`
- Modify: `.dagger/tests/test_public_contracts.py`
- Create: `.dagger/tests/test_shared_parity.py`

**Interfaces:**
- Consumes: central `FOUNDATION_SHA` recorded in Task 9.
- Produces: exact-SHA generated client named `foundation`.
- Preserves: local current implementation in shadow until parity is proven.

- [ ] **Step 1: Write the failing dependency-pin contract**

```python
def test_should_pin_foundation_to_literal_central_sha() -> None:
    config = json.loads(Path("dagger.json").read_text())
    dependency = next(item for item in config["dependencies"] if item["name"] == "foundation")
    source = dependency["source"]
    assert source.startswith("github.com/hseshadr/ci/modules/portfolio-foundation@")
    assert re.search(r"@[0-9a-f]{40}$", source)
```

- [ ] **Step 2: Run and verify RED**

```bash
uv run --directory .dagger pytest .dagger/tests/test_public_contracts.py -q
```

Expected: failure because the dependency is absent.

- [ ] **Step 3: Install the exact merged central dependency**

```bash
FOUNDATION_SHA="$(cat /tmp/portfolio-foundation-sha)"
test "${#FOUNDATION_SHA}" -eq 40
dagger install \
  "github.com/hseshadr/ci/modules/portfolio-foundation@$FOUNDATION_SHA" \
  --name foundation
dagger develop
uv lock --directory .dagger
```

Review `dagger.json` and prove it contains the literal SHA, not `main` or a tag.
Keep `.dagger/sdk` ignored; verify that a clean module load regenerates the same
public schema from the pinned graph.

- [ ] **Step 4: Write failing old/new behavior-parity tests**

Compare:

- Workflow file discovery.
- Actionlint results.
- Runtime canary behavior.
- Snapshot and exact-history Gitleaks results.
- Source inventory and manifest.
- Wrong/abbreviated SHA rejection.

Use semantic evidence objects rather than comparing log formatting.

- [ ] **Step 5: Compose the shared guard in shadow**

Keep the existing local guard as `legacy_guard` and add `shared_guard`. The canonical check requires both to pass and compares their evidence. Mark the legacy path for deletion only after hosted parity succeeds twice.

- [ ] **Step 6: Run complete parity and product gates**

```bash
uv run --directory .dagger poe gate
DAGGER_NO_NAG=1 dagger check
git diff --check
```

Expected: all nine product checks pass and shared parity reports no semantic difference.

- [ ] **Step 7: Commit**

```bash
git add dagger.json .dagger
git commit -m "feat(ci): consume shared Dagger foundation in shadow"
```

---

### Task 12: Move EdgeReco Pages delivery behind the shared provider lego

**Files:**
- Modify: `dagger.json`
- Modify: `.dagger/src/edge_reco/main.py`
- Modify: `.dagger/tests/test_public_contracts.py`
- Modify: `frontend/app/scripts/deployment-contract.test.mjs`
- Modify: `.github/workflows/deploy.yml`

**Interfaces:**
- Consumes: exact central `cloudflare-pages` module SHA, local artifact `Directory`, local live verifier, and GitHub Environment secrets.
- Produces: shared provider evidence followed by EdgeReco-specific live evidence.

- [ ] **Step 1: Write failing shared-provider composition tests**

```python
def test_should_delegate_provider_mutation_to_pinned_shared_module() -> None:
    source = inspect.getsource(EdgeReco.deploy)
    assert "dag.cloudflare_pages" in source
    assert "cloudflare-pages.sh" not in source


def test_should_keep_product_live_verification_local() -> None:
    source = inspect.getsource(EdgeReco.verify_live)
    assert "live.spec.ts" in source
```

- [ ] **Step 2: Run focused tests and verify RED**

```bash
uv run --directory .dagger pytest \
  .dagger/tests/test_public_contracts.py \
  .dagger/tests/test_shared_parity.py -q
```

Expected: failure because deployment still uses local provider scripts.

- [ ] **Step 3: Install the provider at the same central SHA**

```bash
FOUNDATION_SHA="$(cat /tmp/portfolio-foundation-sha)"
dagger install \
  "github.com/hseshadr/ci/modules/cloudflare-pages@$FOUNDATION_SHA" \
  --name cloudflare-pages
dagger develop
uv lock --directory .dagger
```

- [ ] **Step 4: Compose shared provider and local verifier**

`EdgeReco.deploy` must:

1. Resolve exact green main through shared GitHub evidence.
2. Fetch/build the exact source artifact locally.
3. Envelope it through the shared foundation.
4. Call shared Pages preflight/deploy/verify.
5. Call local `verify_live` using the returned exact identity.

Remove local provider API/PATCH/poll mechanics only after parity tests pass. Retain local artifact and live-browser contracts.

- [ ] **Step 5: Add the production Environment boundary**

Create the environment and main-only deployment policy:

```bash
gh api --method PUT repos/hseshadr/edge-reco/environments/production \
  -f wait_timer=0 \
  -F prevent_self_review=false
```

Set the deployment job's `environment: production`. GitHub secret values are write-only, so migrate them through secure prompts rather than attempting to read them:

```bash
gh secret set CLOUDFLARE_ACCOUNT_ID --repo hseshadr/edge-reco --env production
gh secret set CLOUDFLARE_API_TOKEN --repo hseshadr/edge-reco --env production
```

Do not delete the repository-scoped copies yet. First prove the environment-scoped deploy can access both values without logging them.

- [ ] **Step 6: Run full no-secret and dry-run gates**

```bash
uv run --directory .dagger poe gate
DAGGER_NO_NAG=1 dagger check
DAGGER_NO_NAG=1 dagger call release-preflight \
  --commit-sha="$(git rev-parse HEAD)"
actionlint .github/workflows/*.yml
git diff --check
```

Expected: all checks pass without production secrets; release preflight validates exact artifact and provider request structure without upload.

- [ ] **Step 7: Commit**

```bash
git add dagger.json .dagger .github/workflows/deploy.yml frontend/app/scripts
git commit -m "refactor(ci): compose shared Pages delivery"
```

---

### Task 13: Prove the EdgeReco canary through production and rollback

**Files:**
- External evidence only: GitHub runs, Cloudflare deployment, public live routes, and rollback worktree.

**Interfaces:**
- Produces: hosted shadow evidence, exact-main deployment evidence, independent live evidence, and rollback evidence.
- Consumes: exact central module pin and production Environment secrets.

- [ ] **Step 1: Push a human-authored EdgeReco PR**

```bash
git push -u origin feat/dagger-lego-canary
gh pr create \
  --repo hseshadr/edge-reco \
  --base main \
  --head feat/dagger-lego-canary \
  --title "refactor(ci): canary shared Dagger delivery legos" \
  --body $'## Outcome\n\nCanaries the exact-SHA shared Dagger modules in EdgeReco.\n\n## Proof required before merge\n\n- Two hosted Dagger proofs\n- Cold-cache proof\n- Dependency-pin rollback rehearsal\n- No package, tag, or registry mutation'
```

- [ ] **Step 2: Require two hosted exact-head proofs**

The first proof is the normal PR run. Trigger the second with a human-authored empty commit only if GitHub cannot rerun the exact successful workflow while preserving auditability. Both exact heads must pass Dagger and SARIF; no Cloudflare Git check may appear.

- [ ] **Step 3: Run a cold-cache proof**

Use a fresh GitHub-hosted runner and a unique Dagger cache namespace supplied by the local adapter. Do not delete or prune shared developer Docker state. Require dependency installation, generated clients, source/history, security, artifact envelope, and product gates to execute successfully without prior mutable cache state.

- [ ] **Step 4: Rehearse dependency-pin rollback before merge**

Create a temporary linked worktree from the PR head. Revert only the two shared dependency pins and lockfile to the previous local implementation commit, regenerate the ignored client, and run the complete nine-check Dagger graph. Remove the temporary worktree only after it is clean and green. Do not push the rollback branch.

- [ ] **Step 5: Perform the guarded merge**

Verify exact head, human author, mergeable clean state, zero comments/reviews/unresolved threads, all required checks green, strict sole-Dagger protection, and no Dependabot involvement. Merge with an exact-head guard.

- [ ] **Step 6: Verify exact-main Dagger and guarded deployment**

Wait for exact-main Dagger and SARIF. Confirm deploy starts only after the same-SHA Dagger result succeeds. Require log ordering:

1. Exact-green main evidence.
2. Artifact envelope validation.
3. Cloudflare API preflight.
4. Git deployment disable.
5. Wrangler preflight and upload.
6. Raw API full-SHA/stage convergence.
7. Local public live verification.

- [ ] **Step 7: Independently verify production**

Fetch public `build.json`, bundle manifest, model hashes, canonical redirect, and unique Pages deployment URL. Run the real-browser Dagger live verifier and require zero backend calls, zero foreign egress, and zero page/console errors.

- [ ] **Step 8: Remove repository-scoped secret duplicates**

After the environment-scoped production deploy succeeds, verify current-main workflows reference the environment boundary. Then remove only the duplicate EdgeReco repository secrets:

```bash
gh secret delete CLOUDFLARE_ACCOUNT_ID --repo hseshadr/edge-reco
gh secret delete CLOUDFLARE_API_TOKEN --repo hseshadr/edge-reco
```

Immediately verify the names exist in the `production` environment:

```bash
gh secret list --repo hseshadr/edge-reco --env production --json name \
  --jq '[.[].name] | sort'
```

Task 14's fresh exact-main deployment is the no-fallback proof after removal. Secret values remain unreadable throughout.

- [ ] **Step 9: Capture canary evidence for the deletion follow-up**

Record exact central SHA, EdgeReco main SHA, hosted run IDs, provider deployment ID/URL, artifact manifest, rollback command/result, and known limitations in the task handoff. Do not modify the already-merged canary branch and do not label other repositories migrated. Task 14 writes this evidence into the deletion follow-up from fresh exact main.

---

### Task 14: Delete duplicate mechanics only after the canary is proven

**Files:**
- Modify: `.dagger/src/edge_reco/main.py`
- Delete: shared mechanics from `.dagger/scripts/cloudflare-pages.sh`
- Delete: shared mechanics from `.dagger/scripts/gitleaks-canary.sh`
- Modify: `.dagger/tests/test_public_contracts.py`
- Modify: `backend/tests/unit/test_workflow_security.py`
- Modify: `README.md`
- Create: `docs/dagger-lego-adoption.md`

**Interfaces:**
- Preserves: EdgeReco public Dagger API and product-specific behavior.
- Removes: local source/history, common workflow guard, candidate envelope, GitHub evidence, and provider API duplication.

- [ ] **Step 1: Start the follow-up from fresh verified main**

```bash
git fetch origin main
test "$(git rev-parse origin/main)" = "$(gh api repos/hseshadr/edge-reco/commits/main --jq .sha)"
git switch --create refactor/delete-edge-reco-delivery-duplicates origin/main
```

Verify the worktree is clean before editing.

- [ ] **Step 2: Write the deletion contract first**

```python
def test_should_not_retain_shared_provider_or_guard_implementations() -> None:
    root = Path(__file__).parents[2]
    forbidden = (
        root / "scripts/cloudflare-pages.sh",
        root / "scripts/gitleaks-canary.sh",
    )
    assert all(not path.exists() for path in forbidden)
```

- [ ] **Step 3: Run and verify RED**

```bash
uv run --directory .dagger pytest .dagger/tests/test_public_contracts.py -q
```

Expected: failure because duplicate files still exist.

- [ ] **Step 4: Delete only proven duplicates and write canary evidence**

Keep CodeQL, product build, model, parity, browser, signed-catalog, and live verification code local. Remove provider and common guard mechanics now owned by exact-SHA dependencies.

Write `docs/dagger-lego-adoption.md` with the exact evidence captured in Task 13. Update the README to link it and state that EdgeReco alone has graduated the shared canary.

- [ ] **Step 5: Run the final exact-tree gate**

```bash
uv run --directory .dagger poe gate
DAGGER_NO_NAG=1 dagger check
DAGGER_NO_NAG=1 dagger call release-preflight \
  --commit-sha="$(git rev-parse HEAD)"
actionlint .github/workflows/*.yml
git diff --check
```

Expected: all nine checks pass; no product behavior is removed.

- [ ] **Step 6: Commit, push, and open the deletion PR**

```bash
git add -A .dagger backend frontend .github README.md docs/dagger-lego-adoption.md
git commit -m "refactor(ci): delete duplicated delivery mechanics"
git push -u origin refactor/delete-edge-reco-delivery-duplicates
gh pr create \
  --repo hseshadr/edge-reco \
  --base main \
  --head refactor/delete-edge-reco-delivery-duplicates \
  --title "refactor(ci): delete duplicated delivery mechanics" \
  --body $'## Outcome\n\nDeletes mechanics proven by the shared-module canary while retaining EdgeReco product adapters.\n\n## Required proof\n\n- Fresh complete Dagger graph\n- Exact-main deploy and independent live verification'
```

- [ ] **Step 7: Repeat guarded hosted/main/live proof**

Require a fresh PR exact-head Dagger, guarded merge, exact-main Dagger, deployment, and independent live verification. Do not accept prior canary evidence for the deletion tree.

---

### Task 15: Close the canary and authorize the next archetype plan

**Files:**
- Modify: central `README.md`
- Modify: central `CHANGELOG.md`
- Modify: central `.dagger/src/ci/fleet.py`
- Modify: central `.dagger/tests/test_fleet.py`
- Create: central `docs/canaries/edge-reco-foundation-v1.md`

**Interfaces:**
- Produces: one immutable canary record and removal of EdgeReco's temporary grandfathering entry.
- Enables: a separate EdgeProc/EdgeProc Core Python-package implementation plan.

- [ ] **Step 1: Write failing conformance test without grandfathering**

```python
def test_should_require_shared_foundation_for_edge_reco() -> None:
    expectation = expectation_for("edge-reco")
    assert expectation.shared_foundation_required is True
    assert expectation.grandfathered_until is None
```

- [ ] **Step 2: Run and verify RED**

```bash
uv run --directory .dagger pytest .dagger/tests/test_fleet.py -q
```

Expected: failure while EdgeReco still has a temporary rollout allowance.

- [ ] **Step 3: Remove the allowance and record evidence**

Record exact module, EdgeReco, hosted run, deployment, live manifest, cold-cache, and rollback evidence. State explicitly that no other consumer is covered by this proof.

- [ ] **Step 4: Run final central verification**

```bash
uv run --directory .dagger poe gate
uv run --directory .dagger poe audit
GITHUB_TOKEN="$(gh auth token)" dagger call fleet \
  --github-token=env:GITHUB_TOKEN \
  --include-central
git diff --check
```

Expected: all eight exact mains have zero findings and EdgeReco passes without an allowance.

- [ ] **Step 5: Commit and deliver the evidence PR**

```bash
git add README.md CHANGELOG.md .dagger docs/canaries
git commit -m "docs(ci): graduate EdgeReco shared-module canary"
```

Use the same hosted exact-head, guarded merge, and exact-main fleet gates. After it is green, write the next implementation plan for the Python-package archetype; do not begin that migration in this plan.

## Completion Evidence

Before claiming this plan complete, report:

- Exact central module merge SHA.
- Exact EdgeReco merge SHA.
- Literal dependency SHAs from EdgeReco `dagger.json`.
- Local central, module, and EdgeReco gate commands with pass counts.
- Hosted central module, EdgeReco PR, exact-main, deploy, and live run links.
- Cold-cache proof.
- Rollback rehearsal command and result.
- Cloudflare provider full-SHA evidence and independent public identity.
- GitHub `production` environment and secret names only.
- Confirmation that repository-scoped Cloudflare duplicates were removed only after environment deployment passed.
- Final strict sole-Dagger branch protection.
- Final fleet zero-finding result.
- Duplicate CI/CD LOC before and after.
- Confirmation that no Dependabot PR, package, tag, release, or registry was mutated.
