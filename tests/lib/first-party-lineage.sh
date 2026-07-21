#!/usr/bin/env bash
# First-party release-provenance guard.
#
# Extracted from tests/security-policy.sh so tests/lineage-guard-cases.sh can drive
# it against synthetic repositories. Sourcing this file only DEFINES the function;
# the caller must already provide `fail`.

# A pin-SHAPE check can only prove a ref names *a* commit. It cannot prove the
# commit is one we ever released, and that blindness is what let the last hole
# survive a green suite: ci-v2.0.0 (36bf999) is a perfectly-formed 40-hex SHA and
# a genuine ancestor of every later tag — and it carries nested @ci-v1 moving
# tags. Every example in this repo pointed at it, so a consumer who copied our
# own documented path inherited the mutable-ref hole the pin was supposed to close.
#
# So assert provenance, not shape:
#   lineage  — the SHA must exist here and be an ancestor of the newest release
#              tag (catches a fork's SHA, a dropped branch, a typo'd hex string)
#   currency — the SHA must BE the newest release tag (catches a valid, ancestral,
#              but superseded release — the ci-v2.0.0 case specifically)
# Currency is the one that would have caught it; lineage is what makes a bogus
# SHA legible instead of just "not current".
#
# ---------------------------------------------------------------------------
# The one exemption: the release commit itself.
#
# Our first-party self-references name absolute SHAs, and a commit cannot contain
# its own SHA. So at the instant we tag a release, every self-reference inside the
# tagged tree necessarily still names the PREVIOUS release — there is no writable
# value that would name the new one. Under a strict currency rule the tagged commit
# therefore fails its own guard, which made re-running CI at a release tag red for
# a structural reason rather than a real drift.
#
# This is not a taste question about strictness; it is forced. We verified the
# obvious escape empirically and it does not exist: a relative action path
# (`./.github/actions/foo`) inside a reusable workflow resolves against the
# CALLER's workspace, not this repository. Run 29838733369 on hseshadr/privacy-core
# printed `PROBE_RESULT=RESOLVED_TO_CONSUMER_REPO` and
# `action_path=/home/runner/work/privacy-core/privacy-core/./.github/actions/probe-origin`,
# and the no-checkout control failed with "Did you forget to run actions/checkout
# before running your local action?". So an absolute SHA really is the only
# immutable form available, and the bootstrap lag really is unavoidable.
#
# The exemption is therefore scoped as narrowly as the constraint requires:
#   - it fires ONLY when HEAD is exactly the newest release tag's commit
#   - it accepts ONLY the immediately-preceding release, never an older one
#   - lineage (existence + ancestry) is still enforced, with no carve-out
#   - on every ordinary commit the strict "must be the newest tag" rule is intact
#
# Residual gap, stated plainly: a consumer pinning ci-vX.Y.Z gets that release's
# reusable workflows, but the composites nested inside them come from ci-vX.Y.(Z-1).
# README.md documents this under "The release-commit bootstrap".
validate_first_party_release_lineage() {
  local newest newest_sha head_sha previous previous_sha refs sha files

  command -v git >/dev/null 2>&1 || {
    fail "git is unavailable, so first-party ref provenance cannot be verified"
    return
  }
  git rev-parse --git-dir >/dev/null 2>&1 || {
    fail "not a git checkout, so first-party ref provenance cannot be verified"
    return
  }

  newest="$(git tag -l 'ci-v[0-9]*.[0-9]*.[0-9]*' | sort -V | tail -1)"
  [[ -n "$newest" ]] || {
    fail "no ci-vX.Y.Z release tag is reachable (fetch tags: actions/checkout needs fetch-depth: 0)"
    return
  }
  newest_sha="$(git rev-parse "$newest^{commit}")"
  head_sha="$(git rev-parse 'HEAD^{commit}')"

  # Only the tagged release commit gets to lag, and only by one release.
  previous=""
  previous_sha=""
  if [[ "$head_sha" == "$newest_sha" ]]; then
    previous="$(git tag -l 'ci-v[0-9]*.[0-9]*.[0-9]*' | sort -V | tail -2 | head -1)"
    if [[ -n "$previous" && "$previous" != "$newest" ]]; then
      previous_sha="$(git rev-parse "$previous^{commit}")"
    fi
  fi

  refs="$(grep -rhoE --include='*.yml' \
    'hseshadr/ci/[^[:space:]]*@[0-9a-f]{40}' .github examples | sed 's/.*@//' | sort -u)"

  while IFS= read -r sha; do
    [[ -n "$sha" ]] || continue

    files="$(grep -rlF --include='*.yml' "$sha" .github examples | paste -sd' ' -)"

    if ! git cat-file -e "${sha}^{commit}" 2>/dev/null; then
      fail "first-party ref names a commit unknown to this repository: $sha ($files)"
      continue
    fi
    if ! git merge-base --is-ancestor "$sha" "$newest_sha" 2>/dev/null; then
      fail "first-party ref $sha is not an ancestor of $newest — it was never part of a release ($files)"
      continue
    fi
    if [[ "$sha" == "$newest_sha" ]]; then
      continue
    fi
    if [[ -n "$previous_sha" && "$sha" == "$previous_sha" ]]; then
      # HEAD is the $newest tag's own commit and this ref names $previous: the
      # unavoidable bootstrap state described above, not drift.
      continue
    fi
    fail "first-party ref $sha is a superseded release, not $newest ($newest_sha) — re-pin ($files)"
  done <<< "$refs"
}
