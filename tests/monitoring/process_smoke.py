#!/usr/bin/env python3
"""Exercise real node_exporter -> Prometheus -> Alertmanager -> loopback webhook.

Telemetry source is a synthetic journald envelope passed to the real collector.
No remote receiver, host installation or Broker is contacted. Missing binaries
are failures, not skips. Production alert thresholds are never shortened.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stdout
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import re
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/python"))
from test_telemetry_collection import collect, envelope, sample

HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(url: str):
    with HTTP.open(url, timeout=2) as response:
        data = response.read(1 << 20)
        return json.loads(data) if response.headers.get("Content-Type", "").startswith("application/json") else data


def wait_until(predicate, what: str, seconds: float = 30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except (OSError, ValueError, KeyError, IndexError):
            pass
        time.sleep(0.2)
    raise AssertionError("timed out: " + what)


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def binary(root: Path, *names: str) -> Path:
    for name in names:
        candidate = root / "usr/bin" / name
        if candidate.is_file():
            return candidate.resolve()
    raise ValueError("missing monitoring test binary: " + names[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    bins = {
        "prometheus": binary(args.tools_root, "prometheus"),
        "promtool": binary(args.tools_root, "promtool"),
        "alertmanager": binary(args.tools_root, "prometheus-alertmanager", "alertmanager"),
        "node_exporter": binary(args.tools_root, "prometheus-node-exporter", "node_exporter"),
    }
    subprocess.run([str(bins["promtool"]), "test", "rules", "rules.test.yml"],
                   cwd=ROOT / "tests/monitoring", check=True, timeout=45)
    source_sha = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    if re.fullmatch(r"[0-9a-f]{40}", source_sha) is None:
        raise ValueError("exact source commit is required")
    started = time.monotonic()
    accepted = []
    rejected = []
    sink_lock = threading.Lock()

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            self.connection.settimeout(2)
            size = int(self.headers.get("Content-Length", "0"))
            if self.path != "/alerts" or not 0 < size <= 65536:
                self.send_error(400); return
            payload = json.loads(self.rfile.read(size))
            alerts = payload.get("alerts", [])
            relevant = [a for a in alerts if a.get("labels", {}).get("alertname") == "HeptaCollectorFailed"
                        and a.get("labels", {}).get("component") == "oms" and a.get("status") == "firing"]
            with sink_lock:
                if relevant and not rejected:
                    rejected.append(payload)
                    status = 503
                else:
                    accepted.append(payload)
                    status = 200
            self.send_response(status)
            self.end_headers()
            self.wfile.write(b"test receiver\n")

        def log_message(self, *args):
            pass

    def delivered(name, status="firing"):
        with sink_lock:
            return any(a.get("labels", {}).get("alertname") == name and
                       a.get("labels", {}).get("component") == "oms" and a.get("status") == status
                       for payload in accepted for a in payload.get("alerts", []))

    with tempfile.TemporaryDirectory(prefix="hepta-monitoring-") as tmp, ExitStack() as stack:
        root = Path(tmp)
        textfiles = root / "metrics"; textfiles.mkdir(mode=0o755)
        receiver = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
        receiver.daemon_threads = True
        thread = threading.Thread(target=receiver.serve_forever, daemon=True)
        thread.start()
        stack.callback(receiver.server_close)
        stack.callback(receiver.shutdown)
        p_prom, p_alert, p_node = port(), port(), port()
        alert_config = {
            "route": {"receiver": "fixture", "group_by": ["alertname", "component"],
                      "group_wait": "0s", "group_interval": "1s", "repeat_interval": "1h"},
            "receivers": [{"name": "fixture", "webhook_configs": [{
                "url": f"http://127.0.0.1:{receiver.server_port}/alerts", "send_resolved": True}]}],
        }
        prom_config = {
            "global": {"scrape_interval": "1s", "evaluation_interval": "1s"},
            "rule_files": [str(ROOT / "systemd/monitoring/hepta.rules.yml.example")],
            "scrape_configs": [{"job_name": "heptatrader", "static_configs": [{"targets": [f"127.0.0.1:{p_node}"]}]}],
            "alerting": {"alertmanagers": [{"api_version": "v2", "static_configs": [{"targets": [f"127.0.0.1:{p_alert}"]}]}]},
        }
        for name, value in (("prometheus", prom_config), ("alertmanager", alert_config)):
            (root / (name + ".json")).write_text(json.dumps(value))
        processes = []

        def stop(child):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait(timeout=3)

        def launch(name, args):
            log = stack.enter_context((root / (name + ".log")).open("wb"))
            child = subprocess.Popen([str(bins[name]), *args], stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=log,
                                     env={"PATH": "/usr/bin:/bin", "LANG": "C", "GOMAXPROCS": "2", "HOME": str(root)})
            stack.callback(stop, child)
            processes.append(child)

        def healthy():
            with redirect_stdout(io.StringIO()):
                result = collect.collect_profile(textfiles, "simulator", lambda unit:
                    envelope(sample("gateway" if "gateway" in unit else "oms"), unit))
            if result != 0:
                raise AssertionError("healthy source fixture was not collected")

        healthy()
        launch("node_exporter", [f"--web.listen-address=127.0.0.1:{p_node}", "--collector.disable-defaults",
                                 "--collector.textfile", f"--collector.textfile.directory={textfiles}"])
        launch("alertmanager", [f"--web.listen-address=127.0.0.1:{p_alert}", "--cluster.listen-address=",
                                f"--config.file={root}/alertmanager.json", f"--storage.path={root}/alerts"])
        launch("prometheus", [f"--web.listen-address=127.0.0.1:{p_prom}", f"--config.file={root}/prometheus.json",
                              f"--storage.tsdb.path={root}/tsdb", "--storage.tsdb.retention.time=2h"])
        def query(expression):
            if any(child.poll() is not None for child in processes):
                raise AssertionError("monitoring process exited before acceptance")
            return get(f"http://127.0.0.1:{p_prom}/api/v1/query?" + urllib.parse.urlencode({"query": expression}))["data"]["result"]
        wait_until(lambda: get(f"http://127.0.0.1:{p_alert}/-/ready"), "Alertmanager readiness")
        wait_until(lambda: query('hepta_oms_collector_success{job="heptatrader"}')[0]["value"][1] == "1", "actual textfile scrape")
        # A failure must replace the last success, fire the real rule and survive
        # one actual HTTP 503 before delivery can be counted as successful.
        if collect.collect_kind(textfiles, "oms", collect.PROFILES["simulator"]["oms"], lambda _: b'{broken}\n') != 2:
            raise AssertionError("malformed input was accepted")
        wait_until(lambda: delivered("HeptaCollectorFailed"), "firing webhook after receiver retry")
        if not rejected:
            raise AssertionError("notification retry was not exercised")
        healthy()
        wait_until(lambda: delivered("HeptaCollectorFailed", "resolved"), "resolved webhook")
        before = (textfiles / "hepta_oms.prom").read_bytes()
        # Stop updating, but leave a healthy old .prom file intact. No artificial
        # clock or weakened 30-second production threshold is used here.
        wait_until(lambda: delivered("HeptaCollectorStale"), "stopped collector notification", seconds=45)
        if before != (textfiles / "hepta_oms.prom").read_bytes():
            raise AssertionError("dead collector output was unexpectedly rewritten")
        record = {
            "schema": "heptatrader.monitoring-process-acceptance.v1", "result": "PASS",
            "scope": "synthetic-journal;real-collector-textfile-prometheus-alertmanager;loopback-receiver",
            "authorization_effect": "NONE", "paper_authorized": False, "live_authorized": False,
            "source_sha": source_sha,
            "elapsed_seconds": time.monotonic() - started,
            "checks": ["exact-production-promtool-rules", "actual-textfile-scrape", "failed-input-firing",
                       "receiver-503-retry", "resolved-delivery", "stopped-collector-with-unchanged-file"],
            "rule_sha256": hashlib.sha256((ROOT / "systemd/monitoring/hepta.rules.yml.example").read_bytes()).hexdigest(),
            "binaries": {key: hashlib.sha256(value.read_bytes()).hexdigest() for key, value in bins.items()},
            "receiver_rejected_attempts": len(rejected), "receiver_accepted_requests": len(accepted),
        }
        # No PASS file exists unless all assertions and deliveries above succeeded.
        with args.output.open("x") as stream:
            json.dump(record, stream, indent=2); stream.write("\n")
        print(json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
