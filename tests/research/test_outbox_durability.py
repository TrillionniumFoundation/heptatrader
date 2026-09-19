"""Real filesystem checks plus explicit fsync fault injection (not power-loss proof)."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from hepta_research.gateway import Outbox


class OutboxDurabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "outbox"

    @staticmethod
    def identity(path):
        s = path.stat()
        return s.st_dev,s.st_ino

    def test_new_and_existing_directory_entry_are_synced(self):
        real = os.fsync
        for _ in range(2):
            seen=[]
            def sync(fd):
                s=os.fstat(fd);seen.append((s.st_dev,s.st_ino));real(fd)
            with patch("hepta_research.gateway.os.fsync",side_effect=sync):
                Outbox(str(self.path))
            self.assertEqual(seen,[self.identity(self.path),self.identity(self.root)])

    def test_parent_sync_failure_aborts_and_reopen_retries(self):
        parent=self.identity(self.root)
        real=os.fsync
        def fail_parent(fd):
            s=os.fstat(fd)
            if (s.st_dev,s.st_ino)==parent: raise OSError("parent fsync failed")
            real(fd)
        with patch("hepta_research.gateway.os.fsync",side_effect=fail_parent):
            with self.assertRaises(OSError): Outbox(str(self.path))
        # The directory may exist but is not thereby durably initialized.
        with patch("hepta_research.gateway.os.fsync",side_effect=fail_parent):
            with self.assertRaises(OSError): Outbox(str(self.path))
        Outbox(str(self.path))
        self.assertEqual(list(self.path.iterdir()),[])

    def test_directory_sync_failure_aborts(self):
        with patch("hepta_research.gateway.os.fsync",side_effect=OSError("fsync")):
            with self.assertRaises(OSError): Outbox(str(self.path))
        self.assertFalse(list(self.path.glob("*.json")))

    def test_symlink_directory_not_accepted(self):
        target=self.root/"other";target.mkdir(mode=0o700)
        self.path.symlink_to(target,target_is_directory=True)
        with self.assertRaises(OSError): Outbox(str(self.path))

    def test_permissions_validated_before_sync(self):
        self.path.mkdir(mode=0o755)
        with patch("hepta_research.gateway.os.fsync") as sync:
            with self.assertRaises(ValueError): Outbox(str(self.path))
            sync.assert_not_called()

    @unittest.skipUnless(Path("/proc/self/fd").exists(),"Linux descriptor observation")
    def test_failure_does_not_leak_file_descriptors(self):
        before=len(list(Path("/proc/self/fd").iterdir()))
        for _ in range(8):
            with patch("hepta_research.gateway.os.fsync",side_effect=OSError("fsync")):
                with self.assertRaises(OSError): Outbox(str(self.path))
        self.assertEqual(len(list(Path("/proc/self/fd").iterdir())),before)
