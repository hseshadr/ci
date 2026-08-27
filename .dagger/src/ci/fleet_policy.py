"""Typed behavioral policy for the seven Dagger consumer repositories."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Final, TypeIs

import yaml
from pydantic import ConfigDict, Field, TypeAdapter, ValidationError
from pydantic.dataclasses import dataclass as validated_dataclass

PINNED_ACTION: Final = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}$")
SENSITIVE_ARGUMENT: Final = re.compile(
    r"(?:token|secret|password|private_key|api_key|signing_key|oidc_url)$"
)
CHECKOUT: Final = "actions/checkout"
DAGGER_ACTION: Final = "dagger/dagger-for-github"
UPLOAD_ACTION: Final = "actions/upload-artifact"
DOWNLOAD_ACTION: Final = "actions/download-artifact"
PYPI_ACTION: Final = "pypa/gh-action-pypi-publish"
APP_ID: Final = 15368
type Scalar = str | bool | int
BOUNDARY_CONFIG: Final = ConfigDict(frozen=True, extra="forbid")


@validated_dataclass(config=BOUNDARY_CONFIG)
class SourceFile:
    """One exact-main source file returned by GitHub."""

    path: str
    text: str


@validated_dataclass(config=BOUNDARY_CONFIG)
class RequiredCheck:
    """One app-bound required status check."""

    context: str
    app_id: int


@validated_dataclass(config=BOUNDARY_CONFIG)
class CheckRun:
    """One exact-commit integration result."""

    name: str
    app_id: int
    head_sha: str
    conclusion: str


@validated_dataclass(config=BOUNDARY_CONFIG)
class Protection:
    """Effective classic branch protection returned by GitHub."""

    strict: bool
    enforce_admins: bool
    approvals: int
    conversation_resolution: bool
    linear_history: bool
    allow_force_pushes: bool
    allow_deletions: bool
    checks: tuple[RequiredCheck, ...]


@validated_dataclass(config=BOUNDARY_CONFIG)
class RepositoryExpectation:
    """The reviewed protection contract for one fleet member."""

    name: str
    required_contexts: tuple[str, ...]
    linear_history: bool
    conversation_resolution: bool


@validated_dataclass(config=BOUNDARY_CONFIG)
class RepositorySnapshot:
    """All authoritative source, protection, and integration evidence."""

    name: str
    sha: str
    workflows: tuple[SourceFile, ...]
    modules: tuple[SourceFile, ...]
    protection: Protection
    check_runs: tuple[CheckRun, ...]
    check_apps: tuple[str, ...]
    codeql_default_state: str
    legacy_references: tuple[str, ...]


@dataclass(frozen=True)
class PolicyFinding:
    """One actionable failure with a stable machine-readable code."""

    code: str
    path: str
    message: str


@validated_dataclass(config=BOUNDARY_CONFIG)
class WorkflowStep:
    """The complete step vocabulary accepted by the fleet policy."""

    name: str | None = None
    id: str | None = None
    if_: str | None = Field(default=None, alias="if")
    uses: str | None = None
    run: str | None = None
    with_: dict[str, Scalar] = Field(default_factory=dict, alias="with")
    env: dict[str, Scalar] = Field(default_factory=dict)
    shell: str | None = None
    working_directory: str | None = Field(default=None, alias="working-directory")
    continue_on_error: str | None = Field(default=None, alias="continue-on-error")
    timeout_minutes: Scalar | None = Field(default=None, alias="timeout-minutes")


@validated_dataclass(config=BOUNDARY_CONFIG)
class WorkflowJob:
    """The complete job vocabulary accepted by thin fleet workflows."""

    steps: tuple[WorkflowStep, ...]
    name: str | None = None
    if_: str | None = Field(default=None, alias="if")
    runs_on: str | None = Field(default=None, alias="runs-on")
    timeout_minutes: Scalar | None = Field(default=None, alias="timeout-minutes")
    permissions: dict[str, Scalar] = Field(default_factory=dict)
    environment: str | None = None
    needs: str | list[str] | None = None
    outputs: dict[str, Scalar] = Field(default_factory=dict)
    concurrency: object | None = None


@validated_dataclass(config=BOUNDARY_CONFIG)
class WorkflowDocument:
    """The root vocabulary for repository-authored workflows."""

    name: str
    jobs: dict[str, WorkflowJob]
    on_: object = Field(alias="on")
    permissions: dict[str, Scalar] = Field(default_factory=dict)
    concurrency: object | None = None


def finding(code: str, path: str, message: str) -> PolicyFinding:
    """Build one immutable policy finding."""
    return PolicyFinding(code=code, path=path, message=message)


def action_name(step: WorkflowStep) -> str:
    """Return an action reference without its immutable revision."""
    return "" if step.uses is None else step.uses.partition("@")[0]


def scalar_text(value: Scalar | None) -> str:
    """Normalize a validated YAML scalar for exact policy comparison."""
    if isinstance(value, bool):
        return str(value).lower()
    return "" if value is None else str(value)


def parse_workflow(source: SourceFile) -> WorkflowDocument | PolicyFinding:
    """Parse and validate one complete workflow document."""
    try:
        document = normalize_on_key(yaml.safe_load(source.text))
        return TypeAdapter(WorkflowDocument).validate_python(document)
    except (yaml.YAMLError, ValidationError) as error:
        return finding("workflow-schema", source.path, str(error))


def normalize_on_key(document: object) -> object:
    """Restore GitHub's `on` key after YAML 1.1 boolean decoding."""
    if not isinstance(document, dict) or True not in document:
        return document
    document["on"] = document.pop(True)
    return document


