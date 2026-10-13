"""Laya research-only guarded activation — adversarial, no real systemd writes."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from historical_training.common import EvidenceError, sha
from historical_training import laya_guard_archive_activate as m


class ArchiveActivationChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.cache=self.base/"canary"
        self.cache.mkdir(mode=0o700)
        self.out=self.base/"new-activation"
        self.patches=[
            patch.object(m,"CANARY",self.cache),
            patch.object(m,"OUTPUT",self.out),
            patch.object(m,"CANARY_END_MS",100),
        ]
        for item in self.patches:
            item.start();self.addCleanup(item.stop)

    def put(self,name,doc):
        p=self.cache/name
        p.write_bytes(json.dumps(doc,sort_keys=True).encode()+b"\n")
        p.chmod(0o600)
        return p

    def canary_fixture(self):
        plan=self.put("plan.json",{
            "capture_exact_target_ms":1791857102000,
            "fixed_anchor_ms":1791857100000,
            "capture_permitted_window_ms":[1791857101000,1791857104500],
            "model_promotion_allowed":False,"broker_orders_sent":0})
        self.pin=patch.object(m,"SOURCE_PLAN_SHA",sha(plan))
        self.pin.start();self.addCleanup(self.pin.stop)
        data={"schema":"original-quote-health"}
        for label in ("baseline","canary"):
            self.put(label+"-original-status.json",data)
        report={
            "state":"TWO_READONLY_SOURCE_HEALTH_SNAPSHOTS_PRESERVED",
            "canary_precommit_sha256":sha(plan),
            "original_trade_entry_eligibility_not_reclassified":True,
            "trade_authority":False,"broker_orders_sent":0,
            "attempt_at_ms":1791857102000,
            "comparison_durable_at_ms":1791857102100,
        }
        for label in ("baseline","canary"):
            report[label]={"captured_sha256":sha(self.cache/
                (label+"-original-status.json")),"healthy_at_exact_time":False}
        self.put("comparison.json",report)
        return report

    def test_idle_common_gpu_lease_can_be_inspected_without_worker(self):
        root=self.base/"same-gpu-lease"
        root.mkdir(mode=0o700)
        file=root/"heavy-worker.lock"
        file.write_bytes(b"")
        file.chmod(0o600)
        m.shared_gpu_lease_available(root)
        self.assertTrue(file.exists())

    def test_other_process_holding_common_gpu_lease_is_hard_hold(self):
        import subprocess
        import sys
        root=self.base/"same-gpu-lease"
        root.mkdir(mode=0o700)
        file=root/"heavy-worker.lock"
        file.write_bytes(b"")
        file.chmod(0o600)
        process=subprocess.Popen(
            [sys.executable,"-u","-c",
             "import fcntl,sys,time;"
             "f=open(sys.argv[1],'r+');"
             "fcntl.flock(f,fcntl.LOCK_EX);"
             "print('LEASED',flush=True);"
             "time.sleep(3)",str(file)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(),"LEASED")
            with self.assertRaisesRegex(EvidenceError,"SHARED_GPU_LEASE_HELD"):
                m.shared_gpu_lease_available(root)
        finally:
            process.terminate()
            process.communicate(timeout=5)
        m.shared_gpu_lease_available(root)

    def test_symlinked_common_gpu_lease_is_never_followed(self):
        root=self.base/"same-gpu-lease"
        root.mkdir(mode=0o700)
        original=self.base/"separate-real-lock"
        original.write_bytes(b"")
        (root/"heavy-worker.lock").symlink_to(original)
        with self.assertRaisesRegex(EvidenceError,"SHARED_GPU_LEASE_MISSING"):
            m.shared_gpu_lease_available(root)

    def test_future_canary_cannot_activate(self):
        with self.assertRaisesRegex(EvidenceError,"FIXED_CANARY_NOT_COMPLETED"):
            m.collect_canary(100)

    def test_missing_canary_result_fails_closed(self):
        self.canary_fixture()
        (self.cache/"comparison.json").unlink()
        with self.assertRaises(EvidenceError):
            m.collect_canary(101)

    def test_two_original_raw_snapshots_verified_even_if_unhealthy(self):
        self.canary_fixture()
        with patch.object(m.subprocess,"run",
                return_value=SimpleNamespace(returncode=0,
                                             stdout="ActiveState=inactive",
                                             stderr="")):
            res=m.collect_canary(101)
        self.assertEqual(res["comparison_state"],
                         "TWO_READONLY_SOURCE_HEALTH_SNAPSHOTS_PRESERVED")

    def test_original_raw_snapshot_tamper_rejected(self):
        self.canary_fixture()
        self.put("baseline-original-status.json",{"different":True})
        with patch.object(m.subprocess,"run",
                return_value=SimpleNamespace(returncode=0,
                                             stdout="ActiveState=inactive",
                                             stderr="")):
            with self.assertRaisesRegex(EvidenceError,"FIXED_CANARY_SNAPSHOT_CHANGED"):
                m.collect_canary(101)

    def test_cache_worker_still_active_refuses_cutover(self):
        self.canary_fixture()
        with patch.object(m.subprocess,"run",
                return_value=SimpleNamespace(returncode=0,
                                             stdout="ActiveState=active",
                                             stderr="")):
            with self.assertRaisesRegex(EvidenceError,"FIXED_CANARY_WORKER_STILL_RUNNING"):
                m.collect_canary(101)

    def activation_fixture(self):
        self.source=self.base/"staged"/"unit-plan"
        self.source.mkdir(parents=True,mode=0o700)
        self.dest=self.base/"systemd"
        self.dest.mkdir(mode=0o700)
        installed={}
        valid={}
        for name in m.ROLE_UNIT:
            src=self.source/f"zzzz-archive-guard-pin-20261013-{name}.conf"
            src.write_bytes(("[Service]\n# immutable "+name+"\n").encode())
            dest=self.dest/f"role-{name}"/m.CONF
            dest.parent.mkdir(mode=0o700)
            installed[name]={"destination":str(dest)}
            valid[name]=sha(src)
        pre={
            "protected_cache_canary":{"comparison_sha256":"a"*64},
            "candidate_guard_sha256":"b"*64,
            "units":installed,
        }
        stage_root=self.source.parent
        keys=[
            patch.object(m,"STAGED",stage_root),
            patch.object(m,"EXPECTED",valid),
            patch.object(m,"preview",return_value=pre),
            patch.object(m,"controlled_jobs",return_value={}),
            patch.object(m,"collect_canary",return_value={"state":"CLOSED"}),
            patch.object(m.time,"sleep",return_value=None),
        ]
        for x in keys:x.start();self.addCleanup(x.stop)
        return pre

    def show(self,*args):
        if args[0]=="show":
            return "ActiveState=active\nExecStart="+str(m.STAGED/"guard.json")+"\nDropInPaths="+m.CONF+"\n"
        return ""

    def test_activation_modifies_only_two_new_dropins(self):
        self.activation_fixture()
        with patch.object(m,"systemctl",side_effect=self.show) as ctl:
            result=m.activate()
        self.assertEqual(result["state"],"GUARDED_RESEARCH_SERVICE_CUTOVER_APPLIED")
        for role in m.ROLE_UNIT:
            p=self.dest/f"role-{role}"/m.CONF
            self.assertTrue(p.is_file())
            self.assertEqual(sha(p),m.EXPECTED[role])
        self.assertTrue((self.out/"result.json").is_file())
        self.assertFalse((self.out/"rollback.json").exists())
        self.assertTrue(any(call.args[0]=="daemon-reload" for call in ctl.call_args_list))
        self.assertFalse(result["canonical_owner_granted"])

    def test_failed_restart_rolls_back_only_created_dropins(self):
        self.activation_fixture()
        failures={"attempts":0}
        def control(*args):
            if args[0]=="restart":
                failures["attempts"]+=1
                if failures["attempts"]==1:
                    raise EvidenceError("INJECTED_RESEARCH_UNIT_FAILURE")
            return self.show(*args)
        with patch.object(m,"systemctl",side_effect=control):
            with self.assertRaisesRegex(EvidenceError,"INJECTED_RESEARCH_UNIT_FAILURE"):
                m.activate()
        for role in m.ROLE_UNIT:
            self.assertFalse((self.dest/f"role-{role}"/m.CONF).exists())
        self.assertTrue((self.out/"rollback.json").exists())
        self.assertFalse((self.out/"result.json").exists())

    def test_activation_never_overwrites_existing_dropin(self):
        self.activation_fixture()
        dest=self.dest/"role-trainer"/m.CONF
        dest.write_bytes(b"owner original")
        with patch.object(m,"systemctl",side_effect=self.show):
            with self.assertRaisesRegex(EvidenceError,"OUTPUT_ALREADY_EXISTS"):
                m.activate()
        self.assertEqual(dest.read_bytes(),b"owner original")

    def test_immutable_activation_output_blocks_second_run(self):
        self.activation_fixture()
        self.out.mkdir(mode=0o700)
        with self.assertRaisesRegex(EvidenceError,"ACTIVATION_OUTPUT_ALREADY_EXISTS"):
            m.activate()
