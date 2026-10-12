"""Deterministic offline archive-rollover stress of SHA-verified health cache.

Creates 224 fake IB PAPER quote files under TemporaryDirectory on pocket4.
Never edits any original feed, service, broker, user credentials or trading
policy. The candidate cache is loaded read-only from the existing research tree.
"""
from __future__ import annotations
import hashlib,json,os,stat,time
from pathlib import Path
from unittest.mock import patch
from laya_cell.source_archive import seal, directory
from laya_cell.verified_sealed_readiness_cache import VerifiedSealedReadinessCache
from laya_cell.resident_data import read_quotes
from laya_cell.integrity import ContractError
from laya_cell.tests.test_source_archive import SourceArchiveTests
from laya_cell.integrity import sha_file

def demand(ok,msg):
    if not ok:raise AssertionError(msg)
def digest(b):return hashlib.sha256(b).hexdigest()
def compare(cache,c,at):
    raw,pins=read_quotes(c,at)
    reduced,proofs=cache.read_quotes(c,at)
    demand(raw==reduced,"CACHED_DATA_DIFFERS_FROM_CANONICAL_READER")
    demand(pins==proofs,"CACHED_HASH_PREFIX_DIFFERS")
    return len(raw),len(pins)

def main(output_path):
    start=time.monotonic()
    t=SourceArchiveTests(methodName="test_more_than_128_fails_before_archival_then_reads_all_originals")
    t.setUp()
    evidence=[]
    try:
        c=t.c
        at=int(time.time()*1000)
        initial=t.archive()
        cache=VerifiedSealedReadinessCache()
        for case in ["initial_cold","warm_before_rollover"]:
            observed,pins=compare(cache,c,at)
            demand(observed==166 and pins==11, "INITIAL_QUOTE_COHORT_WRONG")
            evidence.append({"phase":case,"original_rows":observed,
                             "original_pin_count":pins,
                             "cache":cache.counters().copy()})
        # Only synthetic original fixture is changed, never the live PAPER source.
        from laya_cell.shadow_source import canonical as quote_canonical, digest as quote_digest
        original_tail=t.feed/"segment-00000165"/"quotes.jsonl"
        previous_quote=json.loads(original_tail.read_bytes().splitlines()[-1])
        complete_body={"schema":"heptatrader.laya-shadow-quotes.v1",
                       "sequence":2,
                       "previous":previous_quote["sha256"],
                       "at_ms":t.T+165*30000+300,
                       "kind":"END","data":{}}
        with original_tail.open("ab") as endfile:
            endfile.write(quote_canonical(
                {**complete_body,"sha256":quote_digest(complete_body)})+b"\n")
        for i in range(166,192):
            t.make_segment(i,ended=True)
        added=seal(c,8)
        demand(added["pages_added"]==1 and added["sealed_segments"]==192,
               "FIRST_32_SEGMENT_ARCHIVE_ROLLOVER_FAILED")
        for case in ["rollover_promotion","warm_after_rollover"]:
            observed,pins=compare(cache,c,at)
            demand(observed==192 and pins==6,
                   "FIRST_ROLLOVER_CANONICAL_PARITY_FAILED")
            evidence.append({"phase":case,"original_rows":observed,
                             "original_pin_count":pins,
                             "cache":cache.counters().copy()})
        cold=VerifiedSealedReadinessCache()
        cold_rows,cold_pins=compare(cold,c,at)
        demand(cold_rows==192 and cold_pins==6,
               "COLD_RESTART_AFTER_ROLLOVER_PARITY")
        evidence.append({"phase":"cold_restart_after_rollover",
                         "original_rows":cold_rows,"original_pin_count":cold_pins,
                         "cache":cold.counters().copy()})
        # Inject simulated power loss *after* fsynced new page at index 6.
        for i in range(192,224):
            t.make_segment(i,ended=True)
        from laya_cell import source_archive as archive
        original_append=archive._append_checkpoint
        def interrupted_append(root,index,page_sha):
            if index==6: raise OSError("SYNTHETIC_POWER_LOSS_AFTER_PAGE_FSYNC")
            return original_append(root,index,page_sha)
        with patch.object(archive,"_append_checkpoint",side_effect=interrupted_append):
            try:seal(c,8)
            except OSError as exc:
                demand("SYNTHETIC_POWER_LOSS" in str(exc),"UNEXPECTED_SEAL_ERROR")
            else:raise AssertionError("POWER_LOSS_NOT_INJECTED")
        for name,fn in (("canonical",lambda:read_quotes(c,at)),
                        ("warm_cache",lambda:cache.read_quotes(c,at))):
            try:fn()
            except ContractError as exc:
                demand("SOURCE_ARCHIVE_PAGE_CHECKPOINT_COUNT" in str(exc),
                       "ORPHAN_NOT_HELD_CLOSED")
            else:
                raise AssertionError("ORPHAN_READER_ADMITTED:"+name)
        demand(cache.counters()["archive_inventories_full_sha256_reverified"]>=4,
               "CACHE_NOT_REVALIDATING_FULL_LEDGER")
        evidence.append({"phase":"synthetic_uncheckpointed_page",
                         "canonical_status":"REJECT",
                         "cache_status":"REJECT"})
        # Authorized synthetic writer-only recovery of a valid fsynced orphan.
        recovered=seal(c,8)
        demand(recovered["recovered_fsynced_orphan_pages"]==1 and
               recovered["sealed_segments"]==224,
               "VALID_ORPHAN_RECOVERY_FAILED")
        observed,pins=compare(cache,c,at)
        demand(observed==224 and pins==7,"SECOND_ROLLOVER_CANONICAL_PARITY")
        evidence.append({"phase":"writer_only_checkpoint_recovery",
                         "original_rows":observed,"original_pin_count":pins,
                         "cache":cache.counters().copy()})
        # Even after recovery a same-size archived-source modification must
        # invalidate the warm cache before a single affected quote is reused.
        path=t.feed/"segment-00000200"/"quotes.jsonl"
        orig=path.read_bytes()
        pos=orig.find(b"1.125")
        demand(pos>=0,"SYNTHETIC_SOURCE_BODY_NOT_FOUND")
        new=orig[:pos]+b"1.124"+orig[pos+5:]
        demand(len(new)==len(orig),"MUTATION_SIZE_MUST_MATCH")
        path.write_bytes(new)
        for name,fn in (("canonical",lambda:read_quotes(c,at)),
                        ("warm_cache",lambda:cache.read_quotes(c,at))):
            try:fn()
            except ContractError as exc:
                demand("SOURCE_PREFIX_HASH_CHANGED" in str(exc),
                       "INCORRECT_TAMPER_REJECTION")
            else:raise AssertionError("TAMPERED_ORIGINAL_ADMITTED:"+name)
        evidence.append({"phase":"corrupt_same_size_archived_source",
                         "canonical_status":"REJECT",
                         "cache_status":"REJECT"})
        end=time.monotonic()
        report={
            "schema":"heptatrader.fx-verified-cache-isolated-archive-roller-adversarial.v1",
            "experiment":"SYNTHETIC_PAPER_QUOTE_FIXTURE_NO_BROKER",
            "phases":evidence,
            "phases_count":len(evidence),
            "duration_seconds":round(end-start,3),
            "original_archive_test_fixture_source_sha256":sha_file(Path(t.__module__.replace(".","/")) if False else
                Path(__import__("laya_cell.tests.test_source_archive",fromlist=["x"]).__file__)),
            "original_cache_candidate_source_sha256":sha_file(
                Path(__import__("laya_cell.verified_sealed_readiness_cache",fromlist=["x"]).__file__)),
            "source_archive_rollover_32_original_segments_checked":True,
            "cold_restart_exact_orig_quote_parity_checked":True,
            "two_checkpoints_and_power_loss_recovery_checked":True,
            "tampered_source_fails_closed":True,
            "real_ib_source_changed":False,
            "real_product_monitor_changed":False,
            "original_future_cohort_changed":False,
            "upstream_ib_signed_source_attested":False,
            "product_rollout_qualified":False,
            "trade_authority":False,
            "broker_orders_sent":0}
        out=Path(output_path)
        demand(out.is_absolute() and not out.exists() and not out.is_symlink(),
               "NEW_OUTPUT_PATH_REQUIRED")
        out.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        demand(out.parent.stat().st_mode & 0o777==0o700,
               "OUTPUT_PARENT_NOT_PRIVATE")
        fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        raw=json.dumps(report,sort_keys=True,indent=2,allow_nan=False).encode()+b"\n"
        with os.fdopen(fd,"wb") as f:
            f.write(raw);f.flush();os.fsync(f.fileno())
        print(json.dumps({"state":"ISOLATED_CACHE_ARCHIVE_ROLLOVER_FAULT_INJECTION_PASS",
            "phases":len(evidence),"synthetic_final_segments":224,
            "report_sha256":digest(raw),
            "duration_seconds":report["duration_seconds"],
            "real_broker_operations":0,
            "production_rollout_qualified":False}))
    finally:
        t.doCleanups()
if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",required=True,type=Path)
    args=parser.parse_args()
    os.umask(0o077)
    main(args.output)
