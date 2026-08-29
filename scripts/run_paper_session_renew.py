#!/usr/bin/env python3
"""Enter the audited durable PAPER session-renew transaction."""

from __future__ import annotations

import os
from pathlib import Path


INSTALLED_REPAIR = Path("/usr/libexec/hepta-local-paper-repair")
SOURCE_REPAIR = Path(__file__).resolve().with_name("run_paper_repair.py")


def main() -> None:
    repair = INSTALLED_REPAIR if INSTALLED_REPAIR.exists() else SOURCE_REPAIR
    os.execv(str(repair), [str(repair), "renew-session"])


if __name__ == "__main__":
    main()
