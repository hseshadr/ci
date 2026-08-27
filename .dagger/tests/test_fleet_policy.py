from __future__ import annotations

from dataclasses import replace

from ci.fleet_policy import (
    CheckRun,
    Protection,
    RepositoryExpectation,
    RepositorySnapshot,
    RequiredCheck,
    SourceFile,
    validate_repository,
)

SHA = "a" * 40
ACTION = "1" * 40
DAGGER = "2" * 40
UPLOAD = "3" * 40
DOWNLOAD = "4" * 40
PYPI = "5" * 40
HEAD_SHA = "${{ github.event.workflow_run.head_sha }}"

MODULE = """
@object_type
class Example:
    source: dagger.Directory = field()

    @classmethod
    def create(cls, workspace: dagger.Workspace) -> Self:
        instance = cls.__new__(cls)
        instance.source = workspace.directory("/", exclude=[".git", ".env"])
        return instance

    @function
    def publish_npm(
        self, oidc_url: dagger.Secret, oidc_token: dagger.Secret
    ) -> str:
        return "ready"
"""

INGRESS = f"""
name: Dagger
on: [push, pull_request]
permissions:
  contents: read
jobs:
  dagger:
    name: Dagger
    runs-on: ubuntu-latest
    timeout-minutes: 30
    concurrency:
      group: example
      cancel-in-progress: false
    steps:
      - uses: actions/checkout@{ACTION}
        with:
          persist-credentials: false
      - uses: dagger/dagger-for-github@{DAGGER}
        with:
          version: "0.21.8"
          verb: call
          args: ci --commit-sha=${{{{ github.sha }}}}
"""

CANDIDATE = f"""
name: Dagger release candidate
on:
  workflow_dispatch:
permissions:
  contents: read
jobs:
  candidate:
    name: Dagger release candidate
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@{ACTION}
        with:
          persist-credentials: false
      - uses: dagger/dagger-for-github@{DAGGER}
        with:
          version: "0.21.8"
          verb: call
          args: release-candidate --commit-sha=${{{{ github.sha }}}} export --path=release
      - uses: actions/upload-artifact@{UPLOAD}
        with:
          name: example-${{{{ github.sha }}}}
          path: release/
          if-no-files-found: error
          retention-days: 1
"""

PYPI_BRIDGE = f"""
name: Publish trusted artifacts
on:
  workflow_run:
    workflows: [Dagger release candidate]
    types: [completed]
permissions: {{}}
jobs:
  publish-python:
    if: >-
      github.event.workflow_run.conclusion == 'success' &&
      github.event.workflow_run.event == 'workflow_dispatch' &&
      github.event.workflow_run.head_branch == github.event.repository.default_branch
    runs-on: ubuntu-latest
    permissions:
      actions: read
      contents: read
      id-token: write
    steps:
      - uses: actions/download-artifact@{DOWNLOAD}
        with:
          name: example-${{{{ github.event.workflow_run.head_sha }}}}
          path: release
          github-token: ${{{{ github.token }}}}
          run-id: ${{{{ github.event.workflow_run.id }}}}
      - uses: pypa/gh-action-pypi-publish@{PYPI}
        with:
          packages-dir: release/dist
          attestations: true
"""

NPM_ARGS = (
    f"publish-npm --candidate=candidate --expected-sha={HEAD_SHA} "
    "--oidc-url=env:ACTIONS_ID_TOKEN_REQUEST_URL "
    "--oidc-token=env:ACTIONS_ID_TOKEN_REQUEST_TOKEN"
)
NPM_BRIDGE = f"""
name: Publish trusted artifacts
on: workflow_run
permissions: {{}}
jobs:
  publish-npm:
    runs-on: ubuntu-latest
    permissions:
      actions: read
      contents: read
      id-token: write
    steps:
      - uses: actions/download-artifact@{DOWNLOAD}
        with:
          name: example-{HEAD_SHA}
          path: release
          github-token: ${{{{ github.token }}}}
          run-id: ${{{{ github.event.workflow_run.id }}}}
      - uses: dagger/dagger-for-github@{DAGGER}
        with:
          version: "0.21.8"
          verb: call
          module: github.com/hseshadr/example@{HEAD_SHA}
          args: {NPM_ARGS}
"""


