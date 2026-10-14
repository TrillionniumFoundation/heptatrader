import unittest
from datetime import datetime, timezone, timedelta
from historical_training.ib_history import validate_cutoff, request_window_days, plans
from historical_training.egress import HistoryGuard
from historical_training.common import EvidenceError

class IncrementalTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 14, 6, tzinfo=timezone.utc)
        self.end = datetime(2026, 10, 13, 23, 59, 59, tzinfo=timezone.utc)
    def test_legacy_remains_strict(self):
        with self.assertRaises(EvidenceError): validate_cutoff(self.end, now=self.now)
    def test_completed_previous_day(self):
        validate_cutoff(self.end, 'COMPLETED_UTC_DAY', self.now)
    def test_no_current_day(self):
        with self.assertRaises(EvidenceError): validate_cutoff(self.end+timedelta(days=1), 'COMPLETED_UTC_DAY', self.now)
    def test_no_intraday_exception(self):
        with self.assertRaises(EvidenceError): validate_cutoff(self.end-timedelta(hours=1), 'COMPLETED_UTC_DAY', self.now)
    def test_bar_settlement_margin(self):
        with self.assertRaises(EvidenceError): validate_cutoff(self.end, 'COMPLETED_UTC_DAY', self.end+timedelta(seconds=60))
    def test_unknown_policy(self):
        with self.assertRaises(EvidenceError): validate_cutoff(self.end, 'ANY', self.now)
    def test_legacy_plan(self):
        p=plans({'chunks':1}, self.end)
        self.assertEqual(len(p),7);self.assertTrue(all(x['duration']=='30 D' for x in p[3:]))
    def test_one_week_plan(self):
        p=plans({'chunks':1,'window_days':7}, self.end)
        self.assertEqual(len(p),7);self.assertTrue(all(x['duration']=='7 D' for x in p[3:]))
        self.assertEqual(p[3]['requested_start_utc'],'2026-10-06T23:59:59+00:00')
    def test_window_bounds(self):
        for d in (0,31,True,1.5,'7'):
            with self.assertRaises(EvidenceError): request_window_days({'chunks':1,'window_days':d})
    def test_no_multiple_incremental_chunks(self):
        with self.assertRaises(EvidenceError): request_window_days({'chunks':2,'window_days':7})
    def test_guard_duration(self):
        for duration in ('31 D','1 Y','-1 D','07 D',None):
            with self.assertRaises(EvidenceError): HistoryGuard(8984).register(7800,{'kind':'bars','what':'BID','duration':duration})
    def test_bound_incremental_wire(self):
        g=HistoryGuard(8984)
        plan={'kind':'bars','what':'BID','duration':'7 D','end':'20261013 23:59:59 UTC'}
        g.register(7800,plan)
        g.check('\0'.join(['71','2','8984',''])+'\0',True,187,now=0)
        fields=['20','7800','12087792','EUR','CASH','','0','','','IDEALPRO','','USD','','','0',plan['end'],'5 mins','7 D','0','BID','2','0','']
        g.check('\0'.join(fields)+'\0',True,187,now=15)
        self.assertEqual(g.issued,{7800:20})
    def test_wire_duration_must_match_frozen_plan(self):
        g=HistoryGuard(8984)
        plan={'kind':'bars','what':'BID','duration':'7 D','end':'20261013 23:59:59 UTC'}
        g.register(7800,plan)
        g.check('\0'.join(['71','2','8984',''])+'\0',True,187,now=0)
        fields=['20','7800','12087792','EUR','CASH','','0','','','IDEALPRO','','USD','','','0',plan['end'],'5 mins','30 D','0','BID','2','0','']
        with self.assertRaises(EvidenceError):g.check('\0'.join(fields)+'\0',True,187,now=15)
    def test_no_order_method(self):
        g=HistoryGuard(8984)
        with self.assertRaises(EvidenceError):g.check('3\0',True,187)
if __name__=='__main__':unittest.main()
