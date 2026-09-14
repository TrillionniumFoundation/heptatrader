"""Real stopped-state rewrites and crash/compatibility vectors, without Broker I/O."""
from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import hepta_oms_archive as archive
import verify_oms_journal_replay as verifier


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hepta-archive-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "journal"
        # Repeated identities and intentionally noncanonical JSON whitespace
        # must remain byte-identical, not normalized or deduplicated.
        self.raw = b''.join((b'{ "event":"status", "ts_ms":1,"req_id":"retained-command", "reason":"\\u4e2d" }\n'
                              for _ in range(200)))
        self.write(self.raw)

    def write(self, content):
        self.path.write_bytes(content)
        self.path.chmod(0o600)

    def call(self, op, **kw):
        return archive.transform(self.path, op, stopped=True, **kw)

    def test_lossless_roundtrip_preserves_duplicate_identities_and_records(self):
        initial = self.call("inspect")
        result = self.call("compact")
        self.assertLess(result["output_storage_bytes"], len(self.raw))
        self.assertEqual(gzip.decompress(self.path.read_bytes()), self.raw)
        self.assertEqual(result["records"], 200)
        self.assertEqual(result["logical_sha256"], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(self.call("inspect")["logical_sha256"], initial["logical_sha256"])
        events = verifier.load_events(self.path)
        self.assertEqual(len(events), 200)
        self.call("expand")
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_concatenated_members_and_repack_preserve_complete_order(self):
        self.write(gzip.compress(self.raw[:81], mtime=0)+gzip.compress(self.raw[81:], mtime=0))
        self.assertEqual(self.call("inspect")["records"], 200)
        self.call("compact")
        self.call("expand")
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_bad_gzip_or_json_never_replaces_input(self):
        good = gzip.compress(self.raw, mtime=0)
        corrupt = bytearray(good); corrupt[-8] ^= 1
        samples = (good[:-1], good+b'garbage\n', good+b'\0', bytes(corrupt),
                   good+bytes(corrupt), gzip.compress(b'{"event":"x","event":"y","ts_ms":1}\n'),
                   gzip.compress(b'{"event":"x","ts_ms":1,"qty":NaN}\n'),
                   gzip.compress(self.raw[:-1]))
        for data in samples:
            with self.subTest(size=len(data)):
                self.write(data)
                with self.assertRaises((archive.MaintenanceError, verifier.JournalError)):
                    self.call("compact")
                self.assertEqual(self.path.read_bytes(), data)
                self.assertFalse(list(self.root.glob(".*oms-rewrite-*")))

    def test_decoded_byte_record_and_line_limits_not_compressed_size(self):
        self.call("compact")
        self.assertEqual(self.call("inspect", max_bytes=len(self.raw))["logical_bytes"], len(self.raw))
        for kwargs in ({"max_bytes":len(self.raw)-1}, {"max_records":199}, {"max_record_bytes":8},
                       {"max_bytes":0}, {"max_records":1_000_001}):
            before = self.path.read_bytes()
            with self.subTest(kwargs=kwargs), self.assertRaises(verifier.JournalError):
                self.call("expand", **kwargs)
            self.assertEqual(self.path.read_bytes(), before)

    def test_live_shared_lock_refuses_maintenance_even_with_acknowledgement(self):
        with self.path.open("rb") as stream:
            fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.call("compact")
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.call("compact")

    def test_requires_explicit_stopped_state_and_private_namespace(self):
        with self.assertRaises(archive.MaintenanceError):
            archive.transform(self.path, "compact")
        self.root.chmod(0o755)
        with self.assertRaises(archive.MaintenanceError):
            self.call("compact")
        self.root.chmod(0o700)
        self.path.chmod(0o640)
        with self.assertRaises(archive.MaintenanceError):
            self.call("compact")

    def test_symlink_hardlink_fifo_and_parent_symlink_fail_without_blocking(self):
        link = self.root / "hardlink"
        os.link(self.path, link)
        with self.assertRaises(archive.MaintenanceError):
            self.call("compact")
        link.unlink(); self.path.unlink()
        self.path.symlink_to("missing")
        with self.assertRaises(OSError):
            self.call("compact")
        self.path.unlink(); os.mkfifo(self.path, 0o600)
        result = subprocess.run([sys.executable, "-S", str(ROOT/"scripts/hepta_oms_archive.py"),
            "--journal", str(self.path), "--operation", "compact", "--stopped-state"],
            capture_output=True, text=True, timeout=2)
        self.assertNotEqual(result.returncode,0)
        self.path.unlink(); self.write(self.raw)
        alias = self.root / "alias"; alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            archive.transform(alias/"journal", "compact", stopped=True)

    def test_fsync_failure_before_replace_preserves_original(self):
        with mock.patch.object(archive.os, "fsync", side_effect=OSError("fixture")):
            with self.assertRaises(OSError):
                self.call("compact")
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertFalse(list(self.root.glob(".*oms-rewrite-*")))

    def test_directory_sync_failure_reports_uncertain_without_rollback(self):
        sync = os.fsync
        calls = []
        def fail_second(fd):
            calls.append(fd)
            if len(calls)==2: raise OSError("fixture")
            return sync(fd)
        with mock.patch.object(archive.os, "fsync", side_effect=fail_second):
            with self.assertRaisesRegex(archive.MaintenanceError, "DURABILITY_UNCERTAIN"):
                self.call("compact")
        self.assertEqual(gzip.decompress(self.path.read_bytes()), self.raw)
        self.call("expand")
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_namespace_exchange_not_overwritten_and_scratch_not_adopted(self):
        def exchange(phase):
            self.path.rename(self.root/"original")
            self.write(b'{"event":"decoy","ts_ms":2}\n')
        with self.assertRaisesRegex(archive.MaintenanceError,"SNAPSHOT_CHANGED"):
            self.call("compact",checkpoint=exchange)
        self.assertEqual((self.root/"original").read_bytes(), self.raw)
        self.assertEqual(self.path.read_bytes(), b'{"event":"decoy","ts_ms":2}\n')

    def test_crash_before_and_after_replace_leaves_complete_restartable_history(self):
        code = """import os,signal,sys
sys.path.insert(0,sys.argv[1])
from pathlib import Path
import hepta_oms_archive as a
def cut(phase):
    if phase == sys.argv[3]: os.kill(os.getpid(),signal.SIGKILL)
a.transform(Path(sys.argv[2]),'compact',stopped=True,checkpoint=cut)
"""
        for phase in ("prepared", "replaced"):
            self.write(self.raw)
            result=subprocess.run([sys.executable,"-S","-c",code,str(ROOT/"scripts"),str(self.path),phase],
                                  capture_output=True,timeout=5)
            self.assertEqual(result.returncode,-signal.SIGKILL)
            self.assertEqual(self.call("inspect")["logical_sha256"],hashlib.sha256(self.raw).hexdigest())
            self.call("expand")
            self.assertEqual(self.path.read_bytes(),self.raw)
            # Orphans are private, ignored, and only removed by fixture cleanup.

    def test_both_inode_locks_are_held_through_replacement(self):
        def competitor(phase):
            if phase == "replaced":
                with self.path.open("rb") as stream:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
        self.call("compact",checkpoint=competitor)

    def test_cli_summary_is_identifier_free(self):
        result=subprocess.run([sys.executable,"-S",str(ROOT/"scripts/hepta_oms_archive.py"),
                               "--journal",str(self.path)],capture_output=True,text=True,timeout=2)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)["records"],200)
        self.assertNotIn("retained-command",result.stdout+result.stderr)
        self.assertNotIn(str(self.path),result.stdout+result.stderr)


if __name__ == "__main__":
    unittest.main()
