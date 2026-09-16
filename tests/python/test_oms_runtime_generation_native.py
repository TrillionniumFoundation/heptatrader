from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import hepta_oms_checkpoint as lifecycle


def event(kind: str, command: str, request_hash: str, *, status: str = "",
          order_id: int = -1, risk_code: str = "", correlation: str = "") -> dict:
    return {
        "schema_version": 4, "event": kind, "ts_ms": 1000, "order_id": order_id,
        "req_id": command, "client_req_id": command, "trace_id": "session-a",
        "event_id": f"{kind}:{command}:{status}", "risk_code": risk_code,
        "venue": "SIM", "strategy": "fixture", "account": "SIM-1",
        "execution_domain": "SIM", "request_hash": request_hash,
        "venue_correlation_id": correlation, "broker_callback_type": "",
        "broker_service_epoch": "", "broker_connection_epoch": 0,
        "broker_request_id": 0, "broker_error_code": 0, "broker_message": "",
        "broker_advanced_order_reject_json": "", "broker_why_held": "",
        "broker_execution_id": "", "broker_remaining_quantity": 0.0,
        "broker_market_cap_price": 0.0, "instrument": "EUR.USD", "side": "BUY",
        "qty": 10.0, "price": 1.1, "status": status, "reason": risk_code,
        "source": "agent.tool:agent-a",
    }


CPP = r'''
#include "HeptaTrade/execution/oms_runtime_generation.h"
#include <iostream>
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    OmsRuntimeGenerationReader reader(argv[1]);
    OmsRuntimeGenerationView view;
    std::string reason;
    if (!reader.Open(view, reason)) { std::cout << "ERROR " << reason << "\n"; return 3; }
    OmsRuntimeHistoricalCommand found;
    const auto old = reader.Lookup(view, "agent-a", "session-a", "old-command", found, reason);
    if (old != OmsRuntimeHistoricalLookup::Found) { std::cout << "LOOKUP " << reason << "\n"; return 4; }
    if (found.requestHash != "hash-old" || found.orderId != 101 || found.status != ExecutionCommandStatus::Accepted)
        return 5;
    OmsRuntimeHistoricalCommand missing;
    if (reader.Lookup(view, "agent-a", "session-a", "never-seen", missing, reason) != OmsRuntimeHistoricalLookup::Missing)
        return 6;
    std::cout << "PASS " << view.generation << " " << view.cutSequence << " " << view.lastSequence << "\n";
    return 0;
}
'''


class NativeOmsRuntimeGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compile_dir = tempfile.TemporaryDirectory(prefix="hepta-native-generation-build-")
        cls.addClassCleanup(cls.compile_dir.cleanup)
        root = Path(cls.compile_dir.name)
        source = root / "reader.cpp"
        cls.binary = root / "reader"
        source.write_text(CPP, encoding="utf-8")
        compiler = shlex.split(os.environ.get("CXX", "g++"))
        subprocess.run(
            compiler + ["-std=c++11", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT),
                        str(source), "-lcrypto", "-o", str(cls.binary)],
            check=True, capture_output=True, text=True, timeout=60)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="hepta-native-generation-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        os.chmod(self.root, 0o700)
        self.journal = self.root / "oms.jsonl"
        self.store = self.root / "store"
        values = [
            event("order_intent", "old-command", "hash-old", correlation="corr-old"),
            event("place_send_attempt", "old-command", "hash-old", correlation="corr-old"),
            event("place_sent", "old-command", "hash-old", status="submitted", order_id=101,
                  correlation="corr-old"),
            event("order_owner_reconciled_terminal", "owner-terminal", "hash-owner",
                  status="terminal", order_id=101),
            event("order_intent", "uncertain-command", "hash-uncertain", correlation="corr-u"),
            event("place_send_attempt", "uncertain-command", "hash-uncertain", correlation="corr-u"),
        ]
        self.journal.write_text("".join(json.dumps(v, sort_keys=True, separators=(",", ":")) + "\n"
                                        for v in values), encoding="utf-8")
        os.chmod(self.journal, 0o600)
        lifecycle.build_generation(self.journal, self.store, stopped=True)

    def run_reader(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(self.binary), str(self.store)], capture_output=True, text=True, timeout=10)

    def test_python_generation_is_consumable_by_native_reader(self) -> None:
        result = self.run_reader()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith("PASS "))

    def test_pointer_split_brain_fails_closed_natively(self) -> None:
        runtime = self.store / "CURRENT.runtime"
        text = runtime.read_text(encoding="utf-8")
        runtime.write_text(text.replace("generation=", "generation=wrong-", 1), encoding="utf-8")
        os.chmod(runtime, 0o600)
        result = self.run_reader()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("POINTER_MISMATCH", result.stdout)

    def test_runtime_index_corruption_is_not_missing(self) -> None:
        generation = json.loads((self.store / "CURRENT").read_text(encoding="utf-8"))["generation"]
        index = self.store / "generations" / generation / "runtime-command-index.tsv"
        with index.open("ab") as stream:
            stream.write(b"corrupt\n")
        result = self.run_reader()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DIGEST_MISMATCH", result.stdout)


if __name__ == "__main__":
    unittest.main()
