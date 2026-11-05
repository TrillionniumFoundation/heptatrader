#!/usr/bin/env python3
"""Synthetic stdin-only checks. Pass the already-built offline CLI path."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

binary = Path(sys.argv[1]).resolve()
quote = json.dumps
header = ("HEPTA_FX_DIAGNOSTIC_V1\n" + " ".join(map(quote, [
    "agent.tool:synthetic", "agent:synthetic", "SYNTHETIC", "PAPER"
])) + " 100 3 1\n" + " ".join(map(quote, ["SYNTHETIC", "EUR.USD", "EUR"]))
    + " 0 0 0 1000\n")

def event(kind, ts, source, qty=2, price=0, status="", callback="", remaining=0, epoch=""):
    fields = [quote(kind), str(ts), "1", quote(source), quote("session"),
        quote("SYNTHETIC"), quote("PAPER"), quote("agent-native"), quote("EUR.USD"),
        quote("BUY"), quote("synthetic-correlation"), quote("synthetic-command"),
        quote(epoch), "1", "3", quote(""), str(qty), str(price), quote(status),
        quote(callback), str(remaining)]
    return " ".join(fields) + "\n"

pending = (header + "3\n" + event("place_sent", 2000, "agent.tool:synthetic")
    + event("broker_order_accepted", 1999, "agent:synthetic", epoch="prior-service")
    + event("broker_order_status", 2001, "agent:synthetic", 0, 0,
            "PreSubmitted", "orderStatus", 2, "prior-service"))
cases = [("complete", header + "0\n", 0, []), ("pending", pending, 3, []),
    ("trailing", pending + "bad", 2, []), ("oversize", "x" * (1024 * 1024 + 1), 2, []),
    ("extra_argument", pending, 2, ["/no/such/file"])]
for name, data, expected, args in cases:
    result = subprocess.run([str(binary), *args], input=data, capture_output=True,
                            text=True, timeout=10)
    assert result.returncode == expected, (name, result.returncode, result.stderr)
    report = json.loads(result.stdout)
    assert report["qualification"] is False and report["effect_authority"] == "NONE"
    if name == "pending":
        assert report["pending_count"] == 1
        assert report["input_sha256"] == hashlib.sha256(data.encode()).hexdigest()
print(json.dumps({"schema": "hepta.offline_diagnostic_cli_test.v1", "cases": 5,
                  "passed": 5, "synthetic_only": True, "qualification": False}))
