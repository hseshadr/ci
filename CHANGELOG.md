# Changelog

All notable changes to the shared CI/CD templates. Each release is cut as an immutable
`ci-vX.Y.Z` tag, and the `ci-vN` pointer is moved to the newest release in that major.
**Consumers pin the release's full commit SHA, not a tag** — the SHA of each release is
listed below. `tests/security-policy.sh` rejects a moving `@ci-vN` ref, first-party
included.

## Unreleased

- **Phase 0 of Dagger-only CI/CD is enforceable.** A new modern Dagger 0.21.8 module
  exposes one credential-free `policy` check from an explicit typed `Directory` source.
  The new `Dagger` workflow is only immutable checkout with credential persistence off,
  followed by the pinned Dagger action. Existing required contexts remain during the
  migration.
- **A semantic fleet detector now fails new execution drift.** It parses workflow YAML,
  Python module ASTs, guarded `workflow_run` expressions, branch protection, CodeQL
  default setup, and check-run app ownership. It rejects reusable or helper jobs,
  arbitrary steps, mutable refs, implicit workspace access, string credentials, unsafe
  pull-request permissions, zero-workflow vacuity, legacy required contexts, and
  unenumerated publisher bridges. It discovers GitGuardian on open-PR heads but classifies
  that non-required, non-mutating observer as `external-advisory`; Cloudflare Git deploy
  and managed CodeQL remain violations. Both-polarity fixtures prove the behavior.
- **The bootstrap is exact and expires.** The live sweep found 75 existing violations
  across the eight repositories. Each grandfathered repository/file/job is bound to its
  canonical behavior digest through 2026-10-01, so changing old debt or adding new debt
  fails while deletion-first migrations proceed.
- **The stale moving-major pointer is repaired.** Lightweight `ci-v3` moved from
  `72521e7` to the commit peeled from annotated `ci-v3.3.0`, `8166345`. A live scan of
  all 34 first-party workflow refs found zero `@ci-v3` consumers, so the documentation
  contract is restored without changing any consumer execution.

## ci-v3.3.0 — 2026-08-25

Commit `8166345c9355dde54c12fa95d0457c4ea97d3e64`.

**A brick changed shape**: `secret-scan.yml` gains one optional input, `full-history`
(boolean, default `false`). Re-pinning without setting it is a drop-in — the default is
the existing event-range behaviour. Setting it runs an explicit
`gitleaks git --log-opts=--all` pass on any event, including tag pushes.

**Action required if you copied `examples/<repo>/security-audit.yml`**: its `gitleaks` job
now carries its own `permissions:` block. Without it that job cannot start — see below.

