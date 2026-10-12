"""Cost-aware zero-alpha original PAPER shadow: negative/adversarial tests."""
from __future__ import annotations
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from train_v2 import fx_cost_shadow as m


def write_private(path: Path, value):
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    raw=(json.dumps(value,sort_keys=True)+"\n").encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    return hashlib.sha256(raw).hexdigest()


def cost_fixture(period_sha):
    return {
        "schema":"heptatrader.ib-cost-evidence.offline.v1",
        "instrument":"EUR.USD","period_source_sha256":period_sha,
        "exact_paper_account_scoped":True,
        "source":"ORIGINAL_CHAIN_VERIFIED_PAPER_LIVE_QUOTES_AND_OMS_COMMISSION_CALLBACKS",
        "actual_slippage_verified":False,"actual_fill_timestamp_attested":False,
        "historical_bar_bid_ask_used_as_executable_spread":False,
        "commission_callbacks_are_reported_not_broker_flex_statements":True,
        "trade_authority":False,"orders_sent":0,"broker_requests_sent":0,
        "fx_fee_missing_or_unproved_executions":0,
        "source_prefix_digest":"a"*64,"oms_sha256":"b"*64,
        "quote_count":2000,"source_prefix_count":1,
        "fx_fee_complete_unique_executions":75,
        "quote_spread_full_bps":{"p50":0.12,"p90":0.30,"p99":3.5},
        "fx_commission_one_way_bps":{"p50":0.2,"p90":0.72,"p99":0.75},
    }


def fixture(base: Path):
    period=base/"pnl-period.json"
    period_sha=write_private(period,{"execution_domain":"PAPER","started_at_ms":1000000})
    source=base/"input"
    source.mkdir(mode=0o700)
    segment=source/"segment-frozen-001"
    segment.mkdir(mode=0o700)
    file=segment/"quotes.jsonl"
    file.write_bytes(b'{"test_only":true}\n')
    file.chmod(0o600)
    config=base/"config.json"
    write_private(config,{"schema":"heptatrader.laya-resident-config.v1",
                          "automatic_trading":False,"period_sha256":period_sha,
                          "period_path":str(period),"feed_directory":str(source)})
    cost=base/"cost.json"
    write_private(cost,cost_fixture(period_sha))
    root=base/"receipts"
    registry=base/"registry"
    return {"config_path":config,"cost_path":cost,
            "root":root,"registry_root":registry,"source_file":file}


def observations(at):
    # Strict six quotes with 1-second separation, all distinct.
    records=[]
    for i in range(6):
        t=at-6000+i*1000
        records.append({"event_at_ms":t,
                        "available_at_ms":t+1,
                        "valid_until_ms":t+5000,
                        "source_id":f"{i+1:064x}",
                        "bid":"1.1200","ask":"1.1201"})
    return records,{"prefix_sha256":"7"*64}


class CostMathTests(unittest.TestCase):
    def setUp(self):
        self.cost=cost_fixture("e"*64)

    def test_zero_alpha_has_no_entry_direction(self):
        r=m.cost_frontier(bid="1.12",ask="1.1201",cost=self.cost)
        self.assertEqual(r["choice"],"NO_TRADE")
        self.assertIsNone(r["expected_market_direction"])
        self.assertLess(Decimal(r["cost_vs_prediction_net_bps"]),0)

    def test_spread_counted_once(self):
        r=m.cost_frontier(bid="1",ask="1.0001",cost=self.cost)
        live=(Decimal("0.0001")/Decimal("1.00005"))*10000
        expected=max(live, Decimal("3.5"))+Decimal("1.44")+Decimal("4")+Decimal("2")
        self.assertEqual(Decimal(r["required_predicted_abs_mid_move_bps"]),expected)

    def test_min_historical_p99_spread(self):
        r=m.cost_frontier(bid="1.12",ask="1.12001",cost=self.cost)
        self.assertEqual(r["precommitted_stress_spread_bps"],"3.5")

    def test_crossed_quote(self):
        with self.assertRaisesRegex(m.CostShadowError,"INVALID_ORIGINAL_BID_ASK"):
            m.cost_frontier(bid="1.3",ask="1.2",cost=self.cost)

    def test_negative(self):
        with self.assertRaisesRegex(m.CostShadowError,"UNBOUNDED_COST"):
            m.cost_frontier(bid="-1",ask="1.2",cost=self.cost)

    def test_nan(self):
        with self.assertRaises(m.CostShadowError):
            m.cost_frontier(bid="NaN",ask="1.2",cost=self.cost)

    def test_boolean(self):
        with self.assertRaises(m.CostShadowError):
            m.cost_frontier(bid=True,ask="1.2",cost=self.cost)

    def test_commission_missing(self):
        self.cost["fx_commission_one_way_bps"]["p90"]=0
        with self.assertRaisesRegex(m.CostShadowError,"COST_ESTIMATE_UNAVAILABLE"):
            m.cost_frontier(bid="1",ask="1.001",cost=self.cost)

    def test_policy_binds_cost_source_and_code(self):
        a=m._manifest("a"*64,"b"*64)
        b=m._manifest("a"*64,"c"*64)
        self.assertNotEqual(a["behavior_policy_id"],b["behavior_policy_id"])
        with patch.object(m,"CODE_SHA",return_value="b"*64):
            c=m._manifest("a"*64,"b"*64)
        self.assertNotEqual(a["behavior_policy_id"],c["behavior_policy_id"])

    def test_manifest_never_claims_model_or_broker_auth(self):
        a=m._manifest("a"*64,"b"*64)
        for field in ("prediction_model_fitted","trade_authority",
                      "promotion_allowed","original_trader_behavior_propensity_known"):
            self.assertIs(a[field],False)


class FrozenInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths=fixture(Path(self.tmp.name))
        self.preseal=m.preseal(**{k:v for k,v in self.paths.items() if k!="source_file"})
        self.at=self.preseal["first_decision_ms"]+100

    def run_with_quotes(self, records=None, at=None, **overrides):
        source = observations(self.at) if records is None else records
        with patch.object(m, "sealed_original_broker_expiry", return_value=source):
            return m.run(**(self.paths | overrides),at_ms=self.at if at is None else at)

    def test_genuine_presealed_no_trade_receipt(self):
        outcome=self.run_with_quotes()
        self.assertEqual(outcome["state"],
                         "RESEARCH_PREACTION_NO_TRADE_DURABLE_BEFORE_ORIGINAL_TTL")
        doc=json.loads((self.paths["root"]/"frozen-decision.json").read_text())
        self.assertEqual(doc["selected_action"],"NO_TRADE")
        self.assertFalse(doc["trained_model_applied"])
        self.assertEqual(doc["broker_query_or_order_by_trial"],0)
        self.assertFalse(doc["model_promotion_authorized"])

    def test_repeated_episode_claim_refused(self):
        self.run_with_quotes()
        with self.assertRaises(FileExistsError):
            self.run_with_quotes()

    def test_same_plan_changed_cost_fails(self):
        new_cost=cost_fixture(json.loads(self.paths["root"].joinpath(
            "manifest.json").read_text())["original_period_sha256"])
        new_cost["fx_commission_one_way_bps"]["p90"]=0.73
        write_private(self.paths["cost_path"],new_cost)
        with self.assertRaisesRegex(m.CostShadowError,"PRESEALED_SOURCE_OR_COST_OR_CODE_CHANGED"):
            self.run_with_quotes()

    def test_source_file_out_of_scope(self):
        foreign=Path(self.tmp.name)/"other"/"segment-evil"/"quotes.jsonl"
        write_private(foreign,{"test_only":True})
        with self.assertRaisesRegex(m.CostShadowError,"SOURCE_FILE_OUTSIDE_REGISTERED_ORIGINAL_FEED"):
            self.run_with_quotes(source_file=foreign)

    def test_future_pretend_quote_rejected(self):
        samples,prov=observations(self.at)
        samples[-1]["available_at_ms"]=self.at+100
        with self.assertRaisesRegex(m.CostShadowError,"NONCAUSAL_INPUT_QUOTE"):
            self.run_with_quotes((samples,prov))

    def test_broker_quote_expired(self):
        samples,prov=observations(self.at)
        samples[-1]["valid_until_ms"]=self.at-1
        with self.assertRaisesRegex(m.CostShadowError,"LATEST_BROKER_ORIGINAL_QUOTE_EXPIRED"):
            self.run_with_quotes((samples,prov))

    def test_old_quote_instead_of_future_replay(self):
        with self.assertRaisesRegex(m.CostShadowError,"DECISION_NOT_IN_PRECOMMITTED_WINDOW"):
            self.run_with_quotes(at=self.preseal["first_decision_ms"]-1)

    def test_gap_refuses(self):
        samples,prov=observations(self.at)
        samples[2]["event_at_ms"] -= 40000
        with self.assertRaisesRegex(m.CostShadowError,"STALE_OR_NONCONTIGUOUS_HISTORY"):
            self.run_with_quotes((samples,prov))

    def test_duplicate_provenance_refuses(self):
        samples,prov=observations(self.at)
        samples[1]["source_id"]=samples[0]["source_id"]
        with self.assertRaisesRegex(m.CostShadowError,"STALE_OR_NONCONTIGUOUS_HISTORY"):
            self.run_with_quotes((samples,prov))

    def test_fewer_than_six_refuses(self):
        samples,prov=observations(self.at)
        with self.assertRaisesRegex(m.CostShadowError,"NO_ORIGINAL_SIX_QUOTE_HISTORY"):
            self.run_with_quotes((samples[:5],prov))

    def test_tampered_plan_refuses(self):
        plan=self.paths["root"]/"plan.json"
        data=json.loads(plan.read_text());data["first_decision_ms"]+=100
        write_private(plan,data)
        with self.assertRaises(Exception):
            self.run_with_quotes()


if __name__=="__main__":
    unittest.main()
