#!/usr/bin/env bash
set -euo pipefail

mode="${1:?mode is required}"
install_args_input="${2:-}"
install_args=()

read -r -a requested_args <<< "$install_args_input"
for arg in "${requested_args[@]}"; do
  case "$arg" in
    --frozen-lockfile | --config.dangerously-allow-all-builds=true)
      install_args+=("$arg")
      ;;
    *)
      printf '::error::Invalid pnpm install argument: %s\n' "$arg" >&2
      exit 1
      ;;
  esac
done

case "$mode" in
  --validate) printf '%s\n' "${install_args[@]}" ;;
  install) exec pnpm install "${install_args[@]}" ;;
  *)
    printf '::error::Invalid pnpm install mode: %s\n' "$mode" >&2
    exit 1
    ;;
esac
