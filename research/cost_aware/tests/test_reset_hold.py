import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fixtures import *
import hepta_cost_observer as observer
import hepta_cost_driver as adapter
from hepta_cost_capture import ResetHold, require_no_reset_hold
from test_observer import Fake, Writer
from test_driver_adapter import Host, FakeCapture

class ResetHoldTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.home=Path(self.temp.name)
        self.marker=self.home/'.local/state/heptatrader/RESET-STOPPED.json'
        self.marker.parent.mkdir(parents=True)
    def tearDown(self): self.temp.cleanup()
    def mark(self): self.marker.write_text('{"state":"operator stopped"}')
    def test_absence_is_not_a_permission_receipt(self):
        self.assertIsNone(require_no_reset_hold(self.home))
        self.assertFalse(self.marker.exists())
    def test_valid_marker_blocks_without_edit(self):
        self.mark();before=self.marker.read_bytes()
        with self.assertRaisesRegex(ResetHold,'RESET_HOLD_ACTIVE'):require_no_reset_hold(self.home)
        self.assertEqual(self.marker.read_bytes(),before)
    def test_invalid_marker_still_blocks(self):
        self.marker.write_text('not json')
        with self.assertRaises(ResetHold):require_no_reset_hold(self.home)
    def test_dangling_marker_still_blocks(self):
        self.marker.symlink_to(self.home/'missing')
        with self.assertRaises(ResetHold):require_no_reset_hold(self.home)
    def test_directory_marker_still_blocks(self):
        self.marker.mkdir()
        with self.assertRaises(ResetHold):require_no_reset_hold(self.home)
    def test_sampler_reset_recheck_precedes_all_io(self):
        f=Fake();writer=Writer();guard=lambda:require_no_reset_hold(self.home)
        sampler=observer.Sampler(f,'unused',self.home,writer,lifecycle_guard=guard)
        sampler.poll(NOW);self.assertEqual(len(f.calls),1)
        before=list(writer.events);self.mark()
        with self.assertRaises(ResetHold):sampler.poll(NOW+2000)
        self.assertEqual(len(f.calls),1);self.assertEqual(writer.events,before)
    def test_observer_launcher_does_not_import_or_create_state(self):
        self.mark();output=self.home/'should-not-exist'
        with patch.object(Path,'home',return_value=self.home),patch.object(observer.os,'geteuid',return_value=self.home.stat().st_uid), \
             patch.object(observer,'load_pinned_driver',side_effect=AssertionError('base import must not occur')), \
             patch('sys.argv',['observer','--base-driver','/unread-base','--instrument','BTC.USD','--state-dir',str(output)]):
            with self.assertRaises(ResetHold):observer.main()
        self.assertFalse(output.exists())
    def test_driver_launcher_does_not_import_or_start(self):
        self.mark()
        with patch.object(Path,'home',return_value=self.home),patch.object(adapter.os,'geteuid',return_value=self.home.stat().st_uid), \
             patch.object(adapter,'load_pinned_driver',side_effect=AssertionError('no import')), \
             patch('sys.argv',['driver','--enable-paper-hooks','--base-driver','/unread-base']), \
             contextlib.redirect_stderr(io.StringIO()) as result:
            self.assertEqual(adapter.main(),78)
        self.assertFalse(json.loads(result.getvalue())['driver_started'])
    def test_private_directory_owner_is_not_assumed_to_be_1000(self):
        from hepta_cost_capture import private_directory
        import os
        with patch.object(os, 'geteuid', return_value=self.home.stat().st_uid + 1):
            with self.assertRaisesRegex(CostError, 'CAPTURE_DIRECTORY_UNSAFE'):
                private_directory(self.home)
        # Real current owner works regardless of the numeric UID allocated by CI.
        private_directory(self.home)

    def test_first_quote_failure_never_opens_capture(self):
        f=Fake();f.fail=True;output=self.home/'observation'
        with patch.object(Path,'home',return_value=self.home),patch.object(observer.os,'geteuid',return_value=self.home.stat().st_uid), \
             patch.object(observer,'load_pinned_driver',return_value=f), \
             patch.object(observer,'Capture',side_effect=AssertionError('no empty live capture')), \
             patch('sys.argv',['observer','--base-driver','/fake','--instrument','BTC.USD','--state-dir',str(output)]):
            with self.assertRaisesRegex(CostError,'FIRST_AUTHORITATIVE_QUOTE'):observer.main()
        self.assertFalse((output/'cost-observations.jsonl').exists())
        self.assertEqual(f.calls,['market.get_quote'])
    def test_entry_rejected_but_reduce_only_original_remains_available(self):
        h=Host();capture=FakeCapture();self.mark()
        close=adapter.attach(h,self.home/'calibration',capture_factory=lambda *_a,**_k:capture,
                             lifecycle_guard=lambda:require_no_reset_hold(self.home))
        state={'pending':None,'position':0.,'last_decision':forecast(),'halt_new_entries':False}
        try:
            with self.assertRaises(ResetHold):
                h.prepare_send(state,self.home/'state','b','s','t','o','OPEN',h.q,'BUY')
            self.assertFalse(h.calls)
            h.prepare_send(state,self.home/'state','b','s','t','o','CLOSE',h.q,'SELL')
            self.assertTrue(h.calls)
        finally:close()
    def test_model_request_does_not_start_during_hold(self):
        h=Host();self.mark();capture=FakeCapture()
        close=adapter.attach(h,self.home/'calibration',capture_factory=lambda *_a,**_k:capture,
                             lifecycle_guard=lambda:require_no_reset_hold(self.home))
        try:
            with patch.object(h,'run',side_effect=AssertionError('model must not start')):
                with self.assertRaises(ResetHold):h.model_decision('unused',self.home,[],0.)
        finally:close()
        self.assertFalse((self.home/'decision-schema-v2.json').exists())

    def test_reset_between_prepare_and_submit_keeps_original_command(self):
        h=Host();capture=FakeCapture()
        close=adapter.attach(h,self.home/'calibration',capture_factory=lambda *_a,**_k:capture,
                             lifecycle_guard=lambda:require_no_reset_hold(self.home))
        h.change_after_prepare=self.mark
        state={'pending':None,'position':0.,'last_decision':forecast(),'halt_new_entries':False}
        try:
            h.prepare_send(state,self.home/'state','b','s','t','o','OPEN',h.q,'BUY')
            self.assertNotIn('submit',h.calls)
            self.assertEqual(state['pending']['command_id'],'original-command')
            self.assertEqual(state['pending']['send_state'],'DEFINITELY_REJECTED')
            self.assertTrue(self.marker.exists())
        finally:close()
    def test_inflight_model_result_after_reset_is_not_recorded_as_candidate(self):
        h=Host();capture=FakeCapture()
        close=adapter.attach(h,self.home/'calibration',capture_factory=lambda *_a,**_k:capture,
                             lifecycle_guard=lambda:require_no_reset_hold(self.home))
        original=h.run
        def completes_before_reset(args,timeout):
            value=original(args,timeout);self.mark();return value
        h.run=completes_before_reset
        history=[{'observed_at_ms':NOW,'bid':'100','ask':'100','mid':'100'}]
        try:
            with self.assertRaises(ResetHold):h.model_decision('unused',self.home,history,0.)
            self.assertFalse(any(x[0]=='CANDIDATE' for x in capture.events))
        finally:close()
