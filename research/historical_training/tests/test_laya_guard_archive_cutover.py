"""Adversarial immutable Laya archive-aware guard config migration tests."""
import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from historical_training.common import EvidenceError, sha, read
from historical_training.laya_guard_archive_cutover import (
    MIGRATED_FILES, EXTRA_PIN, SOURCE_PIN, source_pin, stage_cutover,
)


class GuardArchiveCutoverTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name)
        self.original = self.root / "config-old.json"
        self.guard = self.root / "guard-old.json"
        self.old = self.root / "old-runtime"
        self.new = self.root / "new-runtime"
        self.repo = self.root / "pinned-repo"
        self.old.mkdir(mode=0o700)
        self.new.mkdir(mode=0o700)
        self.repo.mkdir(mode=0o700)
        # 53 existing immutable paths, 51 preserved + 2 intentional changes.
        self.pins = {}
        for i in range(51):
            rel = f"learn/laya_cell/pinned_module_{i:02d}.py"
            self.put(self.old, rel, f"unchanged-{i}")
            self.put(self.new, rel, f"unchanged-{i}")
            self.pins[rel] = sha(self.old / rel)
        for rel in MIGRATED_FILES:
            self.put(self.old, rel, "old code\n")
            self.put(self.new, rel, "new code\n")
            self.pins[rel] = sha(self.old / rel)
        self.put(self.new, EXTRA_PIN, "new archive implementation\n")
        self.mapping = {
            "learn/laya_cell/dataset.py": "research/neuron_learning/laya_cell/dataset.py",
            "learn/laya_cell/resident_data.py": "research/neuron_learning/laya_cell/resident_data.py",
            EXTRA_PIN: "research/neuron_learning/laya_cell/source_archive.py",
        }
        for source, upstream in self.mapping.items():
            self.put(self.repo, upstream, (self.new / source).read_text())
        self.cfg = {
            "schema": "heptatrader.laya-resident-config.v1",
            "automatic_trading": False,
            "canonical_owner_endpoint": None,
            "period_sha256": "a" * 64,
            "training_cadence_ms": 900000,
            "runtime_directory": str(self.old),
            "runtime_sha256": self.pins,
            "promotion_protocol": "POWER_PLANNED_SIGN_SCREEN_V2",
            "decision_attribution_registrations": {"EUR.USD": []},
            "feed_directory": str(self.root / "never-access-originals"),
        }
        self.write(self.original, self.cfg)
        self.g = {
            "schema": "heptatrader.resident-admission-wrapper-config.v1",
            "trade_authority": False,
            "period_sha256": self.cfg["period_sha256"],
            "original_config": str(self.original),
            "original_config_sha256": sha(self.original),
            "max_heavy_workers": 1,
            "resource_rule": {"no_parallel_gpu": True},
            "guard_files_sha256": {},
            "source_health_path": str(self.root / "health"),
            "manual_hold_path": str(self.root / "manual-hold"),
        }
        self.write(self.guard, self.g)
        self.target = self.root / "new-staged"

    @staticmethod
    def put(root, rel, data):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)

    @staticmethod
    def write(path, doc):
        path.write_text(json.dumps(doc, sort_keys=True))
        path.chmod(0o600)

    def stage(self):
        with patch("historical_training.laya_guard_archive_cutover.source_pin",
                   return_value=self.repo):
            return stage_cutover(self.guard, self.original, self.new,
                                 self.repo, self.target)

    def test_preserves_fifty_one_paths_and_pins_exact_three(self):
        before = self.original.read_bytes()
        guard_before = self.guard.read_bytes()
        receipt = self.stage()
        self.assertEqual(receipt["exact_retained_pin_count"], 51)
        self.assertEqual(receipt["exact_changed_source_paths"], list(MIGRATED_FILES))
        self.assertEqual(receipt["added_source_archive_sha256"],
                         sha(self.new / EXTRA_PIN))
        self.assertEqual(self.original.read_bytes(), before)
        self.assertEqual(self.guard.read_bytes(), guard_before)
        c = read(self.target / "resident.json")
        g = read(self.target / "guard.json")
        self.assertEqual(g["original_config"], str(self.target / "resident.json"))
        self.assertEqual(g["original_config_sha256"], sha(self.target / "resident.json"))
        self.assertEqual(c["runtime_directory"], str(self.new))
        self.assertEqual(c["runtime_sha256"][EXTRA_PIN], sha(self.new / EXTRA_PIN))
        for name, expected in self.pins.items():
            if name not in MIGRATED_FILES:
                self.assertEqual(c["runtime_sha256"][name], expected)
        self.assertEqual(c["promotion_protocol"], self.cfg["promotion_protocol"])
        self.assertFalse(c["automatic_trading"])
        self.assertFalse(receipt["services_restarted"])
        self.assertEqual(receipt["broker_orders_sent"], 0)

    def test_old_guard_digest_conflict_fails_before_stage(self):
        self.g["original_config_sha256"] = "0" * 64
        self.write(self.guard, self.g)
        with self.assertRaisesRegex(EvidenceError, "ARCHIVE_OLD_GUARD_BINDING"):
            self.stage()
        self.assertFalse(self.target.exists())

    def test_old_original_runtime_changed_refused(self):
        self.put(self.old, next(iter(self.pins)), "tampered")
        with self.assertRaisesRegex(EvidenceError, "ARCHIVE_OLD_SOURCE_HASH_CHANGED"):
            self.stage()
        self.assertFalse(self.target.exists())

    def test_new_third_file_diff_refused(self):
        self.put(self.new, next(iter(self.pins)), "unexpected migration")
        with self.assertRaisesRegex(EvidenceError, "ARCHIVE_RELEASE_NOT_EXACT_TWO_FILE_MIGRATION"):
            self.stage()

    def test_new_archive_source_diverges_from_pinned_repo_refused(self):
        self.put(self.new, EXTRA_PIN, "modified archive source")
        with self.assertRaisesRegex(EvidenceError, "ARCHIVE_SOURCE_RUNTIME_NOT_IDENTICAL"):
            self.stage()
        self.assertFalse(self.target.exists())

    def test_new_archive_missing_refused(self):
        (self.new / EXTRA_PIN).unlink()
        with self.assertRaisesRegex(EvidenceError, "ARCHIVE_RUNTIME_REQUIRED_FILE_MISSING"):
            self.stage()

    def test_new_runtime_symlink_refused(self):
        target = self.root / "symlink-new"
        target.symlink_to(self.new, target_is_directory=True)
        with patch("historical_training.laya_guard_archive_cutover.source_pin",
                   return_value=self.repo):
            with self.assertRaisesRegex(EvidenceError, "ARCHIVE_RUNTIME_PATH_INVALID"):
                stage_cutover(self.guard, self.original, target, self.repo,
                              self.target)

    def test_unreviewed_trade_authority_refused(self):
        self.cfg["automatic_trading"] = True
        self.write(self.original, self.cfg)
        self.g["original_config_sha256"] = sha(self.original)
        self.write(self.guard, self.g)
        with self.assertRaisesRegex(EvidenceError, "ARCHIVE_ORIGINAL_POLICY"):
            self.stage()

    def test_second_stage_cannot_overwrite_or_relabel(self):
        self.stage()
        previous = (self.target / "resident.json").read_bytes()
        with self.assertRaisesRegex(EvidenceError, "ARCHIVE_STAGING_MUST_BE_NEW"):
            self.stage()
        self.assertEqual((self.target / "resident.json").read_bytes(), previous)

    def test_group_writable_release_rejected(self):
        self.new.chmod(0o775)
        with self.assertRaisesRegex(EvidenceError,
                                    "ARCHIVE_RUNTIME_PRIVATE_OWNER_REQUIRED"):
            self.stage()

    def test_unpinned_source_commit_refused(self):
        with patch("historical_training.laya_guard_archive_cutover.subprocess.run",
                   return_value=type("R", (), {"returncode": 0,
                                               "stdout": "wrong\n"})()):
            with self.assertRaisesRegex(EvidenceError, "ARCHIVE_SOURCE_COMMIT_DRIFT"):
                source_pin(self.repo)

    def test_no_staged_source_may_be_traded_or_promoted(self):
        record = self.stage()
        self.assertFalse(record["trade_authority"])
        self.assertFalse(record["automatic_promotion_enabled"])
        self.assertFalse(record["canonical_owner_enrolled"])

