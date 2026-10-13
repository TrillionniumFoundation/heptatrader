"""Generate exact, inert systemd Guard drop-ins for a staged Laya archive cutover.

No deployment, daemon reload, service stop/start, trade, or model authority.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from historical_training.common import EvidenceError, sha, read

PYTHON = "/home/alex/.local/share/heptatrader/laya-runtime/venv/bin/python"
GUARD_RUNTIME = "/home/alex/.local/share/heptatrader/resident-resource-guard/training-repair-d20801cce510-namespace"
ROLES = ("trainer", "evaluator")
CONF_PREFIX = "zzzz-archive-guard-pin-20261013"


def _path(value):
    p = Path(value)
    if not p.is_absolute() or str(p) != value or any(
            ch in value for ch in ("\n", "\r", "\t", " ", ";", "#", "'", '"')):
        raise EvidenceError("ARCHIVE_SYSTEMD_UNSAFE_PATH")
    return p


def render_dropin(role, config_path, runtime):
    if role not in ROLES:
        raise EvidenceError("ARCHIVE_SYSTEMD_UNKNOWN_ROLE")
    _path(str(config_path))
    _path(str(runtime))
    _path(PYTHON)
    _path(GUARD_RUNTIME)
    command = (PYTHON + " -B -m historical_training.resident_resource_guard"
               + " --config " + str(config_path) + " --role " + role)
    return (
        "[Service]\n"
        "ExecCondition=\n"
        "ExecCondition=" + command + " --check-only\n"
        "ExecStart=\n"
        "ExecStart=" + command + "\n"
        "Environment=PYTHONPATH=" + GUARD_RUNTIME + ":" + str(runtime) +
        "/learn:" + str(runtime) + "/scripts\n"
        "WorkingDirectory=" + str(runtime) + "\n"
    )


def prepare(staged, output):
    staged = _path(str(staged))
    output = _path(str(output))
    if staged.is_symlink() or output.exists() or output.is_symlink():
        raise EvidenceError("ARCHIVE_SYSTEMD_STAGING_MUST_BE_NEW")
    proof = read(staged / "receipt.json")
    c = read(staged / "resident.json")
    g = read(staged / "guard.json")
    if (proof.get("schema") != "heptatrader.laya-archive-runtime-cutover.v1"
            or sha(staged / "resident.json") != proof["new_config_sha256"]
            or sha(staged / "guard.json") != proof["new_guard_sha256"]
            or g.get("original_config") != str(staged / "resident.json")
            or g.get("original_config_sha256") != proof["new_config_sha256"]
            or c.get("runtime_directory") != proof["new_runtime"]
            or c.get("automatic_trading") is not False
            or g.get("trade_authority") is not False):
        raise EvidenceError("ARCHIVE_SYSTEMD_PIN_DRIFT")
    target = _path(c["runtime_directory"])
    if target.is_symlink() or not target.is_dir():
        raise EvidenceError("ARCHIVE_SYSTEMD_RUNTIME_UNAVAILABLE")
    if not Path(PYTHON).is_file() or not Path(GUARD_RUNTIME).is_dir():
        raise EvidenceError("ARCHIVE_SYSTEMD_EXISTING_RUNTIME_UNAVAILABLE")
    output.mkdir(parents=True, mode=0o700)
    files = {}
    for role in ROLES:
        filename = CONF_PREFIX + "-" + role + ".conf"
        dest = output / filename
        raw = render_dropin(role, staged / "guard.json", target).encode()
        fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        files[role] = {"staged_path": str(dest), "sha256": sha(dest)}
    result = {
        "schema": "heptatrader.laya-archive-guard-systemd-plan.v1",
        "guard_sha256": proof["new_guard_sha256"],
        "original_immutable_config_preserved": True,
        "service_dropins": files,
        "old_resource_limits_preserved": True,
        "systemd_deployed": False,
        "systemd_restarted": False,
        "canonical_owner_granted": False,
        "broker_orders_sent": 0,
    }
    dest = output / "unit-plan.json"
    fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(result, f, sort_keys=True, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    parent = os.open(output, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW)
    try: os.fsync(parent)
    finally: os.close(parent)
    return result


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--staged",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    x=p.parse_args()
    os.umask(0o077)
    print(json.dumps(prepare(x.staged,x.output),sort_keys=True))


if __name__=="__main__":
    main()
