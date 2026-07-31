#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

failures=0

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  failures=$((failures + 1))
}

expect_failure() {
  local description="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    fail "$description"
  fi
}

expect_success() {
  local description="$1"
  shift
  if ! "$@" >/dev/null 2>&1; then
    fail "$description"
  fi
}

expect_exit() {
  local expected="$1" description="$2"
  shift 2
  local actual=0
  "$@" >/dev/null 2>&1 || actual=$?
  [[ "$actual" -eq "$expected" ]] ||
    fail "$description (exit $actual, expected $expected)"
}

# Fail soft. This suite used to run under a bare `set -e`, so the first check
# that hit a malformed YAML file died inside a command substitution and took the
# whole script with it — the remaining checks never ran and the run still looked
# like a single tidy failure. Invoking every validator through `||` both records
# the abort AND suppresses errexit inside the function body, so one broken input
# costs you that check and nothing else.
run_check() {
  local check="$1"
  "$check" || fail "$check aborted before completing"
}

yaml_sources() {
  find .github examples -type f \( -name '*.yml' -o -name '*.yaml' \) | sort
}

# EVERY file-scanning guard below is vacuously green on an empty input set: a `find`
# whose path moved, a `--include` glob that stops matching a renamed extension, or a
# directory rename turns "no violations found" into "nothing was looked at" — and the
# two are indistinguishable from the outside. Only the lineage guard asserted its input
# was non-empty; the other eight were one typo away from being decorative forever.
#
# The floors are deliberately well above one. A guard that scanned 1 of 31 files is
# still broken, and "at least one" would not notice.
YAML_SOURCE_FLOOR=20
PERMISSION_SOURCE_FLOOR=15
USES_LINE_FLOOR=30

inputs_are_sufficient() {
  local count="$1" minimum="${2:-1}"
  [[ "$count" -ge "$minimum" ]]
}

require_inputs() {
  local label="$1" count="$2" minimum="${3:-1}"
  inputs_are_sufficient "$count" "$minimum" ||
    fail "$label scanned $count input(s), expected at least $minimum — its input set has drifted and the check is now vacuous"
}

validate_input_floor_cases() {
  expect_failure "input-floor helper accepts an EMPTY input set" \
    inputs_are_sufficient 0 1
  expect_failure "input-floor helper accepts a set that collapsed below its floor" \
    inputs_are_sufficient 3 "$YAML_SOURCE_FLOOR"
  expect_success "input-floor helper rejects a set at its floor" \
    inputs_are_sufficient "$YAML_SOURCE_FLOOR" "$YAML_SOURCE_FLOOR"
}

validate_yaml() {
  local count=0
  while IFS= read -r file; do
    count=$((count + 1))
    ruby -e 'require "yaml"; YAML.safe_load(File.read(ARGV.fetch(0)), aliases: true)' "$file" ||
      fail "$file is not valid YAML"
  done < <(yaml_sources)
  require_inputs "YAML validation" "$count" "$YAML_SOURCE_FLOOR"
}

