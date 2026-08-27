from __future__ import annotations

import inspect

from ci.main import GITLEAKS_IMAGE, PYTHON_IMAGE, SOURCE_EXCLUDES, Ci


def test_should_require_explicit_workspace_and_typed_fleet_secret() -> None:
    # Given the public central Dagger object
    create = inspect.signature(Ci.create)
    ci = inspect.signature(Ci.ci)
    fleet = inspect.signature(Ci.fleet)

    # When its source and hosted credential boundaries are inspected
    workspace_type = str(create.parameters["workspace"].annotation)
    token_type = str(fleet.parameters["github_token"].annotation)
    ci_token_type = str(ci.parameters["github_token"].annotation)

    # Then workspace bytes and the fleet credential are explicit typed inputs
    assert "Workspace" in workspace_type
    assert "Secret" in token_type
    assert "Secret" in ci_token_type
    assert "include_central" in fleet.parameters


def test_should_pin_every_base_image_by_digest() -> None:
    # Given every container image executing repository-authored policy
    images = (PYTHON_IMAGE, GITLEAKS_IMAGE)

    # When immutable identity is checked
    pinned = tuple("@sha256:" in image for image in images)

    # Then no mutable image tag controls CI execution
    assert all(pinned)


def test_should_give_zizmor_explicit_workflow_inputs() -> None:
    # Given Zizmor's Dagger adapter
    adapter = inspect.getsource(Ci._zizmor)

    # When its audit inputs are inspected
    required_inputs = ("../.github/workflows", "../.github/dependabot.yml")

    # Then collection cannot silently depend on repository-root discovery
    assert all(path in adapter for path in required_inputs)
    assert '"--min-severity", "medium"' in " ".join(adapter.split())


def test_should_not_require_generated_sdk_inside_explicit_source() -> None:
    # Given hosted Workspace bytes, which never contain Dagger's generated SDK
    repository = inspect.getsource(Ci._repository)

    # When the inner Python environment is assembled
    generated_sdk = ("current_module", 'directory("sdk")', '"/src/.dagger/sdk"')

    # Then caller source stays explicit while Dagger supplies its generated toolchain
    assert ".dagger/sdk" in SOURCE_EXCLUDES
    assert all(fragment in repository for fragment in generated_sdk)
    assert '"--frozen"' in repository
    assert '"--all-groups"' in repository
