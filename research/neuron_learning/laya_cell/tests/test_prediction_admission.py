"""Metadata admission regressions; no model load, fitting, forward, or live state."""
import ast
from collections import Counter
from copy import deepcopy
import importlib
import inspect
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from laya_cell import resident_action_v2_research as action
from laya_cell import resident_training_v2 as contract
from laya_cell.integrity import ContractError, digest


def raw_objective():
    # Use the producer's literal without importing its Torch training module.
    source = Path(contract.__file__).with_name('resident_train.py').read_text()
    assignment = next(node for node in ast.parse(source).body
                      if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == 'OBJECTIVE'
                              for target in node.targets))
    return ast.literal_eval(assignment.value)


def action_profile():
    # Run only the producer's metadata constructor with declared fixture inputs.
    # The preceding tensor validation/P90 computation is deliberately not run.
    function = ast.parse(inspect.getsource(action.decide_train_only_action)).body[0]
    constructor = next(node for node in function.body
                       if isinstance(node, ast.Assign)
                       and isinstance(node.targets[0], ast.Name)
                       and node.targets[0].id == 'freeze')
    seal = next(node for node in function.body
                if isinstance(node, ast.Assign)
                and isinstance(node.targets[0], ast.Subscript))
    namespace = dict(action.__dict__, cutoff_ms=1000,
                     rows=[{'row_id': f'{index:064x}'} for index in range(32)],
                     counts=Counter({0: 16, 1: 16}), admits=True, scale=2.0)
    module = ast.fix_missing_locations(ast.Module(
        body=[deepcopy(constructor), deepcopy(seal)], type_ignores=[]))
    exec(compile(module, '<producer metadata constructor>', 'exec'), namespace)
    return namespace['freeze']


def closed_action_profile(profile):
    from laya_cell import resident_closed_action_training as closed

    function = ast.parse(inspect.getsource(closed.prepare_profile)).body[0]
    # Preserve the producer's post-logit metadata updates, without reading rows
    # or evaluating logits. This is an output-field projection, not provenance.
    start = next(index for index, node in enumerate(function.body)
                 if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                 and isinstance(node.value.func, ast.Attribute)
                 and node.value.func.attr == 'pop')
    namespace = dict(closed.__dict__, profile=deepcopy(profile), source={'source_id': 'a' * 64})
    module = ast.fix_missing_locations(ast.Module(
        body=deepcopy(function.body[start:-1]), type_ignores=[]))
    exec(compile(module, '<closed producer metadata constructor>', 'exec'), namespace)
    return namespace['profile']


class PredictionMetadataTests(unittest.TestCase):
    def test_supported_producer_versions_retain_their_output_metadata(self):
        profile = action_profile()
        action._check_profile(profile)
        pair = importlib.import_module('laya_cell.resident_training_v3').pair_calibration(profile)
        for version in range(1, 9):
            with self.subTest(version=version):
                if version == 1:
                    candidate = {'objective': raw_objective()}
                else:
                    policy = importlib.import_module(f'laya_cell.resident_training_v{version}')
                    candidate = {
                        'objective': deepcopy(policy.OBJECTIVE),
                        'action_output_profile': policy.ACTION_OUTPUT,
                        'action_training_profile': deepcopy(profile if version == 2 else pair),
                        'action_optimizer_admitted': True,
                    }
                    if version >= 5:
                        candidate['action_training_profile'] = closed_action_profile(profile)
                        candidate['price_output_profile'] = policy.PRICE_OUTPUT
                before = deepcopy(candidate)
                contract.require_prediction_contract(candidate)
                self.assertEqual(candidate, before, 'metadata checking must not recalibrate')

    def test_unknown_and_raw_calibration_metadata_fail_closed(self):
        candidates = [
            {'objective': {'schema': 'heptatrader.laya-resident-objective.v999'}},
            {'objective': raw_objective(), 'action_training_profile': action_profile()},
            {'objective': raw_objective(), 'price_calibration': {'profile_id': 'undeclared'}},
        ]
        for candidate in candidates:
            with self.subTest(candidate=candidate), self.assertRaises(ContractError):
                contract.require_prediction_contract(candidate)


