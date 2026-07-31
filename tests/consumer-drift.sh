#!/usr/bin/env bash
# Consumer drift detector — fail when a consumer repository hand-rolls a control
# that hseshadr/ci already publishes.
#
# WHY THIS EXISTS
#   Five consumer repositories each carried their own Cloudflare Pages deploy
#   while this repository shipped a reusable one. Nothing compared the two, so
#   nobody noticed until one of those five drifted into a fork-PR deploy
#   vulnerability: `workflow_run` + `branches: [main]` runs in the BASE repo with
#   the BASE repo's secrets, and a fork's default branch is also called `main`.
#   The bug was in the copy, not in the shared workflow. A detector that walks
#   the consumers and names every hand-rolled control turns "we forgot to
#   converge that one" from an archaeology problem into a red build.
#
# WHAT IT DOES
#   For each consumer: list .github/workflows/*.yml|*.yaml over the GitHub API,
#   fetch each file, and classify it by BEHAVIOUR (see
#   tests/lib/classify-workflow.rb). A control is ADOPTED when the workflow
#   `uses:` the matching hseshadr/ci reusable workflow or composite, and DRIFT
#   otherwise.
#
# EXIT CODES
#   0  clean, or every drift finding is allowlisted
#   1  NEW drift — a hand-rolled control with no allowlist entry
#   2  the detector could not do its job (missing tool, malformed allowlist)
#
#   A stale allowlist entry (allowlisted, but the repository no longer drifts)
#   is reported as a warning and never fails the run: cleaning up the allowlist
#   must not be able to break someone else's build.
#
# USAGE
#   tests/consumer-drift.sh
#   tests/consumer-drift.sh --owner hseshadr --consumers "almamesh edge-reco"
#   tests/consumer-drift.sh --local <dir> --allowlist <file>
#
#   --local <dir> reads `<dir>/<repo>/<workflow>.yml` instead of calling the API.
#   It is how tests/consumer-drift-cases.sh exercises the exit-code contract
#   without a network or a token.
#
# AUTH
#   Uses `gh`, so GH_TOKEN or an interactive `gh auth login` applies. A
#   workflow's default GITHUB_TOKEN is scoped to its own repository and will
#   404 on every consumer — see .github/workflows/consumer-drift.yml.

set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

owner="${CONSUMER_DRIFT_OWNER:-hseshadr}"
allowlist_file="$repo_root/tests/consumer-drift-allowlist.txt"
local_root=""
consumers=(almamesh aml-filter edge-reco assay privacy-core edge-proc edgeproc-core)

usage() {
  sed -n '2,40p' "${BASH_SOURCE[0]}"
}

die() {
  printf 'consumer-drift: %s\n' "$*" >&2
  exit 2
}

require_argument() {
  [[ -n "${2:-}" ]] || die "$1 requires a value"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --allowlist)
      require_argument "$1" "${2:-}"
      allowlist_file="$2"
      shift 2
      ;;
    --local)
      require_argument "$1" "${2:-}"
      local_root="$2"
      shift 2
      ;;
    --owner)
      require_argument "$1" "${2:-}"
      owner="$2"
      shift 2
      ;;
    --consumers)
      require_argument "$1" "${2:-}"
      consumers=()
      for consumer in $2; do consumers+=("$consumer"); done
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

command -v ruby >/dev/null 2>&1 || die "ruby is required"
if [[ -z "$local_root" ]]; then
  command -v gh >/dev/null 2>&1 || die "gh is required (or pass --local <dir>)"
fi

# --- allowlist ---------------------------------------------------------------
# Format, one entry per line:
#
#     <repo>/<workflow-file>/<category>|<reason>
#
# `#` starts a comment, blank lines are ignored, and the reason is MANDATORY —
# an entry without one is a malformed allowlist, not a silent pass. The reason
# is the whole point: an allowlist without reasons is a mute suppression list
# that nobody can ever safely delete from.

allow_keys=()
allow_reasons=()
allow_hits=()

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  printf '%s' "${value%"${value##*[![:space:]]}"}"
}

