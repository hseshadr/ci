# Changelog

All notable changes to the shared CI/CD templates. Consumers pin the moving major tag
`ci-v1`; each release below is also cut as an immutable `ci-vX.Y.Z` tag at its commit.

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
