#!/usr/bin/env bash
# Scenario suite for the consumer drift detector.
#
# Two things are asserted here, and both need synthetic input:
#
#   1. THE CLASSIFIER'S POLARITY. A drift detector that only ever says "DRIFT"
#      would look perfect against today's consumers and be worthless tomorrow,
#      and one that only ever says "ADOPTED" is a green light with no bulb. Every
#      control below is fixtured twice — once hand-rolled, once calling the
#      hseshadr/ci workflow — so neither answer can be the constant one. An
#      inert workflow that implements no control must produce NO findings at all.
#
#   2. THE EXIT-CODE CONTRACT. New drift fails, allowlisted drift passes, a stale
#      allowlist entry warns without failing, an unreachable repo is skipped
#      rather than failed, and an allowlist entry with no reason is a hard tool
#      error. Those are policy decisions; they are worth pinning to fixtures
#      instead of to whatever the consumers happen to contain this week.
#
# The suite never touches the network: the detector's --local mode reads a
# fixture tree laid out exactly like the API results it normally fetches.
set -uo pipefail

suite_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
classifier="$suite_root/tests/lib/classify-workflow.rb"
detector="$suite_root/tests/consumer-drift.sh"
real_allowlist="$suite_root/tests/consumer-drift-allowlist.txt"

suite_failures=0

report() {
  printf 'FAIL: %s\n' "$*" >&2
  suite_failures=$((suite_failures + 1))
}

fixtures="$(mktemp -d)"
trap 'rm -rf "${fixtures:-}"' EXIT

# A real SHA-shaped pin: adoption is recognised by the ref's PATH, and a fixture
# that used a moving tag would be testing something the security policy forbids.
PIN="@bc68fde66f0805971e1b9aa444933b7975da80b1 # ci-v2.0.3"

write_fixture() {
  local path="$fixtures/$1"
  mkdir -p "$(dirname "$path")"
  cat > "$path"
}

# "<category> <verdict>" for one fixture, one per line.
verdicts() {
  ruby "$classifier" "$fixtures/$1" | awk -F'\t' 'NF > 1 { print $2, $3 }'
}

