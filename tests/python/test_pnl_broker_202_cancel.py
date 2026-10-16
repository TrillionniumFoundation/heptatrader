"""Strict original Broker error 202 cancellation of an ALREADY executed order.

A 202 only ends the unfilled remainder. It never manufactures executions,
commissions, zero-fill proof or a terminal orderStatus callback.
"""
import unittest
from test_pnl_accounting import send,fill,commission,period,NOW
import hepta_pnl_accounting as a


def order_cancel_error(f,**changes):
    fields=('req_id','client_req_id','account','execution_domain','instrument','trace_id',
            'side','order_id','venue_correlation_id','broker_client_id',
            'broker_service_epoch','broker_connection_epoch')
    e={k:f[k] for k in fields if k in f}
    e.update(event='broker_error',source='agent:agent',broker_callback_type='error',
             risk_code='IB_ERROR_202',status='Cancelled',ts_ms=f['ts_ms']+3,
             # The original native TWS error() callback carries (-1, 0)
             # sentinels, NOT the Broker account owner client ID.
             broker_client_id=-1,broker_request_id=0,broker_error_code=202,
             qty=0,price=0,broker_execution_id='')
    e.update(changes)
    return e


class BrokerError202SettlementTests(unittest.TestCase):
    def setup_rows(self):
        s=send();f=fill(s,eid='orig-fill',qty=.4)
        return s,f,[s,f,commission(f)]

    def ledger(self,events):
        return a.replay(events,period(),'BTC.USD',NOW)

    def test_original_202_cancelled_remainder_matures_one_partial_execution(self):
        s,f,rows=self.setup_rows()
        original=self.ledger(rows)
        self.assertEqual(original['settlement_pending_command_ids'],[s['req_id']])
        complete=self.ledger(rows+[order_cancel_error(f)])
        self.assertEqual(complete['settlement_pending_command_ids'],[])
        self.assertEqual(complete['execution_count'],1)
        self.assertEqual(complete['book'].quantity,a.Decimal('.4'))
        self.assertEqual(complete['fees_complete'],True)
        self.assertEqual(complete['broker_cancel_witnesses_total'],1)
        self.assertEqual(complete['broker_cancel_witnesses'][0]['basis'],
                         'ORIGINAL_IB_API_ERROR_202_CANCELLED_UNFILLED_REMAINDER')
        self.assertNotEqual(complete['broker_cancel_witnesses'][0]['basis'],'orderStatus')

    def test_repeating_identical_202_is_idempotent(self):
        _,f,rows=self.setup_rows();error=order_cancel_error(f)
        self.assertEqual(self.ledger(rows+[error]),self.ledger(rows+[error,error]))

    def test_broker_error_202_not_a_zero_fill_for_unsent_or_unexecuted(self):
        s=send();f=fill(s)
        with self.assertRaisesRegex(a.EvidenceError,
                'SUBMITTED_COMMAND_WITHOUT_FILL_OR_ZERO_FILL_PROOF'):
            self.ledger([s,order_cancel_error(f)])
        # A foreign/unbound cancellation cannot invent a strategy command.
        empty=self.ledger([order_cancel_error(f)])
        self.assertEqual(empty['execution_count'],0)
        self.assertEqual(empty.get('broker_cancel_witnesses_total',0),0)

    def test_false_cancel_codes_or_non_cancel_status_never_close(self):
        _,f,rows=self.setup_rows()
        for variant in ({'risk_code':'IB_ERROR_201'},{'status':'Submitted'},
                        {'broker_callback_type':'orderStatus'},{'source':'agent.tool:agent'}):
            with self.subTest(variant=variant):
                try:out=self.ledger(rows+[order_cancel_error(f,**variant)])
                except a.EvidenceError:continue
                self.assertEqual(len(out['settlement_pending_command_ids']),1)
                self.assertEqual(out.get('broker_cancel_witnesses_total',0),0)

    def test_foreign_scope_never_supplies_terminal(self):
        _,f,rows=self.setup_rows()
        variants={'account':'DU_FOREIGN','execution_domain':'LIVE','instrument':'EUR.USD',
                  'trace_id':'other','side':'SELL','order_id':111,'broker_client_id':702,
                  'venue_correlation_id':'hepta-v1-sha256:'+'b'*64,
                  'req_id':'foreign','client_req_id':'foreign',
                  'broker_client_id':702}
        for key,value in variants.items():
            with self.subTest(key=key):
                try:out=self.ledger(rows+[order_cancel_error(f,**{key:value})])
                except a.EvidenceError:continue
                self.assertEqual(len(out['settlement_pending_command_ids']),1)

    def test_mismatched_epoch_or_time_never_borrows_fill_identity(self):
        _,f,rows=self.setup_rows()
        for variant in ({'broker_service_epoch':'other'},{'broker_connection_epoch':4},
                        {'ts_ms':f['ts_ms']-25},{'ts_ms':NOW+1},
                        {'broker_connection_epoch':0}):
            with self.subTest(variant=variant):
                try:out=self.ledger(rows+[order_cancel_error(f,**variant)])
                except a.EvidenceError:continue
                self.assertEqual(len(out['settlement_pending_command_ids']),1)

    def test_native_error_callback_numeric_sentinels_must_be_exact(self):
        _,f,rows=self.setup_rows()
        variants={'broker_client_id':701,'broker_request_id':-1,
                  'broker_error_code':201}
        for key,value in variants.items():
            with self.subTest(key=key):
                try:out=self.ledger(rows+[order_cancel_error(f,**{key:value})])
                except a.EvidenceError:continue
                self.assertEqual(out['settlement_pending_command_ids'],['cmd1'])

    def test_orderstatus_terminal_is_not_replaced_by_error_witness(self):
        from test_pnl_accounting import terminal
        _,f,rows=self.setup_rows()
        out=self.ledger(rows+[terminal(f,'Cancelled'),order_cancel_error(f)])
        self.assertEqual(out['settlement_pending_command_ids'],[])
        self.assertEqual(out.get('broker_cancel_witnesses_total',0),0)

    def test_post_cancel_active_status_does_not_create_false_terminal(self):
        from test_pnl_accounting import terminal
        _,f,rows=self.setup_rows();error=order_cancel_error(f)
        late=terminal(f,'Submitted');late['ts_ms']=error['ts_ms']+1
        out=self.ledger(rows+[error,late])
        self.assertEqual(len(out['settlement_pending_command_ids']),1)


if __name__ == '__main__':unittest.main()
