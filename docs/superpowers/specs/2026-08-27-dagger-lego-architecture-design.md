# Portfolio Dagger Lego Architecture

## TL;DR

The portfolio will use one versioned set of reusable Dagger modules from
`hseshadr/ci`. Each product repository will pin those modules to immutable Git
commit SHAs and keep only a thin, typed adapter for its product-specific tests,
artifact construction, and live verification.

EdgeReco is the executable golden reference. A shared component is not rolled
out elsewhere until EdgeReco has passed local, hosted, cold-cache, production,
and rollback proofs. Rollout then proceeds one repository archetype at a time.

GitHub Actions remains event ingress and secret storage. Build, test, security,
release decisions, deployment, and verification are Dagger functions. Pinned
artifact upload/download actions may transport exact candidate bytes between
privilege-separated jobs. PyPI's official trusted-publisher action may perform
the final upload only after Dagger approves the exact candidate; it does not
build, test, install, or choose what to publish.

## Why This Exists

The fleet now has Dagger-owned delivery in every repository, but much of the
implementation was independently developed. That caused the same failure
classes to recur: stale-head races, incomplete GitHub API schemas, writable-path
assumptions, Cloudflare schema drift, eventual consistency, mutable package
caches, and wall-clock test races.

The next step is not another repository migration. It is to turn the proven
behavior into reusable components with stable typed boundaries, then consume
those components everywhere.

## Goals

1. Fix common CI/CD behavior once and promote it through immutable dependency
   pins.
2. Keep GitHub workflows thin: pinned checkout, pinned Dagger, and narrowly
   approved byte-transport or official-publisher gateways.
3. Preserve repository-specific product tests and release semantics.
4. Make source, artifact, deployment, and publication identities exact and
   independently verifiable.
5. Automate compatibility testing, canarying, consumer pin PRs, drift checks,
   deployment, and live verification.
6. Keep every production secret scoped to the smallest GitHub Environment and
   Dagger function that requires it.
7. Make rollback a pin reversion rather than an emergency rewrite.

## Non-Goals

- A generic configuration language for arbitrary project commands.
- One monolithic module containing every product's build logic.
- Automatic merging of dependency or module-update PRs.
- Moving secret values into repository files, Dagger configuration, artifacts,
  cache volumes, command arguments, or logs.
- Replacing supported registry publisher interfaces with private protocols.
- Upgrading the Dagger engine during the first extraction.

## Architectural Decision

Use exact-SHA remote Dagger module dependencies with thin local adapters.

Dagger's supported module dependency mechanism resolves a Git reference to a
full commit SHA in `dagger.json` and generates typed dependency clients. Each
consumer commits the resolved dependency, generated bindings, and language
lockfiles. Tags may label releases for humans, but production consumers pin the
resolved full SHA.

Vendoring is a documented break-glass fallback only. Templates may scaffold a
new repository but are not the reuse mechanism. Central policy remains an
independent enforcement layer.

## System Shape

```text
GitHub event
    |
    v
thin pinned workflow
    |
    v
repo-local Dagger composition
    |-- shared exact-SHA Dagger legos
    |     |-- foundation
    |     |-- repository guard
    |     |-- artifact envelope
    |     |-- GitHub evidence
    |     |-- Cloudflare Pages delivery
    |     `-- package publisher boundaries
    |
    `-- repo-owned typed adapters
          |-- product quality and integration tests
          |-- product artifact construction
          `-- domain-specific live verification