- **A caller that under-granted did not go red, it went ABSENT.** `secret-scan.yml`
  declares `pull-requests: read` (gitleaks-action lists a PR's commits through the API).
  Five `examples/*/security-audit.yml` called it from a workflow whose only grant was a
  top-level `contents: read`, and so did `ci.yml`'s own `secret-scan-sweep` job — whose
  job-level `permissions: {contents: read}` *replaced* the top level rather than adding to
  it, dropping `pull-requests` to `none`. GitHub refuses such a run before any job starts:
  `requesting 'pull-requests: read', but is only allowed 'pull-requests: none'`. The
  conclusion is `startup_failure` and it emits **zero check runs** — measured on run
  [31127046921](https://github.com/hseshadr/ci/actions/runs/31127046921), which reported
  `jobs: 0` while the check-runs API for its head SHA listed only the checks from other
  workflows. `Security policy` and `Secret scan (own brick) / gitleaks` were not red, they
  were missing, and **branch protection reads a missing required check as "pending", never
  "failed"** — the same shape as the bug this release exists to fix, where a secret scan
  that scanned 0 commits reported success. An `if:` guard does not help: permissions are
  checked before any condition is evaluated, so `ci.yml` died on `pull_request` events
  where the offending job would never have run at all.
- **New guard: `tests/lib/scan-caller-permissions.rb`**, driven by
  `validate_caller_permission_sufficiency` in `tests/security-policy.sh`. It parses every
  caller job's effective grant (job-level block, else workflow-level) and compares it
  scope-by-scope against the callee's declared `permissions:`, across
  `.github/workflows/` **and** `examples/`. It is static by necessity — there is no run to
  inspect, because the failure *is* the absence of a run. 15 both-polarity fixtures pin the
  property (`validate_caller_permission_cases`), including the job-level-replacement trap,
  a granted `read` against a required `write`, `read-all`/`write-all` shorthands, a grant
  supplied through a YAML alias, and a caller that declares no permissions anywhere. The
  scanner also reports how many caller→callee pairs it resolved (25 today) against a floor
  of 20, so ref resolution that quietly broke cannot masquerade as a clean tree.

- **The secret scan never read history, and said it did.** `secret-scan.yml` opened with
  "gitleaks over the FULL git history" and "a credential committed five commits ago is
  exactly as leaked as one committed at HEAD". Neither described what it ran.
  `gitleaks-action` derives its scan range from the **event**, not from `fetch-depth`
  (`gitleaks-action@e0c47f4`, `src/gitleaks.js:103-115`, `src/index.js:176`): on `push`
  and `pull_request` it appends `--log-opts=--no-merges --first-parent BASE^..HEAD`, and
  only on `schedule`/`workflow_dispatch` does it omit `--log-opts` and read every commit.
  `fetch-depth: 0` makes `BASE^` resolvable; it does not widen the scan. Measured on this
  repository through this very workflow: run
  [31051347230](https://github.com/hseshadr/ci/actions/runs/31051347230) (push to `main`)
  scanned **0 commits** and reported success; run
  [30978634362](https://github.com/hseshadr/ci/actions/runs/30978634362) (pull_request)
  scanned **1**; run
  [30793713570](https://github.com/hseshadr/ci/actions/runs/30793713570) (schedule)
  scanned **41**. The workflow is `workflow_call`-only and every caller in `examples/` but
  one ran on push/PR, so no consumer's pre-existing history had ever been scanned by CI.
- **The caller owns the schedule; the brick owns the sweep.** A `workflow_call` workflow
  cannot carry its own `schedule:`, so each `examples/*/security-audit.yml` invokes it
  weekly with `full-history: true`. The shared workflow now executes the explicit `--all`
  scan itself, so release callers can request the same guarantee on tag pushes whose
  action-derived range may be empty.
- **Secret findings cannot become collaboration or artifact data.** The action's comment,
  summary, and SARIF upload features are all disabled, the CLI version is fixed, and both
  passes redact findings. A failed scan may name the file/rule in its job log; it cannot
  copy the detected credential into a PR, summary, or downloadable artifact.
- **Every secret-scan caller job is named.** An unnamed one reports as `gitleaks /
  gitleaks` instead of the documented `Secret scan / gitleaks`, silently orphaning an
  adopter's required status check. Five of the six callers shipped in `examples/` omitted
  the `name:`, as did the README's canonical copy-paste snippet.
- **Two guards, both shown failing.** `validate_secret_scan_history_sweep` refuses an
  `examples/` tree where a repo calls `secret-scan.yml` but never from a scheduled
  workflow, and refuses an unnamed caller job; it carries a vacuity floor.
  `validate_secret_scan_coverage_cases` executes the workflow's real coverage script with
  a recording gitleaks double, proving `--all` is passed under both event families and the
  CLI is not invoked for range-only mode.
- **README: 16 false claims fixed or deleted.** The file had never been updated past
  `ci-v3.0.0` while three releases and one consumer adoption landed. Corrected: the current
  release and every `2a575cd` pin (now `605e51c` / `ci-v3.2.1`), the adoption count (7
  call-sites across 5 repos, not 6 across 4), the drift count (29, not three different
  numbers), the third-party pin table (exact versions — a `# v6` comment on a SHA is the
  defect commit `ae644d7` fixed), the publish-verification bound (14 attempts / 600s, not
  6 / 60s), the "these repos are private" setup section (all eight are public), and the
  repository-settings gap (branch protection and secret scanning are both on). Deleted:
  the `--allow-unlocked` "live gap" callout, closed at `ci-v3.2.1`, and two completed
  owner actions. The one genuinely open owner action is now stated: `ci-v3` still points
  at `72521e7`, 21 commits behind `ci-v3.2.1`.
- **`aml-filter/ci.yml/secret-scan` deleted from the drift allowlist.** aml-filter#93
  merged on 2026-08-02 and the consumer now calls the brick
  ([run 31051313153](https://github.com/hseshadr/aml-filter/actions/runs/31051313153),
  `Secret scan / gitleaks` SUCCESS on `main`). First entry ever removed by an actual
  convergence rather than by a bug fix. 30 -> 29.
- **The post-release re-pin closes the release-commit bootstrap.** All 49 current
  first-party `uses:` refs in `.github/`, `examples/`, and the README now execute the
  immutable `ci-v3.3.0` commit, and current placeholder comments name that exact release.
  `validate_current_first_party_release` resolves the tag and fails if either surface
  falls behind again; CHANGELOG history is deliberately excluded.
- **The drift backlog is reconciled against the live repositories.** Fifteen entries
  that the detector now reports stale were deleted, taking the active backlog from 32 to
  17 with `0 new`. EdgeReco's `dagger.yml` and both Assay full-history callers now adopt
  the hardened shared secret scan; no temporary bootstrap entry remains.

## ci-v3.2.1 — 2026-08-04

Commit `605e51cbc86f452b56edcf1c9660921da797cbfe`.

**No brick changed shape** — no input, output or permission moved, so re-pinning from
`ci-v3.2.0` is a drop-in. One entry, and it changes brick *behaviour*: the
publish-verification retry bound in `python-publish.yml` and `ts-publish.yml`. **Re-pin
only if you publish** through those workflows or copied one of the `examples/*/publish.yml`
inline jobs; nothing else in this release reaches a consumer.

**No composite behavior changed**, so the [release-commit
bootstrap](./README.md#the-release-commit-bootstrap) gap does not apply to this release.
All 41 first-party refs at this commit (9 in `.github/`, 32 in `examples/`) already name
`ci-v3.2.0`, and every composite reached through them is byte-identical to the one in this
tree.

- **The publish-verification bound was too tight, and it failed a release that had
  genuinely succeeded.** The check itself is right and stays: ask the registry whether the
  version is served, never trust the uploader, and treat a timeout as a FAILURE. Its bound
  was wrong — 6 attempts 10s apart (~60s) against measured propagation of ~120s
  (PyPI `edge-proc` 0.3.0) and ~200s (npm `@edgeproc/errors` 0.1.0, the first publish of a
  brand-new name). `edgeproc-core` 0.4.0 went live on PyPI and
  [its publish run went red anyway](https://github.com/hseshadr/edgeproc-core/actions/runs/30842985605).
  That false negative is not harmless: a red run on a live release teaches the reader to
  wave off red publish runs, which is exactly how the six-green-while-404 defect returns.
  New bound: 14 attempts with backoff (5, 10, 15, 30, then 60s) — 600s of sleep, 3x the
  slowest case measured, while the common case still verifies in ~15s. The failure message
  now separates "STILL PROPAGATING" from "THE RELEASE NEVER HAPPENED"; it previously listed
  only the misconfiguration causes, which is misleading now that a timeout is more often
  propagation. The guard keeps its teeth: run the step against a version PyPI/npm does not
  serve and it still exits **1** after the full budget. The three `examples/*/publish.yml`
  inline copies carry the same bound, so the surface consumers copy does not ship the
  defect.

## ci-v3.2.0 — 2026-08-03

Commit `7226072bd02e7aecc5b065b3eaf0bfbf4b3e1790`.

**This is the release that made `ci-v3.1.0`'s `setup-uv` v9 upgrade actually run.** Its
only change over `ci-v3.1.0` is the first-party re-pin: 41 refs across 23 files move from
`ci-v3.0.0` to `ci-v3.1.0`, and nothing else — verify with

```bash
git diff ci-v3.1.0 ci-v3.2.0 | grep -E '^[+-]' | grep -vE '^(\+\+\+|---)' \
  | grep -vE 'hseshadr/ci/'
```

which prints nothing. This is the `ci-v3.x` tag to pin if you are not taking `ci-v3.2.1`.

## ci-v3.1.0 — 2026-08-03

Commit `33c5e5fa421210e6cc91ea30cad708bce29a2407`.

**Do not pin this tag — take `ci-v3.2.0` or newer.** This is the
[release-commit bootstrap](./README.md#the-release-commit-bootstrap) in its worst shape:
the composite in this tree runs `setup-uv` v9.0.0, but the tree's own first-party refs
still name `ci-v3.0.0`, so a consumer pinning `ci-v3.1.0` executes the **v8.3.2**
composite — the exact thing this release set out to fix. `ci-v3.2.0` is the re-pin.

Everything below first shipped here, in the six PRs (#9–#14) between `ci-v3.0.0` and this
commit. Apart from the `setup-uv` bump, none of it changes a brick's shape: each entry is
a guard, a test, or a fix to the copy-paste surface in `examples/`, which needs a re-copy
rather than a re-pin.

- **`setup-python-uv` runs `astral-sh/setup-uv` v9.0.0** (behavior change). `ci-v3.0.0`'s
  tree carried v8.3.2, so every consumer calling `python-gate`, `python-publish` or
  `security-audit` executed v8.3.2 on its gate and publish path while its own `ci.yml`
  ran v9.0.0 — a split nobody could see from either side. Reaches consumers at
  `ci-v3.2.0`, per the note above.
- **The drift detector caught its first new control, and the cause was partly this repo.**
  On 2026-08-02 the scheduled sweep went red: `30 … 29 allowlisted; 1 new`
  ([run 30739082151](https://github.com/hseshadr/ci/actions/runs/30739082151)); the day
  before it read `29 … 0 new`. The new one was `aml-filter/ci.yml/secret-scan`, from
  [aml-filter#89](https://github.com/hseshadr/aml-filter/pull/89) — a PR closing a real hole
  (gitleaks ran only in a weekly sweep, so a secret could merge and sit in public history
  for up to seven days) that closed it by **inlining `gitleaks/gitleaks-action`, the exact
  control `secret-scan.yml` publishes, at the identical pinned SHA**. Part habit, but two
  causes were ours and are fixed here: **`examples/aml-filter/ci.yml` carried no
  secret-scan job**, so the worked example for the very file being edited had nothing to
  copy (it does now); and **nothing warned that adopting a reusable workflow renames its
  check run** to `<caller job> / <called job>`, which silently breaks a required status
  check named after the old inline job — a cost paid by the adopter and invisible to
  whoever published the brick. It now has its own README section. A **third** cause turned
  up while converging: this repo tags `ci-vX.Y.Z` while third-party actions tag `vN`, so a
  consumer that lints its pinned-`uses:` comments with `^v\d` **rejects a correct
  `hseshadr/ci` pin** — that is what reddened
  [aml-filter#93](https://github.com/hseshadr/aml-filter/pull/93) on its first run, on
  aml-filter's own supply-chain test. Also documented, with the tightening fix (key the
  expected scheme off the ref; do not relax the regex). The consumer is converging rather
  than being exempted; the allowlist entry is a pointer to that open PR and is marked for
  deletion when it lands.
- **An allowlist entry covers one control, not the file it lives in**
  (`tests/consumer-drift-cases.sh`). Already true, now pinned — the 08-02 finding depended
  on it. `aml-filter/ci.yml/frontend-gate` had been allowlisted since 07-26; had the key
  been read at file granularity, that older entry would have swallowed the new secret-scan
  control and reported a clean run on the day it mattered most. Proven by mutation:
  widening `allowlist_index` to match on `<repo>/<workflow>` makes the detector report
  `2 allowlisted; 0 new` on a fixture holding one known and one brand-new control, and the
  new cases go red.
- **The allowlist header no longer claims "nothing here is new drift."** That sentence was
  true for seven days. One entry now *is* new drift, the header says so, and it records the
  30 → 29 → 30 reconciliation (07-26 included one classifier false positive, 07-31 deleted
  it, 08-02 added one real control) so adjacent counts stop reading as contradictory.
- **A production Ed25519 signing seed could survive on the runner**
  (`examples/aml-filter/deploy.yml`). The example decoded the seed to `/tmp` and `shred`ed
  it on the **last line of the same `run:` block** — after a bundle-verification step whose
  documented job is to abort a bad deploy. So the cleanup was skipped in exactly the
  failure the design expects, leaving a live signing key on the runner. The scrub is now
  its own step with `if: always()`; the key is written under `umask 077` into
  `$RUNNER_TEMP`, outside the checkout, so no `dist-dir` mistake can publish it to a CDN;
  and the decode asserts the result is exactly 32 bytes. `tests/security-policy.sh`
  asserts the step split and the `if: always()`. This portfolio has already had one
  committed-seed incident and a full key rotation — anyone who copied this example should
  re-copy it.
- **Examples are now checked against the repositories they serve**
  (`tests/example-fidelity.sh`, `tests/lib/example-references.rb`). `consumer-drift.sh`
  proves a consumer diverged from what this repo publishes; nothing proved the mirror —
  that an example still converges to the consumer it names. The checker resolves every
  path, `package.json` script, node script, poe task, brick reference **and brick input
  name** an example uses against that consumer's committed default branch, and reports
  three statuses: OK / MISSING / **UNVERIFIABLE**. UNVERIFIABLE is never a pass — it exits
  2, so "could not verify" can never be read as "verified". It found **8 broken references
  that actionlint and zizmor both passed**, including `examples/edge-reco/ci.yml` naming
  `frontend/.node-version`, a file edge-reco has never had and that `actions/setup-node`
  hard-fails on: the example was red as drafted and the gate shipped it. Run it as
  `tests/example-fidelity.sh` (reads `~/dev/oss` clones) or `--clone` (shallow-clones the
  consumers, which is what CI does). It is wired into `tests/lint-examples.sh`, so CI runs
  it on every push and PR.
- **Both polarities of that checker are fixtures** (`tests/example-fidelity-cases.sh`, new)
  — a checker never shown saying NO is not evidence. CI runs it as its own step.
- **`examples/shared-libs-python/` is now `examples/edgeproc-core/`**, following the
  GitHub rename of that consumer repository. The old path no longer exists; update any
  link or copy script that names it.
- **The Ruby the test suite runs on is pinned.** `.ruby-version` (3.4.10) is the single
  source and `ruby/setup-ruby` reads it in both `ci.yml` and `consumer-drift.yml`. The
  guards that decide whether a workflow is safe are Ruby scripts; running them on whatever
  Ruby a runner image happens to ship is the exact class of drift this repo exists to
  police.
- **`security-audit.yml` refuses to report success having audited nothing.** Called with
  `run-python-audit: false` **and** `run-pnpm-audit: false`, both jobs skipped and the
  workflow went green — a security audit that audited nothing, indistinguishable from one
  that passed. A new unconditional `configured` job fails that combination with a named
  reason.
- **A drift sweep that inspected zero repositories was a clean bill of health.**
  `tests/consumer-drift.sh` now exits **2** when it inspected no repository at all, and
  `consumer-drift.yml` now **fails** a `schedule` / `workflow_dispatch` run whose token is
  missing instead of exiting 0 with a notice. A fork `pull_request` still warns and
  continues — a fork legitimately cannot see secrets, and the case suite is the real gate
  there.

## ci-v3.0.0 — 2026-07-30

Commit `2a575cd193e2e1fc093ccd26821020538e2547b7`.

**The one thing to know:** `cloudflare-pages-deploy.yml` now refuses to deploy unless the
run that triggered it was a **push** (`github.event.workflow_run.event == 'push'`). In
`ci-v2.0.3` that gate pinned the repository, the branch and the conclusion — but not the
event. A fork's default branch is also called `main`, so a branch-name-only gate can run
fork-authored code inside a job that holds `CLOUDFLARE_API_TOKEN`. **If you deploy with
this brick, re-pin.**

**Major, because defaults changed under callers who omit the input**: `ts-publish.yml`
`provenance` went `false` → **`true`**, and `sync-args` / `install-args` became
locked-by-default. Read those two bullets before re-pinning.

**Composite behavior changed in this release, so the [residual
gap](./README.md#the-release-commit-bootstrap) applies.** At the tagged commit all 40
first-party refs still name `ci-v2.0.3` — a commit cannot contain its own SHA — so a
consumer pinning `ci-v3.0.0` gets this release's *reusable workflows* with `ci-v2.0.3`
*composites* nested inside. Concretely: `python-gate.yml@ci-v3.0.0` carries the `--locked`
default, but the composite that **enforces** it, and that understands the
`--allow-unlocked` opt-out sentinel, arrives at the following release. Until then, do not
pass `--allow-unlocked` through a reusable workflow — the older composite's argument
allowlist rejects the unknown flag.

- **`cloudflare-pages-deploy.yml`'s fork-deploy gate now requires
  `github.event.workflow_run.event == 'push'` (behavior change, security fix).** The
  shipped brick was **weaker than all three hand-rolled deploys that copied it**: it
  pinned the repository, the branch and the conclusion, but not the event, so a fork
  *pull_request* workflow_run reached a job holding `CLOUDFLARE_API_TOKEN` if the
  repository pin were ever weakened. Callers should copy the `if:` block now documented
  at the top of that file.
- **Provenance on by default (behavior change).** `ts-publish.yml`'s `provenance` input
  now defaults to **`true`**. It defaulted `false` as a private-repo hangover, and that
  was a silent trap — `--provenance` needs a public repo, so the safe-looking default
  meant a caller who simply *forgot* the input shipped an **unsigned** release and a
  perfectly green run. All four publishing repos went public on 2026-07-25, so signing is
  now the default and *not* signing is the thing you ask for: a **PRIVATE** caller must
  pass `provenance: false` explicitly or its publish will fail. Existing callers all pass
  `provenance: true` already and are unaffected. This also closes the documentation
  discrepancy `ci-v2.0.3` shipped with, where the README described a `main` default that
  no released tag carried.
- **This repo stops exempting itself.** It published `secret-scan.yml` while running no
  gitleaks step of its own, and had **zero** scheduled runs while selling zizmor's online
  audits — audits against a *moving* advisory database, so a push-only gate proves the
  tree was clean the last time someone pushed and nothing more. `ci.yml` now calls its own
  `secret-scan.yml` through a local `./` ref (so the brick is proven against the commit
  being changed, not a released SHA) and carries a weekly `schedule`, which re-runs the
  whole gate — policy suite, actionlint, zizmor online, examples audit — against today's
  advisory data at no duplication cost. `validate_self_ci` asserts both, plus a
  conditional: if this repo ever gains a `pyproject.toml`/`package.json` it must also run
  its own `security-audit.yml` (it has no dependency manifest today, so calling that
  workflow now would be a permanently, vacuously green job).
- **The fork-deploy guard is a parse and a proof, not two substrings**
  (`tests/lib/workflow-run-pin.rb`). The check it replaces asked whether
  `head_repository.full_name` and `github.repository` both appeared *somewhere* in *some*
  `if:`. Five genuinely exploitable gates satisfied it: an inverted `!=` pin, a pin ORed
  away by `|| github.event.workflow_run.id != ''`, a pin sitting in a job other than the
  secret-holder, a gate with no `conclusion == 'success'`, and a gate with no
  `event == 'push'`. It also early-exited on `on: workflow_call` files, so the pin could
  be deleted outright from `cloudflare-pages-deploy.yml` with the suite still green. The
  gate is now parsed into a boolean AST and each of the three properties proven by
  exhaustion; a `workflow_call` workflow that *consumes* `github.event.workflow_run.*` is
  in scope; and every job inherits its `needs:` chain's gates. All ten bypasses are
  fixtures.
- **`pnpm publish` was invisible to the provenance guard.** `/\bnpm\s+publish\b/` does
  **not** match `pnpm publish` — `\b` does not split `p` from `n` — and pnpm is this
  portfolio's package manager, so the one guard written to catch unsigned npm releases
  could not see the command it would actually be given. It now covers `npm`/`pnpm`/`yarn`,
  parses composite actions' `runs.steps` (previously every `.github/actions/*/action.yml`
  was exempt), and treats a `python-publish.yml` caller passing `attestations: false` the
  same as a `ts-publish.yml` caller passing `provenance: false`.
- **The top-level-permissions guard reasoned about text and missed three spellings of
  `contents: write`**: a flow map on the line *below* `permissions:`, a multi-line flow
  map, and a write hidden behind a YAML **alias**. It is now a YAML parse, so one value is
  judged in every spelling.
- **Eight of nine file-scanning guards were vacuous on an empty input set** — only the
  lineage guard asserted its input was non-empty. Every guard now declares a floor and
  fails loudly when its input set collapses; red-proofed by drifting the `find` paths,
  which turns eight checks red where the previous suite printed "Security policy checks
  passed."
- **`# zizmor: ignore[dangerous-triggers]` is now an exemption with a machine-checked
  precondition.** The comment silences the audit for the entire `on:` key: adding
  `pull_request_target:` beneath one of the two shipped deploy examples made zizmor print
  *"No findings to report. Good job!"*. A new guard refuses to let a suppression cover any
  trigger beyond `workflow_run`/`workflow_dispatch`, and requires the suppressing file's
  own fork-deploy gate to be provably sound.
- **A new guard makes provenance unfalsifiable** (`validate_publish_provenance`, backed by
  `tests/lib/scan-publish-provenance.rb`). It parses every workflow and example and fails
  on: a `pypa/gh-action-pypi-publish` step without `attestations: true`; an inline
  `npm publish` without `--provenance`; a caller setting `provenance: false`; a reusable
  `provenance`/`attestations` input that *defaults* off; and a publishing job that does
  not grant `id-token: write` (OIDC is the credential — lose it and the rail is dead).
  It is a YAML parse, not a grep: one of its fixtures carries the literal text
  `attestations: true` in a **comment** above a step that never sets it, and is rejected.
  Red-proofed four ways — flipping the reusable default back to `false`, restoring
  `provenance: false` in the privacy-core example, deleting `attestations: true` from the
  edge-proc example, and dropping `id-token: write` from the edgeproc-core example (then
  filed under `examples/shared-libs-python/`)
  each turn the suite red with a named reason.
- **`examples/privacy-core/publish.yml` was carrying `provenance: false`** long after the
  repo went public, alongside a stale `@gainratio/privacy-core` package name. `examples/`
  is the copy-paste surface, so that file was handing new consumers an unsigned rail. It
  is now synced to the proven caller (run 30173462035 → `@edgeproc/privacy-core` 0.2.2,
  which the registry serves with a SLSA v1 provenance attestation).
- **Locked-by-default installs (behavior change).** `setup-python-uv` `sync-args` and the
  `sync-args` input of `python-gate.yml` / `python-publish.yml` now default to `--locked`,
  and an argument list without `--frozen`/`--locked` (including the old empty default) is
  **rejected**; the explicit opt-out is the `--allow-unlocked` sentinel (consumed by the
  action, never passed to `uv`). `setup-pnpm` likewise rejects an `install-args` list
  without `--frozen-lockfile` unless it carries `--allow-unfrozen-lockfile`. Callers that
  already pass `--frozen`/`--frozen-lockfile` (every example in this repo) are unaffected.
- **Guard hardening, each red-proofed with a bypass that now fails:** the top-level
  permissions check rejects `permissions: write-all` and flow-style
  `permissions: {contents: write}`; the run-block injection scanner is
  whitespace-insensitive inside `${{ }}`; the workflow argument validations
  (poe gate task, pip-audit export args, pnpm audit level) are asserted by *executing*
  the extracted scripts against good/bad inputs instead of grepping for error strings;
  the first-party lineage guard fails on an empty ref set instead of passing vacuously
  and scans `*.yaml` as well as `*.yml`.
- **zizmor online audits are ON** for both the repo scan and the staged `examples/`
  audit (GH_TOKEN required; a missing token is an error, not a silent downgrade). The
  ten major-only pin comments the new audit flagged now name exact releases.
- **`python-publish.yml` is marked SAME-REPO ONLY** (PyPI Trusted Publishing cannot
  match a cross-repo `job_workflow_ref`); cross-repo consumers copy the inline job from
  `examples/edge-proc/publish.yml` or `examples/assay/publish.yml` — both taken verbatim
  from callers with green release runs (assay run 29887096259, privacy-core run
  29886074787 for the npm half).

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
  (6 attempts, ~60s) and fail if it is not served. `edgeproc-core` (then named
  `shared-libs-python`) had six green
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
  provenance needs a public repo). Example callers added for shared-libs-python (PyPI —
  the repo is now `edgeproc-core`)
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
