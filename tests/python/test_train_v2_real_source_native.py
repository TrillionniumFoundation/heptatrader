"""Synthetic adversarial checks for source-time native TEST-ONLY bridge.

No actual IB requests/trades, no real market data or finance model promotion.
The separate actual Phase 4L experiment is not replaced by these fixtures.
"""
from __future__ import annotations

from datetime import datetime,timezone,timedelta
from decimal import Decimal
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from train_v2 import real_source_native_integrity as integ
from train_v2 import real_source_native_signature_verify as signatures
from train_v2.preaction_shadow_policy import (
    POLICY_ID,_policy_selection,_canonical,_sha,CANDIDATES,MAX_SPREAD_BPS,
)
from train_v2.crosshost_test_signer import ROOT_DOMAIN,EVIDENCE_DOMAIN


def native_id(value:str)->bytes:
    b=value.encode("ascii")
    return len(b).to_bytes(8,"big")+b


class ExactNewShadowPolicyTests(unittest.TestCase):
    def setUp(self):
        self.quote={
            "source_id":"a"*64,"event_at_ms":1700000001000,
            "available_at_ms":1700000001002,
            "valid_until_ms":1700000006000,
            "bid":"1.10001","ask":"1.10003",
        }
        self.code="c"*64

    def doc(self,*,word=0,quote=None):
        quote=quote or self.quote
        x=_policy_selection(quote,draw=lambda:word)
        receipt={
            "policy_id":POLICY_ID,"generator_code_sha256":self.code,
            "grammar":list(CANDIDATES),"hard_filter_max_spread_bps":str(MAX_SPREAD_BPS),
            "canonical_order":x["candidates"],"candidate_count":len(x["candidates"]),
            "omitted_count_bound":0,"complete_for_shadow_generator":True,
            "source_quote_receipt":quote["source_id"],
        }
        receipt["candidate_set_digest"]=_sha(_canonical(receipt))
        d={
            "status":"RESEARCH_SHADOW_PREACTION_ONLY",
            "policy_id":POLICY_ID,
            "trade_authority":False,
            "original_opaque_llm_trading_policy_propensity_qualified":False,
            "genuine_behavior_propensity_only_for_new_shadow_policy":True,
            "local_episode_claimed_before_rng":True,"broker_requests_or_orders":0,
            "source_quote_receipt":quote["source_id"],
            "source_at_ms":quote["event_at_ms"],
            "source_available_at_ms":quote["available_at_ms"],
            "source_expires_at_ms":quote["valid_until_ms"],
            "candidate_set_receipt":receipt,
            **x,
        }
        d["decision_digest"]=_sha(_canonical(d))
        return d

    def rehash(self,d):
        body={k:v for k,v in d.items() if k!="decision_digest"}
        d["decision_digest"]=_sha(_canonical(body))

    def test_exact_complete_closed_mapping_all_four_draws(self):
        expected=("NO_TRADE","NO_TRADE","UP_SIGNAL","DOWN_SIGNAL")
        for word,name in enumerate(expected):
            with self.subTest(word=word):
                d=self.doc(word=word)
                r=integ.validate_choice(d,self.quote,self.code)
                self.assertEqual(r["selected"],name)
                self.assertEqual(r["selected_propensity_q32_raw"],
                    (1<<31) if name=="NO_TRADE" else (1<<30))
                self.assertEqual(r["omitted_count_bound"],0)

    def test_hard_filtered_only_abstain_is_probability_one(self):
        q=dict(self.quote,ask="1.10090")
        d=self.doc(quote=q)
        self.assertIsNone(d["selection_word_two_bit"])
        r=integ.validate_choice(d,q,self.code)
        self.assertEqual(r["candidates"],["NO_TRADE"])
        self.assertEqual(r["selected_propensity_q32_raw"],1<<32)

    def test_forged_selection_does_not_become_product_propensity(self):
        d=self.doc(word=0)
        d["selected"]="UP_SIGNAL"
        self.rehash(d)
        with self.assertRaisesRegex(ValueError,"ORIGINAL_SHADOW_RECORDED_SAMPLER_RULE_CHANGED"):
            integ.validate_choice(d,self.quote,self.code)

    def test_forged_q32_probability_rejected_after_locally_rehashing(self):
        d=self.doc(word=3)
        d["selected_propensity_q32_raw"]=1<<31
        self.rehash(d)
        with self.assertRaisesRegex(ValueError,"ORIGINAL_SHADOW_RECORDED_SAMPLER_RULE_CHANGED"):
            integ.validate_choice(d,self.quote,self.code)

    def test_changed_generator_code_digest_cannot_self_authorize(self):
        d=self.doc()
        d["candidate_set_receipt"]["generator_code_sha256"]="f"*64
        receipt=d["candidate_set_receipt"]
        receipt["candidate_set_digest"]=_sha(_canonical({k:v for k,v in receipt.items()
                                                          if k!="candidate_set_digest"}))
        self.rehash(d)
        with self.assertRaisesRegex(ValueError,"ORIGINAL_SHADOW_POLICY_CODE_OR_CANDIDATE_DIGEST_CHANGED"):
            integ.validate_choice(d,self.quote,self.code)

    def test_candidate_order_and_full_set_not_obscured(self):
        d=self.doc()
        d["candidate_set_receipt"]["canonical_order"]=["NO_TRADE"]
        receipt=d["candidate_set_receipt"]
        receipt["candidate_set_digest"]=_sha(_canonical({k:v for k,v in receipt.items()
                                                          if k!="candidate_set_digest"}))
        self.rehash(d)
        with self.assertRaisesRegex(ValueError,"ORIGINAL_SHADOW_POSTFILTER_CANDIDATES_CHANGED"):
            integ.validate_choice(d,self.quote,self.code)

    def test_quote_source_receipt_substitution_refused(self):
        d=self.doc()
        altered=dict(self.quote,source_id="f"*64)
        with self.assertRaisesRegex(ValueError,"ORIGINAL_LOCAL_REAL_SHADOW_CHOICE_SOURCE_CHANGED"):
            integ.validate_choice(d,altered,self.code)

    def test_bad_sampler_state_word_rejected(self):
        d=self.doc()
        d["selection_word_two_bit"]=4
        self.rehash(d)
        with self.assertRaisesRegex(ValueError,"ORIGINAL_SHADOW_SAMPLING_WORD_NOT_TWO_BITS"):
            integ.validate_choice(d,self.quote,self.code)

    def test_source_self_hash_tamper_refused(self):
        d=self.doc()
        d["hard_filter_spread_bps"]="9999"
        with self.assertRaisesRegex(ValueError,"ORIGINAL_SHADOW_SELF_IDENTITY_CHANGED"):
            integ.validate_choice(d,self.quote,self.code)

    def test_test_only_and_original_llm_release_false(self):
        d=self.doc()
        d["original_opaque_llm_trading_policy_propensity_qualified"]=True
        self.rehash(d)
        with self.assertRaisesRegex(ValueError,"ORIGINAL_LOCAL_REAL_SHADOW_CHOICE_SOURCE_CHANGED"):
            integ.validate_choice(d,self.quote,self.code)


