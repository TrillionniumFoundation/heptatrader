import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import prepare_current100k_new_scope as preparation


class NewScopeSelectionTests(unittest.TestCase):
    def setUp(self):
        self.registration = {'manifest': '/new/manifest.json', 'sha256': 'new-producer'}
        self.prior = {'period_sha256': 'period', 'producer_registration': {'sha256': 'old-producer'},
            'behavior_scope': {'behavior_model_id': 'model'},
            'training_input_ids': ['old-input'], 'training_reward_ids': ['old-reward']}
        self.validator = Mock()

    def dataset(self, count):
        data = {'schema': 'heptatrader.current100k-value-dataset.v1', 'period_sha256': 'period',
            'producer_manifest_sha256': 'new-producer', 'synthetic_rewards': 0, 'old25k_rewards': 0,
            'trade_authority': False, 'rows': [{'row_id': str(i), 'input_id': 'input-'+str(i),
                'reward_id': 'reward-'+str(i), 'episode_id': 'episode-'+str(i), 'period_sha256': 'period',
                'input_started_at_ms': 1000+i, 'net_usd': str(i-20),
                'behavior_scope': {'producer_manifest_sha256': 'new-producer', 'model_id': 'model'}}
                for i in range(count)]}
        return self.fingerprint(data)

    def fingerprint(self, data):
        data['fingerprint'] = preparation.digest({k:v for k,v in data.items() if k != 'fingerprint'})
        return data

    def select(self, data, history=None):
        return preparation.select_dataset(data, self.registration, self.prior,
            [self.prior] if history is None else history, self.validator)

    def test_thirty_one_does_not_prepare_fit_or_lower_coverage_requirement(self):
        frozen, summary = self.select(self.dataset(31))
        self.assertIsNone(frozen); self.assertEqual(summary['remaining_rows'], 1)
        self.assertEqual(summary['state'], 'WAITING_NEW_PRODUCER_32_SETTLED_LABELS')
        self.validator.assert_not_called()
        self.assertFalse(summary['training_started']); self.assertFalse(summary['trial_registered'])

    def test_first_thirty_two_are_chosen_by_time_without_reward_ranking(self):
        data = self.dataset(40); data['rows'].reverse(); self.fingerprint(data)
        before = copy.deepcopy(data)
        frozen, summary = self.select(data)
        self.assertEqual([r['row_id'] for r in frozen['rows']], [str(i) for i in range(32)])
        self.assertEqual(data, before)
        self.validator.assert_called_once_with(frozen, data)
        self.assertEqual(frozen['fingerprint'], preparation.digest({k:v for k,v in frozen.items() if k != 'fingerprint'}))
        self.assertEqual(summary['minimum_rows'], 32)
        self.assertFalse(summary['efficacy_claim'])

    def test_legacy_producer_rows_cannot_be_relabelled_as_new_scope(self):
        data = self.dataset(32); data['producer_manifest_sha256'] = 'old-producer'; self.fingerprint(data)
        with self.assertRaisesRegex(ValueError, 'IDENTITY_OR_POLICY'): self.select(data)
        self.validator.assert_not_called()

    def test_mixed_row_producer_model_or_period_is_not_filtered_away(self):
        for field in ('producer_manifest_sha256', 'model_id'):
            data = self.dataset(32); data['rows'][0]['behavior_scope'][field] = 'changed'; self.fingerprint(data)
            with self.assertRaisesRegex(ValueError, 'MIXED_ROW_IDENTITY'): self.select(data)
        data = self.dataset(32); data['rows'][0]['period_sha256'] = 'changed'; self.fingerprint(data)
        with self.assertRaisesRegex(ValueError, 'MIXED_ROW_IDENTITY'): self.select(data)

    def test_duplicates_do_not_inflate_training_coverage(self):
        for field in ('row_id', 'input_id', 'reward_id', 'episode_id'):
            data = self.dataset(32); data['rows'][-1][field] = data['rows'][0][field]; self.fingerprint(data)
            with self.assertRaisesRegex(ValueError, 'DUPLICATE'): self.select(data)

    def test_overlap_with_any_historical_trial_is_rejected(self):
        data = self.dataset(32)
        ancient = {**self.prior, 'training_input_ids': ['input-7'], 'training_reward_ids': []}
        with self.assertRaisesRegex(ValueError, 'HISTORICAL_TRAINING_OVERLAP'):
            self.select(data, [ancient, self.prior])
        ancient = {**self.prior, 'training_input_ids': [], 'training_reward_ids': ['reward-31']}
        with self.assertRaisesRegex(ValueError, 'HISTORICAL_TRAINING_OVERLAP'):
            self.select(data, [ancient, self.prior])

    def test_existing_economic_provenance_failure_blocks_publication(self):
        self.validator.side_effect = ValueError('ECONOMIC_RECOMPUTATION_FAILED')
        with self.assertRaisesRegex(ValueError, 'ECONOMIC_RECOMPUTATION_FAILED'):
            self.select(self.dataset(32))

    def test_immutable_preparation_cannot_change_a_frozen_cohort(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'frozen.json'
            preparation.write_once(path, {'row_ids': ['a']}); before = path.read_bytes()
            preparation.write_once(path, {'row_ids': ['a']})
            with self.assertRaisesRegex(ValueError, 'DIFFERENT_BYTES'):
                preparation.write_once(path, {'row_ids': ['b']})
            self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
