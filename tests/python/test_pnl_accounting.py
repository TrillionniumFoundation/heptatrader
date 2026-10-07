from pathlib import Path
import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
if not SCRIPTS.exists():
    SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import hepta_pnl_accounting as a
import hepta_pnl_report as r

START = 100000
NOW = 200000


def period():
    owner = {'agent_id': 'agent', 'session_id': 'session', 'broker_client_id': 701,
             'opening_quantity': '0', 'opening_mark': None}
    return {'schema': 'heptatrader.pnl-period.v1', 'period_id': 'reset-fixture',
            'kind': 'USER_RESET', 'started_at_ms': START, 'account': 'DU_TEST',
            'currency': 'USD', 'execution_domain': 'PAPER',
            'instruments': {name: dict(owner, session_id='session-' + name) for name in a.INSTRUMENTS}}


def send(cmd='cmd1', instrument='BTC.USD', direction='BUY', at=110000, oid=1):
    return {'schema_version': 4, 'event': 'place_sent', 'req_id': cmd, 'client_req_id': cmd,
            'ts_ms': at, 'account': 'DU_TEST', 'execution_domain': 'PAPER', 'instrument': instrument,
            'trace_id': 'session-' + instrument, 'source': 'agent.tool:agent', 'side': direction,
            'order_id': oid, 'venue_correlation_id': 'hepta-v1-sha256:' + hashlib.sha256(cmd.encode()).hexdigest()}


def fill(sent, eid='e1.1', qty=1, price=100, cumulative=None, average=None, at=None):
    inst = sent['instrument']; subject = ('BTC', 'CRYPTO') if inst == 'BTC.USD' else ('EUR', 'CASH')
    identity = 'HIBE1:' + ''.join(str(len(x)) + ':' + x for x in (subject[0], subject[1], 'USD', '', '', '', '', '')) + ':0'
    out = dict(sent, event='broker_execution', broker_callback_type='execDetails',
               source='agent:agent', ts_ms=at or sent['ts_ms'] + 10, broker_execution_id=eid,
               broker_service_epoch='service-1', broker_connection_epoch=3, broker_client_id=701,
               broker_request_id=-1, qty=qty if cumulative is None else cumulative,
               price=price if average is None else average, status='ExecutionDetails')
    out.update(broker_econ_version=1, broker_econ_valid=1, broker_econ_kind='EXECUTION',
               broker_econ_shares=qty, broker_econ_execution_price=price,
               broker_econ_cumulative_quantity=out['qty'], broker_econ_cumulative_average=out['price'],
               broker_econ_contract_id=123, broker_econ_permanent_order_id=456 + sent['order_id'],
               broker_econ_contract_identity=identity, broker_econ_price_revision_pending=0)
    return out


def commission(f, value=1, currency='USD'):
    return {'schema_version': 4, 'event': 'broker_commission', 'broker_callback_type': 'commissionReport',
            'broker_execution_id': f['broker_execution_id'], 'broker_service_epoch': f['broker_service_epoch'],
            'broker_connection_epoch': f['broker_connection_epoch'], 'ts_ms': f['ts_ms'] + 1,
            'account': '', 'order_id': -1, 'broker_econ_version': 1, 'broker_econ_kind': 'COMMISSION',
            'broker_econ_valid': 1, 'broker_econ_commission': value, 'broker_econ_commission_currency': currency}


def terminal(f, status='Filled'):
    return dict(f, event='broker_order_status', broker_callback_type='orderStatus',
                broker_execution_id='', status=status, ts_ms=f['ts_ms'] + 2)


def observation(quantity=1, bid=90, ask=92, instrument='BTC.USD'):
    return {'authoritative': True, 'sampled_at_ms': NOW - 100, 'quantity': quantity, 'connection_epoch': 3,
            'quote': {'source': 'IB', 'authoritative': True, 'instrument': instrument,
                      'stale': False, 'observed_at_ms': NOW - 100, 'stale_after_ms': NOW + 4000,
                      'bid': bid, 'ask': ask, 'subscription_id': 'IB:3:2:100001'}}