expect_verdicts() {
  local path="$1" description="$2"
  shift 2
  local expected="" actual
  [[ $# -eq 0 ]] || expected="$(printf '%s\n' "$@" | sort)"
  actual="$(verdicts "$path" | sort)"
  [[ "$expected" == "$actual" ]] ||
    report "$description: expected [$expected], got [$actual]"
}

expect_exit() {
  local expected="$1" description="$2"
  shift 2
  local actual=0
  "$@" >/dev/null 2>&1 || actual=$?
  [[ "$actual" -eq "$expected" ]] ||
    report "$description (exit $actual, expected $expected)"
}

expect_output_contains() {
  local needle="$1" description="$2"
  shift 2
  local output
  output="$("$@" 2>&1)"
  [[ "$output" == *"$needle"* ]] ||
    report "$description: report never mentioned '$needle'"
}

# One report ROW must carry both needles. Column-width-independent on purpose:
# asserting the padded string would make a cosmetic table change read as a
# behaviour change, and would let "control X somewhere, verdict Y somewhere else"
# pass as "control X has verdict Y".
expect_row() {
  local control="$1" verdict="$2" description="$3"
  shift 3
  local output
  output="$("$@" 2>&1)"
  awk -v c="$control" -v v="$verdict" \
    'index($0, c) && index($0, v) { hit = 1 } END { exit !hit }' <<< "$output" ||
    report "$description: no row shows '$control' with '$verdict'"
}

# --- fixtures ---------------------------------------------------------------

write_fixture drifty/deploy.yml <<YAML
name: Deploy
on: {push: {branches: [main]}}
permissions: {contents: read}
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: cloudflare/wrangler-action@v3
        with:
          command: pages deploy dist --project-name=drifty
YAML

write_fixture adopter/deploy.yml <<YAML
name: Deploy
on: {workflow_dispatch: null}
permissions: {contents: read}
jobs:
  deploy:
    uses: hseshadr/ci/.github/workflows/cloudflare-pages-deploy.yml$PIN
    with: {project-name: adopter, dist-dir: dist}
YAML

write_fixture compositer/deploy.yml <<YAML
name: Deploy
on: {workflow_dispatch: null}
permissions: {contents: read}
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: hseshadr/ci/.github/actions/pages-deploy-dist$PIN
        with: {project-name: compositer, dist-dir: dist}
YAML

# Same hand-rolled deploy, filed under a name no filename heuristic would catch.
write_fixture renamed/release-site.yml <<YAML
name: Ship the site
on: {workflow_dispatch: null}
permissions: {contents: read}
jobs:
  ship:
    runs-on: ubuntu-latest
    steps:
      - run: npx wrangler pages deploy dist --project-name=renamed
YAML

write_fixture scanner/security.yml <<YAML
name: Security
on: {schedule: [{cron: "0 6 * * *"}]}
permissions: {contents: read}
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - run: gitleaks detect --no-banner
      - run: pip-audit --strict
YAML

# AN ALLOWLIST ENTRY COVERS ONE CONTROL, NOT THE FILE IT LIVES IN.
#
# This is not hypothetical. On 2026-08-02 the sweep went red on
# aml-filter/ci.yml/secret-scan — a gitleaks job added the day before — while
# aml-filter/ci.yml/frontend-gate had been allowlisted since 2026-07-26. Had the
# allowlist key been read at file granularity, the older entry would have
# swallowed the new control silently and the detector would have reported a clean
# run on the day it was most needed. The fixture below pins the granularity so a
# refactor of allowlist_index cannot quietly widen it.
write_fixture halfknown/ci.yml <<YAML
name: CI
on: {pull_request: null}
permissions: {contents: read}
jobs:
  checks:
    runs-on: ubuntu-latest
    steps:
      - run: pip-audit --strict
      - run: gitleaks detect --no-banner
YAML

# Every consumer routes pip-audit through poe. That is a dependency audit, not a
# Python gate — and the neighbouring `uv run poe gate` fixture proves the carve
# out is keyed to the audit task alone, not to poe.
write_fixture poe-auditor/security.yml <<YAML
name: Security
on: {schedule: [{cron: "0 6 * * *"}]}
permissions: {contents: read}
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - run: uv run poe audit
YAML

write_fixture scanner-adopter/security.yml <<YAML
name: Security
on: {schedule: [{cron: "0 6 * * *"}]}
permissions: {contents: read}
jobs:
  secrets:
    uses: hseshadr/ci/.github/workflows/secret-scan.yml$PIN
  deps:
    uses: hseshadr/ci/.github/workflows/security-audit.yml$PIN
YAML

# The documented cross-repo exception: PyPI Trusted Publishing matches
# job_workflow_ref, so this job CANNOT call a reusable workflow and still be
# recognised by the registered publisher. Inline + OIDC + attestations is the
# pattern hseshadr/ci ships in examples/*/publish.yml. The `uv run poe gate`
# step is the release precondition that pattern carries, not a second gate.
write_fixture pypi-good/publish.yml <<YAML
name: Publish
on: {push: {tags: ["v*"]}}
permissions: {contents: read}
jobs:
  publish-pypi:
    runs-on: ubuntu-latest
    permissions: {id-token: write, contents: read}
    steps:
      - run: uv run poe gate
      - run: uv build --out-dir dist
      - uses: pypa/gh-action-pypi-publish@ba38be9e461d3875417946c167d0b5f3d385a247 # v1.14.1
        with: {packages-dir: dist, attestations: true}
YAML

# Same action, neither property: a long-lived token instead of OIDC and no
# attestations. The exemption must not stretch to cover this.
write_fixture pypi-bad/publish.yml <<YAML
name: Publish
on: {push: {tags: ["v*"]}}
permissions: {contents: read}
jobs:
  publish-pypi:
    runs-on: ubuntu-latest
    steps:
      - uses: pypa/gh-action-pypi-publish@ba38be9e461d3875417946c167d0b5f3d385a247 # v1.14.1
        with: {packages-dir: dist, password: "\${{ secrets.PYPI_TOKEN }}"}
YAML

write_fixture gater/ci.yml <<YAML
name: CI
on: {pull_request: null}
permissions: {contents: read}
jobs:
  python:
    runs-on: ubuntu-latest
    steps:
      - run: uv run poe gate
  frontend:
    runs-on: ubuntu-latest
    steps:
      - run: pnpm gate
YAML

write_fixture gater-adopter/ci.yml <<YAML
name: CI
on: {pull_request: null}
permissions: {contents: read}
jobs:
  python:
    uses: hseshadr/ci/.github/workflows/python-gate.yml$PIN
  frontend:
    uses: hseshadr/ci/.github/workflows/frontend-gate.yml$PIN
YAML

# A bespoke frontend job that still uses the published browser-install composite.
# frontend-gate.yml cannot express every frontend job (a model-weights cache is a
# `uses:`, not a command), so the composite is the shared unit here — and the
# bare-`playwright install` fixture above keeps that from becoming a loophole.
write_fixture playwright-adopter/ci.yml <<YAML
name: CI
on: {pull_request: null}
permissions: {contents: read}
jobs:
  e2e:
    runs-on: ubuntu-latest
    steps:
      - uses: hseshadr/ci/.github/actions/setup-playwright$PIN
      - run: pnpm run gate:e2e
YAML

write_fixture playwright-drifty/ci.yml <<YAML
name: CI
on: {pull_request: null}
permissions: {contents: read}
jobs:
  e2e:
    runs-on: ubuntu-latest
    steps:
      - run: pnpm exec playwright install --with-deps chromium
      - run: pnpm run gate:e2e
YAML

write_fixture npm-drifty/release.yml <<YAML
name: Release
on: {push: {tags: ["v*"]}}
permissions: {contents: read}
jobs:
  npm:
    runs-on: ubuntu-latest
    steps:
      - run: pnpm publish --access public --no-git-checks
YAML

# A SHELL COMMENT is not a control. almamesh/deploy.yml's only occurrence of the
# word "gitleaks" is a comment inside an unrelated "Ping IndexNow" step, and the
# classifier's raw-text match reported it as a hand-rolled secret scan — one of
# the 30 findings in the first live sweep was this false positive. A detector that
# cries wolf gets ignored, so the fixture pins the distinction in both polarities:
# the comment alone is NOT drift, and the neighbouring real-command fixture
# (scanner/security.yml) proves the detector still fires on the real thing.
write_fixture commented/deploy.yml <<YAML
name: Deploy
on: {workflow_dispatch: null}
permissions: {contents: read}
jobs:
  ping:
    runs-on: ubuntu-latest
    steps:
      - run: |
          # (gitleaks false-positives on inline keys) and rotation is automatic.
          curl -fsS https://example.invalid/ping   # pip-audit runs elsewhere
          echo done
YAML

write_fixture inert/label.yml <<YAML
name: Label
on: {issues: {types: [opened]}}
permissions: {contents: read}
jobs:
  label:
    runs-on: ubuntu-latest
    steps:
      - run: echo "nothing this repo publishes"
YAML

# --- classifier polarity ----------------------------------------------------

expect_verdicts drifty/deploy.yml \
  "a hand-rolled wrangler-action Pages deploy is drift" \
  "pages-deploy DRIFT"
expect_verdicts adopter/deploy.yml \
  "a caller of cloudflare-pages-deploy.yml is adopted" \
  "pages-deploy ADOPTED"
expect_verdicts compositer/deploy.yml \
  "a user of the pages-deploy-dist composite is adopted" \
  "pages-deploy ADOPTED"
expect_verdicts renamed/release-site.yml \
  "a hand-rolled deploy filed under a non-deploy filename is still drift" \
  "pages-deploy DRIFT"
expect_verdicts scanner/security.yml \
  "inline gitleaks and pip-audit are two separate drifts" \
  "secret-scan DRIFT" "dependency-audit DRIFT"
expect_verdicts poe-auditor/security.yml \
  "\`uv run poe audit\` is a dependency audit, not a hand-rolled python gate" \
  "dependency-audit DRIFT"
expect_verdicts scanner-adopter/security.yml \
  "callers of secret-scan.yml and security-audit.yml are adopted" \
  "secret-scan ADOPTED" "dependency-audit ADOPTED"
expect_verdicts pypi-good/publish.yml \
  "inline PyPI with OIDC + attestations is adopted by pattern, and its inline gate is not counted twice" \
  "pypi-publish ADOPTED-BY-PATTERN"
expect_verdicts pypi-bad/publish.yml \
  "inline PyPI with a token and no attestations is drift" \
  "pypi-publish DRIFT"
expect_verdicts gater/ci.yml \
  "hand-rolled python and frontend gates in an ordinary ci.yml are drift" \
  "python-gate DRIFT" "frontend-gate DRIFT"
expect_verdicts gater-adopter/ci.yml \
  "callers of python-gate.yml and frontend-gate.yml are adopted" \
  "python-gate ADOPTED" "frontend-gate ADOPTED"
expect_verdicts playwright-adopter/ci.yml \
  "a bespoke frontend job using the setup-playwright composite is adopted" \
  "frontend-gate ADOPTED"
expect_verdicts playwright-drifty/ci.yml \
  "a bare \`playwright install\` adopts nothing and stays drift" \
  "frontend-gate DRIFT"
expect_verdicts npm-drifty/release.yml \
  "an inline pnpm publish is drift" \
  "npm-publish DRIFT"
expect_verdicts commented/deploy.yml \
  "a control named only inside a shell COMMENT is not a hand-rolled control"
expect_verdicts inert/label.yml \
  "a workflow implementing no published control produces no findings"

# The other half of the same property: stripping comments must not blind the
# detector to a command that legitimately contains a '#' character.
write_fixture hashy/security.yml <<YAML
name: Security
on: {schedule: [{cron: "0 6 * * *"}]}
permissions: {contents: read}
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - run: |
          echo "release #42 audit"
          gitleaks detect --no-banner
YAML

expect_verdicts hashy/security.yml \
  "a '#' inside a quoted string does not hide the real gitleaks command that follows" \
  "secret-scan DRIFT"

# --- exit-code contract ------------------------------------------------------

empty_allowlist="$fixtures/empty-allowlist.txt"
printf '# no entries\n' > "$empty_allowlist"

populated_allowlist="$fixtures/populated-allowlist.txt"
printf 'drifty/deploy.yml/pages-deploy|known, scheduled for convergence\n' > "$populated_allowlist"

stale_allowlist="$fixtures/stale-allowlist.txt"
printf 'adopter/deploy.yml/pages-deploy|converged already; this entry outlived its drift\n' \
  > "$stale_allowlist"

reasonless_allowlist="$fixtures/reasonless-allowlist.txt"
printf 'drifty/deploy.yml/pages-deploy|\n' > "$reasonless_allowlist"

# Covers ONE of the two controls in halfknown/ci.yml. The other must still fail.
partial_allowlist="$fixtures/partial-allowlist.txt"
printf 'halfknown/ci.yml/dependency-audit|known, scheduled for convergence\n' \
  > "$partial_allowlist"

expect_exit 1 "new drift fails the run" \
  "$detector" --local "$fixtures" --consumers "drifty" --allowlist "$empty_allowlist"
expect_exit 0 "allowlisted drift passes the run" \
  "$detector" --local "$fixtures" --consumers "drifty" --allowlist "$populated_allowlist"
expect_output_contains "DRIFT (allowlisted)" "allowlisted drift is still reported" \
  "$detector" --local "$fixtures" --consumers "drifty" --allowlist "$populated_allowlist"
expect_exit 0 "a stale allowlist entry warns instead of failing" \
  "$detector" --local "$fixtures" --consumers "adopter" --allowlist "$stale_allowlist"
expect_output_contains "Stale allowlist entries" "a stale allowlist entry is named" \
  "$detector" --local "$fixtures" --consumers "adopter" --allowlist "$stale_allowlist"
# THIS CASE USED TO ENCODE THE DEFECT, AND HAS BEEN INVERTED.
#
# It previously asserted `expect_exit 0` for --consumers "no-such-repo": a sweep
# that reached ZERO repositories was pinned as a PASS. That is the shape that let
# the scheduled run report success daily while inspecting nothing — the failure
# only ever disclosed by a log line nobody reads. Inspecting nothing is
# indistinguishable from a clean portfolio, so it is now exit 2 ("the detector
# could not do its job"), never 0.
#
# The original INTENT — one unreachable repo must not fail the build — is real
# and is preserved by the first case below. Only the vacuous-total-miss reading
# is gone.
expect_exit 0 "one unreachable repository among reachable ones is skipped, not failed" \
  "$detector" --local "$fixtures" --consumers "drifty no-such-repo" --allowlist "$populated_allowlist"
expect_output_contains "not found" "an unreachable repository is named in the report" \
  "$detector" --local "$fixtures" --consumers "drifty no-such-repo" --allowlist "$populated_allowlist"
expect_exit 2 "a sweep that reached NO repositories is a tool error, not a clean bill of health" \
  "$detector" --local "$fixtures" --consumers "no-such-repo" --allowlist "$empty_allowlist"
expect_output_contains "inspected 0 of 1" "a sweep that inspected nothing says so loudly" \
  "$detector" --local "$fixtures" --consumers "no-such-repo" --allowlist "$empty_allowlist"
expect_exit 2 "an allowlist entry with no reason is a tool error" \
  "$detector" --local "$fixtures" --consumers "drifty" --allowlist "$reasonless_allowlist"

# The 2026-08-02 regression, pinned in both polarities: an entry for one control
# in a file must not suppress a DIFFERENT control in that same file, and must
# still suppress its own.
expect_exit 1 "an allowlisted control does not cover a second control in the same file" \
  "$detector" --local "$fixtures" --consumers "halfknown" --allowlist "$partial_allowlist"
expect_row "secret-scan" "DRIFT (NEW)" \
  "the un-allowlisted control in a partly-allowlisted file is named as NEW" \
  "$detector" --local "$fixtures" --consumers "halfknown" --allowlist "$partial_allowlist"
expect_row "dependency-audit" "DRIFT (allowlisted)" \
  "the allowlisted control in that same file is still suppressed" \
  "$detector" --local "$fixtures" --consumers "halfknown" --allowlist "$partial_allowlist"

# The checked-in allowlist has to survive the same parser, mandatory reasons and
# all — a malformed one would otherwise only surface on the day it is consulted.
expect_exit 0 "the checked-in allowlist parses" \
  "$detector" --local "$fixtures" --consumers "adopter" --allowlist "$real_allowlist"

if ((suite_failures > 0)); then
  printf '\n%d consumer drift case(s) failed.\n' "$suite_failures" >&2
  exit 1
fi

printf 'Consumer drift cases passed.\n'
