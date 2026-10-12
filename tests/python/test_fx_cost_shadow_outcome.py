"""No-lookahead real PAPER five-minute price-only maturity regression."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from train_v2 import fx_cost_shadow as shadow
from train_v2 import fx_cost_shadow_outcome as matured
from test_fx_cost_shadow import fixture, observations, write_private


SOURCE_SCHEMA="heptatrader.laya-shadow-quotes.v1"


def prior_source(at):
    rows, _=observations(at)
    prefix=b"".join((json.dumps(
        {"schema":SOURCE_SCHEMA,"kind":"QUOTE","sha256":q["source_id"],
         "data":{"observed_at_ms":q["event_at_ms"],
                 "bid":q["bid"],"ask":q["ask"]}},
        sort_keys=True)+"\n").encode() for q in rows)
    proof={"prefix_sha256":hashlib.sha256(prefix).hexdigest(),
           "broker_orders_sent":0}
    return rows,proof,prefix


class PrefixIntegrityTests(unittest.TestCase):
    def test_exact_append_only_prefix(self):
        p=b'{"sequence":1}\n'
        h=hashlib.sha256(p).hexdigest()
        self.assertEqual(matured.exact_historical_prefix(p+b'{"sequence":2}\n',h),p)

    def test_modified_prefix_fails_closed(self):
        original=b'{"sequence":1}\n'
        h=hashlib.sha256(original).hexdigest()
        with self.assertRaisesRegex(matured.MaturityError,"PREFIX_NOT_FOUND"):
            matured.exact_historical_prefix(b'{"sequence":3}\n',h)

    def test_uncommitted_tail_fails_closed(self):
        p=b'{"sequence":1}\n'
        with self.assertRaisesRegex(matured.MaturityError,"INCOMPLETE_TAIL"):
            matured.exact_historical_prefix(p+b"{",hashlib.sha256(p).hexdigest())

    def test_fake_prefix_identity_fails_closed(self):
        with self.assertRaises(matured.MaturityError):
            matured.exact_historical_prefix(b'{"sequence":1}\n',"foo")


class MaturityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths=fixture(Path(self.tmp.name))
        self.reg=shadow.preseal(**{k:v for k,v in self.paths.items() if k!="source_file"})
        self.at=self.reg["first_decision_ms"]+100
        self.rows,self.source_proof,self.prefix=prior_source(self.at)
        with patch.object(shadow, "sealed_original_broker_expiry",
                          return_value=(self.rows,self.source_proof)):
            shadow.run(**self.paths,at_ms=self.at)
        self.decision=json.loads((self.paths["root"]/"frozen-decision.json").read_text())
        self.due=self.decision["outcome_not_available_before_ms"]
        self.label={
            "schema":self.rows[-1]["schema"] if "schema" in self.rows[-1] else "heptatrader.train-v2-observation.v1",
            "source_id":"a"*64,"event_at_ms":self.due+100,
            "available_at_ms":self.due+101,
            "valid_until_ms":self.due+5100,
            "bid":"1.1210","ask":"1.1211",
        }

    def complete(self, *, rows=None, prefix=None, now=None):
        source_rows=(self.rows+[self.label]) if rows is None else rows
        raw=(self.prefix+b'{"kind":"END"}\n') if prefix is None else prefix
        with patch.object(matured,"read_private",return_value=raw):
            return matured.evaluate(
                root=self.paths["root"],config_path=self.paths["config_path"],
                cost_path=self.paths["cost_path"],
                registry_root=self.paths["registry_root"],
                source_file=self.paths["source_file"],
                now_ms=self.due+200 if now is None else now,
                reader=lambda _path,_spec:(source_rows,{"broker_orders_sent":0}))

    def test_price_label_after_real_maturity(self):
        result=self.complete()
        self.assertEqual(result["status"],
                         "RESEARCH_PRICE_ONLY_OUTCOME_MATURED_NO_ALPHA_CLAIM")
        self.assertEqual(result["previously_frozen_action"],"NO_TRADE")
        self.assertFalse(result["actual_execution_pnl_attested"])
        self.assertFalse(result["trained_forecaster_efficacy_tested"])
        self.assertEqual(result["broker_orders_sent"],0)

    def test_maturity_not_early(self):
        with self.assertRaisesRegex(matured.MaturityError,"NO_LOOKAHEAD"):
            self.complete(now=self.due-1)

    def test_horizon_is_frozen(self):
        original=self.paths["root"]/"frozen-decision.json"
        doc=json.loads(original.read_text());doc["outcome_not_available_before_ms"]-=1000
        write_private(original,doc)
        with self.assertRaisesRegex(matured.MaturityError,"DECISION_CHANGED"):
            self.complete()

    def test_outcome_quote_must_be_new(self):
        with self.assertRaisesRegex(matured.MaturityError,"OUTCOME_QUOTE_UNAVAILABLE"):
            self.complete(rows=self.rows)

    def test_no_future_quote_even_if_utc_stamp_matches(self):
        changed=deepcopy(self.label)
        changed["available_at_ms"]=self.due+8000
        with self.assertRaisesRegex(matured.MaturityError,"OUTCOME_QUOTE_UNAVAILABLE"):
            self.complete(rows=self.rows+[changed])

    def test_wrong_input_quote_rejected(self):
        changed=[*self.rows]
        changed[-1]=dict(changed[-1],source_id="f"*64)
        with self.assertRaisesRegex(matured.MaturityError,"INPUT_SOURCE_MISMATCH"):
            self.complete(rows=changed+[self.label])

    def test_historical_cost_change_rejected(self):
        f=self.paths["cost_path"]
        obj=json.loads(f.read_text())
        obj["fx_commission_one_way_bps"]["p90"]=1
        write_private(f,obj)
        with self.assertRaises(Exception):
            self.complete()

    def test_prior_prefix_changed_rejected(self):
        altered=b'{"schema":"wrong"}\n'
        with self.assertRaisesRegex(matured.MaturityError,"PREFIX_NOT_FOUND"):
            self.complete(prefix=altered)

    def test_prior_source_identity_matches_frozen_last_receipt(self):
        # A later unique timestamp added to the alleged *predecision* prefix
        # must invalidate the originally frozen latest-witness identity.
        event={"schema":SOURCE_SCHEMA,"kind":"QUOTE","sha256":"e"*64,
               "data":{"observed_at_ms":self.at+5000,
                       "bid":"1.1200","ask":"1.1201"}}
        changed=self.prefix+(json.dumps(event,sort_keys=True)+"\n").encode()
        with patch.object(matured,"exact_historical_prefix",return_value=changed):
            with self.assertRaisesRegex(matured.MaturityError,"FROZEN_PREFIX"):
                self.complete()

    def test_duplicate_same_timestamp_uses_first_chained_receipt(self):
        # IB may emit the same timestamp/price twice. The original source
        # parser uses setdefault(timestamp), so the first receipt stays valid.
        last=self.rows[-1]
        event={"schema":SOURCE_SCHEMA,"kind":"QUOTE","sha256":"e"*64,
               "data":{"observed_at_ms":last["event_at_ms"],
                       "bid":last["bid"],"ask":last["ask"]}}
        expanded=self.prefix+(json.dumps(event,sort_keys=True)+"\n").encode()
        with patch.object(matured,"exact_historical_prefix",return_value=expanded):
            self.assertEqual(self.complete()["previously_frozen_action"],"NO_TRADE")

    def test_output_never_admits_a_trade(self):
        result=self.complete()
        for field in ("trade_authority","native_product_ledgerwriter_acked",
                      "trained_forecaster_efficacy_tested","actual_execution_pnl_attested"):
            self.assertIs(result[field],False)


if __name__=="__main__":
    unittest.main()
