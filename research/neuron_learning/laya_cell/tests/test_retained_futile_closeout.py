"""CPU subprocess proof that retained plans execute their pinned source version."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import laya_cell
from laya_cell.integrity import digest, sha_file
from laya_cell.promotion import rule_for_trial
from laya_cell.promotion_power import PROTOCOL
from laya_cell.resident_evaluate import Evaluator
from laya_cell.resident_retained_closeout import run_retained


class RetainedCloseoutTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.runtime = self.root / 'original-runtime'
        source = Path(laya_cell.__file__).parent
        target = self.runtime / 'learn/laya_cell'
        shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__', 'tests'))
        shutil.copy2(source.parent / 'data.py', self.runtime / 'learn/data.py')
        self.old_code = target / 'resident_evaluate.py'
        text = self.old_code.read_text()
        text = text.replace('heptatrader.laya-resident-evaluation-status.v2', 'fixture.original-evaluator.v1')
        self.old_code.write_text(text)
        self.period = self.root / 'period.json'; self.period.write_text('{"execution_domain":"PAPER"}')
        self.evaluation = self.root / 'evaluation'; self.folder = self.evaluation / 'old'
        self.folder.mkdir(parents=True)
        self.now = int(time.time() * 1000)
        rule = rule_for_trial(46, PROTOCOL)
        start = self.now - 3 * rule['slot_spacing_ms'] - 180000
        self.plan = {'candidate_id': 'old-candidate', 'job_id': 'old', 'trial': 46,
            'rule': rule, 'start_ms': start,
            'outcome_end_ms': start + (rule['slots'] - 1) * rule['slot_spacing_ms'] + rule['input_grace_ms'] + rule['horizon_ms'] + 5000,
            'scope': sha_file(self.period), 'no_refit': True,
            'evaluation_runtime_sha256': {'learn/laya_cell/resident_evaluate.py': sha_file(self.old_code)}}
        self.path = self.folder / 'plan.json'
        self.write_plan_and_outcomes()
        old = {'automatic_trading': False, 'python': sys.executable,
            'period_path': str(self.period), 'period_sha256': sha_file(self.period),
            'runtime_directory': str(self.runtime), 'evaluation_directory': str(self.evaluation),
            'promotion_protocol': PROTOCOL,
            'runtime_sha256': {str(p.relative_to(self.runtime)): sha_file(p) for p in self.runtime.rglob('*.py')}}
        self.old_config = self.root / 'original.json'; self.old_config.write_text(json.dumps(old))
        self.current = dict(old, runtime_directory=str(self.root / 'new-runtime'))
        self.current['retained_futile_plan_closeouts'] = [{'plan_id': self.plan['plan_id'],
            'config_path': str(self.old_config), 'config_sha256': sha_file(self.old_config)}]

    def write_plan_and_outcomes(self):
        self.plan.pop('plan_id', None); self.plan['plan_id'] = digest(self.plan)
        self.path.write_text(json.dumps(self.plan))
        for slot in range(3):
            (self.folder / f'outcome-{slot:03}.json').write_text(json.dumps({
                'candidate_id': self.plan['candidate_id'], 'plan_id': self.plan['plan_id'],
                'slot': slot, 'status': 'CENSORED', 'reason': 'NO_PREDICTION_IN_FIXED_SLOT'}))

    def run_old(self):
        return run_retained(self.current, self.path, self.plan, {}, self.now)

    def test_dispatch_runs_original_version_and_scopes_out_new_plan(self):
        newroot = Path(self.current['runtime_directory']); newroot.mkdir()
        pinned = newroot / 'pinned.py'; pinned.write_text('# new runtime')
        new = dict(self.plan, candidate_id='new-candidate', job_id='new',
                   start_ms=self.now + 3600000, outcome_end_ms=self.now + 86400000,
                   evaluation_runtime_sha256={'pinned.py': sha_file(pinned)})
        new.pop('plan_id'); new['plan_id'] = digest(new)
        newer = self.evaluation / 'new'; newer.mkdir(); (newer / 'plan.json').write_text(json.dumps(new))
        original = {p.name: p.read_bytes() for p in self.folder.glob('outcome-*.json')}
        config = self.root / 'new.json'; config.write_text(json.dumps(self.current))
        service = Evaluator(config)
        with patch.object(service, 'launch', side_effect=AssertionError('GPU forbidden')):
            result = service.step()
        self.assertEqual(result['counts'], {'PENDING': 2})
        old_status = service._retained_closeout_cache[self.plan['plan_id']][1]
        self.assertEqual(old_status['schema'], 'fixture.original-evaluator.v1')
        self.assertEqual(old_status['plans'], 1)
        self.assertFalse((self.folder / 'qualification.json').exists())
        self.assertFalse((newer / 'qualification.json').exists())
        self.assertEqual(list(newer.glob('outcome-*.json')), [])
        for name, raw in original.items(): self.assertEqual((self.folder / name).read_bytes(), raw)
        self.assertEqual(json.loads(self.path.read_text()), self.plan)

    def test_original_deadline_still_closes_the_original_trial(self):
        self.plan['start_ms'] -= 2 * 86400000
        self.plan['outcome_end_ms'] -= 2 * 86400000
        self.write_plan_and_outcomes()
        self.current['retained_futile_plan_closeouts'][0]['plan_id'] = self.plan['plan_id']
        result = self.run_old()
        self.assertEqual(result['counts'], {'REJECTED': 1})
        qualification = json.loads((self.folder / 'qualification.json').read_text())
        self.assertEqual(qualification['plan_id'], self.plan['plan_id'])
        self.assertEqual(qualification['candidate_id'], self.plan['candidate_id'])
        self.assertEqual(json.loads(self.path.read_text()), self.plan)

    def test_unscored_prediction_cannot_be_discarded_by_light_closeout(self):
        (self.folder / 'prediction-003.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'UNSCORED_PREDICTION'): self.run_old()
        self.assertFalse((self.folder / 'qualification.json').exists())

    def test_changed_original_runtime_fails_before_outcome_writes(self):
        before = sorted(p.name for p in self.folder.iterdir())
        self.old_code.write_text(self.old_code.read_text() + '# changed after pin')
        with self.assertRaisesRegex(ValueError, 'RUNTIME_CHANGED'): self.run_old()
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), before)


if __name__ == '__main__': unittest.main()
