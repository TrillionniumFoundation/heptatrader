#!/usr/bin/env python3
"""Run the CMake-built allocator-fault sweep in a fresh private journal directory."""
from pathlib import Path
import subprocess
import sys
import tempfile


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: recovery_projection_runner.py <built-fault-sweep>", file=sys.stderr)
        return 2
    executable = Path(sys.argv[1]).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="hepta-recovery-faults-") as private:
        result = subprocess.run([str(executable), private], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=85)
        print(result.stdout, end="", flush=True)
        if result.returncode:
            return result.returncode if result.returncode > 0 else 1
        if "recovery_projection_faults: PASS" not in result.stdout:
            print("fault sweep did not produce its completion marker", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
