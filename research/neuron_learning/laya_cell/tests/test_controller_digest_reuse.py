"""Exercise controller integration, including rejection after cached validation."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from laya_cell import resident, resident_evaluate, resident_budget
from laya_cell.file_digest_cache import FileDigestCache
from laya_cell.integrity import ContractError, digest, sha_file
from laya_cell.promotion import RULE


class ControllerDigestReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.period = self.root / 'period.json'
        self.period.write_text('{"execution_domain":"PAPER"}')
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir()
        self.native = self.runtime / 'native'
        self.native.write_bytes(b'bounded native fixture')
        self.source = self.runtime / 'source.py'
        self.source.write_text('# frozen controller fixture\n')
        self.config = {
            'schema': 'heptatrader.laya-resident-config.v1',
            'automatic_trading': False,
            'training_cadence_ms': resident.MIN_CADENCE_MS,
            'period_path': str(self.period), 'period_sha256': sha_file(self.period),
            'native_binary': str(self.native), 'native_binary_sha256': sha_file(self.native),
            'runtime_directory': str(self.runtime),
            'runtime_sha256': {'native': sha_file(self.native), 'source.py': sha_file(self.source)},
            'objective': {'schema': 'heptatrader.laya-resident-objective.v1'},
            'state_max_bytes': 10000,
        }

    def test_config_native_duplicate_is_read_once_and_changes_still_fail(self):
        cache = FileDigestCache()
        with patch.object(resident, 'sha_file', cache.sha256):
            resident.config_check(self.config)
            first = cache.stats()
            self.assertEqual(first['misses'], 3)
            self.assertEqual(first['hits'], 1)  # native is also in the runtime inventory.
            for _ in range(4):
                resident.config_check(self.config)
            self.assertEqual(cache.stats()['bytes_read'], first['bytes_read'])
            old = self.source.stat()
            self.source.write_text('X' * old.st_size)
            os.utime(self.source, ns=(old.st_atime_ns, old.st_mtime_ns))
            with self.assertRaisesRegex(ContractError, 'RUNTIME_CHANGED:source.py'):
                resident.config_check(self.config)
            self.config['runtime_sha256']['source.py'] = sha_file(self.source)
            resident.config_check(self.config)

    def test_cached_native_does_not_hide_conflicting_second_pin(self):
        cache = FileDigestCache()
        with patch.object(resident, 'sha_file', cache.sha256):
            resident.config_check(self.config)
            self.config['runtime_sha256']['native'] = '0' * 64
            with self.assertRaisesRegex(ContractError, 'RUNTIME_CHANGED:native'):
                resident.config_check(self.config)

    def test_actual_evaluator_reuses_active_pins_and_rejects_changed_source(self):
        evaluation = self.root / 'evaluation'
        folder = evaluation / 'candidate'
        folder.mkdir(parents=True)
        now = int(time.time() * 1000)
        plan = {'schema': 'heptatrader.laya-resident-future-plan.v1',
                'candidate_id': 'fixture', 'trial': 1, 'rule': RULE,
                'start_ms': now + 3600000, 'created_at_ms': now,
                'outcome_end_ms': now + 100000000,
                'scope': self.config['period_sha256'], 'no_refit': True,
                'evaluation_runtime_sha256': {'source.py': sha_file(self.source)}}
        plan['plan_id'] = digest(plan)
        path = folder / 'plan.json'
        path.write_text(json.dumps(plan))
        config = {**self.config, 'evaluation_directory': str(evaluation)}
        config_path = self.root / 'config.json'
        config_path.write_text(json.dumps(config))
        evaluator = resident_evaluate.Evaluator(config_path)
        evaluator.last_log_ms = now
        cache = FileDigestCache()
        original = path.read_bytes()
        with patch.object(resident_evaluate, 'sha_file', cache.sha256):
            evaluator.step()
            before = cache.stats()['bytes_read']
            evaluator.step()
            self.assertEqual(cache.stats()['bytes_read'], before)
            self.source.write_text('# changed controller fixture\n')
            evaluator.step()
        result = json.loads((folder / 'qualification.json').read_text())
        self.assertEqual(result['state'], 'REJECTED')
        self.assertEqual(result['reasons'], ['EVALUATOR_SOURCE_UNPINNED_OR_CHANGED'])
        self.assertEqual(path.read_bytes(), original)

    def test_hot_storage_inspection_rechecks_free_space_and_new_evidence(self):
        config = {'state_root': str(self.root), 'state_max_bytes': 10000,
                  'state_admission_reserve_bytes': 200, 'filesystem_reserve_bytes': 0}
        first = resident_budget.inspect(config)
        extra = self.root / 'extra.json'
        extra.write_bytes(b'x' * 500)
        second = resident_budget.inspect(config)
        self.assertEqual(second['state_used_bytes'], first['state_used_bytes'] + 500)
        disk = type('Disk', (), {'f_bavail': 10, 'f_frsize': 1})()
        with patch.object(resident_budget.os, 'statvfs', return_value=disk):
            self.assertEqual(resident_budget.inspect(config)['blocked_reason'],
                             'FILESYSTEM_RESERVE_NO_NEW_LEARNING')


if __name__ == '__main__':
    unittest.main()