def validate_repository(
    snapshot: RepositorySnapshot, expectation: RepositoryExpectation
) -> tuple[PolicyFinding, ...]:
    """Validate source, protection, and exact-main integration as one contract."""
    findings = [item for source in snapshot.workflows for item in validate_workflow(source)]
    findings.extend(validate_modules(snapshot.modules))
    findings.extend(validate_protection(snapshot, expectation))
    findings.extend(validate_control_plane(snapshot))
    findings.extend(validate_legacy(snapshot))
    return tuple(findings)


def validate_workflow(source: SourceFile) -> tuple[PolicyFinding, ...]:
    """Validate every job in one real workflow."""
    parsed = parse_workflow(source)
    if isinstance(parsed, PolicyFinding):
        return (parsed,)
    findings = [item for job in parsed.jobs.values() for item in validate_job(source.path, job)]
    return tuple(findings)


def validate_job(path: str, job: WorkflowJob) -> tuple[PolicyFinding, ...]:
    """Dispatch a job to its only accepted execution shape."""
    common = validate_steps(path, job.steps)
    names = tuple(map(action_name, job.steps))
    if UPLOAD_ACTION in names:
        return common + validate_candidate(path, job.steps)
    if DOWNLOAD_ACTION in names or scalar_text(job.permissions.get("id-token")) == "write":
        return common + validate_publisher(path, job)
    return common + validate_ingress(path, job.steps)


def validate_steps(path: str, steps: tuple[WorkflowStep, ...]) -> tuple[PolicyFinding, ...]:
    """Reject mutable actions and shell execution in every shape."""
    findings: list[PolicyFinding] = []
    for step in steps:
        if step.uses and not PINNED_ACTION.fullmatch(step.uses):
            findings.append(finding("mutable-action", path, step.uses))
        if step.run is not None:
            findings.append(finding("shell-step", path, "run steps are forbidden"))
    return tuple(findings)


def validate_ingress(path: str, steps: tuple[WorkflowStep, ...]) -> tuple[PolicyFinding, ...]:
    """Accept exactly pinned checkout followed by a Dagger call."""
    names = tuple(map(action_name, steps))
    findings: list[PolicyFinding] = []
    if names != (CHECKOUT, DAGGER_ACTION):
        findings.append(finding("non-dagger-ingress", path, "expected checkout then Dagger"))
        return tuple(findings)
    findings.extend(validate_checkout(path, steps[0]))
    findings.extend(validate_dagger(path, steps[1]))
    return tuple(findings)


def validate_checkout(path: str, step: WorkflowStep) -> tuple[PolicyFinding, ...]:
    """Require checkout to leave no credential behind."""
    if scalar_text(step.with_.get("persist-credentials")) == "false":
        return ()
    return (finding("checkout-credentials", path, "persist-credentials must be false"),)


def validate_dagger(path: str, step: WorkflowStep) -> tuple[PolicyFinding, ...]:
    """Require the reviewed immutable Dagger call boundary."""
    findings: list[PolicyFinding] = []
    if scalar_text(step.with_.get("version")) != "0.21.8":
        findings.append(finding("dagger-version", path, "Dagger 0.21.8 required"))
    if not dagger_invocation_is_owned(step):
        findings.append(finding("dagger-verb", path, "Dagger call/check input required"))
    return tuple(findings)


