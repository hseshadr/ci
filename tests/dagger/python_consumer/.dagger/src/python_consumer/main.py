"""Typed Python consumer for the reusable portfolio modules."""

from __future__ import annotations

from typing import Final

import dagger
from dagger import check, dag, function, object_type

REPOSITORY: Final = "hseshadr/ci"
REPOSITORY_URL: Final = "https://github.com/hseshadr/ci.git"
COMMIT_SHA: Final = "1b2b18a38fc52801bdd2f3eb89d6616d847ef1fe"
CONSUMER: Final = f"{REPOSITORY}@{COMMIT_SHA}"
PRODUCER: Final = "b" * 40 + ":7"
ALLOWED_ROOTS: Final = ["dist"]
CACHE_NAMESPACE: Final = "fixture-python-v1"
SECRET_CANARY: Final = "-".join(("python", "private", "canary"))
ARTIFACT_NAME: Final = "python-artifact.txt"
TRANSPORT_MARKERS: Final = ("api.cloudflare.com", "api.github.com", "wrangler")


@object_type
class PythonConsumer:
    """Prove Python generated clients compose the exact local modules."""

    @function
    @check
    async def contract(self) -> str:
        """Run the positive foundation chain and provider tamper boundary."""
        await _source_guard()
        envelope = await _verified_envelope()
        secret = dag.set_secret("python-fixture-secret", SECRET_CANARY)
        _typed_evidence(secret)
        await _provider_rejects_tamper(envelope, secret)
        await dag.cache_volume(CACHE_NAMESPACE).id()
        return "python fixture passed"


async def _source_guard() -> None:
    history = dag.git(REPOSITORY_URL).commit(COMMIT_SHA).tree(depth=0, include_tags=True)
    source = dag.foundation().source(history, REPOSITORY, COMMIT_SHA)
    await source.digest()
    await dag.foundation().guard(source, REPOSITORY, COMMIT_SHA).sync()


async def _verified_envelope() -> dagger.Directory:
    artifact = dag.directory().with_new_file(f"dist/{ARTIFACT_NAME}", "python artifact")
    envelope = dag.foundation().envelope(artifact, CONSUMER, PRODUCER, ALLOWED_ROOTS)
    verified = dag.foundation().verify_envelope(envelope, CONSUMER, PRODUCER, ALLOWED_ROOTS)
    entries = await verified.directory("dist").entries()
    if entries != [ARTIFACT_NAME]:
        raise ValueError("verified Python artifact boundary differs")
    return envelope


def _typed_evidence(secret: dagger.Secret) -> None:
    evidence: dagger.FoundationCheckEvidence = dag.foundation().green_main(secret, REPOSITORY)
    if evidence is None:
        raise ValueError("generated evidence type was unavailable")


async def _provider_rejects_tamper(envelope: dagger.Directory, secret: dagger.Secret) -> None:
    tampered = envelope.with_new_file(f"artifact/dist/{ARTIFACT_NAME}", "tampered")
    try:
        await _preflight(tampered, secret)
    except dagger.QueryError as error:
        _require_safe_tamper_error(str(error))
        return
    raise ValueError("provider accepted a tampered envelope")


# fmt: off
async def _preflight(envelope: dagger.Directory, secret: dagger.Secret) -> str:
    # The generated SDK boundary has fifteen typed positional inputs.
    return await dag.cloudflare_pages().preflight(
        envelope, secret, secret, secret, "7", 1, REPOSITORY,
        "ci", "main", "example.invalid", "dist", [], CONSUMER,
        PRODUCER, ALLOWED_ROOTS,
    )
# fmt: on


def _require_safe_tamper_error(message: str) -> None:
    if "artifact bytes or modes differ from manifest" not in message.lower():
        raise ValueError("provider reached transport before envelope rejection")
    if any(marker in message for marker in TRANSPORT_MARKERS):
        raise ValueError("provider emitted a transport marker for a rejected envelope")
    if SECRET_CANARY in message:
        raise ValueError("provider error disclosed the typed secret")
