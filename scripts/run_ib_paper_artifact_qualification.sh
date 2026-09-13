#!/usr/bin/env bash
set -euo pipefail
umask 077
# Stable operator entry point; supervision and durable attempt handling live in
# one implementation. Python isolation prevents user-site/PYTHONPATH injection.
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec /usr/bin/python3 -I "$script_dir/run_ib_paper_campaign.py" "$@"
