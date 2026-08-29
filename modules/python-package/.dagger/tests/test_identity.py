from __future__ import annotations

import pytest

from python_package.identity import CandidateIdentity, PackageIdentity, ProjectName

SHA = "a" * 40
CENTRAL_SHA = "b" * 40


def test_should_bind_canonical_candidate_identity() -> None:
    # Given canonical workflow and package coordinates
    identity = _identity()

    # When its derived release identity is projected
    consumer, producer = identity.consumer_identity, identity.producing_identity

    # Then the Foundation and retry identities remain distinct and exact
    assert consumer == f"hseshadr/edgeproc-core@{SHA}"
    assert producer == f"{CENTRAL_SHA}:6100"
    assert identity.artifact_suffix == f"{SHA}-6100-2"


@pytest.mark.parametrize(
    "values",
    (
        ("invalid", SHA, "edgeproc-core", CENTRAL_SHA, "1", 1),
        ("hseshadr/edgeproc-core.git", SHA, "edgeproc-core", CENTRAL_SHA, "1", 1),
        ("hseshadr/edgeproc-core", SHA.upper(), "edgeproc-core", CENTRAL_SHA, "1", 1),
        ("hseshadr/edgeproc-core", SHA, "EdgeProc_Core", CENTRAL_SHA, "1", 1),
        ("hseshadr/edgeproc-core", SHA, "edgeproc-core", "b" * 39, "1", 1),
        ("hseshadr/edgeproc-core", SHA, "edgeproc-core", CENTRAL_SHA, "0", 1),
        ("hseshadr/edgeproc-core", SHA, "edgeproc-core", CENTRAL_SHA, "01", 1),
        ("hseshadr/edgeproc-core", SHA, "edgeproc-core", CENTRAL_SHA, "1", 0),
        ("hseshadr/edgeproc-core", SHA, "edgeproc-core", CENTRAL_SHA, "1", 1001),
    ),
)
def test_should_reject_noncanonical_candidate_identity(
    values: tuple[str, str, str, str, str, int],
) -> None:
    # Given / When / Then an untrusted boundary value differs from its closed contract
    repository, commit, project, central, run_id, attempt = values
    with pytest.raises(ValueError):
        package = PackageIdentity.parse(repository, commit, project)
        CandidateIdentity.from_package(package, central, run_id, attempt)


@pytest.mark.parametrize("value", ("edge.proc", "edge_proc", "-edge", "edge-", ""))
def test_should_require_pep503_canonical_project_input(value: str) -> None:
    # Given / When / Then project identity cannot have a second spelling
    with pytest.raises(ValueError, match="canonical"):
        ProjectName(value)


def _identity() -> CandidateIdentity:
    package = PackageIdentity.parse("hseshadr/edgeproc-core", SHA, "edgeproc-core")
    return CandidateIdentity.from_package(package, CENTRAL_SHA, "6100", 2)
