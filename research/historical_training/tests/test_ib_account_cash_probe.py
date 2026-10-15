import unittest
from historical_training.ib_account_cash_probe import CashState,nonfatal_account_transport_notice
from historical_training.common import EvidenceError

class CashProbeTests(unittest.TestCase):
 def test_only_du_account(self):
  for name in ('U123','', 'DU'*20):
   with self.assertRaisesRegex(EvidenceError,'PAPER_ACCOUNT_ONLY'):CashState(name)
 def test_complete_monitor_does_not_mutate_original(self):
  x=CashState('DU123')
  x.ingest('DU123','CashBalance','EUR','-100','1000')
  x.end(1000)
  x.ingest('DU123','CashBalance','EUR','-100',1200)
  x.ingest('DU123','CashBalance','EUR','-101',2000)
  s=x.snapshot()
  self.assertEqual(s['post_end_changed_count'],1)
  self.assertEqual(s['last_post_end_delta_abs'],'1')
  self.assertTrue(s['raw_balances_redacted'])
  self.assertEqual(str(x.head['EUR']),'-100')
  import hashlib
  self.assertNotEqual(s['initial_hashed_eur_balance'],hashlib.sha256(b'-100').hexdigest())
 def test_duplicate_conflict_retained(self):
  x=CashState('DU123')
  x.ingest('DU123','CashBalance','EUR','-100',1000)
  x.ingest('DU123','CashBalance','EUR','-101',1001)
  self.assertIn('INITIAL_DUPLICATE_VALUE_CONFLICT',x.snapshot()['discrepancies'])
 def test_reject_incomplete_account_or_weak_numeric(self):
  x=CashState('DU123')
  with self.assertRaises(EvidenceError):x.end(1000)
  x=CashState('DU123')
  for bad in ('nan','Infinity','abc'):
   with self.assertRaises(EvidenceError):x.ingest('DU123','CashBalance','EUR',bad,1000)
  with self.assertRaises(EvidenceError):x.ingest('DU999','CashBalance','EUR','20',1000)
 def test_historical_farm_loss_does_not_permit_account_errors(self):
  self.assertTrue(nonfatal_account_transport_notice(-1,2105,'cashhmds disconnected'))
  self.assertFalse(nonfatal_account_transport_notice(98501,2105,'cashhmds disconnected'))
  self.assertFalse(nonfatal_account_transport_notice(-1,2105,'other service error'))
  self.assertFalse(nonfatal_account_transport_notice(98501,201,'request failed'))
 def test_no_authoritative_claim(self):
  x=CashState('DU123')
  x.ingest('DU123','CashBalance','EUR','12',1)
  x.end(2)
  y=x.snapshot()
  self.assertNotIn('paper_authorized',y)
  self.assertNotIn('recovery_barrier_complete',y)
if __name__=='__main__':unittest.main()
