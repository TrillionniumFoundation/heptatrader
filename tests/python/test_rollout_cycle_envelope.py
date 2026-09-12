"""Behavioral tests: one P1 cycle is one opening leg and one exact exit.

Fixtures are synthetic; these tests do not connect to or qualify a Broker.
"""
from __future__ import annotations
import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import verify_ib_paper_rollout as rollout
from paper_rollout_fixtures import RolloutFixture


class RolloutCycleEnvelopeTests(unittest.TestCase):
    def test_two_round_trips_cannot_be_hidden_inside_one_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            extra_journal = copy.deepcopy(fixture.journal)
            extra_callbacks = copy.deepcopy(fixture.callbacks)
            for row in extra_journal + extra_callbacks:
                row['command_id'] += '-extra'
                row['order_id'] += '-extra'
                row['observed_at_ms'] += 400
                if row.get('execution_id'):
                    row['execution_id'] += '-extra'
            for row in extra_journal:
                row['sequence'] += len(fixture.journal)
            fixture.journal.extend(extra_journal)
            fixture.callbacks.extend(extra_callbacks)
            fixture.save()
            with self.assertRaisesRegex(rollout.VerificationError, 'exactly two mutation legs'):
                fixture.verify(rollout)

    def test_short_open_is_not_the_fixed_buy_then_flatten_harness(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            for row in fixture.journal + fixture.callbacks:
                row['side'] = 'SELL' if row['side'] == 'BUY' else 'BUY'
            fixture.save()
            with self.assertRaisesRegex(rollout.VerificationError, 'opening BUY'):
                fixture.verify(rollout)

    def test_oversized_exit_is_rejected_even_when_only_partially_filled_flat(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            for row in fixture.callbacks:
                row['quantity'] = 0.5
                if row['event'] == 'terminal':
                    row['status'] = 'Cancelled'
            fixture.save()
            with self.assertRaisesRegex(rollout.VerificationError, 'exact observed opening position'):
                fixture.verify(rollout)

    def test_partial_open_followed_by_exact_exit_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            for row in fixture.journal:
                if row['side'] == 'SELL':
                    row['quantity'] = 0.5
            for row in fixture.callbacks:
                row['quantity'] = 0.5
                if row['event'] == 'terminal' and row['side'] == 'BUY':
                    row['status'] = 'Cancelled'
            receipt = fixture.save().verify(rollout)
            self.assertEqual(receipt['mutation_cycles'], 1)
            self.assertFalse(receipt['paper_authorized'])

    def test_multiple_execution_reports_in_one_leg_are_not_extra_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            first = fixture.callbacks[0]
            second = copy.deepcopy(first)
            first['quantity'] = 0.25
            second.update(quantity=0.75, execution_id=first['execution_id']+'-partial',
                          observed_at_ms=first['observed_at_ms']+1)
            fixture.callbacks.insert(1, second)
            self.assertEqual(fixture.save().verify(rollout)['mutation_cycles'], 1)

    def test_exact_duplicate_execution_is_still_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RolloutFixture(Path(directory))
            fixture.callbacks.insert(1, copy.deepcopy(fixture.callbacks[0]))
            self.assertEqual(fixture.save().verify(rollout)['mutation_cycles'], 1)

    def test_all_reviewed_stages_keep_their_exact_cycle_counts(self):
        for stage, count in [('canary', 1), ('pilot', 3), ('extended', 10)]:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                receipt = RolloutFixture(Path(directory), stage).verify(rollout)
                self.assertEqual(receipt['mutation_cycles'], count)


if __name__ == '__main__':
    unittest.main()
