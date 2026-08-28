# Reusable Dagger modules

**TL;DR:** Install each shared module at one exact central commit, pass source and credentials
through typed Dagger boundaries, and let the module fail closed before deployment.

A **Dagger lego** is a typed reusable Dagger module installed at an immutable commit SHA. The
SHA is the trust boundary: reviewers approve exact module bytes, and every consumer runs those
same bytes until it deliberately upgrades.

## Quickstart

Prerequisites: Git, Bash, Dagger 0.21.8, and an existing Dagger module in the consumer repository.
After the guarded central release is merged, run this from the consumer module root:

```bash
set -euo pipefail

FOUNDATION_SHA="$(
  git ls-remote https://github.com/hseshadr/ci.git refs/heads/main | cut -f1
)"
if [[ ! "$FOUNDATION_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'central main did not resolve to one lowercase 40-character SHA\n' >&2
  exit 1
fi

dagger install \
  "github.com/hseshadr/ci/modules/portfolio-foundation@$FOUNDATION_SHA" \
  --name foundation
dagger develop
```

Review the captured SHA before committing. The resulting dependency in `dagger.json` has this
shape (the example SHA is illustrative):

```json
{
  "dependencies": [
    {
      "name": "foundation",
      "source": "github.com/hseshadr/ci/modules/portfolio-foundation@0123456789abcdef0123456789abcdef01234567",
      "pin": "0123456789abcdef0123456789abcdef01234567"
    }
  ]
}
```

`dagger install` generates both fields. Review and commit them together: `pin` must equal the
40-lowercase-hex suffix of `source`. Fleet policy rejects a missing or different generated pin,
plus `main`, `latest`, version tags, shortened SHAs, uppercase hexadecimal, and every other
mutable or non-canonical dependency reference.

This remote-pin rule applies to consumers. Central CI intentionally keeps its foundation as a
local same-tree dependency so it validates the module bytes in the current commit; a remote
self-pin would instead validate an older published copy.

Pages consumers install the provider from the **same reviewed commit** and regenerate once:

```bash
dagger install \
  "github.com/hseshadr/ci/modules/cloudflare-pages@$FOUNDATION_SHA" \
  --name cloudflare-pages
dagger develop
```

## One realistic consumer flow

This complete Python Dagger object shows the trust chain without hiding any identity. Its release
function is async and non-cacheable; every credential remains a typed `dagger.Secret`. Replace the
illustrative constants with the exact consumer and Pages target before running it.

```python
import dagger
from dagger import dag, function, object_type

REPOSITORY = "owner/service"
REPOSITORY_URL = "https://github.com/owner/service.git"
COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"
CONSUMER = f"{REPOSITORY}@{COMMIT_SHA}"
ALLOWED_ROOTS = ["dist"]


@object_type
class Delivery:
    @function(cache="never")  # type: ignore[call-overload,untyped-decorator]
    async def release(
        self,
        artifact: dagger.Directory,
        github_token: dagger.Secret,
        cloudflare_api_token: dagger.Secret,
        cloudflare_account_id: dagger.Secret,
        workflow_run_id: str,
        run_attempt: int,
    ) -> str:
        producing_identity = f"{COMMIT_SHA}:{workflow_run_id}"
        history = dag.git(REPOSITORY_URL).commit(COMMIT_SHA).tree(
            depth=0, include_tags=True
        )
        source = dag.foundation().source(history, REPOSITORY, COMMIT_SHA)
        await dag.foundation().guard(source, REPOSITORY, COMMIT_SHA).sync()
        envelope = dag.foundation().envelope(
            artifact, CONSUMER, producing_identity, ALLOWED_ROOTS
        )
        verified = dag.foundation().verify_envelope(
            envelope, CONSUMER, producing_identity, ALLOWED_ROOTS
        )
        await verified.directory("dist").digest()
        evidence = dag.cloudflare_pages().deploy(
            envelope=envelope,
            github_token=github_token,
            cloudflare_api_token=cloudflare_api_token,
            cloudflare_account_id=cloudflare_account_id,
            workflow_run_id=workflow_run_id,
            run_attempt=run_attempt,
            repository=REPOSITORY,
            project="service-production",
            production_branch="main",
            live_domain="service.example.com",
            deploy_root="dist",
            domains=["www.service.example.com"],
            consumer_identity=CONSUMER,
            producing_identity=producing_identity,
            allowed_roots=ALLOWED_ROOTS,
        )
        return await evidence.deployment_id()
```

Do not call `plaintext()`, put a token in a string argument, or reconstruct provider authorization
from JSON. The returned deployment ID forces evaluation of the typed provider result.

The provider resolves exact-current-`main` green Dagger evidence internally. A caller cannot
authorize a deployment with stale or caller-authored evidence.

## Secrets and the production environment

