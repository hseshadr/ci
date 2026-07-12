#!/usr/bin/env bash
set -euo pipefail

dist_dir="${1:?dist directory is required}"
headers_file="${dist_dir%/}/_headers"

if [[ ! -d "$dist_dir" ]]; then
  printf '::error::Pages dist directory does not exist: %s\n' "$dist_dir" >&2
  exit 1
fi

# An application-owned file may need a stricter CSP or intentional embedding policy.
if [[ -e "$headers_file" || -L "$headers_file" ]]; then
  printf '::notice::Preserving application-owned Pages headers: %s\n' "$headers_file"
  exit 0
fi

printf '%s\n' \
  '/*' \
  "  Content-Security-Policy: base-uri 'self'; frame-ancestors 'none'; object-src 'none'" \
  '  Permissions-Policy: camera=(), geolocation=(), microphone=()' \
  '  Referrer-Policy: strict-origin-when-cross-origin' \
  '  Strict-Transport-Security: max-age=31536000' \
  '  X-Content-Type-Options: nosniff' \
  '  X-Frame-Options: DENY' \
  > "$headers_file"

printf '::notice::Added baseline Pages security headers: %s\n' "$headers_file"
