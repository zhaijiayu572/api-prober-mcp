#!/usr/bin/env bash
# Install API Prober into a private per-user runtime without requiring sudo.
set -euo pipefail

register_clients=""
while (($#)); do
  case "$1" in
    --register)
      shift
      [[ $# -gt 0 ]] || { echo "--register requires claude,codex" >&2; exit 2; }
      register_clients="$1"
      ;;
    --help|-h)
      cat <<'EOF'
Usage: ./scripts/install.sh [--register claude,codex]

Creates ~/.api-prober-mcp/runtime/venv and installs this checkout. Client
registration is opt-in and never replaces an existing api-prober registration.
EOF
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
data_root="${API_PROBER_HOME:-$HOME/.api-prober-mcp}"
umask 077
mkdir -p "$data_root"/{credentials/profiles,logs,cache/responses,runtime}
chmod 700 "$data_root" "$data_root"/{credentials,credentials/profiles,logs,cache,cache/responses,runtime}

if [[ ! -f "$data_root/config.json" ]]; then
  printf '%s\n' '{"schemaVersion":1}' > "$data_root/config.json"
fi
chmod 600 "$data_root/config.json"

venv="$data_root/runtime/venv"
python3.12 -m venv "$venv"
"$venv/bin/python" -m pip install --upgrade pip
"$venv/bin/python" -m pip install "$repo_root"

register_client() {
  local client="$1"
  case "$client" in
    claude)
      command -v claude >/dev/null || { echo "Claude CLI was not found" >&2; return 1; }
      if claude mcp get api-prober >/dev/null 2>&1; then
        echo "Claude already has an api-prober MCP server; refusing to replace it." >&2
        return 1
      fi
      claude mcp add --scope user api-prober -- "$venv/bin/api-prober-mcp"
      ;;
    codex)
      command -v codex >/dev/null || { echo "Codex CLI was not found" >&2; return 1; }
      if codex mcp get api-prober >/dev/null 2>&1; then
        echo "Codex already has an api-prober MCP server; refusing to replace it." >&2
        return 1
      fi
      codex mcp add api-prober -- "$venv/bin/api-prober-mcp"
      ;;
    *) echo "Unsupported client: $client" >&2; return 1 ;;
  esac
}

if [[ -n "$register_clients" ]]; then
  IFS=',' read -r -a clients <<< "$register_clients"
  for client in "${clients[@]}"; do
    register_client "$client"
  done
fi

printf 'Installed API Prober: %s\n' "$venv/bin/api-prober-mcp"
