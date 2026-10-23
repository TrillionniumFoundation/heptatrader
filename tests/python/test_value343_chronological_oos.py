"""Adversarial scope, clock, fee and leakage tests for Pocket4 Value343."""
from __future__ import annotations
from copy import deepcopy
from decimal import Decimal
import hashlib
import math
from pathlib import Path
import unittest

import numpy as np

from laya_cell.integrity import ContractError,digest
from laya_cell import value343_oos as v


def example():
    when=1791365475971
    request={"started_at_ms":when,"instrument":"EUR.USD",
             "position_at_request":0.0,"request_id":"r1",
             "model_id":"gpt-6-luna","prompt":"fixed market snapshot",
             "history_snapshot":[{"observed_at_ms":when-235000+10000*i,
                 "bid":round(1.1187+i*.00001,8),
                 "ask":round(1.11872+i*.00001,8),
                 "mid":round(1.11871+i*.00001,8)}
                 for i in range(24)]}
    quantity=Decimal(89388)
    entry=Decimal("1.11890")
    close=Decimal("1.11910")
    cash=quantity*(close-entry)
    fees=Decimal(4)
    command={"kind":"OPEN","command_id":"c1","instrument":"EUR.USD",
             "side":"BUY","order_quantity":float(quantity),
             "position_before_send":0.0,"quantity_mode":"BASE_NOTIONAL_TARGET",
             "target_notional_usd":100000.0,"reference_price":1.11872,
             "created_at_ms":when+2000}
    first={"execution_id":"e1","command_id":"c1","side":"BUY",
           "quantity":str(quantity),"price":str(entry),
           "commission":"2","commission_currency":"USD"}
    second={"execution_id":"e2","command_id":"c2","side":"SELL",
            "quantity":str(quantity),"price":str(close),
            "commission":"2","commission_currency":"USD"}
    reward={"input_id":digest(request),"forecast_id":"forecast",
            "behavior_model_id":request["model_id"],
            "input_attribution":{"prompt_sha256":hashlib.sha256(request["prompt"].encode()).hexdigest()},
            "open_command_id":"c1","instrument":"EUR.USD","currency":"USD",
            "learning_eligible":True,"exact_input_bound":True,
            "settlement_complete":True,"fees_complete":True,
            "flat_to_flat_executed_quantity":True,
            "policy_gradient_allowed":False,"laya_on_policy":False,
            "effect_authority":False,"unsettled_commands":[],
            "learning_exclusion_reasons":[],"executions":[first,second],
            "realized_gross_usd":str(cash),
            "actual_commissions_usd":str(fees),
            "realized_net_usd":str(cash-fees)}
    reward["reward_id"]=digest(reward)
    return request,{"command":command},reward


def chronological():
    epoch=1791365000000
    return [{"reward_id":f"{i:064x}",
             "row_id":f"{i:064x}",
             "input_started_at_ms":epoch+i*600000,
             "outcome_first_fully_known_ms":epoch+i*600000+65000,
             "features":[float((i%7)+j) for j in range(len(v.FEATURES))],
             "net_usd":float((i%7)-4),
             "gross_usd":float(i%7),
             "actual_fee_usd":4.0} for i in range(335)]