validate_action_pins() {
  local count=0
  while IFS=: read -r file line_number line; do
    count=$((count + 1))
    ref="${line#*uses:}"
    ref="${ref%%#*}"
    ref="${ref#"${ref%%[![:space:]]*}"}"
    ref="${ref%"${ref##*[![:space:]]}"}"

    # Local (./) actions ship in the same commit, so they are already
    # immutable. Everything else — including our own hseshadr/ci refs —
    # must name a commit. First-party is not the same as trustworthy: a
    # nested @ci-v1 inside a reusable workflow still resolves through a
    # mutable tag at run time, so a consumer's SHA pin was only skin-deep.
    case "$ref" in
      ./* | docker://*) continue ;;
    esac

    if [[ ! "$ref" =~ @[0-9a-f]{40}$ ]]; then
      fail "$file:$line_number action is not pinned to a full commit SHA: $ref"
    fi
    if [[ ! "$line" =~ \#[[:space:]]+(ci-)?v[0-9] ]]; then
      fail "$file:$line_number pinned action lacks a Dependabot version comment"
    fi
  done < <(
    while IFS= read -r file; do
      awk '/^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]+/ {
        printf "%s:%d:%s\n", FILENAME, NR, $0
      }' "$file"
    done < <(yaml_sources)
  )
  require_inputs "action-pin scan" "$count" "$USES_LINE_FLOOR"
}

# Verdict on ONE file's top-level permissions:
#   0 = declared and read-only, 1 = not declared, 2 = grants a top-level write.
# Extracted from validate_permissions so tests can drive it against fixtures.
#
# THIS IS A YAML PARSE, NOT A LINE SCAN. The awk version it replaces reasoned about
# the TEXT of the `permissions:` line, which meant it only understood the two
# spellings its author had in mind. Three others slipped straight through, each a
# real top-level `contents: write`:
#
#   permissions:            permissions: {          x-perms: &perms
#     {contents: write}       contents: write         contents: write
#                           }                       permissions: *perms
#
# The first two put the write on a line the scanner had stopped reading; the third
# hides it behind a YAML alias, which no amount of line matching resolves. A parse
# sees one value in every spelling — and YAML.safe_load already runs with
# `aliases: true` elsewhere in this suite, so the alias form was always loadable.
check_top_level_permissions() {
  # The Ruby program is quoted verbatim; nothing in it is a shell expansion.
  # shellcheck disable=SC2016
  ruby -r yaml -e '
    document = YAML.safe_load(File.read(ARGV.fetch(0)), aliases: true)
    exit 1 unless document.is_a?(Hash)

    value = document["permissions"]
    exit 1 if value.nil?

    grants_write =
      case value
      when String then value.strip.match?(/write/)
      when Hash then value.values.any? { |scope| scope.to_s.strip.match?(/write/) }
      else true # an unrecognised shape gets the loudest verdict, never the quietest
      end
    exit(grants_write ? 2 : 0)
  ' "$1"
}

validate_permissions() {
  local verdict count=0
  while IFS= read -r file; do
    count=$((count + 1))
    verdict=0
    check_top_level_permissions "$file" || verdict=$?
    case "$verdict" in
      1) fail "$file does not declare top-level permissions" ;;
      2) fail "$file grants a top-level write permission" ;;
    esac
  done < <(find .github/workflows examples -type f \( -name '*.yml' -o -name '*.yaml' \) | sort)
  require_inputs "top-level permissions guard" "$count" "$PERMISSION_SOURCE_FLOOR"
}

# The guard above is only worth having if it actually REJECTS the writes it
# exists to block. These fixtures pin the property, not the shape: the two
# single-line forms (`permissions: write-all`, flow-style `{contents: write}`)
# used to sail through because the awk `next` skipped the value carried on the
# `permissions:` line itself.
validate_permission_guard_cases() {
  local dir
  dir="$(mktemp -d)"
  trap 'rm -rf "${dir:-}"' RETURN

  printf 'permissions: write-all\njobs: {}\n' > "$dir/write-all.yml"
  printf 'permissions: {contents: write}\njobs: {}\n' > "$dir/flow-write.yml"
  printf 'permissions: {contents: read}\njobs: {}\n' > "$dir/flow-read.yml"
  printf 'permissions: read-all\njobs: {}\n' > "$dir/read-all.yml"
  printf 'permissions:\n  contents: read\njobs:\n  publish:\n    permissions: {contents: read, id-token: write}\n' \
    > "$dir/job-level-oidc.yml"
  printf 'permissions:\n  contents: write\njobs: {}\n' > "$dir/block-write.yml"
  printf 'jobs: {}\n' > "$dir/missing.yml"
  # The three spellings an auditor walked a top-level `contents: write` through.
  printf 'permissions:\n  {contents: write}\njobs: {}\n' > "$dir/flow-next-line.yml"
  printf 'permissions: {\n  contents: write\n}\njobs: {}\n' > "$dir/flow-multiline.yml"
  printf 'x-perms: &perms\n  contents: write\npermissions: *perms\njobs: {}\n' > "$dir/aliased-write.yml"
  printf 'x-perms: &perms\n  contents: read\npermissions: *perms\njobs: {}\n' > "$dir/aliased-read.yml"

  expect_exit 2 "permissions guard passes 'permissions: write-all'" \
    check_top_level_permissions "$dir/write-all.yml"
  expect_exit 2 "permissions guard passes flow-style 'permissions: {contents: write}'" \
    check_top_level_permissions "$dir/flow-write.yml"
  expect_exit 0 "permissions guard rejects flow-style read-only permissions" \
    check_top_level_permissions "$dir/flow-read.yml"
  expect_exit 0 "permissions guard rejects 'permissions: read-all'" \
    check_top_level_permissions "$dir/read-all.yml"
  expect_exit 0 "permissions guard rejects a job-level OIDC write under a read-only top level" \
    check_top_level_permissions "$dir/job-level-oidc.yml"
  expect_exit 2 "permissions guard passes a block-style top-level write" \
    check_top_level_permissions "$dir/block-write.yml"
  expect_exit 1 "permissions guard passes a workflow with no top-level permissions" \
    check_top_level_permissions "$dir/missing.yml"
  expect_exit 2 "permissions guard passes a flow-style write on the line BELOW permissions:" \
    check_top_level_permissions "$dir/flow-next-line.yml"
  expect_exit 2 "permissions guard passes a MULTI-LINE flow-style top-level write" \
    check_top_level_permissions "$dir/flow-multiline.yml"
  expect_exit 2 "permissions guard passes a top-level write hidden behind a YAML ALIAS" \
    check_top_level_permissions "$dir/aliased-write.yml"
  expect_exit 0 "permissions guard rejects read-only permissions supplied through a YAML alias" \
    check_top_level_permissions "$dir/aliased-read.yml"
}

# Any attacker-influenced expression pasted into a `run:` block is executed by
# the shell before the script ever sees it, so quoting inside the script cannot
# save you. `inputs.*` was the only vector modelled here, which left the textbook
# one wide open: `github.event.*` carries issue titles, PR bodies, commit
# messages and branch names — all attacker-authored on a public repo. Route the
# value through `env:` and reference "$VAR" instead.
validate_shell_boundaries() {
  local findings
  local yaml_files=()

  while IFS= read -r file; do
    yaml_files+=("$file")
  done < <(yaml_sources)

  require_inputs "run-block interpolation scan" "${#yaml_files[@]}" "$YAML_SOURCE_FLOOR"

  findings="$(ruby "$repo_root/tests/lib/scan-run-interpolation.rb" "${yaml_files[@]}")" || {
    fail "run-block interpolation scan failed to execute"
    return
  }

  while IFS=$'\t' read -r file vector; do
    [[ -z "$file" ]] ||
      fail "$file interpolates $vector directly into shell code"
  done <<< "$findings"
}

# True (exit 0) when the scanner reports at least one finding for the file.
scanner_reports_finding() {
  local findings
  findings="$(ruby "$repo_root/tests/lib/scan-run-interpolation.rb" "$1")" || return 2
  [[ -n "$findings" ]]
}

# GitHub's expression syntax does not care about whitespace: `${{inputs.x}}`
# and `${{   github.event.foo }}` expand exactly like the canonical spacing.
# A scanner keyed to the one-space literal therefore missed both. These
# fixtures pin the property across spacings, and keep the env:-routed safe
# pattern (the documented remediation) unflagged.
validate_interpolation_scanner_cases() {
  local dir
  dir="$(mktemp -d)"
  trap 'rm -rf "${dir:-}"' RETURN

  cat > "$dir/no-space.yml" <<'YAML'
jobs:
  a:
    steps:
      - run: echo ${{inputs.name}}
YAML
  cat > "$dir/extra-space.yml" <<'YAML'
jobs:
  a:
    steps:
      - run: echo ${{  github.event.issue.title }}
YAML
  cat > "$dir/head-ref.yml" <<'YAML'
jobs:
  a:
    steps:
      - run: echo ${{ github.head_ref}}
YAML
  cat > "$dir/canonical.yml" <<'YAML'
jobs:
  a:
    steps:
      - run: echo ${{ inputs.name }}
YAML
  cat > "$dir/env-routed.yml" <<'YAML'
jobs:
  a:
    steps:
      - env:
          SAFE: ${{ inputs.name }}
        run: echo "$SAFE"
YAML

  expect_success "injection scanner misses \${{inputs.*}} with no inner whitespace" \
    scanner_reports_finding "$dir/no-space.yml"
  expect_success "injection scanner misses \${{ github.event.* }} with extra inner whitespace" \
    scanner_reports_finding "$dir/extra-space.yml"
  expect_success "injection scanner misses \${{ github.head_ref}} with asymmetric whitespace" \
    scanner_reports_finding "$dir/head-ref.yml"
  expect_success "injection scanner misses the canonical one-space interpolation" \
    scanner_reports_finding "$dir/canonical.yml"
  expect_failure "injection scanner flags the safe env:-routed reference" \
    scanner_reports_finding "$dir/env-routed.yml"
}

validate_checkout_credentials() {
  local unsafe_files
  local yaml_files=()

  while IFS= read -r file; do
    yaml_files+=("$file")
  done < <(yaml_sources)

  require_inputs "checkout-credentials scan" "${#yaml_files[@]}" "$YAML_SOURCE_FLOOR"

  unsafe_files="$(ruby -e '
    require "yaml"

    def walk(value, &block)
      yield value if value.is_a?(Hash)
      children = value.is_a?(Hash) ? value.values : value
      children.each { |child| walk(child, &block) } if children.is_a?(Array)
    end

    ARGV.each do |file|
      begin
        document = YAML.safe_load(File.read(file), aliases: true)
      rescue StandardError
        next
      end
      unsafe = false
      walk(document) do |node|
        next unless node["uses"].to_s.start_with?("actions/checkout@")
        unsafe ||= !node.fetch("with", {}).fetch("persist-credentials", nil).equal?(false)
      end
      puts file if unsafe
    end
  ' "${yaml_files[@]}")" || {
    fail "checkout-credentials scan failed to execute"
    return
  }

  while IFS= read -r file; do
    [[ -z "$file" ]] || fail "$file has a checkout that persists GitHub credentials"
  done <<< "$unsafe_files"
}

# `workflow_run` runs in the BASE repository with the BASE repository's secrets, and
# every `head_*` field is attacker-controlled: a fork's default branch is ALSO called
# `main`, so a fork-PR run satisfies a `head_branch == 'main'` gate and a
# `branches: [main]` trigger filter alike. Three live Pages deploys shipped exactly
# that gate — nothing checked, so nothing noticed.
#
# The check that replaced them asked whether two SUBSTRINGS appeared anywhere in any
# `if:`. That is a shape test, and it blessed five different fork-code deploys: an
# inverted `!=` pin, a pin ORed away by `|| github.run_id != ''`, a pin sitting in a
# job that holds no secrets while another job does, a gate with no
# `conclusion == 'success'` at all, and a gate with no `event == 'push'`. It also
# early-exited on `on: workflow_call` files, so the pin could be deleted outright from
# cloudflare-pages-deploy.yml with this suite still green.
#
# tests/lib/workflow-run-pin.rb replaces it with a parse + exhaustive proof; the
# rationale and the scope rule live there.
#
# Verdict on ONE file: 0 = out of scope, 1 = pinned, 2 = UNPINNED.
workflow_run_repository_pin_verdict() {
  ruby "$repo_root/tests/lib/workflow-run-pin.rb" "$1"
}

validate_workflow_run_repository_pin() {
  local verdict reason count=0
  while IFS= read -r file; do
    count=$((count + 1))
    verdict=0
    reason="$(workflow_run_repository_pin_verdict "$file")" || verdict=$?
    [[ "$verdict" -ne 2 ]] ||
      fail "$file is triggered by (or consumes) workflow_run with an unsound gate: ${reason//$'\n'/; }"
  done < <(yaml_sources)
  require_inputs "fork-deploy pin guard" "$count" "$YAML_SOURCE_FLOOR"
}

# Break the property, not the form. Every fixture is a deploy that would have gone out
# green; only the GATE differs. The first five are the auditor's bypasses of the
# substring check that shipped here — each one hands fork-authored code the base
# repository's deploy credentials while reading, to a grep, exactly like the fix.
validate_workflow_run_pin_cases() {
  local dir
  dir="$(mktemp -d)"
  trap 'rm -rf "${dir:-}"' RETURN

  local gate_prefix="github.event.workflow_run"

  cat > "$dir/inverted.yml" <<YAML
on:
  workflow_run: {workflows: ["CI"], types: [completed], branches: [main]}
jobs:
  deploy:
    if: >-
      \${{ $gate_prefix.event == 'push' &&
      $gate_prefix.conclusion == 'success' &&
      $gate_prefix.head_repository.full_name != github.repository }}
YAML
  cat > "$dir/ored-away.yml" <<YAML
on:
  workflow_run: {workflows: ["CI"], types: [completed], branches: [main]}
jobs:
  deploy:
    if: >-
      \${{ ($gate_prefix.event == 'push' &&
      $gate_prefix.conclusion == 'success' &&
      $gate_prefix.head_repository.full_name == github.repository) ||
      github.run_id != '' }}
YAML
  cat > "$dir/wrong-job.yml" <<YAML
on:
  workflow_run: {workflows: ["CI"], types: [completed], branches: [main]}
jobs:
  audit:
    if: >-
      \${{ $gate_prefix.event == 'push' &&
      $gate_prefix.conclusion == 'success' &&
      $gate_prefix.head_repository.full_name == github.repository }}
    steps: [{run: "echo audited"}]
  deploy:
    if: \${{ $gate_prefix.head_branch == 'main' }}
    steps:
      - env: {TOKEN: "\${{ secrets.CLOUDFLARE_API_TOKEN }}"}
        run: echo deploying
YAML
  cat > "$dir/no-conclusion.yml" <<YAML
on:
  workflow_run: {workflows: ["CI"], types: [completed], branches: [main]}
jobs:
  deploy:
    if: >-
      \${{ $gate_prefix.event == 'push' &&
      $gate_prefix.head_repository.full_name == github.repository }}
YAML
  cat > "$dir/no-push.yml" <<YAML
on:
  workflow_run: {workflows: ["CI"], types: [completed], branches: [main]}
jobs:
  deploy:
    if: >-
      \${{ $gate_prefix.conclusion == 'success' &&
      $gate_prefix.head_branch == 'main' &&
      $gate_prefix.head_repository.full_name == github.repository }}
YAML
  cat > "$dir/workflow-call-unpinned.yml" <<YAML
on:
  workflow_call:
    secrets: {CLOUDFLARE_API_TOKEN: {required: true}}
jobs:
  deploy:
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000
        with: {ref: "\${{ $gate_prefix.head_sha }}"}
YAML
  cat > "$dir/branch-only.yml" <<YAML
on:
  workflow_run: {workflows: ["CI"], types: [completed], branches: [main]}
jobs:
  deploy:
    if: >-
      $gate_prefix.conclusion == 'success' &&
      $gate_prefix.head_branch == 'main'
YAML
  cat > "$dir/comment-only.yml" <<YAML
on:
  workflow_run: {workflows: ["CI"], types: [completed]}
jobs:
  deploy:
    # $gate_prefix.head_repository.full_name == github.repository
    # — present as prose, absent from the gate below.
    if: $gate_prefix.conclusion == 'success'
YAML
  cat > "$dir/no-gate.yml" <<'YAML'
on:
  workflow_run: {workflows: ["CI"], types: [completed], branches: [main]}
jobs:
  deploy:
    runs-on: ubuntu-latest
YAML
  cat > "$dir/pinned.yml" <<YAML
on:
  workflow_run: {workflows: ["CI"], types: [completed], branches: [main]}
jobs:
  deploy:
    if: >-
      github.event_name == 'workflow_dispatch' ||
      ($gate_prefix.event == 'push' &&
       $gate_prefix.conclusion == 'success' &&
       $gate_prefix.head_branch == 'main' &&
       $gate_prefix.head_repository.full_name == github.repository)
YAML
  cat > "$dir/pinned-via-needs.yml" <<YAML
on:
  workflow_run: {workflows: ["CI"], types: [completed], branches: [main]}
jobs:
  preflight:
    if: >-
      $gate_prefix.event == 'push' &&
      $gate_prefix.conclusion == 'success' &&
      $gate_prefix.head_repository.full_name == github.repository
  deploy:
    needs: preflight
    if: \${{ needs.preflight.outputs.configured == 'true' }}
YAML
  cat > "$dir/needs-escaped-by-always.yml" <<YAML
on:
  workflow_run: {workflows: ["CI"], types: [completed], branches: [main]}
jobs:
  preflight:
    if: >-
      $gate_prefix.event == 'push' &&
      $gate_prefix.conclusion == 'success' &&
      $gate_prefix.head_repository.full_name == github.repository
  deploy:
    needs: preflight
    if: \${{ always() }}
YAML
  cat > "$dir/not-workflow-run.yml" <<YAML
on:
  push: {branches: [main]}
jobs:
  gate:
    if: $gate_prefix.head_branch == 'main'
YAML

  expect_exit 2 "fork-deploy guard passes an INVERTED pin (\`!= github.repository\`)" \
    workflow_run_repository_pin_verdict "$dir/inverted.yml"
  expect_exit 2 "fork-deploy guard passes a pin ORed away by \`|| github.run_id != ''\`" \
    workflow_run_repository_pin_verdict "$dir/ored-away.yml"
  expect_exit 2 "fork-deploy guard passes a pin sitting in a job OTHER than the secret-holder" \
    workflow_run_repository_pin_verdict "$dir/wrong-job.yml"
  expect_exit 2 "fork-deploy guard passes a gate with no \`conclusion == 'success'\`" \
    workflow_run_repository_pin_verdict "$dir/no-conclusion.yml"
  expect_exit 2 "fork-deploy guard passes a gate with no \`event == 'push'\`" \
    workflow_run_repository_pin_verdict "$dir/no-push.yml"
  expect_exit 2 "fork-deploy guard IGNORES an on: workflow_call file that consumes workflow_run data" \
    workflow_run_repository_pin_verdict "$dir/workflow-call-unpinned.yml"
  expect_exit 2 "fork-deploy guard passes a branch-name-only gate" \
    workflow_run_repository_pin_verdict "$dir/branch-only.yml"
  expect_exit 2 "fork-deploy guard passes a pin that lives only in a COMMENT" \
    workflow_run_repository_pin_verdict "$dir/comment-only.yml"
  expect_exit 2 "fork-deploy guard passes a workflow_run deploy with no gate at all" \
    workflow_run_repository_pin_verdict "$dir/no-gate.yml"
  expect_exit 2 "fork-deploy guard lets always() erase an inherited needs: gate" \
    workflow_run_repository_pin_verdict "$dir/needs-escaped-by-always.yml"

  expect_exit 1 "fork-deploy guard REJECTS a correctly pinned gate" \
    workflow_run_repository_pin_verdict "$dir/pinned.yml"
  expect_exit 1 "fork-deploy guard REJECTS a job pinned through its needs: chain" \
    workflow_run_repository_pin_verdict "$dir/pinned-via-needs.yml"
  expect_exit 0 "fork-deploy guard judges a workflow that never sees workflow_run data" \
    workflow_run_repository_pin_verdict "$dir/not-workflow-run.yml"
}

# A `# zizmor: ignore[dangerous-triggers]` comment on the `on:` key silences the audit
# for the WHOLE key, not for the trigger it was written about. Adding
# `pull_request_target:` underneath one of the two shipped examples makes zizmor print
# "No findings to report. Good job!" — the suppression turns the most dangerous trigger
# GitHub offers into a silent one. The comment stays (zizmor's audit is category-level
# and cannot see the repository pin that mitigates it), but it is no longer a bare
# assertion: this guard makes the exemption's preconditions machine-checked.
suppressed_dangerous_triggers_verdict() {
  # The Ruby program is quoted verbatim; nothing in it is a shell expansion.
  # shellcheck disable=SC2016
  ruby -r yaml -e '
    ALLOWED = %w[workflow_run workflow_dispatch].freeze

    file = ARGV.fetch(0)
    exit 0 unless File.read(file).include?("zizmor: ignore[dangerous-triggers]")

    document = YAML.safe_load(File.read(file), aliases: true)
    triggers = document["on"] || document[true]
    keys = triggers.is_a?(Hash) ? triggers.keys : Array(triggers)
    extra = keys - ALLOWED
    exit 0 if extra.empty?

    puts "suppression also hides #{extra.join(", ")}"
    exit 1
  ' "$1"
}

validate_dangerous_trigger_suppressions() {
  local count=0 pin_verdict
  while IFS= read -r file; do
    grep -q 'zizmor: ignore\[dangerous-triggers\]' "$file" || continue
    count=$((count + 1))
    suppressed_dangerous_triggers_verdict "$file" ||
      fail "$file suppresses zizmor's dangerous-triggers audit for triggers beyond workflow_run/workflow_dispatch"
    # The justification is "the triggering run is pinned"; that claim must be true.
    pin_verdict=0
    workflow_run_repository_pin_verdict "$file" >/dev/null || pin_verdict=$?
    [[ "$pin_verdict" -eq 1 ]] ||
      fail "$file suppresses dangerous-triggers while its own fork-deploy gate is unsound (verdict $pin_verdict)"
  done < <(yaml_sources)
  require_inputs "dangerous-triggers suppression guard" "$count" 2
}

validate_dangerous_trigger_suppression_cases() {
  local dir
  dir="$(mktemp -d)"
  trap 'rm -rf "${dir:-}"' RETURN

  cat > "$dir/hidden-target.yml" <<'YAML'
on: # zizmor: ignore[dangerous-triggers] deploy-after-CI by design
  workflow_run: {workflows: ["CI"], types: [completed], branches: [main]}
  workflow_dispatch:
  pull_request_target:
jobs: {}
YAML
  cat > "$dir/scoped.yml" <<'YAML'
on: # zizmor: ignore[dangerous-triggers] deploy-after-CI by design
  workflow_run: {workflows: ["CI"], types: [completed], branches: [main]}
  workflow_dispatch:
jobs: {}
YAML
  cat > "$dir/unsuppressed.yml" <<'YAML'
on:
  pull_request_target:
jobs: {}
YAML

  expect_failure "dangerous-triggers guard lets a suppression hide pull_request_target" \
    suppressed_dangerous_triggers_verdict "$dir/hidden-target.yml"
  expect_success "dangerous-triggers guard rejects a suppression scoped to workflow_run" \
    suppressed_dangerous_triggers_verdict "$dir/scoped.yml"
  expect_success "dangerous-triggers guard inspects a file that carries no suppression" \
    suppressed_dangerous_triggers_verdict "$dir/unsuppressed.yml"
}

validate_dependabot_cooldown() {
  ruby -e '
    require "yaml"
    config = YAML.safe_load(File.read(".github/dependabot.yml"), aliases: true)
    valid = config.fetch("updates").all? do |update|
      update.fetch("cooldown", {}).fetch("default-days", 0).to_i >= 7
    end
    exit(valid ? 0 : 1)
  ' || fail ".github/dependabot.yml requires a cooldown of at least seven days"
}

# First-party refs used to ride the moving @ci-v1 tag behind a zizmor
# suppression. That left a hole: a consumer pinning python-publish.yml to a
# SHA still got its nested setup-python-uv@ci-v1 resolved through a mutable
# tag at run time — and the publish workflows run with id-token: write, so
# moving that tag would have reached PyPI and npm. Nothing may reintroduce a
# moving first-party ref, in the workflows we run or the examples we publish.
validate_first_party_pins() {
  local yaml_files=()
  while IFS= read -r file; do
    yaml_files+=("$file")
  done < <(yaml_sources)
  # Scanning the SAME file list every other guard uses, instead of a second set of
  # `--include` globs, removes an input set that could drift on its own.
  require_inputs "first-party pin scan" "${#yaml_files[@]}" "$YAML_SOURCE_FLOOR"

  while IFS=: read -r file line_number _; do
    fail "$file:$line_number first-party ref is not pinned to a full commit SHA"
  done < <(grep -HInE \
    '^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]+hseshadr/ci/[^[:space:]]*@(ci-)?v[0-9]' \
    "${yaml_files[@]}" || true)
}

# validate_first_party_release_lineage lives in tests/lib/ so the scenario suite in
# tests/lineage-guard-cases.sh can drive the same code against synthetic repos —
# the release-commit bootstrap it has to tolerate is not reproducible in this repo
# on demand. The rationale and the exemption's exact scope are documented there.
# shellcheck source=tests/lib/first-party-lineage.sh
source "$repo_root/tests/lib/first-party-lineage.sh"

# Provenance is the one publish property that fails SILENTLY: drop `--provenance`
# or `attestations:` and the release still ships, the run is still green, and the
# only witness is the registry months later. `provenance` used to default FALSE in
# ts-publish.yml (a private-repo hangover) and examples/privacy-core still carried
# `provenance: false` long after the repo went public — a caller copying it got an
# unsigned release and a green run. Nothing could see either, because nothing looked.
validate_publish_provenance() {
  local findings
  local yaml_files=()

  while IFS= read -r file; do
    yaml_files+=("$file")
  done < <(yaml_sources)

  require_inputs "publish-provenance scan" "${#yaml_files[@]}" "$YAML_SOURCE_FLOOR"

  findings="$(ruby "$repo_root/tests/lib/scan-publish-provenance.rb" "${yaml_files[@]}")" || {
    fail "publish-provenance scan failed to execute"
    return
  }

  while IFS=$'\t' read -r file reason; do
    [[ -z "$file" ]] || fail "$file: $reason"
  done <<< "$findings"
}

# True (exit 0) when the scanner reports at least one finding for the file.
scanner_reports_provenance_finding() {
  local findings
  findings="$(ruby "$repo_root/tests/lib/scan-publish-provenance.rb" "$1")" || return 2
  [[ -n "$findings" ]]
}

# Break the property, not the form. Each fixture is a release pipeline that would
# have gone out green: the SIGNING is what differs, never the shape. The comment
# fixture is the load-bearing one — it carries the literal text `attestations: true`
# in a YAML comment above a step that never sets it, which is exactly the file a
# grep-based check blesses and a parse rejects.
validate_publish_provenance_cases() {
  local dir
  dir="$(mktemp -d)"
  trap 'rm -rf "${dir:-}"' RETURN

  cat > "$dir/pypi-off.yml" <<'YAML'
jobs:
  publish:
    permissions: {id-token: write, contents: read}
    steps:
      - uses: pypa/gh-action-pypi-publish@ba38be9e461d3875417946c167d0b5f3d385a247 # v1.14.1
        with: {packages-dir: dist, attestations: false}
YAML
  cat > "$dir/pypi-absent.yml" <<'YAML'
jobs:
  publish:
    permissions: {id-token: write, contents: read}
    steps:
      - uses: pypa/gh-action-pypi-publish@ba38be9e461d3875417946c167d0b5f3d385a247 # v1.14.1
        with: {packages-dir: dist}
YAML
  cat > "$dir/pypi-comment-only.yml" <<'YAML'
jobs:
  publish:
    permissions: {id-token: write, contents: read}
    steps:
      # attestations: true — string present in a comment, setting absent from the step.
      - uses: pypa/gh-action-pypi-publish@ba38be9e461d3875417946c167d0b5f3d385a247 # v1.14.1
        with: {packages-dir: dist}
YAML
  cat > "$dir/no-oidc.yml" <<'YAML'
jobs:
  publish:
    permissions: {contents: read}
    steps:
      - uses: pypa/gh-action-pypi-publish@ba38be9e461d3875417946c167d0b5f3d385a247 # v1.14.1
        with: {packages-dir: dist, attestations: true}
YAML
  cat > "$dir/npm-inline-off.yml" <<'YAML'
jobs:
  publish:
    permissions: {id-token: write, contents: read}
    steps:
      - run: npm publish --access public
YAML
  # `\b` does not split `pnpm`, so `/\bnpm\s+publish\b/` never saw the package manager
  # this portfolio actually uses. Same hole for yarn.
  cat > "$dir/pnpm-inline-off.yml" <<'YAML'
jobs:
  publish:
    permissions: {id-token: write, contents: read}
    steps:
      - run: pnpm publish --access public --no-git-checks
YAML
  cat > "$dir/yarn-inline-off.yml" <<'YAML'
jobs:
  publish:
    permissions: {id-token: write, contents: read}
    steps:
      - run: yarn publish --access public
YAML
  cat > "$dir/pnpm-inline-on.yml" <<'YAML'
jobs:
  publish:
    permissions: {id-token: write, contents: read}
    steps:
      - run: pnpm publish --access public --provenance --no-git-checks
YAML
  # A composite action's steps live under runs.steps; the scanner only walked jobs.*,
  # so every .github/actions/*/action.yml was exempt from the provenance contract.
  cat > "$dir/composite-off.yml" <<'YAML'
runs:
  using: composite
  steps:
    - shell: bash
      run: pnpm publish --access public
YAML
  cat > "$dir/composite-on.yml" <<'YAML'
runs:
  using: composite
  steps:
    - shell: bash
      run: pnpm publish --access public --provenance
YAML
  # The caller check only knew about ts-publish.yml; python-publish.yml callers could
  # turn attestations off and ship an unsigned PyPI release, green.
  cat > "$dir/pypi-caller-off.yml" <<'YAML'
jobs:
  publish:
    permissions: {id-token: write, contents: read}
    uses: hseshadr/ci/.github/workflows/python-publish.yml@bc68fde66f0805971e1b9aa444933b7975da80b1
    with: {attestations: false}
YAML
  cat > "$dir/pypi-caller-on.yml" <<'YAML'
jobs:
  publish:
    permissions: {id-token: write, contents: read}
    uses: hseshadr/ci/.github/workflows/python-publish.yml@bc68fde66f0805971e1b9aa444933b7975da80b1
    with: {attestations: true}
YAML
  cat > "$dir/npm-caller-off.yml" <<'YAML'
jobs:
  publish:
    permissions: {id-token: write, contents: read}
    uses: hseshadr/ci/.github/workflows/ts-publish.yml@bc68fde66f0805971e1b9aa444933b7975da80b1
    with: {provenance: false}
YAML
  cat > "$dir/reusable-default-off.yml" <<'YAML'
on:
  workflow_call:
    inputs:
      provenance: {type: boolean, default: false}
jobs: {}
YAML
  cat > "$dir/pypi-on.yml" <<'YAML'
jobs:
  publish:
    permissions: {id-token: write, contents: read}
    steps:
      - uses: pypa/gh-action-pypi-publish@ba38be9e461d3875417946c167d0b5f3d385a247 # v1.14.1
        with: {packages-dir: dist, attestations: true}
YAML
  cat > "$dir/npm-caller-on.yml" <<'YAML'
jobs:
  publish:
    permissions: {id-token: write, contents: read}
    uses: hseshadr/ci/.github/workflows/ts-publish.yml@bc68fde66f0805971e1b9aa444933b7975da80b1
    with: {provenance: true}
YAML
  cat > "$dir/npm-caller-inherits.yml" <<'YAML'
jobs:
  publish:
    permissions: {id-token: write, contents: read}
    uses: hseshadr/ci/.github/workflows/ts-publish.yml@bc68fde66f0805971e1b9aa444933b7975da80b1
    with: {working-directory: "."}
YAML
  cat > "$dir/reusable-forwards-input.yml" <<'YAML'
on:
  workflow_call:
    inputs:
      attestations: {type: boolean, default: true}
jobs:
  publish:
    permissions: {id-token: write, contents: read}
    steps:
      - uses: pypa/gh-action-pypi-publish@ba38be9e461d3875417946c167d0b5f3d385a247 # v1.14.1
        with:
          attestations: ${{ inputs.attestations }}
YAML
  cat > "$dir/not-a-publisher.yml" <<'YAML'
permissions: {contents: read}
jobs:
  gate:
    steps:
      - run: pnpm gate
YAML

  expect_success "provenance guard passes a PyPI upload with attestations: false" \
    scanner_reports_provenance_finding "$dir/pypi-off.yml"
  expect_success "provenance guard passes a PyPI upload with no attestations key" \
    scanner_reports_provenance_finding "$dir/pypi-absent.yml"
  expect_success "provenance guard passes a PyPI upload whose attestations live only in a COMMENT" \
    scanner_reports_provenance_finding "$dir/pypi-comment-only.yml"
  expect_success "provenance guard passes a publishing job with no id-token: write" \
    scanner_reports_provenance_finding "$dir/no-oidc.yml"
  expect_success "provenance guard passes an inline npm publish without --provenance" \
    scanner_reports_provenance_finding "$dir/npm-inline-off.yml"
  expect_success "provenance guard passes an inline PNPM publish without --provenance" \
    scanner_reports_provenance_finding "$dir/pnpm-inline-off.yml"
  expect_success "provenance guard passes an inline YARN publish without --provenance" \
    scanner_reports_provenance_finding "$dir/yarn-inline-off.yml"
  expect_success "provenance guard passes a COMPOSITE action that publishes unsigned" \
    scanner_reports_provenance_finding "$dir/composite-off.yml"
  expect_success "provenance guard passes a python-publish caller that sets attestations: false" \
    scanner_reports_provenance_finding "$dir/pypi-caller-off.yml"
  expect_success "provenance guard passes a caller that sets provenance: false" \
    scanner_reports_provenance_finding "$dir/npm-caller-off.yml"
  expect_success "provenance guard passes a reusable workflow whose provenance input defaults off" \
    scanner_reports_provenance_finding "$dir/reusable-default-off.yml"

  expect_failure "provenance guard flags a correct PyPI upload" \
    scanner_reports_provenance_finding "$dir/pypi-on.yml"
  expect_failure "provenance guard flags a caller with provenance: true" \
    scanner_reports_provenance_finding "$dir/npm-caller-on.yml"
  expect_failure "provenance guard flags an inline pnpm publish --provenance" \
    scanner_reports_provenance_finding "$dir/pnpm-inline-on.yml"
  expect_failure "provenance guard flags a composite that publishes with --provenance" \
    scanner_reports_provenance_finding "$dir/composite-on.yml"
  expect_failure "provenance guard flags a python-publish caller with attestations: true" \
    scanner_reports_provenance_finding "$dir/pypi-caller-on.yml"
  expect_failure "provenance guard flags a caller that inherits the on-by-default provenance" \
    scanner_reports_provenance_finding "$dir/npm-caller-inherits.yml"
  expect_failure "provenance guard flags a reusable workflow forwarding an on-by-default input" \
    scanner_reports_provenance_finding "$dir/reusable-forwards-input.yml"
  expect_failure "provenance guard flags a workflow that publishes nothing" \
    scanner_reports_provenance_finding "$dir/not-a-publisher.yml"
}

validate_trusted_command_contracts() {
  local phrase="repository-controlled literal command"

  grep -q "$phrase" .github/actions/restore-model-cache/action.yml ||
    fail "restore-model-cache does not document the fetch-command trust boundary"
  grep -q "$phrase" .github/workflows/cloudflare-pages-deploy.yml ||
    fail "cloudflare-pages-deploy does not document its command trust boundary"
  grep -q "$phrase" .github/workflows/frontend-gate.yml ||
    fail "frontend-gate does not document its gate-command trust boundary"
}

# --- property harness for workflow-embedded argument validation --------------
# The old checks grepped the workflows for their error STRINGS ("Invalid poe
# gate task", ...), which a comment could satisfy while the validation itself
# was deleted — a shape check. These helpers extract the actual `run:` script
# from the YAML and execute it against good and bad inputs, with the
# downstream tools stubbed to exit 0, so only the validation decides the
# verdict.

# Print the `run:` script of the first step whose env block declares $2.
extract_run_script_by_env() {
  ruby -r yaml -e '
    target = nil
    walk = lambda do |value|
      if value.is_a?(Hash)
        env = value["env"]
        if target.nil? && value["run"].is_a?(String) && env.is_a?(Hash) && env.key?(ARGV.fetch(1))
          target = value["run"]
        end
        value.each_value { |child| walk.call(child) }
      elsif value.is_a?(Array)
        value.each { |child| walk.call(child) }
      end
    end
    walk.call(YAML.safe_load(File.read(ARGV.fetch(0)), aliases: true))
    abort "no run: step declares env #{ARGV.fetch(1)} in #{ARGV.fetch(0)}" if target.nil?
    print target
  ' "$1" "$2"
}

# Execute that script the way the runner would (bash -e -u -o pipefail), in a
# scratch dir, with $2=$3 in the environment and real tools shadowed by the
# exit-0 stubs in $argument_stub_bin (set by validate_argument_guards).
run_guarded_step() {
  local file="$1" env_var="$2" value="$3" script workdir status=0
  script="$(extract_run_script_by_env "$file" "$env_var")" || return 2
  workdir="$(mktemp -d)"
  (
    cd "$workdir" &&
      env "$env_var=$value" PATH="$argument_stub_bin:$PATH" \
        bash -e -u -o pipefail -c "$script"
  ) >/dev/null 2>&1 || status=$?
  rm -rf "$workdir"
  return "$status"
}

validate_argument_guards() {
  local playwright=".github/actions/setup-playwright/run-playwright.sh"
  local pnpm=".github/actions/setup-pnpm/run-install.sh"
  local uv=".github/actions/setup-python-uv/run-uv.sh"
  local gate_wf=".github/workflows/python-gate.yml"
  local audit_wf=".github/workflows/security-audit.yml"

  local argument_stub_bin tool
  argument_stub_bin="$(mktemp -d)"
  trap 'rm -rf "${argument_stub_bin:-}"' RETURN
  for tool in uv uvx pnpm; do
    printf '#!/usr/bin/env bash\nexit 0\n' > "$argument_stub_bin/$tool"
    chmod +x "$argument_stub_bin/$tool"
  done

  expect_success "Playwright browser allowlist rejects valid browsers" \
    "$playwright" --validate "chromium firefox webkit"
  expect_failure "Playwright browser allowlist accepts shell syntax" \
    "$playwright" --validate "chromium; touch /tmp/injected"
  expect_success "pnpm install allowlist rejects supported arguments" \
    "$pnpm" --validate "--frozen-lockfile --config.dangerously-allow-all-builds=true"
  expect_failure "pnpm install allowlist accepts an unsupported argument" \
    "$pnpm" --validate "--dir /tmp"
  expect_success "Python version guard rejects a valid patch version" \
    "$uv" --validate-version "3.13.2"
  expect_failure "Python version guard accepts shell syntax" \
    "$uv" --validate-version "3.13; touch /tmp/injected"
  expect_success "uv sync allowlist rejects supported arguments" \
    "$uv" --validate-sync "--locked --extra dev"
  expect_failure "uv sync allowlist accepts an unsupported argument" \
    "$uv" --validate-sync "--directory /tmp"

  # Locked-by-default is a policy, not a suggestion: an empty or lock-flag-free
  # argument list silently runs an UNLOCKED `uv sync` / non-frozen
  # `pnpm install`, letting CI resolve dependencies the lockfile never pinned.
  # Opting out must be explicit (a named sentinel), never the quiet default.
  expect_failure "uv sync allowlist accepts an EMPTY argument list (unlocked sync)" \
    "$uv" --validate-sync ""
  expect_failure "uv sync allowlist accepts a lock-flag-free argument list" \
    "$uv" --validate-sync "--all-extras"
  expect_success "uv sync allowlist rejects the explicit --allow-unlocked opt-out" \
    "$uv" --validate-sync "--allow-unlocked --all-extras"
  expect_failure "pnpm install allowlist accepts an EMPTY argument list (non-frozen install)" \
    "$pnpm" --validate ""
  expect_failure "pnpm install allowlist accepts a frozen-lockfile-free argument list" \
    "$pnpm" --validate "--config.dangerously-allow-all-builds=true"
  expect_success "pnpm install allowlist rejects the explicit --allow-unfrozen-lockfile opt-out" \
    "$pnpm" --validate "--allow-unfrozen-lockfile"

  # Property, not shape: execute the real workflow scripts against good and
  # bad inputs (tools stubbed). Each file+variable pair carries BOTH polarities
  # on purpose — if extraction ever breaks (step renamed, env var dropped), the
  # expect_success case goes red rather than the expect_failure case passing
  # vacuously.
  expect_success "python-gate rejects a well-formed poe gate task" \
    run_guarded_step "$gate_wf" POE_GATE_TASK "gate"
  expect_failure "python-gate accepts a shell-metacharacter poe gate task" \
    run_guarded_step "$gate_wf" POE_GATE_TASK "gate; touch /tmp/injected"
  expect_success "security-audit rejects the documented pip-audit export args" \
    run_guarded_step "$audit_wf" PIP_AUDIT_EXPORT_ARGS "--frozen --all-extras --no-emit-project --no-hashes"
  expect_failure "security-audit accepts an unsupported pip-audit export argument" \
    run_guarded_step "$audit_wf" PIP_AUDIT_EXPORT_ARGS "--frozen --index-url https://evil.example"
  expect_success "security-audit rejects a valid pnpm audit level" \
    run_guarded_step "$audit_wf" PNPM_AUDIT_LEVEL "high"
  expect_failure "security-audit accepts a shell-metacharacter pnpm audit level" \
    run_guarded_step "$audit_wf" PNPM_AUDIT_LEVEL "low; touch /tmp/injected"

  # Non-vacuity self-check: a step carrying the error string only in a COMMENT,
  # with the validation deleted, is exactly the file the old grep-based check
  # blessed. The harness must let the bad input sail through it (exit 0 via the
  # stub) — proving these cases measure the validation, not the string.
  local gutted="$argument_stub_bin/gutted.yml"
  cat > "$gutted" <<'YAML'
jobs:
  gate:
    steps:
      - name: Gate
        env:
          POE_GATE_TASK: placeholder
        run: |
          # Invalid poe gate task — string present, validation absent.
          uv run poe "$POE_GATE_TASK"
YAML
  expect_success "harness self-check: a validation-free step must pass bad input through to the stub" \
    run_guarded_step "$gutted" POE_GATE_TASK "gate; touch /tmp/injected"
}

validate_self_ci() {
  local workflow=".github/workflows/ci.yml"

  [[ -f "$workflow" ]] || {
    fail "$workflow is missing"
    return
  }
  grep -q 'tests/security-policy\.sh' "$workflow" ||
    fail "$workflow does not run the security-policy regression test"
  grep -q 'shellcheck -x .github/actions/\*/\*.sh tests/\*.sh tests/lib/\*.sh' "$workflow" ||
    fail "$workflow does not run ShellCheck (-x, following sourced files) over every shell script"
  # The lineage guard's exemption is only safe while it stays narrow, and the cases
  # proving that live in a suite this repo's own history cannot stand in for.
  grep -q 'tests/lineage-guard-cases\.sh' "$workflow" ||
    fail "$workflow does not run the lineage guard's exemption-scope cases"
  grep -q 'uvx "zizmor@1\.26\.1" \.' "$workflow" ||
    fail "$workflow does not run the pinned full zizmor audit"

  # The README advertised an actionlint-clean tree while no job ran actionlint
  # anywhere. Assert the tool is actually wired in, so the claim cannot drift
  # back into decoration.
  grep -q 'actionlint' "$workflow" ||
    fail "$workflow does not run actionlint, which the README claims is clean"
  grep -q 'tests/lint-examples.sh' "$workflow" ||
    fail "$workflow does not lint/audit examples/, which zizmor cannot reach on its own"

  # This repo publishes secret-scan.yml and, until now, was the only repo in the
  # portfolio not running it. A control you sell and do not apply is a claim, not a
  # control.
  grep -q 'uses: \./\.github/workflows/secret-scan\.yml' "$workflow" ||
    fail "$workflow does not run this repo's own secret-scan.yml over this repo"

  # zizmor's ONLINE audits check a MOVING advisory database. A push-only gate proves
  # the tree was clean the last time someone pushed, which is not the same fact.
  grep -qE '^[[:space:]]+- cron:' "$workflow" ||
    fail "$workflow has no scheduled run — its online audits are never re-run between pushes"

  # security-audit.yml audits DEPENDENCY manifests. This repo has none, so calling it
  # here would be a permanently, vacuously green job — the exact failure mode the rest
  # of this suite exists to prevent. The moment that stops being true, it must be wired
  # in; that condition is checked rather than left to memory.
  if [[ -f pyproject.toml || -f package.json ]]; then
    grep -q 'security-audit\.yml' "$workflow" ||
      fail "$workflow gained a dependency manifest but does not run this repo's own security-audit.yml"
  fi
}

# A decoded private key must not be able to outlive the job that decoded it.
#
# examples/aml-filter/deploy.yml decoded an Ed25519 PRODUCTION signing seed and
# shredded it on the LAST LINE of the same `run:` block — after a verification
# step documented to abort on failure. Fail-closed verification exiting non-zero
# is the design working, and it is exactly the path that skipped the shred,
# leaving the seed on the runner. Two properties, both machine-checked here:
#
#   1. A scrub step must carry `if: always()`. Without it the scrub inherits the
#      job's success condition and does not run on the failure that matters.
#   2. A single `run:` block must not both WRITE a key file and SCRUB it. Even
#      with `set -e` off, any non-zero command between the two skips the scrub —
#      that is the same bug wearing a different hat, and property 1 cannot see it
#      because the step may legitimately have no `if:` at all.
key_scrub_is_unconditional() {
  # The Ruby program owns its own patterns; single quotes keep the shell out of them.
  # shellcheck disable=SC2016
  ruby -e '
    require "yaml"

    def walk(value, &block)
      yield value if value.is_a?(Hash)
      children = value.is_a?(Hash) ? value.values : value
      children.each { |child| walk(child, &block) } if children.is_a?(Array)
    end

    document = YAML.safe_load(File.read(ARGV.fetch(0)), aliases: true)
    exit(0) unless document.is_a?(Hash)

    write = /base64[^\n]*>[^\n]*\.key|>\s*"?[^\n"]*signing\.key/
    scrub = /\bshred\b|\brm\b[^\n]*\.key/
    violations = []

    walk(document) do |node|
      script = node["run"]
      next unless script.is_a?(String)

      writes = script.match?(write)
      scrubs = script.match?(scrub)

      # Property 2: one block that both creates and destroys the key.
      violations << "a single run: block both writes and scrubs a key file" if writes && scrubs

      # Property 1: a scrub-only step must be unconditional.
      if scrubs && !writes && node["if"].to_s.delete(" ") !~ /always\(\)/
        violations << "a key-scrub step is missing `if: always()`"
      end
    end

    violations.each { |violation| warn violation }
    exit(violations.empty? ? 0 : 1)
  ' "$1"
}

