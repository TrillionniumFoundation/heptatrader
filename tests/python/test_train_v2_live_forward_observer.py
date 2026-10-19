"""Synthetic-only adversarial tests for fresh original-source forward observer.

All test prices and signed labels here are invented fixtures, never actual IB
market observations, execution fills or product root-trusted signatures.
"""
from __future__ import annotations
import copy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from laya_cell.shadow_source import SCHEMA as QUOTE_SCHEMA,canonical as line_canonical,digest as line_digest
from train_v2.live_forward_x230_witness import (
    DOMAIN, canonical, verify_segment,
)
from train_v2.live_forward_x230_verify import verify
from train_v2.live_forward_outcome import assess
from train_v2.contracts import ContractError


class X230LocalChainTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.root.chmod(0o700)
        self.dir=self.root/"segment-fabricated"
        self.dir.mkdir(mode=0o700)
        self.path=self.dir/"quotes.jsonl"
        self.period="a"*64
        self.now=time.time_ns()//1000000
        self.first=self.now-12000
        self.create_original()

    def create_original(self,*,edit_last=False,duplicate_conflicting=False):
        rows=[
            ("HEADER",self.first-25,{
                "source":"IB_TOOL_GATEWAY",
                "instrument":"EUR.USD",
                "execution_domain":"PAPER",
                "period_sha256":self.period,
                "write_operations_allowed":[],
                "broker_orders_sent":0,
            })
        ]
        for i in range(6):
            t=self.first+i*1000
            rows.append(("QUOTE",t+2,{
                "instrument":"EUR.USD","source":"IB",
                "authoritative":True,"stale":False,
                "observed_at_ms":t,"stale_after_ms":t+5000,
                "subscription_id":"TEST_ONLY_NO_BROKER",
                "subscription_state":"ACTIVE",
                "bid":"1.00001","ask":"1.00003",
            }))
        if duplicate_conflicting:
            row=dict(rows[-1][2],bid="1.00004",ask="1.00005")
            rows.append(("QUOTE",rows[-1][1]+10,row))
        hashes=[]
        prev="0"*64
        for idx,(kind,at,data) in enumerate(rows):
            body={"schema":QUOTE_SCHEMA,"sequence":idx,"previous":prev,
                  "at_ms":at,"kind":kind,"data":data}
            key=line_digest(body)
            hashes.append(line_canonical({**body,"sha256":key})+b"\n")
            prev=key
        if edit_last:
            hashes[-1]=hashes[-1].replace(b'1.00001',b'1.90001')
        raw=b"".join(hashes)
        self.path.write_bytes(raw)
        self.path.chmod(0o600)
        self.raw=raw
        self.receipts=[json.loads(x)["sha256"] for x in hashes[1:7]]

    def test_independent_chain_parser_and_exact_hashed_prefix(self):
        quotes,sha,length=verify_segment(self.path,hashlib.sha256(self.raw).hexdigest(),
                                          self.period,self.now)
        self.assertEqual(len(quotes),6)
        self.assertEqual([q["sha256"] for q in quotes],self.receipts)
        self.assertEqual(sha,hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(length,len(self.raw))

    def test_changed_quote_byte_refused(self):
        self.create_original(edit_last=True)
        with self.assertRaisesRegex(ValueError,"X230_ORIGINAL_QUOTE_CHAIN_INVALID"):
            verify_segment(self.path,hashlib.sha256(self.raw).hexdigest(),
                           self.period,self.now)

    def test_wrong_original_prefix_digest_refused(self):
        with self.assertRaisesRegex(ValueError,"X230_ORIGINAL_OUTCOME_PREFIX_CHANGED"):
            verify_segment(self.path,"0"*64,self.period,self.now)

    def test_source_crossed_same_timestamp_refused(self):
        self.create_original(duplicate_conflicting=True)
        with self.assertRaisesRegex(ValueError,"X230_DUPLICATE_SOURCE_PRICE_CONFLICT"):
            verify_segment(self.path,hashlib.sha256(self.raw).hexdigest(),
                           self.period,self.now)

    def test_wrong_period_or_future_clock_refused(self):
        with self.assertRaisesRegex(ValueError,"X230_ORIGINAL_SOURCE_WRONG_AUTHORITY"):
            verify_segment(self.path,hashlib.sha256(self.raw).hexdigest(),
                           "b"*64,self.now)
        with self.assertRaisesRegex(ValueError,"X230_ORIGINAL_QUOTE_CHAIN_INVALID"):
            verify_segment(self.path,hashlib.sha256(self.raw).hexdigest(),
                           self.period,self.first-1)


class SignedX230ReceiptTests(X230LocalChainTests):
    def setUp(self):
        super().setUp()
        self.now_ms=time.time_ns()//1000000
        self.at=self.first+5000
        self.due=self.at+300000
        pre={
            "full_feature_id":"c"*64,
            "original_period_sha256":self.period,
        }
        self.decision={
            "decision_at_ms":self.at,
            "outcome_not_available_before_ms":self.due,
            "source_quote_receipt":self.receipts[-1],
            "source_history_quote_receipts":self.receipts,
            "local_prospective_plan_id":"test-local-research-shadow-not-prod",
        }
        self.outcome={
            "actual_endpoint_or_censoring_status":"OBSERVED",
            "actual_market_mid_price_return_bps":"-0.98103481",
            "outcome_due_ms":self.due,
            "decision_at_ms":self.at,
            "actual_original_endpoint_quote_receipt":{
                "source_id":"d"*64,
                "event_at_ms":self.due+1500,
            },
            "original_quote_entry_sha256":self.receipts[-1],
            "exact_plan_id":self.decision["local_prospective_plan_id"],
            "evaluated_at_ms":self.due+6500,
            "source_segment_path":str(self.path),
            "trading_authority":False,
        }
        for name,data in (("real-shadow-decision-v1.json",self.decision),
                          ("causal-feature-before-outcome.json",pre),
                          ("real-five-minute-midprice-outcome.json",self.outcome)):
            path=self.root/name
            path.write_bytes(canonical(data)+b"\n")
            path.chmod(0o600)
        h=lambda name:hashlib.sha256((self.root/name).read_bytes()).hexdigest()
        self.key=Ed25519PrivateKey.generate()
        signed={
            "schema":"heptatrader.train-v2-x230-independently-recomputed-original-quote-label.v1",
            "signed_on_physical_host":"qian-ThinkPad-X230",
            "role":"SEPARATE_HOST_TEST_ONLY_OBSERVER",
            "production_independent_authority":False,
            "real_trade_fills_or_net_pnl_verified":False,
            "model_promotion_or_trading_authorized":False,
            "source_is_a_copy_of_pocket4_local_chain_not_separate_ib_feed":True,
            "reported_outcome_sha256":h("real-five-minute-midprice-outcome.json"),
            "decision_precommit_sha256":h("real-shadow-decision-v1.json"),
            "frozen_preoutcome_sha256":h("causal-feature-before-outcome.json"),
            "frozen_feature_id":"c"*64,
            "recomputed_five_minute_midprice_return_bps":"-0.98103481",
            "original_observer_quote_receipt":"d"*64,
            "original_input_quote_receipts":self.receipts,
            "decision_at_ms":self.at,
            "due_at_ms":self.due,
            "endpoint_at_ms":self.due+1500,
            "reported_at_ms":self.due+6500,
            "observer_computed_at_ms":self.due+10000,
            "verified_quote_path_points":148,
            "copied_original_source_bytes":len(self.raw),
            "copied_original_source_sha256":hashlib.sha256(self.raw).hexdigest(),
        }
        self.body=signed
        self.reseal()

    def reseal(self):
        key=self.key
        signed=self.body
        self.envelope={
            "envelope_schema":"heptatrader.train-v2-x230-observer-ephemeral-testonly-signed.v1",
            "signature_domain_hex":DOMAIN.hex(),
            "test_private_key_generated_in_x230_memory_only":True,
            "real_production_private_key_used":False,
            "native_product_root_trust_enrolled":False,
            "signed_observation":signed,
            "public_key_hex":key.public_key().public_bytes(
                Encoding.Raw,PublicFormat.Raw).hex(),
            "signature_hex":key.sign(DOMAIN+canonical(signed)).hex(),
        }

    def test_valid_synthetic_test_only_signature_and_pocket_original_source_prefix(self):
        result=verify(self.root,self.envelope)
        self.assertTrue(result["valid_ed25519_signature_from_x230_test_key"])
        self.assertFalse(result["production_observer_and_root_enrolled"])
        self.assertFalse(result["actual_trade_pnl_and_real_model_propensity_proven"])

    def test_signature_bitflip_fails_closed(self):
        original=self.envelope["signature_hex"]
        damaged=bytearray.fromhex(original)
        damaged[0]^=1
        self.envelope["signature_hex"]=damaged.hex()
        self.assertNotEqual(self.envelope["signature_hex"],original)
        with self.assertRaisesRegex(ValueError,"X230_SIGNED_RECOMPUTATION_INVALID_SIGNATURE"):
            verify(self.root,self.envelope)

    def test_forged_report_after_signing_fails_closed(self):
        self.envelope["signed_observation"]=dict(self.body,
             recomputed_five_minute_midprice_return_bps="9999")
        with self.assertRaisesRegex(ValueError,"X230_SIGNED_RECOMPUTATION_INVALID_SIGNATURE"):
            verify(self.root,self.envelope)

    def test_wrong_signed_feature_and_original_price_refused(self):
        self.body=dict(self.body,frozen_feature_id="f"*64)
        self.reseal()
        with self.assertRaisesRegex(ValueError,"X230_SIGNED_RECOMPUTATION_ORIGINAL_IDENTITY_CHANGED"):
            verify(self.root,self.envelope)

    def test_invalid_product_authority_claim_refused(self):
        self.envelope["native_product_root_trust_enrolled"]=True
        with self.assertRaisesRegex(ValueError,"X230_OBSERVER_PRODUCT_TRUST_NOT_AUTHORIZED"):
            verify(self.root,self.envelope)

    def test_original_source_mutation_refused(self):
        raw=bytearray(self.path.read_bytes())
        raw[-20]^=1
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError):
            verify(self.root,self.envelope)

    def test_original_outcome_price_receipt_change_refused(self):
        self.outcome["actual_market_mid_price_return_bps"]="8.8888"
        p=self.root/"real-five-minute-midprice-outcome.json"
        p.write_bytes(canonical(self.outcome)+b"\n")
        with self.assertRaisesRegex(ValueError,"X230_SIGNED_RECOMPUTATION_ORIGINAL_IDENTITY_CHANGED"):
            verify(self.root,self.envelope)


if __name__=="__main__":
    unittest.main()
