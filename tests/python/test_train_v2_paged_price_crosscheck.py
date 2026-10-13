"""Existing Native price receipts cannot be requalified by a newer paged reader."""
from __future__ import annotations

from decimal import Decimal
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from historical_training.common import sha
from laya_cell.shadow_source import canonical, digest, strict
from laya_cell.integrity import ContractError
from train_v2.paged_price_crosscheck import (
    PRICE_ONLY, audit_pair, inspect_prior_outcome,
)
from train_v2.paged_price_window import verify_paged_price_window
from train_v2.original_multihorizon import ordered_quote_index, label_after

PERIOD = "f"*64


class PriorFrozenPriceChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source"
        self.root.mkdir(mode=0o700)
        self.now = ((int(time.time()*1000)-30*60000)//1000)*1000
        folder = self.root / f"segment-{self.now*1000000:019d}-abcddcba"
        folder.mkdir(mode=0o700)
        self.file = folder / "quotes.jsonl"
        last = "0"*64
        raw = []
        self.indices = []
        body = {
            "schema": "heptatrader.laya-shadow-quotes.v1",
            "sequence": 0, "previous": last, "at_ms": self.now,
            "kind": "HEADER", "data": {
                "source": "IB_TOOL_GATEWAY", "instrument": "EUR.USD",
                "execution_domain": "PAPER", "period_sha256": PERIOD,
                "write_operations_allowed": [], "broker_orders_sent": 0},
        }
        row={**body,"sha256":digest(body)}
        raw.append(canonical(row))
        last=row["sha256"]
        for i in range(82):
            at=self.now+1000+i*10000
            bid=Decimal("1.100000")+Decimal(i)*Decimal("0.000001")
            quote={
                "instrument": "EUR.USD", "source": "IB",
                "authoritative": True, "stale": False,
                "bid": str(bid), "ask": str(bid+Decimal("0.000030")),
                "observed_at_ms": at, "stale_after_ms": at+5000,
                "subscription_id": "synthetic-original-test",
                "subscription_state": "active",
            }
            body={
                "schema": "heptatrader.laya-shadow-quotes.v1",
                "sequence": i+1, "previous": last,
                "at_ms": at+200, "kind": "QUOTE", "data": quote,
            }
            row={**body,"sha256":digest(body)}
            raw.append(canonical(row))
            self.indices.append((at+200,at,row["sha256"]))
            last=row["sha256"]
        final={
            "schema":"heptatrader.laya-shadow-quotes.v1",
            "sequence":83,"previous":last,
            "at_ms":self.now+830000,"kind":"END",
            "data":{"broker_orders_sent":0}
        }
        final["sha256"]=digest(final)
        raw.append(canonical(final))
        self.file.write_bytes(b"\n".join(raw)+b"\n")
        self.file.chmod(0o600)
        indexed=ordered_quote_index(verify_paged_price_window(
            self.root,PERIOD,self.now,self.now+830000)["rows"])
        self.outcomes=[]
        for n,idx in enumerate((5,45)):
            ref=self.indices[idx]
            original=next(x for x in indexed["known"] if x[3]==ref[2])
            state,change=label_after(indexed,original,ref[1],300000)
            self.assertEqual(state,"OBSERVED_RETROSPECTIVE_PRICE_ONLY")
            self.assertIsNotNone(change)
            body={
                "schema":"heptatrader.native-price-ticks-sealed-outcome.v1",
                "plan_id":"fixture-plan-"+str(n),
                "source_quote_receipt":ref[2],
                "source_event_at_ms":ref[1],
                "original_quote_known_at_ms":ref[0],
                "original_price_due_at_ms":ref[1]+300000,
                "model_score_fixed_bps":"0.5",
                "original_price_change_bps":str(change),
                "price_direction_match":change>0,
                "original_source_fingerprint_sha256":
                    verify_paged_price_window(self.root,PERIOD,self.now,self.now+830000)["source_fingerprint_sha256"],
                "original_source_sealed_segments":1,
                "state":PRICE_ONLY,
                "original_source_sealed_price_observed":True,
                "original_price_only_not_executable_fill":True,
                "actual_broker_fills":0,
                "actual_executed_net_pnl_samples":0,
                "native_f64_exact_replay_attested":False,
                "model_promotion_permitted":False,
                "automatic_trading":False,
                "canonical_agentd_owner_enrolled":False,
                "broker_orders_sent":0,
            }
            path=Path(self.temp.name)/f"outcome{n}.json"
            path.write_text(json.dumps(body,sort_keys=True)+"\n")
            path.chmod(0o600)
            self.outcomes.append((path,sha(path)))

    def run_pair(self):
        return audit_pair(
            self.outcomes[0][0],self.outcomes[0][1],
            self.outcomes[1][0],self.outcomes[1][1],
            self.root,PERIOD)

    def rewrite(self, index, key, value):
        p,_=self.outcomes[index]
        body=json.loads(p.read_bytes())
        body[key]=value
        p.write_text(json.dumps(body,sort_keys=True)+"\n")
        p.chmod(0o600)
        self.outcomes[index]=(p,sha(p))

    def test_paged_prior_price_pair_no_new_forecast_or_trade(self):
        report=self.run_pair()
        self.assertEqual(report["state"],
           "TWO_PRIOR_PRICE_LABELS_EXACTLY_REPRODUCED_RESEARCH_ONLY")
        self.assertEqual(len(report["rows"]),2)
        self.assertTrue(all(x["old_price_label_exactly_reproduced"] for x in report["rows"]))
        self.assertEqual(report["actually_executed_trades"],0)
        self.assertEqual(report["statistically_independent_trading_outcomes"],0)
        self.assertFalse(report["trade_authority"])

    def test_new_naturally_sealed_successor_does_not_rewrite_old_prefix(self):
        # Simulate genuinely appended original quote segment; all original
        # prior proof bytes, plan and price outcome receipts remain unchanged.
        delta=900000
        original=[strict(line) for line in self.file.read_bytes().splitlines()]
        previous="0"*64
        reissued=[]
        for old in original:
            row=copy.deepcopy(old)
            row["at_ms"]+=delta
            if row["kind"]=="QUOTE":
                row["data"]["observed_at_ms"]+=delta
                row["data"]["stale_after_ms"]+=delta
            row["previous"]=previous
            row["sha256"]=digest({
                k:v for k,v in row.items() if k!="sha256"})
            previous=row["sha256"]
            reissued.append(canonical(row))
        folder=self.root/f"segment-{(self.now+delta)*1000000:019d}-12345678"
        folder.mkdir(mode=0o700)
        new_source=folder/"quotes.jsonl"
        new_source.write_bytes(b"\n".join(reissued)+b"\n")
        new_source.chmod(0o600)
        prior=self.outcomes[0][1]
        report=self.run_pair()
        self.assertEqual(report["state"],
             "TWO_PRIOR_PRICE_LABELS_EXACTLY_REPRODUCED_RESEARCH_ONLY")
        self.assertEqual(report["rows"][0]["naturally_appended_sealed_successors_verified"],1)
        self.assertEqual(report["rows"][1]["naturally_appended_sealed_successors_verified"],1)
        self.assertNotEqual(report["rows"][0]["original_archive_fingerprint"],
                            report["rows"][0]["original_frozen_prefix_fingerprint"])
        self.assertEqual(self.outcomes[0][1],prior)
        self.assertFalse(report["trade_authority"])

    def test_prior_outcome_sha_changed_rejected(self):
        self.outcomes[0][0].write_text('{"changed":true}')
        with self.assertRaisesRegex(ContractError,"PAGED_PRICE_PRIOR_OUTCOME_BYTES_CHANGED"):
            self.run_pair()

    def test_modifying_outcome_price_after_rehash_refused(self):
        self.rewrite(0,"original_price_change_bps","150.55")
        with self.assertRaisesRegex(ContractError,"PAGED_PRICE_PRIOR_VALUE_CHANGED"):
            self.run_pair()

    def test_forged_outcome_economic_authority_refused(self):
        self.rewrite(0,"broker_orders_sent",1)
        with self.assertRaisesRegex(ContractError,"PAGED_PRICE_OLD_OUTCOME_NOT_RESEARCH_ONLY"):
            self.run_pair()

    def test_wrong_frozen_horizon_refused(self):
        self.rewrite(0,"original_price_due_at_ms",self.indices[5][1]+600000)
        with self.assertRaisesRegex(ContractError,"PAGED_PRICE_PRIOR_HORIZON_CHANGED"):
            self.run_pair()

    def test_stale_archive_fingerprint_cannot_qualify(self):
        self.rewrite(0,"original_source_fingerprint_sha256","a"*64)
        with self.assertRaisesRegex(ContractError,"PAGED_PRICE_ORIGINAL_SOURCE_FRONTIER_CHANGED"):
            self.run_pair()

    def test_original_receipt_id_not_in_new_window_rejected(self):
        self.rewrite(0,"source_quote_receipt","0"*64)
        with self.assertRaisesRegex(ContractError,"PAGED_PRICE_PRIOR_INPUT_NOT_UNIQUE"):
            self.run_pair()

    def test_pair_mixed_original_frontiers_refused(self):
        from train_v2.paged_price_crosscheck import verify_paged_price_window as real
        calls=[0]
        def wrong(*args,**kwargs):
            response=real(*args,**kwargs)
            calls[0]+=1
            if calls[0]==2:
                response["source_fingerprint_sha256"]="a"*64
            return response
        with patch("train_v2.paged_price_crosscheck.verify_paged_price_window",
                   side_effect=wrong):
            with self.assertRaises(ContractError):
                self.run_pair()

    def test_wrong_source_period_cannot_mint_matching_price(self):
        with self.assertRaises(ContractError):
            audit_pair(
                self.outcomes[0][0],self.outcomes[0][1],
                self.outcomes[1][0],self.outcomes[1][1],
                self.root,"a"*64)
