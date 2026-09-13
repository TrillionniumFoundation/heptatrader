from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import hepta_oms_report as report


def sample(t=1000, b=100, r=10, epoch="service-one"):
    value = {"schema": report.SCHEMA, "known": True, "observed_at_ms": t,
             "monotonic_ms": t, "service_epoch": epoch, "write_poisoned": False,
             "bytes": b, "records": r, "max_bytes": 10000, "max_records": 1000,
             "pending_records": 0, "queue_depth": 0, "buffered_depth": 0,
             "byte_headroom": max(0, 10000-b), "record_headroom": max(0, 1000-r),
             "status": "OK", "authorization_effect": "NONE"}
    value["status"] = "EXCEEDED" if b > 10000 or r > 1000 else "WARNING" if b >= 8000 or r >= 800 else "OK"
    return value


def metric(n=1000):
    return {"samples": n, "total_ns": n*1000000, "max_ns": 1000000, "last_ns": 1000000,
            "saturated": False, "bucket_counts": [0, 0, 0, n, 0, 0, 0, 0, 0, 0, 0],
            "bucket_upper_ns": list(report.BOUNDS_NS) + [None]}


class OmsReportTests(unittest.TestCase):
    def test_growth_uses_monotonic_and_exact_incarnation(self):
        a, b = sample(), sample(6000, 1100, 110)
        result = report.report([a, b], 6000, planning_seconds=60)
        self.assertEqual(result["trend"]["bytes_per_second"], 200)
        self.assertEqual(result["trend"]["records_per_second"], 20)
        self.assertEqual(result["trend"]["estimated_headroom_seconds"], 44.5)
        self.assertEqual(result["alerts"][0]["rule_id"], "OMS_HEADROOM_PLANNING")
        for change in (dict(service_epoch="restarted"), dict(monotonic_ms=1000),
                       dict(monotonic_ms=30000), dict(bytes=50, byte_headroom=9950)):
            altered = dict(b, **change)
            self.assertIsNone(report.report([a, altered], 6000)["trend"]["window_ms"])
        # Wall timestamps are NOT used to compute the growth rate.
        b["observed_at_ms"] = 9000
        self.assertEqual(report.report([a, b], 9000)["trend"]["bytes_per_second"], 200)

    def test_idle_or_unidentified_is_not_infinite_capacity_proof(self):
        a, b = sample(), sample(6000)
        result = report.report([a, b], 6000)
        self.assertEqual(result["trend"]["bytes_per_second"], 0)
        self.assertIsNone(result["trend"]["estimated_headroom_seconds"])
        del b["service_epoch"]
        self.assertIsNone(report.report([a, b], 6000)["trend"]["window_ms"])

    def test_unknown_stale_and_poison_are_distinct(self):
        unknown = sample()
        unknown.update(known=False, status="UNKNOWN", write_poisoned=True,
                       bytes=None, records=None, byte_headroom=None, record_headroom=None)
        result = report.report([unknown], 16001)
        codes = {a["rule_id"]: a["severity"] for a in result["alerts"]}
        self.assertEqual(codes["OMS_WRITER_POISONED"], "P1")
        self.assertEqual(codes["OMS_CAPACITY_UNKNOWN"], "P2")
        self.assertEqual(codes["OMS_TELEMETRY_STALE"], "P2")
        text = report.prometheus(unknown, result)
        self.assertNotIn("hepta_oms_bytes ", text)
        self.assertIn("hepta_oms_capacity_known 0", text)

    def test_inclusive_boundaries_and_clock_error(self):
        self.assertTrue(report.report([sample()], 16000)["fresh"])
        self.assertFalse(report.report([sample()], 16001)["fresh"])
        self.assertEqual(report.report([sample()], 999)["alerts"][0]["rule_id"], "OMS_TELEMETRY_CLOCK")
        self.assertEqual(report.report([sample(b=8000)], 1000)["capacity_status"], "WARNING")
        self.assertEqual(report.report([sample(b=10000)], 1000)["capacity_status"], "WARNING")
        self.assertEqual(report.report([sample(b=10001)], 1000)["capacity_status"], "EXCEEDED")

    def test_histogram_quantiles_and_prometheus_accounting(self):
        s=sample();s["append_latency"]=metric()
        result=report.report([s],1000)
        self.assertEqual(result["latencies"]["append_latency"]["p99_upper_ns"], 1000000)
        self.assertEqual(result["latencies"]["append_latency"]["p999_upper_ns"], 1000000)
        text=report.prometheus(s,result)
        self.assertIn('hepta_oms_append_latency_seconds_bucket{le="+Inf"} 1000',text)
        self.assertIn('hepta_oms_append_latency_seconds_sum 1.0',text)
        self.assertNotIn('service-one',text)
        s["append_latency"]=metric(999)
        self.assertIsNone(report.report([s],1000)["latencies"]["append_latency"]["p999_upper_ns"])
        s["append_latency"]["saturated"]=True
        self.assertIsNone(report.quantile_upper(s["append_latency"]))
        self.assertNotIn('histogram',report.prometheus(s,report.report([s],1000)))

    def test_malformed_metrics_and_accounting_rejected(self):
        for key,value in (("records",True),("max_bytes",-1),("bytes",2**64),
                          ("status","OKAY"),("byte_headroom",0),("pending_records",1),
                          ("service_epoch",'secret"\n'),("authorization_effect","ALLOW")):
            s=sample();s[key]=value
            with self.subTest(key=key), self.assertRaises(ValueError): report.validate(s)
        s=sample();s["append_latency"]=metric();s["append_latency"]["bucket_counts"][0]=1
        with self.assertRaises(ValueError):report.validate(s)

    def test_bounded_regular_snapshot_cli_and_secret_free_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"samples"
            s=sample();s["unrelated_secret"]="DO-NOT-PUBLISH"
            path.write_text("ordinary unrelated log line\n"+json.dumps(s)+"\n")
            values,digest=report.read_samples(path)
            self.assertEqual(len(values),1);self.assertEqual(len(digest),64)
            proc=subprocess.run([sys.executable,str(ROOT/"scripts/hepta_oms_report.py"),"--input",str(path),"--now-ms","1000"],capture_output=True,text=True,timeout=5)
            self.assertEqual(proc.returncode,0,proc.stderr)
            self.assertNotIn("DO-NOT-PUBLISH",proc.stdout+proc.stderr)
            os.symlink(path,Path(tmp)/"link")
            with self.assertRaises(OSError):report.read_samples(Path(tmp)/"link")
            os.link(path,Path(tmp)/"hard")
            with self.assertRaises(ValueError):report.read_samples(path)
            (Path(tmp)/"hard").unlink()
            os.mkfifo(Path(tmp)/"fifo")
            with self.assertRaises(ValueError):report.read_samples(Path(tmp)/"fifo")
            for raw in ('{"x":1,"x":2}', '{"x":1e999}', '{"x":1e-999}', '{"x":NaN}', 'x'*(report.MAX_LINE+1)):
                path.write_text(raw+"\n")
                with self.assertRaises(ValueError):report.read_samples(path)
            path.write_text('{"x":'+ '['*2000+'0'+']'*2000+'}\n')
            deep=subprocess.run([sys.executable,str(ROOT/"scripts/hepta_oms_report.py"),"--input",str(path)],capture_output=True,text=True,timeout=5)
            self.assertEqual(deep.returncode,2)
            self.assertEqual(deep.stderr.strip(),"OMS_TELEMETRY_INPUT_INVALID")
            self.assertEqual(deep.stdout,"")

    def test_real_cpp_histogram_wire_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/"hist.cpp";binary=Path(tmp)/"hist"
            source.write_text('#include "HeptaTrade/oms_latency_observation.h"\n#include <iostream>\nint main(){ OmsLatencySummary x; for (unsigned i=0;i<10;++i){x.Observe(x.BucketUpper(i));x.Observe(x.BucketUpper(i)+1);}WriteOmsLatencyJson(std::cout,x);}\n')
            subprocess.run(["g++","-std=c++11","-I",str(ROOT),str(source),"-o",str(binary)],check=True,capture_output=True,timeout=30)
            actual=json.loads(subprocess.check_output([str(binary)],timeout=5))
            self.assertEqual(actual["samples"],20)
            self.assertEqual(actual["bucket_counts"],[1]+[2]*9+[1])
            s=sample();s["append_latency"]=actual
            report.validate(s)
            self.assertIsNone(report.quantile_upper(actual)) # p99 is in +Inf, not an invented 10s value


if __name__ == "__main__":
    unittest.main()
