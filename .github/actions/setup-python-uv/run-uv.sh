#!/usr/bin/env bash
set -euo pipefail

mode="${1:?mode is required}"
value="${2:-}"
validated_args=()

validate_python_version() {
  [[ "$value" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || {
    printf '::error::Invalid Python version: %s\n' "$value" >&2
    exit 1
  }
}

validate_sync_args() {
  local index=0
  local requested=()
  local argument
  local option_value
  local lock_flag=""
  local allow_unlocked=""

  read -r -a requested <<< "$value"
  while ((index < ${#requested[@]})); do
    argument="${requested[$index]}"
    case "$argument" in
      --frozen | --locked)
        lock_flag=1
        validated_args+=("$argument")
        ;;
      --all-extras)
        validated_args+=("$argument")
        ;;
      --allow-unlocked)
        # Explicit opt-out from the locked-by-default policy. Consumed here —
        # it is a policy sentinel for this action, not a real uv flag.
        allow_unlocked=1
        ;;
      --group | --extra)
        index=$((index + 1))
        option_value="${requested[$index]:-}"
        [[ "$option_value" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
          printf '::error::Invalid value for %s: %s\n' "$argument" "$option_value" >&2
          exit 1
        }
        validated_args+=("$argument" "$option_value")
        ;;
      *)
        printf '::error::Invalid uv sync argument: %s\n' "$argument" >&2
        exit 1
        ;;
    esac
    index=$((index + 1))
  done

  # Locked by default: a sync that carries neither --frozen nor --locked lets
  # CI resolve dependency versions the lockfile never pinned. Opting out of
  # that guarantee must be explicit, never the quiet result of an empty input.
  if [[ -z "$lock_flag" && -z "$allow_unlocked" ]]; then
    printf '::error::uv sync would run UNLOCKED: sync-args must include --frozen or --locked (or opt out explicitly with --allow-unlocked)\n' >&2
    exit 1
  fi
}

case "$mode" in
  --validate-version)
    validate_python_version
    printf '%s\n' "$value"
    ;;
  --validate-sync)
    validate_sync_args
    printf '%s\n' "${validated_args[@]}"
    ;;
  python)
    validate_python_version
    exec uv python install "$value"
    ;;
  sync)
    validate_sync_args
    exec uv sync "${validated_args[@]}"
    ;;
  *)
    printf '::error::Invalid uv operation: %s\n' "$mode" >&2
    exit 1
    ;;
esac
