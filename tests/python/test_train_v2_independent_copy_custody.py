"""Portable copied-original archive/pair tests; no broker or signer privileges."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from decimal import Decimal
from paged_crosshost_custody import (
    CustodyError, canonical, sha, original_segment,
    copied_original_inventory, verify_copied_pair,
)

PERIOD="e"*64
ORIGINAL="/home/alex/sandbox/original-feed"
BASE=1791600000000

class SnapshotFixture(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.parent=Path(tmp.name)
        self.root=self.parent/"snapshot"
        self.root.mkdir(mode=0o700)
        self.archive=self.root/"source-archive-v1"
        self.archive.mkdir(mode=0o700)
        self.evidence=self.parent/"evidence"
        self.evidence.mkdir(mode=0o700)
        self.rows=[]
        self.segments=[]
        self.copies=[]

    def event(self,sequence,previous,at,kind,data):
        body={"schema":"heptatrader.laya-shadow-quotes.v1","sequence":sequence,
              "previous":previous,"at_ms":at,"kind":kind,"data":data}
        return {**body,"sha256":sha(canonical(body))}

    def build(self,number):
        for i in range(number):
            at=BASE+i*10000
            folder=self.root/f"segment-{at*1000000:019d}-{i:08x}"
            folder.mkdir(mode=0o700)
            name=folder/"quotes.jsonl"
            bid=Decimal("1.10000")+Decimal(i)/Decimal(100000)
            ask=bid+Decimal("0.00003")
            header={"source":"IB_TOOL_GATEWAY","instrument":"EUR.USD",
                    "execution_domain":"PAPER","period_sha256":PERIOD,
                    "write_operations_allowed":[],"broker_orders_sent":0}
            chain=[]
            previous="0"*64
            for k,datum,stamp in (
                ("HEADER",header,at),
                ("QUOTE",{
                    "source":"IB","instrument":"EUR.USD",
                    "authoritative":True,"stale":False,
                    "bid":str(bid),"ask":str(ask),
                    "observed_at_ms":at+5,"stale_after_ms":at+5000,
                    "subscription_id":"test-paged-custody",
                    "subscription_state":"active"},at+10),
                ("END",{"broker_orders_sent":0},at+15)):
                row=self.event(len(chain),previous,stamp,k,datum)
                previous=row["sha256"]
                chain.append(canonical(row)+b"\n")
            name.write_bytes(b"".join(chain))
            name.chmod(0o600)
            origin=f"{ORIGINAL}/{folder.name}/quotes.jsonl"
            proof,quotes=original_segment(name,origin,PERIOD)
            self.copies.append(proof)
            self.rows.extend(quotes)
            self.segments.append(name)
        self.pages()

    def pages(self):
        previous_page="0"*64
        previous_ledger="0"*64
        lines=[]
        for k,start in enumerate(range(0,len(self.copies)//32*32,32)):
            doc={"schema":"heptatrader.laya-source-archive-page.v1",
                 "index":k,"previous_sha256":previous_page,
                 "period_sha256":PERIOD,
                 "prefixes":self.copies[start:start+32]}
            raw=canonical(doc)+b"\n"
            page=self.archive/f"page-{k:08d}.json"
            page.write_bytes(raw);page.chmod(0o600)
            page_sha=sha(raw)
            body={"schema":"heptatrader.laya-archive-ledger.v1",
                  "index":k,"page_sha256":page_sha,
                  "previous_receipt_sha256":previous_ledger}
            ledger={**body,"receipt_sha256":sha(canonical(body))}
            lines.append(canonical(ledger)+b"\n")
            previous_page=page_sha
            previous_ledger=ledger["receipt_sha256"]
        ledger=self.archive/"ledger.jsonl"
        ledger.write_bytes(b"".join(lines));ledger.chmod(0o600)

    def scan(self,from_index=0,until_index=1):
        return copied_original_inventory(
            self.root,ORIGINAL,PERIOD,
            BASE+from_index*10000,
            BASE+until_index*10000+20)

    def create_pair(self):
        source=self.scan(0,80)
        records=[]
        for slot,i in enumerate((2,38),start=1):
            first=self.rows[i]
            future=self.rows[i+30]
            delta=(future[4]/first[4]-1)*Decimal(10000)
            predicted=Decimal("0.7") if slot==1 else Decimal("-0.8")
            direction=((predicted>0)==(delta>0))
            native={"schema":"heptatrader.native-price-ticks-sealed-outcome.v1",
                    "state":"OBSERVED_ORIGINAL_PAPER_MID_PRICE_ONLY",
                    "plan_id":f"native-plan-{slot}",
                    "source_quote_receipt":first[3],
                    "source_event_at_ms":first[1],
                    "original_quote_known_at_ms":first[0],
                    "original_price_due_at_ms":first[1]+300000,
                    "original_source_sealed_segments":len(self.copies),
                    "original_source_fingerprint_sha256":
                        source["source_fingerprint_sha256"],
                    "model_score_fixed_bps":str(predicted),
                    "original_price_change_bps":str(delta),
                    "price_direction_match":direction,
                    "original_price_only_not_executable_fill":True,
                    "actual_broker_fills":0,
                    "actual_executed_net_pnl_samples":0,
                    "native_f64_exact_replay_attested":False,
                    "model_promotion_permitted":False,
                    "broker_orders_sent":0}
            path=self.evidence/f"real-ticks-{slot:02d}-price-after-natural-seal.json"
            path.write_bytes(canonical(native)+b"\n");path.chmod(0o600)
            records.append({
                "plan_id":native["plan_id"],
                "original_quote_receipt":first[3],
                "original_outcome_sha256":sha(path.read_bytes()),
                "new_paged_price_change_bps":str(delta),
                "old_price_change_bps":str(delta),
                "direction_match":direction})
        pair={"schema":"heptatrader.train-v2-paged-original-price-crosscheck.v1",
              "state":"TWO_PRIOR_PRICE_LABELS_EXACTLY_REPRODUCED_RESEARCH_ONLY",
              "source_period_sha256":PERIOD,"broker_orders_sent":0,
              "actually_executed_trades":0,
              "original_predictions_resealed":False,
              "rows":records}
        path=self.evidence/"prior-native-price-pair-paged-crosscheck.json"
        path.write_bytes(canonical(pair)+b"\n");path.chmod(0o600)
        return sha(path.read_bytes()),source["source_fingerprint_sha256"]

    def test_exact_page_ledger_and_full_segment_proof(self):
        self.build(32)
        r=self.scan(0,31)
        self.assertEqual(r["numeric_segments"],32)
        self.assertEqual(r["archive_pages"],1)
        self.assertEqual(r["original_total_unique_quote_receipts"],32)
        self.assertEqual(r["archived_segments"],32)
        self.assertEqual(r["frozen_prefix_sha256"](32),
                         r["source_fingerprint_sha256"])

    def test_paged_survives_321_segment_legacy_limit(self):
        self.build(321)
        r=self.scan(319,320)
        self.assertEqual(r["numeric_segments"],321)
        self.assertEqual(r["archive_pages"],10)
        self.assertEqual(r["archived_segments"],320)
        self.assertEqual(r["original_total_unique_quote_receipts"],321)
        self.assertEqual(len(r["window_rows"]),2)

    def test_copied_old_quote_corruption_refused(self):
        self.build(32)
        name=self.segments[0]
        name.write_bytes(name.read_bytes().replace(b"1.10000",b"1.12000",1))
        with self.assertRaisesRegex(CustodyError,"ORIGINAL_LINE_HASH_OR_SEQUENCE"):
            self.scan(30,31)

    def test_archive_page_tamper_refused(self):
        self.build(32)
        page=self.archive/"page-00000000.json"
        page.write_bytes(page.read_bytes().replace(b'"index":0',b'"index":1'))
        with self.assertRaises(CustodyError):
            self.scan(30,31)

    def test_archive_ledger_tamper_refused(self):
        self.build(32)
        ledger=self.archive/"ledger.jsonl"
        ledger.write_bytes(ledger.read_bytes().replace(b"page_sha256",b"bad_page_sha",1))
        with self.assertRaises(CustodyError):
            self.scan(30,31)

    def test_missing_frozen_original_segment_refused(self):
        self.build(32)
        self.segments[1].unlink()
        with self.assertRaises(CustodyError):
            self.scan(30,31)

    def test_duplicate_original_receipt_refused(self):
        self.build(2)
        self.segments[1].write_bytes(self.segments[0].read_bytes())
        with self.assertRaisesRegex(CustodyError,"COPIED_ORIGINAL_RECEIPT_REUSED"):
            self.scan(0,1)

    def test_two_price_outcomes_independently_reproduced(self):
        self.build(75)
        pair_sha,source_sha=self.create_pair()
        r=verify_copied_pair(
            self.root,ORIGINAL,PERIOD,self.evidence,
            expected_pair_sha256=pair_sha,expected_prefixes={75:source_sha})
        self.assertEqual(len(r["price_only_outcomes"]),2)
        self.assertEqual(r["actual_broker_executed_trades"],0)
        self.assertEqual(r["price_direction_correct_count"],1)
        self.assertFalse(r["after_cost_profit_attested"])

    def test_rehashed_fraudulent_positive_trade_outcome_refused(self):
        self.build(75)
        pair_sha,source_sha=self.create_pair()
        file=self.evidence/"real-ticks-01-price-after-natural-seal.json"
        body=json.loads(file.read_bytes())
        body["actual_broker_fills"]=1
        file.write_bytes(canonical(body)+b"\n")
        pair_file=self.evidence/"prior-native-price-pair-paged-crosscheck.json"
        pair=json.loads(pair_file.read_bytes())
        pair["rows"][0]["original_outcome_sha256"]=sha(file.read_bytes())
        pair_file.write_bytes(canonical(pair)+b"\n")
        with self.assertRaisesRegex(CustodyError,"OUTCOME_NOT_PRICE_ONLY_OR_AUTHORITY"):
            verify_copied_pair(self.root,ORIGINAL,PERIOD,self.evidence,
                expected_pair_sha256=sha(pair_file.read_bytes()),
                expected_prefixes={75:source_sha})

    def test_wrong_transport_original_root_fails_archived_identity(self):
        self.build(32)
        with self.assertRaisesRegex(CustodyError,"COPIED_ARCHIVE_SEGMENT_CHECKPOINT_CHANGED"):
            copied_original_inventory(self.root,"/tmp/other-root",PERIOD,BASE,BASE+10000)