def dagger_invocation_is_owned(step: WorkflowStep) -> bool:
    """Accept the action's explicit call and check invocation encodings."""
    verb = scalar_text(step.with_.get("verb"))
    call = scalar_text(step.with_.get("call"))
    check_input = scalar_text(step.with_.get("check"))
    return verb in {"call", "check"} or bool(call) or bool(check_input)


def validate_candidate(path: str, steps: tuple[WorkflowStep, ...]) -> tuple[PolicyFinding, ...]:
    """Accept only Dagger-proven candidate bytes persisted after Dagger."""
    names = tuple(map(action_name, steps))
    findings: list[PolicyFinding] = []
    if names != (CHECKOUT, DAGGER_ACTION, UPLOAD_ACTION):
        findings.append(finding("candidate-order", path, "expected checkout, Dagger, upload"))
    if steps and action_name(steps[0]) == CHECKOUT:
        findings.extend(validate_checkout(path, steps[0]))
    findings.extend(validate_candidate_identity(path, steps))
    return tuple(findings)


def validate_candidate_identity(
    path: str, steps: tuple[WorkflowStep, ...]
) -> tuple[PolicyFinding, ...]:
    """Bind the uploaded directory and name to the Dagger candidate SHA."""
    upload = next((step for step in steps if action_name(step) == UPLOAD_ACTION), None)
    if upload is None or candidate_identity_is_weak(upload):
        return (finding("artifact-identity", path, "candidate artifact is not exact"),)
    return ()


def candidate_identity_is_weak(step: WorkflowStep) -> bool:
    """Report whether an upload can persist bytes outside the exact candidate."""
    name = scalar_text(step.with_.get("name"))
    candidate_path = scalar_text(step.with_.get("path")).rstrip("/")
    return (
        "${{ github.sha }}" not in name
        or candidate_path not in {"candidate", "release"}
        or scalar_text(step.with_.get("if-no-files-found")) != "error"
        or scalar_text(step.with_.get("retention-days")) != "1"
    )


def validate_publisher(path: str, job: WorkflowJob) -> tuple[PolicyFinding, ...]:
    """Accept only source-free artifact transport into one OIDC publisher."""
    findings: list[PolicyFinding] = []
    findings.extend(validate_publisher_permissions(path, job))
    findings.extend(validate_publisher_source(path, job.steps))
    findings.extend(validate_download(path, job.steps))
    names = tuple(map(action_name, job.steps))
    if PYPI_ACTION in names:
        findings.extend(validate_pypi(path, job.steps))
    else:
        findings.extend(validate_npm(path, job.steps))
    return tuple(findings)


def validate_publisher_permissions(path: str, job: WorkflowJob) -> tuple[PolicyFinding, ...]:
    """Limit publisher authority to artifact read and OIDC minting."""
    minimal = {"actions": "read", "id-token": "write"}
    current = {"actions": "read", "contents": "read", "id-token": "write"}
    if job.permissions in (minimal, current):
        return ()
    return (finding("publisher-permissions", path, "only read transport plus id-token:write"),)


def validate_publisher_source(
    path: str, steps: tuple[WorkflowStep, ...]
) -> tuple[PolicyFinding, ...]:
    """Reject checkout and setup actions inside a privileged bridge."""
    forbidden = (CHECKOUT, "actions/setup-", "pnpm/action-setup", "astral-sh/setup-uv")
    names = tuple(map(action_name, steps))
    if any(name == forbidden[0] or name.startswith(forbidden[1:]) for name in names):
        return (finding("privileged-source", path, "publisher must remain source-free"),)
    return ()


def validate_download(path: str, steps: tuple[WorkflowStep, ...]) -> tuple[PolicyFinding, ...]:
    """Bind downloaded bytes to the triggering candidate run and SHA."""
    download = next((step for step in steps if action_name(step) == DOWNLOAD_ACTION), None)
    if download is None or download_identity_is_weak(download):
        return (finding("artifact-identity", path, "download is not bound to candidate run"),)
    return ()


