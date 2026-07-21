# hseshadr/ci — one home for the portfolio's CI/CD

**TL;DR — what this is.** Every repo in the edgeproc portfolio used to copy-paste the
same GitHub Actions setup: check out the code, install the toolchain, run the quality
gate, scan for leaked secrets, audit dependencies, deploy the site. Six repos, six
near-identical copies that drifted apart over time. This repo holds **one shared copy
of each**, and every other repo calls it in a few lines. Change CI once here; all six
get the change.

**Why it works.** GitHub lets a workflow *call* a workflow that lives in another repo
(`uses: hseshadr/ci/...@<commit-sha>`), and lets a job *reuse* a bundle of steps called a
"composite action." So the shared logic lives here exactly once, and each repo keeps
only the one thing that is genuinely its own — its build command.

**Why it exists.** "If we are manually changing things per project per repo, nothing is
standardized." One place to bump `actions/checkout`, one place to fix the gitleaks
pattern, one place that defines what "run the gate" means. No drift.

**Status.** Templates written and statically validated — all 29 YAML files parse, and
`actionlint` plus `zizmor` run in CI over the workflows *and* over `examples/` (the
examples need staging into a `.github/workflows/` layout first, which
`tests/lint-examples.sh` does; a plain repo-root scan reaches none of them). Both are
clean. The cross-repo [access flip](#required-setup-read-this-first) is now done, so
callers resolve.

**Adopted in code by four repos — the publish path only.** `shared-libs-python` and
`edge-proc` call `python-publish.yml`; `privacy-core` calls `ts-publish.yml`; `assay`
calls both. All four pin the `ci-v2.0.3` commit SHA. `edge-reco` and `aml-filter` have
not adopted anything yet.

**The publish path is UNVERIFIED end-to-end — no release has run through it.** Those
callers only fire on a `v*` tag, and no tag has been pushed since they landed:
`edge-proc`, `assay`, and `privacy-core` have zero `publish.yml` runs ever, and
`shared-libs-python`'s six green publish runs all predate the migration (they used the
old token + GitHub-Release flow, before `hseshadr/ci` was referenced at all). The gate,
secret-scan, security-audit, frontend, and deploy templates have **no adopters** — those
four repos still run their own inline `ci.yml` and `security-audit.yml`.

