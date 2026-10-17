import fcntl
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from historical_training import resident_resource_guard as guard
from historical_training import resident_comgr_cache as cache


class CacheLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.lock=self.root/"heavy-worker.lock"
        self.gate=guard.Gate.__new__(guard.Gate)
        self.gate.c={"cache_maintenance":cache.PROFILE}
        self.gate.root=self.root;self.gate.role="trainer"
        self.gate.lease=guard.Lease(self.lock)
    def tearDown(self):
        self.gate.lease.release();self.tmp.cleanup()

    def test_parent_lease_survives_maintenance_until_real_release(self):
        self.assertTrue(self.gate.lease.acquire())
        parent_fd=self.gate.lease.fd
        def sweep(config,*,existing_lease_fd):
            self.assertEqual(existing_lease_fd,parent_fd)
            with self.lock.open("r+") as fd:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            return {"phase":"NO_WORK","removed_files":0,"removed_bytes":0}
        with patch.object(cache,"sweep_terminal",sweep):
            self.gate.release()
        self.assertIsNone(self.gate.lease.fd)
        with self.lock.open("r+") as fd:
            fcntl.flock(fd.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)

    def test_cleanup_and_status_failure_do_not_leak_lease_or_change_result(self):
        for status_failure in (False,True):
            self.assertTrue(self.gate.lease.acquire())
            with patch.object(cache,"sweep_terminal",side_effect=OSError("cache failed")):
                if status_failure:
                    with patch.object(guard,"atomic",side_effect=OSError("status failed")):
                        self.gate.release()
                else:self.gate.release()
            self.assertIsNone(self.gate.lease.fd)
            self.assertTrue(self.gate.lease.acquire());self.gate.lease.release()

    def test_idle_busy_is_nonblocking_and_empty_sweep_is_throttled(self):
        with patch.object(cache,"sweep_terminal",side_effect=BlockingIOError) as sweep:
            self.gate.release()
            self.gate.release()
            self.assertEqual(sweep.call_count,1)
        self.assertIsNone(self.gate.lease.fd)
        import json
        self.assertEqual(json.loads((self.root/"trainer-cache-maintenance.json").read_text())["phase"],"HEAVY_LEASE_BUSY")

    def test_unreviewed_or_unpinned_cache_profile_is_rejected(self):
        with self.assertRaisesRegex(Exception,"UNREVIEWED_CACHE"):
            guard.verify_config({"cache_maintenance":"other"})
        with self.assertRaisesRegex(Exception,"RUNTIME_NOT_PINNED"):
            guard.verify_config({"cache_maintenance":cache.PROFILE})
