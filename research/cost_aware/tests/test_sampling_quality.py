import copy,json,subprocess,sys,tempfile,unittest
from pathlib import Path
from fixtures import *
from hepta_cost_capture import Capture,read_capture
from hepta_cost_replay import sampling_quality

CONTEXT={'cycle_id':'obs-synthetic-quality','reset_sha256':'a'*64,'mode':'WATCH','broker_mutation_authorized':False}
class SamplingQualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.path=self.root/'input.jsonl'
    def tearDown(self):self.tmp.cleanup()
    def make(self,events,context=CONTEXT):
        with Capture(self.path,'BTC.USD',source_kind='SYNTHETIC',min_free_bytes=0,observation_context=context) as writer:
            for kind,data,at in events:writer.append(kind,data,at_ms=at)
        return read_capture(self.path)
    def test_repeated_reads_do_not_increase_independent_updates_or_coverage(self):
        q=quote(NOW)
        cap=self.make([('QUOTE',q,NOW),('QUOTE',q,NOW+2000),('QUOTE',q,NOW+4000),
                       ('SAMPLING_FAILURE',{'reason_code':'AUTHORITATIVE_QUOTE_STALE'},NOW+10000)])
        report=sampling_quality(cap)
        self.assertEqual(report['valid_quote_reads'],3)
        self.assertEqual(report['distinct_quote_observations'],1)
        self.assertEqual(report['repeated_valid_quote_reads'],2)
        self.assertEqual(report['usable_quote_coverage_ms'],5000)
        self.assertEqual(report['usable_quote_coverage_fraction'],.5)
        self.assertEqual(report['uncovered_intervals'],[{'start_ms':NOW+5000,'end_ms':NOW+10000,'duration_ms':5000}])
    def test_invalid_source_records_are_not_accepted(self):
        wrong=quote(NOW+10000);wrong['authoritative']=False
        cap=self.make([('QUOTE',quote(),NOW),('QUOTE',wrong,NOW+10000)])
        report=sampling_quality(cap);self.assertEqual(len(report['invalid_quote_records']),1)
        self.assertEqual(report['valid_quote_reads'],1)
    def test_gap_diagnosed_separately_from_http_or_source_failure_rate(self):
        cap=self.make([('QUOTE',quote(),NOW),('SAMPLING_FAILURE',{'reason_code':'QUOTE_STALE'},NOW+10000),
                       ('QUOTE',quote(NOW+40000),NOW+40000)])
        report=sampling_quality(cap);self.assertEqual(report['source_gaps_over_30s'][0]['duration_ms'],40000)
        self.assertEqual(report['quote_read_spacing_ms']['max'],40000)
        self.assertTrue(report['record_fraction_not_exchange_delivery_probability'])
    def test_qualified_current_cycle_must_match_explicit_expectations(self):
        cap=self.make([('QUOTE',quote(),NOW)])
        report=sampling_quality(cap,expected_cycle_id=CONTEXT['cycle_id'],expected_reset_sha256='a'*64)
        self.assertEqual(report['observation_context'],CONTEXT)
        with self.assertRaisesRegex(CostError,'CYCLE_MISMATCH'):sampling_quality(cap,expected_cycle_id='obs-old-cycle')
        with self.assertRaisesRegex(CostError,'RESET_DIGEST'):sampling_quality(cap,expected_reset_sha256='b'*64)
    def test_legacy_context_not_silently_current(self):
        cap=self.make([('QUOTE',quote(),NOW)],context=None)
        with self.assertRaisesRegex(CostError,'CYCLE_MISMATCH'):sampling_quality(cap,expected_cycle_id=CONTEXT['cycle_id'])
    def test_future_events_not_counted_in_earlier_snapshot(self):
        cap=self.make([('QUOTE',quote(),NOW),('QUOTE',quote(NOW+2000),NOW+2000),
                       ('SAMPLING_FAILURE',{'reason_code':'X'},NOW+4000)])
        report=sampling_quality(cap,cutoff_ms=NOW+2500)
        self.assertEqual(report['valid_quote_reads'],2);self.assertEqual(report['window_end_ms'],NOW+2000)
        self.assertEqual(report['sampling_failure_reasons'],{})
    def test_clock_or_source_expiry_not_renewed_by_capture(self):
        cap=self.make([('QUOTE',quote(),NOW+6000)])
        report=sampling_quality(cap);self.assertEqual(report['valid_quote_reads'],0)
        self.assertEqual(report['source_age_ms']['count'],0);self.assertEqual(report['eligible_training_cohorts'],0)
    def test_conflicting_same_observation_does_not_fit(self):
        first=quote();changed=copy.deepcopy(first);changed['bid']='99.9';changed['ask']='100.1'
        cap=self.make([('QUOTE',first,NOW),('QUOTE',changed,NOW+1)])
        report=sampling_quality(cap);self.assertEqual(len(report['quote_conflicts']),1)
        self.assertEqual(report['calibration_evaluation_error'],'QUALITY_CONFLICTING_QUOTE_OBSERVATION')
        self.assertIsNone(report['calibration_artifact'])
    def test_numeric_formatting_is_not_a_conflicting_price(self):
        first=quote();same=copy.deepcopy(first);same['bid']='100.00';same['ask']='100.000'
        cap=self.make([('QUOTE',first,NOW),('QUOTE',same,NOW+1)])
        self.assertFalse(sampling_quality(cap)['quote_conflicts'])
    def test_same_timestamp_in_distinct_subscription_not_false_duplicate(self):
        a=quote();b=copy.deepcopy(a);b['subscription_id']='new-subscription'
        cap=self.make([('QUOTE',a,NOW),('QUOTE',b,NOW+1)])
        report=sampling_quality(cap);self.assertEqual(report['distinct_quote_observations'],2)
        self.assertFalse(report['quote_conflicts'])
    def test_mature_holds_retained_but_do_not_inflate_samples(self):
        f=forecast(NOW,action='HOLD',move='0')
        events=[('QUOTE',quote(),NOW),('CANDIDATE',{'forecast':f},NOW+1000)]
        events.extend(('QUOTE',quote(NOW+sec*1000),NOW+sec*1000) for sec in range(5,311,5))
        report=sampling_quality(self.make(events))
        self.assertEqual(report['candidate_outcomes'],{'OBSERVED':1})
        self.assertEqual(report['model_completion_latency_ms']['max'],1000)
        stat=next(iter(report['cohort_counts'].values()))
        self.assertEqual(stat['independent_samples'],0);self.assertEqual(stat['sample_shortfall'],30)
        self.assertEqual(stat['date_shortfall'],2);self.assertFalse(report['strategy_accepted'])
    def test_overlap_purge_explains_real_sample_deficit(self):
        events=[]
        for sec in range(0,431,5):
            at=NOW+sec*1000;events.append(('QUOTE',quote(at),at))
            if sec in (0,120):events.append(('CANDIDATE',{'forecast':forecast(at)},at))
        report=sampling_quality(self.make(events));stat=next(iter(report['cohort_counts'].values()))
        self.assertEqual(stat['usable_before_nonoverlap_purge'],2)
        self.assertEqual(stat['independent_samples'],1);self.assertEqual(stat['overlap_exclusions'],1)
        self.assertEqual(stat['sample_shortfall'],29);self.assertEqual(stat['date_shortfall'],1)
    def test_no_mutation_and_determinism(self):
        cap=self.make([('QUOTE',quote(),NOW),('QUOTE',quote(NOW+2000),NOW+2000)])
        before=canonical(cap);report=sampling_quality(cap)
        self.assertEqual(before,canonical(cap));self.assertEqual(report,sampling_quality(cap))
        self.assertTrue(report['diagnostic_only']);self.assertFalse(report['vendor_signature_verified'])
        self.assertFalse(report['calibration_activated']);self.assertFalse(report['trading_driver_switched'])
    def test_installed_style_cli_quality(self):
        self.make([('QUOTE',quote(),NOW),('QUOTE',quote(NOW+2000),NOW+2000)])
        script=Path(__file__).resolve().parents[1]/'scripts/hepta_cost_replay.py';out=self.root/'report.json'
        p=subprocess.run([sys.executable,'-B',str(script),'--capture',str(self.path),'--quality',
            '--expected-cycle-id',CONTEXT['cycle_id'],'--expected-reset-sha256','a'*64,'--output',str(out)],capture_output=True,text=True,timeout=10)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual(json.loads(out.read_text())['distinct_quote_observations'],2)
        self.assertEqual(out.stat().st_mode&0o077,0)
    def test_header_only_and_bad_cutoffs_rejected(self):
        cap=self.make([])
        with self.assertRaises(CostError):sampling_quality(cap)
        for invalid in [True,0,-1]:
            with self.assertRaises(CostError):sampling_quality(cap,cutoff_ms=invalid)
