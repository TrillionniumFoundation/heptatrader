"""Synthetic adversarial source/protocol tests. No IB client or API."""
import hashlib, json, math, tempfile, unittest
from decimal import Decimal, localcontext
from pathlib import Path
from unittest.mock import patch

from train_v2 import independent_fx_future_audit as audit
A=1791822000000

def fixture(tmp,expired_bucket=None,cost_lowered=False):
    root=Path(tmp)/"original"
    root.mkdir(mode=0o700)
    cell=root/"window-00";cell.mkdir(mode=0o700)
    d=Path(tmp)/"input"/"segment-00000000000000000001-test"
    d.mkdir(mode=0o700,parents=True)
    filename=d/"quotes.jsonl"
    prev="0"*64
    recs=[]
    def record(kind,when,payload):
        nonlocal prev
        obj={"schema":"heptatrader.laya-shadow-quotes.v1",
             "sequence":len(recs),"kind":kind,"data":payload,
             "previous":prev,"at_ms":when}
        obj["sha256"]=audit.digest(audit.canonical(obj))
        recs.append(obj);prev=obj["sha256"]
        return obj["sha256"]
    events=[]
    for i in range(13):
        end=A-(12-i)*300000
        events.append((i,end-800,end-700,end+4200 if i!=expired_bucket else end-500))
    record("HEADER",events[0][2]-100,{
        "source":"IB_TOOL_GATEWAY","execution_domain":"PAPER",
        "instrument":"EUR.USD","period_sha256":audit.PERIOD_SHA,
        "write_operations_allowed":[],"broker_orders_sent":0})
    ids=[]
    for i,event,known,expiry in events:
        ids.append(record("QUOTE",known,{
            "instrument":"EUR.USD","source":"IB","authoritative":True,
            "stale":False,"observed_at_ms":event,
            "stale_after_ms":expiry,"bid":"1.10000","ask":"1.10002"}))
    record("END",events[-1][2]+100,{"broker_orders_sent":0})
    raw=b"".join(audit.canonical(x)+b"\n" for x in recs)
    filename.write_bytes(raw);filename.chmod(0o600)
    prefix={"path":str(filename),"complete_prefix_bytes":len(raw),
            "prefix_sha256":audit.digest(raw)}
    cost={"schema":"heptatrader.ib-cost-evidence.offline.v1",
          "period_source_sha256":audit.PERIOD_SHA,
          "actual_slippage_verified":False,"trade_authority":False,
          "fx_fee_missing_or_unproved_executions":0,
          "quote_spread_full_bps":{"p99":3.4837759},
          "fx_commission_one_way_bps":{"p90":0.7147899}}
    cp=Path(tmp)/"cost.json"
    cp.write_bytes(audit.canonical(cost)+b"\n");cp.chmod(0o600)
    plan={"cost_file":str(cp),"cost_sha256":audit.digest(cp.read_bytes())}
    hour=((A//1000-300)//3600)%24
    last=events[-1]
    live=(1.10002-1.10000)/((1.10002+1.10000)/2)*10000
    margin=max(live,3.4837759)+2*0.7147899+6
    pre={"schema":audit.RESEARCH+".preaction.v1",
         "anchor_ms":A,"decision_at_ms":A+1000,
         "intended_behavior":"NO_TRADE_PRICE_ONLY",
         "no_model_prediction_or_propensity_derived":True,
         "same_original_period_sha256":audit.PERIOD_SHA,
         "trade_authority":False,
         "source_health":{"state":"READY_FOR_REVIEW_NOT_AUTHORIZATION",
                          "health_at_ms":A-100,"usable_probes":24,"missing_probes":0},
         "source_prefix_receipts":[prefix],
         "input_source_ids":ids,
         "input_quote_bid":"1.10000","input_quote_ask":"1.10002",
         "input_quote_event_at_ms":last[1],
         "input_quote_available_at_ms":last[2],
         "input_quote_expiry_ms":last[3],
         "feature_vector":[0.]*6+[math.sin(2*math.pi*hour/24),
                                  math.cos(2*math.pi*hour/24)],
         "cost_stress":{
            "live_original_spread_bps":live,
            "stressed_spread_bps":max(live,3.4837759),
            "roundtrip_commission_p90_bps":1.4295798,
            "hypothetical_slippage_roundtrip_bps":4.,
            "minimum_margin_bps":2.,
            "required_absolute_prediction_bps":margin-(5. if cost_lowered else 0.),
        }}
    prepath=cell/"preaction.json"
    prepath.write_bytes(audit.canonical(pre)+b"\n");prepath.chmod(0o600)
    attempt={"preaction_sha256":audit.digest(prepath.read_bytes()),
             "attempt_recorded_at_ms":A+1400,
             "original_quote_expiry_ms":last[3],
             "original_preaction_durable_at_ms":A+1200}
    return root,cell,plan,pre,attempt,prefix,ids

def mature_case(tmp, *, tamper_move=False, choose_later=False):
    root,cell,plan,pre,entry,oldproof,ids=fixture(tmp)
    due=A+900000
    d=Path(tmp)/"input"/"segment-00000000000000000002-future"
    d.mkdir(mode=0o700,parents=True)
    fn=d/"quotes.jsonl"
    prev="0"*64
    entries=[]
    def add(kind,at,payload):
        nonlocal prev
        body={"schema":"heptatrader.laya-shadow-quotes.v1",
              "sequence":len(entries),"kind":kind,
              "at_ms":at,"previous":prev,"data":payload}
        body["sha256"]=audit.digest(audit.canonical(body))
        entries.append(body);prev=body["sha256"]
        return body["sha256"]
    add("HEADER",due+50,{
        "source":"IB_TOOL_GATEWAY","instrument":"EUR.USD",
        "execution_domain":"PAPER","period_sha256":audit.PERIOD_SHA,
        "write_operations_allowed":[],"broker_orders_sent":0})
    quotes=[]
    for offset,price in ((200,"1.10050"),(500,"1.10200")):
        ev=due+offset
        q={"instrument":"EUR.USD","source":"IB","authoritative":True,
           "stale":False,"observed_at_ms":ev,
           "stale_after_ms":ev+5000,
           "bid":price,
           "ask":str(Decimal(price)+Decimal("0.00002"))}
        quotes.append((add("QUOTE",ev+100,q),ev,q))
    add("END",due+900,{"broker_orders_sent":0})
    raw=b"".join(audit.canonical(r)+b"\n" for r in entries)
    fn.write_bytes(raw);fn.chmod(0o600)
    proof={"path":str(fn),"complete_prefix_bytes":len(raw),
           "prefix_sha256":audit.digest(raw)}
    chosen=quotes[-1] if choose_later else quotes[0]
    chosen_id,ev,q=chosen
    with localcontext() as ctx:
        ctx.prec=40
        bid=Decimal("1.10000");ask=Decimal("1.10002")
        mid=(bid+ask)/2
        outbid=Decimal(q["bid"]);outask=Decimal(q["ask"])
        vals={
          "midpoint_move_bps":str(((outbid+outask)/2/mid-1)*10000),
          "hypothetical_long_entry_ask_exit_bid_move_bps":str((outbid-ask)/mid*10000),
          "hypothetical_short_entry_bid_exit_ask_move_bps":str((bid-outask)/mid*10000)}
    if tamper_move:vals["midpoint_move_bps"]="999"
    outcome={
        "schema":audit.RESEARCH+".mature-quote.v1",
        "anchor_ms":A,"horizon_minutes":15,"due_ms":due,
        "first_original_mature_source_id":chosen_id,
        "first_mature_quote_event_ms":ev,
        "first_mature_quote_available_ms":ev+100,
        "preaction_sha256":entry["preaction_sha256"],
        "source_prefix_receipts":[oldproof,proof],
        "selected_trading_action":"NO_TRADE",
        "quote_side_prices_are_NOT_guaranteed_fills":True,
        "financing_and_realized_broker_net_pnl_not_attested":True,
        "model_training_or_promotion_qualified":False,
        "actual_broker_execution_pnl_samples":0,"trade_authority":False,
        **vals
    }
    f=cell/"maturity-015.json"
    f.write_bytes(audit.canonical(outcome)+b"\n");f.chmod(0o600)
    attempt={
       "schema":audit.RESEARCH+".attempt.v1",
       "stage":"maturity-015","automatic_retry_allowed":False,
       "historical_price_or_future_quote_backfilled":False,
       "trade_authority":False,"broker_orders_sent":0,
       "state":"MATURE_ORIGINAL_QUOTE_PRICE_ONLY",
       "quote_result_sha256":audit.digest(f.read_bytes()),
       "attempt_recorded_at_ms":due+7000,
    }
    f=cell/"maturity-015-attempt.json"
    f.write_bytes(audit.canonical(attempt)+b"\n");f.chmod(0o600)
    window={"anchor_ms":A}
    return root,cell,plan,entry,pre,window,attempt

class OriginalEvidenceAdversarial(unittest.TestCase):
    def test_strict_chain_original_13_features_and_stressed_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            r,c,p,pre,a,prefix,ids=fixture(tmp)
            original,sha,pins=audit.frozen_feature_economics(r,c,p,{},a,A)
            self.assertEqual(original["input_source_ids"],ids)
            self.assertEqual(len(pins),1)
    def test_expired_older_bucket_is_not_a_valid_5min_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            r,c,p,pre,a,prefix,ids=fixture(tmp,expired_bucket=5)
            with self.assertRaisesRegex(audit.AuditHold,
                                        "OLDER_INPUT_QUOTE_EXPIRED_AT_ITS_GRID"):
                audit.frozen_feature_economics(r,c,p,{},a,A)
    def test_cost_cut_cannot_be_claimed_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            r,c,p,pre,a,prefix,ids=fixture(tmp,cost_lowered=True)
            with self.assertRaisesRegex(audit.AuditHold,"ORIGINAL_FROZEN_COST_WAS_CHANGED"):
                audit.frozen_feature_economics(r,c,p,{},a,A)
    def test_shachain_prefix_mutation_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            r,c,p,pre,a,prefix,ids=fixture(tmp)
            f=Path(prefix["path"])
            f.write_bytes(f.read_bytes().replace(b"1.10000",b"1.10009",1))
            with self.assertRaisesRegex(audit.AuditHold,"ORIGINAL_CAPTURE_PREFIX_CHANGED"):
                audit.exact_prefix_rows([prefix],audit.PERIOD_SHA)
    def test_reissued_prefix_hash_does_not_resign_internal_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            r,c,p,pre,a,prefix,ids=fixture(tmp)
            f=Path(prefix["path"])
            rows=f.read_bytes().splitlines()
            x=json.loads(rows[3]);x["previous"]="f"*64
            rows[3]=audit.canonical(x)
            raw=b"\n".join(rows)+b"\n"
            f.write_bytes(raw)
            prefix["prefix_sha256"]=audit.digest(raw)
            prefix["complete_prefix_bytes"]=len(raw)
            with self.assertRaisesRegex(audit.AuditHold,"ORIGINAL_CHAIN_BROKEN"):
                audit.exact_prefix_rows([prefix],audit.PERIOD_SHA)
    def test_public_source_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r,c,p,pre,a,prefix,ids=fixture(tmp)
            Path(prefix["path"]).chmod(0o644)
            with self.assertRaisesRegex(audit.AuditHold,"SOURCE_PREFIX_REPLACED_OR_PUBLIC"):
                audit.exact_prefix_rows([prefix],audit.PERIOD_SHA)
    def test_unqualified_health_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            r,c,p,pre,a,prefix,ids=fixture(tmp)
            pre["source_health"]["usable_probes"]=19
            f=c/"preaction.json"
            f.write_bytes(audit.canonical(pre)+b"\n")
            a["preaction_sha256"]=audit.digest(f.read_bytes())
            with self.assertRaisesRegex(audit.AuditHold,"FROZEN_SOURCE_READINESS_NOT_ELIGIBLE"):
                audit.frozen_feature_economics(r,c,p,{},a,A)
    def test_duplicate_json_keys_rejected(self):
        with self.assertRaisesRegex(audit.AuditHold,"DUPLICATE_JSON_KEY"):
            audit.strict(b'{"x":1,"x":2}')
    def test_nan_json_rejected(self):
        with self.assertRaisesRegex(audit.AuditHold,"NONFINITE_JSON"):
            audit.strict(b'{"x":NaN}')
    def test_output_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)/"private";d.mkdir(mode=0o700)
            f=d/"once.json"
            audit.write_exclusive(f,{"authority":False})
            with self.assertRaisesRegex(audit.AuditHold,"NONEXCLUSIVE_OUTPUT"):
                audit.write_exclusive(f,{"authority":True})

    def test_independent_first_quote_math(self):
        with tempfile.TemporaryDirectory() as tmp:
            r,c,p,entry,pre,w,att=mature_case(tmp)
            actual=audit.audit_maturity(
                r,c,p,entry,pre,entry["preaction_sha256"],w,15,A+900000+7000)
            self.assertEqual(actual["state"],"VERIFIED_ORIGINAL_QUOTE_ONLY")
            self.assertTrue(actual["quote_side_not_broker_fills"])
    def test_independent_wrong_midpoint_math_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r,c,p,entry,pre,w,att=mature_case(tmp,tamper_move=True)
            with self.assertRaisesRegex(audit.AuditHold,
                                         "FUTURE_ORIGINAL_QUOTE_SIDE_ARITHMETIC_CHANGED"):
                audit.audit_maturity(r,c,p,entry,pre,
                                    entry["preaction_sha256"],w,15,A+900000+7000)
    def test_independent_later_cherry_picked_quote_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r,c,p,entry,pre,w,att=mature_case(tmp,choose_later=True)
            with self.assertRaisesRegex(audit.AuditHold,
                                         "FUTURE_QUOTE_NOT_FIRST_ELIGIBLE"):
                audit.audit_maturity(r,c,p,entry,pre,
                                    entry["preaction_sha256"],w,15,A+900000+7000)
    def test_independent_late_maturity_seal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r,c,p,entry,pre,w,att=mature_case(tmp)
            f=c/"maturity-015-attempt.json"
            att["attempt_recorded_at_ms"]=A+900000+20000
            f.write_bytes(audit.canonical(att)+b"\n")
            with self.assertRaisesRegex(audit.AuditHold,
                                        "MATURITY_RECEIPT_DURABLE_OUTSIDE_FROZEN_DEADLINE"):
                audit.audit_maturity(r,c,p,entry,pre,
                                    entry["preaction_sha256"],w,15,A+900000+20000)

if __name__=="__main__":
    unittest.main()
