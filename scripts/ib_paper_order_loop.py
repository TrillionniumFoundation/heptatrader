#!/usr/bin/env python3
"""Fail-closed compatibility entrypoint for the retired direct-IB loop."""

import sys


def main() -> int:
    print(
        "ERROR: direct broker order loops are quarantined. "
        "Use heptactl/MCP through hepta-tool-gatewayd and the Execution Service.",
        file=sys.stderr,
    )
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
