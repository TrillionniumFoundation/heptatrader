from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import hepta_oms_checkpoint as checkpoint
import hepta_oms_lifecycle as lifecycle
import hepta_oms_simulator_recovery as simulator


def event(kind: str, command: str, order_id: int, *, status: str = "",
          qty: float = 2.0, price: float = 1.1, ts_ms: int = 1000,
          venue: str = "SIMULATOR", account: str = "SIM") -> dict:
    return {
        "schema_version": 4, "event": kind, "ts_ms": ts_ms,
        "order_id": order_id, "req_id": command, "client_req_id": command,
        "trace_id": "session-a", "event_id": f"{kind}:{command}:{status}:{ts_ms}",
        "risk_code": "", "venue": venue, "strategy": "fixture",
        "account": account, "execution_domain": "SIM",
        "request_hash": f"hash-{command}",
        "venue_correlation_id": f"corr-{command}",
        "broker_callback_type": "", "broker_service_epoch": "",
        "broker_connection_epoch": 0, "broker_request_id": 0,
        "broker_error_code": 0, "broker_message": "",
        "broker_advanced_order_reject_json": "", "broker_why_held": "",
        "broker_execution_id": "", "broker_remaining_quantity": 0.0,
        "broker_market_cap_price": 0.0, "instrument": "EUR.USD",
        "side": "BUY", "qty": qty, "price": price, "status": status,
        "reason": "", "source": "agent.tool:agent-a",
    }


def encode(values: list[dict]) -> bytes:
    return b"".join((json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
                    for value in values)


def admitted(command: str, order_id: int, ts_ms: int, *, qty: float = 2.0) -> list[dict]:
    return [
        event("order_intent", command, -1, ts_ms=ts_ms, qty=qty),
        event("place_send_attempt", command, -1, ts_ms=ts_ms + 1, qty=qty),
        event("place_sent", command, order_id, status="submitted", ts_ms=ts_ms + 2, qty=qty),
    ]


class OmsSimulatorGenerationRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="hepta-sim-generation-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        os.chmod(self.root, 0o700)
        self.journal = self.root / "oms.jsonl"
        self.store = Path(str(self.journal) + ".generations")

    def write(self, values: list[dict]) -> None:
        self.journal.write_bytes(encode(values))
        os.chmod(self.journal, 0o600)

    def append(self, values: list[dict]) -> None:
        with self.journal.open("ab") as stream:
            stream.write(encode(values))

    def state(self) -> dict:
        generation = json.loads((self.store / "CURRENT").read_text())["generation"]
        result = simulator.checkpoint_state(self.store / generation)
        self.assertIsNotNone(result)
        return result

    def test_filled_position_and_order_watermark_survive_multiple_seals(self) -> None:
        values = admitted("one", 1_000_000, 1000, qty=2.0)
        values.append(event("status", "one", 1_000_000, status="Filled",
                            ts_ms=1003, qty=2.0, price=1.1))
        self.write(values)
        first = lifecycle.seal_generation(self.journal, self.store, stopped=True)
        self.assertEqual(first["history_records"], 4)
        state = self.state()
        self.assertEqual(state["admitted_order_count"], 1)
        self.assertEqual(state["next_order_id"], 1_000_001)
        self.assertEqual(state["positions"], {"EUR.USD": 2.0})
        self.assertEqual(state["active_orders"], {})
        self.assertTrue(lifecycle.verify_generation(self.store, journal=self.journal)["simulator_recovery"])

        self.append(admitted("two", 1_000_001, 2000, qty=3.0))
        lifecycle.seal_generation(self.journal, self.store, stopped=True)
        state = self.state()
        self.assertEqual(state["admitted_order_count"], 2)
        self.assertEqual(state["positions"], {"EUR.USD": 2.0})
        self.assertIn(1_000_001, state["active_orders"])
        self.assertEqual(state["next_order_id"], 1_000_002)

        self.append([event("status", "two", 1_000_001, status="Filled",
                           ts_ms=2003, qty=3.0, price=1.2)])
        lifecycle.seal_generation(self.journal, self.store, stopped=True)
        state = self.state()
        self.assertEqual(state["admitted_order_count"], 2)
        self.assertEqual(state["positions"], {"EUR.USD": 5.0})
        self.assertEqual(state["active_orders"], {})
        self.assertEqual(state["next_order_id"], 1_000_002)

    def test_non_simulator_records_do_not_corrupt_simulator_projection(self) -> None:
        values = admitted("paper", 101, 1000)
        for value in values:
            value["venue"] = "IB"
            value["account"] = "DU123"
            value["execution_domain"] = "PAPER"
        self.write(values)
        lifecycle.seal_generation(self.journal, self.store, stopped=True)
        state = self.state()
        self.assertEqual(state["admitted_order_count"], 0)
        self.assertEqual(state["positions"], {})
        self.assertEqual(state["active_orders"], {})
        self.assertEqual(state["next_order_id"], 1_000_000)

    def test_terminal_order_id_reuse_in_later_generation_fails_closed(self) -> None:
        values = admitted("one", 1_000_000, 1000)
        values.append(event("status", "one", 1_000_000, status="Filled", ts_ms=1003))
        self.write(values)
        lifecycle.seal_generation(self.journal, self.store, stopped=True)
        self.append([event("place_sent", "one", 1_000_000,
                           status="submitted", ts_ms=2000)])
        with self.assertRaisesRegex(checkpoint.GenerationError,
                                    "OMS_SIMULATOR_RECOVERY_ORDER_ID_REUSE"):
            lifecycle.seal_generation(self.journal, self.store, stopped=True)


if __name__ == "__main__":
    unittest.main()
