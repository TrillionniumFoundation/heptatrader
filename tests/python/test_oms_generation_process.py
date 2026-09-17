"""Installed simulator restart across a stopped-state OMS V2 generation seal."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import test_installed_runtime_processes as base


@unittest.skipUnless(os.environ.get("HEPTA_ISOLATED_PROCESS_TESTS") == "1",
                     "requires explicitly isolated multi-UID process test host")
class OmsGenerationInstalledProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != "linux" or os.geteuid() != 0:
            raise RuntimeError("opted-in generation process test requires disposable root Linux")
        # Use the same host interlock as the canonical installed-process suite;
        # never adopt or remove a pre-existing host namespace.
        base.HOST_INTERLOCK.mkdir(mode=0o711)
        base.HOST_INTERLOCK.chmod(0o711)
        cls.lock = base.HOST_INTERLOCK / "session-lease-terminal-cleanup.lock"
        cls.lock.touch(mode=0o644, exist_ok=False)
        cls.lock.chmod(0o644)
        cls.lock_identity = (cls.lock.stat().st_dev, cls.lock.stat().st_ino)
        cls.host_identity = (base.HOST_INTERLOCK.stat().st_dev,
                             base.HOST_INTERLOCK.stat().st_ino)
        cls.addClassCleanup(cls._remove_interlock)

        cls.tmp = tempfile.TemporaryDirectory(prefix="ht-generation-process-", dir="/tmp")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        cls.root.chmod(0o755)
        artifact = Path(os.environ["HEPTA_PROCESS_CANDIDATE_ARTIFACT"])
        digest = os.environ["HEPTA_PROCESS_CANDIDATE_SHA256"]
        cls.slot, cls.manifest = base.admitted_slot(
            artifact, digest, cls.root / "candidate")

    @classmethod
    def _remove_interlock(cls):
        for path, expected in ((cls.lock, cls.lock_identity),
                               (base.HOST_INTERLOCK, cls.host_identity)):
            observed = path.lstat()
            if (observed.st_dev, observed.st_ino) != expected or observed.st_uid != 0:
                raise RuntimeError("fixture interlock identity changed; refusing cleanup")
        cls.lock.unlink()
        base.HOST_INTERLOCK.rmdir()

    def test_fill_stop_seal_restart_preserves_state_identity_and_order_watermark(self):
        runtime = base.InstalledRuntime(self.root / "runtime")
        self.addCleanup(runtime.stop)
        runtime.start(self.slot)
        runtime.provision()
        command, fields, first_order = runtime.place("BUY", 25, "1.1002")
        runtime.wait_position(25)
        runtime.wait_no_orders()
        runtime.stop()

        journal = runtime.root / "es/oms-journal.jsonl"
        store = Path(str(journal) + ".generations")
        helper = self.slot / "libexec/heptatrader/hepta_oms_lifecycle.py"
        result = subprocess.run([
            sys.executable, "-S", str(helper), "seal",
            "--journal", str(journal), "--store", str(store), "--stopped-state",
        ], env=base.CLEAN_ENV, user=base.EXECUTION_UID, group=base.TEST_GID,
            extra_groups=[], stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["schema"], "heptatrader.oms-generation.v2")
        self.assertEqual(receipt["authorization_effect"], "NONE")
        self.assertTrue(journal.read_bytes().startswith(b"HEPTA_OMS_ACTIVE_TAIL_V1\t"))

        generation = json.loads((store / "CURRENT").read_text())["generation"]
        manifest = json.loads((store / generation / "manifest.json").read_text())
        self.assertGreaterEqual(manifest["send_attempt_records"], 1)
        tail_before_duplicate = journal.read_bytes()

        runtime.start(self.slot)
        runtime.wait_position(25)
        status = runtime.call("execution.get_command_status",
                              [f"command_id={command}"])["payload"]
        self.assertEqual(status["command_status"], "accepted")
        self.assertEqual(status["order_id"], first_order)
        duplicate = runtime.call("trade.place_order", fields,
                                 call_id=command, duplicate=True)
        self.assertEqual(duplicate["order_id"], first_order)
        self.assertEqual(journal.read_bytes(), tail_before_duplicate,
                         "idempotent replay must not append or resend after seal")

        _, _, next_order = runtime.place("SELL", 5, "1.1000")
        self.assertGreater(next_order, first_order,
                           "order-id watermark must survive generation rotation")
        runtime.wait_position(20)
        runtime.wait_no_orders()
        runtime.place("SELL", 20, "1.1000")
        runtime.wait_position(0)
        runtime.wait_no_orders()
        runtime.stop()

        # A second restart proves the post-seal active tail composes with the
        # immutable generation rather than replacing its economic base state.
        runtime.start(self.slot)
        runtime.wait_position(0)
        status = runtime.call("execution.get_command_status",
                              [f"command_id={command}"])["payload"]
        self.assertEqual(status["order_id"], first_order)
        runtime.wait_no_orders()