validate_key_scrub_cannot_be_skipped() {
  local count=0 file
  while IFS= read -r file; do
    count=$((count + 1))
    key_scrub_is_unconditional "$file" ||
      fail "$file can leave a decoded private key on the runner (see the message above)"
  done < <(yaml_sources)
  require_inputs "key-scrub check" "$count" "$YAML_SOURCE_FLOOR"
}

# The fixtures below embed literal GitHub expression markers and $VAR text as
# DATA for the YAML under test — never as shell to expand.
# shellcheck disable=SC2016
validate_key_scrub_cases() {
  local dir
  dir="$(mktemp -d)"
  trap 'rm -rf "${dir:-}"' RETURN

  # The exact shape that shipped: decode + verify + shred in one block.
  printf 'jobs:\n  a:\n    steps:\n      - run: |\n          printf %%s "$K" | base64 -d > /tmp/signing.key\n          pnpm run verify:bundle\n          shred -u /tmp/signing.key\n' \
    > "$dir/one-block.yml"
  # Split, but the scrub inherits job success — skipped on the failure that matters.
  printf 'jobs:\n  a:\n    steps:\n      - run: printf %%s "$K" | base64 -d > /tmp/signing.key\n      - run: shred -u /tmp/signing.key\n' \
    > "$dir/conditional-scrub.yml"
  # The fixed shape.
  printf 'jobs:\n  a:\n    steps:\n      - run: printf %%s "$K" | base64 -d > /tmp/signing.key\n      - if: always()\n        run: shred -u /tmp/signing.key\n' \
    > "$dir/always-scrub.yml"
  printf 'jobs:\n  a:\n    steps:\n      - run: echo "no keys here"\n' > "$dir/no-key.yml"

  expect_failure "key-scrub guard passes decode+verify+shred in ONE run block" \
    key_scrub_is_unconditional "$dir/one-block.yml"
  expect_failure "key-scrub guard passes a scrub step with no \`if: always()\`" \
    key_scrub_is_unconditional "$dir/conditional-scrub.yml"
  expect_success "key-scrub guard rejects a correctly-split \`if: always()\` scrub" \
    key_scrub_is_unconditional "$dir/always-scrub.yml"
  expect_success "key-scrub guard rejects a workflow that handles no key" \
    key_scrub_is_unconditional "$dir/no-key.yml"
}