def _snapshot(
    *workflows: str,
    module: str = MODULE,
    module_path: str = ".dagger/src/example/main.py",
) -> RepositorySnapshot:
    protection = Protection(
        strict=True,
        enforce_admins=True,
        approvals=0,
        conversation_resolution=True,
        linear_history=True,
        allow_force_pushes=False,
        allow_deletions=False,
        checks=(RequiredCheck(context="Dagger", app_id=15368),),
    )
    check = CheckRun(name="Dagger", app_id=15368, head_sha=SHA, conclusion="success")
    sources = tuple(
        SourceFile(path=f".github/workflows/{index}.yml", text=text)
        for index, text in enumerate(workflows)
    )
    return RepositorySnapshot(
        name="example",
        sha=SHA,
        workflows=sources,
        modules=(SourceFile(path=module_path, text=module),),
        protection=protection,
        check_runs=(check,),
        check_apps=("github-actions",),
        codeql_default_state="not-configured",
        legacy_references=(),
    )


def _codes(snapshot: RepositorySnapshot) -> tuple[str, ...]:
    expectation = RepositoryExpectation(
        name="example",
        required_contexts=("Dagger",),
        linear_history=True,
        conversation_resolution=True,
    )
    return tuple(finding.code for finding in validate_repository(snapshot, expectation))


def test_should_accept_pinned_dagger_ingress_and_exact_release_transports() -> None:
    # Given a repository whose only non-Dagger steps are the two reviewed transports
    snapshot = _snapshot(INGRESS, CANDIDATE, PYPI_BRIDGE, NPM_BRIDGE)

    # When the complete repository contract is evaluated
    codes = _codes(snapshot)

    # Then no policy violation is reported
    assert codes == ()


def test_should_reject_shell_setup_and_mutable_actions_in_ordinary_ingress() -> None:
    # Given three independent ways to execute outside the reviewed Dagger boundary
    workflow = INGRESS.replace(
        f"      - uses: dagger/dagger-for-github@{DAGGER}",
        "      - uses: actions/setup-python@v6\n      - run: pytest\n"
        f"      - uses: dagger/dagger-for-github@{DAGGER}",
    )

    # When the ingress is evaluated
    codes = _codes(_snapshot(workflow))

    # Then each executable escape is rejected
    assert {"mutable-action", "shell-step", "non-dagger-ingress"} <= set(codes)


def test_should_reject_candidate_upload_when_identity_or_order_is_not_exact() -> None:
    # Given an upload before Dagger whose artifact name is not bound to the candidate SHA
    upload = f"""      - uses: actions/upload-artifact@{UPLOAD}
        with:
          name: floating
          path: dist/
"""
    workflow = CANDIDATE.replace(upload, "").replace(
        f"      - uses: dagger/dagger-for-github@{DAGGER}",
        upload + f"      - uses: dagger/dagger-for-github@{DAGGER}",
    )

    # When the candidate transport is evaluated
    codes = _codes(_snapshot(workflow))

    # Then it cannot persist unproven bytes
    assert {"candidate-order", "artifact-identity"} <= set(codes)


def test_should_reject_privileged_bridge_with_source_shell_or_weak_attestation() -> None:
    # Given a PyPI bridge that checks out source, runs a verifier, and disables attestations
    bad = PYPI_BRIDGE.replace(
        f"      - uses: actions/download-artifact@{DOWNLOAD}",
        f"      - uses: actions/checkout@{ACTION}\n      - run: python verify.py\n"
        f"      - uses: actions/download-artifact@{DOWNLOAD}",
    ).replace("attestations: true", "attestations: false")

    # When the privileged bridge is evaluated
    codes = _codes(_snapshot(bad))

    # Then source, shell, and provenance weakening all fail closed
    assert {"privileged-source", "shell-step", "pypi-attestation"} <= set(codes)


def test_should_reject_npm_bridge_without_exact_module_or_typed_oidc() -> None:
    # Given a mutable remote module and untyped OIDC arguments
    bridge = NPM_BRIDGE.replace(
        "github.com/hseshadr/example@${{ github.event.workflow_run.head_sha }}",
        "github.com/hseshadr/example@main",
    ).replace("oidc_url: dagger.Secret", "oidc_url: str")

    # When the npm publisher is evaluated
    codes = _codes(
        _snapshot(bridge, module=MODULE.replace("oidc_url: dagger.Secret", "oidc_url: str"))
    )

    # Then both exact source and secret typing are required
    assert {"remote-module-identity", "untyped-secret"} <= set(codes)


def test_should_allow_decrypted_credentials_inside_internal_adapters() -> None:
    # Given an internal HTTP adapter that receives a credential after Dagger decrypts it
    internal = (
        MODULE
        + """
class InternalTransport:
    def __init__(self, token: str) -> None:
        self.token = token
"""
    )

    # When module credential boundaries are evaluated
    codes = _codes(_snapshot(INGRESS, module=internal))

    # Then only Dagger-exposed @function arguments require dagger.Secret
    assert "untyped-secret" not in codes


