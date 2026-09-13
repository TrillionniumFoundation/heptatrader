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
import hepta_gateway_report as gateway
from test_oms_operational_report import metric


def sample(t=1000, n=4):
    result = {"schema": gateway.SCHEMA, "service_epoch": "gateway-test", "observed_at_ms": t,
        "monotonic_ms": t, "running": True, "pending_connections": 0, "active_requests": 0,
        "ready_owners": 0, "worker_limit": 4, "pending_limit": 32, "queue_rejections": 0,
        "owner_rejections": 0, "deadline_rejections": 0, "cancelled_requests": 0,
        "response_attempts": n, "response_writes": n, "response_write_failures": 0,
        "saturated": False, "result_counts": {s: n if s == "ok" else 0 for s in gateway.STATUSES},
        "authorization_effect": "NONE"}
    for key in gateway.LATENCIES:
        result[key] = metric(n)
    return result


class GatewayObservationTests(unittest.TestCase):
    def test_real_cpp_fixed_counters_and_histograms(self):
        source = r'''
#include "HeptaTrade/tool_host/gateway_observation.h"
#include <iostream>
#include <thread>
#include <mutex>
#include <vector>
int main() {
    GatewayActivity a; std::mutex m; std::vector<std::thread> threads;
    for (int i=0;i<4;++i) threads.emplace_back([&] { for(int j=0;j<1000;++j) {
        std::lock_guard<std::mutex> lock(m);
        a.RecordReply(j%2 ? TradingToolCallStatus::PermissionDenied : TradingToolCallStatus::Ok, j%2==0, 1000000);
    }});
    for(auto& t:threads) t.join();
    std::cout << '{'; WriteGatewayActivity(std::cout, a); std::cout << '}';
}
'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "test.cpp").write_text(source)
            subprocess.run([os.environ.get("CXX", "g++"), "-std=c++11", "-pthread", "-I", str(ROOT), str(path / "test.cpp"), "-o", str(path / "test")], check=True, capture_output=True, timeout=30)
            value = json.loads(subprocess.check_output([str(path / "test")], timeout=10))
        self.assertEqual(value["response_attempts"], 4000)
        self.assertEqual(value["response_writes"], 2000)
        self.assertEqual(value["response_write_failures"], 2000)
        self.assertEqual(value["result_counts"]["permission_denied"], 2000)
        s=sample(n=4000);s.update(value);gateway.validate(s)
        text = gateway.prometheus(s, gateway.report([s],1000))
        self.assertIn('hepta_gateway_reply_latency_seconds_bucket{le="+Inf"} 4000', text)

    def test_accounting_and_types_fail_closed(self):
        for key, value in (("response_writes",3),("running",1),("saturated",0),("worker_limit",0),
                           ("pending_limit",1025),("service_epoch",'secret"\n'),("authorization_effect","ALLOW")):
            s=sample();s[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):gateway.validate(s)
        s=sample();s["result_counts"]["user-secret"]=1
        with self.assertRaises(ValueError):gateway.validate(s)
        s=sample();s["dispatch_latency"]=metric(5)
        with self.assertRaises(ValueError):gateway.validate(s)
        s=sample();s["reply_latency"]["bucket_counts"][0]=1
        with self.assertRaises(ValueError):gateway.validate(s)

    def test_restart_reset_and_gap_are_not_spliced(self):
        a,b=sample(),sample(6000,8)
        self.assertEqual(gateway.report([a,b],6000)["interval"]["response_attempts"],4)
        for change in ({"service_epoch":"new"},{"monotonic_ms":1000},{"monotonic_ms":20000},
                       {"worker_limit":8},{"saturated":True}):
            s=copy.deepcopy(b);s.update(change)
            self.assertIsNone(gateway.report([a,s],6000)["interval"])
        self.assertIsNone(gateway.report([b,sample(7000,4)],7000)["interval"])

    def test_alerts_are_observations_not_order_authority(self):
        s=sample();s["active_requests"]=4;s["pending_connections"]=32
        codes={v["rule_id"] for v in gateway.report([s],16001)["alerts"]}
        self.assertEqual(codes,{"GATEWAY_WORKERS_SATURATED","GATEWAY_PENDING_SATURATED","GATEWAY_TELEMETRY_STALE"})
        self.assertTrue(gateway.report([sample()],16000)["fresh"])
        self.assertFalse(gateway.report([sample()],999)["fresh"])
        self.assertEqual(gateway.report([sample()],1000)["authorization_effect"],"NONE")
        a,b=sample(),sample(6000,8)
        b["response_writes"]-=1;b["response_write_failures"]=1
        self.assertIn("GATEWAY_REPLY_WRITE_FAILURE",{v["rule_id"] for v in gateway.report([a,b],6000)["alerts"]})

    def test_installed_style_cli_is_bounded_and_private(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"log"
            s=sample();s["untrusted_secret"]="DO-NOT-EXPORT"
            path.write_text(json.dumps(s)+"\n")
            command=[sys.executable,str(ROOT/"scripts/hepta_gateway_report.py"),"--input",str(path),"--now-ms","1000"]
            result=subprocess.run(command+["--format","prometheus"],capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertNotIn("DO-NOT-EXPORT",result.stdout+result.stderr)
            self.assertNotIn("gateway-test",result.stdout)
            path.unlink();os.mkfifo(path)
            result=subprocess.run(command,capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,2);self.assertEqual(result.stdout,"")
            self.assertEqual(result.stderr.strip(),"GATEWAY_TELEMETRY_INPUT_INVALID")

    def test_insufficient_and_saturated_quantiles_are_not_fabricated(self):
        s=sample(n=999)
        self.assertIsNone(gateway.report([s],1000)["latencies"]["reply_latency"]["p999_upper_ns"])
        s["reply_latency"]["saturated"]=True
        result=gateway.report([s],1000)
        self.assertIsNone(result["latencies"]["reply_latency"]["p99_upper_ns"])
        self.assertNotIn("hepta_gateway_reply_latency_seconds_count",gateway.prometheus(s,result))
