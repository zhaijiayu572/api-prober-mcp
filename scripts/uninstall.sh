#!/usr/bin/env bash
# Remove runtime/client registration; credentials and logs stay unless purged.
set -euo pipefail

purge_data=false
if [[ "${1:-}" == "--purge-data" ]]; then
  purge_data=true
  shift
fi
if (($#)) && [[ "${1:-}" != "--help" && "${1:-}" != "-h" ]]; then
  echo "Usage: ./scripts/uninstall.sh [--purge-data]" >&2
  exit 2
fi
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  echo "Usage: ./scripts/uninstall.sh [--purge-data]"
  exit 0
fi

data_root="${API_PROBER_HOME:-$HOME/.api-prober-mcp}"

if command -v claude >/dev/null 2>&1; then
  claude mcp remove api-prober >/dev/null 2>&1 || true
fi
if command -v codex >/dev/null 2>&1; then
  codex mcp remove api-prober >/dev/null 2>&1 || true
fi
rm -rf "$data_root/runtime"

if [[ "$purge_data" == true ]]; then
  printf 'This will delete all API Prober data at %s. Type DELETE to continue: ' "$data_root"
  read -r confirmation
  if [[ "$confirmation" != "DELETE" ]]; then
    echo "Cancelled; user data was preserved."
    exit 0
  fi
  rm -rf "$data_root"
fi

echo "API Prober runtime removed."
