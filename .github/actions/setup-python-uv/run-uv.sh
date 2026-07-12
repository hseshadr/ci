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

  read -r -a requested <<< "$value"
  while ((index < ${#requested[@]})); do
    argument="${requested[$index]}"
    case "$argument" in
      --frozen | --locked | --all-extras)
        validated_args+=("$argument")
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