def test_should_reject_missing_or_wrong_branch_protection_integration() -> None:
    # Given a required Dagger context attached to the wrong app and a stale check run
    original = _snapshot(INGRESS)
    snapshot = replace(
        original,
        protection=replace(
            original.protection,
            checks=(RequiredCheck(context="Dagger", app_id=7),),
        ),
        check_runs=(
            CheckRun(name="Dagger", app_id=15368, head_sha="b" * 40, conclusion="success"),
        ),
    )

    # When authoritative protection and exact-main integration are evaluated
    codes = _codes(snapshot)

    # Then projections and stale checks cannot look healthy
    assert {"required-check-app", "main-integration"} <= set(codes)


def test_should_reject_legacy_central_execution_reference() -> None:
    # Given a current-main file that still executes a retired central reusable workflow
    snapshot = replace(
        _snapshot(INGRESS),
        legacy_references=(".github/workflows/ci.yml:hseshadr/ci",),
    )

    # When the repository contract is evaluated
    codes = _codes(snapshot)

    # Then deletion is blocked until the reference is removed
    assert codes == ("legacy-central-reference",)


def test_should_reject_managed_codeql_and_cloudflare_but_allow_gitguardian() -> None:
    # Given exact-main checks from Dagger, advisory GitGuardian, and Cloudflare Git
    snapshot = replace(
        _snapshot(INGRESS),
        codeql_default_state="configured",
        check_apps=("github-actions", "gitguardian", "cloudflare-pages"),
    )

    # When independent control-plane ownership is evaluated
    codes = _codes(snapshot)

    # Then managed CodeQL and Cloudflare fail while GitGuardian stays advisory
    assert {"managed-codeql", "independent-check-app"} <= set(codes)


def test_should_accept_typescript_workspace_constructor_when_source_is_explicit() -> None:
    # Given a TypeScript module with the same typed Workspace-to-Directory boundary
    module = """
export class Example {
  private readonly source: Directory;
  constructor(workspace: Workspace) {
    this.source = workspace.directory("/", { exclude: [".git", ".env"] });
  }
  publish(token: Secret): string { return "ready"; }
}
"""

    # When its complete repository contract is evaluated
    codes = _codes(_snapshot(INGRESS, module=module, module_path=".dagger/src/example/main.ts"))

    # Then TypeScript receives the same explicit-source treatment as Python
    assert "explicit-source" not in codes


def test_should_accept_check_or_call_input_when_dagger_action_owns_execution() -> None:
    # Given the action's two supported invocation encodings
    check_workflow = INGRESS.replace(
        "verb: call\n          args: ci", "verb: check\n          args: ci"
    )
    call_workflow = INGRESS.replace(
        "verb: call\n          args: ci --commit-sha=${{ github.sha }}",
        "call: ci --commit-sha=${{ github.sha }}",
    )
    action_check_workflow = INGRESS.replace(
        "verb: call\n          args: ci --commit-sha=${{ github.sha }}",
        'check: "**"',
    )

    # When both thin Dagger jobs are evaluated
    check_codes = _codes(_snapshot(check_workflow))
    call_codes = _codes(_snapshot(call_workflow))
    action_check_codes = _codes(_snapshot(action_check_workflow))

    # Then invocation syntax does not create independent orchestration
    assert "dagger-verb" not in check_codes
    assert "dagger-verb" not in call_codes
    assert "dagger-verb" not in action_check_codes


def test_should_accept_exact_remote_dagger_plan_before_official_pypi() -> None:
    # Given Assay's source-free exact-SHA Dagger decision before official PyPI
    plan = f"""      - uses: dagger/dagger-for-github@{DAGGER}
        with:
          version: "0.21.8"
          verb: call
          module: github.com/hseshadr/example@{HEAD_SHA}
          args: pypi-required --candidate=release --expected-sha={HEAD_SHA}
"""
    bridge = PYPI_BRIDGE.replace(
        f"      - uses: pypa/gh-action-pypi-publish@{PYPI}",
        plan + f"      - uses: pypa/gh-action-pypi-publish@{PYPI}",
    )

    # When the privileged bridge is evaluated
    codes = _codes(_snapshot(bridge))

    # Then exact remote planning remains source-free and allowed
    assert "pypi-shape" not in codes
    assert "remote-module-identity" not in codes
