"""Negative tests for accepted-transport vs qualified historical evidence."""
import unittest,copy
from historical_training.common import EvidenceError
from historical_training.dataset import checked_bar,schedule_extent,interval_epoch
from historical_training.egress import HistoryGuard

class CalendarTests(unittest.TestCase):
    def request(self):return {'requested_start_utc':'2026-10-06T12:00:00+00:00','requested_end_utc':'2026-10-06T12:15:00+00:00'}
    def test_float_timestamp_rejected_not_truncated(self):
        with self.assertRaisesRegex(EvidenceError,'NONINTEGER_BAR_TIME'):
            checked_bar({'time':1791288000.5,'open':'1','high':'1','low':'1','close':'1'})
    def test_naive_requested_time_rejected(self):
        with self.assertRaisesRegex(EvidenceError,'TIMEZONE_REQUIRED'):interval_epoch('2026-10-06T12:00:00')
    def test_missing_calendar_header_cannot_attest_extent(self):
        self.assertIsNone(schedule_extent({'timezone':'UTC','sessions':[]},self.request()))
    def test_late_calendar_start_rejected(self):
        self.assertIsNone(schedule_extent({'start':'20261006-12:05:00','end':'20261006-12:15:00','timezone':'UTC'},self.request()))
    def test_early_calendar_end_rejected(self):
        self.assertIsNone(schedule_extent({'start':'20261006-12:00:00','end':'20261006-12:10:00','timezone':'UTC'},self.request()))
    def test_full_header_extent(self):
        self.assertEqual(schedule_extent({'start':'20261006-12:00:00','end':'20261006-12:15:00','timezone':'UTC'},self.request()),(1791288000,1791288900))

class EgressTests(unittest.TestCase):
    def guard(self):
        g=HistoryGuard(8984);g.check('71\x002\x008984\x00\x00',False,187)
        g.register(7800,{'kind':'bars','what':'MIDPOINT','end':'20261006 23:59:59 UTC'})
        return g
    def fields(self):
        return ['20','7800','12087792','EUR','CASH','','0.0','','','IDEALPRO','','USD','','','0','20261006 23:59:59 UTC','5 mins','30 D','0','MIDPOINT','2','0','']
    def wire(self,fields=None):return '\x00'.join(self.fields() if fields is None else fields)+'\x00'
    def test_real_modern_bar_layout_admitted(self):
        g=self.guard();g.check(self.wire(),True,187,0);self.assertEqual(g.issued,{7800:20})
    def test_foreign_request_denied(self):
        f=self.fields();f[1]='7900'
        with self.assertRaisesRegex(EvidenceError,'FOREIGN_HISTORY_ID'):self.guard().check(self.wire(f),True,187,0)
    def test_changed_contract_denied(self):
        f=self.fields();f[3]='GBP'
        with self.assertRaisesRegex(EvidenceError,'CONTRACT_SCOPE'):self.guard().check(self.wire(f),True,187,0)
    def test_keep_up_to_date_denied(self):
        f=self.fields();f[21]='1'
        with self.assertRaisesRegex(EvidenceError,'PAYLOAD'):self.guard().check(self.wire(f),True,187,0)
    def test_changed_duration_denied(self):
        f=self.fields();f[17]='1 Y'
        with self.assertRaisesRegex(EvidenceError,'PAYLOAD'):self.guard().check(self.wire(f),True,187,0)
    def test_direct_foreign_cancel_denied(self):
        with self.assertRaisesRegex(EvidenceError,'FOREIGN_HISTORY_ID'):self.guard().check('25\x001\x009000\x00',True,187)
    def test_unissued_cancel_denied(self):
        with self.assertRaisesRegex(EvidenceError,'UNISSUED'):self.guard().check('25\x001\x007800\x00',True,187)
    def test_owned_cancel_once(self):
        g=self.guard();g.check(self.wire(),True,187,0);g.check('25\x001\x007800\x00',True,187)
        with self.assertRaisesRegex(EvidenceError,'REPEATED'):g.check('25\x001\x007800\x00',True,187)
    def test_duplicate_send_denied_after_unknown_transport(self):
        g=self.guard();g.check(self.wire(),True,187,0)
        with self.assertRaisesRegex(EvidenceError,'REPEATED'):g.check(self.wire(),True,187,30)
    def test_live_and_order_message_denied(self):
        with self.assertRaisesRegex(EvidenceError,'PAPER'):self.guard().check(self.wire(),False,187)
        with self.assertRaisesRegex(EvidenceError,'NON_HISTORY'):self.guard().check('3\x001\x00',True,187)
    def test_wire_pacing_enforced(self):
        g=self.guard();g.check(self.wire(),True,187,0)
        g.register(7801,{'kind':'bars','what':'MIDPOINT','end':'20261006 23:59:59 UTC'})
        f=self.fields();f[1]='7801'
        with self.assertRaisesRegex(EvidenceError,'PACING'):g.check(self.wire(f),True,187,11.9)
        g.check(self.wire(f),True,187,12)
    def test_response_kind_cannot_substitute(self):
        g=self.guard();g.check(self.wire(),True,187,0)
        with self.assertRaisesRegex(EvidenceError,'UNBOUND'):g.check_response(7800,'schedule')
    def test_repeated_start_api_denied(self):
        with self.assertRaisesRegex(EvidenceError,'REPEAT'):self.guard().check('71\x002\x008984\x00\x00',False,187)
if __name__=='__main__':unittest.main()
