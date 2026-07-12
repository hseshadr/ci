#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

failures=0

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  failures=$((failures + 1))
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

    case "$ref" in
      hseshadr/ci/* | ./* | docker://*) continue ;;
    esac

    if [[ ! "$ref" =~ @[0-9a-f]{40}$ ]]; then
      fail "$file:$line_number third-party action is not pinned to a full commit SHA: $ref"
    fi
    if [[ ! "$line" =~ \#[[:space:]]+v[0-9] ]]; then
      fail "$file:$line_number pinned action lacks a Dependabot version comment"
    fi
  done < <(rg -n --no-heading '^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]+' .github examples -g '*.yml' -g '*.yaml')
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

validate_self_ci() {
  local workflow=".github/workflows/ci.yml"

  [[ -f "$workflow" ]] || {
    fail "$workflow is missing"
    return
  }
  rg -q 'tests/security-policy\.sh' "$workflow" ||
    fail "$workflow does not run the security-policy regression test"
}

validate_pages_headers() {
  local script=".github/actions/pages-deploy-dist/apply-security-headers.sh"
  local action=".github/actions/pages-deploy-dist/action.yml"
  local temp_dir

  [[ -x "$script" ]] || {
    fail "$script is missing or not executable"
    return
  }
  rg -q 'apply-security-headers\.sh' "$action" ||
    fail "$action does not apply the security-headers baseline"
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
    rg -q "^[[:space:]]+$header" "$temp_dir/_headers" ||
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
validate_self_ci
validate_pages_headers

if ((failures > 0)); then
  printf '\n%d security policy check(s) failed.\n' "$failures" >&2
  exit 1
fi

printf 'Security policy checks passed.\n'
