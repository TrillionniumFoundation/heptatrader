"""Read workflow structure and execute its real shell blocks with inert fixtures."""
from __future__ import annotations

from pathlib import Path
import os
import subprocess
import yaml

ROOT = Path(__file__).resolve().parents[2]


def workflow(name: str) -> dict:
    # BaseLoader preserves GitHub's `on` key and booleans as strings, unlike
    # YAML 1.1 implicit booleans. No custom constructors or object loading.
    value = yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)
    if not isinstance(value, dict):
        raise ValueError("workflow must be a mapping")
    return value


def step(job: dict, identifier: str) -> dict:
    matches = [item for item in job["steps"] if item.get("id") == identifier]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one step id={identifier}")
    return matches[0]


def shell(body: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    # Same fail-fast invocation as Actions. Callers supply only non-secret,
    # controlled inputs; network and privileged helpers are replaced by stubs.
    return subprocess.run(["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", body],
                          cwd=cwd, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", **(env or {})},
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
