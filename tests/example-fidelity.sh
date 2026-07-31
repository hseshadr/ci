#!/usr/bin/env bash
# Example fidelity — does every example still resolve against the repository it
# is written for?
#
# WHY THIS EXISTS
#   tests/consumer-drift.sh proves a CONSUMER has diverged from what this
#   repository publishes. Nothing proved the MIRROR: that an example in examples/
#   still converges to the consumer it names. The two guards that did run over
#   examples/ — actionlint and zizmor, via tests/lint-examples.sh — check YAML
#   shape and workflow security. Neither resolves a repo-relative path inside
#   somebody else's repository, so both stayed green while
#   examples/edge-reco/ci.yml named `frontend/.node-version`, a file edge-reco has
#   never had. `actions/setup-node` hard-fails on a missing version file, so that
#   example was RED as drafted and the gate passed it. Every convergence PR in
#   this portfolio starts "copy the example"; an unchecked example is an
#   unchecked migration.
#
# EXIT CODES
#   0  every reference resolved, and enough of them resolved to be meaningful
#   1  at least one reference is MISSING — an example is broken as drafted
#   2  the checker could not do its job (no consumer clones, coverage floor
#      unmet, missing tool). Deliberately NOT 0: "could not verify" must never be
#      indistinguishable from "verified". That confusion is the exact defect this
#      guard exists to end.
#
# USAGE
#   tests/example-fidelity.sh                        # uses ~/dev/oss clones
#   tests/example-fidelity.sh --consumer-root <dir>  # point at your own clones
#   tests/example-fidelity.sh --clone                # shallow-clone consumers (CI)
#
# NO FALSE ALARMS
#   References resolve against a COMMITTED git ref (origin/main by preference),
#   never the working tree, so an uncommitted local edit cannot invent a failure.
#   A consumer with no clone is UNVERIFIABLE — reported loudly and counted as "not
#   checked" — never a pass and never a failure.

set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root" || exit 2

consumer_root="${EXAMPLE_CONSUMER_ROOT:-$HOME/dev/oss}"
clone_owner="${EXAMPLE_CONSUMER_OWNER:-hseshadr}"
# Overridable so tests/example-fidelity-cases.sh can drive this against synthetic
# examples. A runner that can only ever scan the real examples/ cannot be shown
# going red on demand, and an unfalsifiable gate is not evidence.
examples_root="${EXAMPLE_ROOT:-examples}"
do_clone=0
clone_dir=""

# The floors below are the vacuity guard. Every file-scanning check in this
# repository is green on an empty input set — a moved path or a renamed directory
# turns "no violations" into "nothing was looked at", and the two are
# indistinguishable from the outside. These numbers are well above one on purpose:
# a run that resolved 3 of 166 references is broken, and "at least one" would not
# notice. Raise them when examples/ grows; never lower them to make a run pass.
MIN_RESOLVED_REFERENCES="${MIN_RESOLVED_REFERENCES:-120}"
MIN_VERIFIED_CONSUMERS="${MIN_VERIFIED_CONSUMERS:-5}"

die() {
  printf 'example-fidelity: %s\n' "$*" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --consumer-root)
      [[ -n "${2:-}" ]] || die "--consumer-root requires a value"
      consumer_root="$2"
      shift 2
      ;;
    --owner)
      [[ -n "${2:-}" ]] || die "--owner requires a value"
      clone_owner="$2"
      shift 2
      ;;
    --examples)
      [[ -n "${2:-}" ]] || die "--examples requires a value"
      examples_root="$2"
      shift 2
      ;;
    --clone)
      do_clone=1
      shift
      ;;
    -h | --help)
      sed -n '2,40p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

command -v ruby >/dev/null 2>&1 || die "ruby is required"

cleanup() {
  [[ -z "$clone_dir" ]] || rm -rf "$clone_dir"
}
trap cleanup EXIT

# CI has no ~/dev/oss. Shallow-clone the consumers instead of teaching the
# checker a second way to read a repository — a `git clone --depth 1` yields the
# same committed-default-branch semantics the local path already relies on, so
# there is one code path to reason about rather than two.
clone_consumers() {
  local directory repo
  clone_dir="$(mktemp -d)"
  consumer_root="$clone_dir"
  for directory in "$examples_root"/*/; do
    [[ -d "$directory" ]] || continue
    repo="$(basename "$directory")"
    git clone --quiet --depth 1 --filter=blob:none \
      "https://github.com/$clone_owner/$repo.git" "$clone_dir/$repo" 2>/dev/null ||
      printf 'note: could not clone %s/%s — its examples will report UNVERIFIABLE\n' \
        "$clone_owner" "$repo" >&2
  done
}

((do_clone == 0)) || clone_consumers

examples=()
while IFS= read -r file; do
  examples+=("$file")
done < <(find "$examples_root" -type f \( -name '*.yml' -o -name '*.yaml' \) | sort)

[[ ${#examples[@]} -gt 0 ]] ||
  die "no examples found under $examples_root — examples/ is the surface consumers copy, so an empty scan is a broken checker, not a clean repo"

findings="$(mktemp)"
trap 'rm -f "$findings"; cleanup' EXIT

ruby tests/lib/example-references.rb --consumer-root "$consumer_root" "${examples[@]}" > "$findings" ||
  die "the reference resolver failed to run"

count_status() {
  awk -F'\t' -v want="$1" '$2 == want { total++ } END { print total + 0 }' "$findings"
}

missing="$(count_status MISSING)"
unverifiable="$(count_status UNVERIFIABLE)"
resolved="$(count_status OK)"

# A consumer counts as verified only when something about it actually resolved.
verified_consumers="$(
  awk -F'\t' '$2 == "OK" { split($1, part, "/"); seen[part[2]] = 1 }
              END { print length(seen) }' "$findings"
)"

printf 'Example fidelity: %d resolved, %d MISSING, %d UNVERIFIABLE across %d consumer repo(s).\n' \
  "$resolved" "$missing" "$unverifiable" "$verified_consumers"

if [[ "$missing" -gt 0 ]]; then
  printf '\nBroken references (the example is red as drafted — fix the example, not the consumer):\n'
  awk -F'\t' '$2 == "MISSING" { printf "  %-38s %-14s %s\n      -> %s\n", $1, $3, $4, $5 }' "$findings"
fi

if [[ "$unverifiable" -gt 0 ]]; then
  printf '\nCould not verify (NOT a pass — nothing was checked here):\n'
  awk -F'\t' '$2 == "UNVERIFIABLE" { printf "  %-38s %-14s %s\n      -> %s\n", $1, $3, $4, $5 }' "$findings"
fi

if [[ "$resolved" -lt "$MIN_RESOLVED_REFERENCES" || "$verified_consumers" -lt "$MIN_VERIFIED_CONSUMERS" ]]; then
  printf '\n::error::example-fidelity resolved %d reference(s) across %d consumer(s), below the floor of %d/%d. This run did not inspect enough to mean anything — clone the consumer repositories (or pass --clone) rather than reading this as a pass.\n' \
    "$resolved" "$verified_consumers" "$MIN_RESOLVED_REFERENCES" "$MIN_VERIFIED_CONSUMERS" >&2
  exit 2
fi

if [[ "$missing" -gt 0 ]]; then
  printf '\n::error::%d example reference(s) do not resolve in the repository the example is written for. Copying these examples would produce a red workflow.\n' \
    "$missing" >&2
  exit 1
fi

printf 'Every example reference resolves.\n'
