#!/usr/bin/env python3
"""Opt-in, broker-free test of the research client against installed processes.

Reuse the canonical installed-runtime fixture and artifact admission. This is
not a second simulator, not a PID1 test, and not broker qualification. Run only
on a disposable Linux runner as root; an existing host interlock is an error.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/python"))
from test_installed_runtime_processes import (  # noqa: E402
    InstalledRuntime, admitted_slot, AGENT_UID, OTHER_UID, TEST_GID, HOST_INTERLOCK,
)

CHILD = r'''
import json,sys,time
from hepta_research.gateway import HeptactlTransport,Outbox,StrategyGateway,LimitIntent
root,prefix,mode=sys.argv[1:]
base=HeptactlTransport(prefix+"/bin/heptactl",root+"/g/tool.sock",root+"/agent/token")
class DropResponse:
    def scope(self):return base.scope()
    def call(self,tool,call_id,fields):
        answer=base.call(tool,call_id,fields)
        if tool=="trade.place_order":raise TimeoutError("injected loss AFTER real service response")
        return answer
client=StrategyGateway(DropResponse() if mode=="lost" else base,Outbox(root+"/agent/outbox"))
start=time.monotonic_ns()
if mode=="lost":
    intent=LimitIntent("EUR.USD","EUR","CASH","IDEALPRO","USD","BUY","10","1.1002","1.1001",time.time_ns()//1000000+60000)
    record=client.prepare("lost-response",intent)
    try:client.submit("lost-response")
    except RuntimeError:pass
    else:raise AssertionError("response loss was not observed")
    result={"status":"uncertain","command_id":record["command_id"]}
elif mode=="query":
    answer=client.submit("lost-response")
    assert answer["status"]=="ok",answer
    result={"status":answer["status"],"tool":answer["tool"]}
elif mode=="oversize":
    intent=LimitIntent("EUR.USD","EUR","CASH","IDEALPRO","USD","BUY","1000","1.1002","1.1001",time.time_ns()//1000000+60000)
    try:client.prepare("oversize",intent)
    except ValueError:result={"status":"rejected"}
    else:raise AssertionError("oversized intent was approved")
elif mode=="close":
    intent=LimitIntent("EUR.USD","EUR","CASH","IDEALPRO","USD","SELL","10","1.1000","1.1001",time.time_ns()//1000000+60000)
    client.prepare("close",intent)
    answer=client.submit("close")
    assert answer["status"]=="ok",answer
    result={"status":answer["status"]}
else:raise AssertionError("unknown mode")
result["client_round_trip_ms"]=(time.monotonic_ns()-start)/1000000
print(json.dumps(result,sort_keys=True))
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("requires an explicitly disposable root Linux runner")
    if args.report.exists():
        raise RuntimeError("report must not already exist")
    # Never adopt, remove, or temporarily rename an existing host installation.
    HOST_INTERLOCK.mkdir(mode=0o711)
    HOST_INTERLOCK.chmod(0o711)
    identity = (HOST_INTERLOCK.stat().st_dev, HOST_INTERLOCK.stat().st_ino)
    lock = HOST_INTERLOCK / "session-lease-terminal-cleanup.lock"
    lock_identity = None
    try:
        lock.touch(mode=0o644, exist_ok=False)
        lock.chmod(0o644)
        lock_identity = (lock.stat().st_dev, lock.stat().st_ino)
        with tempfile.TemporaryDirectory(prefix="hepta-research-process-", dir="/tmp") as name:
            work = Path(name)
            work.chmod(0o755)
            slot, manifest = admitted_slot(args.artifact.resolve(), args.sha256, work / "candidate")
            if manifest["source_sha"] != args.source_sha:
                raise RuntimeError("artifact source does not match the exact requested revision")
            package = work / "python"
            shutil.copytree(ROOT / "research/python", package)
            for path in [package, *package.rglob("*")]:
                path.chmod(0o755 if path.is_dir() else 0o644)
            runtime = InstalledRuntime(work / "runtime")
            observations = []
            def child(mode):
                result = subprocess.run(
                    [sys.executable, "-B", "-c", CHILD, str(runtime.root), str(slot), mode],
                    env={"PATH":"/usr/bin:/bin", "LC_ALL":"C.UTF-8", "PYTHONPATH":str(package)},
                    user=AGENT_UID, group=TEST_GID, extra_groups=[], stdin=subprocess.DEVNULL,
                    capture_output=True, text=True, timeout=30, check=False)
                if result.returncode:
                    raise RuntimeError(f"client mode {mode} failed: {result.stderr}")
                value = json.loads(result.stdout)
                observations.append(dict(mode=mode, **value))
                return value
            try:
                runtime.start(slot)
                runtime.provision()
                runtime.wait_position(0)
                runtime.call("market.get_quote", ["instrument=EUR.USD"], uid=OTHER_UID, reject=True)
                child("lost")
                runtime.wait_position(10)
                assert runtime.send_count() == 1, "expected one real simulator send"
                child("query") # distinct client process after response loss
                assert runtime.send_count() == 1, "client restart duplicated a placement"
                child("oversize")
                assert runtime.send_count() == 1, "risk rejection reached venue send"
                runtime.stop()
                runtime.start(slot) # preserved service journal/lease; no new provision
                child("query")
                assert runtime.send_count() == 1, "service restart duplicated a placement"
                child("close")
                runtime.wait_position(0)
                runtime.wait_no_orders()
                assert runtime.send_count() == 2
                runtime.stop() # no PASS before clean shutdown
                report = {
                    "schema":"heptatrader.research-process-test.v1", "result":"PASS",
                    "artifact_sha256":args.sha256, "source_sha":args.source_sha,
                    "scope":"installed-multi-uid-simulator-only", "systemd_manager_exercised":False,
                    "broker_mutation":False, "paper_authorized":False, "live_authorized":False,
                    "place_sent_records":2, "final_position":0, "final_active_orders":[],
                    "processes":runtime.observed_processes, "observations":observations,
                    "latency_scope":"single cold CLI round trips; not a latency SLO or broker measurement",
                }
                with args.report.open("x") as stream:
                    json.dump(report, stream, indent=2, sort_keys=True)
                    stream.write("\n")
                print("PASS installed research client, uncertain recovery, service restart, risk and UID boundaries")
            finally:
                runtime.stop()
    finally:
        for path, expected in ((lock, lock_identity), (HOST_INTERLOCK, identity)):
            if expected is None:
                continue
            actual = path.lstat()
            if (actual.st_dev, actual.st_ino) != expected or actual.st_uid != 0:
                raise RuntimeError("interlock identity changed; refusing cleanup")
        if lock_identity is not None:
            lock.unlink()
        HOST_INTERLOCK.rmdir()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
