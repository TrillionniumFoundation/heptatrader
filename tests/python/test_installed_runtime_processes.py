"""Opt-in installed, multi-UID, broker-free process and exact-revision-pair tests.

Only run on a disposable Linux host with HEPTA_ISOLATED_PROCESS_TESTS=1.
The test refuses an existing /run/hepta-agent; it never adopts host state.
Artifacts MUST be digest-pinned core packages, not arbitrary binary directories.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Sequence
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import run_release_simulator_smoke as release_smoke

GATEWAY_UID, EXECUTION_UID, AGENT_UID, OTHER_UID, TEST_GID = 61001, 61002, 61003, 61004, 61000
CLEAN_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
HOST_INTERLOCK = Path("/run/hepta-agent")
# The child creates listeners AFTER dropping UID. SO_PEERCRED therefore names
# the actual service, not an arbitrary root parent impersonating systemd PID 1.
ACTIVATE = """
import fcntl, json, os, socket, sys
fds = []
for path in json.loads(sys.argv[2]):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    os.chmod(path, 0o660)
    listener.listen(64)
    fds.append(fcntl.fcntl(listener.fileno(), fcntl.F_DUPFD_CLOEXEC, 100))
    listener.close()
for index, fd in enumerate(fds):
    os.dup2(fd, 3 + index, inheritable=True)
    os.close(fd)