# A reusable workflow whose every job is gated on a CALLER INPUT can report
# SUCCESS having executed nothing. security-audit.yml was exactly that shape:
# `run-python-audit` and `run-pnpm-audit` both default to false, so a caller that
# named the workflow and passed neither got two skipped jobs and a green check
# from a security audit that audited nothing. A skip must never be
# indistinguishable from a pass.
#
# Gating on github.event.* / github.event_name is a DIFFERENT thing and is
# deliberately not flagged: cloudflare-pages-deploy.yml's fork-PR guard skips a
# job precisely because it must not run, which is the control working. The
# distinction is caller-configuration vs trust boundary.
workflow_has_unconditional_job() {
  ruby -e '
    require "yaml"
    document = YAML.safe_load(File.read(ARGV.fetch(0)), aliases: true)
    exit(0) unless document.is_a?(Hash)
    trigger = document["on"] || document[true]
    # Only reusable workflows are in scope; an ordinary workflow has no caller.
    exit(0) unless trigger.is_a?(Hash) && trigger.key?("workflow_call")
    jobs = document["jobs"]
    exit(0) unless jobs.is_a?(Hash) && !jobs.empty?
    input_gated = jobs.values.all? do |job|
      condition = job.is_a?(Hash) ? job["if"].to_s : ""
      !condition.empty? && condition.include?("inputs.")
    end
    exit(input_gated ? 1 : 0)
  ' "$1"
}

