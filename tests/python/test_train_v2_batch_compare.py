"""Comparison tests: exact older source/economic labels must be identical."""
import copy
import unittest

from train_v2.contracts import ContractError, digest
from train_v2.paged_batch_compare import compare, FIELD_PAIRS


def records():
    left = {
        "schema": "heptatrader.train-v2-batched-frozen-original-3x4-price.v1",
        "source_full_scans": 1,
        "new_future_trial": False,
        "model_signed_forecasts_generated": 0,
        "broker_orders_sent": 0,
        "trade_authority": False,
        "economic_alpha_qualified": False,
    }
    right = {
        "schema": "heptatrader.train-v2-paged-frozen-12cell-crosscheck.v1",
        "native_signed_forecasts_created": 0,
        "actual_broker_trade_rewards": 0,
        "broker_orders_sent": 0,
        "trade_authority": False,
    }
    values = (
        287, "3"*64, "4"*64, 3, 12, 4532,
        "3.360", "10.913", 0, "5"*64, "6"*64)
    for (_, (a,b)), value in zip(FIELD_PAIRS.items(), values):
        left[a] = value
        right[b] = value
    left["receipt_id"] = digest(left)
    right["audit_id"] = digest(right)
    return left,right


class CompareFrozenControlTests(unittest.TestCase):
    def test_all_eleven_original_fields_identical(self):
        new,old=records()
        result=compare(new,old,new_sha="a"*64,control_sha="b"*64)
        self.assertEqual(result["matched_field_count"],11)
        self.assertEqual(result["new_source_scan_count"],1)
        self.assertEqual(result["old_source_scan_count"],3)
        self.assertEqual(result["correlated_horizon_price_observations"],12)
        self.assertEqual(result["actual_independent_trading_rewards"],0)
        self.assertEqual(result["broker_orders_sent"],0)
        self.assertEqual(result["comparison_id"],digest({
            k:v for k,v in result.items() if k!="comparison_id"}))

    def test_every_one_of_eleven_fields_rejects_drift_even_when_rehashed(self):
        for key,(n,o) in FIELD_PAIRS.items():
            new,old=records()
            new[n]="999-changed"
            new["receipt_id"]=digest({
                k:v for k,v in new.items() if k!="receipt_id"})
            with self.subTest(key=key),self.assertRaisesRegex(
                    ContractError,"UNEQUAL_OR_MISSING_FIELD"):
                compare(new,old)

    def test_forged_old_audit_id_rejected(self):
        new,old=records()
        old["audit_id"]="1"*64
        with self.assertRaisesRegex(ContractError,"RECEIPT_OR_AUDIT_ID_INVALID"):
            compare(new,old)

    def test_forged_batch_success_digest_rejected(self):
        new,old=records()
        new["sealed_segments"]+=1
        with self.assertRaisesRegex(ContractError,"RECEIPT_OR_AUDIT_ID_INVALID"):
            compare(new,old)

    def test_any_trade_reward_claim_rejected_even_with_correct_digest(self):
        for key,value,side in (
            ("economic_alpha_qualified",True,"new"),
            ("model_signed_forecasts_generated",1,"new"),
            ("actual_broker_trade_rewards",1,"old"),
            ("trade_authority",True,"old"),
            ("broker_orders_sent",1,"new"),
        ):
            new,old=records()
            item=new if side=="new" else old
            item[key]=value
            digest_key="receipt_id" if side=="new" else "audit_id"
            item[digest_key]=digest({
                k:v for k,v in item.items() if k!=digest_key})
            with self.subTest(key=key),self.assertRaisesRegex(
                    ContractError,"SCOPE_AND_AUTHORITY_CHANGED"):
                compare(new,old)

    def test_missing_old_quote_denominator_never_defaults_to_zero(self):
        new,old=records()
        old.pop("selected_price_quotes_total")
        old["audit_id"]=digest({
            k:v for k,v in old.items() if k!="audit_id"})
        with self.assertRaisesRegex(ContractError,"UNEQUAL_OR_MISSING_FIELD"):
            compare(new,old)
