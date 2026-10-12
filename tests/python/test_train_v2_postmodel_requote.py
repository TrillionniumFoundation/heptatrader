"""Adversarial post-model as-of-requote analysis: no original action rebind.

Synthetic fixture keys/prices never create prospective trades or product
propensity. Original source archive is separately checked on Pocket4.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from laya_cell.shadow_source import canonical as source_canonical
from laya_cell.shadow_source import digest as source_digest
from laya_cell.shadow_source import strict as source_strict

from train_v2.contracts import ContractError
from train_v2.postmodel_requote import (
    SCHEMA, POST_RETURN_BUDGETS_MS, _price, _mid,
    asof_requote, describe_int, analyze_join, load_original_quote_archive,
    source_model_returns,
)
from test_train_v2_phase2 import OriginalSourceFixture


class SyntheticAsOfTest(unittest.TestCase):
    def setUp(self):
        self.quotes = [
            (1000, 900, 1400, "a"*64, Decimal("1.10000")),
            (1800, 1700, 1900, "b"*64, Decimal("1.10020")),
        ]
        self.known = [1000, 1800]
        self.archive = {
            "rows": self.quotes,
            "known_times": self.known,
            "first_available_ms": 1000,
            "last_available_ms": 1800,
            "source_fingerprint_sha256": "f"*64,
            "sealed_segments": 1,
            "empty_segments": 0,
            "unsealed_newest_excluded": False,
        }

    def observation(self, at, action):
        return {
            "original_model_decision_at_ms": at,
            "original_input_quote_at_ms": 800,
            "original_request_at_ms": 900,
            "original_action": action,
            "original_input_mid": Decimal("1.09900"),
            "original_manifest_sha256": "c"*64,
        }

    def test_no_future_source_before_first_record(self):
        status, item = asof_requote(self.known, self.quotes, 999)
        self.assertEqual(status, "BEFORE_RECORDED_SOURCE")
        self.assertIsNone(item)

    def test_after_last_sealed_record_is_unknown_not_fresh(self):
        status, item = asof_requote(self.known, self.quotes, 2100)
        self.assertEqual(status, "AFTER_LAST_SEALED_QUOTE")
        self.assertIsNone(item)

    def test_known_latest_but_expired_rejected(self):
        status, item = asof_requote(self.known, self.quotes, 1600)
        self.assertEqual(status, "NO_FRESH_QUOTE_AT_MODEL_RETURN")
        self.assertIsNone(item)

    def test_exact_source_known_time_and_exact_expiry_are_allowed_upper_bounds(self):
        self.assertEqual(asof_requote(self.known, self.quotes, 1000)[0],
                         "NEW_QUOTE_AVAILABLE_FOR_HYPOTHETICAL_NEW_POLICY")
        self.assertEqual(asof_requote(self.known, self.quotes, 1400)[0],
                         "NEW_QUOTE_AVAILABLE_FOR_HYPOTHETICAL_NEW_POLICY")

    def test_latest_quote_asof_not_future_quote(self):
        status, row = asof_requote(self.known, self.quotes, 1200)
        self.assertEqual(status, "NEW_QUOTE_AVAILABLE_FOR_HYPOTHETICAL_NEW_POLICY")
        self.assertEqual(row[3], "a"*64)
        self.assertNotEqual(row[3], "b"*64)

    def test_strict_invalid_or_crossed_prices(self):
        for bad in ("nan", "Infinity", "-1", "0", False, None):
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                _price(bad)
        with self.assertRaisesRegex(ContractError, "REQUOTING_CROSSED_MARKET"):
            _mid({"bid": 1.20, "ask": 1.10})

    def test_invalid_model_timestamp_refused(self):
        for bad in (0, -1, True, "1000", None):
            with self.subTest(bad=bad), self.assertRaisesRegex(
                ContractError, "REQUOTING_DECISION_TIME_INVALID"):
                asof_requote(self.known, self.quotes, bad)

    def test_denominators_and_hypothetical_overhead_budgets(self):
        samples = [
            self.observation(1200, "BUY"),
            self.observation(1600, "SELL"),
            self.observation(2000, "HOLD"),
        ]
        result = analyze_join(self.archive, samples, [], Counter())
        self.assertEqual(result["schema"], SCHEMA)
        counts = result["observations"]
        self.assertEqual(counts["verified_original_model_returns"], 3)
        self.assertEqual(counts["in_attested_sealed_source_time_bounds"], 2)
        self.assertEqual(counts["fresh_local_quote_asof_model_return_upper_bound"], 1)
        self.assertEqual(counts["no_original_local_fresh_quote_known_at_model_return"], 1)
        self.assertEqual(counts["outside_attested_sealed_source_coverage_unknown"], 1)
        budgets = result["hypothetical_new_policy_after_return_budgets_ms"]
        self.assertEqual(set(map(int, budgets)), set(POST_RETURN_BUDGETS_MS))
        self.assertEqual(budgets["0"]["all_model_returns"], 1)
        self.assertEqual(budgets["100"]["returned_BUY"], 1)
        self.assertEqual(budgets["250"].get("all_model_returns", 0), 0)
        self.assertFalse(result["claim_of_original_policy_source_rebinding"])
        self.assertFalse(result["model_based_after_cost_performance_attested"])
        self.assertFalse(result["original_full_action_propensities_known"])
        self.assertEqual(result["broker_orders_sent"], 0)

    def test_quote_movement_research_not_signals_or_order(self):
        r = analyze_join(self.archive, [self.observation(1200, "BUY")], [], Counter())
        self.assertEqual(r["absolute_requote_drift_microbps"]["count"], 1)
        self.assertIsInstance(r["absolute_requote_drift_microbps"]["p50"], int)
        self.assertFalse(r["promotion_allowed"])

    def test_invalid_future_model_input_clock_rejected(self):
        x = self.observation(1200, "BUY")
        x["original_input_quote_at_ms"] = 1300
        with self.assertRaisesRegex(ContractError, "REQUOTING_ORIGINAL_MODEL_SOURCE_CLOCK"):
            analyze_join(self.archive, [x], [], Counter())

    def test_invalid_expiry_slack_cannot_qualify(self):
        poisoned = dict(self.archive)
        poisoned["rows"] = [(1000,900,6700,"a"*64,Decimal("1.1")),
                            self.quotes[1]]
        with self.assertRaisesRegex(ContractError, "REQUOTING_BROKER_EXPIRY_SLACK_INCONSISTENT"):
            analyze_join(poisoned, [self.observation(1200,"SELL")], [], Counter())

    def test_percentile_empty_stays_unknown(self):
        empty = describe_int([])
        self.assertIsNone(empty["p50"])
        self.assertEqual(empty["count"], 0)
        self.assertEqual(describe_int([1, 2, 5])["p99"], 5)

    def test_observed_hold_never_converted_to_directional(self):
        result = analyze_join(self.archive, [self.observation(1200,"HOLD")], [], Counter())
        self.assertEqual(result["observations"]["fresh_returned_HOLD"], 1)
        self.assertEqual(result["observations"].get("fresh_returned_BUY",0), 0)
        self.assertFalse(result["claim_of_original_policy_source_rebinding"])


class SyntheticOriginalQuoteArchive(OriginalSourceFixture):
    def setUp(self):
        super().setUp()
        self.archive = self.root / "sealed-source-archive"
        self.archive.mkdir(mode=0o700)

    def make_segment(self, suffix="abcdef12", *, with_end=True, increment_ns=0):
        rows = [source_strict(line) for line in self.path.read_bytes().splitlines()]
        if with_end:
            last = rows[-1]
            rec = {
                "schema": last["schema"],
                "sequence": len(rows),
                "previous": last["sha256"],
                "at_ms": last["at_ms"] + 5,
                "kind": "END",
                "data": {"broker_orders_sent": 0},
            }
            rec["sha256"] = source_digest(rec)
            rows.append(rec)
        directory = self.archive / ("segment-"+str(self.first*1000000+increment_ns)+"-"+suffix)
        directory.mkdir(mode=0o700)
        content = directory / "quotes.jsonl"
        content.write_bytes(b"\n".join(source_canonical(r) for r in rows) + b"\n")
        content.chmod(0o600)
        return content

    def test_sealed_local_paper_source_is_accepted_readonly(self):
        self.make_segment()
        result = load_original_quote_archive(self.archive, self.spec["period_sha256"])
        self.assertEqual(result["sealed_segments"], 1)
        self.assertEqual(result["numeric_segments_total"], 1)
        self.assertEqual(len(result["rows"]), 7)
        self.assertFalse(result["unsealed_newest_excluded"])
        self.assertEqual(result["first_available_ms"], result["rows"][0][0])
        self.assertFalse(any(result["rows"][i][0] > result["rows"][i+1][0]
                             for i in range(len(result["rows"])-1)))

    def test_duplicate_segment_start_is_rejected_even_different_name(self):
        self.make_segment()
        self.make_segment("bbbbbbbb", with_end=False)
        with self.assertRaisesRegex(ContractError, "REQUOTING_DUPLICATE_SEGMENT_START"):
            load_original_quote_archive(self.archive, self.spec["period_sha256"])

    def test_only_latest_unsealed_segment_is_excluded(self):
        self.make_segment()
        self.make_segment("bbbbbbbb", with_end=False, increment_ns=1000000000)
        result = load_original_quote_archive(self.archive, self.spec["period_sha256"])
        self.assertEqual(result["sealed_segments"], 1)
        self.assertEqual(result["numeric_segments_total"], 2)
        self.assertTrue(result["unsealed_newest_excluded"])
        self.assertEqual(len(result["rows"]), 7)

    def test_earlier_unsealed_segment_rejected(self):
        self.make_segment(with_end=False)
        self.make_segment("bbbbbbbb", with_end=True, increment_ns=1000000000)
        with self.assertRaisesRegex(ContractError, "REQUOTING_UNSEALED_EARLIER_SEGMENT"):
            load_original_quote_archive(self.archive, self.spec["period_sha256"])

    def test_symlinked_segment_refused(self):
        self.make_segment()
        (self.archive/("segment-"+str(self.first*1000000)+"-bbbbbbbb")).symlink_to(
            self.archive/("segment-"+str(self.first*1000000)+"-abcdef12"),
            target_is_directory=True)
        with self.assertRaises(ContractError):
            load_original_quote_archive(self.archive, self.spec["period_sha256"])

    def test_wrong_paper_accounting_period_rejected(self):
        self.make_segment()
        with self.assertRaisesRegex(ContractError, "REQUOTING_SOURCE_PERIOD_OR_INSTRUMENT"):
            load_original_quote_archive(self.archive, "f"*64)

    def test_malformed_segment_name_is_not_ignored(self):
        self.make_segment()
        bad = self.archive/"segment-not-a-number"
        bad.mkdir(mode=0o700)
        with self.assertRaisesRegex(ContractError, "REQUOTING_MALFORMED_NUMERIC_SOURCE"):
            load_original_quote_archive(self.archive, self.spec["period_sha256"])

    def test_no_sealed_segment_is_not_a_complete_archive(self):
        self.make_segment(with_end=False)
        with self.assertRaisesRegex(ContractError, "REQUOTING_NO_SEALED_QUOTE_SOURCE"):
            load_original_quote_archive(self.archive, self.spec["period_sha256"])

    def test_untrusted_public_original_root_is_refused(self):
        self.archive.chmod(0o755)
        with self.assertRaisesRegex(ContractError, "REQUOTING_ORIGINAL_ROOT_NOT_PRIVATE"):
            load_original_quote_archive(self.archive, self.spec["period_sha256"])

    def test_changed_original_paper_source_chain_rejected(self):
        file = self.make_segment()
        raw = file.read_bytes()
        modified = raw.replace(b"1.10002", b"1.19902",1)
        self.assertNotEqual(raw,modified)
        file.write_bytes(modified)
        with self.assertRaises(ValueError):
            load_original_quote_archive(self.archive, self.spec["period_sha256"])


class SyntheticOriginalModelGraph(unittest.TestCase):
    """Mocks cannot replace real original graph verification in smoke."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "releases"
        self.root.mkdir(mode=0o700)
        self.period = "a"*64

    def manifest(self, release, *, instrument="EUR.USD", period=None):
        from historical_training.common import sha
        path = self.root / release / instrument / "manifest.json"
        path.parent.mkdir(parents=True, mode=0o700)
        doc = {
            "instrument": instrument,
            "period_sha256": self.period if period is None else period,
            "journal_directory": str(self.root / ("journal-"+release)),
        }
        path.write_bytes(json.dumps(doc,sort_keys=True).encode())
        path.chmod(0o600)
        return path, sha(path)

    @staticmethod
    def rows(request_id, *, missing=None, future=False):
        source_at = 1200 if future else 900
        stages = [
            {"kind":"REQUEST","sequence":1,
             "data":{"request_id":request_id,"started_at_ms":1000,
                     "history_snapshot":[{"observed_at_ms":source_at,
                                          "bid":"1.10000","ask":"1.10002"}]}},
            {"kind":"RESPONSE","sequence":2,
             "data":{"request_id":request_id,"available_at_ms":1100}},
            {"kind":"DECISION","sequence":3,
             "data":{"request_id":request_id,"available_at_ms":1200}},
        ]
        return [row for row in stages if row["kind"]!=missing]

    def sources(self, mapping, *, action="HOLD"):
        from historical_training.common import sha
        def get(directory):
            rel=Path(directory).name.removeprefix("journal-")
            path=self.root/rel/"EUR.USD"/"manifest.json"
            return {"producers":[{"manifest_sha256":sha(path),
                                  "rows":mapping[rel]}]}
        return (
            patch("train_v2.postmodel_requote.verify_release",
                  side_effect=lambda path: {"manifest_sha256":sha(path)}),
            patch("train_v2.postmodel_requote._scan_original",
                  side_effect=get),
            patch("train_v2.postmodel_requote._graph_decision",
                  return_value=(action,{"test_only":True})),
        )

    def run_with(self, mapping, *, action="HOLD"):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for cm in self.sources(mapping,action=action):
                stack.enter_context(cm)
            return source_model_returns(self.root,self.period)

    def test_complete_eur_original_graph_source_time_is_preserved(self):
        self.manifest("one")
        observed,per_release,unknown=self.run_with({"one":self.rows("req-1")})
        self.assertEqual(len(observed),1)
        self.assertEqual(observed[0]["original_input_quote_at_ms"],900)
        self.assertEqual(observed[0]["original_model_decision_at_ms"],1200)
        self.assertEqual(observed[0]["original_action"],"HOLD")
        self.assertEqual(per_release[0]["observed_model_returns"],1)
        self.assertEqual(dict(unknown),{})

    def test_missing_original_response_does_not_become_a_model_return(self):
        self.manifest("one")
        with self.assertRaisesRegex(ContractError,"REQUOTING_NO_VERIFIED_ORIGINAL_MODEL_GRAPH"):
            self.run_with({"one":self.rows("req-1",missing="RESPONSE")})

    def test_original_llm_graph_future_quote_is_not_admitted(self):
        self.manifest("one")
        with self.assertRaisesRegex(ContractError,"REQUOTING_NO_VERIFIED_ORIGINAL_MODEL_GRAPH"):
            self.run_with({"one":self.rows("req-1",future=True)})

    def test_reused_request_identity_across_installed_releases_denied(self):
        self.manifest("one")
        self.manifest("two")
        with self.assertRaisesRegex(ContractError,"REQUOTING_CROSS_RELEASE_MODEL_REQUEST_REUSE"):
            self.run_with({"one":self.rows("request-same"),
                           "two":self.rows("request-same")})

    def test_original_non_trade_grammar_not_silently_upgraded(self):
        self.manifest("one")
        with self.assertRaisesRegex(ContractError,"REQUOTING_NO_VERIFIED_ORIGINAL_MODEL_GRAPH"):
            self.run_with({"one":self.rows("req-1")},action="NOT_A_TRADE")

    def test_cross_accounting_period_original_release_denied(self):
        self.manifest("one",period="b"*64)
        with self.assertRaisesRegex(ContractError,"REQUOTING_CROSS_PERIOD_MODEL"):
            self.run_with({"one":self.rows("req-1")})

    def test_duplicate_original_model_stage_is_denied(self):
        self.manifest("one")
        items=self.rows("req-1")
        items.append(copy.deepcopy(items[-1]))
        with self.assertRaisesRegex(ContractError,"REQUOTING_DUPLICATE_ORIGINAL_MODEL_EVENT"):
            self.run_with({"one":items})