validate_no_vacuous_success() {
  local count=0 file
  while IFS= read -r file; do
    count=$((count + 1))
    workflow_has_unconditional_job "$file" ||
      fail "$file gates EVERY job on a caller input — a caller that enables nothing gets a green check from a workflow that did nothing"
  done < <(find .github/workflows -type f \( -name '*.yml' -o -name '*.yaml' \) | sort)
  require_inputs "vacuous-success check" "$count" 5
}

# The fixtures below embed literal GitHub expression markers and $VAR text as
# DATA for the YAML under test — never as shell to expand.
# shellcheck disable=SC2016
validate_no_vacuous_success_cases() {
  local dir
  dir="$(mktemp -d)"
  trap 'rm -rf "${dir:-}"' RETURN

  printf 'on: {workflow_call: {inputs: {a: {type: boolean}}}}\njobs:\n  x:\n    if: ${{ inputs.a }}\n    runs-on: ubuntu-latest\n  y:\n    if: ${{ inputs.b }}\n    runs-on: ubuntu-latest\n' \
    > "$dir/all-gated.yml"
  printf 'on: {workflow_call: {inputs: {a: {type: boolean}}}}\njobs:\n  guard:\n    runs-on: ubuntu-latest\n  x:\n    if: ${{ inputs.a }}\n    runs-on: ubuntu-latest\n' \
    > "$dir/has-guard.yml"
  printf 'on: {workflow_call: null}\njobs:\n  x:\n    if: ${{ github.event_name == "push" }}\n    runs-on: ubuntu-latest\n' \
    > "$dir/trust-gated.yml"
  printf 'on: {push: null}\njobs:\n  x:\n    if: ${{ inputs.a }}\n    runs-on: ubuntu-latest\n' \
    > "$dir/not-reusable.yml"

  expect_failure "vacuous-success guard passes a workflow whose every job is input-gated" \
    workflow_has_unconditional_job "$dir/all-gated.yml"
  expect_success "vacuous-success guard flags a workflow that keeps one unconditional job" \
    workflow_has_unconditional_job "$dir/has-guard.yml"
  expect_success "vacuous-success guard wrongly flags a TRUST-boundary skip (fork-PR gate)" \
    workflow_has_unconditional_job "$dir/trust-gated.yml"
  expect_success "vacuous-success guard wrongly flags a non-reusable workflow" \
    workflow_has_unconditional_job "$dir/not-reusable.yml"
}

