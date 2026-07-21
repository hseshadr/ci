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

validate_yaml() {
  while IFS= read -r file; do
    ruby -e 'require "yaml"; YAML.safe_load(File.read(ARGV.fetch(0)), aliases: true)' "$file" ||
      fail "$file is not valid YAML"
  done < <(find .github examples -type f \( -name '*.yml' -o -name '*.yaml' \) | sort)
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
    done < <(find .github examples -type f \( -name '*.yml' -o -name '*.yaml' \) | sort)
  )
}

validate_permissions() {
  while IFS= read -r file; do
    if awk '
      /^permissions:/ { found = 1; in_permissions = 1; next }
      in_permissions && /^[^[:space:]]/ { in_permissions = 0 }
      in_permissions && /:[[:space:]]*write([[:space:]]*#.*)?$/ { write_permission = 1 }
      END { exit(found ? (write_permission ? 2 : 0) : 1) }
    ' "$file"; then
      continue
    else
      case "$?" in
        1) fail "$file does not declare top-level permissions" ;;
        2) fail "$file grants a top-level write permission" ;;
      esac
    fi
  done < <(find .github/workflows examples -type f \( -name '*.yml' -o -name '*.yaml' \) | sort)
}

validate_shell_boundaries() {
  local unsafe_files
  local yaml_files=()

  while IFS= read -r file; do
    yaml_files+=("$file")
  done < <(find .github examples -type f \( -name '*.yml' -o -name '*.yaml' \) | sort)

  # The Ruby program intentionally searches for the literal GitHub expression marker.
  # shellcheck disable=SC2016
  unsafe_files="$(ruby -e '
    require "yaml"

    def walk(value, &block)
      yield value if value.is_a?(Hash)
      children = value.is_a?(Hash) ? value.values : value
      children.each { |child| walk(child, &block) } if children.is_a?(Array)
    end

    ARGV.each do |file|
      document = YAML.safe_load(File.read(file), aliases: true)
      unsafe = false
      walk(document) do |node|
        unsafe ||= node["run"].is_a?(String) && node["run"].include?("${{ inputs.")
      end
      puts file if unsafe
    end
  ' "${yaml_files[@]}")"

  while IFS= read -r file; do
    [[ -z "$file" ]] || fail "$file interpolates workflow inputs directly into shell code"
  done <<< "$unsafe_files"
}

validate_checkout_credentials() {
  local unsafe_files
  local yaml_files=()

  while IFS= read -r file; do
    yaml_files+=("$file")
  done < <(find .github examples -type f \( -name '*.yml' -o -name '*.yaml' \) | sort)

  unsafe_files="$(ruby -e '
    require "yaml"

    def walk(value, &block)
      yield value if value.is_a?(Hash)
      children = value.is_a?(Hash) ? value.values : value
      children.each { |child| walk(child, &block) } if children.is_a?(Array)
    end

    ARGV.each do |file|
      document = YAML.safe_load(File.read(file), aliases: true)
      unsafe = false
      walk(document) do |node|
        next unless node["uses"].to_s.start_with?("actions/checkout@")
        unsafe ||= !node.fetch("with", {}).fetch("persist-credentials", nil).equal?(false)
      end
      puts file if unsafe
    end
  ' "${yaml_files[@]}")"

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
  done < <(grep -RInE --include='*.yml' \
    '^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]+hseshadr/ci/[^[:space:]]*@(ci-)?v[0-9]' \
    .github examples)
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

validate_argument_guards() {
  local playwright=".github/actions/setup-playwright/run-playwright.sh"
  local pnpm=".github/actions/setup-pnpm/run-install.sh"
  local uv=".github/actions/setup-python-uv/run-uv.sh"

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

  grep -q 'Invalid poe gate task' .github/workflows/python-gate.yml ||
    fail "python-gate does not validate gate-task"
  grep -q 'Invalid pip-audit export argument' .github/workflows/security-audit.yml ||
    fail "security-audit does not validate pip-audit export arguments"
  grep -q 'Invalid pnpm audit level' .github/workflows/security-audit.yml ||
    fail "security-audit does not validate pnpm-audit-level"
}

validate_self_ci() {
  local workflow=".github/workflows/ci.yml"

  [[ -f "$workflow" ]] || {
    fail "$workflow is missing"
    return
  }
  grep -q 'tests/security-policy\.sh' "$workflow" ||
    fail "$workflow does not run the security-policy regression test"
  grep -q 'shellcheck .github/actions/\*/\*.sh tests/security-policy.sh' "$workflow" ||
    fail "$workflow does not run ShellCheck over every shell script"
  grep -q 'uvx "zizmor@1\.26\.1" \.' "$workflow" ||
    fail "$workflow does not run the pinned full zizmor audit"
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

  temp_dir="$(mktemp -d)"
  trap 'rm -rf "$temp_dir"' RETURN
  "$script" "$temp_dir"

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

validate_yaml
validate_action_pins
validate_permissions
validate_shell_boundaries
validate_checkout_credentials
validate_dependabot_cooldown
validate_first_party_pins
validate_trusted_command_contracts
validate_argument_guards
validate_self_ci
validate_pages_headers

if ((failures > 0)); then
  printf '\n%d security policy check(s) failed.\n' "$failures" >&2
  exit 1
fi

printf 'Security policy checks passed.\n'
