"""Original source-gap forensics: report absence, never manufacture readiness."""
import copy
import unittest
from historical_training.common import EvidenceError
from laya_cell.source_readiness import RULE
from train_v2.source_gap_forensics import diagnose

AT=1791906900000

def status(probe_states=None):
    states=probe_states or ["INPUT_UNAVAILABLE"]*24
    last=((AT-RULE["horizon_ms"]-RULE["input_grace_ms"]-
           RULE["endpoint_tolerance_ms"])//RULE["spacing_ms"])*RULE["spacing_ms"]
    starts=[last-i*RULE["spacing_ms"] for i in reversed(range(24))]
    return {
        "schema":"heptatrader.quote-readiness-status.v1",
        "at_ms":AT,
        "period_sha256":"a"*64,
        "rule":copy.deepcopy(RULE),
        "state":"HOLD_NEW_FUSION_WINDOW",
        "reasons":["INSUFFICIENT_COMPLETE_HISTORY_SPAN",
                   "INSUFFICIENT_ENDPOINT_AND_PATH_COVERAGE"],
        "probes":[{"start_ms":t,"status":st,
                   "input_failures":{}} for t,st in zip(starts,states)],
    }

def quote(t,latency=220):
    return {"t":t,"known_at_ms":t+latency,
            "bid":1.01,"ask":1.02}

class SourceGapTests(unittest.TestCase):
    def test_empty_original_source_does_not_become_training(self):
        x=diagnose([],status(),observation_now_ms=AT+1000)
        self.assertEqual(x["source_anchor_diagnosis"]["input_empty"],24)
        self.assertEqual(x["source_operational_admission"],
                         "UNCHANGED_ORIGINAL_HOLD")
        self.assertFalse(x["future_readiness_claim"])
        self.assertFalse(x["past_missing_windows_repaired"])
        self.assertEqual(x["broker_orders_sent"],0)

    def test_original_feed_resumed_after_unseen_minutes(self):
        y=status()
        t=y["probes"][16]["start_ms"]
        ticks=[quote(t+i*10000) for i in range(48)]
        x=diagnose(ticks,y,observation_now_ms=AT+1000)
        self.assertEqual(x["source_anchor_diagnosis"]["input_empty"],16)
        self.assertEqual(x["source_anchor_diagnosis"][
            "input_rejected_after_source"],8)
        self.assertEqual(x["last_contiguous_quote_suffix_ms"],470000)
        self.assertEqual(x["largest_actual_quote_gap_ms"],0)
        self.assertFalse(x["gpu_training_admitted"])

    def test_status_has_usable_probe_without_witness_reject(self):
        y=status(["USABLE"]+["INPUT_UNAVAILABLE"]*23)
        with self.assertRaisesRegex(
                EvidenceError,"ORIGINAL_USABLE_PROBE_WITHOUT_SOURCE"):
            diagnose([],y,observation_now_ms=AT)

    def test_anchor_outside_fixed_minute_not_backdated(self):
        y=status()
        # One second before the ENTIRE 24-minute grid; one second before
        # only its final minute would legitimately belong to the prior slot.
        start=y["probes"][0]["start_ms"]
        data=[quote(start-1000)]
        x=diagnose(data,y,observation_now_ms=AT)
        self.assertEqual(x["source_anchor_diagnosis"]["input_empty"],24)

    def test_arrives_after_minute_not_backdated(self):
        y=status()
        start=y["probes"][-1]["start_ms"]
        data=[{"t":start+1000,"known_at_ms":start+60001}]
        x=diagnose(data,y,observation_now_ms=AT)
        self.assertEqual(x["source_anchor_diagnosis"]["input_empty"],24)

    def test_long_actual_gap_is_counted_without_interpolation(self):
        y=status()
        start=y["probes"][-1]["start_ms"]
        x=diagnose([quote(start+1000),quote(start+70000)],
                   y,observation_now_ms=AT)
        self.assertEqual(x["largest_actual_quote_gap_ms"],69000)
        self.assertEqual(x["large_actual_quote_gap_count"],1)
        self.assertEqual(x["last_contiguous_quote_suffix_ms"],0)

    def test_wrong_status_schema_and_grid_refused(self):
        y=status();y["probes"][-1]["start_ms"]+=1000
        with self.assertRaisesRegex(EvidenceError,
                                     "ORIGINAL_FIXED_GRID_MISMATCH"):
            diagnose([],y,observation_now_ms=AT)
        y=status();y["schema"]="not original"
        with self.assertRaisesRegex(EvidenceError,
                                     "ORIGINAL_SOURCE_STATUS_SCHEMA"):
            diagnose([],y,observation_now_ms=AT)

    def test_changed_original_guard_rules_refused(self):
        y=status();y["rule"]["input_grace_ms"]+=1
        with self.assertRaisesRegex(
                EvidenceError,"ORIGINAL_SOURCE_POLICY_CHANGED"):
            diagnose([],y,observation_now_ms=AT)

    def test_old_status_or_future_status_does_not_qualify(self):
        with self.assertRaisesRegex(
                EvidenceError,"ORIGINAL_SOURCE_STATUS_NOT_RECENT"):
            diagnose([],status(),observation_now_ms=AT+120001)
        with self.assertRaisesRegex(
                EvidenceError,"ORIGINAL_SOURCE_STATUS_NOT_RECENT"):
            diagnose([],status(),observation_now_ms=AT-1)

    def test_quote_timestamp_inversion_refused(self):
        y=status();start=y["probes"][0]["start_ms"]
        with self.assertRaisesRegex(
                EvidenceError,"ORIGINAL_QUOTE_TIMES_INVALID"):
            diagnose([{"t":start+2000,"known_at_ms":start+1000}],
                     y,observation_now_ms=AT)

    def test_reordered_quote_ids_refused(self):
        y=status();start=y["probes"][0]["start_ms"]
        with self.assertRaisesRegex(EvidenceError,
                                     "ORIGINAL_QUOTE_NOT_ORDERED"):
            diagnose([quote(start+2000),quote(start+1000)],
                     y,observation_now_ms=AT)

    def test_unexpected_promotion_status_refused(self):
        y=status();y["probes"][0]["status"]="AUTO_TRAINED"
        with self.assertRaisesRegex(
                EvidenceError,"ORIGINAL_PROBE_UNEXPECTED_STATUS"):
            diagnose([],y,observation_now_ms=AT)

    def test_original_ready_is_still_review_only_not_training(self):
        y=status(["USABLE"]*24)
        y["state"]="READY_FOR_REVIEW_NOT_AUTHORIZATION"
        y["reasons"]=[]
        quotes=[quote(p["start_ms"]+1000) for p in y["probes"]]
        x=diagnose(quotes,y,observation_now_ms=AT)
        self.assertEqual(x["source_operational_admission"],
                         "UNCHANGED_REVIEW_ONLY_NO_TRAINING")
        self.assertFalse(x["future_readiness_claim"])
        self.assertFalse(x["gpu_training_admitted"])

    def test_forged_ready_without_original_matured_windows_denied(self):
        y=status()
        y["state"]="READY_FOR_REVIEW_NOT_AUTHORIZATION"
        y["reasons"]=[]
        with self.assertRaisesRegex(
                EvidenceError,"ORIGINAL_READY_WITHOUT_USABLE_PROBES"):
            diagnose([],y,observation_now_ms=AT)

    def test_contradictory_original_ready_and_hold_reasons_denied(self):
        y=status()
        y["state"]="READY_FOR_REVIEW_NOT_AUTHORIZATION"
        with self.assertRaisesRegex(
                EvidenceError,"ORIGINAL_SOURCE_STATE_REASON_CONFLICT"):
            diagnose([],y,observation_now_ms=AT)

    def test_latest_source_quote_cannot_be_forged(self):
        y=status()
        y["latest_source_quote_ms"]=y["probes"][-1]["start_ms"]
        x=quote(y["probes"][-1]["start_ms"]+1000)
        with self.assertRaisesRegex(
                EvidenceError,"ORIGINAL_SOURCE_LATEST_QUOTE_MISMATCH"):
            diagnose([x],y,observation_now_ms=AT)

    def test_original_status_mutation_during_read_is_rejected(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from train_v2.source_gap_forensics import run
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            cfg=root/"config.json"
            status_path=root/"status.json"
            cfg.write_text(json.dumps({"period_sha256":"a"*64}))
            status_path.write_text(json.dumps(status()))
            from historical_training.common import read as real_read
            def tamper(path,*args,**kwargs):
                value=real_read(path,*args,**kwargs)
                if str(path)==str(status_path):
                    status_path.write_text(json.dumps(
                        {**value,"at_ms":value["at_ms"]+1000}))
                return value
            with patch("train_v2.source_gap_forensics.read",side_effect=tamper):
                with self.assertRaisesRegex(
                    EvidenceError,"ORIGINAL_CONFIG_OR_STATUS_CHANGED_DURING_READ"):
                    run(cfg,status_path)

    def test_original_status_sha_pinned_to_same_read_bytes(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from historical_training.common import sha
        from train_v2.source_gap_forensics import run
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            cfg=root/"config.json"
            status_path=root/"status.json"
            cfg.write_text(json.dumps({"period_sha256":"a"*64}))
            status_path.write_text(json.dumps(status()))
            want=sha(status_path)
            with patch("laya_cell.resident_data.read_quotes",
                       return_value=([],[])),patch(
                       "train_v2.source_gap_forensics.time.time",
                       return_value=(AT+1000)/1000):
                item=run(cfg,status_path)
            self.assertEqual(item["original_status_sha256"],want)
            self.assertFalse(item["future_readiness_claim"])

    def test_original_existing_source_window_preserved(self):
        y=status(["INPUT_UNAVAILABLE"]*23+["PATH_GAP"])
        start=y["probes"][-1]["start_ms"]
        x=diagnose([quote(start+200),quote(start+60000)],
                   y,observation_now_ms=AT)
        self.assertEqual(x["original_grid_probes"][-1][
            "root_cause_class"],"SOURCE_PRESENT_ORIGINAL_STATUS_PATH_GAP")
        self.assertFalse(x["historical_revised_bars_substituted"])
