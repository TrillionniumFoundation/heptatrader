"""Adversarial tests: captured, immutable original local quote-gap diagnosis."""
from __future__ import annotations
import json,hashlib,tempfile,unittest
from pathlib import Path
from train_v2.fx_original_cache480_gap_forensics import (
    GapHold,PERIOD_SHA,STATUS_SHA,ORIGINAL_PLAN_SHA,RECEIPT_SHA,
    canonical,sha,strict,parse_original_prefix,quote_gaps,probe_matches,
    private_prefix,
)

def original_bytes(*,gap=True):
    seq=0
    prior="0"*64
    rows=[]
    def add(kind,data,at):
        nonlocal seq,prior
        msg={"schema":"heptatrader.laya-shadow-quotes.v1",
             "sequence":seq,"previous":prior,
             "at_ms":at,"kind":kind,"data":data}
        msg["sha256"]=sha(canonical(msg));prior=msg["sha256"];seq+=1
        rows.append(canonical(msg)+b"\n")
    add("HEADER",{"source":"IB_TOOL_GATEWAY","instrument":"EUR.USD",
                  "execution_domain":"PAPER","period_sha256":PERIOD_SHA,
                  "write_operations_allowed":[],"broker_orders_sent":0},100000)
    for t,s in ((101000,"ABC"),(102000,"ABC"),(125000,"DEF")):
        # The third quote can be an event gap after preceding quotes,
        # without pretending any cause was an executable fill.
        if not gap and t==125000:t=105000
        add("QUOTE",{"instrument":"EUR.USD","source":"IB",
                    "authoritative":True,"stale":False,
                    "bid":1.10,"ask":1.1002,
                    "observed_at_ms":t,"stale_after_ms":t+5000,
                    "subscription_id":s,"subscription_state":"active"},t+500)
        if t==102000:
            add("FAILURE",{"reason":"AUTHORITATIVE_QUOTE_STALE",
                           "quote_timestamp_rewritten":False},t+1500)
    add("END",{"broker_orders_sent":0},130000)
    return b"".join(rows)

def event(e,t,seq=0,avail=None,price="1.10"):
    return {"event":t,"available":t+100 if avail is None else avail,
            "seq":seq,"bid":price,"ask":"1.1002",
            "subscription_sha256":"f"*64}