def download_identity_is_weak(step: WorkflowStep) -> bool:
    """Report whether a download can select unrelated artifact bytes."""
    values = step.with_
    return (
        "${{ github.event.workflow_run.head_sha }}" not in scalar_text(values.get("name"))
        or scalar_text(values.get("run-id")) != "${{ github.event.workflow_run.id }}"
        or scalar_text(values.get("github-token")) != "${{ github.token }}"
        or scalar_text(values.get("path")) not in {"candidate", "release"}
    )


def validate_pypi(path: str, steps: tuple[WorkflowStep, ...]) -> tuple[PolicyFinding, ...]:
    """Require exact download followed by official attested PyPI publication."""
    names = tuple(map(action_name, steps))
    findings = list(validate_pypi_shape(path, names))
    pypi = next(step for step in steps if action_name(step) == PYPI_ACTION)
    findings.extend(validate_pypi_attestation(path, pypi))
    findings.extend(validate_pypi_remote(path, names, steps))
    return tuple(findings)


def validate_pypi_shape(path: str, names: tuple[str, ...]) -> tuple[PolicyFinding, ...]:
    """Require one of the two reviewed source-free PyPI shapes."""
    if pypi_shape_is_allowed(names):
        return ()
    return (finding("pypi-shape", path, "expected exact source-free PyPI bridge"),)


def validate_pypi_attestation(path: str, step: WorkflowStep) -> tuple[PolicyFinding, ...]:
    """Require official PyPI attestations for every publication."""
    if scalar_text(step.with_.get("attestations")) == "true":
        return ()
    return (finding("pypi-attestation", path, "attestations must be true"),)


def validate_pypi_remote(
    path: str, names: tuple[str, ...], steps: tuple[WorkflowStep, ...]
) -> tuple[PolicyFinding, ...]:
    """Bind an optional pre-publication Dagger decision to the candidate SHA."""
    return validate_remote_dagger(path, steps) if DAGGER_ACTION in names else ()


def pypi_shape_is_allowed(names: tuple[str, ...]) -> bool:
    """Accept official PyPI directly or after an exact remote Dagger decision."""
    direct = (DOWNLOAD_ACTION, PYPI_ACTION)
    planned = (DOWNLOAD_ACTION, DAGGER_ACTION, PYPI_ACTION)
    return names in (direct, planned)


def validate_npm(path: str, steps: tuple[WorkflowStep, ...]) -> tuple[PolicyFinding, ...]:
    """Require exact remote Dagger npm publication with typed OIDC addresses."""
    names = tuple(map(action_name, steps))
    findings: list[PolicyFinding] = []
    if names != (DOWNLOAD_ACTION, DAGGER_ACTION):
        findings.append(finding("npm-shape", path, "expected download then remote Dagger"))
    findings.extend(validate_remote_dagger(path, steps))
    if not has_typed_oidc_publisher_step(steps):
        findings.append(finding("typed-oidc", path, "typed OIDC URL and token required"))
    return tuple(findings)


def has_typed_oidc_publisher_step(steps: tuple[WorkflowStep, ...]) -> bool:
    """Return whether the only Dagger publisher receives both OIDC addresses."""
    step = next((item for item in steps if action_name(item) == DAGGER_ACTION), None)
    return False if step is None else oidc_arguments_are_typed(step)


def validate_remote_dagger(path: str, steps: tuple[WorkflowStep, ...]) -> tuple[PolicyFinding, ...]:
    """Bind remote publisher code to the triggering candidate SHA."""
    step = next((item for item in steps if action_name(item) == DAGGER_ACTION), None)
    module = "" if step is None else scalar_text(step.with_.get("module"))
    if module.endswith("@${{ github.event.workflow_run.head_sha }}"):
        return ()
    return (finding("remote-module-identity", path, "remote Dagger module is not exact"),)


def oidc_arguments_are_typed(step: WorkflowStep) -> bool:
    """Require both GitHub OIDC addresses to cross the Dagger Secret boundary."""
    arguments = scalar_text(step.with_.get("args"))
    return (
        "--oidc-url=env:ACTIONS_ID_TOKEN_REQUEST_URL" in arguments
        and "--oidc-token=env:ACTIONS_ID_TOKEN_REQUEST_TOKEN" in arguments
    )


