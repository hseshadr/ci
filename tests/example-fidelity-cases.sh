#!/usr/bin/env bash
# Scenario suite for the example-fidelity checker.
#
# WHY SYNTHETIC FIXTURES
#   The checker's real run is only as good as its ability to say NO. Run against
#   today's examples/ it prints "everything resolves" — which is exactly what a
#   checker that resolved nothing would print. Every property below is therefore
#   fixtured in BOTH polarities: a reference that exists must be OK, and the same
#   reference removed must be MISSING. Neither answer can be the constant one.
#
#   The third status matters just as much. A consumer with no clone must be
#   UNVERIFIABLE — never OK (which would be a silent pass) and never MISSING
#   (which would be a false alarm on any machine that has not cloned that repo).
#   That distinction is the whole reason this checker has three statuses.
#
# The suite never touches the network: it builds a throwaway git repository and
# points the checker at it.
set -uo pipefail

suite_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
resolver="$suite_root/tests/lib/example-references.rb"
runner="$suite_root/tests/example-fidelity.sh"

suite_failures=0

report() {
  printf 'FAIL: %s\n' "$*" >&2
  suite_failures=$((suite_failures + 1))
}

work="$(mktemp -d)"
trap 'rm -rf "${work:-}"' EXIT

consumers="$work/consumers"
examples="$work/examples"
mkdir -p "$consumers" "$examples/demo"

# --- a synthetic consumer repository ----------------------------------------
# Committed, on `main`, so the checker resolves it the same way it resolves a
# real clone: through a git ref, never the working tree.
build_consumer() {
  local repo="$consumers/demo"
  mkdir -p "$repo/frontend/scripts" "$repo/backend"
  printf '22.13.0\n' > "$repo/frontend/.nvmrc"
  printf '{"name":"demo-frontend","scripts":{"gate":"biome check"}}\n' > "$repo/frontend/package.json"
  printf '{"name":"@demo/inner","scripts":{"build:pages":"vite build"}}\n' > "$repo/frontend/scripts/package.json"
  printf 'console.log(1)\n' > "$repo/frontend/scripts/download-model.mjs"
  printf '[tool.poe.tasks]\ngate = "pytest"\n' > "$repo/backend/pyproject.toml"
  git -c init.defaultBranch=main init --quiet "$repo"
  git -C "$repo" add -A
  git -C "$repo" -c user.email=t@t -c user.name=t commit --quiet -m "fixture"
}
build_consumer

resolve() {
  ruby "$resolver" --consumer-root "$consumers" --ci-root "$suite_root" "$examples/demo/$1"
}

# "<status> <kind> <reference>" for one example, one per line.
statuses() {
  resolve "$1" | awk -F'\t' '{ print $2, $3, $4 }'
}

expect_status() {
  local file="$1" needle="$2" description="$3"
  local actual
  actual="$(statuses "$file")"
  grep -Fq "$needle" <<< "$actual" ||
    report "$description: never produced '$needle'. Got:
$actual"
}

expect_no_status() {
  local file="$1" needle="$2" description="$3"
  local actual
  actual="$(statuses "$file")"
  grep -Fq "$needle" <<< "$actual" &&
    report "$description: unexpectedly produced '$needle'"
  return 0
}

expect_exit() {
  local expected="$1" description="$2"
  shift 2
  local actual=0
  "$@" >/dev/null 2>&1 || actual=$?
  [[ "$actual" -eq "$expected" ]] ||
    report "$description (exit $actual, expected $expected)"
}

write_example() {
  cat > "$examples/demo/$1"
}

PIN="@2a575cd193e2e1fc093ccd26821020538e2547b7 # ci-v3.0.0"

# --- files: present vs absent ------------------------------------------------
write_example good-file.yml <<YAML
name: CI
on: {pull_request: null}
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - uses: hseshadr/ci/.github/actions/setup-pnpm$PIN
        with:
          node-version-file: frontend/.nvmrc
          package-json-file: frontend/package.json
YAML

write_example bad-file.yml <<YAML
name: CI
on: {pull_request: null}
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - uses: hseshadr/ci/.github/actions/setup-pnpm$PIN
        with:
          node-version-file: frontend/.node-version
YAML

expect_status good-file.yml "OK file frontend/.nvmrc" \
  "a version file the consumer has resolves"
expect_status bad-file.yml "MISSING file frontend/.node-version" \
  "a version file the consumer does NOT have is MISSING (the real edge-reco defect)"

# --- directories: present vs absent -----------------------------------------
write_example good-dir.yml <<YAML
name: CI
on: {pull_request: null}
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - name: gate
        working-directory: frontend
        run: echo hi
YAML

write_example bad-dir.yml <<YAML
name: CI
on: {pull_request: null}
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - name: gate
        working-directory: frontend/nope
        run: echo hi
YAML

expect_status good-dir.yml "OK dir frontend" "an existing working-directory resolves"
expect_status bad-dir.yml "MISSING dir frontend/nope" "a non-existent working-directory is MISSING"

# --- package scripts: present vs absent -------------------------------------
write_example good-script.yml <<YAML
name: CI
on: {pull_request: null}
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - working-directory: frontend
        run: pnpm run gate
YAML

write_example bad-script.yml <<YAML
name: CI
on: {pull_request: null}
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - working-directory: frontend
        run: pnpm run sign:bundle
YAML