class Value343OfflineTest(unittest.TestCase):
    def test_actual_usd100k_reference_not_hardcoded_25k_eur(self):
        request,binding,reward=example()
        row=v.verify_100k_reward(reward,request,binding)
        self.assertEqual(row["net_usd"],"13.87760")
        self.assertEqual(row["fee_usd"],"4")
        self.assertEqual(len(row["row_id"]),64)

    def test_no_forged_25k_or_100k_quantity(self):
        request,binding,reward=example()
        original=binding["command"]["order_quantity"]
        for illegal in (25000,0,-1,175000):
            binding["command"]["order_quantity"]=illegal
            with self.assertRaises(ContractError):
                v.verify_100k_reward(reward,request,binding)
        binding["command"]["order_quantity"]=original
        binding["command"]["target_notional_usd"]=25000.0
        with self.assertRaises(ContractError):
            v.verify_100k_reward(reward,request,binding)

    def test_unsupported_original_reference_price_and_scope_rejected(self):
        request,binding,reward=example()
        binding["command"]["reference_price"]=1.12
        with self.assertRaisesRegex(ContractError,"QUANTITY_REFERENCE"):
            v.verify_100k_reward(reward,request,binding)
        request,binding,reward=example()
        reward["currency"]="EUR"
        reward["reward_id"]=digest({k:v for k,v in reward.items() if k!="reward_id"})
        with self.assertRaises(ContractError):
            v.verify_100k_reward(reward,request,binding)

    def test_fee_revision_or_changed_net_is_not_accepted(self):
        request,binding,reward=example()
        reward["realized_net_usd"]="0"
        reward["reward_id"]=digest({k:v for k,v in reward.items() if k!="reward_id"})
        with self.assertRaisesRegex(ContractError,"ECONOMIC_RECOMPUTATION"):
            v.verify_100k_reward(reward,request,binding)

    def test_actor_gradient_and_changed_input_refused(self):
        request,binding,reward=example()
        reward["policy_gradient_allowed"]=True
        reward["reward_id"]=digest({k:v for k,v in reward.items() if k!="reward_id"})
        with self.assertRaises(ContractError):
            v.verify_100k_reward(reward,request,binding)
        request,binding,reward=example()
        request["prompt"]="tampered market forecast"
        with self.assertRaises(ContractError):
            v.verify_100k_reward(reward,request,binding)

    def test_real_original_pretrade_history_only(self):
        request,binding,reward=example()
        f=v.make_features(request,"BUY")
        self.assertEqual(len(f),len(v.FEATURES))
        self.assertTrue(all(math.isfinite(z) for z in f))
        self.assertEqual(f[0],-1.0)
        self.assertEqual(v.make_features(request,"SELL")[0],1.0)
        self.assertNotEqual(f,v.make_features(request,"SELL"))

    def test_future_unsorted_stale_and_crossed_spread_rejected(self):
        for mode in ("future","unsorted","stale","crossed","nan"):
            request,binding,reward=example()
            if mode=="future":
                request["history_snapshot"][-1]["observed_at_ms"]=request["started_at_ms"]+1
            if mode=="unsorted":
                request["history_snapshot"][3]["observed_at_ms"]=request["history_snapshot"][2]["observed_at_ms"]
            if mode=="stale":
                request["started_at_ms"]+=100000
            if mode=="crossed":
                request["history_snapshot"][-1]["ask"]=1
            if mode=="nan":
                request["history_snapshot"][0]["mid"]=float("nan")
            with self.subTest(mode=mode),self.assertRaises(ContractError):
                v.make_features(request,"BUY")

    def test_timeline_split_sizes_and_label_availability(self):
        rows=chronological()
        train,embargo,hold=v.fixed_split(rows)
        self.assertEqual((len(train),len(embargo),len(hold)),(217,51,67))
        self.assertLess(max(x["outcome_first_fully_known_ms"] for x in train),
                        min(x["input_started_at_ms"] for x in hold))
        tampered=deepcopy(rows)
        tampered[30]["outcome_first_fully_known_ms"]=hold[0]["input_started_at_ms"]+1
        with self.assertRaises(ContractError):
            v.fixed_split(tampered)

    def test_train_normalization_and_weights_ignore_test(self):
        a,b,c=v.fixed_split(chronological())
        x,z,norm=v.design_from_train_only(a,c)
        changed=deepcopy(c)
        for row in changed:row["features"]=[10000.0]*len(v.FEATURES)
        x2,z2,norm2=v.design_from_train_only(a,changed)
        self.assertEqual(norm,norm2)
        np.testing.assert_allclose(x,x2)
        target=[row["net_usd"] for row in a]
        w,fit=v.fit_huber(x,target)
        w2,fit2=v.fit_huber(x2,target)
        np.testing.assert_allclose(w,w2,rtol=0,atol=0)
        self.assertEqual(fit["actual_optimizer_steps"],20)
        self.assertEqual(fit["coefficients"],fit2["coefficients"])

    def test_original_execution_receipt_matures_only_after_fees(self):
        request,binding,reward=example()
        first={
            ("e1","broker_execution"):{"ts_ms":request["started_at_ms"]+3000,
                    "broker_econ_execution_price":1.11890},
            ("e1","broker_commission"):{"ts_ms":request["started_at_ms"]+3010,
                    "broker_econ_commission":2},
            ("e2","broker_execution"):{"ts_ms":request["started_at_ms"]+700000,
                    "broker_econ_execution_price":1.11910},
            ("e2","broker_commission"):{"ts_ms":request["started_at_ms"]+700080,
                    "broker_econ_commission":2}
        }
        self.assertEqual(v.settled_time(reward,first,request,binding),
                         request["started_at_ms"]+700080)
        first["e2","broker_commission"]["broker_econ_commission"]=4
        with self.assertRaises(ContractError):
            v.settled_time(reward,first,request,binding)

    def test_no_private_output_or_trading_authority(self):
        text=Path(v.__file__).read_text()
        self.assertFalse(any(forbidden in text for forbidden in
                        ("placeOrder(", "cancelOrder(", "reqExecutions(", "send_order(", "open_position(")))
        self.assertIn("no_live_model_deployed",text)
        self.assertIn("broker_orders_sent",text)
        with self.assertRaises(ContractError):
            v.write_once(Path("/tmp/not_permitted_value343.json"),{"trade":False})


if __name__=="__main__":
    unittest.main()
