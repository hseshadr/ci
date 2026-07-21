# Changelog

All notable changes to the shared CI/CD templates. Each release is cut as an immutable
`ci-vX.Y.Z` tag, and the `ci-vN` pointer is moved to the newest release in that major.
**Consumers pin the release's full commit SHA, not a tag** — the SHA of each release is
listed below. `tests/security-policy.sh` rejects a moving `@ci-vN` ref, first-party
included.

## ci-v2.0.3 — 2026-07-21

Commit `bc68fde66f0805971e1b9aa444933b7975da80b1`.

No reusable-workflow inputs or composite-action signatures changed, and **no composite
behavior changed**, so re-pinning from `ci-v2.0.2` is a drop-in. The change is to this
repo's own guards and docs.

- **A release can now re-run its own pipeline green.** `ci-v2.0.2` could not:
  dispatching CI at the tag ([run
  29839090693](https://github.com/hseshadr/ci/actions/runs/29839090693)) failed with
  `first-party ref 9e8cf2e… is a superseded release, not ci-v2.0.2` across all 21 files.
  The cause is arithmetic, not drift — a commit cannot contain its own SHA, so the tagged
  tree necessarily still names the previous release. `validate_first_party_release_lineage`
  now accepts that one state: at the newest tag's own commit, a first-party ref may name
  **the immediately-preceding release**. Everywhere else the strict "must be the newest
  tag" rule is unchanged, and existence + ancestry are still enforced with no carve-out.

  *Residual gap, stated plainly:* a consumer pinning `ci-vX.Y.Z` gets that release's
  reusable workflows with composites from `ci-vX.Y.(Z-1)` nested inside. Still immutable
  released SHAs, but one generation behind — so a release that changes a composite reaches
  consumers at the *following* release. Any such release is marked in this file. See
  [The release-commit bootstrap](./README.md#the-release-commit-bootstrap).

- **We measured the escape hatch instead of assuming it.** The README asserted that a
  relative action path (`./.github/actions/…`) inside a reusable workflow resolves against
  the caller's checkout — the reason self-references must be absolute SHAs, which is what
  forces the bootstrap above. That assertion had never been tested. A probe with
  identically-pathed composites in both repositories confirmed it: [run
  29838733369](https://github.com/hseshadr/privacy-core/actions/runs/29838733369) printed
  `PROBE_RESULT=RESOLVED_TO_CONSUMER_REPO_hseshadr_privacy_core`, and the no-checkout
  control failed with "Did you forget to run actions/checkout before running your local
  action?". `./` is workspace-relative. The constraint is real, and the docs now carry the
  evidence rather than the claim.

- **The exemption is tested for narrowness** (`tests/lineage-guard-cases.sh`, new). An
  exemption is only safe while it stays small, and this repository's own history cannot
  produce a two-releases-behind tagged commit on demand. The suite builds synthetic repos
  with synthetic `ci-vX.Y.Z` tags and asserts seven verdicts — **six of which must keep
  failing**, including two-releases-back at a tagged commit, a superseded ref on an
  ordinary commit, and a non-ancestor ref at a tagged commit. Removing the five-line
  exemption fails exactly one case and no others. The guard itself moved to
  `tests/lib/first-party-lineage.sh` so both callers share one copy; `validate_self_ci`
  fails the build if CI stops running the new suite, and ShellCheck now covers `tests/lib/`.

- **Node 20 deprecation: nothing to bump here** (verified, no change). Every third-party
  action pinned in this repo already targets Node 24 — `actions/checkout` v7,
  `actions/setup-node` v6, `actions/cache` v6, `pnpm/action-setup` v6,
  `astral-sh/setup-uv` v8.3.2 and `gitleaks/gitleaks-action` v3 all declare `using: node24`
  — and no recent run here carries the deprecation annotation. The warning seen in the
  portfolio came from `assay`'s **own** workflows (`actions/setup-node` v4.4.0 and
  `pnpm/action-setup` v4.1.0), not from anything reached through `hseshadr/ci`; it was
  fixed in that repo.

## ci-v2.0.2 — 2026-07-21

Commit `102e06c2da82e3a201bd7aee4fb8c3e4554593a6`.

No reusable-workflow *inputs* or composite-action signatures changed, so re-pinning from
`ci-v2.0.1` is a drop-in — nothing in a caller has to move. Two entries below do change
what a publish or deploy run *does*, and both are marked.

- **Finish the `ci-v2.0.1` re-pin.** `ci-v2.0.1` fixed the nested refs inside the
  reusable workflows, and a follow-up fixed the two OIDC publish examples — but 35
  executable `uses:` refs across 19 files still named `36bf999` (`ci-v2.0.0`). Sixteen of
  those were `examples/` pointing at *reusable workflows* whose `ci-v2.0.0` copies still
  contain nested `@ci-v1` moving tags, so anyone copying an example inherited the very
  hole `ci-v2.0.1` closed. All 37 first-party refs now name this release.

  The re-pin to *this* commit necessarily lands in the commit after the tag: the
  currency guard below requires every ref to name the newest tag's commit, and a
  release cannot pin a SHA that does not exist until the commit is written. So `main`
  sits one commit ahead of `ci-v2.0.2`, and at the tagged commit itself the 37 refs
  still name `9e8cf2e…` (`ci-v2.0.1`) — immutable, released, and functionally
  identical, since no composite behavior changed in this release.
- **Assert pin provenance, not pin shape** (`validate_first_party_release_lineage`).
  Every `hseshadr/ci` SHA must exist here, be an ancestor of the newest `ci-vX.Y.Z` tag,
  and *be* that tag. A shape check cannot express "this valid SHA points at a bad
  release" — which is exactly why the above survived a green suite.
- **Verify publishes against the registry** (behavior change). `python-publish.yml` and
  `ts-publish.yml` now poll PyPI / npm for the exact `name@version` they just shipped
  (6 attempts, ~60s) and fail if it is not served. `shared-libs-python` had six green
  publish runs sitting on a package that does not resolve on PyPI; a green upload step
  and a published package were never the same fact.
- **Pin the deploy trigger to this repository** (behavior change).
  `cloudflare-pages-deploy.yml` gated auto-deploy on
  `workflow_run.head_branch == 'main'`, which a fork can satisfy by naming its branch
  `main`; the job then checks out `workflow_run.head_sha` with `CLOUDFLARE_*` in scope.
  Now also requires `head_repository.full_name == github.repository`.
- **Audit `examples/` for the first time.** `zizmor` and `actionlint` only collect from
  `.github/workflows/`, so a repo-root scan covered 0 of the 15 example files.
  `tests/lint-examples.sh` stages them into a real layout and audits them; both tools run
  in CI. First pass found: `secrets: inherit` in two examples (now named secrets),
  a missing `conclusion == 'success'` guard in `examples/edge-reco/deploy.yml` (it
  deployed on red CI), and the `workflow_run` trigger design (documented, justified).
- **Run `actionlint` at all.** The README claimed an actionlint-clean tree while no job
  ran it. It now runs over the workflows and the examples, and `validate_self_ci` asserts
  its presence so the claim cannot drift again.
- **Reject `github.event.*` in `run:` blocks.** `validate_shell_boundaries` modelled only
  `${{ inputs. }}`, leaving the textbook script-injection vector unguarded.
- **Fail soft.** One malformed YAML file used to abort the suite at check 4 of 11,
  silently skipping checks 5–11. Every check now runs and all failures are reported.

## ci-v2.0.1 — 2026-07-20

Commit `9e8cf2e170441a6250b9b3c1a7af8128539a388f`.

- **Close a transitive pinning hole in the OIDC publish workflows.** A consumer that
  pinned `python-publish.yml` / `ts-publish.yml` to a SHA still had the *nested*
  `setup-python-uv` / `setup-pnpm` composites resolved through the mutable `@ci-v1` tag
  at run time, so the pin was only skin-deep. These workflows run with
  `id-token: write` for OIDC Trusted Publishing, so moving `ci-v1` would have reached
  PyPI and npm across every consumer. All nested first-party refs are now full commit
  SHAs.
- Remove the first-party carve-out from the security-policy test: `validate_first_party_pins`
  now fails on any `uses: hseshadr/ci/...@ci-vN` in `.github/` or `examples/`, with no
  exemption, and the matching `zizmor` suppressions are gone.
- Documentation only otherwise — no composite action behavior changed since `ci-v1`.
  Every differing line in `.github/actions/` is a comment; verify with:

  ```bash
  git diff ci-v1 ci-v2.0.1 -- .github/actions/ | grep -E '^[+-]' \
    | grep -vE '^(\+\+\+|---)' | grep -vE '^[+-][[:space:]]*(#|$)'
  ```

  which prints nothing.

## ci-v2.0.0 — 2026-07-20

Commit `36bf999acd0617135497b62605e19bed29ee1b94`. **Superseded by `ci-v2.0.1`** — this
commit predates the transitive-pin fix above; do not pin it in a publishing workflow.

- Add two OIDC Trusted Publishing reusable workflows — `python-publish.yml` (PyPI, via
  `pypa/gh-action-pypi-publish`) and `ts-publish.yml` (npm, via `npm publish`) — that
  release from a `v*` tag with **no stored write token**: the build and the `id-token`
  OIDC identity run in one job. `ts-publish` keeps `provenance` off by default (npm
  provenance needs a public repo). Example callers added for shared-libs-python (PyPI)
  and privacy-core (npm).
- Pin every third-party action to a full commit SHA with Dependabot version comments.
- Restrict reusable workflows and consumer examples to explicit least-privilege token
  permissions.
- Add a conservative Cloudflare Pages security-header baseline while preserving any
  application-owned `_headers` file.
- Add a security-policy regression test covering YAML, action pins, permissions, and
  Pages header behavior, enforced by this repository's CI workflow.
- Eliminate all actionable `zizmor` findings: move command inputs through environment
  variables, validate data arguments, disable checkout credential persistence, and add a
  Dependabot cooldown. (This release still carried narrow first-party `ci-v1` ignores;
  `ci-v2.0.1` removes them.)

## ci-v1.0.0 — 2026-07-12

Initial release. Five reusable workflows + five composite actions that de-duplicate
CI/CD across the six edgeproc-portfolio repos.

### Reusable workflows (`on: workflow_call`)
- **python-gate** — checkout → setup-python-uv → `uv run poe gate` → optional Codecov.
- **frontend-gate** — checkout → setup-pnpm → optional cached Playwright → `pnpm gate`.
- **secret-scan** — gitleaks over full git history.
- **security-audit** — `pip-audit` and/or `pnpm audit`, each bool-gated, no suppressions.
- **cloudflare-pages-deploy** — skip-clean preflight → guard → setup-pnpm → build →
  pages-deploy-dist.

### Composite actions
- **setup-python-uv** — uv (cached) + Python pin + optional `uv sync` (`run-sync` toggle).
- **setup-pnpm** — pnpm + Node (pnpm cache) + optional install (`install` toggle).
- **setup-playwright** — cache + install Playwright browsers.
- **restore-model-cache** — cache self-hosted model weights, fetch on cache miss.
- **pages-deploy-dist** — the shared `wrangler pages deploy` step.

### Standardization
- Reusable workflows compose the composites (one source for setup, caching, deploy).
- Action majors unified to the newest each publishes: `checkout@v7`, `setup-node@v6`,
  `cache@v6`, `upload-artifact@v7`, `pnpm/action-setup@v6`, `codecov@v7`,
  `gitleaks-action@v3`; `astral-sh/setup-uv@v8.3.2` pinned exact (no `v8` float exists).
- Dependabot (`github-actions`) tracks this repo's pins so a bump propagates via `@ci-v1`.

### Notes
- Requires `hseshadr/ci` → Settings → Actions → Access → "Accessible from repositories
  owned by the user" for private consumers to resolve the refs.
- Validated statically (every YAML parses; `actionlint` clean). Not yet consumer-proven —
  the first real cross-repo run lands when the first repo migrates.
