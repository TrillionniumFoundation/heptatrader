"""Idle provenance health has a bounded cadence; business evidence bypasses it."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import hepta_decision_attribution as attribution


class AttributionHealthCadenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hepta-health-cadence-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = {"instrument": "EUR.USD", "period_sha256": "a" * 64}
        with mock.patch.object(attribution.threading, "Thread"):
            self.journal = attribution.Journal(self.root / "journal", self.manifest, "b" * 64)
        self.clock = 1000.0
        clock_patch = mock.patch.object(attribution.time, "monotonic", side_effect=lambda: self.clock)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.path = self.journal.root / (self.journal.id + ".health.json")

    def health(self):
        return json.loads(self.path.read_text())

    def test_idle_half_second_polls_publish_once_per_ten_seconds(self):
        with mock.patch.object(attribution.os, "replace", wraps=attribution.os.replace) as replace:
            self.assertTrue(self.journal._health())
            for _ in range(19):
                self.clock += .5
                self.assertFalse(self.journal._health())
            self.assertEqual(replace.call_count, 1)
            self.clock += .5
            self.assertTrue(self.journal._health())
            self.assertEqual(replace.call_count, 2)

    def test_record_drop_and_error_changes_publish_without_waiting(self):
        self.journal._health()
        for attribute, value, key in (("count", 1, "written_records"),
                                       ("dropped", 1, "dropped"),
                                       ("error", "OSError:fixture", "error")):
            with self.subTest(attribute=attribute):
                setattr(self.journal, attribute, value)
                self.assertTrue(self.journal._health())
                self.assertEqual(self.health()[key], value)
                self.assertFalse(self.journal._health())

    def test_force_and_clock_regression_publish_even_without_business_change(self):
        self.journal._health()
        self.assertTrue(self.journal._health(force=True))
        self.clock -= 1
        self.assertTrue(self.journal._health())

    def test_failed_health_replace_is_not_cached_as_success(self):
        self.journal._health()
        self.journal.dropped += 1
        with mock.patch.object(attribution.os, "replace", side_effect=OSError("fixture")):
            with self.assertRaises(OSError):
                self.journal._health()
        self.assertTrue(self.journal._health())
        self.assertEqual(self.health()["dropped"], 1)


class AttributionWriterHealthTests(unittest.TestCase):
    def test_business_write_precedes_queue_completion_and_close_forces_health(self):
        with tempfile.TemporaryDirectory(prefix="hepta-health-writer-") as directory:
            manifest = {"instrument": "EUR.USD", "period_sha256": "a" * 64}
            journal = attribution.Journal(Path(directory) / "journal", manifest, "b" * 64)
            try:
                self.assertTrue(journal.emit("REQUEST", {"fixture": True}))
                journal.q.join()
                path = journal.root / (journal.id + ".health.json")
                health = json.loads(path.read_text())
                self.assertEqual(health["written_records"], 2)
                with mock.patch.object(journal, "_health", wraps=journal._health) as update:
                    journal.close()
                self.assertIn(mock.call(force=True), update.call_args_list)
                self.assertEqual(json.loads(path.read_text())["written_records"], 2)
            finally:
                journal.close()


if __name__ == "__main__":
    unittest.main()
