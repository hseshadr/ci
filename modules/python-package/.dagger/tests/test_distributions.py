from __future__ import annotations

import pytest

from python_package.distributions import (
    DistributionError,
    DistributionObservation,
    DistributionSet,
)


def _wheel(
    *,
    filename: str = "edgeproc_core-0.4.2-py3-none-any.whl",
    project: str = "edgeproc-core",
    version: str = "0.4.2",
    member_count: int = 21,
    size: int = 37_231,
) -> DistributionObservation:
    return DistributionObservation(
        filename=filename,
        sha256="a" * 64,
        kind="wheel",
        project=project,
        version=version,
        member_count=member_count,
        size=size,
    )


def _sdist(*, project: str = "edgeproc-core", version: str = "0.4.2") -> DistributionObservation:
    return DistributionObservation(
        filename="edgeproc_core-0.4.2.tar.gz",
        sha256="b" * 64,
        kind="sdist",
        project=project,
        version=version,
        member_count=58,
        size=100_700,
    )


def test_should_accept_one_pure_wheel_and_one_sdist() -> None:
    # Given two bounded distributions with identical package metadata
    observed = (_wheel(), _sdist())

    # When they are validated as one release unit
    result = DistributionSet.parse(observed, "edgeproc-core")

    # Then the version and tag are derived from built metadata
    assert result.version == "0.4.2"
    assert result.tag == "v0.4.2"
    assert result.wheel.filename.endswith("-py3-none-any.whl")


@pytest.mark.parametrize(
    "observed",
    (
        (_wheel(),),
        (_wheel(), _wheel()),
        (_wheel(version="0.4.3"), _sdist()),
        (_wheel(project="other"), _sdist()),
        (_wheel(filename="edgeproc_core-0.4.2-cp313-cp313-manylinux.whl"), _sdist()),
        (_wheel(member_count=4097), _sdist()),
        (_wheel(size=33_554_433), _sdist()),
    ),
)
def test_should_fail_closed_on_invalid_distribution_sets(
    observed: tuple[DistributionObservation, ...],
) -> None:
    # Given / When / Then malformed or unbounded products cannot become a candidate
    with pytest.raises(DistributionError):
        DistributionSet.parse(observed, "edgeproc-core")


@pytest.mark.parametrize("version", ("1.0", "v1.0.0", "1.0.0rc1", "1.0.0+local"))
def test_should_allow_only_stable_semantic_versions(version: str) -> None:
    # Given / When / Then release tags are unambiguous and publication-safe
    with pytest.raises(DistributionError, match="stable semantic"):
        DistributionSet.parse((_wheel(version=version), _sdist(version=version)), "edgeproc-core")
