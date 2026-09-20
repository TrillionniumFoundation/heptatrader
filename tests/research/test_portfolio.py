from __future__ import annotations

from dataclasses import asdict, replace
from decimal import Decimal as D, localcontext
import hashlib
import io
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from hepta_research.model import ReplayBar, replay
from hepta_research.pipeline import _encode, MovingAverageTarget
from hepta_research.portfolio import (InstrumentModel, MAX_TIME, _capture, main,
                                      portfolio_replay, run_portfolio_report)
from portfolio_fixture import fixture, check_report


def bar(begin, end, opening, close=None, complete=True):
    return ReplayBar(begin, end, D(str(opening)), D(str(opening if close is None else close)), complete)


class PortfolioTests(unittest.TestCase):
    def run_case(self, streams=None, models=None, targets=None, **kwargs):
        if streams is None:
            streams = {"A": [bar(0,10,10,11), bar(10,20,12,13), bar(20,30,14,15)]}
        if models is None:
            models = {name: InstrumentModel("USD", 2) for name in streams}
        if targets is None:
            targets = {name: lambda b: 2 for name in streams}
        options = dict(capital=1000, currency="USD", max_mark_age_us=1000)
        options.update(kwargs)
        return portfolio_replay(streams, models, targets, **options)

    def test_single_instrument_matches_existing_replay(self):
        streams = {"A": [bar(0,10,10,11), bar(10,20,12,13), bar(20,30,14,15)]}
        old = replay(streams["A"], lambda b: 2, capital=1000, max_abs_target=2,
                     multiplier=10, slippage=1, fee_per_unit="0.5")
        new = self.run_case(streams, {"A": InstrumentModel("USD",2,10,1,1,"0.5")})
        self.assertEqual(new["fills"], [{"instrument":"A", **asdict(f)} for f in old["fills"]])
        self.assertEqual(new["final_equity"], old["equity"][-1])
        self.assertEqual(new["positions"]["A"], old["position"])
        self.assertEqual(new["fees"], old["fees"])

    def test_shared_capital_is_counted_once(self):
        r = self.run_case({"A":[bar(0,10,1)], "B":[bar(0,10,2)]})
        self.assertEqual(r["cash"], 1000)
        self.assertEqual(r["final_equity"], 1000)
        self.assertEqual(r["fills"], [])

    def test_no_cross_symbol_future_close_lookahead(self):
        r = self.run_case({"A":[bar(0,1,10),bar(1,100,10,100)], "B":[bar(5,6,20)]})
        at5 = next(item for item in r["equity"] if item["timestamp_us"] == 5)
        self.assertEqual(at5["equity"], 1000)
        self.assertEqual(r["final_equity"], 1180)

    def test_target_waits_for_same_instrument_open(self):
        r = self.run_case({"A":[bar(0,1,10),bar(100,110,20)], "B":[bar(2,3,30)]})
        self.assertEqual([(f["instrument"], f["timestamp_us"]) for f in r["fills"]], [("A",100)])

    def test_simultaneous_closes_precede_opens(self):
        r = self.run_case({"B":[bar(0,10,1),bar(10,20,2)],"A":[bar(0,10,3),bar(10,20,4)]})
        at10 = next(item for item in r["equity"] if item["timestamp_us"] == 10)
        self.assertEqual([(e["phase"],e["instrument"]) for e in at10["events"]],
                         [("close","A"),("close","B"),("open","A"),("open","B")])

    def test_input_mapping_order_does_not_change_result(self):
        a, b = [bar(0,10,1),bar(10,20,2)], [bar(5,15,3),bar(15,25,4)]
        self.assertEqual(self.run_case({"A":a,"B":b}), self.run_case({"B":b,"A":a}))

    def test_stale_held_asset_does_not_invent_equity(self):
        r = self.run_case({"A":[bar(0,10,1),bar(10,20,2)],"B":[bar(100,110,3)]}, max_mark_age_us=5)
        self.assertIsNone(r["final_equity"])
        self.assertEqual(r["equity"][-1]["stale_instruments"], ["A"])
        self.assertIsNone(r["metrics"]["total_return"])
        self.assertIsNone(r["metrics"]["max_drawdown"])
        self.assertGreater(r["metrics"]["valuation_gap_count"], 0)

    def test_flat_stale_asset_does_not_require_valuation(self):
        r = self.run_case({"A":[bar(0,10,1)],"B":[bar(100,110,3)]}, max_mark_age_us=0)
        self.assertEqual(r["final_equity"],1000)

    def test_mark_age_boundary_is_inclusive(self):
        r = self.run_case({"A":[bar(0,10,1),bar(10,20,2)],"B":[bar(24,25,3)]}, max_mark_age_us=5)
        self.assertTrue(r["equity"][-1]["valuation_complete"])

    def test_fresh_price_restores_valuation_after_gap(self):
        r = self.run_case({"A":[bar(0,10,1),bar(10,20,2),bar(100,110,3)],
                           "B":[bar(50,60,4)]}, max_mark_age_us=5)
        self.assertEqual(r["final_equity"],1002)
        self.assertIsNone(r["metrics"]["max_drawdown"])

    def test_incomplete_close_is_untimed_not_future_mark(self):
        r = self.run_case({"A":[bar(0,10,1),bar(10,100,2,999,False)], "B":[bar(20,30,3)]})
        self.assertEqual(r["final_equity"],1000)
        self.assertEqual(r["equity"][-1]["timestamp_us"],30)
        self.assertEqual(r["untimed_incomplete_closes"],{"A":999})
        self.assertEqual(r["marks"]["A"]["timestamp_us"],10)

    def test_incomplete_bar_can_consume_previous_target(self):
        r = self.run_case({"A":[bar(0,10,1),bar(10,100,2,999,False)]})
        self.assertEqual(len(r["fills"]),1)
        self.assertIsNone(r["pending_targets"]["A"])
        self.assertEqual(r["positions"]["A"],2)

    def test_incomplete_only_bar_never_signals(self):
        def fail(_):
            self.fail("incomplete bar invoked target")
        r = self.run_case({"A":[bar(0,10,1,2,False)]}, targets={"A":fail})
        self.assertEqual(r["fills"],[])
        self.assertEqual(r["equity"][-1]["timestamp_us"],0)

    def test_final_target_is_left_pending_not_filled(self):
        r = self.run_case({"A":[bar(0,10,1)]})
        self.assertEqual(r["pending_targets"],{"A":2})
        self.assertEqual(r["positions"],{"A":0})

    def test_short_reversal_multiplier_fee_and_negative_price(self):
        bars = [bar(0,10,-10),bar(10,20,-8),bar(20,30,-4)]
        target = lambda b: -2 if b.end_us == 10 else 2
        old = replay(bars,target,capital=1000,max_abs_target=2,multiplier=3,slippage="0.5",fee_per_unit="0.25")
        new = self.run_case({"A":bars},{"A":InstrumentModel("USD",2,3,1,"0.5","0.25")},{"A":target})
        self.assertEqual(new["final_equity"],old["equity"][-1])
        self.assertEqual(new["fees"],D("1.5"))
        self.assertEqual([f["delta"] for f in new["fills"]],[-2,4])

    def test_decimal_context_independence(self):
        expected = self.run_case()
        with localcontext() as ctx:
            ctx.prec = 4
            self.assertEqual(self.run_case(), expected)

    def test_fractional_lot_target_is_exact(self):
        r = self.run_case(models={"A":InstrumentModel("USD","0.001",lot="0.001")},
                          targets={"A":lambda b:D("0.001")})
        self.assertEqual(r["positions"]["A"], D("0.001"))

    def test_no_annualization_on_irregular_event_clock(self):
        r = self.run_case()
        self.assertIsNone(r["metrics"]["annualized"])
        self.assertEqual(r["metrics"]["annualization_reason"],"irregular_event_clock")

    def test_insolvency_is_not_covered_by_automatic_funding(self):
        r = self.run_case({"A":[bar(0,10,1),bar(10,20,1,-1000)]})
        self.assertLess(r["final_equity"],0)
        self.assertGreater(r["metrics"]["max_drawdown"],1)
        self.assertFalse(r["assumptions"]["automatic_funding"])

    def test_all_streams_validated_before_callbacks(self):
        seen = []
        with self.assertRaises(ValueError):
            self.run_case({"A":[bar(0,10,1)],"B":[bar(0,10,1),bar(9,20,2)]},
                          targets={"A":lambda b:seen.append(b) or 0,"B":lambda b:0})
        self.assertEqual(seen,[])

    def test_bar_shapes_and_order_reject(self):
        for values in ([], [bar(-1,10,1)], [bar(0,0,1)], [bar(0,MAX_TIME+1,1)],
                       [bar(0,10,1,complete=1)], [bar(0,10,1),bar(9,20,2)],
                       [bar(0,10,1,complete=False),bar(10,20,2)], [bar(0,10,"NaN")],
                       [replace(bar(0,10,1),begin_us=True)], [object()]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.run_case({"A":values})

    def test_symbol_sets_types_and_bounds_reject(self):
        for streams, models, targets in (({}, {}, {}), ({"A": [bar(0,1,1)]}, {}, {}),
                ({"bad/name":[bar(0,1,1)]},{"bad/name":InstrumentModel("USD",2)},{"bad/name":lambda b:0}),
                ({"A":[bar(0,1,1)]},{"A":None},{"A":lambda b:0}),
                ({"A":[bar(0,1,1)]},{"A":InstrumentModel("USD",2)},{"A":None})):
            with self.subTest(streams=streams), self.assertRaises(ValueError):
                self.run_case(streams, models, targets)
        with self.assertRaises(ValueError):
            self.run_case({str(i):[bar(0,1,1)] for i in range(65)})

    def test_total_bar_budget_applies_across_instruments(self):
        with self.assertRaises(ValueError):
            self.run_case({"A":[bar(0,1,1)],"B":[bar(0,1,2)]}, max_total_bars=1)

    def test_explicit_time_capital_and_currency_bounds(self):
        for kw in ({"max_mark_age_us":True},{"max_mark_age_us":-1},{"max_mark_age_us":MAX_TIME+1},
                   {"capital":0},{"capital":"NaN"},{"currency":"usd"},{"max_total_bars":True}):
            with self.subTest(kw=kw), self.assertRaises(ValueError): self.run_case(**kw)
        with self.assertRaises(ValueError):self.run_case(models={"A":InstrumentModel("EUR",2)})

    def test_model_assumptions_reject(self):
        for kw in ({"max_abs_target":0},{"lot":0},{"multiplier":-1},{"slippage":-1},
                   {"fee_per_unit":"NaN"},{"fee_per_unit":-1},{"long_only":1},{"lot":"0.3"}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.run_case(models={"A":replace(InstrumentModel("USD",2),**kw)})

    def test_target_bounds_and_nonfinite_reject(self):
        for quantity in (3,"0.5","NaN",True,"1e100"):
            with self.subTest(quantity=quantity),self.assertRaises(ValueError):
                self.run_case(targets={"A":lambda b,q=quantity:q})
        with self.assertRaises(ValueError):
            self.run_case(models={"A":InstrumentModel("USD",2,long_only=True)},targets={"A":lambda b:-1})

    def test_ledger_overflow_propagates_without_report(self):
        with self.assertRaises(ValueError):
            self.run_case({"A":[bar(0,1,1),bar(1,2,"1e18")]},models={"A":InstrumentModel("USD",2,"1e18")})

    def test_fixed_seed_differential_against_existing_ledger_and_replay(self):
        rng = random.Random(17899)
        for scenario in range(40):
            streams, models, targets, oracles = {}, {}, {}, {}
            for index, name in enumerate(("A","B","C")):
                prices = [rng.randint(-30,50) for _ in range(12)]
                streams[name] = [bar(index+i*10,index+(i+1)*10,p) for i,p in enumerate(prices)]
                models[name] = InstrumentModel("USD",2,index+1,1,"0.25","0.125")
                targets[name] = MovingAverageTarget(1,3,2)
                oracles[name] = replay(streams[name],MovingAverageTarget(1,3,2),capital=1000,max_abs_target=2,
                                       multiplier=index+1,slippage="0.25",fee_per_unit="0.125")
            actual = self.run_case(streams,models,targets)
            with self.subTest(scenario=scenario):
                self.assertEqual(actual["final_equity"],1000+sum(o["equity"][-1]-1000 for o in oracles.values()))
                self.assertEqual(actual["fees"],sum(o["fees"] for o in oracles.values()))
                for name in streams:
                    self.assertEqual([f for f in actual["fills"] if f["instrument"]==name],
                                     [{"instrument":name,**asdict(f)} for f in oracles[name]["fills"]])


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest, self.sources, self.document = fixture(self.root)
        self.output = self.root/"report.json"

    def save(self):
        self.manifest.write_text(json.dumps(self.document),encoding="utf-8")

    def run_report(self, **kwargs):
        return run_portfolio_report(self.manifest,self.sources,**kwargs)

    def argv(self):
        args = ["--manifest",str(self.manifest),"--output",str(self.output)]
        for name,path in self.sources.items():args.extend(["--source",name+"="+str(path)])
        return args

    def test_end_to_end_exact_cash_fees_positions(self):
        r = json.loads(json.dumps(self.run_report(),default=_encode))
        check_report(r)
        self.assertEqual(r["input"]["manifest_sha256"],hashlib.sha256(self.manifest.read_bytes()).hexdigest())

    def test_all_file_splits_preserve_full_continuous_result(self):
        expected = self.run_report()
        data = self.sources["a"].read_bytes().splitlines(keepends=True)
        for split in range(1,6):
            paths = {"b":self.sources["b"]}
            entries = []
            for ref, rows in (("a1",data[1:split+1]),("a2",data[split+1:])):
                payload = data[0]+b"".join(rows)
                paths[ref] = self.root/(ref+".csv")
                paths[ref].write_bytes(payload)
                entries.append({"ref":ref,"sha256":hashlib.sha256(payload).hexdigest()})
            self.document["instruments"][0]["sources"] = entries
            self.save()
            actual = run_portfolio_report(self.manifest,paths)
            for key in ("fills","equity","positions","fees","final_equity","pending_targets","metrics"):
                with self.subTest(split=split,key=key):self.assertEqual(actual[key],expected[key])

    def test_instrument_declaration_order_invariance(self):
        before = self.run_report()
        self.document["instruments"].reverse();self.save()
        after = self.run_report()
        for key in before:
            if key!="input":self.assertEqual(before[key],after[key])

    def test_source_digest_mismatch_rejects(self):
        self.sources["a"].write_bytes(self.sources["a"].read_bytes()+b"\n")
        with self.assertRaisesRegex(ValueError,"SHA-256"):self.run_report()

    def test_duplicate_json_keys_reject(self):
        self.manifest.write_text('{"schema":"a","schema":"b"}')
        with self.assertRaisesRegex(ValueError,"duplicate"):self.run_report()

    def test_invalid_json_encoding_and_constants_reject(self):
        for data in (b"\xff",b"{",b'{"a":NaN}',b'{"a":Infinity}',b"["*2000+b"]"*2000):
            self.manifest.write_bytes(data)
            with self.subTest(data=data[:20]),self.assertRaises(ValueError):self.run_report()

    def test_unknown_manifest_fields_reject(self):
        self.document["broker"] = "not-a-runtime-config";self.save()
        with self.assertRaises(ValueError):self.run_report()

    def test_missing_manifest_fields_reject(self):
        del self.document["currency"];self.save()
        with self.assertRaises(ValueError):self.run_report()

    def test_wrong_schema_reject(self):
        self.document["schema"] = "hepta.live.v1";self.save()
        with self.assertRaises(ValueError):self.run_report()

    def test_duplicate_instrument_reject(self):
        self.document["instruments"].append(self.document["instruments"][0]);self.save()
        with self.assertRaisesRegex(ValueError,"duplicate instrument"):self.run_report()

    def test_duplicate_reference_reject(self):
        self.document["instruments"][1]["sources"] = self.document["instruments"][0]["sources"];self.save()
        with self.assertRaisesRegex(ValueError,"duplicate source"):self.run_report()

    def test_missing_and_unused_bindings_reject(self):
        for sources in ({"a":self.sources["a"]},{**self.sources,"unused":self.sources["a"]}):
            with self.subTest(sources=sources),self.assertRaises(ValueError):run_portfolio_report(self.manifest,sources)

    def test_empty_sources_and_bad_digest_reject(self):
        for entries in ([],[{"ref":"a","sha256":"x"}], [{"ref":"a","sha256":"0"*64,"path":"anything"}]):
            self.document["instruments"][0]["sources"]=entries;self.save()
            with self.subTest(entries=entries),self.assertRaises(ValueError):self.run_report()

    def test_foreign_bar_identity_rejects(self):
        entry=self.document["instruments"][0]["sources"][0]
        data=self.sources["a"].read_bytes().replace(b"A,",b"C,")
        self.sources["a"].write_bytes(data);entry["sha256"]=hashlib.sha256(data).hexdigest();self.save()
        with self.assertRaisesRegex(ValueError,"identity"):self.run_report()

    def test_malformed_ohlc_is_rejected_by_existing_parser(self):
        data=self.sources["a"].read_bytes().replace(b"10,10,10,10,1",b"10,9,10,10,1",1)
        self.sources["a"].write_bytes(data)
        self.document["instruments"][0]["sources"][0]["sha256"]=hashlib.sha256(data).hexdigest();self.save()
        with self.assertRaisesRegex(ValueError,"OHLC"):self.run_report()

    def test_input_byte_and_row_budgets_are_global(self):
        for kw in ({"max_input_bytes":1},{"max_total_bars":11},{"max_input_bytes":True}):
            with self.subTest(kw=kw),self.assertRaises(ValueError):self.run_report(**kw)

    def test_cross_file_trading_day_regression_reject(self):
        rows=self.sources["a"].read_bytes().splitlines(keepends=True)
        pieces=[rows[0]+b"".join(rows[1:4]),rows[0]+b"".join(rows[4:]).replace(b"20260102",b"20260101")]
        entries=[]; del self.sources["a"]
        for ref,data in zip(("a1","a2"),pieces):
            path=self.root/(ref+".csv");path.write_bytes(data);self.sources[ref]=path
            entries.append({"ref":ref,"sha256":hashlib.sha256(data).hexdigest()})
        self.document["instruments"][0]["sources"]=entries;self.save()
        with self.assertRaisesRegex(ValueError,"trading-day"):self.run_report()

    def test_cross_file_overlap_reject(self):
        data=self.sources["a"].read_bytes()
        self.sources["a2"]=self.root/"a2.csv";self.sources["a2"].write_bytes(data)
        self.document["instruments"][0]["sources"].append({"ref":"a2","sha256":hashlib.sha256(data).hexdigest()});self.save()
        with self.assertRaisesRegex(ValueError,"overlap/regression"):self.run_report()

    def test_final_symlink_rejected(self):
        link=self.root/"link.csv";link.symlink_to(self.sources["a"])
        self.sources["a"]=link
        with self.assertRaises(OSError):self.run_report()

    def test_fifo_rejected_without_blocking(self):
        fifo=self.root/"source.fifo";os.mkfifo(fifo);self.sources["a"]=fifo
        with self.assertRaisesRegex(ValueError,"regular"):self.run_report()

    def test_directory_is_not_a_source(self):
        self.sources["a"]=self.root
        with self.assertRaises(ValueError):self.run_report()

    def test_changed_capture_is_rejected(self):
        real=os.read
        changed=False
        def mutate(fd,n):
            nonlocal changed
            data=real(fd,n)
            if not changed:
                changed=True
                with self.sources["a"].open("ab") as f:f.write(b"\n")
            return data
        with patch("hepta_research.portfolio.os.read",side_effect=mutate):
            with self.assertRaisesRegex(ValueError,"changed"):_capture(self.sources["a"],10000)

    def test_cli_success_and_atomic_report(self):
        self.assertEqual(main(self.argv()),0)
        check_report(json.loads(self.output.read_text()))
        self.assertEqual(self.output.stat().st_mode&0o777,0o600)

    def test_cli_rejection_preserves_existing_output(self):
        self.output.write_text("original")
        self.sources["a"].write_bytes(b"bad")
        with patch("sys.stderr",new=io.StringIO()):self.assertEqual(main(self.argv()),2)
        self.assertEqual(self.output.read_text(),"original")

    def test_cli_duplicate_binding_rejects(self):
        with patch("sys.stderr",new=io.StringIO()):
            self.assertEqual(main(self.argv()+["--source","a="+str(self.sources["a"])]),2)
        self.assertFalse(self.output.exists())

    def test_cli_cannot_overwrite_input_or_manifest(self):
        for path in (self.manifest,self.sources["a"]):
            before=path.read_bytes();self.output=path
            with patch("sys.stderr",new=io.StringIO()):self.assertEqual(main(self.argv()),2)
            self.assertEqual(path.read_bytes(),before)

    def test_cli_hardlink_input_output_alias_rejects(self):
        self.output.hardlink_to(self.sources["a"])
        with patch("sys.stderr",new=io.StringIO()):self.assertEqual(main(self.argv()),2)

    def test_local_manifest_paths_are_never_executed(self):
        self.document["instruments"][0]["sources"][0]["ref"]="/etc/passwd";self.save()
        with self.assertRaises(ValueError):self.run_report()

    def test_manifest_number_precision_not_binary_float(self):
        text=self.manifest.read_text().replace('"capital": "1000"','"capital": 1000.000000000000000001')
        self.manifest.write_text(text)
        r=self.run_report()
        self.assertEqual(r["assumptions"]["capital"],D("1000.000000000000000001"))

    def test_mixed_currency_rejects_before_source_read(self):
        self.document["instruments"][1]["currency"]="EUR";self.save()
        self.sources["a"].unlink()
        with self.assertRaisesRegex(ValueError,"mixed currencies"):self.run_report()

    def test_module_cli_subprocess(self):
        result=subprocess.run([sys.executable,"-B","-m","hepta_research.portfolio",*self.argv()],
                              capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        check_report(json.loads(self.output.read_text()))


if __name__ == "__main__":unittest.main()