def basic():
    s = send(); f = fill(s)
    return [s, f, terminal(f), commission(f)]


class AccountingTests(unittest.TestCase):
    def row(self, events=None, obs=None, p=None, driver=None):
        return a.instrument_report(basic() if events is None else events, period() if p is None else p,
                                   'BTC.USD', observation() if obs is None else obs, NOW, driver)

    def test_open_loss_not_hidden_by_positive_realized(self):
        rows = basic()
        s = send('close1', direction='SELL', at=120000, oid=2); f = fill(s, 'e2.1', price=110)
        rows += [s, f, terminal(f), commission(f)]
        s = send('open2', at=130000, oid=3); f = fill(s, 'e3.1', price=100)
        rows += [s, f, terminal(f), commission(f)]
        result = self.row(rows, observation(bid=80))
        self.assertEqual(result['realized_gross_pnl'], '10')
        self.assertEqual(result['unrealized_gross_pnl'], '-20')
        self.assertEqual(result['gross_total_pnl'], '-10')
        self.assertEqual(result['net_total_pnl'], '-13')
        self.assertEqual(result['realized_net_pnl'], '8')
        self.assertEqual(result['unrealized_net_pnl'], '-21')

    def test_long_marks_bid_short_marks_ask(self):
        self.assertEqual(self.row()['mark_price'], '90')
        s = send(direction='SELL'); f = fill(s)
        result = self.row([s, f, terminal(f), commission(f)], observation(-1, 89, 92))
        self.assertEqual(result['mark_source'], 'IB_ASK')
        self.assertEqual(result['unrealized_gross_pnl'], '8')

    def test_missing_fee_leaves_net_unknown_not_zero(self):
        result = self.row(basic()[:-1])
        self.assertEqual(result['gross_total_pnl'], '-10')
        self.assertFalse(result['fees_complete'])
        for name in ('fees_paid', 'realized_net_pnl', 'unrealized_net_pnl', 'net_total_pnl'):
            self.assertIsNone(result[name])

    def test_explicit_zero_fee_and_rebate_are_valid(self):
        rows = basic(); rows[-1]['broker_econ_commission'] = 0
        self.assertEqual(self.row(rows)['net_total_pnl'], '-10')
        rows[-1]['broker_econ_commission'] = -1
        self.assertEqual(self.row(rows)['net_total_pnl'], '-9')

    def test_duplicate_execution_and_fees_are_idempotent(self):
        self.assertEqual(self.row(basic() * 5), self.row())

    def test_reversed_and_delayed_receipts_are_idempotent(self):
        self.assertEqual(self.row(list(reversed(basic()))), self.row())

    def test_multiple_cumulative_points_are_not_added(self):
        s = send(); f1 = fill(s, 'exec1.1', .1, 100)
        f2 = fill(s, 'exec2.1', .2, 103, cumulative=.3, average=102, at=s['ts_ms'] + 20)
        result = self.row([s, f2, f1, terminal(f2, 'Cancelled'), commission(f1), commission(f2)], observation(.3))
        self.assertEqual(result['position'], '0.3')
        self.assertEqual(result['execution_count'], 2)
        self.assertEqual(result['entry_cost_basis'], '102')
        self.assertEqual(result['gross_total_pnl'], '-3.6')

    def test_individual_share_gap_rejected(self):
        rows = basic(); rows[1]['broker_econ_shares'] = .5
        result = self.row(rows)
        self.assertFalse(result['ledger_complete'])
        self.assertIn('EXECUTION_SHARES_GAP', result['reason_codes'])

    def test_legacy_cumulative_only_differences(self):
        s = send(); f1 = fill(s, 'e1.1', .1, 100); f2 = fill(s, 'e2.1', .2, 103, .3, 102)
        f1 = {k: v for k, v in f1.items() if not k.startswith('broker_econ')}
        f2 = {k: v for k, v in f2.items() if not k.startswith('broker_econ')}
        result = self.row([s, f1, f2, terminal(f2)], observation(.3))
        self.assertEqual(result['gross_total_pnl'], '-3.6')
        self.assertIsNone(result['net_total_pnl'])

    def test_correction_or_execution_replay_conflict_not_counted_twice(self):
        for variant in ('price', 'quantity', 'correction'):
            rows = basic(); changed = dict(rows[1])
            if variant == 'price': changed['price'] = 101
            elif variant == 'quantity': changed['qty'] = 2
            else: changed['broker_execution_id'] = 'e1.2'
            result = self.row(rows + [changed])
            self.assertFalse(result['ledger_complete'])
            self.assertIsNone(result['gross_total_pnl'])

    def test_pending_revision_and_bad_contract_are_blocked(self):
        for key, value in [('broker_econ_price_revision_pending', 1), ('broker_econ_valid', True),
                           ('broker_econ_contract_id', 0), ('broker_econ_contract_identity', 'HIBE1:broken')]:
            rows = basic(); rows[1][key] = value
            self.assertFalse(self.row(rows)['ledger_complete'])

    def test_foreign_binding_cannot_supply_economics(self):
        for key, value in [('account', 'DU_OTHER'), ('broker_client_id', 702), ('trace_id', 'other'),
                           ('execution_domain', 'LIVE'), ('order_id', 2), ('req_id', 'another')]:
            rows = basic(); rows[1][key] = value
            with self.subTest(key=key):
                self.assertFalse(self.row(rows)['ledger_complete'])

    def test_fee_must_bind_execution_and_native_epoch(self):
        for key, value in [('broker_service_epoch', 'other'), ('broker_connection_epoch', 0), ('account', 'DU_OTHER'),
                           ('broker_econ_valid', 0), ('broker_econ_commission', 1.7976931348623157e308)]:
            rows = basic(); rows[-1][key] = value
            with self.subTest(key=key):
                result = self.row(rows)
                self.assertFalse(result['fees_complete'])
                self.assertIsNone(result['net_total_pnl'])

    def test_fee_conflict_and_non_usd_are_not_silently_converted(self):
        rows = basic(); rows.append(dict(rows[-1], broker_econ_commission=2))
        self.assertFalse(self.row(rows)['fees_complete'])
        rows = basic(); rows[-1]['broker_econ_commission_currency'] = 'EUR'
        result = self.row(rows)
        self.assertEqual(result['fees_known_by_currency'], {'EUR': '1'})
        self.assertIsNone(result['net_total_pnl'])

    def test_partial_closes_allocate_entry_fees(self):
        s = send(); f = fill(s, qty=2); rows = [s, f, terminal(f), commission(f, 2)]
        s = send('exit', direction='SELL', at=120000, oid=2); f = fill(s, 'e2.1', price=110)
        rows += [s, f, terminal(f), commission(f, 1)]
        result = self.row(rows, observation(1, 95, 96))
        self.assertEqual(result['realized_gross_pnl'], '10')
        self.assertEqual(result['realized_net_pnl'], '8.0')
        self.assertEqual(result['unrealized_net_pnl'], '-6.0')
        self.assertEqual(result['net_total_pnl'], '2')

    def test_reversal_carries_only_opening_fraction_fee(self):
        rows = basic(); s = send('flip', direction='SELL', at=120000, oid=2); f = fill(s, 'e2.1', qty=2, price=110)
        rows += [s, f, terminal(f), commission(f, 2)]
        result = self.row(rows, observation(-1, 99, 100))
        self.assertEqual(result['realized_gross_pnl'], '10')
        self.assertEqual(result['unrealized_gross_pnl'], '10')
        self.assertEqual(result['remaining_entry_fees'], '1')
        self.assertEqual(result['net_total_pnl'], '17')

    def test_zero_fill_status_is_not_an_execution(self):
        s = send(); t = terminal(fill(s), 'Cancelled'); t['qty'] = t['price'] = 0
        result = self.row([s, t], observation(0))
        self.assertEqual(result['gross_total_pnl'], '0')
        self.assertEqual(result['execution_count'], 0)
        t['status'] = 'Filled'
        self.assertFalse(self.row([s, t], observation(0))['ledger_complete'])

    def test_accepted_or_absent_command_not_assumed_zero_fill(self):
        result = self.row([send()], observation(0))
        self.assertFalse(result['ledger_complete'])
        self.assertIsNone(result['gross_total_pnl'])

    def test_fills_can_be_valued_before_terminal_settlement(self):
        rows = basic(); rows.pop(2)
        driver = {'position': 0, 'gross_pnl': 0, 'pending': {'command_id': 'cmd1'}}
        result = self.row(rows, driver=driver)
        self.assertEqual(result['settlement_pending_command_ids'], ['cmd1'])
        self.assertEqual(result['gross_total_pnl'], '-10')
        self.assertIn('DRIVER_POSITION_LAGS_BROKER_EXECUTIONS', result['reason_codes'])
        self.assertEqual(driver['pending']['command_id'], 'cmd1')

    def test_stale_missing_future_or_crossed_marks_never_give_total(self):
        for key, value in [('observed_at_ms', NOW + 1), ('stale_after_ms', NOW), ('stale', True),
                           ('bid', 0), ('ask', 80), ('authoritative', False), ('instrument', 'EUR.USD'),
                           ('subscription_id', 'IB:4:2:100001')]:
            obs = observation(); obs['quote'][key] = value
            result = self.row(obs=obs)
            self.assertIsNone(result['unrealized_gross_pnl'])
            self.assertIsNone(result['gross_total_pnl'])
            self.assertEqual(result['realized_gross_pnl'], '0')

    def test_unproven_empty_position_does_not_mean_zero(self):
        result = self.row(obs={'authoritative': False, 'quantity': 0})
        self.assertFalse(result['valuation_complete'])
        self.assertIsNone(result['unrealized_gross_pnl'])

    def test_position_mismatch_blocks_total_not_historical_realized(self):
        result = self.row(obs=observation(2))
        self.assertIn('BROKER_LEDGER_POSITION_MISMATCH', result['reason_codes'])
        self.assertIsNone(result['gross_total_pnl'])
        self.assertEqual(result['realized_gross_pnl'], '0')

    def test_flat_needs_no_quote_but_still_needs_authority(self):
        result = self.row([], {'authoritative': True, 'sampled_at_ms': NOW, 'quantity': 0})
        self.assertEqual(result['unrealized_gross_pnl'], '0')
        self.assertTrue(result['valuation_complete'])

    def test_reset_carry_uses_opening_mark_not_old_purchase_or_cost(self):
        p = period(); p['instruments']['BTC.USD'].update(opening_quantity='1', opening_mark='90',
            opening_mark_source='IB_MID_AT_OPERATOR_RESET', opening_mark_observed_at_ms=START - 100,
            opening_mark_valid_until_ms=START + 100, historical_purchase_fill_price='100', historical_broker_average_cost='101')
        old = send('old', at=START - 2000); old_fill = fill(old, 'old1.1', price=100)
        s = send('close', direction='SELL'); f = fill(s, 'close1.1', price=92)
        result = self.row([old, old_fill, s, f, terminal(f), commission(f)], observation(0), p)
        self.assertEqual(result['realized_gross_pnl'], '2')
        self.assertEqual(result['net_total_pnl'], '1')

    def test_historical_replay_time_does_not_change_accounting_period(self):
        old = send('old', at=START - 2000); f = fill(old, 'old1.1', at=NOW - 10); f['broker_request_id'] = 18
        result = self.row([old, f], observation(0))
        self.assertEqual(result['gross_total_pnl'], '0')
        f['broker_request_id'] = -1
        self.assertFalse(self.row([old, f], observation(0))['ledger_complete'])

    def test_completed_execution_redownload_requires_preperiod_execution_identity(self):
        old = fill(send('historical', at=START - 3000), 'old-exec.1', at=START - 2000)
        old.update(source='ib-api-callback', trace_id='', req_id='', client_req_id='', venue_correlation_id='',
                   broker_request_id=18)
        replayed = dict(old, ts_ms=NOW - 10, broker_service_epoch='service-2', broker_connection_epoch=4)
        result = self.row([old, replayed], observation(0))
        self.assertTrue(result['ledger_complete'])
        self.assertEqual(result['gross_total_pnl'], '0')

        unseen = dict(replayed, broker_execution_id='new-after-boundary.1')
        self.assertFalse(self.row([old, unseen], observation(0))['ledger_complete'])

        live = dict(replayed, broker_request_id=-1)
        self.assertFalse(self.row([old, live], observation(0))['ledger_complete'])

    def test_preperiod_execution_identity_must_be_exact_callback_scope(self):
        old = fill(send('historical', at=START - 3000), 'old-exec.2', at=START - 2000)
        old.update(source='agent:agent', trace_id='', req_id='', client_req_id='', venue_correlation_id='',
                   broker_request_id=18)
        replayed = dict(old, source='ib-api-callback', ts_ms=NOW - 10)
        self.assertFalse(self.row([old, replayed], observation(0))['ledger_complete'])

    def test_manual_cash_conversion_does_not_enter_strategy_book(self):
        s = send('manual', instrument='EUR.USD'); s['trace_id'] = ''; s['source'] = 'ib-api-callback'
        f = fill(s, 'manual1.1'); f['broker_client_id'] = 8813
        result = self.row(basic() + [s, f, commission(f, 20)])
        self.assertEqual(result['fees_paid'], '1')
        self.assertEqual(result['gross_total_pnl'], '-10')

    def test_aggregate_unknown_component_not_sum_of_available_subset(self):
        p = period(); obs = {'BTC.USD': observation(), 'EUR.USD': {'authoritative': False, 'quantity': 0}}
        report = a.build_report(basic(), p, obs, NOW)
        self.assertIsNone(report['totals']['gross_total_pnl'])
        self.assertIsNone(report['totals']['net_total_pnl'])
        self.assertIsNone(report['account_nav'])
        self.assertFalse(report['mutation_authorized'])

    def test_inputs_are_not_mutated_and_replay_is_repeatable(self):
        events = basic(); obs = observation(); p = period(); before = copy.deepcopy((events, obs, p))
        self.assertEqual(self.row(events, obs, p), self.row(events, obs, p))
        self.assertEqual(before, (events, obs, p))

    def test_nan_infinity_bool_and_missing_numbers_rejected(self):
        for v in [None, True, False, 'nan', 'Infinity', 1.7976931348623157e308, float('nan')]:
            with self.assertRaises(a.EvidenceError): a.number(v)

    def test_terminal_total_or_average_conflict_not_erased(self):
        for key, val in [('qty', 2), ('price', 101)]:
            rows = basic(); rows[2][key] = val
            self.assertFalse(self.row(rows)['ledger_complete'])

    def test_incomplete_intent_is_not_ignored(self):
        s = send(); s['event'] = 'order_intent'
        self.assertFalse(self.row([s], observation(0))['ledger_complete'])

    def test_explicit_reliable_place_reject_proves_zero_send(self):
        intent = send('rejected-before-broker'); intent.update(
            event='order_intent', order_id=-1, status='intent_recorded')
        rejected = dict(intent, event='reject', status='rejected',
                        risk_code='IB_PLACE_REJECT',
                        reason='IB_CRYPTO_EXPOSURE_LIMIT_EXCEEDED')
        result = self.row([intent, rejected], observation(0))
        self.assertTrue(result['ledger_complete'])
        self.assertTrue(result['valuation_complete'])
        self.assertEqual(result['execution_count'], 0)
        self.assertEqual(result['gross_total_pnl'], '0')

    def test_uncertain_or_unqualified_reject_never_proves_zero_send(self):
        for variant in ('uncertain', 'wrong_code', 'empty_reason'):
            intent = send('ambiguous-'+variant); intent.update(
                event='order_intent', order_id=-1, status='intent_recorded')
            if variant == 'uncertain':
                outcome = dict(intent, event='place_outcome_uncertain',
                               status='uncertain',
                               risk_code='IB_PLACE_OUTCOME_UNCERTAIN',
                               reason='transport outcome unknown')
            else:
                outcome = dict(intent, event='reject', status='rejected',
                               risk_code='OTHER_REJECT' if variant == 'wrong_code' else 'IB_PLACE_REJECT',
                               reason='' if variant == 'empty_reason' else 'not coordinator reliable reject')
            with self.subTest(variant=variant):
                result = self.row([intent, outcome], observation(0))
                self.assertFalse(result['ledger_complete'])
                self.assertIn('ORIGINAL_SEND_OUTCOME_UNPROVEN', result['reason_codes'])

    def test_reliable_reject_must_match_original_durable_binding(self):
        intent = send('reject-binding'); intent.update(
            event='order_intent', order_id=-1, status='intent_recorded')
        rejected = dict(intent, event='reject', status='rejected',
                        risk_code='IB_PLACE_REJECT', reason='explicit reliable rejection',
                        venue_correlation_id='hepta-v1-sha256:' + 'b'*64)
        result = self.row([intent, rejected], observation(0))
        self.assertFalse(result['ledger_complete'])
        self.assertIn('ORIGINAL_REJECT_BINDING_CONFLICT', result['reason_codes'])

    def test_same_command_cannot_be_both_sent_and_reliably_rejected(self):
        sent = send('send-reject-conflict')
        intent = dict(sent, event='order_intent', order_id=-1, status='intent_recorded')
        rejected = dict(intent, event='reject', status='rejected',
                        risk_code='IB_PLACE_REJECT', reason='explicit reliable rejection')
        result = self.row([intent, sent, rejected], observation(1))
        self.assertFalse(result['ledger_complete'])
        self.assertIn('ORIGINAL_SEND_AND_REJECT_CONFLICT', result['reason_codes'])

    def test_cross_subject_same_execution_id_collision_rejected(self):
        rows = basic(); foreign = dict(rows[1], account='DU_OTHER', venue_correlation_id='foreign')
        self.assertFalse(self.row(rows + [foreign])['ledger_complete'])

    def test_legacy_realized_field_cannot_be_rendered_as_total(self):
        report = a.build_report(basic(), period(), {'BTC.USD': observation(), 'EUR.USD': observation(0, instrument='EUR.USD')}, NOW)
        text = r.render(report)
        self.assertIn('TOTAL gross=-10.00', text)
        self.assertIn('NOT account NAV', text)