validate_pages_headers() {
  local script=".github/actions/pages-deploy-dist/apply-security-headers.sh"
  local action=".github/actions/pages-deploy-dist/action.yml"
  local temp_dir

  [[ -x "$script" ]] || {
    fail "$script is missing or not executable"
    return
  }
  grep -q 'apply-security-headers\.sh' "$action" ||
    fail "$action does not apply the security-headers baseline"
  # The Ruby program intentionally searches for the literal GitHub expression marker.
  # shellcheck disable=SC2016
  if ! ruby -e '
    require "yaml"
    action = YAML.safe_load(File.read(ARGV.fetch(0)), aliases: true)
    unsafe = action.fetch("runs").fetch("steps").map { |step| step["run"] }.compact
      .any? { |run| run.include?("${{ inputs.") }
    exit(unsafe ? 1 : 0)
  ' "$action"; then
    fail "$action interpolates inputs directly into shell code"
  fi

  # Guarded expansion: run_check invokes this via `||`, and the RETURN trap fires
  # again as that wrapper returns — by which point temp_dir is out of scope and a
  # bare "$temp_dir" would abort the suite on `set -u`.
  temp_dir="$(mktemp -d)"
  trap 'rm -rf "${temp_dir:-}"' RETURN
  "$script" "$temp_dir" || {
    fail "$script failed to generate a Pages headers baseline"
    return
  }

  for header in \
    'Content-Security-Policy:' \
    'Permissions-Policy:' \
    'Referrer-Policy:' \
    'Strict-Transport-Security:' \
    'X-Content-Type-Options:' \
    'X-Frame-Options:'; do
    grep -Eq "^[[:space:]]+$header" "$temp_dir/_headers" ||
      fail "generated Pages baseline is missing $header"
  done

  printf '/assets/*\n  Cache-Control: public, max-age=31536000\n' > "$temp_dir/_headers"
  before="$(shasum -a 256 "$temp_dir/_headers")"
  "$script" "$temp_dir"
  after="$(shasum -a 256 "$temp_dir/_headers")"
  [[ "$before" == "$after" ]] || fail "Pages baseline overwrites an app-owned _headers file"
}

run_check validate_input_floor_cases
run_check validate_yaml
run_check validate_action_pins
run_check validate_permissions
run_check validate_permission_guard_cases
run_check validate_shell_boundaries
run_check validate_interpolation_scanner_cases
run_check validate_checkout_credentials
run_check validate_workflow_run_repository_pin
run_check validate_workflow_run_pin_cases
run_check validate_dangerous_trigger_suppressions
run_check validate_dangerous_trigger_suppression_cases
run_check validate_dependabot_cooldown
run_check validate_first_party_pins
run_check validate_first_party_release_lineage
run_check validate_publish_provenance
run_check validate_publish_provenance_cases
run_check validate_trusted_command_contracts
run_check validate_argument_guards
run_check validate_self_ci
run_check validate_no_vacuous_success
run_check validate_no_vacuous_success_cases
run_check validate_key_scrub_cannot_be_skipped
run_check validate_key_scrub_cases
run_check validate_pages_headers

if ((failures > 0)); then
  printf '\n%d security policy check(s) failed.\n' "$failures" >&2
  exit 1
fi

printf 'Security policy checks passed.\n'
