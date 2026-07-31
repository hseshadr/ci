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

# zizmor's ONLINE audits (known-vulnerable-actions, ref-version-mismatch, ...)
# need a GitHub API token. Running without one silently skips them — a quieter
# gate pretending to be a passing one — so a missing token is an error here,
# never a downgrade. CI passes GH_TOKEN; locally `gh auth token` fills it.
GH_TOKEN="${GH_TOKEN:-$(gh auth token 2>/dev/null || true)}"
if [[ -z "$GH_TOKEN" ]]; then
  echo "::error::no GitHub token available (set GH_TOKEN or authenticate gh) — refusing to run zizmor with its online audits skipped"
  exit 1
fi
export GH_TOKEN

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

echo "--- zizmor (examples, online audits on) ---"
uvx "zizmor@${ZIZMOR_VERSION}" "$stage_dir" || status=1

# actionlint and zizmor check YAML shape and workflow security. NEITHER resolves a
# repo-relative path inside the consumer repository the example is written for, so
# both stayed green while examples/edge-reco/ci.yml named `frontend/.node-version`
# — a file edge-reco has never had. setup-node hard-fails on a missing version
# file, so that example was red as drafted and this gate passed it. The fidelity
# check is the missing half: it resolves every path, script and brick reference an
# example makes against the consumer's committed default branch.
#
# ORDER IS LOAD-BEARING: this runs LAST because it is the only check here that
# needs the network for consumer clones. A network failure should not mask a
# lint finding that needed no network to produce.
echo "--- example fidelity (references resolve in the consumer repo) ---"
fidelity_args=()
if [[ ! -d "${EXAMPLE_CONSUMER_ROOT:-$HOME/dev/oss}" ]]; then
  # CI has no ~/dev/oss. Shallow-clone the consumers rather than skip: a skip
  # here would be indistinguishable from a pass, which is the defect this whole
  # guard exists to end.
  fidelity_args+=(--clone)
fi
"$repo_root/tests/example-fidelity.sh" "${fidelity_args[@]+"${fidelity_args[@]}"}" || status=1

exit "$status"
