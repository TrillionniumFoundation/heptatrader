#!/usr/bin/env python3
"""Fail-closed compatibility entrypoint for retired direct XT/QMT trading."""

import sys


def main() -> int:
    print(
        "ERROR: direct XT/QMT broker scripts are quarantined. "
        "Use MCP/heptactl through hepta-tool-gatewayd and the Execution Service.",
        file=sys.stderr,
    )
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
