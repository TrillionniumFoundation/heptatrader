"""Isolated real-code source-chain -> Value prediction -> RESPONSE -> OMS scoring.

All source files and market values are SYNTHETIC TEST fixtures in owner-private
temporary directories on Pocket4. They are NEVER real or historical outcomes.
The test invokes production research parser, sealed model/control and original
economic recomputation, but cannot access/order through a broker.
"""
from __future__ import annotations
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import hashlib
import json
import os
import time
import unittest

import numpy as np
from hepta_decision_attribution import canonical,digest
from laya_cell import value343_prospective as s
from laya_cell.value343_oos import FEATURES


class OriginalSyntheticFixture:
    def __init__(self,root):
        self.root=Path(root)
        self.root.mkdir(mode=0o700,exist_ok=True)
        self.dir=self.root/"original"
        self.dir.mkdir(mode=0o700)
        self.file=self.dir/("d"*32+"-0000.jsonl")
        self.next=0
        self.previous="0"*64
        self.producer_id="d"*32
        self.period="e"*64
        self.manifest={"journal_directory":str(self.dir),
                       "period_sha256":self.period}
        self.append("HEADER",{"manifest_sha256":s.COHORT,
                              "instrument":"EUR.USD",
                              "period_sha256":self.period,
                              "execution_domain":"PAPER",
                              "trading_authority":False})
    def append(self,kind,data):
        now=int(time.time()*1000)
        body={"schema":"heptatrader.exact-decision-attribution.v1",
              "producer_id":self.producer_id,
              "sequence":self.next,"previous":self.previous,
              "dropped_total":0,"written_at_ms":now,
              "captured_at_ms":now,"kind":kind,"data":data}
        body["sha256"]=digest(body)
        raw=(canonical(body)+"\n").encode()
        with self.file.open("ab") as f:
            f.write(raw);f.flush();os.fsync(f.fileno())
        self.file.chmod(0o600)
        self.next+=1
        self.previous=body["sha256"]
        return body

    def request(self,id=None):
        now=int(time.time()*1000)
        start=now-1000
        ident=id or "a"*32
        # 24 fully causal pre-request EUR/USD original quotes
        quotes=[]
        for k in range(24):
            mid=1.12000+k*0.000005
            quotes.append({"bid":round(mid-.000025,9),
                           "ask":round(mid+.000025,9),
                           "mid":round(mid,9),
                           "observed_at_ms":start-(24-k)*110})
        prompt="SYNTHETIC FIXTURE ONLY: predict EUR/USD direction"
        request={"request_id":ident,"instrument":"EUR.USD",
                 "period_sha256":self.period,
                 "model_id":s.EXPECTED_MODEL,
                 "model_controls":{},
                 "prompt":prompt,
                 "prompt_sha256":hashlib.sha256(prompt.encode()).hexdigest(),
                 "history_snapshot":quotes,"position_at_request":0.0,
                 "started_at_ms":start,
                 "input_scope":"EXACT_LOCAL_INFER_CLI_REQUEST_NOT_UNOBSERVED_PROVIDER_INTERNAL_CONTEXT"}
        return request

    def response(self,request):
        now=int(time.time()*1000)
        return self.append("RESPONSE",{"request_id":request["request_id"],
                    "request_sha256":digest(request),
                    "returncode":0,"available_at_ms":now,
                    "stdout":"SYNTHETIC FIXTURE, not a real model call"})

    def decision_and_binding(self,request):
        now=int(time.time()*1000)
        decision=self.append("DECISION",{"request_id":request["request_id"],
                    "request_sha256":digest(request),
                    "response_sha256":"f"*64,
                    "decision_sha256":"c"*64,
                    "available_at_ms":now})
        lastmid=request["history_snapshot"][-1]["mid"]
        qty=int(100000/lastmid)
        command={"command_id":"fixture-original-open-"+request["request_id"],
                 "kind":"OPEN","created_at_ms":int(time.time()*1000),
                 "instrument":"EUR.USD","side":"BUY",
                 "position_before_send":"0","reference_price":str(lastmid),
                 "quantity_mode":"BASE_NOTIONAL_TARGET",
                 "target_notional_usd":"100000","order_quantity":str(qty)}
        binding={"request_id":request["request_id"],
                 "request_sha256":digest(request),
                 "response_sha256":"f"*64,
                 "decision_sha256":"c"*64,
                 "decision_available_at_ms":now,
                 "command":command,
                 "state_write_succeeded":True,
                 "basis":"IDENTICAL_RETURNED_OBJECT_AT_ORIGINAL_DURABLE_PREPARED"}
        self.append("COMMAND_BINDING",binding)
        return decision,binding

    def economics(self,request,binding):
        q=Decimal(binding["command"]["order_quantity"])
        p0=Decimal("1.120000")
        p1=Decimal("1.120125")
        fee=Decimal("2")
        gross=q*(p1-p0)
        net=gross-2*fee
        eid1="TEST.SYNTHETIC.OPEN."+request["request_id"]
        eid2="TEST.SYNTHETIC.CLOSE."+request["request_id"]
        original=binding["command"]["command_id"]
        close="fixture-original-close-"+request["request_id"]
        executions=[
            {"command_id":original,"execution_id":eid1,"quantity":str(q),
             "price":str(p0),"commission":str(fee),
             "commission_currency":"USD","side":"BUY"},
            {"command_id":close,"execution_id":eid2,"quantity":str(q),
             "price":str(p1),"commission":str(fee),
             "commission_currency":"USD","side":"SELL"}
        ]
        reward={"input_id":digest(request),
                "input_attribution":{"producer_manifest_sha256":s.COHORT,
                     "request_id":request["request_id"],
                     "prompt_sha256":request["prompt_sha256"]},
                "behavior_model_id":request["model_id"],
                "instrument":"EUR.USD","currency":"USD",
                "open_command_id":original,"executions":executions,
                "realized_gross_usd":str(gross),
                "actual_commissions_usd":str(2*fee),
                "realized_net_usd":str(net),
                "learning_eligible":True,"exact_input_bound":True,
                "settlement_complete":True,"fees_complete":True,
                "flat_to_flat_executed_quantity":True,
                "policy_gradient_allowed":False,"laya_on_policy":False,
                "effect_authority":False,"unsettled_commands":[],
                "learning_exclusion_reasons":[]}
        reward["reward_id"]=digest(reward)
        oms=self.root/"synthetic-oms.jsonl"
        t=int(time.time()*1000)
        with oms.open("w") as stream:
            for eid,price in ((eid1,p0),(eid2,p1)):
                for kind in ("broker_execution","broker_commission"):
                    event={"event":kind,"broker_execution_id":eid,
                           "ts_ms":t,"execution_domain":"PAPER",
                           "venue":"IB","instrument":"EUR.USD"}
                    if kind=="broker_execution":
                        event["broker_econ_execution_price"]=str(price)
                    else:
                        event["broker_econ_commission_currency"]="USD"
                        event["broker_econ_commission"]=str(fee)
                    stream.write(canonical(event)+"\n")
        oms.chmod(0o600)
        snapshot=self.root/"synthetic-rewards.json"
        snapshot.write_text(canonical({"broker_orders_sent":0,
                                      "rewards":[reward]}))
        snapshot.chmod(0o600)
        return oms,snapshot,net

    @staticmethod
    def fake_model():
        # 18 intercept/features, deliberately NO learned profits from any
        # response, observed price, realized PnL or other future source.
        w=np.zeros(len(FEATURES)+1,dtype=np.float64)
        w[0]=-.1
        return ({"report_id":"TEST_ONLY_FROZEN_SOURCE"},w,
                np.zeros(len(FEATURES),dtype=np.float64),
                np.ones(len(FEATURES),dtype=np.float64),-12.0)


