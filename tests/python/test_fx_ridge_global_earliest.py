"""Host-local complete-prefix earliest-quote attacks, without touching PAPER."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

from train_v2.fx_ridge_global_earliest import (
    AuditReject, NUMERIC, original_quotes,
    numeric_source_inventory, require_original_first_maturity,
    strict_line,
    digest, canonical,
)

PERIOD = "a" * 64
DUE = 1791803100000
MATURED = DUE + 7500

def quote(index, event, arrival, *, bid="1.10000", ask="1.10002",
          expiry=None):
    return {
        "instrument": "EUR.USD",
        "source": "IB",
        "authoritative": True,
        "stale": False,
        "bid": bid, "ask": ask,
        "observed_at_ms": event,
        "stale_after_ms": expiry if expiry is not None else event + 4500,
        "subscription_id": "LOCAL-PAPER-TEST-ONLY",
        "subscription_state": "active",
        "_fixture_index": index,
    }

def build_segment(root, number, records):
    path = root / f"segment-{number}-aabbccdd"
    path.mkdir(mode=0o700)
    header = {
        "source": "IB_TOOL_GATEWAY",
        "instrument": "EUR.USD",
        "execution_domain": "PAPER",
        "period_sha256": PERIOD,
        "write_operations_allowed": [],
        "broker_orders_sent": 0,
    }
    events = [("HEADER", header, DUE-120000)]+[
        ("QUOTE", row, at) for row, at in records]
    predecessor = "0"*64
    lines = []
    receipts=[]
    for i,(kind,data,at) in enumerate(events):
        value = {
            "schema":"heptatrader.laya-shadow-quotes.v1",
            "sequence":i, "previous":predecessor,
            "at_ms":at, "kind":kind, "data":data,
        }
        value["sha256"]=digest(value)
        predecessor=value["sha256"]
        lines.append(canonical(value))
        receipts.append(predecessor)
    filename=path/"quotes.jsonl"
    filename.write_bytes(b"\n".join(lines)+b"\n")
    filename.chmod(0o600)
    return filename,receipts

class GlobalEarliestTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.root.chmod(0o700)
        self.early=quote(1,DUE+100,DUE+210)
        self.late=quote(2,DUE+1400,DUE+1500,bid="1.1001",ask="1.10012")
        _,receipts_a=build_segment(
            self.root,(DUE-120000)*1000000,[(self.early,DUE+210)])
        _,receipts_b=build_segment(
            self.root,(DUE-60000)*1000000,[(self.late,DUE+1500)])
        self.first_receipt=receipts_a[-1]
        self.second_receipt=receipts_b[-1]

    def scan(self, *, due=DUE, maturity=MATURED):
        return original_quotes(
            self.root, due_at_ms=due,
            maturity_at_ms=maturity, period_sha=PERIOD)

    def test_omitted_earlier_segment_is_detected(self):
        first,evidence=self.scan()
        self.assertEqual(first["quote_receipt"],self.first_receipt)
        self.assertEqual(first["event_at_ms"],DUE+100)
        self.assertEqual(evidence["numeric_writer_segments_audited"],2)
        # A producer who only submitted the second segment must FAIL.
        with self.assertRaisesRegex(AuditReject,
                                    "GLOBAL_ORIGINAL_OMITTED_EARLIER"):
            require_original_first_maturity({
                "price_event_ms":DUE+1400,
                "price_available_ms":DUE+1500,
                "outcome_quote_sha256":self.second_receipt},first)
        require_original_first_maturity({
            "price_event_ms":DUE+100,
            "price_available_ms":DUE+210,
            "outcome_quote_sha256":self.first_receipt},first)

    def test_identical_byte_scan_is_deterministic(self):
        a,pa=self.scan()
        b,pb=self.scan()
        self.assertEqual(a,b)
        self.assertEqual(pa,pb)
        self.assertFalse("broker_order_id" in a)
        self.assertEqual(pa["broker_requests_sent"],0)

    def test_early_arrival_cannot_be_repaired_post_maturity(self):
        chosen,_=self.scan(maturity=DUE+150)
        self.assertIsNone(chosen)  # both records were only available later

    def test_late_creation_segment_is_not_backdated(self):
        extra=quote(3,DUE+10,DUE+20)
        build_segment(self.root,(DUE+20000)*1000000,
                      [(extra,DUE+20)])
        first,proof=self.scan()
        self.assertEqual(first["quote_receipt"],self.first_receipt)
        self.assertEqual(proof["numeric_writer_segments_audited"],2)

    def test_conflicting_same_timestamp_is_refused(self):
        third=quote(3,DUE+100,DUE+300,bid="1.2",ask="1.201")
        build_segment(self.root,(DUE-30000)*1000000,
                      [(third,DUE+300)])
        with self.assertRaisesRegex(AuditReject,
                                    "GLOBAL_ORIGINAL_SAME_TIMESTAMP_PRICE_CONFLICT"):
            self.scan()

    def test_original_record_change_breaks_chain(self):
        path=next(self.root.glob("segment-*/quotes.jsonl"))
        raw=path.read_bytes()
        self.assertIn(b'"source":"IB"', raw)
        path.write_bytes(raw.replace(b'"source":"IB"',b'"source":"XX"',1))
        with self.assertRaisesRegex(AuditReject,"GLOBAL_ORIGINAL_CHAIN_INVALID"):
            self.scan()

    def test_missing_original_source_file_refused(self):
        path=next(self.root.glob("segment-*/quotes.jsonl"))
        path.unlink()
        with self.assertRaisesRegex(AuditReject,
                                    "GLOBAL_ORIGINAL_SEGMENT_MISSING_QUOTE_FILE"):
            self.scan()

    def test_untrusted_segment_symlink_refused(self):
        source=next(self.root.glob("segment-*"))
        fake=self.root/f"segment-{(DUE-30000)*1000000}-cccccccc"
        fake.symlink_to(source,target_is_directory=True)
        with self.assertRaises(AuditReject):
            self.scan()

    def test_bad_file_mode_refused(self):
        path=next(self.root.glob("segment-*/quotes.jsonl"))
        path.chmod(0o644)
        with self.assertRaisesRegex(AuditReject,
                                    "GLOBAL_ORIGINAL_QUOTE_CUSTODY_OR_BOUND"):
            self.scan()

    def test_corrupted_json_and_hash_chain_refused(self):
        path=next(self.root.glob("segment-*/quotes.jsonl"))
        raw=path.read_bytes().splitlines()
        value=json.loads(raw[-1])
        value["sequence"]=0
        raw[-1]=canonical(value)
        path.write_bytes(b"\n".join(raw)+b"\n")
        with self.assertRaisesRegex(AuditReject,"GLOBAL_ORIGINAL_CHAIN_INVALID"):
            self.scan()

    def test_duplicate_json_key_refused(self):
        with self.assertRaisesRegex(AuditReject,
                                    "GLOBAL_ORIGINAL_DUPLICATE_JSON_KEY"):
            strict_line(b'{"a":1,"a":2}')

    def test_nan_numeric_refused(self):
        with self.assertRaisesRegex(AuditReject,"GLOBAL_ORIGINAL_NONFINITE_JSON"):
            strict_line(b'{"v":NaN}')

    def test_original_end_invalid_or_expired_is_refused(self):
        path=next(self.root.glob("segment-*/quotes.jsonl"))
        bad=quote(7,DUE+100,DUE+210,expiry=DUE+200)
        replacement,_=build_segment(
            self.root,(DUE-30000)*1000000,[(bad,DUE+210)])
        with self.assertRaisesRegex(AuditReject,"GLOBAL_ORIGINAL_NOT_AUTHORITY_QUALIFIED"):
            self.scan()

    def test_recovery_live_is_not_accepted_as_numeric_new_segment(self):
        legacy=self.root/"segment-recovery-live"
        legacy.mkdir(mode=0o700)
        first,proof=self.scan()
        self.assertEqual(first["quote_receipt"],self.first_receipt)
        self.assertEqual(proof["numeric_writer_segments_audited"],2)
