"""Host-independent stdlib archive verification; all test sources are synthetic."""
from __future__ import annotations
import copy
import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest

from historical_training.common import canonical
from test_fx_original_health_sustained_frontier import make_fixture
from train_v2.fx_original_health_sustained_frontier import verify_prefix
from train_v2.fx_original_health_portable_prefix_verify import (
  PIN, Fail, archive, pack, parse, read_private, sha, verify
)

def fixture(t):
    root=make_fixture(Path(t)/"root",32)
    report=verify_prefix(root,32)
    raw=pack(report)+b"\n"
    paths=["plan.json","worker-start.json","sandbox.json"]
    paths += [f"cycle-{i:02d}-{suffix}.json"
              for i in range(32) for suffix in ("receipt","status")]
    archived=Path(t)/"immutable.tar.gz"
    with tarfile.open(archived,"w:gz") as tf:
        for name in paths:tf.add(root/name,arcname=name)
    archived.chmod(0o600)
    pins={
        "plan":sha((root/"plan.json").read_bytes()),
        "worker":sha((root/"worker-start.json").read_bytes()),
        "sandbox":sha((root/"sandbox.json").read_bytes()),
        "head":report["prefix_original_receipt_hash_chain_head"],
        "report":sha(raw),
    }
    data,bundle_sha=archive(archived)
    return data,(raw,parse(raw)),pins,archived

class PortableTests(unittest.TestCase):
    def test_portable_independently_recomputes_raw_source(self):
        with tempfile.TemporaryDirectory() as t:
            data,report,pins,archived=fixture(t)
            answer=verify(data,report,pins)
            self.assertEqual(answer["verified_count"],32)
            self.assertEqual(answer["ready_count"],32)
            self.assertEqual(answer["max_publish_age_ms"],1200)
            self.assertEqual(answer["max_age_before_next_publication_ms"],16200)
            self.assertEqual(answer["unverified_count"],448)
            self.assertFalse(answer["final_480_accepted"])
            self.assertFalse(answer["trade_authority"])
    def test_tamper_seal_declared_frozen_plan_refused(self):
        with tempfile.TemporaryDirectory() as t:
            data,report,pins,_=fixture(t)
            d=dict(data);d["plan.json"]+=b" "
            with self.assertRaisesRegex(Fail,"PRESEALED_OR_WORKER_BYTES_CHANGED"):
                verify(d,report,pins)
    def test_corrupt_middle_status_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as t:
            data,report,pins,_=fixture(t)
            d=dict(data)
            d["cycle-09-status.json"]=d["cycle-09-status.json"].replace(
                b'"USABLE"',b'"PATH_GAP"',1)
            with self.assertRaisesRegex(Fail,"ORIGINAL_RAW_SOURCE_NOT_BOUND"):
                verify(d,report,pins)
    def test_corrupt_rolling_receipt_refused(self):
        with tempfile.TemporaryDirectory() as t:
            data,report,pins,_=fixture(t)
            d=dict(data)
            d["cycle-10-receipt.json"]=d["cycle-10-receipt.json"].replace(
                b'"source_ready":true',b'"source_ready":false',1)
            with self.assertRaises(Fail):verify(d,report,pins)
    def test_fake_crosshost_unreviewed_report_refused(self):
        with tempfile.TemporaryDirectory() as t:
            data,report,pins,_=fixture(t)
            changed=copy.deepcopy(report[1])
            changed["future_cycles_NOT_verified"]=0
            raw=pack(changed)+b"\n"
            with self.assertRaisesRegex(Fail,"OLD_AUDIT_CONTENT_CHANGED"):
                verify(data,(raw,changed),pins)
    def test_no_production_promotion_from_32_cycle_success(self):
        with tempfile.TemporaryDirectory() as t:
            data,report,pins,_=fixture(t)
            ans=verify(data,report,pins)
            for key in ("production_approval","original_source_ib_signed",
                        "product_witness_enrolled","final_480_accepted",
                        "quote_source_independent_from_pocket4",
                        "model_training_or_promotion_admitted"):
                self.assertIs(ans[key],False)
    def test_source_archive_duplicate_path_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            data,report,pins,archived=fixture(t)
            copy_to=Path(t)/"duplicate.tar.gz"
            with tarfile.open(copy_to,"w:gz") as out:
                with tarfile.open(archived,"r:gz") as inp:
                    for row in inp:
                        raw=inp.extractfile(row).read()
                        info=tarfile.TarInfo(row.name)
                        info.size=len(raw);info.mode=0o600
                        out.addfile(info,io.BytesIO(raw))
                payload=b'{"x":2}'
                info=tarfile.TarInfo("plan.json")
                info.size=len(payload);info.mode=0o600
                out.addfile(info,io.BytesIO(payload))
            copy_to.chmod(0o600)
            with self.assertRaisesRegex(Fail,"ARCHIVE_UNSAFE_OR_DUPLICATE"):
                archive(copy_to)
    def test_unsafe_member_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"link.tar.gz"
            with tarfile.open(p,"w:gz") as tf:
                member=tarfile.TarInfo("plan.json")
                member.type=tarfile.SYMTYPE
                member.linkname="/etc/passwd";member.mode=0o600
                tf.addfile(member)
            p.chmod(0o600)
            with self.assertRaisesRegex(Fail,"ARCHIVE_UNSAFE_OR_DUPLICATE"):
                archive(p)
    def test_public_tar_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            data,report,pins,archived=fixture(t)
            archived.chmod(0o644)
            with self.assertRaisesRegex(Fail,"UNSAFE_PRIVATE_FILE"):
                archive(archived)
    def test_duplicate_or_nan_json_refused(self):
        for b in (b'{"key":1,"key":2}',b'{"x":NaN}'):
            with self.assertRaises(Fail):parse(b)
if __name__=="__main__":unittest.main()
