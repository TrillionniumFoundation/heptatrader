"""Old hash-pinned PAPER segments remain readable; new quotes stay strict."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from laya_cell.shadow_source import (
    SCHEMA, LEGACY_INCLUSIVE_EXPIRY_COLLECTOR_SHA256 as OLD_COLLECTOR,
    canonical, digest, parse_native_quote_reply, read_source,
)


class OriginalBoundaryTests(unittest.TestCase):
    def quote(self, anchor):
        return {
            "instrument": "EUR.USD", "source": "IB",
            "authoritative": True, "stale": False,
            "bid": "1.1", "ask": "1.2",
            "observed_at_ms": anchor,
            "stale_after_ms": anchor + 8000,
            "subscription_id": "test-sub",
            "subscription_state": "active",
        }

    def archived_source(self, path, collector, extra_delay_ms=0):
        observed = int(time.time() * 1000) - 8000
        header = {
            "source": "IB_TOOL_GATEWAY", "execution_domain": "PAPER",
            "instrument": "EUR.USD", "write_operations_allowed": [],
            "broker_orders_sent": 0, "period_sha256": "a" * 64,
            "collector_sha256": collector,
        }
        previous = "0" * 64
        lines = []
        for i, (kind, at, data) in enumerate((
            ("HEADER", observed - 50, header),
            ("QUOTE", observed + 5000 + extra_delay_ms, self.quote(observed)),
        )):
            item = {
                "schema": SCHEMA, "sequence": i, "previous": previous,
                "at_ms": at, "kind": kind, "data": data,
            }
            item["sha256"] = digest(item)
            lines.append(canonical(item) + b"\n")
            previous = item["sha256"]
        path.write_bytes(b"".join(lines))
        path.chmod(0o600)
        return observed, previous

    def test_old_source_exact_expiry_equality_is_only_historical(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "quotes.jsonl"
            observed, head = self.archived_source(path, OLD_COLLECTOR)
            header, quotes, pin = read_source(path)
            self.assertEqual(header["collector_sha256"], OLD_COLLECTOR)
            self.assertEqual(len(quotes), 1)
            self.assertEqual(quotes[0]["t"], observed)
            self.assertEqual(pin["chain_head"], head)
            self.assertEqual(pin["uncommitted_tail_bytes"], 0)
            self.assertEqual(pin["path"], str(path))

    def test_new_source_cannot_inherit_old_inclusive_expiry(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "quotes.jsonl"
            observed, _ = self.archived_source(path, "f" * 64)
            with self.assertRaisesRegex(ValueError, "QUOTE_STALE_OR_FUTURE"):
                read_source(path)
            reply = json.dumps({
                "status": "ok",
                "payload": self.quote(observed),
            })
            with self.assertRaisesRegex(ValueError, "QUOTE_STALE_OR_FUTURE"):
                parse_native_quote_reply(
                    0, reply, observed + 5000, "EUR.USD")

    def test_even_legacy_collector_rejects_past_deadline(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "quotes.jsonl"
            self.archived_source(path, OLD_COLLECTOR, extra_delay_ms=1)
            with self.assertRaisesRegex(ValueError, "QUOTE_STALE_OR_FUTURE"):
                read_source(path)


if __name__ == "__main__":
    unittest.main()