load_allowlist() {
  local line key reason stripped
  [[ -f "$allowlist_file" ]] || die "allowlist not found: $allowlist_file"

  while IFS= read -r line || [[ -n "$line" ]]; do
    stripped="$(trim "$line")"
    [[ -n "$stripped" ]] || continue
    [[ "$stripped" != \#* ]] || continue
    [[ "$stripped" == *"|"* ]] ||
      die "allowlist entry has no '|<reason>': $stripped"

    key="$(trim "${stripped%%|*}")"
    reason="$(trim "${stripped#*|}")"
    [[ -n "$reason" ]] || die "allowlist entry has an empty reason: $key"
    [[ "$key" == */*/* ]] ||
      die "allowlist key is not <repo>/<workflow-file>/<category>: $key"

    allow_keys+=("$key")
    allow_reasons+=("$reason")
    allow_hits+=(0)
  done < "$allowlist_file"
}

# Echo the index of $1 in allow_keys, or nothing.
allowlist_index() {
  local index
  for ((index = 0; index < ${#allow_keys[@]}; index++)); do
    if [[ "${allow_keys[index]}" == "$1" ]]; then
      printf '%s' "$index"
      return 0
    fi
  done
  return 1
}

# --- collection --------------------------------------------------------------

work_dir=""
scanned_repos=()
skipped_notes=()

cleanup() {
  [[ -z "$work_dir" ]] || rm -rf "$work_dir"
}
trap cleanup EXIT

list_remote_workflows() {
  gh api "repos/$owner/$1/contents/.github/workflows" \
    --jq '.[] | select(.type == "file") | select(.name | test("\\.ya?ml$")) | .name'
}

fetch_remote_workflow() {
  gh api -H "Accept: application/vnd.github.raw" \
    "repos/$owner/$1/contents/.github/workflows/$2"
}

collect_local_repo() {
  local repo="$1" dest="$2"
  if [[ ! -d "$local_root/$repo" ]]; then
    skipped_notes+=("$repo: not found / no access (no fixture directory under $local_root)")
    return 1
  fi
  find "$local_root/$repo" -maxdepth 1 -type f \( -name '*.yml' -o -name '*.yaml' \) \
    -exec cp {} "$dest/" \;
  return 0
}

collect_remote_repo() {
  local repo="$1" dest="$2" names name
  if ! names="$(list_remote_workflows "$repo" 2>/dev/null)"; then
    skipped_notes+=("$repo: not found / no access (the API refused the workflow listing)")
    return 1
  fi
  if [[ -z "$names" ]]; then
    skipped_notes+=("$repo: no workflows under .github/workflows")
    return 1
  fi
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    if ! fetch_remote_workflow "$repo" "$name" > "$dest/$name" 2>/dev/null; then
      skipped_notes+=("$repo/$name: could not be fetched")
      rm -f "$dest/$name"
    fi
  done <<< "$names"
  return 0
}

collect_workflows() {
  local repo dest
  work_dir="$(mktemp -d)"
  for repo in "${consumers[@]}"; do
    dest="$work_dir/$repo"
    mkdir -p "$dest"
    if [[ -n "$local_root" ]]; then
      collect_local_repo "$repo" "$dest" || continue
    else
      collect_remote_repo "$repo" "$dest" || continue
    fi
    scanned_repos+=("$repo")
  done
}

# --- classification ----------------------------------------------------------

classify_collected() {
  local files=() file
  while IFS= read -r file; do
    files+=("$file")
  done < <(find "$work_dir" -type f \( -name '*.yml' -o -name '*.yaml' \) | sort)

  [[ ${#files[@]} -gt 0 ]] || return 0
  ruby "$repo_root/tests/lib/classify-workflow.rb" "${files[@]}"
}

row_repos=()
row_workflows=()
row_categories=()
row_verdicts=()
row_states=()
row_reasons=()

record_row() {
  local path="$1" category="$2" verdict="$3" evidence="$4"
  local repo workflow key index state reason

  workflow="${path##*/}"
  repo="${path%/*}"
  repo="${repo##*/}"
  key="$repo/$workflow/$category"
  state="$verdict"
  reason="$evidence"

  if [[ "$verdict" == "DRIFT" ]]; then
    if index="$(allowlist_index "$key")"; then
      allow_hits[index]=1
      state="DRIFT (allowlisted)"
      reason="${allow_reasons[index]} [$evidence]"
    else
      state="DRIFT (NEW)"
    fi
  fi

  row_repos+=("$repo")
  row_workflows+=("$workflow")
  row_categories+=("$category")
  row_verdicts+=("$verdict")
  row_states+=("$state")
  row_reasons+=("$reason")
}

collect_rows() {
  local path category verdict evidence
  while IFS=$'\t' read -r path category verdict evidence; do
    [[ -n "$path" ]] || continue
    record_row "$path" "$category" "$verdict" "$evidence"
  done < <(classify_collected)
}

# --- reporting ---------------------------------------------------------------

print_table() {
  local index
  printf '%-16s %-26s %-18s %-20s %s\n' REPO WORKFLOW CONTROL VERDICT REASON
  printf '%-16s %-26s %-18s %-20s %s\n' \
    '----------------' '--------------------------' '------------------' \
    '--------------------' '------'
  for ((index = 0; index < ${#row_repos[@]}; index++)); do
    printf '%-16s %-26s %-18s %-20s %s\n' \
      "${row_repos[index]}" "${row_workflows[index]}" "${row_categories[index]}" \
      "${row_states[index]}" "${row_reasons[index]}"
  done
}

print_skips() {
  local note
  [[ ${#skipped_notes[@]} -gt 0 ]] || return 0
  printf '\nSkipped (not a failure — nothing was inspected here):\n'
  for note in "${skipped_notes[@]}"; do
    printf '  - %s\n' "$note"
  done
}

repo_was_scanned() {
  local repo
  for repo in ${scanned_repos[@]+"${scanned_repos[@]}"}; do
    [[ "$repo" != "$1" ]] || return 0
  done
  return 1
}

# An allowlist entry whose drift is gone is drift in the other direction: the
# suppression outlived the thing it suppressed. Warn only — and only for repos
# this run actually inspected, since a skipped repo proves nothing either way.
print_stale_allowlist() {
  local index key repo stale=0
  for ((index = 0; index < ${#allow_keys[@]}; index++)); do
    [[ "${allow_hits[index]}" -eq 0 ]] || continue
    key="${allow_keys[index]}"
    repo="${key%%/*}"
    repo_was_scanned "$repo" || continue
    if [[ "$stale" -eq 0 ]]; then
      printf '\nStale allowlist entries (no longer drifting — delete them):\n'
      stale=1
    fi
    printf '  - %s | %s\n' "$key" "${allow_reasons[index]}"
  done
}

count_state() {
  local index total=0
  for ((index = 0; index < ${#row_states[@]}; index++)); do
    [[ "${row_states[index]}" != "$1" ]] || total=$((total + 1))
  done
  printf '%s' "$total"
}

count_drift_repos() {
  local index seen="" repo total=0
  for ((index = 0; index < ${#row_repos[@]}; index++)); do
    [[ "${row_verdicts[index]}" == "DRIFT" ]] || continue
    repo="${row_repos[index]}"
    case " $seen " in
      *" $repo "*) continue ;;
    esac
    seen="$seen $repo"
    total=$((total + 1))
  done
  printf '%s' "$total"
}

# A sweep that inspected NOTHING printed "0 hand-rolled control(s) across 0
# repo(s)" and exited 0 — a clean bill of health from a run that never looked.
# Every other guard in this repository already carries a vacuity floor
# (require_inputs in tests/security-policy.sh); this one did not, and it is the
# guard most exposed to it because its inputs live behind a network and a token.
#
# The calibration matters. A PARTIAL sweep still passes and warns: skipping one
# unreachable repo out of seven is honest, disclosed, and better than nothing. A
# TOTAL miss is exit 2 — "the detector could not do its job" — because that is
# indistinguishable from a clean portfolio and must never be reported as one.
assert_something_was_inspected() {
  local requested="${#consumers[@]}" scanned="${#scanned_repos[@]}"

  if [[ "$requested" -gt 0 && "$scanned" -eq 0 ]]; then
    printf '::error::consumer-drift inspected 0 of %d requested repositories, so this run proves nothing. It is NOT a clean sweep. Check the token (CONSUMER_DRIFT_TOKEN / GH_TOKEN) and the repository names.\n' \
      "$requested" >&2
    exit 2
  fi

  if [[ "$scanned" -lt "$requested" ]]; then
    printf '::warning::consumer-drift inspected %d of %d repositories; the rest are listed under "Skipped" above and were NOT checked.\n' \
      "$scanned" "$requested" >&2
  fi
}

main() {
  load_allowlist
  collect_workflows
  collect_rows

  print_table
  print_skips
  print_stale_allowlist

  # ORDER IS LOAD-BEARING: the floor is asserted AFTER the table prints, so a
  # run that inspected nothing still shows its skip notes before exiting.
  assert_something_was_inspected

  local allowlisted new drift repos
  allowlisted="$(count_state 'DRIFT (allowlisted)')"
  new="$(count_state 'DRIFT (NEW)')"
  drift=$((allowlisted + new))
  repos="$(count_drift_repos)"

  printf '\n%d hand-rolled control(s) across %d repo(s); %d allowlisted; %d new\n' \
    "$drift" "$repos" "$allowlisted" "$new"

  if [[ "$new" -gt 0 ]]; then
    printf '::error::%d consumer control(s) hand-roll something hseshadr/ci publishes. Converge them, or add an allowlist entry with a reason to %s.\n' \
      "$new" "$allowlist_file" >&2
    return 1
  fi
  return 0
}

main
