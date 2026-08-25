#!/usr/bin/env bash
# Fail when a fleet repository adds execution outside its exact Dagger bootstrap.
set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
scanner="$repo_root/tests/lib/dagger-control-plane.rb"
local_root=""
remote_root=""
owner="hseshadr"
allowlist="$repo_root/tests/dagger-control-plane-allowlist.txt"
today=""
gh_bin="${GH_BIN:-gh}"
consumers=(ci almamesh aml-filter assay edge-proc edge-reco edgeproc-core privacy-core)

die() {
  printf 'dagger-control-plane: %s\n' "$*" >&2
  exit 2
}

require_value() {
  [[ -n "${2:-}" ]] || die "$1 requires a value"
}

parse_arguments() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --local) require_value "$1" "${2:-}"; local_root="$2"; shift 2 ;;
      --allowlist) require_value "$1" "${2:-}"; allowlist="$2"; shift 2 ;;
      --today) require_value "$1" "${2:-}"; today="$2"; shift 2 ;;
      --owner) require_value "$1" "${2:-}"; owner="$2"; shift 2 ;;
      --consumers) require_value "$1" "${2:-}"; read -r -a consumers <<< "$2"; shift 2 ;;
      *) die "unknown argument: $1" ;;
    esac
  done
}

scan_path() {
  local repo="$1" path="$2"
  local command=(ruby "$scanner" --repo "$repo" --path "$path" --allowlist "$allowlist")
  [[ -z "$today" ]] || command+=(--today "$today")
  "${command[@]}"
}

fetch_metadata() {
  local repo="$1" path="$2"
  local base="repos/$owner/$repo"
  # GraphQL variables are literal query syntax, not shell expansions.
  # shellcheck disable=SC2016
  local query='query($owner:String!,$name:String!){repository(owner:$owner,name:$name){branchProtectionRules(first:100){nodes{pattern requiresStatusChecks requiredStatusCheckContexts}}}}'
  mkdir -p "$path/.control-plane"
  "$gh_bin" api graphql -f query="$query" -F owner="$owner" -F name="$repo" \
    > "$path/.control-plane/branch-protection.json" ||
    die "cannot read branch protection for $owner/$repo"
  "$gh_bin" api "$base/code-scanning/default-setup" > "$path/.control-plane/codeql-default.json" ||
    die "cannot read CodeQL setup for $owner/$repo"
  fetch_check_apps "$repo" "$path" "$base"
}

fetch_check_apps() {
  local repo="$1" path="$2" base="$3" heads sha
  local apps="$path/.control-plane/check-apps.txt"
  "$gh_bin" api "$base/commits/main/check-runs?per_page=100" \
    --jq '.check_runs[].app.slug' > "$apps" || die "cannot read main check apps for $owner/$repo"
  heads="$("$gh_bin" api "$base/pulls?state=open&per_page=30" --jq '.[].head.sha')" ||
    die "cannot read open pull requests for $owner/$repo"
  while IFS= read -r sha; do
    [[ -z "$sha" ]] && continue
    [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "invalid pull request head for $owner/$repo"
    "$gh_bin" api "$base/commits/$sha/check-runs?per_page=100" \
      --jq '.check_runs[].app.slug' >> "$apps" || die "cannot read pull request check apps for $owner/$repo"
  done <<< "$heads"
}

fetch_remote() {
  local repo="$1"
  local path="$remote_root/$repo"
  "$gh_bin" repo clone "$owner/$repo" "$path" -- --depth=1 --branch main >/dev/null ||
    die "cannot clone $owner/$repo"
  fetch_metadata "$repo" "$path"
  printf '%s' "$path"
}

repository_path() {
  local repo="$1" path
  if [[ -n "$local_root" ]]; then
    path="$local_root/$repo"
    [[ -d "$path" ]] || die "repository is absent: $path"
    printf '%s' "$path"
    return
  fi
  fetch_remote "$repo"
}

main() {
  parse_arguments "$@"
  if [[ -z "$local_root" ]]; then
    remote_root="$(mktemp -d)"
    trap 'rm -rf "${remote_root:-}"' EXIT
  fi
  local result=0 status repo path
  for repo in "${consumers[@]}"; do
    status=0
    path="$(repository_path "$repo")" || status=$?
    [[ "$status" -ne 2 ]] || return 2
    scan_path "$repo" "$path" || status=$?
    [[ "$status" -eq 0 ]] || result=1
  done
  return "$result"
}

main "$@"
