"""Only Pocket4 synthetic ORIGINAL-format source fixtures; never Broker IO."""
from __future__ import annotations
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from laya_cell import value343_journal_cache as cache_module
import hashlib
import json
import os
import time
import unittest

from hepta_decision_attribution import canonical,digest
from laya_cell import value343_prospective as watcher
from laya_cell.value343_journal_cache import VerifiedIncrementalJournal
from tests.python.test_value343_realcode_isolated_e2e import OriginalSyntheticFixture


def cache_for(world):
    m={**world.manifest,"instrument":"EUR.USD","execution_domain":"PAPER"}
    return VerifiedIncrementalJournal(m,watcher.COHORT)


class IncrementalOriginalEvidenceTests(unittest.TestCase):
    def test_true_original_pocket4_archive_matches_one_time_and_fast_rescan(self):
        manifest=watcher.registration()
        cache=VerifiedIncrementalJournal(manifest,watcher.COHORT)
        before=time.monotonic()
        first,rids=cache.update()
        cold=time.monotonic()-before
        byte_count=cache.incremental_bytes
        count=cache.verified_new_records
        before=time.monotonic()
        unchanged,again=cache.update()
        warm=time.monotonic()-before
        original,source=watcher.original_events(manifest)
        self.assertEqual(len(first),len(original))
        self.assertEqual(len(rids),4432)
        self.assertEqual(byte_count,cache.incremental_bytes)
        self.assertEqual(count,cache.verified_new_records)
        self.assertIs(first,unchanged)
        self.assertIs(rids,again)
        self.assertTrue(cache.assert_full_replay_same(original,source)["full_source_consistent"])
        self.assertLess(warm,2.0)
        print("ACTUAL_POCKET4_ORIGINAL_SCAN",
              {"cold_ms":round(cold*1000,2),"unchanged_ms":round(warm*1000,3),
               "events":len(first),"requests":len(rids)},flush=True)

    def test_two_original_appends_and_new_response_need_only_suffix(self):
        with TemporaryDirectory() as td:
            world=OriginalSyntheticFixture(Path(td))
            cache=cache_for(world)
            first,requests=cache.update()
            self.assertEqual((len(first),len(requests)),(1,0))
            initial_bytes=cache.incremental_bytes
            r=world.request("b"*32)
            original=world.append("REQUEST",r)
            events,req=cache.update()
            self.assertEqual(len(events),2)
            self.assertIn(r["request_id"],req)
            self.assertEqual(req[r["request_id"]]["sha256"],original["sha256"])
            self.assertEqual(req[r["request_id"]]["_witness"]["sha256"],
                 hashlib.sha256(world.file.read_bytes()).hexdigest())
            self.assertGreater(cache.incremental_bytes,initial_bytes)
            old_size=cache.incremental_bytes
            world.response(r)
            ev2,req2=cache.update()
            self.assertEqual(len(ev2),3)
            self.assertEqual(len(req2),1)
            self.assertGreater(cache.incremental_bytes,old_size)
            self.assertTrue(cache.assert_full_replay_same(
                *watcher.original_events({**world.manifest,"instrument":"EUR.USD",
                                          "execution_domain":"PAPER"}))["full_source_consistent"])

    def test_incomplete_original_json_append_does_not_admit_a_prediction(self):
        with TemporaryDirectory() as td:
            world=OriginalSyntheticFixture(Path(td))
            cache=cache_for(world)
            cache.update()
            r=world.request("b"*32)
            now=int(time.time()*1000)
            body={"schema":"heptatrader.exact-decision-attribution.v1",
                  "producer_id":world.producer_id,
                  "sequence":world.next,"previous":world.previous,
                  "dropped_total":0,"written_at_ms":now,
                  "captured_at_ms":now,"kind":"REQUEST","data":r}
            body["sha256"]=digest(body)
            raw=(canonical(body)+"\n").encode()
            cut=len(raw)//2
            with world.file.open("ab") as f:
                f.write(raw[:cut]);f.flush();os.fsync(f.fileno())
            self.assertEqual(len(cache.update()[1]),0)
            with world.file.open("ab") as f:
                f.write(raw[cut:]);f.flush();os.fsync(f.fileno())
            ev,req=cache.update()
            self.assertEqual(len(req),1)
            self.assertEqual(len(ev),2)
            self.assertEqual(req[r["request_id"]]["sha256"],body["sha256"])

    def test_same_size_rewrite_and_truncate_fail_closed(self):
        for cut in (False,True):
            with self.subTest(truncate=cut),TemporaryDirectory() as td:
                world=OriginalSyntheticFixture(Path(td))
                cache=cache_for(world)
                cache.update()
                if cut:
                    with world.file.open("rb+") as f:f.truncate(max(1,world.file.stat().st_size-1))
                    with self.assertRaisesRegex(ValueError,"ROTATED_OR_TRUNCATED"):
                        cache.update()
                else:
                    with world.file.open("rb+") as f:
                        b=f.read();self.assertIn(b"HEADER",b)
                        f.seek(0);f.write(b.replace(b"HEADER",b"FOOTER",1))
                        f.flush();os.fsync(f.fileno())
                    with self.assertRaisesRegex(ValueError,"SAME_SIZE_MODIFIED"):
                        cache.update()

    def test_periodic_complete_replay_accepts_only_verified_new_suffix(self):
        with TemporaryDirectory() as td:
            world=OriginalSyntheticFixture(Path(td))
            cache=cache_for(world)
            cache.update()
            original,requests=watcher.original_events(
                {**world.manifest,"instrument":"EUR.USD","execution_domain":"PAPER"})
            request=world.request("b"*32)
            world.append("REQUEST",request)  # arrived after full scan snapshot
            cache.update()
            with self.assertRaisesRegex(ValueError,"COUNT_DIFFERENCE"):
                cache.assert_full_replay_same(original,requests)
            valid=cache.assert_full_replay_same(
                original,requests,allow_new_incremental_suffix=True)
            self.assertTrue(valid["full_source_consistent"])
            self.assertEqual(valid["verified_extra_new_events"],1)
            tampered=list(original)
            forged=dict(tampered[0]);forged["sha256"]="f"*64
            with self.assertRaisesRegex(ValueError,"CHAIN_DIFFERENCE"):
                cache.assert_full_replay_same(
                    [forged],requests,allow_new_incremental_suffix=True)

    def test_true_original_source_chain_tamper_is_rejected(self):
        with TemporaryDirectory() as td:
            world=OriginalSyntheticFixture(Path(td))
            cache=cache_for(world)
            cache.update()
            r=world.request("b"*32)
            now=int(time.time()*1000)
            wrong={"schema":"heptatrader.exact-decision-attribution.v1",
                   "producer_id":world.producer_id,"sequence":world.next,
                   "previous":"bad"*21+"b","dropped_total":0,
                   "written_at_ms":now,"captured_at_ms":now,
                   "kind":"REQUEST","data":r}
            wrong["sha256"]=digest(wrong)
            with world.file.open("ab") as f:
                f.write((canonical(wrong)+"\n").encode())
            with self.assertRaisesRegex(ValueError,"CHAIN_BREAK"):
                cache.update()

    def test_concurrent_legitimate_original_append_between_stat_and_open(self):
        with TemporaryDirectory() as td:
            world=OriginalSyntheticFixture(Path(td))
            cache=cache_for(world)
            cache.update()
            request=world.request("b"*32)
            world.append("REQUEST",request)
            genuine_stat=cache_module.private_stat
            triggered=[]
            def timed_stat(path):
                item=genuine_stat(path)
                if not triggered and str(path)==str(world.file):
                    # A complete native RESPONSE arrives after stat, while
                    # the observer is still opening its snapshot FD.
                    world.response(request)
                    triggered.append(True)
                return item
            with patch.object(cache_module,"private_stat",side_effect=timed_stat):
                events,requests=cache.update()
            self.assertEqual(len(triggered),1)
            self.assertEqual(len(events),2)  # REQUEST prefix captured
            self.assertEqual(len(requests),1)
            events,requests=cache.update()
            self.assertEqual(len(events),3)  # RESPONSE append safely recovered
            self.assertTrue(cache.assert_full_replay_same(
                *watcher.original_events({**world.manifest,"instrument":"EUR.USD",
                                          "execution_domain":"PAPER"}))['full_source_consistent'])

    def test_new_complete_segment_keeps_cross_file_chain(self):
        with TemporaryDirectory() as td:
            world=OriginalSyntheticFixture(Path(td))
            cache=cache_for(world)
            cache.update()
            world.file=world.dir/(world.producer_id+"-0001.jsonl")
            world.append("HEADER",{"manifest_sha256":watcher.COHORT,
                                   "instrument":"EUR.USD",
                                   "period_sha256":world.period,
                                   "execution_domain":"PAPER"})
            r=world.request("c"*32)
            world.append("REQUEST",r)
            ev,req=cache.update()
            self.assertEqual(len(ev),3)
            self.assertEqual(len(req),1)
            self.assertTrue(cache.assert_full_replay_same(
                *watcher.original_events({**world.manifest,"instrument":"EUR.USD",
                                          "execution_domain":"PAPER"}))["full_source_consistent"])

    def test_source_inotify_and_incremental_run_once_preserve_original_proofs(self):
        with TemporaryDirectory() as td:
            world=OriginalSyntheticFixture(Path(td))
            root=Path(td)/"research"
            root.mkdir(mode=0o700)
            for n in ("predictions","durable_seals","misses","response_proofs","scores","rejects"):
                (root/n).mkdir(mode=0o700)
            manifest={**world.manifest,"instrument":"EUR.USD","execution_domain":"PAPER"}
            cache=VerifiedIncrementalJournal(manifest,watcher.COHORT)
            original_model=watcher.read_model()
            freeze={"activated_at_ms":int(time.time()*1000)-15000,
                    "model_report_id":original_model[0]["report_id"],
                    "source_period_sha256":world.period}
            r=world.request("c"*32)
            req_id=r["request_id"]
            with patch.object(watcher,"OUT",root),\
                 patch.object(watcher,"registration",return_value=manifest),\
                 patch.object(watcher,"frozen",return_value=freeze):
                watcher.run_once(cache)
                world.append("REQUEST",r)
                watcher.run_once(cache)
                self.assertTrue((root/"predictions"/(req_id+".json")).exists())
                self.assertFalse((root/"response_proofs"/(req_id+".json")).exists())
                time.sleep(.004)
                world.response(r)
                watcher.run_once(cache)
                proof=json.loads((root/"response_proofs"/(req_id+".json")).read_bytes())
                self.assertEqual(proof["status"],"VERIFIED_PRE_ORIGINAL_RESPONSE")
                self.assertEqual(len(list((root/"scores").glob("*.json"))),0)

    def test_new_cache_source_pin_enforced_by_frozen_program_identity(self):
        with patch.object(watcher,"CACHE_CODE_FILE",Path("/nonexistent/source")):
            with self.assertRaises(Exception):
                watcher.frozen()


if __name__=="__main__":
    unittest.main()
