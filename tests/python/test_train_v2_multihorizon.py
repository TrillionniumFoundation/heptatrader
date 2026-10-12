"""Price-only multi-horizon feasibility tests. No training/market actions."""
import hashlib
import json
from decimal import Decimal
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from train_v2.contracts import ContractError
from train_v2.original_multihorizon import (
    HORIZONS_MIN, GRID_MS, PINNED_COST_SHA256,
    PINNED_ORIGINAL_SEALED_SOURCE,
    MAX_GRID_WINDOWS, ordered_quote_index, input_asof, label_after,
    grid_schedule, assess_horizon, original_multi_horizon_summary,
    price_stats, percentile,
)


PERIOD = "a"*64


def mock_quotes(minutes=150, step=1000):
    origin = 1791360000000  # aligned 5-minute UTC grid
    rows = []
    for i in range(minutes*60000//step + 1):
        event = origin + i*step
        mid = Decimal("1.1000") + Decimal(i)*Decimal("0.0000007")
        rows.append((event+100, event, event+5000,
                     hashlib.sha256(str(event).encode()).hexdigest(), mid))
    return rows


def mock_archive(rows):
    return {
        "rows": rows,
        "sealed_segments": 1,
        "numeric_segments_total": 1,
        "unsealed_newest_excluded": False,
        "verified_original_bytes": 12345,
        "source_fingerprint_sha256": PINNED_ORIGINAL_SEALED_SOURCE,
    }


def mock_cost():
    return {
        "schema": "heptatrader.ib-cost-evidence.offline.v1",
        "instrument": "EUR.USD",
        "period_source_sha256": PERIOD,
        "exact_paper_account_scoped": True,
        "source": "ORIGINAL_CHAIN_VERIFIED_PAPER_LIVE_QUOTES_AND_OMS_COMMISSION_CALLBACKS",
        "source_prefix_digest": "c"*64,
        "source_prefix_count": 1,
        "quote_count": 200,
        "oms_sha256": "d"*64,
        "fx_fee_missing_or_unproved_executions": 0,
        "fx_fee_complete_unique_executions": 190,
        "actual_slippage_verified": False,
        "actual_fill_timestamp_attested": False,
        "historical_bar_bid_ask_used_as_executable_spread": False,
        "commission_callbacks_are_reported_not_broker_flex_statements": True,
        "quote_spread_full_bps": {"p50": 0.1780722,"p90": 0.2675144,
                                  "p99": 3.4837759},
        "fx_commission_one_way_bps": {"p50": 0.2000032,"p90": 0.7147899,
                                      "p99": 0.7157236},
        "trade_authority": False, "orders_sent": 0,
        "broker_requests_sent": 0,
    }


class HorizonCapacityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = mock_quotes()
        cls.index = ordered_quote_index(cls.rows)
        cls.grid = grid_schedule(cls.index)

    def test_source_is_stable_and_validated(self):
        self.assertEqual(len(self.index["known"]),len(self.rows))
        self.assertGreater(len(self.grid),20)
        self.assertEqual(len(self.index["events"]),len(self.rows))

    def test_fixed_grid_source_is_true_asof(self):
        anchor = self.grid[0]
        state, quote = input_asof(self.index,anchor)
        self.assertEqual(state,"INPUT_CAUSAL")
        self.assertLessEqual(quote[0],anchor+1000)
        self.assertLessEqual(quote[1],anchor+1000)
        self.assertGreaterEqual(quote[2],anchor+1000)

    def test_horizon_endpoint_is_exact_and_not_interpolated(self):
        anchor=self.grid[0]
        _, q=input_asof(self.index,anchor)
        status, value=label_after(self.index,q,anchor,300000)
        self.assertEqual(status,"OBSERVED_RETROSPECTIVE_PRICE_ONLY")
        self.assertGreater(value,0)

    def test_fixed_four_horizons_never_select_trades(self):
        r=original_multi_horizon_summary(
            mock_archive(self.rows),mock_cost(),PINNED_COST_SHA256,PERIOD)
        self.assertEqual(r["horizon_minutes_frozen_not_optimized"],list(HORIZONS_MIN))
        self.assertFalse(r["new_model_fit_allowed"])
        self.assertFalse(r["actual_execution_slippage_attested"])
        self.assertFalse(r["independent_forward_sample_attested"])
        for row in r["horizons"]:
            self.assertFalse(row["price_labels_used_to_select_windows"])
            self.assertFalse(row["independent_prospective_test"])
            self.assertFalse(row["price_only_model_training_permitted"])
            self.assertEqual(row["fixed_grid_anchor_count"],len(self.grid))
        self.assertEqual(r["fixed_cost_plus_margin_bps"],"10.9133557")

    def test_label_blind_cohort_does_not_change_when_future_price_changes(self):
        before=assess_horizon(self.index,self.grid,300000,Decimal("10"))
        # Distort price dynamics (not timestamps) to prove selection happens
        # before access to horizon outcomes. A constant scale would leave
        # percentage returns identical and would not test this boundary.
        rows=[(*row[:4],row[4] + Decimal(i)*Decimal("0.00001"))
              for i,row in enumerate(self.rows)]
        after=assess_horizon(ordered_quote_index(rows),self.grid,
                             300000,Decimal("10"))
        self.assertEqual(before["input_at_decision_eligible"],
                         after["input_at_decision_eligible"])
        self.assertEqual(before["prelabel_time_disjoint_selected"],
                         after["prelabel_time_disjoint_selected"])
        self.assertNotEqual(before["all_eligible_covered_price_stats"],
                            after["all_eligible_covered_price_stats"])

    def test_gap_censors_instead_of_making_zero_return(self):
        anchor=self.grid[0]
        gap_start=anchor+125000
        rows=[r for r in self.rows if not gap_start <= r[1] <= gap_start+39000]
        idx=ordered_quote_index(rows)
        _, quote=input_asof(idx,anchor)
        state,value=label_after(idx,quote,anchor,300000)
        self.assertEqual(state,"CENSORED_INTERVENING_SOURCE_GAP")
        self.assertIsNone(value)

    def test_endpoint_missing_censored_without_next_bar_fill(self):
        anchor=self.grid[0]
        due=anchor+300000
        rows=[r for r in self.rows if not due <= r[1] <= due+6000]
        idx=ordered_quote_index(rows)
        _,quote=input_asof(idx,anchor)
        state,value=label_after(idx,quote,anchor,300000)
        self.assertEqual(state,"CENSORED_ENDPOINT_NOT_OBSERVED")
        self.assertIsNone(value)

    def test_end_of_sealed_source_is_censored(self):
        anchor=self.grid[-1]
        _,q=input_asof(self.index,anchor)
        self.assertEqual(label_after(self.index,q,anchor,300000),
                         ("CENSORED_BEYOND_SEALED_SOURCE",None))

    def test_input_expiry_is_not_repaired_with_earlier_quote(self):
        original=list(self.rows)
        latest=original[300]
        original[300]=(latest[0],latest[1],latest[0],latest[3],latest[4])
        idx=ordered_quote_index(original)
        anchor=original[300][1]  # decision anchor+1s, latest expired
        state,quote=input_asof(idx,anchor)
        self.assertEqual(state,"INPUT_QUOTE_EXPIRED")
        self.assertIsNone(quote)

    def test_source_disallows_future_or_revised_quote_availability(self):
        malformed=list(self.rows)
        original=malformed[5]
        malformed[5]=(original[0]+6000,*original[1:])
        with self.assertRaisesRegex(ContractError,"MULTIHORIZON_SOURCE_TIME_OR_TTL"):
            ordered_quote_index(malformed)

    def test_identity_conflict_and_event_price_conflict_fail(self):
        dup=list(self.rows)
        e=dup[10]
        dup.insert(11,(e[0]+1,e[1],e[2],hashlib.sha256(b"new").hexdigest(),
                       e[4]+Decimal("0.1")))
        with self.assertRaisesRegex(ContractError,"MULTIHORIZON_DUPLICATE_EVENT_PRICE"):
            ordered_quote_index(dup)
        dupe=list(self.rows);dupe.insert(12,dupe[10])
        with self.assertRaisesRegex(ContractError,"MULTIHORIZON_SOURCE_IDENTITY"):
            ordered_quote_index(dupe)

    def test_frozen_sealed_quote_cohort_does_not_expand_after_inspection(self):
        changed=mock_archive(self.rows)
        changed["source_fingerprint_sha256"]="e"*64
        with self.assertRaisesRegex(
                ContractError,"MULTIHORIZON_FROZEN_SEALED_SOURCE_CHANGED"):
            original_multi_horizon_summary(
                changed,mock_cost(),PINNED_COST_SHA256,PERIOD)

    def test_growing_excluded_unsealed_bytes_do_not_change_price_economics(self):
        original=mock_archive(self.rows)
        enlarged=mock_archive(self.rows)
        enlarged["verified_original_bytes"]=original["verified_original_bytes"]+123456
        first=original_multi_horizon_summary(
            original,mock_cost(),PINNED_COST_SHA256,PERIOD)
        second=original_multi_horizon_summary(
            enlarged,mock_cost(),PINNED_COST_SHA256,PERIOD)
        self.assertEqual(first["horizons"],second["horizons"])
        self.assertTrue(second["pin_applies_only_to_sealed_source_fingerprint"])

    def test_true_historical_cost_input_must_be_unchanged(self):
        cost=mock_cost();cost["fx_fee_missing_or_unproved_executions"]=1
        with self.assertRaises(ContractError):
            original_multi_horizon_summary(mock_archive(self.rows),
                                            cost,PINNED_COST_SHA256,PERIOD)
        with self.assertRaisesRegex(ContractError,"MULTIHORIZON_FROZEN_ORIGINAL_COST_CHANGED"):
            original_multi_horizon_summary(mock_archive(self.rows),
                                            mock_cost(),"f"*64,PERIOD)

    def test_cost_shopping_by_lower_spread_is_rejected(self):
        cost=mock_cost()
        cost["quote_spread_full_bps"]["p99"]=1.
        with self.assertRaisesRegex(ContractError,"MULTIHORIZON_FROZEN_COST_HURDLE_CHANGED"):
            original_multi_horizon_summary(mock_archive(self.rows),
                                            cost,PINNED_COST_SHA256,PERIOD)

    def test_revised_historical_fake_authority_not_a_quote(self):
        x=list(self.rows);q=list(x[2]);q[4]=Decimal("-1");x[2]=tuple(q)
        with self.assertRaisesRegex(ContractError,"MULTIHORIZON_PRICE_NOT_FINITE_OR_POSITIVE"):
            ordered_quote_index(x)

    def test_nearest_rank_and_hindsight_are_not_profit(self):
        values=[Decimal(x) for x in ("1","2","3","4","5")]
        self.assertEqual(percentile(values,95),"5")
        p=price_stats(values,Decimal("4"))
        self.assertEqual(p["hindsight_move_clears_cost_plus_margin_count"],1)
        self.assertFalse(p["hindsight_oracle_is_trade_policy"])

    def test_invalid_horizon_refused_not_retuned_post_labels(self):
        with self.assertRaisesRegex(ContractError,"MULTIHORIZON_UNREGISTERED_HORIZON"):
            assess_horizon(self.index,self.grid,700000,Decimal("10"))

    def test_grid_above_bounded_window_rejected(self):
        rows=list(self.rows)
        extra=(rows[-1][0]+MAX_GRID_WINDOWS*GRID_MS,
               rows[-1][1]+MAX_GRID_WINDOWS*GRID_MS,
               rows[-1][2]+MAX_GRID_WINDOWS*GRID_MS,
               hashlib.sha256(b"long").hexdigest(),rows[-1][4])
        idx=ordered_quote_index(rows+[extra])
        with self.assertRaisesRegex(ContractError,"MULTIHORIZON_GRID_POPULATION_BOUND"):
            grid_schedule(idx)

    def test_private_output_is_create_once_even_for_synthetic_fixture(self):
        from train_v2.original_multihorizon import run
        from train_v2.contracts import canonical
        from train_v2 import original_multihorizon as module
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            root.chmod(0o700)
            output=root/"no-overwrite.json"
            blob=canonical(mock_cost())+b"\n"
            checksum=hashlib.sha256(blob).hexdigest()
            with (patch.object(module,"read_private",return_value=blob),
                  patch.object(module,"load_original_quote_archive",
                               return_value=mock_archive(self.rows)),
                  patch.object(module,"PINNED_COST_SHA256",checksum),
                  patch.object(module,"sha",return_value=PERIOD)):
                first=run(source_root=root,period_file=root/"period.json",
                          cost_file=root/"cost.json",output_file=output)
                self.assertFalse(first["new_model_fit_allowed"])
                original=output.read_bytes()
                self.assertEqual(original[-1:],b"\n")
                self.assertEqual(json.loads(original)["horizons"][0]["horizon_minutes"],5)
                with self.assertRaisesRegex(ContractError,
                                          "MULTIHORIZON_OUTPUT_CREATE_ONLY"):
                    run(source_root=root,period_file=root/"period.json",
                        cost_file=root/"cost.json",output_file=output)
                self.assertEqual(output.read_bytes(),original)

    def test_public_output_parent_is_refused(self):
        from train_v2.original_multihorizon import run
        from train_v2.contracts import canonical
        from train_v2 import original_multihorizon as module
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            root.chmod(0o755)
            output=root/"should-not-exist.json"
            blob=canonical(mock_cost())+b"\n"
            checksum=hashlib.sha256(blob).hexdigest()
            with (patch.object(module,"read_private",return_value=blob),
                  patch.object(module,"load_original_quote_archive",
                               return_value=mock_archive(self.rows)),
                  patch.object(module,"PINNED_COST_SHA256",checksum),
                  patch.object(module,"sha",return_value=PERIOD)):
                with self.assertRaisesRegex(ContractError,
                                           "MULTIHORIZON_PRIVATE_OUTPUT_REQUIRED"):
                    run(source_root=root,period_file=root/"period.json",
                        cost_file=root/"cost.json",output_file=output)
            self.assertFalse(output.exists())

    def test_mature_status_reconciles_selected_denominator(self):
        r=assess_horizon(self.index,self.grid,3600000,Decimal("10"))
        self.assertEqual(sum(r["time_disjoint_outcome_status"].values()),
                         r["prelabel_time_disjoint_selected"])
        self.assertEqual(sum(r["all_eligible_outcome_status"].values()),
                         r["input_at_decision_eligible"])
        self.assertFalse(r["outcome_censoring_imputed_as_zero"])
