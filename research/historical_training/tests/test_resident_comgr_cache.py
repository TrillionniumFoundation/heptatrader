import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from historical_training import resident_comgr_cache as gc


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.root=self.base/"run";self.cache=self.root/"candidates/job/gpu-tmp/comgr-cache"
        self.cache.mkdir(parents=True);self.lock=self.base/"heavy-worker.lock";self.lock.touch()
        self.receipt=self.base/"cleanup.json"
    def tearDown(self):self.tmp.cleanup()

    def test_receipt_precedes_unlink_and_preserves_evidence(self):
        (self.cache/"llvmcache-a").write_bytes(b"compiled")
        original=self.root/"candidates/job/artifact.json";original.write_bytes(b"immutable evidence")
        (self.cache/"keep.db").write_bytes(b"other-cache")
        real=gc.os.unlink
        def checked(name,*,dir_fd=None):
            r=json.loads(self.receipt.read_text())
            self.assertEqual(r["phase"],"PLANNED")
            self.assertEqual(r["files"][0]["sha256"],hashlib.sha256(b"compiled").hexdigest())
            return real(name,dir_fd=dir_fd)
        with patch.object(gc.os,"unlink",checked):
            r=gc.prune_comgr_cache(self.root,self.lock,{"candidates/job":"COMPLETED"},self.receipt)
        self.assertEqual(r["removed_files"],1);self.assertEqual(r["removed_bytes"],8)
        self.assertEqual(original.read_bytes(),b"immutable evidence")
        self.assertEqual((self.cache/"keep.db").read_bytes(),b"other-cache")

    def test_existing_parent_lease_links_and_busy_refusal(self):
        (self.cache/"llvmcache-a").write_bytes(b"compiled")
        outside=self.base/"outside";outside.write_bytes(b"source")
        (self.cache/"llvmcache-soft").symlink_to(outside)
        os.link(outside,self.cache/"llvmcache-hard")
        with self.lock.open("r+") as owner:
            fcntl.flock(owner.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                gc.prune_comgr_cache(self.root,self.lock,{"candidates/job":"COMPLETED"},self.receipt)
            self.assertFalse(self.receipt.exists())
            def selected():
                with self.lock.open("r+") as competitor:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(competitor.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                return {"candidates/job":"FAILED"}
            r=gc.prune_comgr_cache(self.root,self.lock,selected,self.receipt,existing_lease_fd=owner.fileno())
            self.assertEqual(r["removed_files"],1)
            with self.lock.open("r+") as competitor:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(competitor.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertGreaterEqual(os.fstat(owner.fileno()).st_ino,1)
        self.assertEqual(outside.read_bytes(),b"source")
        self.assertTrue((self.cache/"llvmcache-soft").is_symlink())
        self.assertTrue((self.cache/"llvmcache-hard").exists())

    def test_path_boundary_identity_change_and_receipt_retry(self):
        p=self.cache/"llvmcache-a";p.write_bytes(b"old")
        with self.assertRaises(ValueError):
            gc.prune_comgr_cache(self.root,self.lock,{"candidates/../escape":"COMPLETED"},self.receipt)
        with self.assertRaises(ValueError):
            gc.prune_comgr_cache(self.root,self.lock,{"candidates/job":"RUNNING"},self.receipt)
        original=gc.write_new
        def changed(fd,name,value):
            result=original(fd,name,value)
            if name==self.receipt.name:p.write_bytes(b"new different bytes")
            return result
        with patch.object(gc,"write_new",changed):
            r=gc.prune_comgr_cache(self.root,self.lock,{"candidates/job":"COMPLETED"},self.receipt)
        self.assertEqual(r["removed_files"],0)
        self.assertEqual(r["files"][0]["reason"],"IDENTITY_CHANGED_BEFORE_UNLINK")
        with self.assertRaises(FileExistsError):
            gc.prune_comgr_cache(self.root,self.lock,{"candidates/job":"COMPLETED"},self.receipt)
        self.assertEqual(p.read_bytes(),b"new different bytes")

    def test_wrong_parent_fd_and_idle_no_receipt(self):
        other=self.base/"other.lock";other.touch()
        (self.cache/"llvmcache-a").write_bytes(b"compiled")
        with other.open("r+") as fd:
            fcntl.flock(fd.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(ValueError):
                gc.prune_comgr_cache(self.root,self.lock,{"candidates/job":"COMPLETED"},self.receipt,
                                     existing_lease_fd=fd.fileno())
        self.assertTrue((self.cache/"llvmcache-a").exists())
        r=gc.prune_comgr_cache(self.root,self.lock,lambda:{},self.receipt)
        self.assertEqual(r["phase"],"NO_WORK");self.assertFalse(self.receipt.exists())


    def test_rechallenge_directory_binds_original_candidate(self):
        import sqlite3
        control=self.root/"control";control.mkdir()
        with sqlite3.connect(control/"scheduler.sqlite") as db:
            db.execute("CREATE TABLE jobs(id TEXT,status TEXT,payload TEXT)")
        evaluation=self.root/"evaluation"
        task=evaluation/"rechallenge-example"
        cache=task/"gpu-tmp/comgr-cache";cache.mkdir(parents=True)
        (cache/"llvmcache-a").write_bytes(b"compiled")
        q={"state":"REJECTED","plan_id":"plan","candidate_id":"candidate"}
        plan={"job_id":"job","plan_id":"plan","candidate_id":"candidate",
              "candidate_directory":str(self.root/"candidates/job/artifact")}
        (task/"qualification.json").write_text(json.dumps(q))
        (task/"plan.json").write_text(json.dumps(plan))
        config={"state_root":str(self.root),"control_directory":str(control),
                "evaluation_directory":str(evaluation)}
        iterate=Path.iterdir
        def fixture_processes(path):
            return iter(()) if path==Path("/proc") else iterate(path)
        with patch.object(Path,"iterdir",fixture_processes):
            result=gc.terminal_tasks(config,{})
            self.assertEqual(result,{"evaluation/rechallenge-example":"REJECTED"})
            plan["candidate_directory"]=str(self.base/"unrelated/artifact")
            (task/"plan.json").write_text(json.dumps(plan))
            with self.assertRaises(ValueError):gc.terminal_tasks(config,{})
