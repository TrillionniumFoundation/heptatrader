from contextlib import redirect_stderr
from dataclasses import replace
from decimal import Decimal as D, localcontext
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hepta_research.model import ReplayBar
from hepta_research.pipeline import HEADER, MovingAverageTarget, read_bars, run_report, write_report, main


def row(begin, end, opening, close, complete=1, instrument="TEST", day="20260921"):
    return f"{instrument},{day},{begin},{end},{opening},{max(opening,close)},{min(opening,close)},{close},10,2,{complete}\n"


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.input = self.root / "bars.csv"
        self.output = self.root / "report.json"
        self.rows = [row(0,10,10,11), row(10,20,12,13), row(20,30,14,15), row(30,40,16,17,0)]
        self.data()

    def data(self, rows=None):
        self.input.write_text(HEADER+"\n"+"".join(self.rows if rows is None else rows))

    def report(self, **changes):
        args = dict(tick_size=1, capital=100, quantity=2, fast=1, slow=2)
        args.update(changes)
        return run_report(self.input, **args)

    def cli(self, output=None):
        with redirect_stderr(io.StringIO()):
            return main(["--bars", str(self.input), "--output", str(output or self.output),
                         "--tick-size", "1", "--capital", "100", "--quantity", "2", "--fast", "1", "--slow", "2"])

    def test_real_model_next_bar_fill_and_costs(self):
        result = self.report(slippage=1, fee_per_unit="0.5")
        self.assertEqual(result["mode"], "OFFLINE_HYPOTHETICAL")
        self.assertEqual(result["fills"], [dict(timestamp_us=20, delta=D(2), price=D(15), fee=D(1))])
        self.assertEqual([r["value"] for r in result["equity"]], [100,100,99,103])
        self.assertEqual(result["position"], 2)
        self.assertIsNone(result["pending_target"])
        self.assertFalse(result["assumptions"]["broker_authorized"])

    def test_input_hash_exact_bytes_and_crlf(self):
        raw = (HEADER+"\r\n"+self.rows[0].replace("\n","\r\n")).encode()
        self.input.write_bytes(raw)
        bars, meta = read_bars(self.input, "0.01")
        self.assertEqual(meta["bars_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(bars[0].open, D("0.10"))

    def test_decimal_scale_without_binary_float(self):
        self.data([row(0,10,1000000000000000000,1000000000000000001)])
        bars, _ = read_bars(self.input, "0.000000000000000001")
        self.assertEqual(bars[0].close, D("1.000000000000000001"))

    def test_complete_final_target_remains_unfilled(self):
        self.data(self.rows[:2])
        result = self.report()
        self.assertEqual(result["pending_target"], 2)
        self.assertEqual(result["fills"], [])
        self.assertEqual(result["position"], 0)

    def test_incomplete_excluded_from_annualization(self):
        result = self.report(periods_per_year=252)
        self.assertEqual(result["metrics"]["annualized"]["periods"], 2)
        self.assertEqual(result["metrics"]["annualization_reason"], "explicit_complete_bar_frequency")
        self.assertIsNone(self.report()["metrics"]["annualized"])

    def test_irregular_annualization_rejected(self):
        self.data([row(0,10,10,11),row(10,20,12,13),row(30,40,14,15)])
        with self.assertRaises(ValueError): self.report(periods_per_year=252)
        self.assertIsNone(self.report()["metrics"]["annualized"])

    def test_insufficient_complete_observations(self):
        self.data([row(0,10,10,11),row(10,20,12,13,0)])
        result = self.report(periods_per_year=252)
        self.assertEqual(result["metrics"]["annualization_reason"], "insufficient_complete_observations")

    def test_nonpositive_equity_not_refunded(self):
        self.data([row(0,10,1,1),row(10,20,2,2),row(20,30,10,0)])
        result = self.report(quantity=100, periods_per_year=252)
        self.assertEqual(result["equity"][-1]["value"], -900)
        self.assertEqual(result["metrics"]["annualization_reason"], "nonpositive_equity")
        self.assertFalse(result["assumptions"]["automatic_funding"])

    def test_invalid_rows_reject_entire_input(self):
        bad = [row(0,10,10,11).replace(",11,10,11,",",9,10,11,"),
               row(0,10,10,11,2), row(0,10,10,11,day="20260230"),
               row(0,10,10,11).replace(",10,2,1",",-1,2,1"),
               row(0,10,10,11).replace(",10,2,1",",10,0,1"),
               row(0,2**63,10,11), row(0,10,2**63,2**63),
               row(0,10,10,11)+"extra\n", "X"*4097+"\n", "\n"]
        for value in bad:
            with self.subTest(value=value[:80]):
                self.data([value])
                with self.assertRaises(ValueError): read_bars(self.input, 1)

    def test_stream_identity_order_and_finality(self):
        for last in (row(9,20,1,2),row(10,20,1,2,instrument="OTHER"),row(10,20,1,2,day="20260920")):
            self.data([self.rows[0],last])
            with self.assertRaises(ValueError): read_bars(self.input, 1)
        self.data([row(0,10,1,2,0),row(10,20,1,2)])
        with self.assertRaises(ValueError): read_bars(self.input, 1)

    def test_explicit_row_and_scale_bounds(self):
        for bound in (0,True,1000001):
            with self.assertRaises(ValueError): read_bars(self.input,1,bound)
        with self.assertRaises(ValueError): read_bars(self.input,1,1)
        for scale in (0,-1,"nan","1e-19"):
            with self.assertRaises(ValueError): read_bars(self.input,scale)

    def test_output_decimals_and_no_partial_report(self):
        self.assertEqual(self.cli(),0)
        saved = self.output.read_bytes()
        self.assertIsInstance(json.loads(saved)["fills"][0]["price"],str)
        self.input.write_text(self.input.read_text()+"late,bad,row\n")
        self.assertEqual(self.cli(),2)
        self.assertEqual(self.output.read_bytes(),saved)

    def test_serialization_or_file_sync_failure_preserves_output(self):
        self.output.write_text("old")
        with self.assertRaises(ValueError): write_report(self.output,{"bad":float("nan")})
        with patch("hepta_research.pipeline.os.fsync",side_effect=OSError("disk")):
            with self.assertRaises(OSError): write_report(self.output,self.report())
        self.assertEqual(self.output.read_text(),"old")
        self.assertEqual(list(self.root.glob(".hepta-report-*")),[])

    def test_post_replace_sync_failure_is_not_false_rollback(self):
        self.output.write_text("old")
        real = os.fsync
        count = 0
        def fail_second(fd):
            nonlocal count
            count += 1
            if count == 2: raise OSError("directory sync")
            real(fd)
        with patch("hepta_research.pipeline.os.fsync",side_effect=fail_second):
            with self.assertRaises(OSError): write_report(self.output,self.report())
        self.assertEqual(json.loads(self.output.read_text())["schema"],"hepta.research.report.v1")

    def test_output_cannot_replace_input_or_alias(self):
        old = self.input.read_bytes()
        self.assertEqual(self.cli(self.input),2)
        self.output.symlink_to(self.input)
        self.assertEqual(self.cli(),2)
        self.output.unlink()
        os.link(self.input,self.output)
        self.assertEqual(self.cli(),2)
        self.assertEqual(self.input.read_bytes(),old)


class SignalTests(unittest.TestCase):
    def bar(self,i,price,complete=True):
        return ReplayBar(i*10,(i+1)*10,D(price),D(price),complete)

    def test_warmup_long_short_equal(self):
        signal = MovingAverageTarget(1,2,3)
        self.assertEqual([signal(self.bar(i,p)) for i,p in enumerate([10,12,8,8])], [0,3,-3,0])

    def test_long_only_and_rejected_bar_no_advance(self):
        signal = MovingAverageTarget(1,2,3,True)
        signal(self.bar(0,10))
        with self.assertRaises(ValueError): signal(self.bar(1,99,False))
        self.assertEqual(signal(self.bar(1,8)),0)
        with self.assertRaises(ValueError): signal(self.bar(1,9))
        self.assertEqual(signal(self.bar(2,9)),3)

    def test_exact_comparison_independent_of_decimal_context(self):
        signal = MovingAverageTarget(1,2,"0.000000000000000001")
        with localcontext() as ctx:
            ctx.prec=3
            signal(self.bar(0,"999999999999999999.999999999999999998"))
            self.assertEqual(signal(self.bar(1,"999999999999999999.999999999999999999")),D("1e-18"))

    def test_future_changes_do_not_change_past_signal(self):
        def signals(prices):
            strategy=MovingAverageTarget(1,2,1)
            return [strategy(self.bar(i,p)) for i,p in enumerate(prices)]
        self.assertEqual(signals([10,12,8,500])[:3],signals([10,12,8,-500])[:3])

    def test_parameter_bounds(self):
        for fast,slow,size in [(0,2,1),(2,2,1),(1,100001,1),(True,2,1),(1,2,0)]:
            with self.assertRaises(ValueError): MovingAverageTarget(fast,slow,size)