Those six green runs are also why both publish workflows now **verify the release against
the registry** after uploading: the package they were releasing does not resolve on PyPI.
A green upload step and a published package are different facts, and until now nothing
here checked the second one. See [Publish verification](#publish-verification).

Net: nothing in this repo has yet executed inside a consumer run. Static validation is
real; live proof is not there yet. See the [self-assessment](#self-assessment) for the
scorecard.

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
    uses: hseshadr/ci/.github/workflows/python-gate.yml@<40-char-sha> # ci-v2.0.3
    with: { sync-args: "--frozen --all-extras" }
  gitleaks:
    uses: hseshadr/ci/.github/workflows/secret-scan.yml@<40-char-sha> # ci-v2.0.3
```

That is the *whole file*. `gate` runs the repo's `poe gate` (lint, format-check, types,
complexity, tests + coverage floor); `gitleaks` scans the full git history for secrets.
Ready-to-copy callers for all six repos live in [`examples/`](./examples) — those carry
the real SHAs, so copy from there rather than typing a ref by hand. Every
`hseshadr/ci/...` ref must be a full commit SHA, never a moving `@ci-vN` tag; see
[Version pinning](#version-pinning-full-commit-shas) for why.

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
    python-publish.yml            #   gate → uv build → PyPI via OIDC → verify on PyPI
    ts-publish.yml                #   gate → build → npm via OIDC → verify on npm
  actions/                        # composite actions — a bundle of steps you `uses:` INSIDE a job
    setup-python-uv/              #   install uv (cached) + pin Python + (opt) uv sync
    setup-pnpm/                   #   pnpm + Node (pnpm cache) + (opt) install
    setup-playwright/             #   cache + install Playwright browsers
    restore-model-cache/          #   cache self-hosted model weights + fetch on miss
    pages-deploy-dist/            #   baseline headers + shared wrangler deploy
  dependabot.yml                  # bumps THIS repo's action pins; consumers re-pin the new SHA
examples/                         # copy-paste caller workflows, one folder per repo
tests/
  security-policy.sh              # YAML + pins + pin PROVENANCE + permissions + injection
  lint-examples.sh                # stages examples/ into a real workflow layout, then
                                  #   actionlint + zizmor them (neither tool reaches them otherwise)
  lineage-guard-cases.sh          # drives the lineage guard against synthetic repos to prove
                                  #   its release-commit exemption stays one release wide
  lib/
    scan-run-interpolation.rb     #   finds attacker-controllable ${{ }} inside run: blocks
    first-party-lineage.sh        #   the lineage/currency guard, shared by the two above
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
| `python-publish.yml` | `working-directory` `.`, `python-version` `3.13`, `sync-args` `""`, `gate-task` `gate`, `run-gate` `true`, `packages-dir` `dist`, `attestations` `true`, `environment` `""` | — (OIDC, token-free) | checkout → **setup-python-uv** → reuse gate → `uv build` → `gh-action-pypi-publish` (PyPI **OIDC Trusted Publishing**) |
| `ts-publish.yml` | `working-directory` `.`, `node-version` `24`, `gate-command` `pnpm gate`, `build-command` `pnpm build`, `run-gate` `true`, `provenance` `false`, `registry-url` `…npmjs.org`, `environment` `""` | `NPM_READ_TOKEN` (optional, private-dep installs only) | checkout → **setup-node** (registry for OIDC) → **setup-pnpm** → gate → build → `npm publish` (npm **OIDC Trusted Publishing**) |

\* required. Every other input has a documented default — no version or path is a magic
literal buried in a step; the gate's coverage floor is deliberately **not** an input (it
lives in each repo's `pytest --cov-fail-under`, so CI can never pass a looser bar than local).

**OIDC publishing (`python-publish` / `ts-publish`) — no stored token.** Both publish
workflows carry `on: workflow_call` and are pinned by callers at a full commit SHA (a
moving tag here would be a supply-chain hole — see [Version
pinning](#version-pinning-full-commit-shas)); the caller owns
the `on: push: tags: ['v*']` trigger and grants `id-token: write` on its publish job (the
top-level stays read-only, as the security policy requires). The build and the OIDC identity
run in one job, so nothing needs a `twine`/`NODE_AUTH_TOKEN` write token. Two one-time human
bootstraps remain, both outside CI: registering the trusted publisher on PyPI/npm once per
package, and — because npm has **no** "pending publisher" — a single token/OTP first-publish
for each brand-new npm name before OIDC can take over. Caveats baked into `ts-publish`:
`provenance` defaults **false** because `npm publish --provenance` writes a public
transparency-log entry and therefore requires a **public** source repo (flip it true only in
the wave a repo goes public); and each `package.json` `repository.url` must exactly match its
GitHub repo or npm OIDC fails. Ready callers live at
[`examples/shared-libs-python/publish.yml`](./examples/shared-libs-python/publish.yml) (PyPI)
and [`examples/privacy-core/publish.yml`](./examples/privacy-core/publish.yml) (npm).

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

### Version pinning: full commit SHAs

Consumers pin a **full 40-character commit SHA**, with the release name in a trailing
comment so Dependabot can bump it:

```yaml
uses: hseshadr/ci/.github/workflows/python-gate.yml@<40-char-sha> # ci-v2.0.3
```

Moving tags are **not** a supported pin, not even for first-party refs.
`tests/security-policy.sh` fails the build on any `uses: hseshadr/ci/...@ci-vN`, in the
workflows this repo runs and in the examples it publishes.

**Why the stricter rule.** These refs used to ride the moving `@ci-v1` tag behind a
`zizmor` suppression, and that left a real hole: a consumer that pinned
`python-publish.yml` to a SHA still had the *nested* `setup-python-uv@ci-v1` resolved
through a mutable tag at run time, so the pin was only skin-deep. The publish workflows
run with `id-token: write` for OIDC Trusted Publishing, so moving `ci-v1` would have
reached PyPI and npm across every consumer. Pinning the whole chain closes it.

`ci-vX.Y.Z` and the moving `ci-vN` pointers still exist as human-readable release
*names* — read them in [`CHANGELOG.md`](./CHANGELOG.md) to find the SHA you want. They
are not what you put after the `@`. Add a Dependabot `github-actions` entry in each
consumer so these pinned SHAs are tracked like any other dependency; upgrading is then a
deliberate, reviewable commit rather than a tag someone else can move under you.

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

First-party `hseshadr/ci/...` references get the **same** treatment — full commit SHA,
no exceptions. First-party is not a synonym for trustworthy: a moving tag is a moving
tag regardless of who owns it, and these run in workflows that hold `id-token: write`.
The self-references can't be relative action paths (`./.github/actions/...`), because a
reusable workflow executes against the *caller's* checkout, where that path would
resolve to the consumer repository instead of this one — so a SHA is the only immutable
form available, and `validate_first_party_pins` in `tests/security-policy.sh` enforces
it with no carve-out.

That last claim is load-bearing enough that we measured it rather than trusting it.
A throwaway probe put an identically-pathed composite in both repositories, with
different markers, and had a consumer call a reusable workflow here that referenced it
as `./.github/actions/probe-origin`. [Run
29838733369](https://github.com/hseshadr/privacy-core/actions/runs/29838733369) printed
the **consumer's** marker:

```
PROBE_RESULT=RESOLVED_TO_CONSUMER_REPO_hseshadr_privacy_core
action_path=/home/runner/work/privacy-core/privacy-core/./.github/actions/probe-origin
```

and the no-checkout control failed with `Can't find 'action.yml' … under
'/home/runner/work/privacy-core/privacy-core/.github/actions/probe-origin'. Did you
forget to run actions/checkout before running your local action?`. So `./` is
workspace-relative, not repository-relative: it would silently run whatever the consumer
happens to have at that path, or nothing at all. It is not an option here.

**A pin-shape check is not enough, which we learned the expensive way.** Every ref can be
a valid 40-hex SHA and the tree can still be wrong: for a while every file in `examples/`
pointed at `ci-v2.0.0` (`36bf999`), a real commit and a real ancestor — whose reusable
workflows still contained nested `@ci-v1` moving tags. The shape check passed, so a
consumer following this repo's own documented path inherited the exact hole the pin was
supposed to close. `validate_first_party_release_lineage` now asserts *provenance*
instead: every `hseshadr/ci` SHA must exist in this repository, be an ancestor of the
newest `ci-vX.Y.Z` tag, and **be** that tag. A superseded-but-valid release now fails the
build.

### The release-commit bootstrap

There is exactly one state that rule cannot express, and it is forced by arithmetic
rather than by taste: **a commit cannot contain its own SHA.** Our self-references are
absolute SHAs (see above — `./` is not available), so at the moment we tag a release,
every self-reference inside the tagged tree still names the *previous* release. There is
no value we could have written that would name the new one.

Under a strict "must be the newest tag" rule the tagged commit therefore failed its own
guard. That was not hypothetical: dispatching CI at `ci-v2.0.2`
([run 29839090693](https://github.com/hseshadr/ci/actions/runs/29839090693)) went red
with `first-party ref 9e8cf2e… is a superseded release, not ci-v2.0.2` across all 21
files — a release that could not re-run its own pipeline green.

So the guard now allows one narrow thing:

| Where the guard runs | What a first-party ref may name |
|---|---|
| The newest tag's own commit | that tag, **or** the release immediately before it |
| Any other commit | that tag, and nothing else |

Ancestry and existence are still checked everywhere, with no carve-out; only the
*currency* clause relaxes, only at the tagged commit, and only by one release.

**The residual gap, stated plainly.** A consumer pinning `ci-vX.Y.Z` gets that release's
reusable workflows, but the composite actions nested *inside* those workflows come from
`ci-vX.Y.(Z-1)`. Those nested refs are still immutable released SHAs — nothing moves
under anyone — but they are one generation behind. When a release changes a composite's
behavior, that change reaches consumers only at the following release. The CHANGELOG
marks any release whose composites changed, and the re-pin commit on `main` immediately
after each tag is what closes the gap for anyone tracking `main`.

Because this repository's own history cannot produce a two-releases-behind tagged commit
on demand, the exemption's *scope* is asserted against synthetic repositories in
`tests/lineage-guard-cases.sh` — seven cases, six of which must keep failing. It runs in
CI as its own step, and `validate_self_ci` fails the build if that step is ever removed.

### Publish verification

Both publish workflows ask the registry whether the release actually landed, instead of
trusting the upload step's exit code. After `pypa/gh-action-pypi-publish` (or
`npm publish`), the job derives the exact `name` + `version` it just shipped — from the
sdist filename for PyPI, from `npm pkg get` for npm — and polls
`https://pypi.org/pypi/<name>/<version>/json` or `npm view <name>@<version>`. Six
attempts, ten seconds apart, roughly a minute. Propagation delay gets retries; a timeout
is a **failure**, never a pass.

This exists because a green upload and a published package turned out to be different
facts. `shared-libs-python` collected six green `Publish (PyPI, OIDC)` runs — one named
`Release v0.2.0` — on top of a package that does not resolve on PyPI. The trusted-publisher
bootstrap had never been completed, and no check in the pipeline was capable of noticing.
The failure message says so directly and names that bootstrap as the first thing to check.

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
workflows pull the composites from here at a pinned SHA), so it must be set once for
everything to resolve. When the repo is public this is automatic.

---

## Standardization coverage (per repo)

This is the **target** mapping — what each repo should call once migrated — not current
adoption. For what is actually wired up today, see [Status](#hseshadrci--one-home-for-the-portfolios-cicd).

Reusable workflow = whole shared job. Composite = shared steps inside a repo's own job.
Bespoke = the irreducible repo-specific build, which still composes the shared composites.

| Repo | Reusable workflows | Composites (inside bespoke jobs) | Irreducibly bespoke |
|---|---|---|---|
| **edge-proc** | python-gate, secret-scan, security-audit | — | none |
| **shared-libs-python** | python-gate (+coverage), **python-publish** (PyPI OIDC), secret-scan, security-audit | — | none |
| **privacy-core** | frontend-gate (+Playwright), **ts-publish** (npm OIDC), secret-scan, security-audit | — | none |
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
- **Status matches reality / tags match the story** — ✅ CHANGELOG top = `ci-v2.0.3`, and
  every release lists the SHA consumers actually pin. Every first-party ref here pins
  `ci-v2.0.3`, and `validate_first_party_release_lineage` fails the build if one drifts
  off it. `main` sits ahead of the tag, and at least the first commit of that gap is
  structural rather than drift: the re-pin cannot be *in* the commit it names, because a
  commit cannot contain its own SHA. The tag is cut first, the re-pin follows. The guard
  accepts that one state at the tagged commit and nowhere else — see [The release-commit
  bootstrap](#the-release-commit-bootstrap), which also states the residual gap it leaves.
- **Every YAML valid** — ✅ all 29 files parse. `actionlint` runs in CI over both our own
  workflows and, via `tests/lint-examples.sh`, over `examples/`; both are clean.
- **Live-validated end-to-end** — ⛔ **not yet.** The [Required
  setup](#required-setup-read-this-first) access flip is done, and four repos now call the
  publish workflows — but nothing has *run*. Those callers fire only on a `v*` tag and none
  has been pushed since they landed, so zero consumer runs have executed a workflow or
  composite from this repo. The first real green run lands with the first release tag. This
  is stated plainly rather than implied-green.