class ReadOnlyProducerTests(unittest.TestCase):
    def test_native_tool_allowlist_blocks_trading_before_subprocess(self):
        reader = r.NativeReader(Path('/prefix'), Path('/config'), Path('/sock'))
        with patch.object(r.subprocess, 'run') as run:
            for tool in ['trade.place_order', 'trade.flatten_position', 'trade.cancel_order', 'risk.preview_order', 'execution.reconcile']:
                with self.assertRaises(a.EvidenceError): reader('BTC.USD', tool)
            run.assert_not_called()

    def test_atomic_output_and_private_input_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'snapshot.json'
            r.write_snapshot(path, {'a': 1})
            self.assertEqual(r.load_private(path), {'a': 1})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            r.write_snapshot(path, {'a': 2})
            self.assertEqual(r.load_private(path), {'a': 2})

    def test_period_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'period.json'; r.write_snapshot(path, period(), exclusive=True)
            before = path.read_bytes()
            with self.assertRaises(a.EvidenceError): r.write_snapshot(path, {'period_id': 'new'}, exclusive=True)
            self.assertEqual(path.read_bytes(), before)

    def test_symlink_and_shared_input_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'state'; p.write_text('{}'); p.chmod(0o644)
            with self.assertRaises(a.EvidenceError): r.read_private(p)
            p.chmod(0o600); link = Path(temp) / 'link'; link.symlink_to(p)
            with self.assertRaises(OSError): r.read_private(link)

    def test_duplicate_keys_refused(self):
        with self.assertRaises(a.EvidenceError): r.strict_json('{"x":1,"x":2}')

    def test_health_missing_or_boolean_generation_not_authoritative(self):
        h = {'execution_mode': 'PAPER', 'gateway_ready': True, 'remote_execution_ready': True,
             'execution_service_epoch': 'epoch', 'execution_service_fencing_generation': True,
             'execution_authoritative_health': {'source': 'IB', 'authoritative': True}}
        with self.assertRaises(a.EvidenceError): r.health_identity(h)

    def test_cached_old_period_and_stale_never_show_positive_total(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); path = root/'period'; r.write_snapshot(path, period())
            for name in ('paper-driver', 'crypto-driver'):
                r.write_snapshot(root/name/'state.json', {'day_one_started_at_ms': START})
            report = {'schema':'heptatrader.pnl.v1','period_id':'reset-fixture', 'complete':True,
                      'period_sha256':hashlib.sha256(path.read_bytes()).hexdigest(), 'observed_at_ms':100,
                      'valid_until_ms':200, 'totals':{'gross_total_pnl':'100'}}
            output=root/'snapshot';r.write_snapshot(output, report)
            with patch.object(r, 'now_ms', lambda:300):
                value=r.cached(output,path,root)
                self.assertEqual(value['reason_codes'],['CACHED_PNL_STALE'])
                self.assertEqual(value['totals'],{})
            r.write_snapshot(root/'crypto-driver/state.json',{'day_one_started_at_ms':START+1})
            with patch.object(r, 'now_ms', lambda:150):
                self.assertEqual(r.cached(output,path,root)['reason_codes'],['DRIVER_ACCOUNTING_PERIOD_CHANGED'])




