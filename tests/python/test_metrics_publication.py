"""Execute the real reporter/publisher; never require a Broker or live host."""
from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/hepta_oms_report.py"
spec = importlib.util.spec_from_file_location("metrics_publication_reporter", SCRIPT)
reporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reporter)
NOW = 1800000000000


def observation(now=NOW):
    return {"schema": reporter.SCHEMA, "authorization_effect": "NONE", "known": True,
            "write_poisoned": False, "observed_at_ms": now, "max_bytes": 1000,
            "max_records": 100, "pending_records": 0, "queue_depth": 0,
            "buffered_depth": 0, "status": "OK", "bytes": 10, "records": 1,
            "byte_headroom": 990, "record_headroom": 99, "service_epoch": "test-epoch",
            "monotonic_ms": 1000}


class MetricsPublicationTests(unittest.TestCase):
    def setUp(self):
        # Honor the operator-selected scratch filesystem; retain real sync and
        # namespace checks rather than mocking durability to suit a host.
        self.scratch = tempfile.TemporaryDirectory(prefix="hepta-metrics-")
        self.root = Path(self.scratch.name)
        self.output = self.root / "metrics"
        self.output.mkdir(mode=0o755)
        self.source = self.root / "sample.jsonl"
        self.source.write_text(json.dumps(observation()) + "\n")
        self.target = self.output / "hepta_oms.prom"

    def tearDown(self):
        self.scratch.cleanup()

    def run_cli(self, *extra):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(reporter.time, "time", return_value=NOW / 1000), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = reporter.main(["--input", str(self.source), "--format", "prometheus",
                                    "--output-dir", str(self.output), *extra])
        return result, stdout.getvalue(), stderr.getvalue()

    def test_real_report_and_timestamp_publication(self):
        original = self.source.read_bytes()
        code, output, error = self.run_cli()
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(output, self.target.read_text())
        self.assertIn("hepta_oms_collector_success 1\n", output)
        self.assertIn("hepta_oms_collector_timestamp_seconds 1800000000.000\n", output)
        self.assertIn("hepta_oms_sample_timestamp_seconds 1800000000.000\n", output)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o644)
        self.assertEqual(self.source.read_bytes(), original)

    def test_malformed_input_replaces_old_healthy_series_without_secrets(self):
        self.assertEqual(self.run_cli()[0], 0)
        self.source.write_text('{"secret":"DO_NOT_PRINT",broken}\n')
        code, output, error = self.run_cli()
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        failed = self.target.read_text()
        self.assertIn("hepta_oms_collector_success 0\n", failed)
        self.assertIn("hepta_oms_telemetry_fresh 0\n", failed)
        self.assertNotIn("hepta_oms_bytes", failed)
        self.assertNotIn("sample_timestamp_seconds", failed)
        self.assertNotIn("DO_NOT_PRINT", failed + error)
        self.assertNotIn(str(self.source), error)

    def test_missing_input_publishes_explicit_collection_failure(self):
        self.source.unlink()
        self.assertEqual(self.run_cli()[0], 2)
        self.assertIn("collector_success 0\n", self.target.read_text())

    def test_stale_and_future_samples_are_not_healthy_collections(self):
        for timestamp in (NOW - 15001, NOW + 1):
            with self.subTest(timestamp=timestamp):
                self.source.write_text(json.dumps(observation(timestamp)) + "\n")
                code, _, _ = self.run_cli()
                self.assertEqual(code, 1)
                text = self.target.read_text()
                self.assertIn("collector_success 1\n", text)  # parse succeeded, health did not
                self.assertIn("telemetry_fresh 0\n", text)
                self.assertIn("sample_timestamp_seconds", text)

    def test_collector_stoppage_is_detectable_without_rewriting_fresh_flag(self):
        self.run_cli()
        lines = self.target.read_text().splitlines()
        stamp = float(next(x.split()[1] for x in lines if x.startswith("hepta_oms_collector_timestamp_seconds ")))
        self.assertGreater(NOW / 1000 + 31 - stamp, 30)
        # A scraper must use this timestamp or absence, not just a cached fresh=1.
        self.assertIn("hepta_oms_telemetry_fresh 1", lines)

    def test_gateway_has_an_independent_fixed_filename_and_failure_state(self):
        code, _, _ = self.run_cli("--kind", "gateway")  # OMS input has no Gateway sample
        self.assertEqual(code, 2)
        text = (self.output / "hepta_gateway.prom").read_text()
        self.assertIn("hepta_gateway_collector_success 0\n", text)
        self.assertFalse(self.target.exists())

    def test_concurrent_writer_fails_without_replacing_current_output(self):
        reporter.publish_metrics(self.output, "oms", "original 1\n")
        lock = os.open(self.output / ".hepta_oms.lock", os.O_RDWR)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                reporter.publish_metrics(self.output, "oms", "replacement 2\n")
        finally:
            os.close(lock)
        self.assertEqual(self.target.read_text(), "original 1\n")

    def test_file_sync_failure_preserves_old_bytes_and_cleans_only_temporary(self):
        reporter.publish_metrics(self.output, "oms", "original 1\n")
        with mock.patch.object(reporter.os, "fsync", side_effect=OSError("injected EIO")):
            with self.assertRaises(OSError):
                reporter.publish_metrics(self.output, "oms", "replacement 2\n")
        self.assertEqual(self.target.read_text(), "original 1\n")
        self.assertEqual(list(self.output.glob("*.tmp")), [])
        self.assertTrue((self.output / ".hepta_oms.lock").is_file())

    def test_directory_sync_failure_does_not_pretend_old_bytes_survived(self):
        reporter.publish_metrics(self.output, "oms", "original 1\n")
        fsync = reporter.os.fsync
        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("injected directory sync failure")
            return fsync(fd)
        with mock.patch.object(reporter.os, "fsync", side_effect=fail_directory):
            with self.assertRaises(OSError):
                reporter.publish_metrics(self.output, "oms", "replacement 2\n")
        self.assertEqual(self.target.read_text(), "replacement 2\n")

    def test_output_symlink_hardlink_and_fifo_are_not_overwritten(self):
        victim = self.root / "victim"
        victim.write_text("private content")
        victim.chmod(0o644)
        for kind in ("symlink", "hardlink", "fifo"):
            with self.subTest(kind=kind):
                if kind == "symlink": self.target.symlink_to(victim)
                elif kind == "hardlink": os.link(victim, self.target)
                else: os.mkfifo(self.target)
                with self.assertRaises(ValueError):
                    reporter.publish_metrics(self.output, "oms", "replacement 2\n")
                self.assertEqual(victim.read_text(), "private content")
                self.target.unlink()

    def test_lock_fifo_is_rejected_by_real_timeout_bounded_process(self):
        os.mkfifo(self.output / ".hepta_oms.lock", 0o600)
        result = subprocess.run([sys.executable, str(SCRIPT), "--input", str(self.source),
                                 "--format", "prometheus", "--output-dir", str(self.output)],
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertIn("METRICS_PUBLICATION_FAILED", result.stderr)
        self.assertFalse(self.target.exists())

    def test_directory_link_and_writable_parent_are_rejected(self):
        link = self.root / "link"
        link.symlink_to(self.output, target_is_directory=True)
        with self.assertRaises(OSError): reporter.publish_metrics(link, "oms", "x 1\n")
        self.root.chmod(0o777)
        try:
            with self.assertRaises(ValueError): reporter.publish_metrics(self.output, "oms", "x 1\n")
        finally:
            self.root.chmod(0o700)
        self.assertFalse(self.target.exists())

    def test_directory_substitution_is_rejected_before_replace(self):
        old = self.root / "old"
        fsync = reporter.os.fsync
        exchanged = False
        def exchange(fd):
            nonlocal exchanged
            result = fsync(fd)
            if not exchanged:
                exchanged = True
                self.output.rename(old)
                self.output.mkdir(mode=0o755)
            return result
        with mock.patch.object(reporter.os, "fsync", side_effect=exchange):
            with self.assertRaises(ValueError): reporter.publish_metrics(self.output, "oms", "x 1\n")
        self.assertFalse(self.target.exists())
        self.assertFalse((old / "hepta_oms.prom").exists())

    def test_target_substitution_during_write_is_not_overwritten(self):
        reporter.publish_metrics(self.output, "oms", "original 1\n")
        fsync = reporter.os.fsync
        def exchange(fd):
            result = fsync(fd)
            self.target.write_text("competitor 7\n")
            return result
        with mock.patch.object(reporter.os, "fsync", side_effect=exchange):
            with self.assertRaises(ValueError): reporter.publish_metrics(self.output, "oms", "replacement 2\n")
        self.assertEqual(self.target.read_text(), "competitor 7\n")

    def test_output_mode_is_explicit_under_restrictive_umask(self):
        mask = os.umask(0o077)
        try:
            reporter.publish_metrics(self.output, "oms", "x 1\n")
        finally:
            os.umask(mask)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o644)

    def test_bounds_kind_and_relative_directory_fail_before_writes(self):
        for kind, text in (("../escape", "x 1\n"), ("oms", ""), ("oms", "x"), ("oms", "x" * (1 << 20) + "\n")):
            with self.subTest(kind=kind, size=len(text)):
                with self.assertRaises(ValueError): reporter.publish_metrics(self.output, kind, text)
        with self.assertRaises(ValueError): reporter.publish_metrics(Path("relative"), "oms", "x 1\n")
        self.assertEqual(list(self.output.iterdir()), [])

    def test_publication_rejects_fake_clock_without_side_effects(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            reporter.main(["--input", str(self.source), "--output-dir", str(self.output),
                           "--format", "prometheus", "--now-ms", str(NOW)])
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_publication_failure_returns_nonzero_and_prints_no_payload(self):
        with mock.patch.object(reporter, "publish_metrics", side_effect=OSError("private-path")):
            code, output, error = self.run_cli()
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("METRICS_PUBLICATION_FAILED", error)
        self.assertNotIn("private-path", error)


if __name__ == "__main__":
    unittest.main()
