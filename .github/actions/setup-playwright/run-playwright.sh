#!/usr/bin/env bash
set -euo pipefail

mode="${1:?mode is required}"
browsers_input="${2:-}"
browsers=()

read -r -a requested_browsers <<< "$browsers_input"
if ((${#requested_browsers[@]} == 0)); then
  printf '::error::At least one Playwright browser is required\n' >&2
  exit 1
fi

for browser in "${requested_browsers[@]}"; do
  case "$browser" in
    chromium | firefox | webkit) browsers+=("$browser") ;;
    *)
      printf '::error::Invalid Playwright browser: %s\n' "$browser" >&2
      exit 1
      ;;
  esac
done

case "$mode" in
  --validate) printf '%s\n' "${browsers[@]}" ;;
  install) exec pnpm exec playwright install --with-deps "${browsers[@]}" ;;
  install-deps) exec pnpm exec playwright install-deps "${browsers[@]}" ;;
  *)
    printf '::error::Invalid Playwright install mode: %s\n' "$mode" >&2
    exit 1
    ;;
esac