class CollectionTests(unittest.TestCase):
    def native(self, instrument, tool):
        h={'execution_mode':'PAPER','gateway_ready':True,'remote_execution_ready':True,
           'execution_service_epoch':'service-1','execution_service_fencing_generation':1,'tool_gateway_epoch':'gateway-1',
           'execution_authoritative_health':{'source':'IB','authoritative':True}}
        owner=period()['instruments'][instrument]
        orders={'source':'IB','authoritative':True,'global_active_orders_complete':True,
                'owner_projection_complete':True,'active_orders_connection_epoch':3,'active_orders_generation':1,
                'owner_scope':{'account':'DU_TEST','execution_domain':'PAPER','agent_id':owner['agent_id'],'session_id':owner['session_id']},
                'active_order_ids':[],'recent_orders':[]}
        positions={'source':'IB','authoritative':True,'position_scope':'PAPER_BASELINE_DELTA',
                   'position_generation':1,'fx_cash_generation':1,'positions':[{'instrument':'BTC.USD','quantity':1}]}
        return {'system.get_health':h,'orders.list':orders,'portfolio.list_positions':positions,
                'market.get_quote':observation(instrument=instrument)['quote']}[tool]

    def setup_files(self, root):
        r.write_snapshot(root/'period.json',period())
        (root/'execution').mkdir(mode=0o700)
        journal=root/'execution/oms-journal.jsonl'
        journal.write_text(''.join(json.dumps(e)+'\n' for e in basic()));journal.chmod(0o600)
        for name,inst in [('paper-driver','EUR.USD'),('crypto-driver','BTC.USD')]:
            r.write_snapshot(root/name/'state.json',{'day_one_started_at_ms':START,'instrument':inst,
                                                   'gross_pnl':0,'position':0 if inst=='EUR.USD' else 1})

    def test_fresh_collection_is_read_only_and_amounts_are_separate(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(r,'now_ms',lambda:NOW):
            root=Path(tmp);self.setup_files(root)
            before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            report=r.collect(self.native,root/'period.json',root)
            self.assertEqual(report['totals']['gross_total_pnl'],'-10')
            self.assertEqual(report['totals']['net_total_pnl'],'-11')
            self.assertEqual(before,{str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_restart_between_native_reads_cannot_form_snapshot(self):
        count=0
        def native(i,t):
            nonlocal count
            v=self.native(i,t)
            if t=='system.get_health':
                count+=1
                if count>1:v['execution_service_epoch']='new-service'
            return v
        with patch.object(r,'now_ms',lambda:NOW),self.assertRaises(a.EvidenceError):
            r.observe(native,period(),'BTC.USD')

    def test_owner_and_position_generation_changes_refused(self):
        for change in ['owner','position','empty']:
            n=0
            def native(i,t):
                nonlocal n
                v=self.native(i,t)
                if change=='owner' and t=='orders.list':v['owner_scope']['account']='DU_OTHER'
                if t=='portfolio.list_positions':
                    n+=1
                    if change=='position' and n==2:v['position_generation']+=1
                    if change=='empty':v['authoritative']=False;v['positions']=[]
                return v
            with self.subTest(change=change),patch.object(r,'now_ms',lambda:NOW),self.assertRaises(a.EvidenceError):
                r.observe(native,period(),'BTC.USD')

    def test_journal_incomplete_tail_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.setup_files(root)
            p=root/'execution/oms-journal.jsonl';p.write_bytes(p.read_bytes()[:-1])
            with self.assertRaises(a.EvidenceError):r.collect(self.native,root/'period.json',root)

    def test_ledger_changed_during_native_reads_refused(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(r,'now_ms',lambda:NOW):
            root=Path(tmp);self.setup_files(root);changed=False
            def native(i,t):
                nonlocal changed
                if not changed:
                    with (root/'execution/oms-journal.jsonl').open('a') as f:f.write(json.dumps(basic()[-1])+'\n')
                    changed=True
                return self.native(i,t)
            with self.assertRaises(a.EvidenceError):r.collect(native,root/'period.json',root)

    def test_calendar_day_is_not_silently_rolled_or_reset(self):
        p=period();rpt=a.build_report([],p,{i:observation(0,instrument=i) for i in a.INSTRUMENTS},NOW)
        self.assertEqual(rpt['period_kind'],'USER_RESET')
        self.assertEqual(rpt['period_started_at_ms'],START)
        self.assertNotIn('daily_pnl',rpt['totals'])



class CacheAndBoundTests(unittest.TestCase):
    setup_files = CollectionTests.setup_files
    native = CollectionTests.native
    def test_fresh_cache_schema_and_journal_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(r,'now_ms',lambda:NOW):
            root=Path(tmp); self.setup_files(root)
            report=r.collect(self.native,root/'period.json',root)
            output=root/'snapshot.json';r.write_snapshot(output,report)
            self.assertTrue(r.cached(output,root/'period.json',root)['complete'])
            wrong=copy.deepcopy(report);wrong['schema']='other'
            r.write_snapshot(output,wrong)
            self.assertEqual(r.cached(output,root/'period.json',root)['reason_codes'],['CACHED_PNL_SCHEMA_INVALID'])
            r.write_snapshot(output,report)
            with (root/'execution/oms-journal.jsonl').open('a') as f:f.write(json.dumps(basic()[-1])+'\n')
            value=r.cached(output,root/'period.json',root)
            self.assertEqual(value['reason_codes'],['CACHED_PNL_LEDGER_CHANGED'])
            self.assertEqual(value['totals'],{})

    def test_full_accounting_kept_when_display_tail_is_bounded(self):
        rows=[]
        for n in range(140):
            s=send('bound-'+str(n), direction='BUY' if n%2==0 else 'SELL',at=110000+n*10,oid=n+1)
            f=fill(s,'bounded.'+str(n)+'.01.01',price=100 if n%2==0 else 101)
            rows.extend([s,f,terminal(f),commission(f)])
        result=a.instrument_report(rows,period(),'BTC.USD',observation(0),NOW)
        self.assertTrue(result['valuation_complete'])
        self.assertEqual(result['realized_gross_pnl'],'70')
        self.assertEqual(result['net_total_pnl'],'-70')
        self.assertEqual(result['execution_count'],140)
        self.assertEqual(result['execution_details_total'],140)
        self.assertTrue(result['execution_details_truncated'])
        self.assertEqual(len(result['executions']),128)

if __name__ == '__main__':
    unittest.main()
