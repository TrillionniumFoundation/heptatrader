"""Original forward records, causal labels and distinct ACT pair accounting."""
from copy import deepcopy
import json
import stat
import unittest

import test_laya_resident_action_v2_closed_source as original
from laya_cell import resident_closed_action_training as closed
from laya_cell.integrity import ContractError, digest, sha_file


class ClosedActionTrainingSourceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = original.ClosedOriginalParentSourceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def freeze(self):
        return closed.freeze_source(self.fixture.root, original.PARENT, original.CUTOFF)

    def test_pairs_are_distinct_original_opposite_labels_and_keep_price_inputs(self):
        source = self.freeze()
        pairs = closed.batches(source)
        flat = [row for pair in pairs for row in pair]
        self.assertEqual(len(source['closed_source']['rows']), 16)
        self.assertEqual(len(closed.calibration_rows(source)), 16)
        self.assertEqual(len(pairs), 4)
        self.assertEqual(len({r['row_id'] for r in flat}), 8)
        for act, escalate in pairs:
            self.assertIs(act['parent_correct'], True)
            self.assertIs(escalate['parent_correct'], False)
            for row in (act, escalate):
                prediction = json.loads((self.fixture.dir / ('prediction-%03d.json' % row['slot'])).read_text())
                self.assertEqual(row['state'], prediction['input'])
                self.assertEqual(row['input_id'], digest(row['state']))
                self.assertNotIn('parent_correct', row['state'])
                self.assertNotIn('target_class', row['state'])
                self.assertLessEqual(row['future_outcome_available_ms'], original.CUTOFF)
        self.assertFalse(source['policy']['price_population_filtered_by_action_labels'])

    def test_source_is_write_once_and_job_binding_is_exact(self):
        source = self.freeze()
        path = self.fixture.root / 'source.json'
        sha = closed.write_source(path, source)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o400)
        self.assertEqual(sha, sha_file(path))
        def load(**changed):
            kwargs = dict(parent_id=original.PARENT, cutoff_ms=original.CUTOFF,
                          evaluation_root=self.fixture.root)
            kwargs.update(changed)
            return closed.load_source(path, sha, **kwargs)
        self.assertEqual(load(), source)
        for changed in ({'parent_id': 'f'*64}, {'cutoff_ms': original.CUTOFF+1}):
            with self.subTest(changed=changed), self.assertRaisesRegex(ContractError, 'JOB_BINDING_CHANGED'):
                load(**changed)
        with self.assertRaises(FileExistsError):
            closed.write_source(path, source)
        with self.assertRaisesRegex(ContractError, 'SOURCE_FILE_CHANGED'):
            closed.load_source(path, '0'*64, parent_id=original.PARENT,
                cutoff_ms=original.CUTOFF, evaluation_root=self.fixture.root)

    def test_new_pending_plan_does_not_rewrite_frozen_population(self):
        source = self.freeze()
        directory = self.fixture.root / 'future-pending'
        directory.mkdir()
        plan = deepcopy(self.fixture.plan)
        plan['trial'] += 1
        plan['start_ms'] = original.CUTOFF+100
        plan['outcome_end_ms'] = original.CUTOFF+100000
        plan.pop('plan_id')
        plan['plan_id'] = digest(plan)
        original.save(directory/'plan.json', plan)
        self.assertEqual(closed.check_source(source), source)

    def test_modified_original_is_rejected_even_if_saved_source_hash_is_consistent(self):
        source = self.freeze()
        path = self.fixture.dir / 'prediction-000.json'
        prediction = json.loads(path.read_text())
        prediction['reference_mid'] = 9
        original.save(path, prediction)
        with self.assertRaisesRegex(ContractError, 'PREDICTION_CHANGED'):
            closed.check_source(source)

    def test_relabelled_artifact_cannot_replace_actual_original_parent_correctness(self):
        source = self.freeze()
        # Swap two opposite labels to preserve counts and recompute every public
        # artifact digest. Re-reading originals must still reject the claim.
        rows = source['closed_source']['rows']
        rows[0]['parent_correct'], rows[4]['parent_correct'] = False, True
        manifest = source['closed_source']['manifest']
        manifest['rows_digest'] = digest(rows)
        manifest['manifest_id'] = digest({k: v for k, v in manifest.items() if k != 'manifest_id'})
        pairs, ids = closed._selection(rows)
        source['pair_row_ids'], source['calibration_row_ids'] = pairs, ids
        source['source_id'] = digest({k: v for k, v in source.items() if k != 'source_id'})
        with self.assertRaisesRegex(ContractError, 'ORIGINAL_POPULATION_CHANGED'):
            closed.check_source(source)

    def test_reusing_or_reordering_pair_rows_is_rejected(self):
        for change in ('reuse', 'reverse'):
            source = self.freeze()
            if change == 'reuse':
                source['pair_row_ids'][1] = source['pair_row_ids'][0]
            else:
                source['pair_row_ids'][0].reverse()
            source['source_id'] = digest({k: v for k, v in source.items() if k != 'source_id'})
            with self.subTest(change=change), self.assertRaisesRegex(ContractError, 'SELECTION_CHANGED'):
                closed.check_source(source)

    def test_minimum_genuine_class_support_is_not_relaxed(self):
        path = self.fixture.dir / 'outcome-000.json'
        outcome = json.loads(path.read_text())
        outcome.update(target_class=6, return_bps=100.,
                       parent_brier=sum((p-float(i == 6))**2 for i, p in enumerate(original.PROBS)),
                       action_head_brier=2*original.ACTION[0]**2)
        original.save(path, outcome)
        with self.assertRaisesRegex(ContractError, 'INSUFFICIENT_GENUINE_SUPPORT'):
            self.freeze()

    def test_plan_closed_after_cutoff_cannot_supply_auxiliary_labels(self):
        self.fixture.plan['outcome_end_ms'] = original.CUTOFF+1
        self.fixture.plan.pop('plan_id')
        self.fixture.plan['plan_id'] = digest(self.fixture.plan)
        self.fixture._plan()
        with self.assertRaisesRegex(ContractError, 'INSUFFICIENT_GENUINE_SUPPORT'):
            self.freeze()


if __name__ == '__main__':
    unittest.main()
