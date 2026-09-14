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
sys.path.insert(0, str(ROOT / 'scripts'))
import hepta_oms_report as report


def metric(n):
    return dict(samples=n, total_ns=n*1000000, max_ns=1000000 if n else 0,
                last_ns=1000000 if n else 0, saturated=False,
                bucket_counts=[0, 0, 0, n, 0, 0, 0, 0, 0, 0, 0],
                bucket_upper_ns=list(report.BOUNDS_NS)+[None])


def sample(t=1000, epoch='gw-one', delivered=3, failed=0):
    n=delivered+failed
    value = dict(schema=report.GATEWAY_SCHEMA, service_epoch=epoch,
        observed_at_ms=t, monotonic_ms=t, authorization_effect='NONE',
        metrics_saturated=False, responses_delivered=delivered,
        response_write_failures=failed, results=[n, 0, 0, 0, 0, 0, 0],
        queue_backpressure_rejections=0, owner_backpressure_rejections=0,
        deadline_rejections=0, cancelled_requests=0,
        pending_connections=0, active_requests=0, ready_owners=0,
        max_pending_connections=32)
    value.update({key: metric(n) for key in report.GATEWAY_LATENCIES})
    return value


class GatewayObservabilityTests(unittest.TestCase):
    def test_actual_accounting_shapes_reject_inconsistency(self):
        value=sample()
        self.assertEqual(report.validate_gateway(value), value)
        for key, bad in [('results', [0]*7), ('responses_delivered', True),
                         ('service_epoch', 'secret\n'), ('metrics_saturated', 0),
                         ('max_pending_connections', 0), ('queue_wait_latency', None)]:
            with self.subTest(key=key):
                altered=copy.deepcopy(value);altered[key]=bad
                with self.assertRaises(ValueError): report.validate_gateway(altered)
        value['response_write_latency']['bucket_counts'][0]=1
        with self.assertRaises(ValueError): report.validate_gateway(value)

    def test_delivery_failures_are_not_success_and_do_not_alert_forever(self):
        a,b,c=sample(), sample(6000,failed=1), sample(11000,delivered=4,failed=1)
        flagged=report.gateway_report([a,b],6000)
        self.assertEqual(flagged['interval_deltas']['response_write_failures'],1)
        self.assertEqual(flagged['alerts'][0]['rule_id'],'GATEWAY_RESPONSE_WRITE_FAILURE')
        self.assertEqual(report.gateway_report([b,c],11000)['alerts'],[])
        for change in (dict(service_epoch='new'),dict(monotonic_ms=1000),
                       dict(monotonic_ms=100000),dict(responses_delivered=1,results=[2,0,0,0,0,0,0],
                           response_write_latency=metric(2))):
            changed=dict(b,**change)
            self.assertIsNone(report.gateway_report([a,changed],6000)['interval_deltas'])

    def test_stale_future_and_capacity_are_observable(self):
        for now,rule in ((0,'GATEWAY_TELEMETRY_CLOCK'),(20000,'GATEWAY_TELEMETRY_STALE')):
            self.assertIn(rule,[x['rule_id'] for x in report.gateway_report([sample()],now)['alerts']])
        value=sample();value['pending_connections']=32
        self.assertIn('GATEWAY_QUEUE_SATURATED',[x['rule_id'] for x in report.gateway_report([value],1000)['alerts']])

    def test_prometheus_has_bounded_labels_and_cumulative_buckets(self):
        value=sample(delivered=1000)
        text=report.gateway_prometheus(value,report.gateway_report([value],1000))
        self.assertIn('hepta_gateway_response_write_latency_seconds_bucket{le="+Inf"} 1000',text)
        self.assertIn('hepta_gateway_response_write_latency_seconds_count 1000',text)
        self.assertNotIn('gw-one',text)
        self.assertEqual(text.count('hepta_gateway_results_total{'),7)
        self.assertEqual(report.gateway_report([value],1000)['latencies']['execution_latency']['p999_upper_ns'],1000000)

    def test_real_cli_and_input_failure_never_claim_healthy_zero(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'snapshot';path.write_text(json.dumps(sample())+'\n')
            cmd=[sys.executable,str(ROOT/'scripts/hepta_oms_report.py'),'--kind','gateway',
                 '--input',str(path),'--now-ms','1000']
            result=subprocess.run(cmd,capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['service_epoch'],'gw-one')
            path.write_text('{"schema":"x","schema":"y"}\n')
            result=subprocess.run(cmd,capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,2);self.assertEqual(result.stdout,'')
            path.unlink();os.mkfifo(path)
            result=subprocess.run(cmd,capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,2)

    def test_optional_oms_queue_fields_have_real_bounds(self):
        from test_oms_operational_report import sample as oms_sample
        value=oms_sample()
        value.update(pending_bytes=100,max_pending_bytes=100,max_pending_records=2,
                     queue_capacity_rejections=1,queue_depth=1,pending_records=1,
                     record_headroom=989)
        self.assertEqual(report.validate(value),value)
        self.assertIn('OMS_PENDING_QUEUE_FULL',[x['rule_id'] for x in report.report([value],1000)['alerts']])
        value['pending_bytes']=101
        with self.assertRaises(ValueError):report.validate(value)


if __name__=='__main__': unittest.main()
