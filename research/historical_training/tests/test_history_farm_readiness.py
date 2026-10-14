import unittest
from historical_training.history_farm_readiness import FarmReadiness, validate_wait
from historical_training.common import EvidenceError

class FakeClock:
 def __init__(self): self.t=0.; self.after=None
 def __call__(self):return self.t
 def sleep(self,n):
  self.t+=n
  if self.after:self.after(self.t)

class FarmTest(unittest.TestCase):
 def make(self):
  clock=FakeClock();f=FarmReadiness(clock,lambda:1791960000+clock());return f,clock
 def test_disconnected_never_dispatches(self):
  f,c=self.make();f.notice(-1,2105,'断开:cashhmds')
  with self.assertRaises(EvidenceError):f.wait(1,sleep=c.sleep)
  with self.assertRaises(EvidenceError):
   with f.dispatch(20,7800):self.fail('dispatched')
  self.assertEqual(f.snapshot()['dispatch_checks'],[])
 def test_same_session_recovery(self):
  f,c=self.make();f.notice(-1,2105,'断开:cashhmds')
  c.after=lambda t:f.notice(-1,2106,'正常:cashhmds') if t>=.15 else None
  self.assertEqual(f.wait(1,sleep=c.sleep),'CONNECTED')
  with f.dispatch(20,7800):pass
  self.assertEqual([e['code'] for e in f.events],[2105,2106])
 def test_inactive_is_available_on_demand(self):
  f,c=self.make();f.notice(-1,2107,'inactive:cashhmds');self.assertEqual(f.wait(0),'INACTIVE')
 def test_other_farm_cannot_recover(self):
  f,c=self.make();f.notice(-1,2105,'bad:cashhmds');f.notice(-1,2106,'good:apachmds')
  with self.assertRaises(EvidenceError):f.wait(1,sleep=c.sleep)
 def test_substring_is_not_exact_farm(self):
  f,c=self.make()
  for name in ('notcashhmds','cashhmds.backup','cashhmds,apachmds'):
   f.notice(-1,2106,'good:'+name)
  self.assertEqual(f.state,'UNKNOWN')
 def test_request_scoped_notice_not_global(self):
  f,c=self.make();f.notice(7800,2106,'good:cashhmds');self.assertEqual(f.state,'UNKNOWN')
 def test_disconnect_between_wait_and_wire(self):
  f,c=self.make();f.notice(-1,2106,'good:cashhmds');f.wait(0);f.notice(-1,2105,'bad:cashhmds')
  with self.assertRaises(EvidenceError):
   with f.dispatch(87,7800):self.fail('dispatched')
 def test_fatal_not_healed_by_farm_notice(self):
  f,c=self.make();f.notice(-1,1100,'lost');f.notice(-1,2106,'good:cashhmds')
  with self.assertRaises(EvidenceError):f.wait(1,sleep=c.sleep)
 def test_socket_loss(self):
  f,c=self.make();f.notice(-1,2106,'good:cashhmds')
  with self.assertRaises(EvidenceError):f.wait(1,alive=lambda:False,sleep=c.sleep)
 def test_no_silent_unbounded_wait(self):
  for value in (True,-1,91,1.5,'60'):
   with self.assertRaises(EvidenceError):validate_wait(value)
 def test_unknown_is_not_ready(self):
  f,c=self.make()
  with self.assertRaises(EvidenceError):f.wait(0)
 def test_unknown_one_head_probe_then_success(self):
  f,c=self.make()
  self.assertEqual(f.wait_or_probe(0,allow_unknown=True,sleep=c.sleep),'UNKNOWN_SINGLE_HEAD_PROBE')
  with f.dispatch(87,7800):pass
  with self.assertRaises(EvidenceError):
   with f.dispatch(87,7801):pass
  with self.assertRaises(EvidenceError):
   with f.dispatch(20,7801):pass
  self.assertTrue(f.prove_head_response(7800))
  with f.dispatch(20,7801):pass
  self.assertEqual(f.snapshot()['state'],'PROBED')
 def test_unknown_probe_error_or_disconnect_no_second_request(self):
  f,c=self.make()
  self.assertEqual(f.wait_or_probe(0,allow_unknown=True,sleep=c.sleep),'UNKNOWN_SINGLE_HEAD_PROBE')
  with f.dispatch(87,7800):pass
  f.notice(-1,2105,'disconnected:cashhmds')
  self.assertFalse(f.prove_head_response(7800))
  with self.assertRaises(EvidenceError):
   with f.dispatch(20,7801):pass
  self.assertEqual(len(f.snapshot()['dispatch_checks']),1)
 def test_unknown_default_still_denies(self):
  f,c=self.make()
  with self.assertRaises(EvidenceError):f.wait_or_probe(0,sleep=c.sleep)
 def test_order_code_denied(self):
  f,c=self.make();f.notice(-1,2106,'good:cashhmds')
  with self.assertRaises(EvidenceError):
   with f.dispatch(3,7800):self.fail('order')
 def test_bounded_farm_churn(self):
  f,c=self.make()
  for _ in range(129):f.notice(-1,2106,'good:cashhmds')
  with self.assertRaises(EvidenceError):f.wait(0)
if __name__=='__main__':unittest.main()
