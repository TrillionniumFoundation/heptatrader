"""Portable, synthetic source-chain and price-pair verifier negative tests.

The physical qian-qi 10-case mutation suite separately covers actual
transferred Pocket4 evidence and is NOT shipped as raw market data.
"""
import json
import tempfile
import unittest
from pathlib import Path

from train_v2 import independent_cpu_pair as mod

AT=2000000000000

def example_source():
    events=[
       {"kind":"HEADER","at_ms":AT-100,"data":{
         "source":"IB_TOOL_GATEWAY","execution_domain":"PAPER",
         "instrument":"EUR.USD","period_sha256":"a"*64,
         "write_operations_allowed":[],"broker_orders_sent":0}},
    ]
    for i in range(13):
        event=AT+1000*i
        events.append({"kind":"QUOTE","at_ms":event+100,
           "data":{"instrument":"EUR.USD","source":"IB",
            "authoritative":True,"stale":False,
            "bid":str(mod.Decimal("1.10000")+mod.Decimal(i)/mod.Decimal(100000)),
            "ask":str(mod.Decimal("1.10010")+mod.Decimal(i)/mod.Decimal(100000)),
            "observed_at_ms":event,"stale_after_ms":event+5000}})
    events.append({"kind":"END","at_ms":AT+13000,
                   "data":{"broker_orders_sent":0}})
    result=[];previous="0"*64
    for seq,original in enumerate(events):
        row={"schema":"heptatrader.laya-shadow-quotes.v1",
             "sequence":seq,"previous":previous,**original}
        row["sha256"]=mod.digest(row)
        previous=row["sha256"]
        result.append(row)
    return result

def render(events):
    return b"\n".join(mod.canonical(r) for r in events)+b"\n"

def rehash(rows):
    previous="0"*64
    for i,r in enumerate(rows):
        r["sequence"]=i
        r["previous"]=previous
        r["sha256"]=mod.digest({k:v for k,v in r.items() if k!="sha256"})
        previous=r["sha256"]
    return rows

class OriginalChainTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/"quotes.jsonl"
        self.rows=example_source()
        self.path.write_bytes(render(self.rows))
        self.pin=mod.sha(self.path.read_bytes())

    def review(self,expected=None):
        return mod.original_source(self.path,self.pin if expected is None else expected)

    def replace(self,rows):
        self.path.write_bytes(render(rows))
        return mod.sha(self.path.read_bytes())

    def test_sealed_quote_source_and_immutable_hash(self):
        p=self.review()
        self.assertEqual(p["quote_count"],13)
        self.assertEqual(p["source_sha256"],self.pin)
        self.assertEqual(p["header"]["execution_domain"],"PAPER")

    def test_mutated_source_fails_external_pin(self):
        self.rows[1]["data"]["bid"]="2"
        new=self.replace(self.rows)
        self.assertNotEqual(new,self.pin)
        with self.assertRaisesRegex(mod.IndependentError,"SOURCE_SHA_OR_TAIL"):
            self.review()

    def test_modified_quote_even_with_new_file_hash_fails_chain(self):
        self.rows[1]["data"]["bid"]="1.999"
        new=self.replace(self.rows)
        with self.assertRaisesRegex(mod.IndependentError,"SOURCE_CHAIN_DIVERGED"):
            self.review(new)

    def test_missing_sealed_end_cannot_become_price(self):
        self.rows.pop()
        new=self.replace(self.rows)
        with self.assertRaisesRegex(mod.IndependentError,"SEGMENT_NOT_SEALED"):
            self.review(new)

    def test_forged_complete_expired_original_quote_rejected(self):
        self.rows[1]["data"]["stale_after_ms"]=AT
        new=self.replace(rehash(self.rows))
        with self.assertRaisesRegex(mod.IndependentError,"EXPIRED_AT_OBSERVATION"):
            self.review(new)

    def test_forged_complete_crossed_quote_rejected(self):
        self.rows[1]["data"]["bid"]="1.5000"
        new=self.replace(rehash(self.rows))
        with self.assertRaisesRegex(mod.IndependentError,"ORIGINAL_QUOTE_CROSSED"):
            self.review(new)

    def test_wrong_header_cannot_be_upgraded_by_rehash(self):
        self.rows[0]["data"]["write_operations_allowed"]=["placeOrder"]
        new=self.replace(rehash(self.rows))
        with self.assertRaisesRegex(mod.IndependentError,"ORIGINAL_HEADER_SCOPE_INVALID"):
            self.review(new)

    def test_end_record_claims_trade_forbidden(self):
        self.rows[-1]["data"]["broker_orders_sent"]=1
        new=self.replace(rehash(self.rows))
        with self.assertRaisesRegex(mod.IndependentError,"ORIGINAL_END_ORDER_SCOPE"):
            self.review(new)

    def test_duplicate_record_hash_rejected(self):
        self.rows[2]["sha256"]=self.rows[1]["sha256"]
        new=self.replace(self.rows)
        with self.assertRaisesRegex(mod.IndependentError,"SOURCE_CHAIN_DIVERGED"):
            self.review(new)

    def test_json_duplicate_key_refused(self):
        with self.assertRaisesRegex(mod.IndependentError,"DUPLICATE_JSON_KEY"):
            mod.strict_json(b'{"a":1,"a":2}')

    def test_nonfinite_json_refused(self):
        with self.assertRaises(mod.IndependentError):
            mod.strict_json(b'{"v":NaN}')

    def test_nonfinite_and_boolean_price_refused(self):
        for bad in ("NaN","Infinity",0,-1,True,None):
            with self.subTest(bad=bad),self.assertRaises(mod.IndependentError):
                mod.price(bad)

    def test_symlink_original_file_fails(self):
        target=self.path.with_name("copy")
        self.path.rename(target)
        self.path.symlink_to(target)
        with self.assertRaisesRegex(mod.IndependentError,"SOURCE_FILE_UNSAFE"):
            self.review()

    def test_pair_requires_existing_local_private_root(self):
        with self.assertRaises(mod.IndependentError):
            mod.audit_pair(self.path.parent/"missing","0"*64)

if __name__=="__main__":
    unittest.main()