class OriginalJournalEndToEnd(unittest.TestCase):
    def test_all_phases_with_actual_original_code_and_fee_verification(self):
        with TemporaryDirectory() as temporary:
            world=OriginalSyntheticFixture(Path(temporary))
            research=Path(temporary)/"research"
            research.mkdir(mode=0o700)
            for sub in ("predictions","misses","response_proofs","scores","rejects"):
                (research/sub).mkdir(mode=0o700)
            request=world.request()
            record=world.append("REQUEST",request)
            actual_events,requests=s.original_events(world.manifest)
            self.assertEqual(len(requests),1)
            rid=request["request_id"]
            fixed={"activated_at_ms":request["started_at_ms"]-100,
                   "model_report_id":"TEST_ONLY_FROZEN_SOURCE",
                   "source_period_sha256":world.period}
            with patch.object(s,"OUT",research):
                result=s.capture(actual_events,requests,fixed,world.fake_model())
                self.assertEqual(result["new_valid_predictions"],1)
                pred=research/"predictions"/(rid+".json")
                value=json.loads(pred.read_bytes())
                self.assertEqual(set(value["predictions_after_actual_reported_fees_usd"]),
                                 {"BUY","SELL"})
                # The original REQUEST has not received a RESPONSE.
                self.assertEqual(s.attest_responses(actual_events,requests,fixed)
                                 ["still_no_response"],1)
                time.sleep(.005)
                response=world.response(request)
                actual_events,requests=s.original_events(world.manifest)
                verified=s.attest_responses(actual_events,requests,fixed)
                self.assertEqual(verified["verified_new"],1)
                response_proof=json.loads(
                    (research/"response_proofs"/(rid+".json")).read_bytes())
                self.assertEqual(response_proof["original_response_receipt_sha256"],
                                 response["sha256"])
                self.assertGreater(response_proof["original_response_available_at_ms"],
                                   value["sealed_at_ms"])
                # No realized future PnL label exists yet: this is not a score.
                early=world.root/"pre-settlement-empty-rewards.json"
                early.write_text(canonical({"broker_orders_sent":0,"rewards":[]}))
                early.chmod(0o600)
                with patch.object(s.prior,"REWARDS",early):
                    status=s.score(actual_events,requests,fixed)
                    self.assertEqual(status["pending"],1)
                    self.assertEqual(status["new_scores"],0)
                time.sleep(.005)
                decision,binding=world.decision_and_binding(request)
                oms,rewards,net=world.economics(request,binding)
                events,requests=s.original_events(world.manifest)
                with patch.object(s.prior,"REWARDS",rewards),\
                     patch.object(s.prior,"OMS",oms):
                    scored=s.score(events,requests,fixed)
                    self.assertEqual(scored["new_scores"],1)
                    score=json.loads((research/"scores"/(rid+".json")).read_bytes())
                    self.assertEqual(Decimal(str(score["actual_net_after_reported_fees_usd"])),
                                     net)
                    self.assertEqual(score["original_side"],"BUY")
                    self.assertTrue(score["score_valid"])
                    self.assertFalse(score["automatic_trading"])
                    self.assertEqual(score["model_prediction_net_usd"],-10.)
                    self.assertEqual(score["training_median_prediction_net_usd"],-12.)
                    score_bytes=(research/"scores"/(rid+".json")).read_bytes()
                    self.assertEqual(s.score(events,requests,fixed)["new_scores"],0)
                    self.assertEqual(score_bytes,
                         (research/"scores"/(rid+".json")).read_bytes())

    def test_unsafe_original_id_fails_before_any_prediction(self):
        for bad in ("../private/secret","0"*31+"/","A"*32,"0"*31+"g",
                    ".."*16,"",123,None):
            with self.subTest(bad=repr(bad)),TemporaryDirectory() as tmp:
                world=OriginalSyntheticFixture(Path(tmp))
                request=world.request("b"*32)
                request["request_id"]=bad
                world.append("REQUEST",request)
                with self.assertRaisesRegex(ValueError,"UNSAFE_ORIGINAL_REQUEST_ID"):
                    s.original_events(world.manifest)

    def test_response_arriving_between_scan_and_seal_is_censored(self):
        # Simulate exactly the live race: the initial source snapshot has
        # REQUEST only; the original provider's RESPONSE arrives while the
        # research model calculates both possible side predictions.
        with TemporaryDirectory() as tmp:
            world=OriginalSyntheticFixture(Path(tmp))
            request=world.request(id="b"*32)
            world.append("REQUEST",request)
            events,requests=s.original_events(world.manifest)
            research=Path(tmp)/"research"
            research.mkdir(mode=0o700)
            for sub in ("predictions","misses","response_proofs","scores","rejects"):
                (research/sub).mkdir(mode=0o700)
            fixed={"activated_at_ms":request["started_at_ms"]-100,
                   "model_report_id":"TEST_ONLY_FROZEN_SOURCE",
                   "source_period_sha256":world.period}
            real_predict=s.predict_two_sides
            sent=[]
            def race(req,model):
                result=real_predict(req,model)
                sent.append(world.response(request))
                return result
            with patch.object(s,"OUT",research), patch.object(s,"predict_two_sides",race):
                out=s.capture(events,requests,fixed,world.fake_model())
                self.assertEqual(out["new_valid_predictions"],1)
                self.assertEqual(len(sent),1)
                replay,rerequests=s.original_events(world.manifest)
                r=s.attest_responses(replay,rerequests,fixed)
                self.assertEqual(r["censored_new"],1)
                self.assertFalse(list((research/"response_proofs").iterdir()))
                reject=json.loads((research/"rejects"/(request["request_id"]+".json")).read_bytes())
                self.assertFalse(reject["score_valid"])

    def test_inotify_original_append_then_capture_then_response(self):
        import select
        with TemporaryDirectory() as tmp:
            world=OriginalSyntheticFixture(Path(tmp))
            research=Path(tmp)/"research"
            research.mkdir(mode=0o700)
            for sub in ("predictions","misses","response_proofs","scores","rejects"):
                (research/sub).mkdir(mode=0o700)
            req=world.request(id="c"*32)
            freeze={"activated_at_ms":req["started_at_ms"]-100,
                    "model_report_id":"TEST_ONLY_FROZEN_SOURCE",
                    "source_period_sha256":world.period}
            fd=s.watch_handle(world.dir)
            try:
                world.append("REQUEST",req)
                waiter=select.poll();waiter.register(fd,select.POLLIN)
                self.assertTrue(waiter.poll(1500))
                trigger,overflow=s.original_journal_events(fd)
                self.assertTrue(trigger);self.assertEqual(overflow,0)
                ev,requests=s.original_events(world.manifest)
                with patch.object(s,"OUT",research):
                    captured=s.capture(ev,requests,freeze,world.fake_model())
                    self.assertEqual(captured["new_valid_predictions"],1)
                    stamp=json.loads((research/"predictions"/(req["request_id"]+".json")).read_bytes())["sealed_at_ms"]
                    time.sleep(.005)
                    response=world.response(req)
                    self.assertTrue(waiter.poll(1500))
                    trigger,overflow=s.original_journal_events(fd)
                    self.assertTrue(trigger);self.assertEqual(overflow,0)
                    ev,requests=s.original_events(world.manifest)
                    proof=s.attest_responses(ev,requests,freeze)
                    self.assertEqual(proof["verified_new"],1)
                    self.assertLess(stamp,response["data"]["available_at_ms"])
            finally:os.close(fd)

    def test_filename_receipt_disagreement_fails_before_scoring(self):
        with TemporaryDirectory() as tmp:
            world=OriginalSyntheticFixture(Path(tmp))
            request=world.request()
            ev=world.append("REQUEST",request)
            events,requests=s.original_events(world.manifest)
            research=Path(tmp)/"research"
            research.mkdir(mode=0o700)
            for sub in ("predictions","misses","response_proofs","scores","rejects"):
                (research/sub).mkdir(mode=0o700)
            with patch.object(s,"OUT",research):
                result=s.capture(events,requests,{
                    "activated_at_ms":request["started_at_ms"]-10,
                    "model_report_id":"TEST_ONLY_FROZEN_SOURCE",
                    "source_period_sha256":world.period
                },world.fake_model())
                self.assertEqual(result["new_valid_predictions"],1)
                path=research/"predictions"/(request["request_id"]+".json")
                path.rename(research/"predictions"/("f"*32+".json"))
                with self.assertRaisesRegex(ValueError,
                      "PREDICTION_RECEIPT_FILENAME_CHANGED"):
                    s.attest_responses(events,requests,{})


if __name__=="__main__":
    unittest.main()