os.environ['LISTEN_PID'] = str(os.getpid())
os.environ['LISTEN_FDS'] = str(len(fds))
os.execve(sys.argv[1], [sys.argv[1]], os.environ)
"""


def canonical_digest(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def admitted_slot(artifact: Path, digest: str, work: Path) -> tuple[Path, dict]:
    if not canonical_digest(digest):
        raise ValueError("an explicit canonical artifact SHA-256 is required")
    work.mkdir(mode=0o755)
    snapshot = release_smoke._snapshot_artifact(artifact, work / "input/release.tar.gz", digest)
    release_smoke._run_preflight(ROOT / "scripts/hepta_preflight.py", snapshot, digest,
                                 ROOT / "docs/preflight-policy-v1.json", "core")
    slot = work / "slot"
    release_smoke._extract_release(snapshot, slot)
    manifest = json.loads((slot / "manifest.json").read_text())
    metadata = json.loads((slot / "share/heptatrader/heptatrader-build-info.json").read_text())
    if any(metadata.get(field) is not False for field in
           ("ib_api_compiled", "paper_authorized", "live_authorized")):
        raise ValueError("process tests admit broker-disabled core artifacts only")
    if (slot / "bin/hepta-ib-executiond").exists():
        raise ValueError("broker-enabled binary is forbidden in process test slot")
    slot.chmod(0o755)  # root-owned read/execute only to the isolated service UIDs
    return slot, manifest


class InstalledRuntime:
    """One private persistent simulator/gateway state; binaries may change."""
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(mode=0o755)
        self.processes: list[tuple[subprocess.Popen, object, Path, str]] = []
        self.observed_processes: list[dict] = []
        self.serial = 0
        self.prefix: Path | None = None
        for name, uid, mode in (("e", EXECUTION_UID, 0o755), ("g", GATEWAY_UID, 0o755),
                                ("es", EXECUTION_UID, 0o700), ("gs", GATEWAY_UID, 0o700),
                                ("cred", EXECUTION_UID, 0o700), ("agent", AGENT_UID, 0o700),
                                ("other", OTHER_UID, 0o700)):
            path = root / name
            path.mkdir()
            path.chmod(mode)
            os.chown(path, uid, TEST_GID)
        self._write("cred/hepta-execution-fence", "HFC1\nfencing_token=1\ngeneration=1\n", EXECUTION_UID, 0o400)
        self._write("gs/key", "K" * 32, GATEWAY_UID, 0o400)
        # Disposable fixture secrets only. Identical bytes in separate private
        # files let us distinguish credential possession from OS-UID authority.
        self._write("operator-token", "S" * 32, 0, 0o600)
        self._write("agent/token", "S" * 32, AGENT_UID, 0o600)
        self._write("other/token", "S" * 32, OTHER_UID, 0o600)

    def _write(self, name: str, value: str, uid: int, mode: int) -> None:
        path = self.root / name
        with path.open("x") as stream:
            stream.write(value)
        path.chmod(mode)
        os.chown(path, uid, TEST_GID)

    def _spawn(self, name: str, uid: int, sockets: list[str], names: str,
               settings: dict[str, str], ready: str) -> None:
        self.serial += 1
        log = self.root / f"{self.serial}-{name}.log"
        output = log.open("x")
        paths = [self.root / p for p in sockets]
        for path in paths:
            if os.path.lexists(path):
                if not stat.S_ISSOCK(path.lstat().st_mode):
                    output.close()
                    raise AssertionError("refusing to replace a non-socket fixture path")
                path.unlink()  # only after our prior processes have terminated
        executable = self.prefix / "bin" / name
        process = subprocess.Popen(
            [sys.executable, "-c", ACTIVATE, str(executable), json.dumps(list(map(str, paths)))],
            env={**CLEAN_ENV, **settings, "LISTEN_FDNAMES": names},
            user=uid, group=TEST_GID, extra_groups=[], stdin=subprocess.DEVNULL,
            stdout=output, stderr=output, start_new_session=True,
        )
        self.processes.append((process, output, log, name))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            text = log.read_text()
            if ready in text:
                if "degraded" in text:
                    raise AssertionError(f"unexpected degraded startup: {text}")
                # Root in user namespaces may lack CAP_SYS_PTRACE for another
                # UID; inspect procfs as the service's own UID instead.
                observed = subprocess.run(
                    ["/usr/bin/readlink", f"/proc/{process.pid}/exe"],
                    user=uid, group=TEST_GID, extra_groups=[], env=CLEAN_ENV,
                    capture_output=True, text=True, timeout=5, check=True)
                actual = Path(observed.stdout.strip())
                if actual != executable.resolve():
                    raise AssertionError(f"not executing installed binary: {actual}")
                self.observed_processes.append({
                    "name": name, "pid": process.pid, "uid": uid,
                    "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                })
                return
            if process.poll() is not None:
                raise AssertionError(f"{name} failed startup: {text}")
            time.sleep(0.025)
        raise AssertionError(f"{name} readiness deadline exceeded: {log.read_text()}")

    def start(self, prefix: Path) -> None:
        if self.processes:
            raise AssertionError("must stop the old revision before replacing it")
        self.prefix = prefix
        p = lambda name: str(self.root / name)
        self._spawn("hepta-executiond", EXECUTION_UID, ["e/execution.sock", "e/events.sock"],
                    "execution:events", {
                        "HEPTA_EXECUTION_SERVICE_MODE": "SIMULATOR",
                        "HEPTA_EXECUTION_GATEWAY_UID": str(GATEWAY_UID),
                        "HEPTA_EXECUTION_GATEWAY_AGENT_ID": "smoke-agent",
                        "STATE_DIRECTORY": p("es"), "CREDENTIALS_DIRECTORY": p("cred"),
                    }, "execution runtime ready")
        self._spawn("hepta-tool-gatewayd", GATEWAY_UID, ["g/tool.sock", "g/supervisor.sock"],
                    "hepta-tool:hepta-supervisor", {
                        "HEPTA_TOOL_SOCKET": p("g/tool.sock"),
                        "HEPTA_TOOL_SUPERVISOR_SOCKET": p("g/supervisor.sock"),
                        "HEPTA_TOOL_SUPERVISOR_LEASE_STORE": p("gs/leases"),
                        "HEPTA_TOOL_SUPERVISOR_LEASE_KEY_FILE": p("gs/key"),
                        "HEPTA_TOOL_SUPERVISOR_AUDIT_JOURNAL": p("gs/audit"),
                        "HEPTA_TOOL_SUPERVISOR_UID": "0", "HEPTA_TOOL_AGENT_UID": str(AGENT_UID),
                        "HEPTA_EXECUTION_REMOTE_MODE": "SIMULATOR",
                        "HEPTA_EXECUTION_SOCKET": p("e/execution.sock"),
                        "HEPTA_EXECUTION_EVENT_SOCKET": p("e/events.sock"),
                        "HEPTA_EXECUTION_SERVICE_UID": str(EXECUTION_UID),
                        "HEPTA_TOOL_ALLOW_TRADE": "1", "HEPTA_TOOL_ACCOUNT": "SIM",
                        "HEPTA_TOOL_AGENT_ID": "smoke-agent", "HEPTA_EXECUTION_DOMAIN_ID": "SIM:smoke-agent",
                        "HEPTA_TOOL_SESSION_TEMPLATES": "watch,paper",
                        "HEPTA_TOOL_CONTRACT_BINDINGS": "EUR.USD|EUR|CASH|IDEALPRO|USD",
                        "HEPTA_TOOL_MAX_ORDER_QTY": "100",
                        "HEPTA_TOOL_MAX_TRADE_CALLS_PER_MIN": "60",
                        "HEPTA_TOOL_DECISION_LEASE_TTL_MS": "60000",
                    }, "tool gateway ready mode=SIMULATOR")

    def stop(self) -> None:
        failures = []
        for process, output, log, name in reversed(self.processes):
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                    failures.append(f"{name} required SIGKILL")
            output.close()
            if process.returncode != 0:
                failures.append(f"{name} exit={process.returncode}: {log.read_text()[-2000:]}")
        self.processes.clear()
        if failures:
            raise AssertionError("; ".join(failures))

    def provision(self) -> None:
        result = subprocess.run([
            str(self.prefix / "bin/hepta-sessionctl"), "--socket", str(self.root / "g/supervisor.sock"),
            "provision", "--template", "paper", "--token-file", str(self.root / "operator-token"),
            "--agent-id", "smoke-agent", "--session-id", "smoke-session", "--peer-uid", str(AGENT_UID),
            "--ttl-sec", "3600"], env=CLEAN_ENV, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=10)
        if result.returncode or json.loads(result.stdout).get("accepted") is not True:
            raise AssertionError(f"session provision failed: {result.stdout} {result.stderr}")

    def call(self, tool: str, fields: Sequence[str] = (), *, call_id: str | None = None,
             uid: int = AGENT_UID, reject: bool = False, duplicate: bool = False) -> dict:
        self.serial += 1
        token = "agent/token" if uid == AGENT_UID else "other/token"
        result = subprocess.run([
            str(self.prefix / "bin/heptactl"), "--socket", str(self.root / "g/tool.sock"),
            "--token-file", str(self.root / token), "--call-id", call_id or f"process-read-{self.serial}",
            "call", tool, *fields], env=CLEAN_ENV, user=uid, group=TEST_GID, extra_groups=[],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
        if reject:
            if result.returncode == 0:
                raise AssertionError(f"unauthorized/rejected call succeeded: {tool}")
            return {"exit_code": result.returncode, "output": result.stdout, "error": result.stderr}
        if duplicate:
            value = json.loads(result.stdout)
            if result.returncode != 7 or value.get("status") != "duplicate":
                raise AssertionError(f"expected a typed duplicate: {value}")
            return value
        if result.returncode:
            raise AssertionError(f"{tool}: exit={result.returncode}: {result.stdout} {result.stderr}")
        value = json.loads(result.stdout)
        if value.get("status") != "ok" or value.get("tool") != tool:
            raise AssertionError(value)
        return value

    def mcp_call(self, tool: str, arguments: dict) -> dict:
        self.serial += 1
        request = {"jsonrpc": "2.0", "id": self.serial, "method": "tools/call",
                   "params": {"name": tool, "arguments": arguments}}
        executable = self.prefix / "libexec/heptatrader/hepta_mcp_server.py"
        result = subprocess.run([sys.executable, str(executable)],
            input=json.dumps(request) + "\n", text=True, capture_output=True, timeout=20,
            env={**CLEAN_ENV, "HEPTA_TOOL_SOCKET": str(self.root / "g/tool.sock"),
                 "HEPTA_TOOL_SESSION_TOKEN_FILE": str(self.root / "agent/token"),
                 "HEPTA_TOOL_EXPECTED_UID": str(AGENT_UID)},
            user=AGENT_UID, group=TEST_GID, extra_groups=[])
        if result.returncode:
            raise AssertionError(f"installed MCP failed: {result.stderr}")
        response = json.loads(result.stdout)
        if response.get("id") != self.serial or "error" in response:
            raise AssertionError(response)
        envelope = response["result"]["structuredContent"]
        if response["result"]["isError"] or envelope["tool"] != tool or envelope["status"] != "ok":
            raise AssertionError(response)
        return envelope

    def position(self) -> float:
        return sum(item["quantity"] for item in self.call("portfolio.list_positions")["payload"]["positions"])

    def wait_no_orders(self) -> None:
        deadline = time.monotonic() + 5
        observed = self.call("orders.list")["payload"]["active_order_ids"]
        while observed and time.monotonic() < deadline:
            time.sleep(0.025)
            observed = self.call("orders.list")["payload"]["active_order_ids"]
        if observed:
            raise AssertionError(f"cancel/fill did not settle: {observed}")

    def wait_position(self, expected: float) -> None:
        # Accepted transport response is not a fill acknowledgement. Poll only
        # authoritative reads; never repeat a mutation to wait for settlement.
        deadline = time.monotonic() + 5
        observed = self.position()
        while observed != expected and time.monotonic() < deadline:
            time.sleep(0.025)
            observed = self.position()
        if observed != expected:
            raise AssertionError(f"position did not settle: expected={expected}, observed={observed}")

    def place(self, side: str, quantity: int, price: str) -> tuple[str, list[str], int]:
        fields = ["instrument=EUR.USD", "symbol=EUR", "currency=USD", "sec_type=CASH", "exchange=IDEALPRO",
                  f"side={side}", "order_type=LMT", "tif=DAY", f"quantity={quantity}",
                  f"limit_price={price}", "reference_price=1.1001", f"expires_at_ms={int(time.time()*1000)+60000}"]
        preview = self.call("risk.preview_order", fields)["payload"]
        if preview.get("approved") is not True or preview.get("single_use") is not True:
            raise AssertionError(preview)
        command = preview["command_id"]
        exact = fields + ["preview_permit=" + preview["preview_permit"]]
        result = self.call("trade.place_order", exact, call_id=command)
        return command, exact, result["order_id"]

    def send_count(self) -> int:
        records = [json.loads(line) for line in (self.root / "es/oms-journal.jsonl").read_text().splitlines()]
        return sum(record.get("event") == "place_sent" for record in records)


class ProcessArtifactAdmissionTests(unittest.TestCase):
    def test_digest_is_explicit_and_canonical(self):
        for value in ("", "A"*64, "a"*63, "../" + "b"*61):
            self.assertFalse(canonical_digest(value))
        self.assertTrue(canonical_digest("a"*64))


@unittest.skipUnless(os.environ.get("HEPTA_ISOLATED_PROCESS_TESTS") == "1",
                     "requires explicitly isolated multi-UID process test host")
class InstalledRuntimeProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != "linux" or os.geteuid() != 0:
            raise RuntimeError("opted-in process tests require a disposable root Linux host")
        # Do not remove/reuse a production host interlock. Existing state is an
        # error, not permission to run privileged tests against that host.
        HOST_INTERLOCK.mkdir(mode=0o711)
        HOST_INTERLOCK.chmod(0o711)
        cls.lock = HOST_INTERLOCK / "session-lease-terminal-cleanup.lock"
        cls.lock.touch(mode=0o644, exist_ok=False)
        cls.lock.chmod(0o644)
        cls.lock_identity = (cls.lock.stat().st_dev, cls.lock.stat().st_ino)
        cls.host_identity = (HOST_INTERLOCK.stat().st_dev, HOST_INTERLOCK.stat().st_ino)
        cls.addClassCleanup(cls._remove_interlock)
        cls.evidence = None
        if os.environ.get("HEPTA_PROCESS_EVIDENCE_DIR"):
            cls.evidence = Path(os.environ["HEPTA_PROCESS_EVIDENCE_DIR"])
            cls.evidence.mkdir(mode=0o755, parents=True, exist_ok=False)
        cls.tmp = tempfile.TemporaryDirectory(prefix="ht-process-", dir="/tmp")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        cls.root.chmod(0o755)
        cls.slots = {}
        cls.manifests = {}
        for name in ("CANDIDATE", "PREVIOUS"):
            artifact = Path(os.environ[f"HEPTA_PROCESS_{name}_ARTIFACT"])
            digest = os.environ[f"HEPTA_PROCESS_{name}_SHA256"]
            cls.slots[name], cls.manifests[name] = admitted_slot(artifact, digest, cls.root / name.lower())
        if cls.manifests["CANDIDATE"]["source_sha"] == cls.manifests["PREVIOUS"]["source_sha"]:
            raise RuntimeError("same source revision is not cross-revision rollback evidence")
        if os.environ["HEPTA_PROCESS_CANDIDATE_SHA256"] == os.environ["HEPTA_PROCESS_PREVIOUS_SHA256"]:
            raise RuntimeError("identical artifacts cannot prove cross-revision rollback")

    @classmethod
    def _remove_interlock(cls):
        for path, expected in ((cls.lock, cls.lock_identity), (HOST_INTERLOCK, cls.host_identity)):
            observed = path.lstat()
            if (observed.st_dev, observed.st_ino) != expected or observed.st_uid != 0:
                raise RuntimeError("fixture interlock identity changed; refusing host cleanup")
        cls.lock.unlink()
        HOST_INTERLOCK.rmdir()

    def fixture(self, name: str) -> InstalledRuntime:
        runtime = InstalledRuntime(self.root / name)
        if self.evidence:
            def retain_logs():
                for log in runtime.root.glob("*.log"):
                    with (self.evidence / f"{name}-{log.name}").open("xb") as target:
                        target.write(log.read_bytes())
            self.addCleanup(retain_logs)
        self.addCleanup(runtime.stop)
        return runtime

    def record_success(self, name: str, runtime: InstalledRuntime, phases: list[str]) -> None:
        # Publish only after explicit clean process shutdown. A failed scenario
        # retains diagnostic logs but never receives a PASS record.
        runtime.stop()
        if self.evidence is None:
            return
        record = {
            "schema": "heptatrader.installed-process-test.v1", "result": "PASS",
            "scenario": name, "scope": "exact-source-revision-pair;simulator-only",
            "broker_mutation": False, "paper_authorized": False, "live_authorized": False,
            "systemd_manager_exercised": False,
            "artifacts": {key.lower(): {
                "sha256": os.environ[f"HEPTA_PROCESS_{key}_SHA256"],
                "source_sha": self.manifests[key]["source_sha"],
                "version": self.manifests[key]["version"],
            } for key in ("CANDIDATE", "PREVIOUS")},
            "phases": phases, "processes": runtime.observed_processes,
            "final_position": 0, "final_active_orders": [], "place_sent_records": runtime.send_count(),
        }
        with (self.evidence / f"{name}.json").open("x") as stream:
            json.dump(record, stream, sort_keys=True, indent=2)
            stream.write("\n")


    def test_installed_process_identity_permit_replay_cancel_and_restart(self):
        runtime = self.fixture("lifecycle")
        runtime.start(self.slots["CANDIDATE"])
        runtime.provision()
        self.assertTrue(runtime.call("market.get_quote", ["instrument=EUR.USD"])["payload"]["authoritative"])
        native_quote = runtime.call("market.get_quote", ["instrument=EUR.USD"])["payload"]
        python_quote = runtime.mcp_call("market.get_quote", {"instrument": "EUR.USD"})["payload"]
        self.assertEqual((native_quote["bid"], native_quote["ask"]), (python_quote["bid"], python_quote["ask"]))
        runtime.call("market.get_quote", ["instrument=EUR.USD"], uid=OTHER_UID, reject=True)
        runtime.wait_position(0)
        command, fields, order = runtime.place("BUY", 100, "1.1002")
        runtime.wait_position(100)
        sends = runtime.send_count()
        self.assertGreater(sends, 0)
        self.assertEqual(runtime.call("trade.place_order", fields, call_id=command, duplicate=True)["order_id"], order)
        self.assertEqual(runtime.send_count(), sends)
        _, _, pending = runtime.place("SELL", 25, "1.1003")
        self.assertIn(pending, runtime.call("orders.list")["payload"]["active_order_ids"])
        runtime.mcp_call("trade.cancel_order", {"order_id": pending, "command_id": "process-mcp-cancel-1"})
        runtime.wait_no_orders()
        runtime.stop()
        runtime.start(self.slots["CANDIDATE"])
        # No reprovision: the actual installed supervisor must recover the
        # same signed lease store as well as the execution journal.
        runtime.wait_position(100)
        status = runtime.call("execution.get_command_status", [f"command_id={command}"])["payload"]
        self.assertEqual(status["command_status"], "accepted")
        self.assertEqual(status["order_id"], order)
        runtime.place("SELL", 100, "1.1000")
        runtime.wait_position(0)
        runtime.wait_no_orders()
        self.record_success("installed-process-lifecycle", runtime,
                            ["provision", "native-and-mcp-quote-parity", "wrong-uid-rejected", "preview-and-fill", "duplicate-no-resend",
                             "mcp-cancel-settled", "restart-with-persisted-lease-and-journal", "final-flat"])

    def test_two_distinct_artifacts_upgrade_rollback_and_repromote_same_state(self):
        runtime = self.fixture("pair")
        runtime.start(self.slots["PREVIOUS"])
        runtime.provision()
        command, _, order = runtime.place("BUY", 100, "1.1002")
        runtime.wait_position(100)
        runtime.stop()
        runtime.start(self.slots["CANDIDATE"])
        runtime.wait_position(100)
        status = runtime.call("execution.get_command_status", [f"command_id={command}"])["payload"]
        self.assertEqual(status["order_id"], order)
        runtime.place("SELL", 25, "1.1000")
        runtime.wait_position(75)
        runtime.stop()
        runtime.start(self.slots["PREVIOUS"])
        runtime.wait_position(75)
        runtime.place("SELL", 75, "1.1000")
        runtime.wait_position(0)
        runtime.stop()
        runtime.start(self.slots["CANDIDATE"])
        runtime.wait_position(0)
        runtime.wait_no_orders()
        self.assertEqual(runtime.send_count(), 3)
        self.record_success("exact-pair-upgrade-rollback", runtime,
                            ["previous:100", "candidate:100-to-75", "previous:75-to-flat", "candidate:flat"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