def validate_modules(modules: tuple[SourceFile, ...]) -> tuple[PolicyFinding, ...]:
    """Require explicit source construction and typed credential arguments."""
    python, typescript = split_modules(modules)
    trees = parse_python_modules(python)
    findings = validate_module_source(trees, typescript)
    findings.extend(validate_python_secrets(trees))
    findings.extend(validate_typescript_secrets(typescript))
    return tuple(findings)


def split_modules(
    modules: tuple[SourceFile, ...],
) -> tuple[tuple[SourceFile, ...], tuple[SourceFile, ...]]:
    """Split authored module sources by supported SDK language."""
    python = tuple(module for module in modules if module.path.endswith(".py"))
    typescript = tuple(module for module in modules if module.path.endswith(".ts"))
    return python, typescript


def parse_python_modules(
    modules: tuple[SourceFile, ...],
) -> tuple[tuple[SourceFile, ast.Module], ...]:
    """Parse every authored Python module against its exact path."""
    return tuple((module, ast.parse(module.text, filename=module.path)) for module in modules)


def validate_python_secrets(
    trees: tuple[tuple[SourceFile, ast.Module], ...],
) -> tuple[PolicyFinding, ...]:
    """Validate every credential-shaped Python argument."""
    return tuple(
        item for module, tree in trees for item in validate_secret_arguments(module.path, tree)
    )


def validate_module_source(
    trees: tuple[tuple[SourceFile, ast.Module], ...], typescript: tuple[SourceFile, ...]
) -> list[PolicyFinding]:
    """Require one explicit Directory boundary in either supported SDK language."""
    python_source = any(has_explicit_source(tree) for _, tree in trees)
    typescript_source = any(has_typescript_source(module.text) for module in typescript)
    if not any((python_source, typescript_source)):
        return [finding("explicit-source", "dagger.json", "typed source is absent")]
    return []


def has_typescript_source(source: str) -> bool:
    """Recognize a typed Workspace constructor assigning an explicit Directory."""
    source_field = re.search(r"\bsource\s*:\s*Directory\b", source) is not None
    workspace = re.search(r"constructor\s*\([^)]*:\s*Workspace\b", source) is not None
    directory = re.search(r"\bworkspace\.directory\s*\(", source) is not None
    return all((source_field, workspace, directory))


def validate_typescript_secrets(modules: tuple[SourceFile, ...]) -> tuple[PolicyFinding, ...]:
    """Reject credential-shaped TypeScript arguments typed as plain strings."""
    findings: list[PolicyFinding] = []
    pattern = re.compile(r"\b(?:token|secret|password|privateKey|apiKey)\??\s*:\s*string\b")
    for module in modules:
        if pattern.search(module.text):
            findings.append(finding("untyped-secret", module.path, "credential typed string"))
    return tuple(findings)


def has_explicit_source(tree: ast.Module) -> bool:
    """Accept DefaultPath fields or modern typed Workspace constructors."""
    source_field = any(is_source_field(node) for node in ast.walk(tree))
    workspace = any(is_workspace_directory(node) for node in ast.walk(tree))
    default_path = any(is_default_path_source(node) for node in ast.walk(tree))
    constructor = any((workspace, default_path))
    return all((source_field, constructor))


def is_source_field(node: ast.AST) -> bool:
    """Recognize an object field whose declared type is Dagger Directory."""
    if not isinstance(node, ast.AnnAssign):
        return False
    if not isinstance(node.target, ast.Name):
        return False
    annotation = ast.unparse(node.annotation)
    value = "" if node.value is None else ast.unparse(node.value)
    return all((node.target.id == "source", "Directory" in annotation, value.endswith("field()")))


def is_workspace_directory(node: ast.AST) -> bool:
    """Recognize construction from a typed Dagger Workspace snapshot."""
    if not is_create_function(node):
        return False
    typed = any(
        "Workspace" in ast.unparse(argument.annotation)
        for argument in node.args.args
        if argument.annotation
    )
    directory = any("workspace.directory" in ast.unparse(item) for item in ast.walk(node))
    return all((typed, directory))