from historical_training.laya_guard_archive_service_plan import (
    prepare, render_dropin, CONF_PREFIX,
)


class GuardUnitPlanTests(unittest.TestCase):
    def setUp(self):
        fixture = GuardArchiveCutoverTests(
            "test_preserves_fifty_one_paths_and_pins_exact_three")
        fixture.setUp()
        self.addCleanup(fixture.td.cleanup)
        self.fixture = fixture
        fixture.stage()
        self.target = fixture.target / "units"
        self.python = fixture.root / "venv-python"
        self.python.write_text("test python binary")
        self.python.chmod(0o700)
        self.guard_runtime = fixture.old

    def planned(self):
        with patch("historical_training.laya_guard_archive_service_plan.PYTHON",
                   str(self.python)), patch(
             "historical_training.laya_guard_archive_service_plan.GUARD_RUNTIME",
             str(self.guard_runtime)):
            return prepare(self.fixture.target, self.target)

    def test_only_replaces_guard_config_and_original_runtime_path(self):
        result = self.planned()
        self.assertEqual(set(result["service_dropins"]), {"trainer", "evaluator"})
        self.assertFalse(result["systemd_deployed"])
        self.assertFalse(result["systemd_restarted"])
        self.assertFalse(result["canonical_owner_granted"])
        for role, details in result["service_dropins"].items():
            conf = Path(details["staged_path"]).read_text()
            self.assertEqual(details["sha256"], sha(details["staged_path"]))
            self.assertEqual(conf.count("ExecStart="), 2)
            self.assertEqual(conf.count("ExecCondition="), 2)
            self.assertIn(" --config " + str(self.fixture.target / "guard.json"),
                          conf)
            self.assertIn(" --role " + role, conf)
            self.assertIn("WorkingDirectory=" + str(self.fixture.new), conf)
            self.assertNotIn("NoNewPrivileges=no", conf)
            self.assertNotIn("Restart=always", conf)
            self.assertNotIn("trade_authority", conf)

    def test_staged_guard_mutation_refused_without_unit_write(self):
        p = self.fixture.target / "guard.json"
        tampered = read(p)
        tampered["source_health_path"] = "/tmp/alternate"
        self.fixture.write(p, tampered)
        with self.assertRaisesRegex(EvidenceError, "ARCHIVE_SYSTEMD_PIN_DRIFT"):
            self.planned()
        self.assertFalse(self.target.exists())

    def test_existing_unit_plan_is_immutable(self):
        self.planned()
        with self.assertRaisesRegex(EvidenceError,
                                    "ARCHIVE_SYSTEMD_STAGING_MUST_BE_NEW"):
            self.planned()

    def test_shell_path_injection_cannot_be_rendered(self):
        with self.assertRaisesRegex(EvidenceError, "ARCHIVE_SYSTEMD_UNSAFE_PATH"):
            render_dropin("trainer", "/tmp/config with space", self.fixture.new)
        with self.assertRaisesRegex(EvidenceError, "ARCHIVE_SYSTEMD_UNSAFE_PATH"):
            render_dropin("trainer", "/tmp/config;evil", self.fixture.new)

    def test_unknown_unit_role_refused(self):
        with self.assertRaisesRegex(EvidenceError, "ARCHIVE_SYSTEMD_UNKNOWN_ROLE"):
            render_dropin("driver", self.fixture.target / "guard.json", self.fixture.new)
