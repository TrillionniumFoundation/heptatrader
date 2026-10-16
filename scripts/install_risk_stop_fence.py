#!/usr/bin/env python3
"""Install a stop-request shield on the existing PAPER risk-exit dependency chain.

Only changes user-systemd *unit metadata*, with daemon-reload, never stops or
restarts a broker/strategy service. It is not independent risk management.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

GUARD_NAME = "95-risk-stop-continuity.conf"
GUARD_TEXT = "[Unit]\nRefuseManualStop=yes\n"
RISK_UNITS = (
    "heptatrader-paper-driver.service",
    "heptatrader-crypto-driver.service",
    "heptatrader-execution.service",
    "heptatrader-gateway.service",
    "heptatrader-execution.socket",
    "heptatrader-events.socket",
    "heptatrader-runtime.service",
    "ibgateway.service",
    "heptatrader.target",
)
SHOW_PROPS = ("LoadState", "ActiveState", "SubState", "MainPID",
              "InvocationID", "RefuseManualStop", "PartOf")


class StopFenceError(RuntimeError):
    pass


def check_paper_config(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        file = os.fstat(fd)
        if (not stat.S_ISREG(file.st_mode) or file.st_uid != os.geteuid() or
                file.st_mode & 0o077 or file.st_size > 65536):
            raise StopFenceError("PAPER_PROFILE_NOT_PRIVATE")
        raw = os.read(fd, file.st_size).decode("utf-8",errors="strict")
    finally:
        os.close(fd)
    values = {}
    for line in raw.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            if key in ("HEPTA_IB_EXECUTION_MODE", "HEPTA_IB_PAPER_PORT"):
                if key in values:
                    raise StopFenceError("PAPER_PROFILE_DUPLICATE_FIELD")
                values[key] = value.strip()
    if values != {"HEPTA_IB_EXECUTION_MODE": "PAPER",
                  "HEPTA_IB_PAPER_PORT": "4002"}:
        raise StopFenceError("ONLY_EXISTING_PAPER_4002_BROKER")


def parse_show(output: str, *, is_socket: bool = False) -> dict[str, str]:
    d = {}
    for line in output.splitlines():
        if "=" not in line:
            raise StopFenceError("UNRECOGNIZED_SYSTEMD_STATUS")
        key, value = line.split("=", 1)
        if key not in SHOW_PROPS or key in d:
            raise StopFenceError("UNRECOGNIZED_SYSTEMD_PROPERTY")
        d[key] = value
    # Sockets and inactive target units need not expose service MainPID.
    # An inactive target is valid: future manual start/stop must be shielded.
    if is_socket and "MainPID" not in d:
        d["MainPID"] = ""
    if set(d) != set(SHOW_PROPS):
        raise StopFenceError("INCOMPLETE_SYSTEMD_PROPERTIES")
    return d


def show_unit(name: str) -> dict[str, str]:
    if name not in RISK_UNITS:
        raise StopFenceError("NON_RISK_UNIT_REFUSED")
    text = subprocess.check_output(
        ["systemctl", "--user", "show", name, "--no-pager",
         *[flag for p in SHOW_PROPS for flag in ("-p", p)]],
        text=True, timeout=15,
    )
    return parse_show(text, is_socket=name.endswith((".socket", ".target")))


def check_state(before: dict, after: dict, *, required: bool) -> None:
    for name, original in before.items():
        current = after[name]
        is_target = name == "heptatrader.target"
        if (original["LoadState"] != "loaded" or
                (is_target and original["ActiveState"] not in ("active", "inactive")) or
                (not is_target and original["ActiveState"] != "active")):
            raise StopFenceError("RISK_PATH_NOT_ACTIVE:" + name)
        for key in ("LoadState", "ActiveState", "MainPID", "InvocationID", "PartOf"):
            if original[key] != current[key]:
                raise StopFenceError("LIVE_RISK_PROCESS_OR_DEPENDENCY_CHANGED:" + name)
        # A drop-in cannot REMOVE PartOf= dependencies. We instead protect
        # both endpoints of manual stop propagation: the exact target AND
        # the individual eight risk-chain units. Dependency stops from a
        # manager shutdown or external failure remain unprotected.
        if required and current["RefuseManualStop"] != "yes":
            raise StopFenceError("EFFECTIVE_STOP_FENCE_NOT_INSTALLED:" + name)


def private_directory(path: Path, uid: int) -> None:
    if path.is_symlink():
        raise StopFenceError("SYMLINKED_UNIT_DIRECTORY")
    if path.exists():
        st = path.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid:
            raise StopFenceError("UNOWNED_UNIT_DIRECTORY")
    else:
        path.mkdir(mode=0o700)
        st = path.lstat()
        if st.st_uid != uid or not stat.S_ISDIR(st.st_mode):
            raise StopFenceError("UNIT_DIRECTORY_CREATION_FAILED")


def write_new(path: Path, data: bytes) -> None:
    """Create private bytes durably, or leave no partial installer-created file."""
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                 os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        # O_EXCL established exclusive ownership of this newly created path.
        # A failed flush is NOT a valid installed stop policy or audit receipt.
        path.unlink(missing_ok=True)
        raise


def install(home: Path, *, persist_receipt=None) -> dict:
    if os.geteuid() == 0:
        raise StopFenceError("NON_ROOT_USER_SYSTEMD_ONLY")
    check_paper_config(home / ".config/heptatrader/execution.env")
    uid = os.geteuid()
    user_units = home / ".config/systemd/user"
    private_directory(user_units, uid)
    before = {name: show_unit(name) for name in RISK_UNITS}
    check_state(before, before, required=False)
    additions: list[Path] = []
    paths = {}
    original_states = {}
    try:
        for name in RISK_UNITS:
            directory = user_units / (name + ".d")
            private_directory(directory, uid)
            path = directory / GUARD_NAME
            if path.is_symlink():
                raise StopFenceError("SYMLINKED_STOP_FENCE:" + name)
            if path.exists():
                if path.read_bytes() != GUARD_TEXT.encode():
                    raise StopFenceError("EXISTING_STOP_FENCE_CHANGED:" + name)
                original_states[name] = "ALREADY_PRESENT"
            else:
                write_new(path, GUARD_TEXT.encode())
                additions.append(path)
                original_states[name] = "INSTALLED"
            paths[name] = str(path)
        subprocess.run(["systemctl", "--user", "daemon-reload"],
                       check=True, timeout=30)
        after = {name: show_unit(name) for name in RISK_UNITS}
        check_state(before, after, required=True)
        report = {
            "schema": "heptatrader.paper-stop-request-fence.v2",
            "state": "MANUAL_TARGET_AND_RISK_PATH_STOP_REQUESTS_SHIELDED_ONLY",
            "critical_limit": "NOT_INDEPENDENT_BROKER_PROTECTIVE_ORDER",
            "at_ms": int(time.time() * 1000),
            "mode": "PAPER",
            "installer_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "dropin_text_sha256": hashlib.sha256(GUARD_TEXT.encode()).hexdigest(),
            "risk_units": list(RISK_UNITS),
            "unit_dropins": paths,
            "install_status": original_states,
            "all_effective_refuse_manual_stop": True,
            "manual_target_stop_refused": True,
            "existing_part_of_dependencies_preserved": True,
            "original_unit_invocations_preserved": True,
            "protected_services_restarted": 0,
            "broker_orders_sent": 0,
            "trade_authority_changed": False,
            "still_unprotected_from": [
                "HOST_SHUTDOWN", "MANAGER_PROCESS_TERMINATION",
                "DEPENDENCY_FAILURE", "SIGKILL", "NETWORK_OR_BROKER_FAILURE",
                "BROKER_NATIVE_STOP_ABSENT",
                "NONMANUAL_TARGET_DEPENDENCY_STOP",
            ],
        }
        # Include the private activation receipt in the SAME rollback scope.
        # A successful daemon-reload without a durable receipt is not PASS.
        if persist_receipt is not None:
            persist_receipt(report)
    except BaseException:
        # Never stop/restart risk units. Remove only exact bytes created by
        # this attempt; refuse to delete another operator's changed drop-in.
        for path in additions:
            if path.is_file() and not path.is_symlink() and path.read_bytes()==GUARD_TEXT.encode():
                path.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"],
                       check=False, timeout=30)
        raise
    return report



def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()
    if not args.install or args.evidence_dir is None:
        parser.error("explicit --install --evidence-dir required")
    os.umask(0o077)
    # Refuse a bad evidence destination before changing effective unit policy.
    root = args.evidence_dir
    private_directory(root.parent, os.geteuid())
    private_directory(root, os.geteuid())
    filename = root / ("stop-shield-" + str(time.time_ns()) + ".json")
    def persist_receipt(report):
        raw = (json.dumps(report, sort_keys=True, indent=2) + "\n").encode()
        write_new(filename, raw)
    report = install(Path.home(), persist_receipt=persist_receipt)
    raw = filename.read_bytes()
    print(json.dumps({
        "state": report["state"],
        "guarded_units": len(RISK_UNITS),
        "no_service_restart": report["protected_services_restarted"] == 0,
        "evidence_file": str(filename),
        "evidence_sha256": hashlib.sha256(raw).hexdigest(),
        "still_no_broker_native_stop": True,
    }))


if __name__ == "__main__":
    try:
        main()
    except (OSError, StopFenceError, subprocess.SubprocessError) as exc:
        print(type(exc).__name__ + ":" + str(exc)[:200], file=sys.stderr)
        raise SystemExit(1)
