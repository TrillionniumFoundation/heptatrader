#!/usr/bin/env python3
"""Install and exercise an exact core artifact using real PID1/systemd.

DESTRUCTIVE TEST FIXTURE: disposable Linux CI VM only, never a trading host.
Refuses any existing HeptaTrader files, users, units or runtime state. No Broker
unit, SDK, network policy, PAPER credential or external order is involved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import grp
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests/python"))
sys.path.insert(0, str(ROOT / "scripts"))
from test_installed_runtime_processes import admitted_slot
import check_systemd_units

ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}
SERVICES = ("hepta-tool-gateway.service", "hepta-execution-simulator.service")
SOCKETS = ("hepta-tool-gateway.socket", "hepta-tool-session-supervisor.socket",
           "hepta-execution-simulator.socket", "hepta-execution-events-simulator.socket")
ACCOUNTS = ("hepta-gateway", "hepta-exec", "hepta-agent", "hepta-outsider")
PRIVATE_ROOTS = ("/etc/heptatrader", "/var/lib/hepta-execution", "/var/lib/hepta-tool-gateway",
                 "/run/hepta-agent", "/run/hepta-execution", "/run/hepta-tool-gateway")


def run(argv: list[str], *, check: bool = True, uid: int | None = None,
        gid: int | None = None, timeout: int = 30, data: str | None = None,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    identity = {} if uid is None else {"user": uid, "group": gid, "extra_groups": []}
    result = subprocess.run(argv, env=env or ENV, input=data, capture_output=True, text=True,
                            timeout=timeout, **identity)
    if check and result.returncode:
        # No token or credential is ever passed as an argument.
        raise RuntimeError(f"{argv[0]} exit={result.returncode}: {result.stdout[-4000:]} {result.stderr[-4000:]}")
    return result


def host_preconditions() -> None:
    if os.environ.get("HEPTA_DISPOSABLE_SYSTEMD_TEST") != "1":
        raise RuntimeError("requires HEPTA_DISPOSABLE_SYSTEMD_TEST=1 on a disposable VM")
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("requires root on a disposable Linux VM")
    if Path("/proc/1/comm").read_text().strip() != "systemd":
        raise RuntimeError("real systemd PID 1 is required; simulated activation is not this test")
    for name in ACCOUNTS:
        for lookup in (pwd.getpwnam, grp.getgrnam):
            try:
                lookup(name)
            except KeyError:
                continue
            raise RuntimeError(f"refusing existing account/group: {name}")
    for value in PRIVATE_ROOTS + ("/run/hepta",):
        if os.path.lexists(value):
            raise RuntimeError(f"refusing existing host state: {value}")
    for directory in ("/etc/systemd/system", "/run/systemd/system", "/usr/lib/systemd/system"):
        if list(Path(directory).glob("hepta-*")):
            raise RuntimeError(f"refusing existing HeptaTrader unit: {directory}")
    for command in ("systemctl", "systemd-analyze", "systemd-tmpfiles", "useradd", "userdel", "journalctl"):
        if shutil.which(command, path=ENV["PATH"]) is None:
            raise RuntimeError(f"missing host test dependency: {command}")


class SystemdFixture:
    def __init__(self, work: Path, evidence: Path):
        self.work = work
        self.evidence = evidence
        self.files: list[tuple[Path, tuple[int, int]]] = []
        self.directories: list[tuple[Path, tuple[int, int]]] = []
        self.private: list[tuple[Path, tuple[int, int]]] = []
        self.accounts: dict[str, pwd.struct_passwd] = {}
        self.processes: list[dict[str, Any]] = []
        self.phase = 0
        self.units_installed = False
        self.token = "simulator-disposable-fixture-token-" + os.urandom(16).hex()
        self.retained: dict[str, str] = {}

    @staticmethod
    def identity(path: Path) -> tuple[int, int]:
        value = path.lstat()
        return value.st_dev, value.st_ino

    def mkdir(self, path: Path, mode: int = 0o755, owner: str | None = None, private: bool = False) -> None:
        path.mkdir(mode=mode)
        path.chmod(mode)
        if owner:
            account = self.accounts[owner]
            os.chown(path, account.pw_uid, account.pw_gid)
        (self.private if private else self.directories).append((path, self.identity(path)))

    def parent_dirs(self, path: Path) -> None:
        missing = []
        parent = path.parent
        while not parent.exists():
            missing.append(parent); parent = parent.parent
        if parent.is_symlink() or not parent.is_dir():
            raise RuntimeError(f"unsafe install parent: {parent}")
        for directory in reversed(missing):
            self.mkdir(directory)

    def write(self, path: Path, content: bytes, mode: int = 0o644, owner: str | None = None) -> None:
        self.parent_dirs(path)
        with path.open("xb") as stream:
            stream.write(content)
        path.chmod(mode)
        if owner:
            account = self.accounts[owner]
            os.chown(path, account.pw_uid, account.pw_gid)
        self.files.append((path, self.identity(path)))

    def install(self, slot: Path, manifest: dict) -> None:
        problems = check_systemd_units.validate_installed(slot, "core")
        if problems:
            raise RuntimeError("installed unit contract: " + "; ".join(problems))
        targets = [(slot / item["path"], Path("/usr") / item["path"], item) for item in manifest["files"]]
        for _, target, _ in targets:
            if os.path.lexists(target):
                raise RuntimeError(f"refusing existing installed payload: {target}")
        # All refusal checks precede installation and user creation.
        for name in ACCOUNTS:
            run(["useradd", "--system", "--user-group", "--no-create-home", "--shell", "/usr/sbin/nologin", name])
            self.accounts[name] = pwd.getpwnam(name)
        for source, target, item in targets:
            data = source.read_bytes()
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise RuntimeError(f"admitted payload changed: {item['path']}")
            self.write(target, data, item["mode"])
        self.units_installed = True
        run(["systemctl", "daemon-reload"])

    def configure(self) -> None:
        for value, owner, mode in (("/etc/heptatrader", None, 0o755),
                ("/var/lib/hepta-execution", "hepta-exec", 0o700),
                ("/var/lib/hepta-tool-gateway", "hepta-gateway", 0o700),
                ("/run/hepta-agent", None, 0o711),
                ("/run/hepta-execution", None, 0o755),
                ("/run/hepta-tool-gateway", "hepta-gateway", 0o700)):
            self.mkdir(Path(value), mode, owner, private=True)
        self.mkdir(Path("/etc/heptatrader/credentials"), 0o700)
        self.write(Path("/etc/heptatrader/credentials/hepta-execution-simulator-fence"),
                   b"HFC1\nfencing_token=1\ngeneration=1\n", 0o400)
        self.write(Path("/etc/heptatrader/hepta-supervisor-lease.key"), os.urandom(16).hex().encode(), 0o400)
        agent = self.accounts["hepta-agent"]
        gateway = self.accounts["hepta-gateway"]
        execution = self.accounts["hepta-exec"]
        examples = Path("/usr/share/heptatrader/examples/systemd")

        def example(name: str, overrides: dict[str, str]) -> bytes:
            # Exercise the installed defaults, overriding only fixture identity
            # and the explicitly SIMULATOR-only test session's bounded policy.
            values = {}
            for line in (examples / name).read_text().splitlines():
                if line and not line.startswith("#"):
                    key, value = line.split("=", 1); values[key] = value
            values.update(overrides)
            return ("\n".join(f"{k}={v}" for k, v in sorted(values.items())) + "\n").encode()

        self.write(Path("/etc/heptatrader/hepta-execution-simulator.env"), example(
            "hepta-execution-simulator.env.example", {
                "HEPTA_EXECUTION_GATEWAY_UID": str(gateway.pw_uid),
                "HEPTA_EXECUTION_GATEWAY_AGENT_ID": "smoke-agent"}))
        self.write(Path("/etc/heptatrader/hepta-tool-gateway.env"), example(
            "hepta-tool-gateway.env.example", {
                "HEPTA_EXECUTION_SERVICE_UID": str(execution.pw_uid),
                "HEPTA_TOOL_AGENT_UID": str(agent.pw_uid), "HEPTA_TOOL_AGENT_ID": "smoke-agent",
                "HEPTA_EXECUTION_DOMAIN_ID": "SIM:smoke-agent", "HEPTA_TOOL_ALLOW_TRADE": "1",
                "HEPTA_TOOL_SESSION_TEMPLATES": "watch,paper", "HEPTA_TOOL_MAX_ORDER_QTY": "100",
                "HEPTA_TOOL_MAX_TRADE_CALLS_PER_MIN": "60", "HEPTA_TOOL_DECISION_LEASE_TTL_MS": "60000"}))
        for name in ("hepta-agent", "hepta-outsider"):
            self.mkdir(self.work / name, 0o700, name)
            self.write(self.work / name / "token", self.token.encode(), 0o600, name)
        self.write(self.work / "operator-token", self.token.encode(), 0o600)
        run(["systemd-tmpfiles", "--create", "/usr/lib/tmpfiles.d/heptatrader-agent-os.conf"])
        run(["systemd-analyze", "verify", *["/usr/lib/systemd/system/" + name for name in SERVICES + SOCKETS]])

    def properties(self, name: str) -> dict[str, str]:
        result = run(["systemctl", "show", name, "--property=ActiveState,SubState,MainPID,InvocationID,Result,ExecMainStatus,NRestarts"])
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    def journal(self, name: str, invocation: str | None = None) -> str:
        args = ["journalctl", "--no-pager", "-o", "cat", "-n", "500", "-u", name]
        if invocation:
            args.append("_SYSTEMD_INVOCATION_ID=" + invocation)
        return run(args, check=False).stdout.replace(self.token, "<fixture-token-redacted>")

    def start(self) -> None:
        self.phase += 1
        run(["systemctl", "start", *reversed(SERVICES)], timeout=60)
        for name, user, executable, marker in (
                (SERVICES[1], "hepta-exec", "hepta-executiond", "execution runtime ready"),
                (SERVICES[0], "hepta-gateway", "hepta-tool-gatewayd", "tool gateway ready mode=SIMULATOR")):
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                value = self.properties(name)
                text = self.journal(name, value.get("InvocationID"))
                if value.get("ActiveState") == "active" and marker in text:
                    if "degraded" in text or value.get("NRestarts") != "0":
                        raise RuntimeError(f"unexpected degraded/restarted {name}: {text}")
                    break
                if value.get("ActiveState") == "failed":
                    raise RuntimeError(f"{name} failed: {text}")
                time.sleep(0.1)
            else:
                raise RuntimeError(f"{name} readiness failed: {text}")
            pid = int(value["MainPID"])
            actual = Path(f"/proc/{pid}/exe").resolve()
            if actual != Path("/usr/bin") / executable:
                raise RuntimeError(f"not executing installed daemon: {actual}")
            status = dict(line.split(":", 1) for line in Path(f"/proc/{pid}/status").read_text().splitlines() if ":" in line)
            if set(map(int, status["Uid"].split())) != {self.accounts[user].pw_uid}:
                raise RuntimeError("service UID mismatch")
            if int(status["CapEff"], 16) != 0 or status["NoNewPrivs"].strip() != "1":
                raise RuntimeError("daemon capability/no-new-privileges boundary not active")
            if os.readlink(f"/proc/{pid}/ns/net") == os.readlink("/proc/self/ns/net"):
                raise RuntimeError("PrivateNetwork isolation not active")
            self.processes.append({"unit": name, "pid": pid, "uid": self.accounts[user].pw_uid,
                "executable_sha256": hashlib.sha256(actual.read_bytes()).hexdigest(),
                "phase": self.phase, "private_network": True, "effective_capabilities": 0})

    def stop(self, all_units: bool = False) -> None:
        if not self.units_installed:
            return
        run(["systemctl", "stop", *(SERVICES + SOCKETS if all_units else SERVICES)], timeout=60)
        for name in SERVICES:
            value = self.properties(name)
            self.retained[f"phase-{self.phase}-{name}.log"] = self.journal(name)
            if value.get("ActiveState") != "inactive" or value.get("Result") != "success" or value.get("ExecMainStatus") != "0":
                raise RuntimeError(f"unclean service stop: {name}: {value}")

    def provision(self) -> None:
        result = run(["/usr/bin/hepta-sessionctl", "--socket", "/run/hepta-tool-gateway/session-supervisor.sock",
            "provision", "--template", "paper", "--token-file", str(self.work / "operator-token"),
            "--agent-id", "smoke-agent", "--session-id", "smoke-session", "--peer-uid",
            str(self.accounts["hepta-agent"].pw_uid), "--ttl-sec", "3600"])
        if json.loads(result.stdout).get("accepted") is not True:
            raise RuntimeError("session provisioning was not accepted")

    def call(self, tool: str, fields: list[str] | None = None, command: str | None = None,
             *, user: str = "hepta-agent", expected: str = "ok") -> dict:
        account = self.accounts[user]
        result = run(["/usr/bin/heptactl", "--socket", "/run/hepta-agent/tools.sock", "--token-file",
            str(self.work / user / "token"), "--call-id", command or f"systemd-read-{time.monotonic_ns()}",
            "call", tool, *(fields or [])], uid=account.pw_uid, gid=account.pw_gid, check=False)
        if expected == "denied":
            if result.returncode == 0:
                raise RuntimeError("wrong UID successfully used a copied token")
            return {}
        value = json.loads(result.stdout)
        expected_exit = 7 if expected == "duplicate" else 0
        if result.returncode != expected_exit or value.get("status") != expected or value.get("tool") != tool:
            raise RuntimeError(f"unexpected tool result: {tool}: {value}: {result.stderr}")
        return value

    def mcp(self, tool: str, arguments: dict) -> dict:
        account = self.accounts["hepta-agent"]
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
        result = run(["/usr/bin/python3", "/usr/libexec/heptatrader/hepta_mcp_server.py"],
            uid=account.pw_uid, gid=account.pw_gid, data=json.dumps(request)+"\n",
            env={**ENV, "HEPTA_TOOL_SOCKET": "/run/hepta-agent/tools.sock",
                 "HEPTA_TOOL_SESSION_TOKEN_FILE": str(self.work / "hepta-agent/token"),
                 "HEPTA_TOOL_EXPECTED_UID": str(account.pw_uid)})
        response = json.loads(result.stdout)
        if response.get("id") != 1 or "error" in response or response["result"]["isError"]:
            raise RuntimeError(f"MCP rejected: {response}")
        return response["result"]["structuredContent"]

    def place(self, side: str, quantity: int, price: str) -> tuple[str, list[str], int]:
        fields = ["instrument=EUR.USD", "symbol=EUR", "currency=USD", "sec_type=CASH", "exchange=IDEALPRO",
                  f"side={side}", "order_type=LMT", "tif=DAY", f"quantity={quantity}", f"limit_price={price}",
                  "reference_price=1.1001", f"expires_at_ms={int(time.time()*1000)+60000}"]
        preview = self.call("risk.preview_order", fields)["payload"]
        if preview.get("approved") is not True or preview.get("single_use") is not True:
            raise RuntimeError("preview did not grant a single-use simulator permit")
        exact = fields + ["preview_permit=" + preview["preview_permit"]]
        command = preview["command_id"]
        return command, exact, self.call("trade.place_order", exact, command)["order_id"]

    def settle(self, quantity: int) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            positions = self.call("portfolio.list_positions")["payload"]["positions"]
            orders = self.call("orders.list")["payload"]["active_order_ids"]
            if sum(item["quantity"] for item in positions) == quantity and not orders:
                return
            time.sleep(0.05)
        raise RuntimeError(f"authoritative settlement failed: {positions}, {orders}")

    def lifecycle(self) -> dict:
        self.start(); self.provision()
        quote = self.call("market.get_quote", ["instrument=EUR.USD"])["payload"]
        other = self.mcp("market.get_quote", {"instrument": "EUR.USD"})["payload"]
        if not quote.get("authoritative") or (quote["bid"], quote["ask"]) != (other["bid"], other["ask"]):
            raise RuntimeError("native/MCP authoritative quote disagreement")
        self.call("market.get_quote", ["instrument=EUR.USD"], user="hepta-outsider", expected="denied")
        command, exact, order = self.place("BUY", 100, "1.1002")
        self.settle(100)
        self.call("trade.place_order", exact, command, expected="duplicate")
        _, _, pending = self.place("SELL", 25, "1.1003")
        self.mcp("trade.cancel_order", {"order_id": pending, "command_id": "systemd-cancel-1"})
        self.settle(100)
        self.stop(); self.start()  # No re-provision: actual encrypted lease and journal replay.
        self.settle(100)
        prior = self.call("execution.get_command_status", ["command_id=" + command])["payload"]
        if prior.get("command_status") != "accepted" or prior.get("order_id") != order:
            raise RuntimeError("command identity lost across manager restart")
        self.place("SELL", 100, "1.1000"); self.settle(0)
        records = [json.loads(line) for line in Path("/var/lib/hepta-execution/oms-journal.jsonl").read_text().splitlines()]
        sends = sum(item.get("event") == "place_sent" for item in records)
        if sends != 3:
            raise RuntimeError(f"duplicate or missing mutation: {sends} sends, expected 3")
        self.stop(all_units=True)
        for socket in ("/run/hepta-agent/tools.sock", "/run/hepta-tool-gateway/session-supervisor.sock",
                       "/run/hepta-execution/execution.sock", "/run/hepta-execution/events.sock"):
            if os.path.lexists(socket):
                raise RuntimeError(f"socket not removed after clean stop: {socket}")
        return {"final_position": 0, "final_active_orders": [], "place_sent_records": sends,
                "processes": self.processes, "systemd_manager_exercised": True}

    def cleanup(self) -> None:
        errors = []
        if self.units_installed:
            try:
                self.stop(all_units=True)
            except Exception as error:
                errors.append(str(error))
            for name in SERVICES:
                self.retained[f"final-{name}.log"] = self.journal(name)
        for name, content in self.retained.items():
            (self.evidence / name).write_text(content)
        # Never unlink a pre-existing or substituted static input.
        for path, identity in reversed(self.files):
            if os.path.lexists(path):
                if self.identity(path) != identity:
                    errors.append(f"static file substituted, refusing cleanup: {path}"); continue
                path.unlink()
        for path, identity in reversed(self.private):
            if os.path.lexists(path):
                if self.identity(path) != identity:
                    errors.append(f"state directory substituted, refusing cleanup: {path}"); continue
                shutil.rmtree(path)
        for path, identity in reversed(self.directories):
            if os.path.lexists(path):
                if self.identity(path) != identity:
                    errors.append(f"directory substituted, refusing cleanup: {path}"); continue
                try:
                    path.rmdir()
                except OSError as error:
                    errors.append(str(error))
        if self.units_installed:
            run(["systemctl", "daemon-reload"])
        for name, account in reversed(self.accounts.items()):
            if pwd.getpwnam(name).pw_uid != account.pw_uid:
                errors.append(f"account replaced, refusing cleanup: {name}"); continue
            result = run(["userdel", name], check=False)
            if result.returncode:
                errors.append(f"user cleanup failed: {name}: {result.stderr}")
        if errors:
            raise RuntimeError("; ".join(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        host_preconditions()
        args.evidence_dir.mkdir(parents=True, mode=0o755, exist_ok=False)
        with tempfile.TemporaryDirectory(prefix="hepta-systemd-", dir="/tmp") as directory:
            work = Path(directory); work.chmod(0o755)
            slot, manifest = admitted_slot(args.artifact.absolute(), args.expected_sha256, work / "candidate")
            fixture = SystemdFixture(work, args.evidence_dir)
            try:
                fixture.install(slot, manifest); fixture.configure()
                result = fixture.lifecycle()
            finally:
                fixture.cleanup()
            record = {"schema": "heptatrader.systemd-simulator-smoke.v1", "result": "PASS",
                "scope": "disposable-host;exact-core-artifact;simulator-only", "authorization_effect": "NONE",
                "broker_mutation": False, "paper_authorized": False, "live_authorized": False,
                "artifact_sha256": args.expected_sha256, "source_sha": manifest["source_sha"],
                "systemd_version": run(["systemctl", "--version"]).stdout.splitlines()[0],
                "installed_units": {item["path"]: item["sha256"] for item in manifest["files"] if "/systemd/system/" in item["path"]},
                **result}
            with (args.evidence_dir / "systemd-simulator.json").open("x") as stream:
                json.dump(record, stream, sort_keys=True, indent=2); stream.write("\n")
            print(json.dumps(record, sort_keys=True))
        return 0
    except Exception as error:
        print(f"[SYSTEMD-SMOKE] FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
