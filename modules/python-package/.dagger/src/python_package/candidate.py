"""Canonical source-free publication candidate evidence."""

from __future__ import annotations

import json
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from .distributions import DistributionObservation, DistributionSet
from .identity import CandidateIdentity

CLOSED_MODEL: Final = ConfigDict(extra="forbid", frozen=True, strict=True)


class CandidateDistribution(BaseModel):  # type: ignore[explicit-any]  # Pydantic base stub
    """Closed distribution evidence embedded in the candidate manifest."""

    model_config = CLOSED_MODEL
    filename: str
    sha256: str
    kind: Literal["wheel", "sdist"]
    member_count: int = Field(gt=0, le=4_096)
    size: int = Field(gt=0, le=32 * 1_024 * 1_024)


class CandidateManifest(BaseModel):  # type: ignore[explicit-any]  # Pydantic base stub
    """Exact non-secret handoff from Dagger to a source-free publisher job."""

    model_config = CLOSED_MODEL
    schema_version: Literal[1] = 1
    repository: str
    commit_sha: str
    tag: str
    project: str
    version: str
    central_module_sha: str
    workflow_run_id: str
    run_attempt: int = Field(gt=0, le=1_000)
    distributions: tuple[CandidateDistribution, CandidateDistribution]

    def canonical_json(self) -> str:
        """Serialize with one deterministic JSON representation."""
        return json.dumps(
            self.model_dump(), ensure_ascii=True, separators=(",", ":"), sort_keys=True
        )

    @classmethod
    def from_canonical_json(cls, value: str) -> CandidateManifest:
        """Parse a closed candidate only if its byte representation is canonical."""
        result = cls.model_validate_json(value)
        if result.canonical_json() != value:
            raise ValueError("candidate manifest must use canonical JSON")
        return result


def manifest_from(identity: CandidateIdentity, products: DistributionSet) -> CandidateManifest:
    """Create canonical candidate evidence from validated observations."""
    return CandidateManifest(
        repository=identity.repository.value,
        commit_sha=identity.commit.value,
        tag=products.tag,
        project=products.project,
        version=products.version,
        central_module_sha=identity.central_module.value,
        workflow_run_id=identity.workflow.run_id,
        run_attempt=identity.workflow.attempt,
        distributions=_manifest_distributions(products),
    )


def require_manifest_binding(
    manifest: CandidateManifest, identity: CandidateIdentity, products: DistributionSet
) -> None:
    """Require candidate metadata to match the caller and observed artifact bytes."""
    expected = manifest_from(identity, products)
    if manifest != expected:
        raise ValueError("candidate manifest identity differs from expected artifact")


def _candidate_distribution(item: DistributionObservation) -> CandidateDistribution:
    return CandidateDistribution(
        filename=item.filename,
        sha256=item.sha256,
        kind=item.kind,
        member_count=item.member_count,
        size=item.size,
    )


def _manifest_distributions(
    products: DistributionSet,
) -> tuple[CandidateDistribution, CandidateDistribution]:
    return (
        _candidate_distribution(products.wheel),
        _candidate_distribution(products.sdist),
    )