def signed_msg(role):
    if role=="root":
        return ROOT_DOMAIN+native_id("lab.root")+b"\0"*47
    tag={"generator":0,"observer":1,"evaluator":2}[role]
    return (EVIDENCE_DOMAIN+native_id("lab.originalmarket."+role)+
            native_id("lab."+role)+bytes([tag])+b"\0"*144)


class NativeSignatureTimelineTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.root.chmod(0o700)
        (self.root/"native-lab-v1").mkdir(mode=0o700)
        self.now=int(datetime(2026,10,12,1,17,0,tzinfo=timezone.utc).timestamp()*1000)
        self.due=self.now+300000
        roles={
            "root":"qian-qi",
            "generator":"MacBook-Pro.local",
            "observer":"qian-ThinkPad-X230",
            "evaluator":"qian-qi",
        }
        self.keys={r:Ed25519PrivateKey.generate() for r in roles}
        pins={role:{
            "role":role,"actual_signing_host":host,"test_only":True,
            "private_key_exported":False,
            "public_key_hex":self.keys[role].public_key().public_bytes(
                Encoding.Raw,PublicFormat.Raw).hex(),
        } for role,host in roles.items()}
        self.write("public_roles.json",{
            "schema":"hepta-train-v2-crosshost-native-signing-pubs.v1",
            "purpose":"test-only-no-production","roles":pins,
        })
        self.write("real-shadow-decision-v1.json",{
            "decision_at_ms":self.now,
            "outcome_not_available_before_ms":self.due,
            "selected_propensity_q32_raw":1<<31,
        })
        self.write("native-real-quote-packet.json",{
            "selected_propensity_q32_raw":1<<31,
            "shadow_decision_sha256":hashlib.sha256(
                (self.root/"real-shadow-decision-v1.json").read_bytes()).hexdigest(),
        })
        self.write("real-five-minute-midprice-outcome.json",{
            "actual_endpoint_or_censoring_status":"OBSERVED",
            "actual_market_mid_price_return_bps":"-0.80241080",
            "outcome_due_ms":self.due,
        })
        packet=(self.root/"native-real-quote-packet.json").read_bytes()
        self.write("native-real-price-observer-packet.json",{
            "source_shadow_packet_sha256":hashlib.sha256(packet).hexdigest(),
            "market_midprice_change_bps":"-0.80241080",
            "research_only":True,
        })
        self.write("native-lab-v1/native-source-decision-ack.json",{
            "native_witness_ack_sequence":1,"native_acknowledged_at_ms":self.now+12000,
        })
        self.write("native-lab-v1/native-price-outcome-and-dataset-test-ack.json",{
            "native_durable_witness_sequence":2,
            "native_V3_dataset_is_product_market_eligible":False,
            "actual_order_execution_cost_and_PnL":False,
        })
        for mode in ("init","finish"):
            out=[]
            for role in signatures.ROLE_ORDER[mode]:
                msg=signed_msg(role)
                key=self.keys[role]
                signed_time=self.now+13000 if mode=="init" else self.due+12000
                signed_at=datetime.fromtimestamp(
                    signed_time/1000,timezone.utc).isoformat()
                sig=key.sign(msg)
                out.append({
                    "role":role,"signing_host":roles[role],
                    "public_message_hex":msg.hex(),
                    "message_sha256":hashlib.sha256(msg).hexdigest(),
                    "signature_hex":sig.hex(),
                    "signed_at_utc":signed_at,
                })
            self.write("native-"+mode+"-crosshost-public-signatures.json",{
                "schema":"heptatrader.real-source-native-multihost-test-attestations.v1",
                "stage":mode,"signatures":out,
                "no_private_signer_export":True,"production_authorization":False,
                "native":{"product_authority":False},
            })

    def write(self,path,doc):
        file=self.root/path
        file.write_text(json.dumps(doc,sort_keys=True)+"\n")
        file.chmod(0o600)

    def document(self,path):
        return json.loads((self.root/path).read_text())

    def test_all_four_real_native_domains_and_roles_publicly_verify(self):
        r=signatures.verify(self.root)
        self.assertEqual(r["public_only_ed25519_messages_verified"],4)
        self.assertTrue(r["native_Mac_generator_decision_actual_ack_before_horizon"])
        self.assertFalse(r["product_root_organization_independence_authenticated"])

    def test_wrong_root_signature_refuses_verification(self):
        f="native-init-crosshost-public-signatures.json"
        body=self.document(f)
        entry=body["signatures"][0]
        b=bytearray.fromhex(entry["signature_hex"]);b[-1]^=1
        entry["signature_hex"]=bytes(b).hex()
        self.write(f,body)
        with self.assertRaisesRegex(ValueError,"NATIVE_ORIGINAL_CROSSHOST_ED25519_MISMATCH"):
            signatures.verify(self.root)

    def test_generator_signature_issued_after_horizon_refuses(self):
        f="native-init-crosshost-public-signatures.json"
        body=self.document(f)
        entry=body["signatures"][1]
        entry["signed_at_utc"]=datetime.fromtimestamp(self.due/1000,timezone.utc).isoformat()
        self.write(f,body)
        with self.assertRaisesRegex(ValueError,"NATIVE_GENERATOR_SIGNED_AFTER_OUTCOME"):
            signatures.verify(self.root)

    def test_observer_signature_issued_before_horizon_refuses(self):
        f="native-finish-crosshost-public-signatures.json"
        body=self.document(f)
        entry=body["signatures"][0]
        entry["signed_at_utc"]=datetime.fromtimestamp((self.due-1)/1000,timezone.utc).isoformat()
        self.write(f,body)
        with self.assertRaisesRegex(ValueError,"NATIVE_OBSERVER_SIGNED_BEFORE_OUTCOME"):
            signatures.verify(self.root)

    def test_product_root_claim_refused(self):
        f="native-finish-crosshost-public-signatures.json"
        body=self.document(f)
        body["production_authorization"]=True
        self.write(f,body)
        with self.assertRaisesRegex(ValueError,"NATIVE_SIGNED_STAGE_CONTEXT_CHANGED"):
            signatures.verify(self.root)

    def test_rewritten_preoutcome_decision_bundle_refused(self):
        f="native-real-quote-packet.json"
        body=self.document(f)
        body["selected_propensity_q32_raw"]=1<<30
        self.write(f,body)
        with self.assertRaisesRegex(ValueError,"NATIVE_SOURCE_ACTUAL_LEDGER_TIME_OR_SCOPE_WRONG"):
            signatures.verify(self.root)


if __name__=="__main__":
    unittest.main()
