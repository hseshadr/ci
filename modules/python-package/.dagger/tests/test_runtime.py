from __future__ import annotations

import inspect
import json

import pytest

from python_package.identity import CandidateIdentity, PackageIdentity
from python_package.runtime import (
    PYTHON_IMAGE,
    UV_IMAGE,
    FoundationGreenEvidence,
    parse_observations,
    require_green_binding,
)


def test_should_parse_only_closed_distribution_probe_payload() -> None:
    # Given canonical observations from the static archive probe
    payload = json.dumps(
        [_observation("wheel"), _observation("sdist")], separators=(",", ":"), sort_keys=True
    )

    # When the container boundary is parsed
    observed = parse_observations(payload)

    # Then exactly typed non-secret observations cross into orchestration
    assert tuple(item.kind for item in observed) == ("wheel", "sdist")
    assert all(item.project == "edgeproc-core" for item in observed)


def test_should_reject_extra_distribution_probe_fields() -> None:
    # Given a probe payload containing an unrecognized path-like control
    wheel = _observation("wheel") | {"path": "caller-controlled"}
    payload = json.dumps([wheel, _observation("sdist")])

    # When / Then orchestration rejects schema drift
    with pytest.raises(ValueError, match="schema"):
        parse_observations(payload)


def test_should_bind_foundation_green_evidence_to_exact_main_commit() -> None:
    # Given exact Foundation evidence for the requested repository commit
    evidence = FoundationGreenEvidence.model_validate(_green_payload())

    # When / Then its immutable source binding is accepted
    require_green_binding(evidence, _identity())


def test_should_reject_green_evidence_for_another_commit() -> None:
    # Given exact-green evidence for a different immutable commit
    payload = _green_payload() | {"commit_sha": "c" * 40}
    evidence = FoundationGreenEvidence.model_validate(payload)

    # When / Then release-candidate construction fails closed
    with pytest.raises(ValueError, match="does not bind"):
        require_green_binding(evidence, _identity())


def test_should_pin_toolchain_and_exclude_secret_or_publisher_from_build_graph() -> None:
    # Given central runtime source and every build tool image
    source = inspect.getsource(__import__("python_package.runtime", fromlist=["runtime"]))

    # When the unprivileged build graph is inspected
    images = (PYTHON_IMAGE, UV_IMAGE)

    # Then tools are immutable and no registry credential can enter a build container
    assert all("@sha256:" in image for image in images)
    assert "with_secret_variable" not in source
    assert "OIDC" not in source and "upload.pypi.org" not in source


def _observation(kind: str) -> dict[str, object]:
    suffix = "py3-none-any.whl" if kind == "wheel" else "tar.gz"
    return {
        "filename": f"edgeproc_core-0.4.2-{suffix}",
        "kind": kind,
        "member_count": 20,
        "project": "edgeproc-core",
        "sha256": "d" * 64,
        "size": 40_000,
        "version": "0.4.2",
    }


def _green_payload() -> dict[str, object]:
    return {
        "app_id": 15368,
        "branch": "main",
        "check_completed_at": "2026-08-29T00:00:01Z",
        "check_name": "Dagger",
        "check_run_id": "1",
        "check_started_at": "2026-08-29T00:00:00Z",
        "check_suite_id": "2",
        "commit_sha": "a" * 40,
        "repository": "hseshadr/edgeproc-core",
        "run_attempt": 1,
        "workflow_created_at": "2026-08-29T00:00:00Z",
        "workflow_job_id": "3",
        "workflow_name": "Dagger",
        "workflow_path": ".github/workflows/dagger.yml",
        "workflow_run_id": "6100",
        "workflow_started_at": "2026-08-29T00:00:00Z",
        "workflow_updated_at": "2026-08-29T00:00:01Z",
    }


def _identity() -> CandidateIdentity:
    package = PackageIdentity.parse("hseshadr/edgeproc-core", "a" * 40, "edgeproc-core")
    return CandidateIdentity.from_package(package, "b" * 40, "6100", 1)
