#!/usr/bin/env bash
# Scenario suite for validate_first_party_release_lineage.
#
# The guard tolerates exactly one thing it did not used to: at the tagged release
# commit, a first-party ref may still name the immediately-preceding release, because
# a commit cannot contain its own SHA (see tests/lib/first-party-lineage.sh). An
# exemption is only safe if it is *narrow*, and "narrow" is not observable from this
# repository — we cannot manufacture a two-releases-behind tagged commit here to
# prove the guard would reject it. So each case below builds a throwaway git repo
# with synthetic ci-vX.Y.Z tags and asserts the guard's verdict directly.
#
# The cases that must keep FAILING are the point of the file. If the exemption ever
# widens — to any older release, or to ordinary non-tagged commits — these go red.
set -uo pipefail

suite_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=tests/lib/first-party-lineage.sh
source "$suite_root/tests/lib/first-party-lineage.sh"

suite_failures=0
guard_failures=0

# The guard reports through `fail`; capture instead of printing.
fail() {
  guard_failures=$((guard_failures + 1))
  captured_reasons+=("$*")
}

report() {
  printf 'FAIL: %s\n' "$*" >&2
  suite_failures=$((suite_failures + 1))
}

# Build a throwaway repo, then run the guard in it and compare against expectation.
# $1 expectation: pass|fail   $2 description   $3 builder function
run_case() {
  local expectation="$1" description="$2" builder="$3" workdir
  workdir="$(mktemp -d)"
  guard_failures=0
  captured_reasons=()

  (
    # errexit here on purpose: a fixture that silently no-ops (an empty commit that
    # never lands, a tag applied to the wrong commit) would quietly turn a case into
    # a different, weaker case that still "passes".
    set -e
    cd "$workdir"
    git init -q .
    git config user.email ci@example.test
    git config user.name "CI Test"
    git config commit.gpgsign false
    mkdir -p .github/workflows examples
    "$builder"
  ) || { report "$description: fixture builder errored"; rm -rf "$workdir"; return; }

  pushd "$workdir" >/dev/null || return
  validate_first_party_release_lineage || true
  popd >/dev/null || return

  rm -rf "$workdir"

  if [[ "$expectation" == pass && "$guard_failures" -ne 0 ]]; then
    report "$description: expected the guard to accept this tree, got: ${captured_reasons[*]}"
  elif [[ "$expectation" == fail && "$guard_failures" -eq 0 ]]; then
    report "$description: expected the guard to REJECT this tree, but it passed"
  fi
}

# --- fixture helpers (run inside the throwaway repo) ------------------------

# Write a workflow whose first-party ref names $1, then commit as $2.
commit_ref_to() {
  printf 'jobs:\n  gate:\n    uses: hseshadr/ci/.github/workflows/python-gate.yml@%s\n' "$1" \
    > .github/workflows/gate.yml
  git add -A
  git commit -qm "$2"
}

commit_empty() {
  git commit -q --allow-empty -m "$1"
}

# Two tagged releases, r1 then r2, leaving HEAD at r2's commit.
build_two_releases() {
  commit_empty "release one"
  git tag ci-v1.0.0
  commit_empty "release two"
  git tag ci-v1.0.1
}

# --- cases -----------------------------------------------------------------

# The bootstrap this change exists to legalize: HEAD *is* the newest tag, and the
# ref names the release immediately before it. Unavoidable, therefore allowed.
case_tagged_release_refs_previous() {
  build_two_releases
  # The real release shape: the commit we tag still carries the previous release's
  # pin, because it cannot carry its own SHA.
  commit_ref_to "$(git rev-parse 'ci-v1.0.1^{commit}')" "release three lags by one"
  git tag ci-v1.0.2
}

# One release further back is NOT the bootstrap — it is drift that happens to sit
# on a tagged commit. This is the case that keeps the exemption narrow.
case_tagged_release_refs_two_back() {
  build_two_releases
  commit_ref_to "$(git rev-parse 'ci-v1.0.0^{commit}')" "release three lags by two"
  git tag ci-v1.0.2
}

# Ordinary commit ahead of the newest tag: strict currency, exactly as before.
case_untagged_commit_refs_previous() {
  build_two_releases
  commit_ref_to "$(git rev-parse 'ci-v1.0.0^{commit}')" "main carries a stale pin"
}

# Ordinary commit ahead of the newest tag, correctly re-pinned: the post-release
# steady state on main.
case_untagged_commit_refs_newest() {
  build_two_releases
  commit_ref_to "$(git rev-parse 'ci-v1.0.1^{commit}')" "main re-pinned to newest"
}

# Ancestry is still enforced at the tagged commit — the exemption must not have
# turned the release commit into a hole where any SHA passes.
case_tagged_release_refs_non_ancestor() {
  commit_empty "release one"
  git tag ci-v1.0.0
  git checkout -q -b sidetrack
  commit_empty "never released"
  local orphan
  orphan="$(git rev-parse HEAD)"
  git checkout -q -
  commit_empty "release two"
  git tag ci-v1.0.1
  commit_ref_to "$orphan" "release three references an unreleased commit"
  git tag ci-v1.0.2
}

# A well-formed SHA that is not a commit here at all.
case_tagged_release_refs_unknown_commit() {
  build_two_releases
  commit_ref_to "0123456789abcdef0123456789abcdef01234567" "release three cites a stranger"
  git tag ci-v1.0.2
}

# No preceding release means no exemption is available.
case_first_release_refs_untagged_ancestor() {
  commit_empty "groundwork"
  local ancestor
  ancestor="$(git rev-parse HEAD)"
  commit_ref_to "$ancestor" "first release references a pre-release commit"
  git tag ci-v1.0.0
}

run_case pass "tagged release commit referencing the immediately-preceding release" \
  case_tagged_release_refs_previous
run_case fail "tagged release commit referencing two releases back" \
  case_tagged_release_refs_two_back
run_case fail "untagged commit referencing a superseded release" \
  case_untagged_commit_refs_previous
run_case pass "untagged commit referencing the newest release" \
  case_untagged_commit_refs_newest
run_case fail "tagged release commit referencing a non-ancestor commit" \
  case_tagged_release_refs_non_ancestor
run_case fail "tagged release commit referencing an unknown commit" \
  case_tagged_release_refs_unknown_commit
run_case fail "first release referencing an untagged ancestor (no previous release)" \
  case_first_release_refs_untagged_ancestor

if ((suite_failures > 0)); then
  printf '\n%d lineage guard case(s) failed.\n' "$suite_failures" >&2
  exit 1
fi

printf 'Lineage guard cases passed.\n'
