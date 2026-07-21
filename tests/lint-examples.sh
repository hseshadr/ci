#!/usr/bin/env bash
# Audit examples/ with the same tools that audit the workflows we run.
#
# WHY THIS EXISTS: zizmor only collects workflows from a `.github/workflows/`
# path. Every file in examples/ lives at `examples/<repo>/<name>.yml`, so a
# repo-root `zizmor .` scanned 0 of them while the README advertised a
# "full-repository" audit. examples/ is the copy-paste surface — it is the code
# most likely to end up in someone else's repo, and it was the least audited
# thing here.
#
# Fix: stage each example into a throwaway tree shaped like the repo it is
# written for (`<tmp>/<repo>/.github/workflows/<name>.yml`), then point both
# zizmor and actionlint at that tree. The staged layout is also what makes
# actionlint's workflow-shape checks meaningful.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

ZIZMOR_VERSION="1.26.1"

stage_dir="$(mktemp -d)"
trap 'rm -rf "$stage_dir"' EXIT

staged=0
while IFS= read -r example; do
  consumer="$(basename "$(dirname "$example")")"
  mkdir -p "$stage_dir/$consumer/.github/workflows"
  cp "$example" "$stage_dir/$consumer/.github/workflows/$(basename "$example")"
  staged=$((staged + 1))
done < <(find examples -type f \( -name '*.yml' -o -name '*.yaml' \) | sort)

if ((staged == 0)); then
  echo "::error::no example workflows found to audit — examples/ is the surface consumers copy"
  exit 1
fi

echo "Staged $staged example workflow(s) for audit."

status=0

echo "--- actionlint (examples) ---"
actionlint "$stage_dir"/*/.github/workflows/*.yml || status=1

echo "--- zizmor (examples) ---"
uvx "zizmor@${ZIZMOR_VERSION}" --no-online-audits "$stage_dir" || status=1

exit "$status"
