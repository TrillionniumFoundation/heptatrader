#!/usr/bin/env python3

"""Broker-free stand-in for the templated IB PAPER execution daemon.

The disposable effective-systemd gate installs this file at the production
daemon path.  It deliberately contains no broker client, credential parser,
socket protocol, or order surface.  Its only behaviors are a bounded startup
failure and an idle process that can be terminated by the gate.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import signal
import sys
import time


DOMAIN = re.compile(r"[a-z][a-z0-9-]{0,17}")


def main() -> int:
    domain = os.environ.get("HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID", "")
    mode = os.environ.get("HEPTA_PAPER_STUB_MODE", "")
    if DOMAIN.fullmatch(domain) is None or mode not in {"hold", "fail"}:
        return 78
    marker = Path(f"/var/lib/hepta-ib-execution-{domain}/inert-stub.started")
    marker.write_text(
        f"domain={domain}\nmode={mode}\npid={os.getpid()}\n",
        encoding="ascii",
    )
    if mode == "fail":
        return 42

    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        time.sleep(0.05)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
