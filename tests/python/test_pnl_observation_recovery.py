"""Recover whole read-only observations without relaxing economic identity."""
import unittest

import hepta_pnl_report as report
import test_pnl_accounting as fixture
import test_pnl_native_stream as stream_fixture


class ObservationRecoveryTests(unittest.TestCase):
    native = fixture.CollectionTests.native

    def test_both_native_read_timestamps_do_not_change_the_broker_snapshot(self):
        calls = 0
        originals = []
        def native(instrument, tool):
            nonlocal calls
            value = self.native(instrument, tool)
            if tool == 'portfolio.list_positions':
                calls += 1
                for name in ('settlement_observation', 'configured_crypto_position_observation'):
                    value[name] = {'sampled_at_ms': calls, 'position_generation': 9,
                                   'positions': [{'instrument': 'BTC.USD', 'quantity': 1}]}
                originals.append(value)
            return value
        result = report.observe_with_recovery(native, fixture.period(), 'EUR.USD')
        self.assertEqual(result['collection_attempts'], 1)
        self.assertEqual(result['discarded_observation_reasons'], [])
        self.assertEqual([v['configured_crypto_position_observation']['sampled_at_ms']
                          for v in originals], [1, 2])

    def test_other_nested_fields_are_still_checked_and_retry_is_bounded(self):
        for field in ('position_generation', 'terminal_generation', 'connection_epoch',
                      'account', 'positions', 'future_identity_field'):
            count = 0
            def native(instrument, tool):
                nonlocal count
                value = self.native(instrument, tool)
                if tool == 'portfolio.list_positions':
                    count += 1
                    value['configured_crypto_position_observation'] = {
                        'sampled_at_ms': count, field: count}
                return value
            with self.subTest(field=field), self.assertRaisesRegex(
                    report.EvidenceError, 'BROKER_SNAPSHOT_CHANGED'):
                report.observe_with_recovery(native, fixture.period(), 'EUR.USD')
            self.assertEqual(count, 4)

    def test_epoch_rollover_discards_the_entire_old_sample(self):
        health = 0
        quotes = 0
        def native(instrument, tool):
            nonlocal health, quotes
            value = self.native(instrument, tool)
            if tool == 'system.get_health':
                health += 1
                value['execution_service_epoch'] = 'old' if health == 1 else 'new'
            if tool == 'market.get_quote':
                quotes += 1
                value['sample_number'] = quotes
            return value
        result = report.observe_with_recovery(native, fixture.period(), 'BTC.USD')
        self.assertEqual(result['service_identity'][0], 'new')
        self.assertEqual(result['quote']['sample_number'], 2)
        self.assertEqual(result['collection_attempts'], 2)
        self.assertEqual(result['discarded_observation_reasons'],
                         ['PNL_COLLECTION_SERVICE_EPOCH_CHANGED'])

    def test_owner_and_schema_refusals_are_not_retried(self):
        for reason in ('NATIVE_HEALTH_NOT_AUTHORITATIVE', 'SCHEMA_HASH_MISMATCH',
                       'NATIVE_READ_BINARY_CHANGED', 'PNL_READ_STREAM_CLEANUP_PENDING'):
            calls = []
            def native(instrument, tool):
                calls.append((instrument, tool))
                raise report.EvidenceError(reason)
            with self.subTest(reason=reason), self.assertRaisesRegex(report.EvidenceError, reason):
                report.observe_with_recovery(native, fixture.period(), 'BTC.USD')
            self.assertEqual(len(calls), 1)

    def test_changed_account_still_cannot_authorize_a_snapshot(self):
        count = 0
        def native(instrument, tool):
            nonlocal count
            count += 1
            value = self.native(instrument, tool)
            if tool == 'orders.list':
                value['owner_scope']['account'] = 'DU_OTHER'
            return value
        with self.assertRaisesRegex(report.EvidenceError, 'ORDER_OWNER_UNPROVEN'):
            report.observe_with_recovery(native, fixture.period(), 'BTC.USD')
        self.assertEqual(count, 7)


class StreamRecoveryTests(unittest.TestCase):
    setUp = stream_fixture.NativeStreamTests.setUp
    calls = stream_fixture.NativeStreamTests.calls
    mode = stream_fixture.NativeStreamTests.mode

    def test_eof_starts_one_new_verified_reader_and_all_seven_reads(self):
        executable = self.root / 'bin/heptactl'
        executable.write_text(executable.read_text().replace(
            "    if mode=='eof':sys.exit(3)",
            "    if mode=='eof_once':(root/'mode').unlink();sys.exit(3)\n"
            "    if mode=='eof':sys.exit(3)"))
        self.mode('eof_once')
        result = report.observe_with_recovery(self.reader, fixture.period(), 'BTC.USD')
        self.assertEqual(result['collection_attempts'], 2)
        self.assertEqual(result['discarded_observation_reasons'], ['PNL_READ_STREAM_CLOSED'])
        calls = self.calls()
        self.assertEqual(len([c for c in calls if c[0] == 'START']), 2)
        self.assertEqual(len([c for c in calls if c[0] == 'CALL']), 8)
        self.assertTrue(all(c[2] == 'crypto.token' for c in calls if c[0] == 'START'))

    def test_persistent_eof_is_unavailable_after_two_readers(self):
        self.mode('eof')
        with self.assertRaisesRegex(report.EvidenceError, 'PNL_READ_STREAM_CLOSED'):
            report.observe_with_recovery(self.reader, fixture.period(), 'BTC.USD')
        self.assertEqual(len([c for c in self.calls() if c[0] == 'START']), 2)
        self.assertEqual(self.reader._processes, {})

    def test_protocol_refusal_remains_fail_fast(self):
        self.mode('schema')
        with self.assertRaisesRegex(report.EvidenceError, 'SCHEMA_HASH_MISMATCH'):
            report.observe_with_recovery(self.reader, fixture.period(), 'BTC.USD')
        self.assertEqual(len([c for c in self.calls() if c[0] == 'START']), 1)


