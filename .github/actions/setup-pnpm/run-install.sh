#!/usr/bin/env bash
set -euo pipefail

mode="${1:?mode is required}"
install_args_input="${2:-}"
install_args=()

frozen_flag=""
allow_unfrozen=""

read -r -a requested_args <<< "$install_args_input"
for arg in "${requested_args[@]}"; do
  case "$arg" in
    --frozen-lockfile)
      frozen_flag=1
      install_args+=("$arg")
      ;;
    --config.dangerously-allow-all-builds=true)
      install_args+=("$arg")
      ;;
    --allow-unfrozen-lockfile)
      # Explicit opt-out from the frozen-by-default policy. Consumed here —
      # it is a policy sentinel for this action, not a real pnpm flag.
      allow_unfrozen=1
      ;;
    *)
      printf '::error::Invalid pnpm install argument: %s\n' "$arg" >&2
      exit 1
      ;;
  esac
done

# Frozen by default: without --frozen-lockfile the install may rewrite the
# lockfile and resolve versions it never pinned. Opting out of that guarantee
# must be explicit, never the quiet result of an empty input.
if [[ -z "$frozen_flag" && -z "$allow_unfrozen" ]]; then
  printf '::error::pnpm install would run WITHOUT --frozen-lockfile: install-args must include it (or opt out explicitly with --allow-unfrozen-lockfile)\n' >&2
  exit 1
fi

case "$mode" in
  --validate) printf '%s\n' "${install_args[@]}" ;;
  install) exec pnpm install "${install_args[@]}" ;;
  *)
    printf '::error::Invalid pnpm install mode: %s\n' "$mode" >&2
    exit 1
    ;;
esac
