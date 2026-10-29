"""An explicit account zero values a disabled lane without minting owner scope."""
import json
from decimal import Decimal, localcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import hepta_pnl_report as report
import test_pnl_accounting as fixture


class AccountFlatCryptoTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        fixture.CollectionTests.setup_files(self, self.root)
        rows = fixture.basic()
        sent = fixture.send('close', direction='SELL', at=120000, oid=2)
        fill = fixture.fill(sent, 'close.1', price=110)
        self.rows = rows + [sent, fill, fixture.terminal(fill), fixture.commission(fill)]
        self.write_journal(self.rows)
        self.state = {'day_one_started_at_ms': fixture.START, 'instrument': 'BTC.USD',
                      'gross_pnl': 10, 'position': 0, 'pending': None, 'lease_uncertain': False}
        self.write_state(self.state)
        self.clock = fixture.NOW
        self.owner_error = 'DISCOVERY_LIST_REJECTED'
        self.calls = []
        self.mutate = lambda instrument, tool, value: None
        self.on_owner_read = lambda: None
        self.patcher = patch.object(report, 'now_ms', side_effect=lambda: self.clock)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def write_journal(self, rows):
        path = self.root / 'execution/oms-journal.jsonl'
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))

    def write_state(self, state):
        report.write_snapshot(self.root / 'crypto-driver/state.json', state)

    def native(self, instrument, tool):
        self.calls.append((instrument, tool))
        if instrument == 'BTC.USD':
            self.on_owner_read()
            raise report.EvidenceError(self.owner_error)
        value = fixture.CollectionTests.native(self, instrument, tool)
        if tool == 'orders.list':
            value['active_orders_source'] = 'IB_OPEN_ORDERS'
        if tool == 'portfolio.list_positions':
            value['positions'] = []
            value['configured_crypto_position_observation'] = {
                'source': 'IB_REQ_POSITIONS', 'authoritative': True,
                'scope': 'CONFIGURED_PHYSICAL_CRYPTO', 'mutation_authorized': False,
                'account': 'DU_TEST', 'execution_service_epoch': 'service-1',
                'connection_epoch': 3, 'position_generation': 1,
                'active_orders_generation': 1, 'terminal_generation': 2,
                'sampled_at_ms': fixture.NOW - 100,
                'positions': [{'instrument': 'BTC.USD', 'quantity': 0}]}
        self.mutate(instrument, tool, value)
        return value

    def collect(self, parser=None):
        return report.collect(self.native, self.root / 'period.json', self.root, parser)

    def mutate_physical(self, field, value):
        def change(instrument, tool, result):
            if tool == 'portfolio.list_positions':
                result['configured_crypto_position_observation'][field] = value
        self.mutate = change

    def rejected(self, result, expected=None):
        row = next(row for row in result['components'] if row['instrument'] == 'BTC.USD')
        detail = result['observation_details']['BTC.USD']
        self.assertFalse(result['complete'])
        self.assertFalse(row['valuation_complete'])
        self.assertIsNone(row['net_total_pnl'])
        self.assertIs(detail['authoritative'], False)
        self.assertEqual(detail['reason_code'], self.owner_error)
        if expected is not None:
            self.assertEqual(detail['account_flat_fallback_reason_code'], expected)
        return row, detail

    def test_coherent_explicit_account_zero_recovers_valuation_but_never_owner_scope(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = self.collect()
        row = next(row for row in result['components'] if row['instrument'] == 'BTC.USD')
        detail = result['observation_details']['BTC.USD']
        self.assertTrue(result['complete'])
        self.assertTrue(row['valuation_complete'])
        self.assertEqual(row['mark_source'], 'NOT_REQUIRED_AUTHORITATIVE_FLAT')
        self.assertEqual(result['totals']['gross_total_pnl'], '10')
        self.assertEqual(result['totals']['net_total_pnl'], '8')
        self.assertEqual(detail['fallback'], 'ACCOUNT_ZERO_POSITION_VALUATION_ONLY')
        self.assertEqual(detail['owner_read_failure_reason_code'], 'DISCOVERY_LIST_REJECTED')
        self.assertEqual(detail['source_read_instrument'], 'EUR.USD')
        self.assertIs(detail['owner_scope_proven'], False)
        self.assertIs(detail['mutation_authorized'], False)
        self.assertNotIn('owner_scope', detail)
        self.assertNotIn('unsettled_native_command_ids', detail)
        self.assertIsNone(detail['quote'])
        self.assertEqual(detail['sampled_at_ms'], fixture.NOW - 100)
        self.assertEqual(detail['position_evidence']['account'], 'DU_TEST')
        self.assertEqual(detail['global_order_evidence']['active_order_ids'], [])
        self.assertEqual(len(self.calls), 8)
        self.assertTrue(all(tool in report.READ_TOOLS for _, tool in self.calls))
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)

    def test_missing_duplicate_aliased_or_nonzero_rows_cannot_prove_zero(self):
        for rows in (None, [], [{'instrument': 'EUR.USD', 'quantity': 0}],
                     [{'instrument': 'BTC', 'quantity': 0}],
                     [{'instrument': 'BTC.USD', 'quantity': 0}] * 2,
                     [{'instrument': 'BTC.USD', 'quantity': 1}],
                     [{'instrument': 'BTC.USD', 'quantity': -1}],
                     [{'instrument': 'BTC.USD', 'quantity': '0.00000000001'}]):
            with self.subTest(rows=rows):
                self.mutate_physical('positions', rows)
                self.rejected(self.collect(), 'PNL_ACCOUNT_CRYPTO_POSITION_NOT_EXPLICIT_ZERO')

    def test_absent_account_proof_never_uses_empty_baseline_delta_as_zero(self):
        def change(instrument, tool, result):
            if tool == 'portfolio.list_positions':
                result.pop('configured_crypto_position_observation')
        self.mutate = change
        self.rejected(self.collect(), 'PNL_ACCOUNT_CRYPTO_POSITION_PROOF_UNAVAILABLE')

    def test_account_service_source_and_scope_must_be_exact(self):
        for field, value in (('account', 'DU_OTHER'), ('execution_service_epoch', 'other'),
                             ('source', 'DRIVER'), ('scope', 'PAPER_BASELINE_DELTA'),
                             ('authoritative', False), ('mutation_authorized', True)):
            with self.subTest(field=field):
                self.mutate_physical(field, value)
                self.rejected(self.collect(), 'PNL_ACCOUNT_CRYPTO_POSITION_PROOF_UNAVAILABLE')

    def test_all_generations_must_be_positive_and_match_same_snapshot(self):
        for field, value in (('connection_epoch', 4), ('connection_epoch', True),
                             ('position_generation', 2), ('position_generation', 0),
                             ('active_orders_generation', 2), ('active_orders_generation', 0),
                             ('terminal_generation', 0), ('terminal_generation', True)):
            with self.subTest(field=field, value=value):
                self.mutate_physical(field, value)
                self.rejected(self.collect(), 'PNL_ACCOUNT_CRYPTO_SNAPSHOT_GENERATION_MISMATCH')

    def test_global_order_emptiness_is_required_not_just_eur_owner_emptiness(self):
        for field, value in (('active_order_ids', [777]), ('active_order_ids', None),
                             ('active_orders_source', 'OWNER_PROJECTION')):
            def change(instrument, tool, result):
                if tool == 'orders.list':
                    result[field] = value
                    result['owned_active_order_ids'] = []
            with self.subTest(field=field, value=value):
                self.mutate = change
                self.rejected(self.collect(), 'PNL_ACCOUNT_GLOBAL_ORDERS_NOT_PROVEN_EMPTY')

    def test_position_and_global_order_changes_discard_whole_account_sample(self):
        calls = 0
        def change(instrument, tool, result):
            nonlocal calls
            if tool == 'portfolio.list_positions':
                calls += 1
                result['configured_crypto_position_observation']['terminal_generation'] += calls
        self.mutate = change
        result = self.collect()
        self.rejected(result, 'PNL_ACCOUNT_CRYPTO_POSITION_PROOF_UNAVAILABLE')
        self.assertEqual(result['observation_details']['EUR.USD']['reason_code'],
                         'PNL_COLLECTION_BROKER_SNAPSHOT_CHANGED')
        self.assertEqual(calls, 4)

    def test_stale_future_or_expired_during_btc_refusal_proof_cannot_be_refreshed(self):
        for sampled in (fixture.NOW - 10001, fixture.NOW + 1, None):
            with self.subTest(sampled=sampled):
                self.mutate_physical('sampled_at_ms', sampled)
                self.rejected(self.collect(), 'PNL_ACCOUNT_CRYPTO_POSITION_STALE_OR_FUTURE')
        self.mutate = lambda instrument, tool, value: None
        self.on_owner_read = lambda: setattr(self, 'clock', fixture.NOW + 10001)
        self.rejected(self.collect(), 'PNL_ACCOUNT_CRYPTO_POSITION_STALE_OR_FUTURE')

    def test_flat_local_driver_must_be_explicit_unsettled_false_and_unchanged(self):
        for update in ({'position': 1}, {'pending': {}}, {'pending': {'command_id': 'new'}},
                       {'lease_uncertain': True}, {'lease_uncertain': None}):
            with self.subTest(update=update):
                self.write_state({**self.state, **update})
                self.rejected(self.collect(), 'PNL_ACCOUNT_FLAT_DRIVER_UNSETTLED_OR_NONZERO')
        for field in ('pending', 'lease_uncertain'):
            state = dict(self.state)
            state.pop(field)
            self.write_state(state)
            with self.subTest(missing=field):
                self.rejected(self.collect(), 'PNL_ACCOUNT_FLAT_DRIVER_UNSETTLED_OR_NONZERO')
        self.write_state(self.state)
        self.on_owner_read = lambda: self.write_state({**self.state, 'pending': {'command_id': 'new'}})
        self.rejected(self.collect(), 'PNL_ACCOUNT_FLAT_DRIVER_STATE_CHANGED')

    def test_nonzero_or_unsettled_ledger_cannot_be_flattened_by_account_proof(self):
        for rows in (fixture.basic(), [row for row in self.rows
                                       if not (row['event'] == 'broker_order_status' and row['req_id'] == 'close')]):
            with self.subTest(events=len(rows)):
                self.write_journal(rows)
                self.rejected(self.collect(), 'PNL_ACCOUNT_FLAT_LEDGER_UNSETTLED_OR_NONZERO')
        self.write_journal([fixture.send()])
        self.rejected(self.collect(), 'SUBMITTED_COMMAND_WITHOUT_FILL_OR_ZERO_FILL_PROOF')

    def test_fee_conflict_stays_unknown_even_when_physical_zero_values_gross(self):
        self.write_journal(self.rows + [dict(self.rows[-1], broker_econ_commission=2)])
        result = self.collect()
        row = next(row for row in result['components'] if row['instrument'] == 'BTC.USD')
        self.assertTrue(row['valuation_complete'])
        self.assertFalse(row['fees_complete'])
        self.assertEqual(row['gross_total_pnl'], '10')
        self.assertIsNone(row['realized_net_pnl'])
        self.assertIsNone(row['net_total_pnl'])
        self.assertIsNone(result['totals']['net_total_pnl'])

    def test_only_exact_authorization_refusal_can_use_this_fallback(self):
        for reason in ('PNL_READ_STREAM_CLOSED', 'SCHEMA_HASH_MISMATCH',
                       'NATIVE_READ_BINARY_CHANGED', 'NATIVE_HEALTH_NOT_AUTHORITATIVE',
                       'PNL_COLLECTION_SERVICE_EPOCH_CHANGED', 'DISCOVERY_LIST_REJECTED_EXTRA'):
            with self.subTest(reason=reason):
                self.owner_error = reason
                _, detail = self.rejected(self.collect())
                self.assertNotIn('account_flat_fallback_reason_code', detail)

    def test_incremental_ledger_cache_never_reuses_prior_account_zero(self):
        parser = report.IncrementalOMSParser()
        self.addCleanup(parser.close)
        first = self.collect(parser)
        self.assertTrue(first['complete'])
        self.mutate_physical('positions', [{'instrument': 'BTC.USD', 'quantity': 1}])
        second = self.collect(parser)
        self.rejected(second, 'PNL_ACCOUNT_CRYPTO_POSITION_NOT_EXPLICIT_ZERO')
        self.assertEqual(second['journal_sha256'], first['journal_sha256'])

    def test_flat_proof_preserves_ledger_arithmetic_when_it_primes_the_cache(self):
        sent = fixture.send('close', direction='SELL', at=120000, oid=2)
        fill = fixture.fill(sent, 'close.1', price='110.1234567890123456789012345678901234567')
        rows = fixture.basic() + [sent, fill, fixture.terminal(fill), fixture.commission(fill)]
        self.write_journal(rows)
        expected = report.build_report(rows, fixture.period(),
            {instrument: fixture.observation(0, instrument=instrument)
             for instrument in report.INSTRUMENTS}, fixture.NOW)
        parser = report.IncrementalOMSParser()
        self.addCleanup(parser.close)
        for result in (self.collect(), self.collect(parser), self.collect(parser)):
            self.assertEqual(result['totals'], expected['totals'])

    def test_repeating_cost_basis_keeps_exact_component_totals_in_cold_and_warm_reads(self):
        # Ordinary finite-price fills can create a repeating weighted entry
        # cost. Preserve the component's 40-digit Decimal result in totals;
        # summing at Python's default 28 digits silently drops the last places.
        rows = list(self.rows)
        for index, (direction, quantity, price) in enumerate((
                ('BUY', 10000, '1.1'), ('BUY', 20000, '1.101'), ('SELL', 30000, '1.1'))):
            sent = fixture.send('eur-' + str(index), instrument='EUR.USD', direction=direction,
                                at=110000 + index * 10000, oid=100 + index)
            fill = fixture.fill(sent, 'eur-' + str(index) + '.1', qty=quantity, price=price)
            rows.extend([sent, fill, fixture.terminal(fill), fixture.commission(fill)])
        self.write_journal(rows)
        parser = report.IncrementalOMSParser()
        self.addCleanup(parser.close)
        results = [self.collect(), self.collect(parser), self.collect(parser)]
        self.assertTrue(all(result['complete'] for result in results))
        self.assertEqual(results[0]['totals'], results[1]['totals'])
        self.assertEqual(results[1]['totals'], results[2]['totals'])
        fields = ('realized_gross_pnl', 'unrealized_gross_pnl', 'gross_total_pnl',
                  'fees_paid', 'realized_net_pnl', 'unrealized_net_pnl', 'net_total_pnl')
        for result in results:
            with localcontext() as context:
                context.prec = 40
                for field in fields:
                    expected = sum((Decimal(row[field]) for row in result['components']), Decimal(0))
                    self.assertEqual(Decimal(result['totals'][field]), expected, field)
                full = Decimal(result['totals']['realized_gross_pnl'])
            with localcontext() as context:
                context.prec = 28
                # Ensure this fixture actually exercises the lost precision.
                self.assertNotEqual(full, +full)


if __name__ == '__main__':
    unittest.main()
