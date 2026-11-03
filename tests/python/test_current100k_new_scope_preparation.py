import copy
import hashlib
import json
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

    def prepare(self, output, data, now=5000):
        raw = preparation.canonical(data)
        return preparation.prepare_ready(output, data, self.registration, self.prior, [self.prior],
            {'/history/plan.json': 'original-plan-pin'}, Path('/consumer/dataset.json'),
            hashlib.sha256(raw).hexdigest(), now, self.validator)

    def test_ready_resume_retains_original_cohort_when_population_and_metadata_advance(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            receipt, _ = self.prepare(output, self.dataset(32))
            frozen_before = (output / 'FROZEN_NEW_PRODUCER_32.json').read_bytes()
            ready_before = (output / 'READY_FOR_GUARDED_FIT.json').read_bytes()
            expanded = self.dataset(40); expanded['exported_at_ms'] = 6000
            # A later settlement can have an earlier input time. It must not
            # replace the cohort chosen at the first eligible observation.
            expanded['rows'][39]['input_started_at_ms'] = 999
            self.fingerprint(expanded)
            resumed, status = self.prepare(output, expanded, now=6000)
            self.assertEqual(resumed, receipt)
            self.assertEqual(status['distinct_settled_rows'], 40)
            self.assertTrue(status['resumed_frozen_cohort'])
            self.assertEqual((output / 'FROZEN_NEW_PRODUCER_32.json').read_bytes(), frozen_before)
            self.assertEqual((output / 'READY_FOR_GUARDED_FIT.json').read_bytes(), ready_before)
            self.assertEqual(self.validator.call_count, 2)
            self.assertEqual(self.validator.call_args.args[1], expanded)

    def test_crash_after_frozen_write_recovers_without_reselecting(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            frozen, _ = self.select(self.dataset(32))
            preparation.write_once(output / 'FROZEN_NEW_PRODUCER_32.json', frozen)
            receipt, _ = self.prepare(output, self.dataset(40))
            self.assertTrue(receipt['recovered_existing_frozen_cohort'])
            self.assertEqual(receipt['selected_row_ids'], [str(i) for i in range(32)])
            self.assertEqual(json.loads((output / 'FROZEN_NEW_PRODUCER_32.json').read_bytes()), frozen)

    def test_resume_requires_original_economic_provenance_again(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory); self.prepare(output, self.dataset(32))
            before = (output / 'READY_FOR_GUARDED_FIT.json').read_bytes()
            self.validator.side_effect = ValueError('REAL_REWARD_WITHDRAWN_OR_CHANGED')
            with self.assertRaisesRegex(ValueError, 'REAL_REWARD_WITHDRAWN_OR_CHANGED'):
                self.prepare(output, self.dataset(40))
            self.assertEqual((output / 'READY_FOR_GUARDED_FIT.json').read_bytes(), before)

    def test_changed_ready_identity_cannot_redirect_continuation(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory); self.prepare(output, self.dataset(32))
            path = output / 'READY_FOR_GUARDED_FIT.json'; receipt = json.loads(path.read_bytes())
            receipt['dataset_path'] = '/another/cohort.json'
            path.chmod(0o600); path.write_bytes(preparation.canonical(receipt))
            with self.assertRaisesRegex(ValueError, 'READY_RECEIPT_CHANGED'):
                self.prepare(output, self.dataset(32))

    def test_ready_without_frozen_data_cannot_be_reconstructed_with_new_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory); self.prepare(output, self.dataset(32))
            (output / 'FROZEN_NEW_PRODUCER_32.json').unlink()
            with self.assertRaisesRegex(ValueError, 'READY_WITHOUT_FROZEN_DATASET'):
                self.prepare(output, self.dataset(40))

    def test_resumed_cohort_still_requires_exactly_thirty_two_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            preparation.write_once(output / 'FROZEN_NEW_PRODUCER_32.json', self.dataset(31))
            with self.assertRaisesRegex(ValueError, 'FROZEN_COHORT_BOUND_OR_ORDER'):
                self.prepare(output, self.dataset(32))

    def test_latest_status_is_published_after_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory); _, summary = self.prepare(output, self.dataset(32))
            preparation.write_status(output, summary)
            status = json.loads((output / 'status.json').read_bytes())
            self.assertEqual(status['state'], 'VERIFIED_NEW_PRODUCER_32_READY_FOR_GUARDED_FIT')
            self.assertEqual(status['remaining_rows'], 0)
            self.assertFalse((output / 'status.json.next').exists())


if __name__ == '__main__':
    unittest.main()
