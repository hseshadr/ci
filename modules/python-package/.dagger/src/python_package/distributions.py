"""Closed validation for built wheel and source distributions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

from .identity import ProjectName

SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
VERSION_PATTERN: Final = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
MAX_DISTRIBUTION_BYTES: Final = 32 * 1_024 * 1_024
MAX_DISTRIBUTION_MEMBERS: Final = 4_096
PRODUCT_COUNTS: Final = (2, 1, 1)


class DistributionError(ValueError):
    """Raised when built package products violate the release contract."""


@dataclass(frozen=True)
class DistributionObservation:
    """Observed non-secret metadata for one built distribution."""

    filename: str
    sha256: str
    kind: Literal["wheel", "sdist"]
    project: str
    version: str
    member_count: int
    size: int


@dataclass(frozen=True)
class DistributionSet:
    """Exactly one pure-Python wheel and one source distribution."""

    project: str
    version: str
    wheel: DistributionObservation
    sdist: DistributionObservation

    @classmethod
    def parse(
        cls, observed: tuple[DistributionObservation, ...], expected_project: str
    ) -> DistributionSet:
        """Fail closed over a bounded two-file product inventory."""
        project = ProjectName(expected_project).value
        wheel, sdist = _split_products(observed)
        _validate_observation(wheel, project)
        _validate_observation(sdist, project)
        _require_same_version(wheel, sdist)
        _require_filenames(wheel, sdist, project)
        return cls(project, wheel.version, wheel, sdist)

    @property
    def tag(self) -> str:
        return f"v{self.version}"

    @property
    def observations(self) -> tuple[DistributionObservation, ...]:
        return self.wheel, self.sdist


def _split_products(
    observed: tuple[DistributionObservation, ...],
) -> tuple[DistributionObservation, DistributionObservation]:
    wheels = tuple(filter(_is_wheel, observed))
    sdists = tuple(filter(_is_sdist, observed))
    if (len(observed), len(wheels), len(sdists)) != PRODUCT_COUNTS:
        raise DistributionError("candidate requires exactly one wheel and one sdist")
    return wheels[0], sdists[0]


def _is_wheel(item: DistributionObservation) -> bool:
    return item.kind == "wheel"


def _is_sdist(item: DistributionObservation) -> bool:
    return item.kind == "sdist"


def _validate_observation(item: DistributionObservation, project: str) -> None:
    valid_digest = SHA256_PATTERN.fullmatch(item.sha256) is not None
    valid_count = 0 < item.member_count <= MAX_DISTRIBUTION_MEMBERS
    valid_size = 0 < item.size <= MAX_DISTRIBUTION_BYTES
    if not all((valid_digest, valid_count, valid_size)):
        raise DistributionError("distribution evidence is malformed or exceeds its bound")
    if item.project != project:
        raise DistributionError("distribution project identity differs")
    if VERSION_PATTERN.fullmatch(item.version) is None:
        raise DistributionError("distribution version must be stable semantic MAJOR.MINOR.PATCH")


def _require_same_version(wheel: DistributionObservation, sdist: DistributionObservation) -> None:
    if wheel.version != sdist.version:
        raise DistributionError("wheel and sdist versions differ")


def _require_filenames(
    wheel: DistributionObservation, sdist: DistributionObservation, project: str
) -> None:
    stem = project.replace("-", "_")
    pure_wheel = f"{stem}-{wheel.version}-py3-none-any.whl"
    source_names = {f"{stem}-{sdist.version}.tar.gz", f"{project}-{sdist.version}.tar.gz"}
    if wheel.filename != pure_wheel or sdist.filename not in source_names:
        raise DistributionError("distribution filenames differ from pure package metadata")
