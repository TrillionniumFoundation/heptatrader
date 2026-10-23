"""Pocket4 no-Broker original-journal tests for prediction FSYNC vs RESPONSE race.

Original model and money ledger are read-only. Every request and response used
here is a clearly SYNTHETIC isolated scratch fixture, not a market event.
"""
from __future__ import annotations
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import os
import time
import unittest

from laya_cell import value343_prospective as shadow
from test_value343_realcode_isolated_e2e import OriginalSyntheticFixture


def setup(private):
    world=OriginalSyntheticFixture(Path(private))
    folder=Path(private)/"research"
    folder.mkdir(mode=0o700)
    for sub in ("predictions","durable_seals","misses","response_proofs",
                "scores","rejects"):
        (folder/sub).mkdir(mode=0o700)
    request=world.request("a"*32)
    world.append("REQUEST",request)
    original,requests=shadow.original_events(world.manifest)
    frozen={"activated_at_ms":request["started_at_ms"]-100,
            "model_report_id":"TEST_ONLY_FROZEN_SOURCE",
            "source_period_sha256":world.period}
    return world,folder,request,original,requests,frozen


class DurablePreResponseSealTests(unittest.TestCase):
    def test_real_fsync_then_response_qualifies_with_same_original_journal(self):
        with TemporaryDirectory() as td:
            world,root,req,ev,requests,freeze=setup(td)
            rid=req["request_id"]
            with patch.object(shadow,"OUT",root):
                result=shadow.capture(ev,requests,freeze,world.fake_model())
                self.assertEqual(result["new_valid_predictions"],1)
                filename=root/"predictions"/(rid+".json")
                prediction=json.loads(filename.read_bytes())
                proof=shadow.read_durable_seal(filename,prediction)
                self.assertIsNotNone(proof)
                self.assertGreaterEqual(
                    proof[0]["durable_fsync_returned_at_ns"],
                    filename.stat().st_ctime_ns)
                self.assertGreaterEqual(
                    proof[0]["durable_fsync_returned_at_ns"],
                    prediction["sealed_at_ms"]*1000000)
                # The synthetic original RESPONSE truly comes after
                # persistence has been confirmed.
                time.sleep(.010)
                response=world.response(req)
                original,requests=shadow.original_events(world.manifest)
                att=shadow.attest_responses(original,requests,freeze)
                self.assertEqual(att["verified_new"],1)
                response_proof=json.loads(
                    (root/"response_proofs"/(rid+".json")).read_bytes())
                self.assertEqual(response_proof["durable_seal_sha256"],proof[1])
                self.assertLess(proof[0]["durable_fsync_returned_at_ns"],
                                response["data"]["available_at_ms"]*1000000)
                self.assertFalse(response_proof["model_promotion"])

    def test_response_during_fsync_not_qualified_by_early_sealed_at_ms(self):
        with TemporaryDirectory() as td:
            world,root,req,ev,requests,freeze=setup(td)
            rid=req["request_id"]
            original_write=shadow.write_once
            responses=[]
            def delayed_write(path,body):
                result=original_write(path,body)
                if path.parent.name=="predictions":
                    # Emulate journal writer's response while the observer
                    # is still inside the write_once() API. It has not yet
                    # returned a proven post-fsync time.
                    time.sleep(.011)
                    responses.append(world.response(req))
                    time.sleep(.003)
                return result
            with patch.object(shadow,"OUT",root),\
                 patch.object(shadow,"write_once",side_effect=delayed_write):
                self.assertEqual(shadow.capture(ev,requests,freeze,
                                                 world.fake_model())[
                                                 "new_valid_predictions"],1)
            assert len(responses)==1
            predicted=json.loads((root/"predictions"/(rid+".json")).read_bytes())
            seal=json.loads((root/"durable_seals"/(rid+".json")).read_bytes())
            self.assertLess(predicted["sealed_at_ms"],
                            responses[0]["data"]["available_at_ms"])
            self.assertGreater(seal["durable_fsync_returned_at_ns"],
                               responses[0]["data"]["available_at_ms"]*1000000)
            recorded,requests=shadow.original_events(world.manifest)
            with patch.object(shadow,"OUT",root):
                outcome=shadow.attest_responses(recorded,requests,freeze)
                self.assertEqual(outcome["censored_new"],1)
                self.assertFalse(list((root/"response_proofs").glob("*.json")))
                denied=json.loads((root/"rejects"/(rid+".json")).read_bytes())
                self.assertEqual(denied["status"],
                    "CENSORED_SEAL_NOT_PROVEN_BEFORE_ORIGINAL_RESPONSE")
                self.assertFalse(denied["score_valid"])

    def test_process_crash_after_prediction_fsync_before_proof_censors(self):
        with TemporaryDirectory() as td:
            world,root,req,ev,requests,freeze=setup(td)
            rid=req["request_id"]
            original_write=shadow.write_once
            def crash_after_prediction(path,body):
                if path.parent.name=="durable_seals":
                    raise RuntimeError("SIMULATED_TEST_ONLY_PROCESS_CRASH")
                return original_write(path,body)
            with patch.object(shadow,"OUT",root),\
                 patch.object(shadow,"write_once",side_effect=crash_after_prediction):
                with self.assertRaisesRegex(RuntimeError,"SIMULATED_TEST_ONLY"):
                    shadow.capture(ev,requests,freeze,world.fake_model())
            self.assertTrue((root/"predictions"/(rid+".json")).exists())
            self.assertFalse(list((root/"durable_seals").glob("*.json")))
            time.sleep(.005)
            world.response(req)
            events,requests=shadow.original_events(world.manifest)
            with patch.object(shadow,"OUT",root):
                out=shadow.attest_responses(events,requests,freeze)
                self.assertEqual(out["censored_new"],1)
                self.assertEqual(json.loads((root/"rejects"/(rid+".json")).read_bytes())["status"],
                                 "CENSORED_MISSING_DURABLE_FSYNC_WITNESS")
                self.assertFalse(list((root/"response_proofs").glob("*.json")))

    def test_submillisecond_response_also_fails_post_fsync_ns(self):
        with TemporaryDirectory() as td:
            world,root,req,ev,requests,freeze=setup(td)
            rid=req["request_id"]
            with patch.object(shadow,"OUT",root):
                shadow.capture(ev,requests,freeze,world.fake_model())
                p=json.loads((root/"durable_seals"/(rid+".json")).read_bytes())
                # response original ms is rounded down, which must be
                # compared against the full (more precise) fsync ns.
                time.sleep(.006)
                response=world.response(req)
                response["data"]["available_at_ms"]=p["durable_fsync_returned_at_ns"]//1000000
                events,requests=shadow.original_events(world.manifest)
                # Only use this modified response for isolated adversarial
                # timestamp test, never alter the original journal bytes.
                events=[response if x["kind"]=="RESPONSE" else x for x in events]
                out=shadow.attest_responses(events,requests,freeze)
                self.assertEqual(out["censored_new"],1)
                self.assertFalse(list((root/"response_proofs").glob("*.json")))

    def test_fsync_also_must_finish_before_fixed_request_deadline(self):
        with TemporaryDirectory() as td:
            world,root,req,ev,requests,freeze=setup(td)
            rid=req["request_id"]
            with patch.object(shadow,"OUT",root):
                shadow.capture(ev,requests,freeze,world.fake_model())
                # The real sealed receipt still came from a valid 90s
                # source. Test admission defensively recomputes expiry
                # using the DURABLE ns time, not its pre-write clock.
                with patch.object(shadow,"MAX_REQUEST_AGE_MS",500):
                    time.sleep(.007)
                    world.response(req)
                    actual,requests=shadow.original_events(world.manifest)
                    out=shadow.attest_responses(actual,requests,freeze)
                    self.assertEqual(out["censored_new"],1)
                    self.assertFalse((root/"response_proofs"/(rid+".json")).exists())

    def test_corrupted_commit_sha_fails_without_qualification(self):
        with TemporaryDirectory() as td:
            world,root,req,ev,requests,freeze=setup(td)
            rid=req["request_id"]
            with patch.object(shadow,"OUT",root):
                shadow.capture(ev,requests,freeze,world.fake_model())
                path=root/"durable_seals"/(rid+".json")
                obj=json.loads(path.read_bytes())
                obj["prediction_sha256"]="f"*64
                path.unlink()  # only delete synthetic disposable fixture
                shadow.write_once(path,obj)
                with self.assertRaisesRegex(ValueError,
                        "DURABLE_FSYNC_RECEIPT_IDENTITY_OR_TIME_CHANGED"):
                    shadow.read_durable_seal(root/"predictions"/(rid+".json"),
                        json.loads((root/"predictions"/(rid+".json")).read_bytes()))

    def test_reader_with_no_original_response_remains_provisional(self):
        with TemporaryDirectory() as td:
            world,root,req,ev,requests,freeze=setup(td)
            with patch.object(shadow,"OUT",root):
                shadow.capture(ev,requests,freeze,world.fake_model())
                out=shadow.attest_responses(ev,requests,freeze)
                self.assertEqual(out["still_no_response"],1)
                self.assertEqual(out["verified_new"],0)
                self.assertEqual(len(list((root/"response_proofs").glob("*.json"))),0)


if __name__=="__main__":
    unittest.main()