GitHub Actions injects credentials into Dagger as typed `Secret` arguments. After a repository
adopts the Pages provider, put `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` in a
main-restricted `production` GitHub Environment and bind the deploy job to that environment.
Keep the workflow's `GITHUB_TOKEN` permission-minimal and pass it through the same typed boundary.

Fleet policy validates only secret **names**, environment binding, and typed use. It never reads
secret values. GitHub does not reveal existing secret values, so moving repository secrets into
the Environment requires secure re-entry; delete the old repository secrets only after an
environment-backed production proof succeeds.

Secret values never belong in command-line arguments, logs, Dagger outputs, artifacts, or
persistent caches. The modules mount provider credentials and responses only in ephemeral Dagger
storage and return non-secret evidence.

## Pages delivery safety and ordering

Every provider entrypoint requires a foundation-verified envelope and exact-current-`main` green
Dagger evidence for the requested GitHub Actions `run_id` and `run_attempt`. The envelope is
revalidated before provider transport, so changed bytes, modes, roots, identities, or checksums
fail locally.

A deployment then follows one bounded sequence:

1. Require exact-current-`main` green Dagger evidence and the verified artifact envelope.
2. Read the Pages project and recent production deployments, disable Git integration with one
   non-retried update, and immediately read the project back.
3. Run one direct upload with pinned Wrangler 4.103.0, using only the verified `deploy_root`.
4. Bind convergence to the deployment ID created by that upload; an older same-SHA deployment
   cannot satisfy the attempt.
5. Verify the created Pages hostname, source SHA, and required project-domain bindings before
   returning non-secret evidence.

Wrangler has no Pages dry-run. Tests therefore use a local TLS mock that exercises the real
request and Wrangler boundaries without access to Cloudflare and cannot mutate Cloudflare. The
cross-language fixtures also tamper with an envelope and prove rejection occurs before GitHub or
Cloudflare transport.

## Composition proofs

Warm proof from a repository checkout:

```bash
set -euo pipefail
(cd tests/dagger/python_consumer && DAGGER_NO_NAG=1 dagger call contract)
(cd tests/dagger/typescript_consumer && DAGGER_NO_NAG=1 dagger call contract)
DAGGER_NO_NAG=1 dagger call module-fixtures
```

The first two commands exercise the generated Python and TypeScript clients independently. The
root command dynamically loads both consumer modules through Dagger's typed module API.

For an isolated cold proof, start an engine with its own empty state volume, run the same root
graph, and remove only those named resources:

```bash
set -euo pipefail
COLD_ENGINE='registry.dagger.io/engine@sha256:c9c1a0a6546380983d42e8d75adde070a2a0935c54b498d8bc9045d9cb2ee336'
COLD_NAME="dagger-module-cold-$$"
COLD_VOLUME="${COLD_NAME}-state"

cleanup() {
  docker rm --force "$COLD_NAME" >/dev/null 2>&1 || true
  docker volume rm "$COLD_VOLUME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker volume create "$COLD_VOLUME" >/dev/null
docker run --detach --privileged --name "$COLD_NAME" \
  --volume "$COLD_VOLUME:/var/lib/dagger" \
  "$COLD_ENGINE" >/dev/null
_EXPERIMENTAL_DAGGER_RUNNER_HOST="container://$COLD_NAME" \
  DAGGER_NO_NAG=1 dagger call module-fixtures
```

Do not prune or reset a shared engine, cache, image, container, or volume to simulate cold state.

## Quality gates

Run the root and both module contracts before proposing an upgrade:

```bash
set -euo pipefail
uv run --directory .dagger poe gate
uv run --directory .dagger poe audit
uv run --directory modules/portfolio-foundation/.dagger poe gate
uv run --directory modules/portfolio-foundation/.dagger poe audit
uv run --directory modules/cloudflare-pages/.dagger poe gate
uv run --directory modules/cloudflare-pages/.dagger poe audit
DAGGER_NO_NAG=1 dagger call module-fixtures
git diff --check
```

The hosted release additionally requires exact-head root CI, module fixtures, security, and
authoritative fleet evidence before merge, followed by exact-main evidence after merge. Revalidate
the merged SHA against `^[0-9a-f]{40}$` and record it in the durable release ledger; a temporary
file alone is not release evidence.

## Release status

Shipped in this central change:

- reusable foundation and Pages module implementations;
- exact-SHA dependency and production-environment policy;
- deterministic Python and TypeScript composition fixtures;
- real local-TLS provider tests and isolated cold-engine proof.

Still pending in the EdgeReco canary rollout (Tasks 10–14): consumer hardening, foundation shadow
adoption, provider shadow adoption, secure Environment secret migration, exact live deployment
proof, and removal of proven duplicate local mechanics. No fleet-wide adoption, production
deployment, package publication, version tag, or registry release is claimed here.
