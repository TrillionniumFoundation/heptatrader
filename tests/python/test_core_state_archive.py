from __future__ import annotations
import fcntl
import io
import zlib
from contextlib import redirect_stderr
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"scripts"))
import hepta_core_state_archive as archive


class CoreStateArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.es=self.root/"es";self.es.mkdir(mode=0o700)
        self.gs=self.root/"gs";self.gs.mkdir(mode=0o700)
        self.lease=self.gs/"leases";self.lease.write_bytes(b"HSL2\nfixture-ciphertext\n");self.lease.chmod(0o600)
        self.journal=self.es/"oms-journal.jsonl";self.journal.write_bytes(b'{"event":"order_intent","req_id":"stable-fixture-id"}\n'*100);self.journal.chmod(0o600)
        self.key=self.root/"key";self.key.write_bytes(b"K"*32);self.key.chmod(0o400)
        self.elock=self.es/"execution-runtime.lock";self.elock.touch(mode=0o600)
        self.glock=self.root/"cleanup.lock";self.glock.touch(mode=0o600)
        self.target=self.root/"archive"
        self.uid,self.gid=os.geteuid(),os.getegid()

    def snapshot(self,target=None):
        return archive.snapshot(self.es,self.lease,self.key,target or self.target,self.uid,self.uid,self.gid,
                                cleanup_lock=self.glock,cleanup_uid=self.uid,cleanup_gid=self.gid,cleanup_mode=0o600)

    def test_lossless_compressed_copy_and_new_restore_without_key_export(self):
        original=self.journal.read_bytes();lease=self.lease.read_bytes()
        manifest=self.snapshot()
        self.assertEqual(set(os.listdir(self.target)),archive.NAMES)
        self.assertLess((self.target/"journal.jsonl.gz").stat().st_size,len(original))
        self.assertEqual(archive.verify(self.target,self.key),manifest)
        dest=self.root/"restored"
        archive.restore(self.target,self.key,dest,self.uid,self.uid,self.gid)
        self.assertEqual((dest/"execution/oms-journal.jsonl").read_bytes(),original)
        self.assertEqual((dest/"gateway/leases").read_bytes(),lease)
        self.assertTrue((dest/"RESTORED.json").is_file())
        self.assertFalse((dest/"gateway/key").exists())
        self.assertEqual(self.journal.read_bytes(),original)
        self.assertEqual((dest/"execution").stat().st_mode&0o777,0o700)
        self.assertEqual((dest/"execution/oms-journal.jsonl").stat().st_mode&0o777,0o600)
        self.assertNotIn(b"K"*32,(self.target/"manifest.json").read_bytes())

    def test_each_live_lock_rejects_before_creating_output(self):
        for path in (self.elock,self.glock):
            with path.open("rb") as source:
                fcntl.flock(source,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.subTest(path=path),self.assertRaises(BlockingIOError):self.snapshot()
                self.assertFalse(self.target.exists())

    def test_does_not_create_missing_lock_or_overwrite_any_tree(self):
        self.elock.unlink()
        with self.assertRaises(FileNotFoundError):self.snapshot()
        self.assertFalse(self.elock.exists());self.assertFalse(self.target.exists())
        self.elock.touch(mode=0o600);self.snapshot()
        before=(self.target/"manifest.json").read_bytes()
        with self.assertRaises(FileExistsError):self.snapshot()
        self.assertEqual((self.target/"manifest.json").read_bytes(),before)
        dest=self.root/"restored";dest.mkdir(mode=0o700)
        with self.assertRaises(FileExistsError):archive.restore(self.target,self.key,dest,self.uid,self.uid,self.gid)
        self.assertEqual(list(dest.iterdir()),[])

    def test_key_and_custody_mismatch_reject_before_restore(self):
        self.snapshot();dest=self.root/"restored"
        wrong=self.root/"wrong-key";wrong.write_bytes(b"X"*32);wrong.chmod(0o400)
        with self.assertRaises(ValueError):archive.restore(self.target,wrong,dest,self.uid,self.uid,self.gid)
        self.assertFalse(dest.exists())
        with self.assertRaises(ValueError):archive.restore(self.target,self.key,dest,self.uid+1,self.uid,self.gid)
        self.assertFalse(dest.exists())

    def test_corruption_missing_payload_and_extra_namespace_rejected(self):
        self.snapshot();dest=self.root/"restored"
        blob=self.target/"lease.bin";blob.write_bytes(b"changed")
        with self.assertRaises(ValueError):archive.restore(self.target,self.key,dest,self.uid,self.uid,self.gid)
        self.assertFalse(dest.exists())
        blob.write_bytes(self.lease.read_bytes())
        extra=self.target/"credential";extra.touch(mode=0o600)
        with self.assertRaises(ValueError):archive.verify(self.target,self.key)
        extra.unlink();blob.unlink()
        with self.assertRaises(ValueError):archive.verify(self.target,self.key)

    def test_expansion_and_duplicate_manifest_fields_are_bounded(self):
        self.snapshot();compressed=self.target/"journal.jsonl.gz"
        compressed.write_bytes(gzip.compress(b"z"*100000))
        with self.assertRaises(ValueError):archive.verify(self.target,self.key)
        manifest=self.target/"manifest.json"
        manifest.write_text('{"schema":"x","schema":"y"}')
        with self.assertRaises(ValueError):archive.verify(self.target,self.key)

    def test_fifo_links_and_unsafe_modes_reject_without_output(self):
        content=self.lease.read_bytes();self.lease.unlink();os.mkfifo(self.lease)
        with self.assertRaises(ValueError):self.snapshot()
        self.assertFalse(self.target.exists());self.lease.unlink()
        self.lease.symlink_to(self.key)
        with self.assertRaises(OSError):self.snapshot()
        self.lease.unlink();self.lease.write_bytes(content);self.lease.chmod(0o600)
        linked=self.gs/"linked";os.link(self.lease,linked)
        with self.assertRaises(ValueError):self.snapshot()
        linked.unlink();self.lease.chmod(0o644)
        with self.assertRaises(ValueError):self.snapshot()
        self.assertFalse(self.target.exists())

    def test_symlink_ancestor_never_redirects_reads(self):
        link=self.root/"alias";link.symlink_to(self.es,target_is_directory=True)
        with self.assertRaises(OSError):
            with archive.directory(link):pass

    def test_failed_sync_retains_incomplete_attempt_not_false_success(self):
        original=self.journal.read_bytes()
        with mock.patch.object(archive.os,"fsync",side_effect=OSError("fixture sync failure")):
            with self.assertRaises(OSError):self.snapshot()
        self.assertTrue(self.target.is_dir())
        self.assertFalse((self.target/"manifest.json").exists())
        self.assertEqual(self.journal.read_bytes(),original)
        with self.assertRaises(ValueError):archive.verify(self.target,self.key)

    def test_source_substitution_is_detected_before_manifest(self):
        real=archive.copy_stream;switched=False
        def substitute(*args,**kwargs):
            nonlocal switched
            result=real(*args,**kwargs)
            if not switched:
                switched=True;self.journal.rename(self.es/"original")
                self.journal.write_bytes(b"substituted");self.journal.chmod(0o600)
            return result
        with mock.patch.object(archive,"copy_stream",side_effect=substitute):
            with self.assertRaises(ValueError):self.snapshot()
        self.assertFalse((self.target/"manifest.json").exists())

    def test_nonroot_operational_cli_refuses_before_io(self):
        with mock.patch.object(archive.os,"geteuid",return_value=12345),mock.patch.object(archive,"verify") as verify:
            self.assertEqual(archive.main(["verify","--archive","/not-used","--key","/not-used"]),2)
            verify.assert_not_called()

    def test_mixed_ib_state_is_rejected_before_archive_creation(self):
        (self.es/"ib-paper-runtime.lock").touch(mode=0o600)
        with self.assertRaises(ValueError):self.snapshot()
        self.assertFalse(self.target.exists())

    def test_invalid_deflate_is_rejected_without_sensitive_cli_traceback(self):
        self.snapshot()
        (self.target/"journal.jsonl.gz").write_bytes(
            bytes.fromhex("1f8b080000000000000307000000000000000000"))
        with self.assertRaises(zlib.error):archive.verify(self.target,self.key)
        error=io.StringIO()
        with mock.patch.object(archive.os,"geteuid",return_value=0), mock.patch.object(
                archive,"verify",side_effect=zlib.error("private-path-must-not-leak")), redirect_stderr(error):
            self.assertEqual(archive.main(["verify","--archive","/not-used","--key","/not-used"]),2)
        self.assertEqual(error.getvalue(),"CORE_STATE_ARCHIVE_REJECTED\n")
