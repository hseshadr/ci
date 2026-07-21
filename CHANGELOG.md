# Changelog

All notable changes to the shared CI/CD templates. Each release is cut as an immutable
`ci-vX.Y.Z` tag, and the `ci-vN` pointer is moved to the newest release in that major.
**Consumers pin the release's full commit SHA, not a tag** — the SHA of each release is
listed below. `tests/security-policy.sh` rejects a moving `@ci-vN` ref, first-party
included.

## ci-v2.0.1 — 2026-07-20

Commit `740c72b3435c0156f1389f935b75f212def37f96`.

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
