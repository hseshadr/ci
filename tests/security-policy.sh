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

validate_yaml() {
  while IFS= read -r file; do
    ruby -e 'require "yaml"; YAML.safe_load(File.read(ARGV.fetch(0)), aliases: true)' "$file" ||
      fail "$file is not valid YAML"
  done < <(yaml_sources)
}

validate_action_pins() {
  while IFS=: read -r file line_number line; do
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
}

# Verdict on ONE file's top-level permissions:
#   0 = declared and read-only, 1 = not declared, 2 = grants a top-level write.
# Extracted from validate_permissions so tests can drive it against fixtures.
check_top_level_permissions() {
  awk '
    /^permissions:/ {
      found = 1
      # The value can ride on this very line: `permissions: write-all` or a
      # flow-style map like `permissions: {contents: write}`. The old code
      # jumped to the next line here, so both forms passed unexamined.
      rest = $0
      sub(/^permissions:[[:space:]]*/, "", rest)
      sub(/#.*/, "", rest)
      gsub(/[[:space:]]+$/, "", rest)
      if (rest != "") {
        if (rest ~ /write/) write_permission = 1
      } else {
        in_permissions = 1
      }
      next
    }
    in_permissions && /^[^[:space:]]/ { in_permissions = 0 }
    in_permissions && /:[[:space:]]*(write|write-all)([[:space:]]*#.*)?$/ { write_permission = 1 }
    END { exit(found ? (write_permission ? 2 : 0) : 1) }
  ' "$1"
}

validate_permissions() {
  local verdict
  while IFS= read -r file; do
    verdict=0
    check_top_level_permissions "$file" || verdict=$?
    case "$verdict" in
      1) fail "$file does not declare top-level permissions" ;;
      2) fail "$file grants a top-level write permission" ;;
    esac
  done < <(find .github/workflows examples -type f \( -name '*.yml' -o -name '*.yaml' \) | sort)
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
  while IFS=: read -r file line_number _; do
    fail "$file:$line_number first-party ref is not pinned to a full commit SHA"
  done < <(grep -RInE --include='*.yml' --include='*.yaml' \
    '^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]+hseshadr/ci/[^[:space:]]*@(ci-)?v[0-9]' \
    .github examples)
}

# validate_first_party_release_lineage lives in tests/lib/ so the scenario suite in
# tests/lineage-guard-cases.sh can drive the same code against synthetic repos —
# the release-commit bootstrap it has to tolerate is not reproducible in this repo
# on demand. The rationale and the exemption's exact scope are documented there.
# shellcheck source=tests/lib/first-party-lineage.sh
source "$repo_root/tests/lib/first-party-lineage.sh"

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

run_check validate_yaml
run_check validate_action_pins
run_check validate_permissions
run_check validate_permission_guard_cases
run_check validate_shell_boundaries
run_check validate_interpolation_scanner_cases
run_check validate_checkout_credentials
run_check validate_dependabot_cooldown
run_check validate_first_party_pins
run_check validate_first_party_release_lineage
run_check validate_trusted_command_contracts
run_check validate_argument_guards
run_check validate_self_ci
run_check validate_pages_headers

if ((failures > 0)); then
  printf '\n%d security policy check(s) failed.\n' "$failures" >&2
  exit 1
fi

printf 'Security policy checks passed.\n'
