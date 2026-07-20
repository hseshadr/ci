# hseshadr/ci — one home for the portfolio's CI/CD

**TL;DR — what this is.** Every repo in the edgeproc portfolio used to copy-paste the
same GitHub Actions setup: check out the code, install the toolchain, run the quality
gate, scan for leaked secrets, audit dependencies, deploy the site. Six repos, six
near-identical copies that drifted apart over time. This repo holds **one shared copy
of each**, and every other repo calls it in a few lines. Change CI once here; all six
get the change.

**Why it works.** GitHub lets a workflow *call* a workflow that lives in another repo
(`uses: hseshadr/ci/...@ci-v1`), and lets a job *reuse* a bundle of steps called a
"composite action." So the shared logic lives here exactly once, and each repo keeps
only the one thing that is genuinely its own — its build command.

**Why it exists.** "If we are manually changing things per project per repo, nothing is
standardized." One place to bump `actions/checkout`, one place to fix the gitleaks
pattern, one place that defines what "run the gate" means. No drift.

**Status.** Templates written and statically validated (every file parses; `actionlint`
and full-repository `zizmor` are clean). **Not yet live-proven by a consumer run** —
the six repos still call their old inline workflows; migrating them is a separate wave,
and cross-repo calls stay broken until the one repo setting in
[Required setup](#required-setup-read-this-first) is flipped. See the
[self-assessment](#self-assessment) for the honest scorecard.

---

## Adopt it with one caller

A repo's entire CI can become this (`.github/workflows/ci.yml`):

```yaml
name: CI
on: { push: { branches: [main] }, pull_request: }
permissions:
  contents: read
  pull-requests: read
jobs:
  gate:
    uses: hseshadr/ci/.github/workflows/python-gate.yml@ci-v1
    with: { sync-args: "--frozen --all-extras" }
  gitleaks:
    uses: hseshadr/ci/.github/workflows/secret-scan.yml@ci-v1
```

That is the *whole file*. `gate` runs the repo's `poe gate` (lint, format-check, types,
complexity, tests + coverage floor); `gitleaks` scans the full git history for secrets.
Ready-to-copy callers for all six repos live in [`examples/`](./examples).

### Before → after (a real consumer)

edge-proc's hand-rolled `ci.yml` + `security-audit.yml` was **~89 lines** of the same
five steps every Python repo repeats:

```yaml
# BEFORE — .github/workflows/ci.yml (representative; ~49 lines, and a ~40-line
# security-audit.yml just like it)
name: CI
on: { push: { branches: [main] }, pull_request: }
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v8.1.0
        with: { enable-cache: true }
      - run: uv python install 3.13
      - run: uv sync --frozen --all-extras
      - run: uv run poe gate
  gitleaks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
        with: { fetch-depth: 0 }
      - uses: gitleaks/gitleaks-action@v2
        env: { GITHUB_TOKEN: "${{ secrets.GITHUB_TOKEN }}" }
  # …then a whole separate security-audit.yml repeating the uv setup for pip-audit…
```

**After** — the small `ci.yml` above plus a 10-line `security-audit.yml`. Across the
whole portfolio the templatable workflows shrink from **~598 lines to ~135** (about a
77% cut), and the action-version drift the survey found (edge-reco on `checkout@v7`
while the others were on `@v5`) collapses to one pinned set here.

---

## What's in here (maps 1:1 to the tree)

```
.github/
  workflows/                      # reusable workflows plus this repo's own gate
    ci.yml                        #   validates this repo's CI security policy
    python-gate.yml               #   checkout → setup → uv run poe gate → (opt) codecov
    frontend-gate.yml             #   checkout → pnpm setup → (opt) Playwright → pnpm gate
    secret-scan.yml               #   gitleaks over full history
    security-audit.yml            #   pip-audit and/or pnpm audit (each bool-gated)
    cloudflare-pages-deploy.yml   #   preflight → build → wrangler pages deploy
  actions/                        # composite actions — a bundle of steps you `uses:` INSIDE a job
    setup-python-uv/              #   install uv (cached) + pin Python + (opt) uv sync
    setup-pnpm/                   #   pnpm + Node (pnpm cache) + (opt) install
    setup-playwright/             #   cache + install Playwright browsers
    restore-model-cache/          #   cache self-hosted model weights + fetch on miss
    pages-deploy-dist/            #   baseline headers + shared wrangler deploy
  dependabot.yml                  # bumps THIS repo's action pins → propagates via @ci-v1
examples/                         # copy-paste caller workflows, one folder per repo
tests/
  security-policy.sh              # YAML + pins + permissions + Pages-header contract
CHANGELOG.md
```

**Reusable workflow vs composite action — the one distinction that explains everything.**
A *reusable workflow* is an entire job on its own fresh runner: great when the whole job
is shared, but it cannot accept extra `uses:` steps injected by the caller. A *composite
action* runs *inside* the caller's own job, so the caller can wrap its own cache/build
steps around it. That single fact decides what became a workflow and what became a
composite (details below).

---

## Under the hood (for developers)

### Reusable workflows

| Workflow | Key inputs | Secrets | What the job runs |
|---|---|---|---|
| `python-gate.yml` | `working-directory` `.`, `python-version` `3.13`, `sync-args` `""`, `gate-task` `gate`, `upload-coverage` `false`, `coverage-file` `coverage.xml` | `CODECOV_TOKEN` (optional) | checkout → **setup-python-uv** → `uv run poe <gate-task>` → optional Codecov upload |
| `frontend-gate.yml` | `working-directory` `.`, `package-json-file`, `node-version` `24` / `node-version-file`, `cache-dependency-path` `pnpm-lock.yaml`, `install-args` `--frozen-lockfile`, `gate-command` `pnpm gate`, `install-playwright` `false`, `playwright-browsers` `chromium` | — | checkout → **setup-pnpm** → optional **setup-playwright** → `gate-command` |
| `secret-scan.yml` | `runs-on` | uses `GITHUB_TOKEN` | checkout `fetch-depth:0` → `gitleaks-action` over full history |
| `security-audit.yml` | `run-python-audit` `false`, `run-pnpm-audit` `false`, `python-working-directory` `.`, allowlisted `pip-audit-export-args`, `frontend-working-directory` `frontend`, `pnpm-audit-level` `low` | — | `pip-audit` job (validated export args → `pip-audit`) and/or `pnpm-audit` job (validated severity) |
| `cloudflare-pages-deploy.yml` | `project-name`*, `dist-dir`*, `build-command`*, `install-working-directory` `.`, `pre-build-run` `""`, `node-version(-file)`, `cache-dependency-path`, `branch` `main`, `wrangler-version` `4.110.0` | `CLOUDFLARE_API_TOKEN`*, `CLOUDFLARE_ACCOUNT_ID`* | preflight (skip-clean if secrets absent) → guard → **setup-pnpm** → pre-build → build → **pages-deploy-dist** |

\* required. Every other input has a documented default — no version or path is a magic
literal buried in a step; the gate's coverage floor is deliberately **not** an input (it
lives in each repo's `pytest --cov-fail-under`, so CI can never pass a looser bar than local).

### Composite actions

| Action | Key inputs | What it runs |
|---|---|---|
| `setup-python-uv` | validated `python-version` `3.13`, allowlisted `sync-args` `""`, `working-directory` `.`, `run-sync` `true` | install uv (cached) → `uv python install` → optional `uv sync` |
| `setup-pnpm` | `package-json-file`, `node-version` `24` / `node-version-file`, `cache-dependency-path`, allowlisted `install-args` `--frozen-lockfile`, `working-directory` `.`, `install` `true` | `pnpm/action-setup` → `setup-node` (pnpm cache) → optional `pnpm install` |
| `setup-playwright` | `cache-key`*, allowlisted `browsers` `chromium`, `working-directory` `.` | cache `~/.cache/ms-playwright` → install browsers (miss) or OS deps only (hit) |
| `restore-model-cache` | `cache-path`*, `cache-key`*, `fetch-command`*, `working-directory` `.`, `always-fetch` `false` | cache the weights dir → run `fetch-command` only on a cache miss |
| `pages-deploy-dist` | `project-name`*, `dist-dir`*, `cloudflare-api-token`*, `cloudflare-account-id`*, `branch` `main`, `wrangler-version` `4.110.0` | add a conservative `_headers` baseline when absent → `npx wrangler pages deploy` |

Every composite assumes the caller **already ran `actions/checkout`** (a composite can't
assume a working tree). Composites can't read the `secrets` context, so
`pages-deploy-dist` takes the two Cloudflare secrets as inputs.

The Pages baseline sets anti-framing, MIME-sniffing, referrer, browser-feature,
HSTS, and narrow CSP controls. If a build already contains `_headers`, the action
preserves it byte-for-byte so an application can own a stricter or intentionally
different policy. Cloudflare applies `_headers` to static asset responses; Pages
Functions must set equivalent headers in their own response code.

**Composition, not duplication.** The reusable workflows don't re-implement setup — they
*compose the same composites the bespoke jobs use*. `python-gate` composes
`setup-python-uv`; `frontend-gate` composes `setup-pnpm` + `setup-playwright`;
`security-audit` composes both (with sync/install switched off); `cloudflare-pages-deploy`
composes `setup-pnpm` + `pages-deploy-dist`. So the uv pin, the pnpm/Node pins, the
Playwright cache pattern, and the wrangler invocation each live in exactly one file.

### Version pinning: `@ci-v1`

Consumers pin `@ci-v1` — a **moving major tag**. Compatible changes (a new input, a
patched action) move `ci-v1` forward and every consumer picks them up with no edit. A
breaking input change cuts `ci-v2`; consumers migrate deliberately. Add a Dependabot
`github-actions` entry in each consumer so the `uses: hseshadr/ci/...@ci-v1` refs are
tracked like any other dependency.

### Immutable third-party action pins

Every executable third-party `uses:` reference is pinned to the full 40-character
commit behind the selected release. The trailing release comment is intentional:
Dependabot updates both the SHA and its readable `# v…` label.

| Action | Release comment | Pin policy |
|---|---|---|
| `actions/checkout` | `# v7` | full commit SHA |
| `actions/setup-node` | `# v6` | full commit SHA |
| `actions/cache` | `# v6` | full commit SHA |
| `pnpm/action-setup` | `# v6` | full commit SHA |
| `astral-sh/setup-uv` | `# v8.3.2` | full commit SHA |
| `codecov/codecov-action` | `# v7` | full commit SHA |
| `gitleaks/gitleaks-action` | `# v3` | full commit SHA |

The `hseshadr/ci/...@ci-v1` references are first-party release channels, not
third-party actions. They remain on the moving major tag so compatible fixes from
this repository reach all consumers; immutable `ci-vX.Y.Z` tags remain available
for callers that need a frozen shared-workflow release. Each executable self-reference
has a narrow `zizmor` ignore explaining why it cannot be a relative action path: reusable
workflows execute against the caller's checkout, where `./.github/actions/...` would
resolve to the consumer repository instead of this one.

### Input trust boundary

Data-shaped inputs are parsed as quoted argument arrays and constrained to documented
values: Playwright browsers, pnpm install flags, Python versions, uv sync/export flags,
Poe task names, and audit severity. They are never expanded directly into shell code.

Three inputs are intentionally command-shaped: model `fetch-command`, Pages
`build-command` / `pre-build-run`, and frontend `gate-command`. They accept only literal
commands committed in a trusted caller workflow. Never derive them from event payloads,
repository variables, workflow-dispatch text, or other untrusted data. The implementation
passes them through environment variables before invoking an isolated Bash process, which
prevents GitHub template expansion from turning input text into the surrounding script.

Every checkout sets `persist-credentials: false`; every workflow declares explicit token
permissions; Dependabot waits seven days before adopting new action releases.

### Required setup (read this first)

**These repos are private, so callers 404 with "workflow was not found" until this repo
allows them.** One time, on `hseshadr/ci`:

> **Settings → Actions → General → Access →** select **"Accessible from repositories
> owned by the user"** → **Save.**

Or via the CLI:

```bash
gh api -X PUT repos/hseshadr/ci/actions/permissions/access -f access_level=user
```

This governs both the reusable workflows *and* the composite actions in this repo (the
workflows pull the composites from here at `@ci-v1`), so it must be set once for
everything to resolve. When the repo is public this is automatic.

---

## Standardization coverage (per repo)

Reusable workflow = whole shared job. Composite = shared steps inside a repo's own job.
Bespoke = the irreducible repo-specific build, which still composes the shared composites.

| Repo | Reusable workflows | Composites (inside bespoke jobs) | Irreducibly bespoke |
|---|---|---|---|
| **edge-proc** | python-gate, secret-scan, security-audit | — | none |
| **shared-libs-python** | python-gate (+coverage), secret-scan, security-audit | — | `publish.yml` (PyPI/npm release) |
| **privacy-core** | frontend-gate (+Playwright), secret-scan, security-audit | — | none |
| **edge-reco** | secret-scan, python-gate (backend), cloudflare-pages-deploy, security-audit | setup-pnpm, restore-model-cache, setup-playwright (frontend + e2e jobs) | the frontend/e2e *gate commands* only |
| **aml-filter** | secret-scan, security-audit | setup-pnpm, restore-model-cache, setup-playwright (ci); setup-pnpm + **pages-deploy-dist** (deploy) | bundle sign/verify build; `publish-watchlist.yml` |
| **almamesh** | security-audit (python) | (optional) setup-python-uv | Bun + Pyodide `test.yml`, `deploy.yml`, `nightly-e2e.yml`; key-custody gitleaks |

The point of the composites: even the "bespoke" jobs re-implement **zero** setup or
caching — aml-filter's signing deploy still calls `pages-deploy-dist` for the wrangler
step, so there is one deploy half across edge-reco, aml-filter, and almamesh.

## Limits — where standardization genuinely can't reach

Honest boundaries, not force-fits:

- **`dependabot.yml`** is config, not a workflow — it can't be `uses:`-referenced. Each
  repo keeps its own; standardize by copy, not by reference.
- **almamesh's toolchain** is Bun + in-tree vendored deps (a sanctioned exception), so
  its `test.yml` / `deploy.yml` / `nightly-e2e.yml` don't fit the pnpm/uv templates. Only
  its python-only security audit maps cleanly.
- **almamesh's gitleaks job** carries an extra key-custody tree-guard step, so it keeps a
  bespoke secret-scan job rather than calling `secret-scan.yml`.
- **Multi-step signing builds** (aml-filter's Ed25519 sanctions bundle, almamesh's Pyodide
  + prod-key + IndexNow) are irreducibly repo-specific — a reusable workflow can't accept
  injected steps. They share only the *deploy half* via `pages-deploy-dist`.
- **Singletons** (`shared-libs` publish, `aml-filter` publish-watchlist, almamesh nightly)
  exist in exactly one repo — nothing to de-duplicate.

## Self-assessment

An honest self-assessment against a publish-readiness checklist:

- **Teen-readable front door + layered depth** — ✅ plain-language TL;DR (what / why /
  status) before any jargon; a separate "Under the hood" section carries the depth.
- **One-command adopt on real inputs** — ✅ the 3-line caller is copy-paste; `examples/`
  holds a ready file for every repo.
- **Arch maps 1:1 to tree** — ✅ the "What's in here" tree matches `.github/` exactly.
- **No hardcoded config** — ✅ every version/path is a documented input default; the
  coverage floor is deliberately owned by each repo's gate, not a CI input.
- **Status matches reality / tags match the story** — ✅ CHANGELOG top = `ci-v1.0.0`, cut
  at HEAD; status says "validated, not yet consumer-proven."
- **Every YAML valid** — ✅ all 23 files parse; `actionlint` clean on workflows + examples.
- **Live-validated end-to-end** — ⛔ **not yet.** A cross-repo CI run can't be driven from
  here: it needs (a) the [Required setup](#required-setup-read-this-first) access flip
  (user-only) and (b) a consumer migration (a separate wave, out of scope for this repo).
  Static validation is done; the first real green run lands when the first repo migrates.
  This is stated plainly rather than implied-green.
