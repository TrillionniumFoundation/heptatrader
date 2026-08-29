#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

exec python3 scripts/openclaw_agent_trader_data_plane_refresh.py --repo "$SCRIPT_DIR" "$@"
