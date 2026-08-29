#!/usr/bin/env python3

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hepta_ops.cli import main  # noqa: E402
from hepta_ops.registry import RegistryError  # noqa: E402
from hepta_ops.sandbox import SandboxError  # noqa: E402


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RegistryError, SandboxError, OSError) as error:
        print(f"hepta-ops: {error}", file=sys.stderr)
        raise SystemExit(78)
