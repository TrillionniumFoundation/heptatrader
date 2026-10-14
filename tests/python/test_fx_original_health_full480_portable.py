"""Synthetic adversarial tests of pinned FULL-480 source evidence, not trade."""
from __future__ import annotations
from pathlib import Path
import io,json,tarfile,tempfile,unittest
from historical_training.common import canonical
from test_fx_original_health_sustained_frontier import make_fixture
from train_v2.fx_original_health_sustained_frontier import verify_prefix
from train_v2.fx_original_health_sustained_audit import verify_all
from train_v2.fx_original_health_full480_portable import (
    Hold, PIN, archive, canonical as c14n,parse,sha,verify
)
COHORT="heptatrader.fx-original-sustained-source-archive.v1"

def fixture(t):
    root=make_fixture(Path(t)/"root",480)
    prefix=verify_prefix(root,320)
    prefix_raw=c14n(prefix)+b"\n"
    receipts=[]
    for i in range(480):
        r=(root/f"cycle-{i:02d}-receipt.json").read_bytes()
        p=parse(r)
        receipts.append({"index":i,"status":p["state"],
                         "receipt_sha256":sha(r),"source_ready":p["source_ready"]})
    final={"schema":COHORT+".final.v1","plan_sha256":sha((root/"plan.json").read_bytes()),
           "receipt_chain_head":receipts[-1]["receipt_sha256"],"cycles":receipts,
           "full_snapshots_written":480,
           "no_prod_monitor_cutover":True,"production_trading_grant":False,
           "broker_orders_sent":0}
    (root/"final.json").write_bytes(c14n(final)+b"\n")
    (root/"final.json").chmod(0o600)
    full=verify_all(root)
    full_raw=c14n(full)+b"\n"
    paths=["plan.json","worker-start.json","sandbox.json","final.json"]
    paths += [f"cycle-{i:02d}-{s}.json" for i in range(480) for s in ("receipt","status")]
    docs={name:(root/name).read_bytes() for name in paths}
    pins={"plan":sha(docs["plan.json"]),"worker":sha(docs["worker-start.json"]),
          "sandbox":sha(docs["sandbox.json"]),
          "first320_head":prefix["prefix_original_receipt_hash_chain_head"],
          "first320_report":sha(prefix_raw),
          "final":sha(docs["final.json"]),"full_report":sha(full_raw)}
    return docs,(prefix_raw,prefix),(full_raw,full),pins

class Full480Tests(unittest.TestCase):
    def test_true_full_480_synthetic_is_monitor_only(self):
        with tempfile.TemporaryDirectory() as t:
            d,pre,full,pins=fixture(t)
            result=verify(d,pre,full,pins)
            self.assertEqual(result["verified_480_records"],480)
            self.assertEqual(result["source_ready_count"],480)
            self.assertEqual(result["source_hold_count"],0)
            self.assertEqual(result["complete_original_source_snapshots"],480)
            self.assertEqual(result["max_original_source_age_before_next_publication_ms"],16200)
            self.assertTrue(result["full_source_snapshot_SLA_met"])
            self.assertFalse(result["production_monitor_cutover_allowed"])
            self.assertFalse(result["actual_broker_IB_quote_signature_verified"])
            self.assertFalse(result["trade_authority"])
    def test_unpinned_future_final_must_refuse(self):
        with tempfile.TemporaryDirectory() as t:
            d,pre,full,pins=fixture(t)
            with self.assertRaisesRegex(Hold,"NOT_YET_PINNED"):
                verify(d,pre,full,{**PIN,"full_report":None})
    def test_original_terminal_byte_changed(self):
        with tempfile.TemporaryDirectory() as t:
            d,pre,full,pins=fixture(t)
            changed=dict(d);changed["final.json"]+=b" "
            with self.assertRaisesRegex(Hold,"ORIGINAL_FULL_ROOT_HASH_CHANGED"):
                verify(changed,pre,full,pins)
    def test_original_321_status_mutation_refused(self):
        with tempfile.TemporaryDirectory() as t:
            d,pre,full,pins=fixture(t)
            changed=dict(d)
            changed["cycle-320-status.json"]=changed["cycle-320-status.json"].replace(
                b'"USABLE"',b'"PATH_GAP"',1)
            with self.assertRaisesRegex(Hold,"ORIGINAL_HEALTH_BYTES_CHANGED"):
                verify(changed,pre,full,pins)
    def test_original_479_receipt_replacement_refused(self):
        with tempfile.TemporaryDirectory() as t:
            d,pre,full,pins=fixture(t)
            changed=dict(d)
            changed["cycle-479-receipt.json"]+=b" "
            with self.assertRaisesRegex(Hold,"ORIGINAL_TERMINAL_NOT_BOUND_TO_CYCLE"):
                verify(changed,pre,full,pins)
    def test_fake_first320_report_does_not_reanchor_new_480(self):
        with tempfile.TemporaryDirectory() as t:
            d,pre,full,pins=fixture(t)
            bad=dict(pre[1]);bad["contiguous_prefix_cycles_checked"]=479
            raw=c14n(bad)+b"\n"
            with self.assertRaisesRegex(Hold,"INDEPENDENT_PREEXISTING_AUDIT_BYTES_CHANGED"):
                verify(d,(raw,bad),full,pins)
    def test_changed_full_pocket4_report_not_authorization(self):
        with tempfile.TemporaryDirectory() as t:
            d,pre,full,pins=fixture(t)
            bad=dict(full[1]);bad["prod_monitor_cadence_switch_approved"]=True
            raw=c14n(bad)+b"\n"
            with self.assertRaisesRegex(Hold,"INDEPENDENT_PREEXISTING_AUDIT_BYTES_CHANGED"):
                verify(d,pre,(raw,bad),pins)
    def test_missing_orig_health_status_is_not_qualified(self):
        with tempfile.TemporaryDirectory() as t:
            d,pre,full,pins=fixture(t)
            changed=dict(d);changed.pop("cycle-400-status.json")
            with self.assertRaisesRegex(Hold,"MISSING_SOURCE_STATUS_RELABELED_AS_SUCCESS"):
                verify(changed,pre,full,pins)
    def test_numerically_nonfinite_json_refused(self):
        with self.assertRaisesRegex(Hold,"NONFINITE_JSON"):
            parse(b'{"status":NaN}')
    def test_duplicated_json_key_refused(self):
        with self.assertRaisesRegex(Hold,"DUPLICATE_JSON_KEY"):
            parse(b'{"status":1,"status":2}')
    def test_duplicate_archive_original_filename_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"archive.tar.gz"
            import os
            with tarfile.open(path,"w:gz") as tf:
                for n in range(2):
                    b=b'{"original":true}'
                    meta=tarfile.TarInfo("plan.json");meta.size=len(b);meta.mode=0o600
                    tf.addfile(meta,io.BytesIO(b))
            path.chmod(0o600)
            with self.assertRaisesRegex(Hold,"UNTRUSTED_OR_DUPLICATE_ARCHIVE_MEMBER"):
                archive(path)
    def test_symlink_archive_source_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"archive.tar.gz"
            with tarfile.open(path,"w:gz") as tf:
                meta=tarfile.TarInfo("plan.json");meta.type=tarfile.SYMTYPE
                meta.linkname="/etc/passwd";meta.mode=0o600
                tf.addfile(meta)
            path.chmod(0o600)
            with self.assertRaisesRegex(Hold,"UNTRUSTED_OR_DUPLICATE_ARCHIVE_MEMBER"):
                archive(path)

if __name__=="__main__":unittest.main()