expect_status good-script.yml "OK pnpm-script" "a package script the consumer defines resolves"
expect_status bad-script.yml "MISSING pnpm-script" \
  "a package script the consumer does NOT define is MISSING (the real aml-filter defect)"

# --- workspace filters select by package NAME, not by -C directory ----------
# Regression: `-F` is pnpm's short --filter. Reading it as a directory made
# edge-reco's `pnpm -C frontend -F frontend run build:pages` look broken when the
# script exists in the filtered package. A false alarm trains people to ignore
# the guard, so this is pinned in both directions.
write_example filter-script.yml <<YAML
name: CI
on: {pull_request: null}
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - run: pnpm -C frontend -F @demo/inner run build:pages
YAML

write_example filter-missing.yml <<YAML
name: CI
on: {pull_request: null}
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - run: pnpm -C frontend -F @demo/inner run no-such-script
YAML

expect_status filter-script.yml "OK pnpm-script" \
  "-F selects the workspace package by name, so its script resolves"
expect_no_status filter-script.yml "MISSING" \
  "-F must not be misread as a directory (false-alarm regression)"
expect_status filter-missing.yml "MISSING pnpm-script" \
  "a script absent from the FILTERED package is still MISSING"

# --- node scripts and poe tasks ---------------------------------------------
write_example good-node.yml <<YAML
name: CI
on: {pull_request: null}
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - working-directory: frontend
        run: node scripts/download-model.mjs
YAML

write_example bad-node.yml <<YAML
name: CI
on: {pull_request: null}
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - working-directory: frontend
        run: node scripts/fetch-weights.mjs
YAML

write_example good-poe.yml <<YAML
name: CI
on: {pull_request: null}
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - working-directory: backend
        run: uv run poe gate
YAML

write_example bad-poe.yml <<YAML
name: CI
on: {pull_request: null}
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - working-directory: backend
        run: uv run poe nosuchtask
YAML

expect_status good-node.yml "OK node-script" "an existing node script resolves"
expect_status bad-node.yml "MISSING node-script" \
  "a non-existent node script is MISSING (the real aml-filter fetch-weights defect)"
expect_status good-poe.yml "OK poe-task" "a declared poe task resolves"
expect_status bad-poe.yml "MISSING poe-task" "an undeclared poe task is MISSING"

# --- references into THIS repository ----------------------------------------
write_example good-brick.yml <<YAML
name: CI
on: {pull_request: null}
jobs:
  a:
    uses: hseshadr/ci/.github/workflows/python-gate.yml$PIN
YAML

write_example bad-brick.yml <<YAML
name: CI
on: {pull_request: null}
jobs:
  a:
    uses: hseshadr/ci/.github/workflows/no-such-brick.yml$PIN
YAML

write_example bad-brick-input.yml <<YAML
name: CI
on: {pull_request: null}
jobs:
  a:
    uses: hseshadr/ci/.github/workflows/secret-scan.yml$PIN
    with:
      not-a-real-input: "true"
YAML

expect_status good-brick.yml "OK brick .github/workflows/python-gate.yml" \
  "an example naming a published brick resolves"
expect_status bad-brick.yml "MISSING brick .github/workflows/no-such-brick.yml" \
  "an example naming a brick this repo does not publish is MISSING"
expect_status bad-brick-input.yml "MISSING brick-input" \
  "an input the brick does not declare is MISSING — Actions would ignore it silently"

# --- the third status: cannot verify is neither pass nor fail ----------------
mkdir -p "$examples/absent-consumer"
cat > "$examples/absent-consumer/ci.yml" <<YAML
name: CI
on: {pull_request: null}
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - working-directory: frontend
        run: echo hi
YAML

no_clone="$(ruby "$resolver" --consumer-root "$consumers" --ci-root "$suite_root" \
  "$examples/absent-consumer/ci.yml" | awk -F'\t' '{ print $2 }')"
grep -Fqx "UNVERIFIABLE" <<< "$no_clone" ||
  report "a consumer with no clone must be UNVERIFIABLE, got [$no_clone]"
grep -Fqx "OK" <<< "$no_clone" &&
  report "a consumer with no clone reported OK — that is a silent pass"
grep -Fqx "MISSING" <<< "$no_clone" &&
  report "a consumer with no clone reported MISSING — that is a false alarm"

# --- the runner's exit-code contract ----------------------------------------
# A broken reference fails (1). An unverifiable run fails DIFFERENTLY (2), so
# "could not check" can never be read as "checked and clean".
expect_exit 1 "a MISSING reference fails the run" \
  env MIN_RESOLVED_REFERENCES=1 MIN_VERIFIED_CONSUMERS=1 \
  "$runner" --consumer-root "$consumers" --examples "$examples"
expect_exit 2 "a run that verified nothing exits 2, never 0" \
  "$runner" --consumer-root "$work/nowhere" --examples "$examples"
# The floors themselves must be able to fail, or they are decoration: a run that
# resolves a handful of references while the floor expects 120 is not a pass.
expect_exit 2 "a run below the coverage floor exits 2 even with zero MISSING" \
  env MIN_RESOLVED_REFERENCES=99999 \
  "$runner" --consumer-root "$consumers" --examples "$examples"

if ((suite_failures > 0)); then
  printf '\n%d example fidelity case(s) failed.\n' "$suite_failures" >&2
  exit 1
fi

printf 'Example fidelity cases passed.\n'
