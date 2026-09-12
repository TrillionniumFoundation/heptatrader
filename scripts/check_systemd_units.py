#!/usr/bin/env python3
"""Lint HeptaTrader systemd units without requiring a running systemd or root.

The check is deliberately structural: it catches unit wiring and readiness
contract drift in CI and developer checkouts.  ``systemd-analyze verify`` can
be run separately on a target distribution, but this script never talks to
PID 1 or mutates host state.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def _parse(path: Path) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    section = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            sections.setdefault(section, [])
        elif section:
            sections[section].append(line)
    return sections


def _values(section: list[str], key: str) -> list[str]:
    prefix = key + "="
    return [line[len(prefix):] for line in section if line.startswith(prefix)]


def _lint(root: Path) -> list[str]:
    systemd = root / "systemd"
    errors: list[str] = []
    service_paths = sorted(systemd.glob("*.service"))
    socket_paths = sorted(systemd.glob("*.socket"))
    if not service_paths or not socket_paths:
        return ["systemd: expected service and socket units"]

    services: dict[str, dict[str, list[str]]] = {}
    for path in service_paths:
        try:
            sections = _parse(path)
        except OSError as exc:
            errors.append(f"{path}: unreadable: {exc}")
            continue
        services[path.name] = sections
        unit = f"systemd/{path.name}"
        service = sections.get("Service", [])
        if not service:
            errors.append(f"{unit}: missing [Service]")
            continue
        for key in ("Type", "User", "Group", "ExecStart", "NoNewPrivileges", "ProtectSystem", "UMask"):
            if not _values(service, key):
                errors.append(f"{unit}: missing Service.{key}")
        starts = _values(service, "ExecStart")
        if starts and not starts[0].startswith("/"):
            errors.append(f"{unit}: ExecStart must use an absolute executable path")
        if _values(service, "UMask") and _values(service, "UMask")[0] != "0077":
            errors.append(f"{unit}: UMask must be 0077")
        if _values(service, "NoNewPrivileges") and _values(service, "NoNewPrivileges")[0].lower() != "yes":
            errors.append(f"{unit}: NoNewPrivileges must be yes")
        if _values(service, "ProtectSystem") and _values(service, "ProtectSystem")[0] != "strict":
            errors.append(f"{unit}: ProtectSystem must be strict")

    for path in socket_paths:
        try:
            sections = _parse(path)
        except OSError as exc:
            errors.append(f"{path}: unreadable: {exc}")
            continue
        unit = f"systemd/{path.name}"
        socket = sections.get("Socket", [])
        if not socket:
            errors.append(f"{unit}: missing [Socket]")
            continue
        listens = _values(socket, "ListenStream")
        if not listens or not all(value.startswith("/run/") for value in listens):
            errors.append(f"{unit}: ListenStream must be an absolute /run path")
        if not _values(socket, "SocketMode"):
            errors.append(f"{unit}: missing SocketMode")
        if not _values(socket, "Service"):
            errors.append(f"{unit}: missing Service association")

    simulator = services.get("hepta-execution-simulator.service")
    if simulator:
        service = simulator.get("Service", [])
        if _values(service, "PrivateNetwork") != ["yes"]:
            errors.append("systemd/hepta-execution-simulator.service: must be PrivateNetwork=yes")
        if not any(value == "HEPTA_EXECUTION_SERVICE_MODE=SIMULATOR" for value in _values(service, "Environment")):
            errors.append("systemd/hepta-execution-simulator.service: simulator mode is not explicit")
        if "Install" in simulator:
            errors.append("systemd/hepta-execution-simulator.service: authority unit must not be install-enabled")

    paper = services.get("hepta-execution-ib-paper.service")
    if paper:
        unit = "systemd/hepta-execution-ib-paper.service"
        required = "hepta-broker-egress-policy.service"
        requires = _values(paper.get("Unit", []), "Requires")
        if not any(required in value.split() for value in requires):
            errors.append(f"{unit}: must require {required}")
        service = paper.get("Service", [])
        if len(_values(service, "LoadCredential")) < 3:
            errors.append(f"{unit}: expected fenced credential declarations")
        if not _values(service, "ReadWritePaths"):
            errors.append(f"{unit}: must declare bounded ReadWritePaths")

    policy = services.get("hepta-broker-egress-policy.service")
    if policy:
        service = policy.get("Service", [])
        if _values(service, "User") != ["root"] or _values(service, "Group") != ["root"]:
            errors.append("systemd/hepta-broker-egress-policy.service: policy service must run as root")
        if not any("CAP_NET_ADMIN" in value for value in _values(service, "CapabilityBoundingSet")):
            errors.append("systemd/hepta-broker-egress-policy.service: CAP_NET_ADMIN is required")
        if not any("--deny-all" in value for value in _values(service, "ExecStop")):
            errors.append("systemd/hepta-broker-egress-policy.service: ExecStop must enforce deny-all")
    return errors


def validate(root: Path | str = ROOT) -> list[str]:
    return _lint(Path(root).resolve())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    errors = validate(args.root)
    for error in errors:
        print(f"[SYSTEMD] {error}", file=sys.stderr)
    if errors:
        return 1
    print("[SYSTEMD] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