class IntegrityTests(unittest.TestCase):
    def test_real_pinned_baseline_lengths(self):
        for v in (ORIGINAL_PLAN_SHA,RECEIPT_SHA,STATUS_SHA):
            self.assertEqual(len(v),64)
    def test_original_chain_parses_and_failures_retained(self):
        r=parse_original_prefix(original_bytes(),PERIOD_SHA)
        self.assertEqual(len(r["quotes"]),3)
        self.assertEqual(len(r["failures"]),1)
        self.assertTrue(r["ended"])
        self.assertEqual(r["failures"][0]["reason"],"AUTHORITATIVE_QUOTE_STALE")
        self.assertTrue(all(len(x["subscription_sha256"])==64
                            for x in r["quotes"]))
        self.assertNotIn("subscription_id",r["quotes"][0])
    def test_changed_header_period_refused_even_if_self_signed(self):
        raw=original_bytes()
        with self.assertRaisesRegex(GapHold,"ORIGINAL_HEADER_OR_PERIOD_CHANGED"):
            parse_original_prefix(raw,"e"*64)
    def test_byte_mutation_cannot_be_considered_same_chain(self):
        raw=original_bytes()
        mutated=raw.replace(b"1.1002",b"1.1003",1)
        with self.assertRaisesRegex(GapHold,"ORIGINAL_EVENT_CHAIN_INVALID"):
            parse_original_prefix(mutated,PERIOD_SHA)
    def test_truncated_full_line_refused(self):
        raw=original_bytes()
        with self.assertRaisesRegex(GapHold,"ORIGINAL_PREFIX_NOT_LINE_COMPLETE"):
            parse_original_prefix(raw[:-1],PERIOD_SHA)
    def test_unknown_non_quote_record_kind_refused(self):
        raw=original_bytes()
        with self.assertRaisesRegex(GapHold,"ORIGINAL_EVENT_CHAIN_INVALID"):
            parse_original_prefix(raw.replace(b'"kind":"FAILURE"',
                                             b'"kind":"TRADE"',1),PERIOD_SHA)
    def test_bad_source_numeric_refused(self):
        raw=original_bytes()
        with self.assertRaisesRegex(GapHold,"ORIGINAL_EVENT_CHAIN_INVALID"):
            parse_original_prefix(raw.replace(b'"bid":1.1',b'"bid":0.0',1),PERIOD_SHA)
    def test_duplicate_json_keys_refused(self):
        with self.assertRaisesRegex(GapHold,"DUPLICATE_JSON_KEY"):
            strict(b'{"x":1,"x":2}')
    def test_nonfinite_json_refused(self):
        with self.assertRaisesRegex(GapHold,"NONFINITE_JSON"):
            strict(b'{"x":NaN}')
    def test_public_original_prefix_refused(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"quotes.jsonl";p.write_bytes(original_bytes())
            p.chmod(0o644)
            with self.assertRaisesRegex(GapHold,"ORIGINAL_PREFIX_OWNER_INODE_SIZE"):
                private_prefix(p,p.stat().st_size)
    def test_snapshot_prefix_survives_append_only(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"quotes.jsonl";raw=original_bytes()
            p.write_bytes(raw);p.chmod(0o600)
            with p.open("ab") as f:f.write(b'{"later":"not eligible"}\n')
            self.assertEqual(private_prefix(p,len(raw)),raw)
            with p.open("r+b") as f:f.write(b'{"tamper":1}')
            self.assertNotEqual(sha(private_prefix(p,len(raw))),sha(raw))
    def test_symlink_not_a_source(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"real";p.write_bytes(original_bytes());p.chmod(0o600)
            alias=Path(td)/"quotes.jsonl";alias.symlink_to(p)
            with self.assertRaisesRegex(GapHold,"ORIGINAL_PREFIX_PATH_UNTRUSTED"):
                private_prefix(alias,p.stat().st_size)
    def test_quote_gap_uses_last_duplicate_before_and_first_after(self):
        r=[event(1,1000,seq=1,avail=1010),
           event(2,1000,seq=2,avail=1040),
           event(3,125000,seq=80,avail=125100),
           event(4,125000,seq=81,avail=125200)]
        gaps,count=quote_gaps(r)
        self.assertEqual(count,2)
        self.assertEqual(gaps[0][0],124000)
        self.assertEqual(gaps[0][1]["seq"],2)
        self.assertEqual(gaps[0][2]["seq"],80)
    def test_duplicate_quote_conflicting_price_refused(self):
        r=[event(1,1000),event(2,1000,price="1.2")]
        with self.assertRaisesRegex(GapHold,"SAME_TIMESTAMP_PRICE_CONFLICT"):
            quote_gaps(r)
    def test_two_endpoint_delays_and_three_path_gaps_are_correlated(self):
        # Exact logic: the first original post-due quote is >5s late,
        # or the 30s+ gap intersects the frozen path.
        events=[event(1,10000),event(2,133095)]
        probes=[
            {"status":"ENDPOINT_GAP","start_ms":0,"due_ms":30000,
             "actual_delay_ms":103095},
            {"status":"ENDPOINT_GAP","start_ms":10000,"due_ms":50000,
             "actual_delay_ms":83095},
            {"status":"PATH_GAP","start_ms":0,"anchor_ms":20000,"due_ms":90000},
            {"status":"PATH_GAP","start_ms":10000,"anchor_ms":40000,"due_ms":120000},
            {"status":"PATH_GAP","start_ms":30000,"anchor_ms":50000,"due_ms":150000},
            {"status":"USABLE","start_ms":150001}
        ]
        actual,c=probe_matches(probes,10000,133095,events)
        self.assertEqual(c,{"ENDPOINT_GAP":2,"PATH_GAP":3})
        self.assertEqual(len(actual),5)
        self.assertTrue(all(x["matched_gap_evidence"] for x in actual))
    def test_false_endpoint_claim_not_explained(self):
        events=[event(1,10000),event(2,133095)]
        p=[{"status":"ENDPOINT_GAP","start_ms":0,"due_ms":30000,
            "actual_delay_ms":1}]
        actual,_=probe_matches(p,10000,133095,events)
        self.assertFalse(actual[0]["matched_gap_evidence"])
    def test_input_unavailable_during_gap_can_be_associated(self):
        actual,_=probe_matches(
            [{"status":"INPUT_UNAVAILABLE","start_ms":20000}],
            10000,133095,[event(1,10000),event(2,133095)])
        self.assertTrue(actual[0]["matched_gap_evidence"])
    def test_short_quote_gap_not_material(self):
        with self.assertRaisesRegex(GapHold,"NO_MATERIAL_ORIGINAL_SOURCE_GAP"):
            probe_matches([],100,10000,[])

if __name__=="__main__":
    unittest.main()