class PredictionAdmissionTests(unittest.TestCase):
    def test_rejected_metadata_does_not_spend_a_legacy_trial_or_publish_a_plan(self):
        from laya_cell import resident
        from laya_cell.resident_store import database, get_meta, put_meta

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = resident.Supervisor.__new__(resident.Supervisor)
            service.root = root
            service.c = {'evaluation_directory': str(root / 'evaluation')}
            service.db = database(':memory:')
            try:
                with service.db:
                    put_meta(service.db, 'evaluation_trial', 7)
                candidates = [
                    {'candidate_id': 'unknown', 'objective': {'schema': 'unknown'}},
                    {'candidate_id': 'raw-profile', 'objective': raw_objective(),
                     'action_training_profile': action_profile()},
                ]
                for candidate in candidates:
                    with self.subTest(candidate=candidate['candidate_id']):
                        before = list(service.db.iterdump())
                        with patch.object(resident, 'inspect_evaluation_queue',
                                          return_value={'pending_original_trials': 0}), \
                                patch.object(resident, 'protocol_for_job', return_value='LEGACY'), \
                                self.assertRaises(ContractError):
                            service.register_evaluation(candidate['candidate_id'], candidate, {})
                        self.assertEqual(list(service.db.iterdump()), before)
                        self.assertEqual(get_meta(service.db, 'evaluation_trial'), 7)
                        self.assertFalse((root / 'evaluation' / candidate['candidate_id'] / 'plan.json').exists())
                # Existing immutable plans keep their original replay behavior.
                folder = root / 'evaluation' / 'retained'
                folder.mkdir()
                path = folder / 'plan.json'
                original = b'{"candidate_id":"already-registered","trial":7}\n'
                path.write_bytes(original)
                service.register_evaluation('retained', {'candidate_id': 'already-registered'}, {})
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(get_meta(service.db, 'evaluation_trial'), 7)
            finally:
                service.db.close()

    def test_raw_calibration_is_rejected_before_the_model_loader(self):
        from laya_cell import resident_predict as predictor

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            child, parent = root / 'child', root / 'parent'
            child.mkdir()
            parent.mkdir()
            base_manifest = {'fixture': 'metadata-only'}
            (root / 'base-manifest.json').write_text(json.dumps(base_manifest))
            candidate = {'candidate_id': 'child', 'parent_id': 'parent',
                         'base_id': digest(base_manifest), 'objective': raw_objective(),
                         'action_training_profile': action_profile()}
            parent_record = {'candidate_id': 'parent'}
            (parent / 'candidate.json').write_text(json.dumps(parent_record))
            rule = {'slots': 1, 'slot_spacing_ms': 1000, 'input_grace_ms': 1000}
            plan = {'candidate_id': 'child', 'candidate_directory': str(child),
                    'parent_directory': str(parent), 'start_ms': 1000, 'rule': rule}
            plan['plan_id'] = digest(plan)
            path = root / 'plan.json'
            path.write_text(json.dumps(plan))
            def identity(directory):
                return candidate if Path(directory) == child else parent_record
            with patch.object(predictor, 'validate_parent', side_effect=identity), \
                    patch.object(predictor, 'declared_profile', return_value='fixture'), \
                    patch('laya_cell.resident_hip_runtime.check_plan'), \
                    patch('laya_cell.promotion.validate_plan_rule', return_value=rule), \
                    patch.object(predictor.time, 'time', return_value=1.0), \
                    patch.object(predictor, 'load_verified_laya',
                                 side_effect=AssertionError('model loader must remain untouched')) as loader:
                with self.assertRaisesRegex(ContractError, 'PREDICTION_RAW_OUTPUT_METADATA'):
                    predictor.predict({'base_directory': str(root)}, path, 0)
                loader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