def is_create_function(
    node: ast.AST,
) -> TypeIs[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Narrow one AST node to the modern source constructor."""
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "create"


def is_default_path_source(node: ast.AST) -> bool:
    """Recognize the older explicit DefaultPath Directory form."""
    return isinstance(node, ast.AnnAssign) and "DefaultPath" in ast.unparse(node.annotation)


def validate_secret_arguments(path: str, tree: ast.Module) -> tuple[PolicyFinding, ...]:
    """Reject credential-like Dagger function arguments not typed as Secret."""
    findings: list[PolicyFinding] = []
    for node in ast.walk(tree):
        if is_dagger_function(node):
            findings.extend(validate_function_secrets(path, node))
    return tuple(findings)


def is_dagger_function(
    node: ast.AST,
) -> TypeIs[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Recognize a function exposed through the Dagger API boundary."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    decorators = tuple(ast.unparse(item) for item in node.decorator_list)
    return any(item in {"function", "dagger.function"} for item in decorators)


def validate_function_secrets(
    path: str, node: ast.FunctionDef | ast.AsyncFunctionDef
) -> tuple[PolicyFinding, ...]:
    """Validate one function's credential-shaped arguments."""
    findings: list[PolicyFinding] = []
    for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
        annotation = "" if argument.annotation is None else ast.unparse(argument.annotation)
        if SENSITIVE_ARGUMENT.search(argument.arg) and "Secret" not in annotation:
            findings.append(finding("untyped-secret", path, argument.arg))
    return tuple(findings)


def validate_protection(
    snapshot: RepositorySnapshot, expectation: RepositoryExpectation
) -> tuple[PolicyFinding, ...]:
    """Require effective app-bound protection and matching exact-main checks."""
    findings: list[PolicyFinding] = []
    protection = snapshot.protection
    actual = tuple(check.context for check in protection.checks)
    if not contexts_are_strict(actual, expectation, protection):
        findings.append(finding("required-checks", snapshot.name, "strict contexts differ"))
    if any(check.app_id != APP_ID for check in protection.checks):
        findings.append(finding("required-check-app", snapshot.name, "wrong check app"))
    findings.extend(validate_protection_flags(snapshot.name, protection, expectation))
    findings.extend(validate_integrations(snapshot, expectation))
    return tuple(findings)


def contexts_are_strict(
    actual: tuple[str, ...], expectation: RepositoryExpectation, protection: Protection
) -> bool:
    """Require the exact reviewed status contexts in strict mode."""
    return all((actual == expectation.required_contexts, protection.strict))


def validate_protection_flags(
    name: str, protection: Protection, expectation: RepositoryExpectation
) -> tuple[PolicyFinding, ...]:
    """Require the reviewed solo-maintainer protection flags."""
    valid = all(
        (
            protection.enforce_admins,
            protection.approvals == 0,
            protection.conversation_resolution == expectation.conversation_resolution,
            protection.linear_history == expectation.linear_history,
            not protection.allow_force_pushes,
            not protection.allow_deletions,
        )
    )
    return () if valid else (finding("protection-flags", name, "flags differ"),)


def validate_integrations(
    snapshot: RepositorySnapshot, expectation: RepositoryExpectation
) -> tuple[PolicyFinding, ...]:
    """Require each protected context green from GitHub Actions on exact main."""
    for context in expectation.required_contexts:
        if not has_green_integration(snapshot, context):
            return (finding("main-integration", snapshot.name, context),)
    return ()


def has_green_integration(snapshot: RepositorySnapshot, context: str) -> bool:
    """Return whether one exact-main app-bound check is successful."""
    return any(
        run.name == context
        and run.app_id == APP_ID
        and run.head_sha == snapshot.sha
        and run.conclusion == "success"
        for run in snapshot.check_runs
    )


def validate_legacy(snapshot: RepositorySnapshot) -> tuple[PolicyFinding, ...]:
    """Block central deletion while a live consumer executes a legacy control."""
    return tuple(
        finding("legacy-central-reference", reference, "retired hseshadr/ci execution")
        for reference in snapshot.legacy_references
    )


def validate_control_plane(snapshot: RepositorySnapshot) -> tuple[PolicyFinding, ...]:
    """Reject managed CodeQL and independent non-advisory check applications."""
    findings: list[PolicyFinding] = []
    if snapshot.codeql_default_state == "configured":
        findings.append(finding("managed-codeql", snapshot.name, "default setup is configured"))
    external = tuple(app for app in snapshot.check_apps if app not in allowed_check_apps())
    if external:
        findings.append(finding("independent-check-app", snapshot.name, ", ".join(external)))
    return tuple(findings)


def allowed_check_apps() -> frozenset[str]:
    """Return execution ownership plus the reviewed advisory-only integration."""
    return frozenset(("github-actions", "gitguardian"))
