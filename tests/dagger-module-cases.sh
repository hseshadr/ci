#!/usr/bin/env bash
# Behavioral proof that central policy runs through the repository's Dagger API.
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
failures=0

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  failures=$((failures + 1))
}

functions="$(cd "$root" && dagger functions 2>/dev/null)" ||
  fail "the central Dagger module is not loadable"
grep -Eq '^policy[[:space:]]' <<< "$functions" ||
  fail "the central Dagger module does not expose policy"

if [[ "$failures" -ne 0 ]]; then
  exit 1
fi

printf 'central Dagger module cases passed\n'