class DriverDifferenceTests(unittest.TestCase):
    def rows(self):
        rows = []
        for command, direction, price, average, at, oid in (
                ('open', 'BUY', '1.1000003', '1.100000', 110000, 1),
                ('close', 'SELL', '1.1010002', '1.101000', 120000, 2)):
            sent = fixture.send(command, instrument='EUR.USD', direction=direction, at=at, oid=oid)
            fill = fixture.fill(sent, command + '.1', qty=25000, price=price, average=average)
            rows.extend([sent, fill, fixture.terminal(fill), fixture.commission(fill)])
        return rows

    def test_rounded_average_difference_is_explained_without_changing_ledger(self):
        result = report.build_report(self.rows(), fixture.period(),
            {i: fixture.observation(0, instrument=i) for i in report.INSTRUMENTS},
            fixture.NOW, {'EUR.USD': {'position': 0, 'gross_pnl': '25'}})
        row = result['components'][0]
        self.assertEqual(row['instrument'], 'EUR.USD')
        self.assertEqual(row['realized_gross_pnl'], '24.9975000')
        self.assertEqual(row['realized_net_pnl'], '22.9975000')
        self.assertEqual(row['driver_reconciliation']['status'], 'EXPLAINED_BY_CUMULATIVE_AVERAGE_PRICE')
        self.assertFalse(row['driver_reconciliation']['ledger_adjusted'])
        self.assertIn('DRIVER_REALIZED_GROSS_DIFFERS_FROM_EXECUTION_LEDGER', row['reason_codes'])

    def test_other_difference_is_not_disguised_as_rounding(self):
        result = report.build_report(self.rows(), fixture.period(),
            {i: fixture.observation(0, instrument=i) for i in report.INSTRUMENTS},
            fixture.NOW, {'EUR.USD': {'position': 0, 'gross_pnl': '25.5'}})
        row = result['components'][0]
        self.assertEqual(row['driver_reconciliation']['status'], 'UNEXPLAINED_DRIVER_LEDGER_DIFFERENCE')
        self.assertEqual(row['realized_gross_pnl'], '24.9975000')


class RewardFeeCompletenessTests(unittest.TestCase):
    def test_conflicting_fee_cannot_become_a_learning_target(self):
        from laya_cell.resident_rewards import financial_episodes
        rows = DriverDifferenceTests().rows()
        rows.append(dict(rows[-1], broker_econ_commission=2))
        ledger = fixture.a.replay(rows, fixture.period(), 'EUR.USD', fixture.NOW)
        self.assertFalse(ledger['fees_complete'])
        bindings = {'open': {'input_id': 'i', 'forecast_id': 'f', 'model_id': 'm'}}
        rewards, blocked = financial_episodes(ledger, bindings, 'reset-fixture', 'EUR.USD')
        self.assertEqual(len(rewards), 1)
        self.assertFalse(rewards[0]['learning_eligible'])
        self.assertFalse(rewards[0]['fees_complete'])
        self.assertIsNone(rewards[0]['actual_commissions_usd'])
        self.assertIsNone(rewards[0]['realized_net_usd'])
        self.assertIn('CONFLICTING_EXECUTION_COMMISSION', rewards[0]['learning_exclusion_reasons'])

    def test_one_conflicted_episode_does_not_poison_unrelated_exact_rewards(self):
        from laya_cell.resident_rewards import financial_episodes
        rows = DriverDifferenceTests().rows()
        rows.append(dict(rows[-1], broker_econ_commission=2))
        for command, direction, price, at, oid in (
                ('open2', 'BUY', 1.1, 130000, 3), ('close2', 'SELL', 1.101, 140000, 4)):
            sent = fixture.send(command, instrument='EUR.USD', direction=direction, at=at, oid=oid)
            fill = fixture.fill(sent, command + '.1', qty=25000, price=price)
            rows.extend([sent, fill, fixture.terminal(fill), fixture.commission(fill)])
        ledger = fixture.a.replay(rows, fixture.period(), 'EUR.USD', fixture.NOW)
        bindings = {c: {'input_id': c, 'forecast_id': 'f', 'model_id': 'm'} for c in ('open', 'open2')}
        rewards, blocked = financial_episodes(ledger, bindings, 'reset-fixture', 'EUR.USD')
        self.assertEqual([r['learning_eligible'] for r in rewards], [False, True])
        self.assertEqual(rewards[1]['realized_net_usd'], '23.000')


if __name__ == '__main__':
    unittest.main()
