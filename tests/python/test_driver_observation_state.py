"""Loop observations cannot replace durable original-command recovery state."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from hepta_decision_attribution import DRIVER_OBSERVATION_FIELDS, merge_driver_observation


class DriverObservationStateTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "isolated_driver_observation", ROOT / "scripts/heptatrader_paper_driver.py")
        self.driver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.driver)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "state.json"
        self.observation = self.path.with_suffix(".observation.json")
        self.state = self.driver.load_state(self.path)
        self.driver.atomic_json(self.path, self.state)

    def read_observed(self):
        raw = self.path.read_bytes()
        return merge_driver_observation(self.path, json.loads(raw), hashlib.sha256(raw).hexdigest())

    def tick(self, iteration=1):
        self.state["resident_loop_progress"] = {
            "schema": self.driver.RESIDENT_LOOP_PROGRESS_SCHEMA,
            **self.driver.resident_process_identity(), "iteration": iteration, "at_ms": 1000 + iteration}
        self.state["loop_observation"] = {
            "schema": self.driver.LOOP_OBSERVATION_SCHEMA,
            "iteration": iteration, "checked_at_ms": 1000 + iteration, "phase": "OBSERVING"}
        self.state["history"] = [{"observed_at_ms": 1000 + iteration, "bid": 1.1, "ask": 1.1001}]
        self.driver.checkpoint_loop_state(self.path, self.state)

    def test_idle_ticks_only_replace_bound_observation_without_fsync(self):
        self.assertEqual(self.driver.OBSERVATION_STATE_FIELDS, DRIVER_OBSERVATION_FIELDS)
        original = self.path.read_bytes()
        identity = self.driver.state_file_identity(self.path)
        with patch.object(self.driver.os, "fsync", wraps=os.fsync) as sync:
            for iteration in range(1, 7):
                self.tick(iteration)
            self.assertEqual(sync.call_count, 0)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.driver.state_file_identity(self.path), identity)
        self.assertEqual(self.read_observed(), self.state)
        # Restart recovery deliberately ignores a fresh diagnostic/history sidecar.
        self.assertEqual(self.driver.load_state(self.path), json.loads(original))

    def test_every_business_transition_remains_durable(self):
        transitions = [
            {"pending": {"command_id": "original", "send_state": "PREPARED"}},
            {"pending": {"command_id": "original", "send_state": "POSSIBLY_SENT"}},
            {"halt_new_entries": True}, {"unknown_future_business_field": "preserve"},
        ]
        for change in transitions:
            with self.subTest(change=change):
                self.state.update(change)
                with patch.object(self.driver.os, "fsync", wraps=os.fsync) as sync:
                    self.tick()
                    self.assertEqual(sync.call_count, 2)
                for key, value in change.items():
                    self.assertEqual(self.driver.load_state(self.path)[key], value)
                self.assertEqual(self.read_observed(), self.state)

    def test_observation_failure_falls_back_to_existing_durable_write(self):
        self.state["last_error"] = "diagnostic"
        with patch.object(self.driver, "publish_loop_observation", side_effect=OSError("test")), \
                patch.object(self.driver.os, "fsync", wraps=os.fsync) as sync:
            self.driver.checkpoint_loop_state(self.path, self.state)
            self.assertEqual(sync.call_count, 2)
        self.assertEqual(self.driver.load_state(self.path), self.state)

    def test_later_critical_write_invalidates_previous_observation(self):
        self.tick()
        self.state["pending"] = {"command_id": "original", "send_state": "POSSIBLY_SENT"}
        self.driver.atomic_json(self.path, self.state)
        self.assertEqual(self.read_observed(), self.state)
        self.assertNotEqual(json.loads(self.observation.read_text())["state_sha256"],
                            hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_replaced_durable_file_invalidates_writer_cache(self):
        changed = copy.deepcopy(self.state)
        changed["last_error"] = "older diagnostic in a replacement inode"
        replacement = self.path.with_suffix(".replacement")
        replacement.write_text(json.dumps(changed))
        replacement.chmod(0o600)
        replacement.replace(self.path)
        with patch.object(self.driver.os, "fsync", wraps=os.fsync) as sync:
            self.tick()
            self.assertEqual(sync.call_count, 2)
        self.assertIsNone(self.driver.load_state(self.path)["pending"])

    def test_sidecar_cannot_override_pending_or_other_business_fields(self):
        self.tick()
        valid = json.loads(self.observation.read_text())
        original = json.loads(self.path.read_bytes())
        for key in ("pending", "position", "halt_new_entries", "lease_generation", "entries"):
            with self.subTest(key=key):
                bad = copy.deepcopy(valid)
                bad["fields"][key] = "untrusted"
                self.observation.write_text(json.dumps(bad))
                self.assertEqual(self.read_observed(), original)
        for key, value in (("state_sha256", "0" * 64), ("instrument", "BTC.USD"),
                           ("schema", "unbound"), ("extra", True)):
            bad = copy.deepcopy(valid)
            bad[key] = value
            self.observation.write_text(json.dumps(bad))
            self.assertEqual(self.read_observed(), original)

    def test_optional_nonregular_symlink_and_corrupt_sidecars_are_ignored(self):
        original = json.loads(self.path.read_bytes())
        os.mkfifo(self.observation, 0o600)
        self.assertEqual(self.read_observed(), original)
        self.observation.unlink()
        self.observation.symlink_to(self.path)
        self.assertEqual(self.read_observed(), original)
        self.observation.unlink()
        self.observation.write_text('{"schema":1,"schema":2}')
        self.observation.chmod(0o600)
        self.assertEqual(self.read_observed(), original)

    def test_durable_sync_failure_does_not_publish_new_observation(self):
        self.tick()
        previous = self.observation.read_bytes()
        self.state["pending"] = {"command_id": "original", "send_state": "POSSIBLY_SENT"}
        with patch.object(self.driver.os, "fsync", side_effect=OSError("test sync failure")):
            with self.assertRaises(OSError):
                self.driver.checkpoint_loop_state(self.path, self.state)
        self.assertEqual(self.observation.read_bytes(), previous)
        self.assertIsNone(self.driver.load_state(self.path)["pending"])


if __name__ == "__main__":
    unittest.main()
