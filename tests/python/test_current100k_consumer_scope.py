import copy
import unittest

import prepare_current100k_consumer_scope as scope


class ConsumerScopeTests(unittest.TestCase):
    def setUp(self):
        self.config = {'schema': 'heptatrader.current100k-consumer-config.v1',
            'registration': {'manifest': '/old/manifest', 'sha256': 'old'},
            'output_root': '/evidence/scopes/old', 'automatic_training': False, 'automatic_trading': False,
            'period_sha256': 'period', 'runtime_root': '/frozen/runtime', 'runtime_sha256': {'file.py': 'sha'},
            'reward_config': {'oms_path': '/same/oms', 'legacy_mappings': [{'sha256': 'historical'}]}}
        self.manifest = {'schema': 'heptatrader.attribution-producer.v1', 'execution_domain': 'PAPER',
            'instrument': 'EUR.USD', 'period_sha256': 'period', 'behavior_profile': 'ORIGINAL_100K'}
        self.new = {**self.manifest, 'prepared_from_manifest_sha256': 'old'}
        self.registration = {'manifest': '/new/manifest', 'sha256': 'new'}

    def test_only_new_registration_and_separate_output_change(self):
        before = copy.deepcopy(self.config)
        result = scope.new_scope_config(self.config, self.registration, self.manifest, self.new)
        self.assertEqual(result['output_root'], '/evidence/scopes/new')
        self.assertEqual(result['reward_config'], before['reward_config'])
        self.assertEqual(result['runtime_sha256'], before['runtime_sha256'])
        self.assertEqual(result['runtime_root'], before['runtime_root'])
        self.assertEqual(self.config, before)
        result['reward_config']['legacy_mappings'][0]['sha256'] = 'different'
        self.assertEqual(self.config, before)

    def test_changed_period_behavior_or_instrument_is_rejected(self):
        for field in ('period_sha256', 'behavior_profile', 'instrument', 'execution_domain'):
            with self.assertRaisesRegex(ValueError, 'ORIGINAL_BEHAVIOR_CHANGED'):
                scope.new_scope_config(self.config, self.registration, self.manifest, {**self.new, field: 'changed'})

    def test_unknown_lineage_and_same_producer_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'ORIGINAL_LINEAGE_REQUIRED'):
            scope.new_scope_config(self.config, self.registration, self.manifest,
                {**self.new, 'prepared_from_manifest_sha256': 'unrelated'})
        with self.assertRaisesRegex(ValueError, 'ORIGINAL_LINEAGE_REQUIRED'):
            scope.new_scope_config(self.config, self.config['registration'], self.manifest, self.new)

    def test_shared_unscoped_output_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'PRODUCER_NAMED_OUTPUT_REQUIRED'):
            scope.new_scope_config({**self.config, 'output_root': '/shared/output'},
                self.registration, self.manifest, self.new)


if __name__ == '__main__':
    unittest.main()