```

The shared module owns mechanics and invariants. The local adapter owns product
meaning. Shared APIs accept typed values, not arbitrary shell commands.

## Shared Module Layout

The modules live below `modules/` in `hseshadr/ci` so each trust boundary can be
pinned and reviewed independently.

### `modules/foundation`

Responsibilities:

- Filter an explicit caller `Directory` using a typed source policy.
- Validate lowercase 40-character commit identities.
- Fetch an exact remote commit tree and complete canonical Git history.
- Compare hosted workspace bytes with the claimed exact source.
- Provide managed scratch directories and module/repository-scoped caches.
- Expose digest-pinned tool containers and a compatibility tuple.

It returns typed source and history evidence. It never receives production
credentials.

### `modules/repository-guard`

Responsibilities:

- Validate workflow ingress and immutable action pins.
- Run actionlint across both `.yml` and `.yaml`.
- Run snapshot and full-history Gitleaks with a runtime-generated canary.
- Run dependency and workflow security tools through pinned containers.
- Resolve and require one successful exact-main Dagger check.
- Parse authoritative GitHub states, including nullable in-progress check
  conclusions, without treating incomplete checks as green evidence.

### `modules/artifact-envelope`

Responsibilities:

- Accept a typed product artifact `Directory`.
- Reject unexpected paths and symlinks where the artifact contract forbids
  them.
- Produce a canonical file inventory and per-file SHA-256 manifest.
- Bind the artifact to consumer SHA, shared-module SHA, engine version,
  toolchain tuple, and producing run identity.
- Revalidate the complete envelope before any privileged mutation.

The durable identity is the canonical manifest, not a Dagger object ID.

### `modules/github-evidence`

Responsibilities:

- Validate `owner/repository`, branch, ref, workflow, run, and check-app
  identities as typed values.
- Resolve exact green main at execution time.
- Reject fork, stale-head, wrong-workflow, wrong-app, incomplete, and duplicate
  evidence.
- Produce typed evidence consumed by deploy and publisher modules.

### `modules/cloudflare-pages`

Responsibilities:

- Bind repository, Pages project, production branch, live domain, and canonical
  redirect into one validated target.
- Preflight the documented raw Cloudflare API before upload.
- Disable production and preview Git deployments before direct upload.
- Upload one exact artifact with pinned Wrangler.
- Poll the documented deployments endpoint with bounded backoff.
- Require exact full source SHA, production environment, deploy stage, and
  success state.
- Return provider evidence for a repo-owned live verifier.

The local adapter supplies product-specific public routes, signed bundle
checks, zero-egress journeys, and other domain evidence.

### `modules/npm-publisher`

Responsibilities:

- Consume a typed candidate `Directory` and typed GitHub OIDC credentials.
- Revalidate inventory, checksum, package name, version, source SHA, and run
  identity.
- Disable lifecycle scripts.
- Publish the exact tarball without rebuilding.
- Verify the resulting registry provenance.
- Disable function caching and persistent mutable caches for every effectful
  operation.

### `modules/pypi-decision`

Responsibilities:

- Revalidate the exact wheel/sdist candidate and publication decision.
- Confirm the expected version is absent or already byte-identical.
- Return a typed allow/deny decision and exact candidate digest.

PyPA's official trusted-publisher action is the supported final PyPI upload
gateway. It receives only the Dagger-approved candidate and cannot build,
install, test, or choose different bytes.

## Stable Typed Contracts

The first public API uses typed records equivalent to:

```text
FullSha(value)
RepositoryRef(owner, name, default_branch)
CommitIdentity(repository, sha, ref)
SourceEvidence(source: Directory, history: Directory, identity, manifest)
ArtifactEvidence(directory: Directory, identity, manifest, toolchain)
CheckEvidence(workflow, check_name, app_id, run_id, sha, conclusion)
PagesTarget(repository, account, project, branch, live_domain)
DeploymentEvidence(target, identity, provider_id, provider_url, status)
PublicationDecision(package, version, identity, artifact_digest, allowed)
```

Construction rejects unknown fields, malformed repositories, abbreviated SHAs,
mutable refs, mixed Cloudflare targets, and incomplete evidence. Secrets are
accepted only by the exact methods that use them.

## Repository-Owned Adapters

The shared module does not absorb:

- EdgeReco model hashes, parity fixtures, storefront journeys, signed catalog,
  and zero-egress browser assertions.
- AlmaMesh privacy, PDF, interpretation, onboarding, and signed-bundle rules.
- AML Filter monotonic watchlist sequencing, signing, freshness, and source
  provenance.
- EdgeProc and EdgeProc Core examples, benchmarks, and Python package details.
- Privacy Core browser and npm package semantics.
- Assay mutation testing and combined Python/npm candidate semantics.

Each repository retains a small local composition module that builds its
product artifact and implements domain verification. It cannot pass arbitrary
commands into the shared module.

## Workflow Contract

Ordinary CI, scheduled security, deployment, and candidate workflows contain:

1. Pinned `actions/checkout` with `persist-credentials: false` when source is
   required.
2. Pinned `dagger-for-github` calling one typed function.

The only approved post-Dagger steps are:

- Pinned `upload-artifact` persisting the exact exported candidate directory.
- Source-free pinned `download-artifact` retrieving the exact producing run's
  SHA-qualified candidate.
- The official PyPA publisher action consuming a Dagger-approved candidate.

No approved gateway may checkout source, run free-form shell, set up a runtime,
install dependencies, build, test, or alter candidate bytes.

## Secrets, Tokens, Variables, and Environments

### Storage model

- Production credentials live in GitHub Environment secrets.
- Repository-scoped read-only control-plane credentials remain repository
  secrets when they are not deployment-specific.
- Non-secret identifiers use GitHub Environment or repository variables.
- Ephemeral `GITHUB_TOKEN` and OIDC request credentials come from GitHub at run
  time and are never stored as long-lived secrets.
- Dagger receives credentials as typed `Secret` inputs only.

### Target environments

Create a `production` GitHub Environment for AlmaMesh, AML Filter, and EdgeReco.
Restrict deployments to `main`. Keep required reviewers empty for automatic
delivery; branch protection and exact-main Dagger evidence are the gate.

Move these existing repository secrets into the appropriate environment:

- AlmaMesh: Cloudflare account/token and bundle signing material.
- AML Filter: Cloudflare account/token and watchlist signing material.
- EdgeReco: Cloudflare account/token.

Assay's existing `npm-release` environment remains the registry boundary.
PyPI and npm trusted publishing use GitHub OIDC rather than long-lived registry
tokens.

The central `CONSUMER_DRIFT_TOKEN` remains isolated to `hseshadr/ci` and
read-only. A later automation phase replaces it with a least-privilege GitHub
App installation token after that path has its own canary and rollback proof.

Unused legacy secrets are not deleted automatically. The promotion process
first proves zero references on current main, reports candidates for removal,
and requires a separate reviewed cleanup change.

### Runtime rules

- Secrets never appear in CLI values, scalar return values, manifests, logs,
  traces, caches, or artifacts.
- Use secret environment variables or `/run/secrets` files with cleanup traps.
- Fork PRs receive no production secrets and cannot call privileged functions.
- Deployment and publishing functions use `cache="never"` and fresh execution
  identities.

## EdgeReco Golden Reference

EdgeReco is the first and only implementation canary. Before extraction it must
close these reference gaps:

1. Add scheduled/manual Dagger security ingress.
2. Actionlint both YAML filename extensions.
3. Validate `owner/repository` rather than interpolate a raw string.
4. Bind repository, Pages project, branch, and live domain into one target.
5. Move release mechanics into the immutable shared module so a resolved newer
   main cannot execute older checkout-local release scripts.

The current product-specific build and live verifier remain local.

## Promotion Pipeline

A shared-module change progresses through these automatic stages:

1. Unit, property, mutation, schema, and secret non-disclosure tests.
2. Python and TypeScript generated-client composition fixtures.
3. Current-engine and next-engine compatibility fixtures.
4. Cold-cache source, image, and dependency execution.
5. EdgeReco shadow comparison against the current local implementation.
6. EdgeReco dependency-pin PR generated by Dagger.
7. Hosted EdgeReco PR proof, exact-main proof, production deploy, and live proof.
8. Automated rollback rehearsal by testing the previous pin against the current
   consumer tree.
9. Dagger-generated pin PRs for one repository archetype.
10. Fleet conformance after each merge.

Promotion stops on the first failure. Consumer PRs are never auto-merged.

The promotion credential is separate from the read-only fleet credential. It
may write branches and open PRs but cannot merge, change protection, access
deployment environments, publish packages, or deploy production.

## Serial Rollout

Rollout order is deliberately one completed project at a time:

1. EdgeReco golden reference and reusable foundation.
2. EdgeProc Python-package archetype.
3. EdgeProc Core using the proven Python-package lego.
4. Privacy Core npm-package archetype.
5. Assay hybrid Python/npm archetype.
6. AlmaMesh TypeScript Pages archetype.
7. AML Filter data-producing Pages archetype.

For each repository:

1. Add the exact-SHA dependency and generated client.
2. Write failing conformance and behavior-parity tests.
3. Run local old/new implementations in shadow and compare evidence.
4. Prove hosted PR Dagger twice, including one cold-cache run.
5. Merge with exact-head protection.
6. Prove exact-main Dagger and dependent policy.
7. For applications, deploy and verify live identity.
8. Rehearse pin rollback.
9. Delete duplicate local mechanics only after all preceding gates pass.
10. Start the next repository only after the current repository is complete.

## Central Policy Extensions

The fleet policy must additionally inspect:

- `dagger.json` and workspace/module configuration.
- Literal full-SHA direct dependencies.
- The recursively resolved dependency graph.
- Generated SDK and lockfile drift.
- Dagger engine/action/API compatibility.
- Approved publisher-module SHA independent of consumer source SHA.
- Environment names and secret-reference placement, never secret values.
- Prohibited tag, branch, default-branch, and `latest` dependencies.
- Consumer adapter size and forbidden arbitrary-command escape hatches.

The policy remains fail-closed when authoritative GitHub metadata is
unavailable.

## Required Test Matrix

- Pure unit/property/mutation tests for shared logic.
- Public API and generated-schema snapshots.
- Python and TypeScript local composition fixtures.
- A real remote exact-SHA dependency fixture.
- Clean-checkout deterministic binding and lock regeneration.
- Source inventory mismatch and dirty hosted source rejection.
- Snapshot, old-history, tag-history, and runtime-canary secret regressions.
- Secret absence from logs, traces, artifacts, caches, and scalar results.
- Two-repository cache-isolation tests.
- Candidate parity, unexpected-file, symlink, checksum-tamper, wrong-run, and
  wrong-SHA rejection.
- GitHub in-progress/null, duplicate, wrong-app, stale-check, and pagination
  fixtures.
- Cloudflare wrong endpoint, malformed schema, documented page size, provider
  errors, convergence timeout, and full-SHA tests.
- Source-free publisher, OIDC audience, lifecycle-script, duplicate publish,
  no-rebuild, and stale-cache tests.
- Current/next module by current/next engine compatibility matrix.
- Git-host outage, mirror recovery, and dependency-pin rollback drills.

## Failure and Rollback Behavior

- A shared-module failure blocks its promotion before consumer PR creation.
- A consumer failure blocks only that repository's pin PR and the rollout wave.
- Production deployment never runs unless exact-main Dagger succeeded for the
  same SHA.
- Provider verification failure after an intended upload preserves the intended
  artifact and uses a new reviewed fix-forward PR; it is not blindly rerun.
- Normal rollback is a reviewed PR reverting the shared-module SHA, generated
  bindings, and lockfiles.
- Git-host unavailability fails closed. A reviewed break-glass procedure may
  vendor the exact shared commit plus a SHA-256 manifest; it never falls back to
  a mutable ref or unverified cache.

## Success Criteria

The architecture is complete when:

- EdgeReco consumes the shared foundation and completes the full golden proof.
- Every consumer pins shared modules by exact SHA.
- Central policy validates dependency graphs and environment boundaries.
- Common fixes require one shared-module change plus generated pin PRs.
- No repository duplicates shared source/history, workflow, candidate, or
  provider verification mechanics.
- Product-specific adapters remain readable and independently testable.
- All eight exact mains have zero fleet findings.
- A cold-cache upgrade and a pin rollback have been successfully rehearsed for
  every repository archetype.

## Measured Targets

- At least 60% reduction in duplicated non-product CI/CD implementation lines
  across consumers.
- One shared fix PR and automated consumer pin PRs for common defects.
- Zero mutable Dagger dependency references.
- Zero production secrets at repository scope where a production Environment is
  available.
- Zero CI/CD workflow computation outside Dagger, excluding the explicitly
  approved transport and official PyPI upload gateways.
- One project in migration at a time.
