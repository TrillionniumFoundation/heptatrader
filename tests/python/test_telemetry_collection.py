"""Real collector/parser/publisher behavior; synthetic journal envelopes, no Broker."""
from contextlib import redirect_stdout
import fcntl
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import hepta_telemetry_collect as collect
import hepta_oms_report as metrics


def sample(kind="oms", now=None):
    now = time.time_ns() // 1000000 if now is None else now
    common = {"schema": metrics.SCHEMA if kind == "oms" else metrics.GATEWAY_SCHEMA,
              "authorization_effect": "NONE", "service_epoch": "fixture-epoch",
              "observed_at_ms": now, "monotonic_ms": 10000}
    if kind == "oms":
        common.update(known=True, write_poisoned=False, max_bytes=1000, max_records=100,
                      bytes=100, records=4, byte_headroom=900, record_headroom=96,
                      pending_records=0, queue_depth=0, buffered_depth=0, status="OK")
    else:
        common.update({key: 0 for key in metrics.GATEWAY_COUNTERS + metrics.GATEWAY_GAUGES})
        common.update(max_pending_connections=128, metrics_saturated=False, results=[0] * 7)
        for name in metrics.GATEWAY_LATENCIES:
            common[name] = {"samples": 0, "total_ns": 0, "max_ns": 0, "last_ns": 0, "saturated": False}
    return common


def envelope(value, unit=None, invocation="1" * 32):
    if unit is None:
        unit = collect.PROFILES["simulator"]["gateway" if value.get("schema") == metrics.GATEWAY_SCHEMA else "oms"]
    message = value if isinstance(value, str) else json.dumps(value)
    return (json.dumps({"_SYSTEMD_UNIT": unit, "_BOOT_ID": "a" * 32,
                       "_SYSTEMD_INVOCATION_ID": invocation, "MESSAGE": message}) + "\n").encode()


class TelemetryCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hepta-collect-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.unit = collect.PROFILES["simulator"]["oms"]

    def test_actual_atomic_publication_for_both_producers(self):
        def reader(unit):
            value = sample("gateway" if "gateway" in unit else "oms")
            value["extra_secret"] = "do-not-publish-this"
            return envelope(value, unit)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(collect.collect_profile(self.directory, "simulator", reader), 0)
        for kind in ("oms", "gateway"):
            text = (self.directory / f"hepta_{kind}.prom").read_text()
            self.assertIn(f"hepta_{kind}_collector_success 1", text)
            self.assertIn(f"hepta_{kind}_sample_timestamp_seconds ", text)
            self.assertNotIn("do-not-publish-this", text + output.getvalue())

    def test_one_failed_source_does_not_hide_or_skip_the_other(self):
        collect.collect_kind(self.directory, "oms", self.unit, lambda _: envelope(sample()))
        def reader(unit):
            if unit == self.unit:
                raise TimeoutError("private command output")
            return envelope(sample("gateway"), unit)
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(collect.collect_profile(self.directory, "simulator", reader), 2)
        self.assertNotIn("private", output.getvalue())
        self.assertIn("hepta_oms_collector_success 0", (self.directory / "hepta_oms.prom").read_text())
        self.assertNotIn("hepta_oms_bytes", (self.directory / "hepta_oms.prom").read_text())
        self.assertIn("hepta_gateway_collector_success 1", (self.directory / "hepta_gateway.prom").read_text())

    def test_unit_is_bound_to_trusted_journal_metadata(self):
        data = envelope(sample(), "unrelated.service")
        with self.assertRaises(ValueError):
            collect.journal_samples(data, self.unit, "oms")
        obj = json.loads(envelope(sample()))
        obj["UNIT"] = obj.pop("_SYSTEMD_UNIT")
        with self.assertRaises(ValueError):
            collect.journal_samples((json.dumps(obj) + "\n").encode(), self.unit, "oms")

    def test_new_invocation_without_telemetry_invalidates_old_health(self):
        data = envelope(sample()) + envelope("starting", self.unit, "2" * 32)
        with self.assertRaises(ValueError):
            collect.journal_samples(data, self.unit, "oms")
        new = sample(); new["service_epoch"] = "new-epoch"
        values = collect.journal_samples(data + envelope(new, self.unit, "2" * 32), self.unit, "oms")
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0]["service_epoch"], "new-epoch")

    def test_multiple_epochs_in_same_invocation_are_rejected(self):
        new = sample(); new["service_epoch"] = "other"
        with self.assertRaises(ValueError):
            collect.journal_samples(envelope(sample()) + envelope(new), self.unit, "oms")

    def test_identity_and_json_fail_closed_without_valid_prefix(self):
        bad = json.loads(envelope(sample())); bad.pop("_SYSTEMD_INVOCATION_ID")
        for tail in (b'{bad}\n', b'{"x":1,"x":2}\n', b'\xff\n', (json.dumps(bad) + "\n").encode()):
            with self.subTest(tail=tail[:20]), self.assertRaises((ValueError, UnicodeError)):
                collect.journal_samples(envelope(sample()) + tail, self.unit, "oms")

    def test_missing_and_torn_output_are_not_healthy(self):
        for data in (b"", envelope(sample())[:-1]):
            with self.assertRaises(ValueError):
                collect.journal_samples(data, self.unit, "oms")

    def test_snapshot_and_row_limits_are_enforced(self):
        with self.assertRaises(ValueError):
            collect.journal_samples(b"x" * (collect.MAX_BYTES + 1), self.unit, "oms")
        with self.assertRaises(ValueError):
            collect.journal_samples(b"x" * collect.MAX_ROW_BYTES + b"\n", self.unit, "oms")
        noise = b'{}\n'
        self.assertEqual(len(collect.journal_samples(noise * (collect.MAX_ROWS - 1) + envelope(sample()), self.unit, "oms")), 1)
        with self.assertRaises(ValueError):
            collect.journal_samples(noise * collect.MAX_ROWS + envelope(sample()), self.unit, "oms")

    def test_retained_window_is_bounded(self):
        values = collect.journal_samples(envelope(sample()) * 200, self.unit, "oms")
        self.assertEqual(len(values), 120)

    def test_slow_collection_uses_completion_time_for_freshness(self):
        value = sample(now=1000)
        with patch.object(collect.time, "time_ns", return_value=20000 * 1000000):
            self.assertEqual(collect.collect_kind(self.directory, "oms", self.unit, lambda _: envelope(value)), 1)
        text = (self.directory / "hepta_oms.prom").read_text()
        self.assertIn("hepta_oms_telemetry_fresh 0", text)
        self.assertIn("hepta_oms_collector_timestamp_seconds 20.000", text)

    def test_collection_lock_is_held_before_reader(self):
        with collect.collection_lock(self.directory):
            with patch.object(collect, "read_journal") as reader, self.assertRaises(BlockingIOError):
                collect.collect_profile(self.directory, "simulator", reader)
            reader.assert_not_called()

    def test_bad_namespace_and_fifo_lock_fail_without_collecting(self):
        fifo = self.directory / ".hepta_collection.lock"
        os.mkfifo(fifo)
        with self.assertRaises((OSError, ValueError)):
            with collect.collection_lock(self.directory):
                self.fail("entered with FIFO lock")
        with self.assertRaises(ValueError):
            collect.collect_profile(Path("relative"), "simulator", lambda _: self.fail("read called"))

    def test_fixed_journalctl_command_has_no_shell_or_foreign_unit(self):
        with patch.object(collect, "bounded_command", return_value=b"") as runner:
            collect.read_journal(self.unit)
            argv = runner.call_args.args[0]
            self.assertEqual(argv[0], "/usr/bin/journalctl")
            self.assertIn("--boot=0", argv)
            self.assertIn("--unit=" + self.unit, argv)
            with self.assertRaises(ValueError):
                collect.read_journal("*.service")
            self.assertEqual(runner.call_count, 1)

    def test_actual_child_success_and_exact_byte_limit(self):
        argv = [sys.executable, "-c", "import os;os.write(1,b'1234')"]
        self.assertEqual(collect.bounded_command(argv, max_bytes=4), b"1234")
        with self.assertRaises(ValueError):
            collect.bounded_command(argv, max_bytes=3)

    def test_actual_timeout_does_not_accept_partial_output(self):
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            collect.bounded_command([sys.executable, "-c", "import os,time;os.write(1,b'valid-prefix');time.sleep(10)"], timeout=0.1)
        self.assertLess(time.monotonic() - started, 3)

    def test_actual_failed_process_is_not_success(self):
        with self.assertRaises(ValueError):
            collect.bounded_command([sys.executable, "-c", "import os;os.write(1,b'valid-prefix');os.write(2,b'secret');raise SystemExit(1)"])

    def test_invalid_profile_and_pair_have_no_side_effects(self):
        with self.assertRaises(ValueError):
            collect.collect_profile(self.directory, "LIVE", lambda _: self.fail("reader invoked"))
        with self.assertRaises(ValueError):
            collect.collect_kind(self.directory, "gateway", self.unit, lambda _: self.fail("reader invoked"))
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_directory_substitution_during_collection_is_not_published(self):
        directory = self.directory / "metrics"; directory.mkdir(mode=0o700)
        changed = False
        def reader(unit):
            nonlocal changed
            if not changed:
                directory.rename(self.directory / "retained")
                directory.mkdir(mode=0o700)
                changed = True
            return envelope(sample("gateway" if "gateway" in unit else "oms"), unit)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(collect.collect_profile(directory, "simulator", reader), 2)
        self.assertEqual(list(directory.iterdir()), [])

    def test_cli_rejects_fake_clock_before_publication(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/hepta_telemetry_collect.py"),
                                 "--profile", "simulator", "--output-dir", str(self.directory), "--now-ms", "1000"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(list(self.directory.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
