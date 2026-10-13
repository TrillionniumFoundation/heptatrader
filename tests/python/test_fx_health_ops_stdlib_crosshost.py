"""Host-independent adversarial rechecks for exact source-cache 24-cycle run."""
import copy,hashlib,json,os
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from train_v2 import fx_health_ops_stdlib_crosshost as x
except (ModuleNotFoundError, ImportError):
    import fx_health_ops_stdlib_crosshost as x
ROOT=Path(os.environ.get("HT_FX_SOURCE_CACHE_AUDIT_DIR",
         str(Path.home()/".local/state/heptatrader-health-real24-20261013")))

def raw_files():
    return {name:(ROOT/name).read_bytes() for name in x.PINS}
def unique_resign(changed):
    # Test a fully self-consistent forged provenance chain. The real verifier
    # does NOT permit self-signing; this patch is test-only hostile behavior.
    r=x.strict(changed["independent-operational-audit.json"])
    r["live_source_readiness_bytes_sha256"]=x.digest(changed["readiness.json"])
    r["actual_journal_original_bytes_sha256"]=x.digest(changed["original-journal.txt"])
    r["precommitted_plan_sha256"]=x.digest(changed["plan.json"])
    changed["independent-operational-audit.json"]=x.canonical(r)+bytes([10])
    return {name:hashlib.sha256(raw).hexdigest() for name,raw in changed.items()}
def mutate(contents,name,f):
    doc=x.strict(contents[name])
    f(doc)
    contents[name]=x.canonical(doc)+b"\n"

class PhysicalOriginalAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ROOT.is_dir():
            raise unittest.SkipTest("Private pinned source-cache 24-cycle archive is not mounted")
    def test_original_reproduction_not_model_or_broker_authority(self):
        result=x.verify_bytes(raw_files())
        self.assertEqual(result["actual_verified_original_cycles"],24)
        self.assertEqual(result["actual_max_asof_age_pre_next_pub_ms"],21907)
        self.assertEqual(result["original_archive_complete_rehashes"],24)
        self.assertEqual(result["cache_hit_original_rehashes"],5888)
        self.assertFalse(result["product_rollout_qualified"])
        self.assertFalse(result["hardware_custody_timestamp_or_native_root_proven"])
    def test_signed_after_event_forgery_cannot_override_pinned_hashes(self):
        c=raw_files()
        mutate(c,"plan.json",lambda p:p.update(cpu_quota_percent=50))
        with self.assertRaisesRegex(x.AuditHold,"FROZEN_SOURCE_FILE_HASH_MISMATCH"):
            x.verify_bytes(c)
    def test_fake_success_when_22_cycles_refused_even_if_self_rehashed(self):
        c=raw_files()
        rows=c["original-journal.txt"].splitlines(keepends=True)
        filtered=[r for r in rows if b'"cycle": 13,' not in r]
        c["original-journal.txt"]=b"".join(filtered)
        with patch.dict(x.PINS,unique_resign(c)):
            with self.assertRaisesRegex(x.AuditHold,"NOT_24_CYCLES"):
                x.verify_bytes(c)
    def test_negative_cache_complete_hash_counts_self_reissued_refused(self):
        c=raw_files()
        raw=c["original-journal.txt"]
        lines=raw.splitlines(keepends=True)
        idx=next(i for i,r in enumerate(lines) if b"readiness-verified-sealed-cache-counters" in r)
        counter=x.strict(lines[idx]);counter["sealed_hits_bytes_reverified_at_use"]=0
        lines[idx]=x.canonical(counter)+b"\n"
        c["original-journal.txt"]=b"".join(lines)
        with patch.dict(x.PINS,unique_resign(c)):
            with self.assertRaisesRegex(x.AuditHold,"ARCHIVE_SHA_CACHE_REVALIDATION_INVALID"):
                x.verify_bytes(c)
    def test_hidden_future_period_claim_rejected(self):
        c=raw_files()
        mutate(c,"readiness.json",lambda p:p.update(period_sha256="0"*64))
        with patch.dict(x.PINS,unique_resign(c)):
            with self.assertRaisesRegex(x.AuditHold,"ORIGINAL_24PROBE_PERIOD_OR_GUARD_CHANGED"):
                x.verify_bytes(c)
    def test_forged_24probe_denom_rejected(self):
        c=raw_files()
        mutate(c,"readiness.json",lambda p:p["probes"][0].update(status="PATH_GAP"))
        with patch.dict(x.PINS,unique_resign(c)):
            with self.assertRaisesRegex(x.AuditHold,"ORIGINAL_24PROBE_PERIOD_OR_GUARD_CHANGED"):
                x.verify_bytes(c)
    def test_fake_freshness_with_same_time_but_wrong_result_refused(self):
        c=raw_files()
        mutate(c,"independent-operational-audit.json",
            lambda p:p.update(worst_age_before_next_publish_ms=123))
        with patch.dict(x.PINS,unique_resign(c)):
            with self.assertRaisesRegex(x.AuditHold,"INDEPENDENT_FRESHNESS_SLA_NOT_REPRODUCED"):
                x.verify_bytes(c)
    def test_reversed_asof_clock_refused(self):
        c=raw_files()
        raw=c["original-journal.txt"]
        lines=raw.splitlines(keepends=True)
        ix=next(i for i,r in enumerate(lines) if b'"cycle": 14,' in r)
        obj=x.strict(lines[ix]);obj["source_asof_ms"]-=600000
        obj["published_source_age_ms"]=obj["published_ms"]-obj["source_asof_ms"]
        obj["assess_ms"]=obj["published_source_age_ms"]
        lines[ix]=x.canonical(obj)+b"\n"
        c["original-journal.txt"]=b"".join(lines)
        with patch.dict(x.PINS,unique_resign(c)):
            with self.assertRaisesRegex(x.AuditHold,"ORIGINAL_ASOF_PUBLISH_TIME_INVALID"):
                x.verify_bytes(c)
    def test_bad_source_age_claim_rejected(self):
        c=raw_files()
        mutate(c,"readiness.json",lambda p:p.update(latest_quote_age_ms=100000))
        with patch.dict(x.PINS,unique_resign(c)):
            with self.assertRaisesRegex(x.AuditHold,"ORIGINAL_SOURCE_PREFIX_OR_QUOTE_FRESHNESS_INVALID"):
                x.verify_bytes(c)
    def test_model_promotion_claim_refused(self):
        c=raw_files()
        mutate(c,"independent-operational-audit.json",
               lambda p:p.update(ready_for_unattended_production_cutover=True))
        with patch.dict(x.PINS,unique_resign(c)):
            with self.assertRaisesRegex(x.AuditHold,"PRIOR_SOURCE_REPLAY_NOT_BINDING"):
                x.verify_bytes(c)
    def test_duplicate_json_keys_refused(self):
        with self.assertRaisesRegex(x.AuditHold,"DUPLICATE_JSON_KEY"):
            x.strict(b'{"state":"HOLD","state":"READY"}')
    def test_nan_json_refused(self):
        with self.assertRaisesRegex(x.AuditHold,"NONFINITE_JSON"):
            x.strict(b'{"state":NaN}')

if __name__=="__main__":unittest.main()
