"""Trainer-only append identity, actual role checks, and same-plan recovery."""
import copy
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import current100k_background_guards as backgrounds
import current100k_priority_activation as activation
import continue_current100k_new_scope as continuation
from historical_training import resident_resource_guard as guard
from laya_cell import stage_current100k_warm_flat as warm
import test_current100k_priority_activation as original_tests

write = original_tests.write
sha = original_tests.sha


class BackgroundGuardTests(unittest.TestCase):
    prepare = original_tests.PreparationTests.prepare
    selection_fixture = original_tests.SelectionTests.selection_fixture
    select = original_tests.SelectionTests.select

    def setUp(self):
        original_tests.PreparationTests.setUp(self)
        old_registration = copy.deepcopy(self.config['producer_registration'])
        old_owner = json.loads(self.owner_path.read_bytes())
        old_owner['decision_attribution_registrations'] = {
            'EUR.USD': [old_registration], 'BTC.USD': [{'manifest': '/retained/btc.json', 'sha256': 'b'*64}]}
        write(self.owner_path, old_owner)
        self.wrapper['original_config_sha256'] = sha(self.owner_path)
        self.guard_root = self.root / 'frozen-guard'
        for name in self.wrapper['guard_files_sha256']:
            write(self.guard_root / 'historical_training' / name, Path(guard.__file__).with_name(name).read_bytes())
        self.old_trainer = write(self.guard_root / 'trainer.json', self.wrapper)
        self.evaluator = write(self.guard_root / 'evaluator.json', self.wrapper)
        manifest = json.loads(Path(old_registration['manifest']).read_bytes())
        manifest.update(execution_domain='PAPER', prepared_from_manifest_sha256=old_registration['sha256'])
        new_manifest = write(self.root / 'new-producer.json', manifest)
        self.registration = {'manifest': str(new_manifest), 'sha256': sha(new_manifest)}
        self.new_owner = copy.deepcopy(old_owner)
        self.new_owner['decision_attribution_registrations']['EUR.USD'].append(self.registration)
        self.new_owner_path = write(self.home / '.config/laya/new-owner.json', self.new_owner)
        self.new_wrapper = {**self.wrapper, 'original_config': str(self.new_owner_path),
                            'original_config_sha256': sha(self.new_owner_path)}
        self.wrapper_path = write(self.home / '.config/laya/new-wrapper.json', self.new_wrapper)
        def record(path):
            return {'wrapper': str(path), 'wrapper_sha256': sha(path), 'guard_import_root': str(self.guard_root)}
        self.document = {'schema': backgrounds.SCHEMA, 'profile': backgrounds.PROFILE,
            'producer_registration': self.registration, 'prior_trainer': record(self.old_trainer),
            'roles': {'trainer': record(self.wrapper_path), 'evaluator': record(self.evaluator)}}
        self.binding_path = self.root / 'background-guards.json'
        self.config['producer_registration'] = self.registration
        self.plan.update(producer_registration=self.registration, registration_scope='NEW_PRODUCER_SCOPE')
        self.stage['registration_scope'] = 'NEW_PRODUCER_SCOPE'
        write(self.plan_path, self.plan)
        self.publish()

    def publish(self):
        write(self.new_owner_path, self.new_owner)
        self.new_wrapper['original_config_sha256'] = sha(self.new_owner_path)
        write(self.wrapper_path, self.new_wrapper)
        self.document['roles']['trainer']['wrapper_sha256'] = sha(self.wrapper_path)
        write(self.binding_path, self.document)
        self.reference = {'path': str(self.binding_path), 'sha256': sha(self.binding_path)}
        for value in (self.config, self.stage):
            value.update(guard_config=str(self.wrapper_path), guard_config_sha256=sha(self.wrapper_path),
                         background_guards=self.reference)
        write(self.config_path, self.config)
        self.stage['configs']['registrar']['sha256'] = sha(self.config_path)

    def load(self):
        return backgrounds.load_binding(self.reference, self.registration, str(self.wrapper_path), sha(self.wrapper_path))

    def shared_fixture(self):
        request, properties, processes = original_tests.PreparationTests.shared_fixture(self)
        binding = self.load()
        for offset, (role, unit) in enumerate(activation.SHARED_UNITS.items()):
            bound = binding['roles'][role]; expected = request['roles'][role]
            argv = [bound['owner']['python'], '-B', '-m', 'historical_training.resident_resource_guard',
                    '--config', bound['wrapper'], '--role', role]
            expected.update(old_wrapper=bound['wrapper'], old_wrapper_sha256=bound['wrapper_sha256'], old_argv=argv)
            expected['old_environment']['PYTHONPATH'] = bound['pythonpath']
            properties[unit]['ExecStart'] = ('{ path=' + argv[0] + ' ; argv[]=' + ' '.join(argv)
                + ' ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }')
            properties[unit]['Environment'] = 'PYTHONPATH=' + bound['pythonpath']
            processes[101+offset]['cmdline'] = b'\0'.join(item.encode() for item in argv)+b'\0'
            processes[101+offset]['environ'] = ('PYTHONPATH='+bound['pythonpath']).encode()+b'\0INVOCATION_ID=invocation-'+role.encode()+b'\0'
        return request, properties, processes

    def test_exact_append_uses_real_import_root_outside_new_wrapper_directory(self):
        loaded = self.load()
        self.assertEqual(loaded['roles']['trainer']['guard_import_root'], str(self.guard_root))
        self.assertNotEqual(Path(loaded['roles']['trainer']['wrapper']).parent / 'research', self.guard_root)
        self.assertEqual(loaded['roles']['evaluator']['owner']['decision_attribution_registrations']['EUR.USD'],
                         self.new_owner['decision_attribution_registrations']['EUR.USD'][:-1])

    def test_only_requested_producer_and_exact_append_order_are_allowed(self):
        for mutation in ('reverse', 'extra', 'other_instrument', 'runtime'):
            with self.subTest(mutation=mutation):
                original = copy.deepcopy(self.new_owner)
                if mutation == 'reverse': self.new_owner['decision_attribution_registrations']['EUR.USD'].reverse()
                elif mutation == 'extra': self.new_owner['decision_attribution_registrations']['EUR.USD'].append({'sha256':'c'*64})
                elif mutation == 'other_instrument': self.new_owner['decision_attribution_registrations']['BTC.USD'] = []
                else: self.new_owner['unreviewed_model_option'] = True
                self.publish()
                with self.assertRaisesRegex(ValueError, 'ONLY_TRAINER_EUR_APPEND_ALLOWED'): self.load()
                self.new_owner = original
        self.publish()
        with self.assertRaisesRegex(ValueError, 'PROFILE_OR_PRODUCER_CHANGED'):
            backgrounds.load_binding(self.reference, {'manifest':'/other','sha256':'f'*64}, str(self.wrapper_path), sha(self.wrapper_path))

    def test_evaluator_change_or_role_swap_cannot_borrow_trainer_permission(self):
        self.document['roles']['trainer'], self.document['roles']['evaluator'] = (
            self.document['roles']['evaluator'], self.document['roles']['trainer'])
        write(self.binding_path, self.document)
        self.reference['sha256'] = sha(self.binding_path)
        with self.assertRaisesRegex(ValueError, 'PRIMARY_MUST_BE_APPENDED_TRAINER'): self.load()

    def test_unchanged_old_wrapper_is_not_append_proof(self):
        self.new_owner['decision_attribution_registrations']['EUR.USD'].pop()
        self.publish()
        with self.assertRaisesRegex(ValueError, 'ONLY_TRAINER_EUR_APPEND_ALLOWED'): self.load()

    def test_policy_change_and_changed_guard_bytes_refuse(self):
        self.new_wrapper['manual_hold_path'] += '.changed'; self.publish()
        with self.assertRaisesRegex(ValueError, 'ORIGINAL_ROLE_POLICY_OR_EVALUATOR_CHANGED'): self.load()
        self.new_wrapper['manual_hold_path'] = self.wrapper['manual_hold_path']; self.publish()
        write(self.guard_root / 'historical_training/common.py', b'# changed\n')
        with self.assertRaisesRegex(ValueError, 'ACTUAL_GUARD_SOURCE_CHANGED'): self.load()

    def test_wrong_config_relative_import_root_is_not_accepted(self):
        self.document['roles']['trainer']['guard_import_root'] = str(self.wrapper_path.parent / 'research')
        self.publish()
        with self.assertRaises(FileNotFoundError): self.load()

    def test_candidate_and_dropins_preserve_both_role_owners(self):
        request, _, _ = self.shared_fixture(); candidate = self.prepare()
        selected, documents = activation.selection_dropins(request, candidate)
        for role, owner_path in (('trainer', self.new_owner_path), ('evaluator', self.owner_path)):
            choice = activation.role_candidate(candidate, role)
            config = json.loads(Path(choice['wrapper']).read_bytes())
            self.assertEqual(config['original_config'], str(owner_path))
            self.assertEqual(config['value_priority']['plan_path'], str(self.plan_path))
            self.assertEqual(selected['roles'][role]['old_wrapper'], choice['wrapper'])
            self.assertTrue(any(choice['wrapper'].encode() in raw for raw in documents.values()))
        self.assertNotEqual(candidate['role_wrappers']['trainer']['wrapper'], candidate['role_wrappers']['evaluator']['wrapper'])
        self.assertEqual(candidate['wrapper'], candidate['role_wrappers']['trainer']['wrapper'])
        with self.assertRaisesRegex(ValueError, 'EXPLICIT_BACKGROUND_ROLE_REQUIRED'):
            activation.check_background_wrapper(candidate, self.evaluator, sha(self.evaluator))

    def test_precheck_verifies_direct_guard_root_and_actual_processes(self):
        request, _, processes = self.shared_fixture(); before = set(self.root.rglob('*'))
        self.assertEqual(activation.precheck_shared_priority(self.stage, self.config, request)['service_actions'], [])
        self.assertEqual(backgrounds.verify_loaded_roles(self.load())['services_changed'], [])
        self.assertEqual(set(self.root.rglob('*')), before)
        processes[101]['environ'] = b'PYTHONPATH=/wrong\0'
        with self.assertRaisesRegex(ValueError, 'ACTUAL_ROLE_IDENTITY_CHANGED'):
            backgrounds.verify_loaded_roles(self.load())

    def test_legacy_path_does_not_gain_distinct_wrapper_permission(self):
        request, _, _ = self.shared_fixture()
        self.config.pop('background_guards'); self.stage.pop('background_guards')
        write(self.config_path, self.config); self.stage['configs']['registrar']['sha256'] = sha(self.config_path)
        with self.assertRaisesRegex(ValueError, 'ORIGINAL_GUARD_IMPORT_SELECTION_CHANGED|REQUIRES_DISTINCT_CANDIDATE'):
            activation.precheck_shared_priority(self.stage, self.config, request)

    def test_initial_install_refuses_before_unit_effect_when_trainer_still_old(self):
        request, properties, _ = self.shared_fixture()
        properties[activation.SHARED_UNITS['trainer']]['ExecStart'] = properties[
            activation.SHARED_UNITS['trainer']]['ExecStart'].replace(str(self.wrapper_path), str(self.old_trainer))
        with patch.object(continuation, 'service_units', return_value={}), patch.object(continuation, 'install_units') as mutate:
            with self.assertRaisesRegex(ValueError, 'LOADED_ROLE_CHANGED'):
                continuation.install_continuation(Path('/request'), 'f'*64, {}, {'background_guards': self.load()})
            mutate.assert_not_called()

    def test_same_plan_resume_with_trainer_selected_evaluator_drained_preserves_lists(self):
        request, candidate, _, _, calls, signals, _ = self.selection_fixture()
        original = activation.subprocess.run
        def partial_start(argv, **kwargs):
            if argv[:3] == ['systemctl', '--user', 'start']:
                original(argv[:3]+[activation.SHARED_UNITS['trainer']], **kwargs)
                raise OSError('evaluator start result unavailable')
            return original(argv, **kwargs)
        with patch.object(activation.subprocess, 'run', side_effect=partial_start):
            with self.assertRaisesRegex(OSError, 'evaluator start result unavailable'): self.select(request, candidate)
        result = self.select(request, candidate)
        self.assertEqual(signals, [101, 102])
        self.assertEqual(result['roles']['trainer']['wrapper'], candidate['role_wrappers']['trainer']['wrapper'])
        self.assertEqual(result['roles']['evaluator']['wrapper'], candidate['role_wrappers']['evaluator']['wrapper'])
        self.assertEqual(json.loads(self.new_owner_path.read_bytes()), self.new_owner)
        self.assertEqual(len(json.loads(self.owner_path.read_bytes())['decision_attribution_registrations']['EUR.USD']), 1)
        count = len(calls); self.select(request, candidate); self.assertEqual(len(calls), count)

    def test_same_plan_mixed_selected_and_original_roles_never_signal_or_revert_selected_trainer(self):
        request, candidate, properties, processes, _, signals, documents = self.selection_fixture()
        with patch.object(activation, '_select_observed', side_effect=OSError('before first effect')):
            with self.assertRaisesRegex(OSError, 'before first effect'): self.select(request, candidate)
        selected, _ = activation.selection_dropins(request, candidate)
        expected = selected['roles']['trainer']; unit = activation.SHARED_UNITS['trainer']
        for path, raw in documents.items():
            if unit in path: write(path, raw).chmod(0o600)
        p = properties[unit]; argv = expected['old_argv']
        p.update(ExecStart='{ path='+argv[0]+' ; argv[]='+' '.join(argv)
            +' ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }',
            Environment='PYTHONPATH='+expected['old_environment']['PYTHONPATH'],
            DropInPaths=' '.join(path for path in expected['files'] if path != expected['fragment_path']),
            MainPID='201', InvocationID='selected-trainer')
        processes.pop(101)
        processes[201] = {'cmdline':b'\0'.join(item.encode() for item in argv)+b'\0',
            'environ':p['Environment'].encode()+b'\0INVOCATION_ID=selected-trainer\0',
            'cgroup':('0::'+p['ControlGroup']+'\n').encode()}
        result = self.select(request, candidate)
        self.assertEqual(signals, [102])
        self.assertEqual(result['roles']['trainer']['pid_key'][0], '201')
        self.assertEqual(json.loads(self.new_owner_path.read_bytes()), self.new_owner)

    def test_fit_stage_freezes_binding_in_the_hashed_config_before_warm_staging(self):
        old = self.root / 'old-fit-runtime'
        launcher = write(old / 'bin/current100k-value-launch.py', b'# original bounded launcher\n')
        native = write(self.root / 'native', b'fixture-native')
        template = {'runtime_directory':str(old), 'runtime_sha256':{'bin/current100k-value-launch.py':sha(launcher)},
                    'python':'/usr/bin/python3', 'readout_profile':'fixed-test-profile'}
        root = self.root / 'control'; root.mkdir(mode=0o700)
        request = {'controller_root':str(root), 'source_commit':'pinned-source', 'fit_output_root':str(self.root/'fit-output'),
            'fit_template':'/old/template.json', 'fit_template_sha256':'f'*64,
            'guard_config':str(self.wrapper_path), 'guard_config_sha256':sha(self.wrapper_path), 'background_guards':self.reference}
        inputs = {'source':self.root, 'owner':self.new_owner, 'wrapper':self.new_wrapper, 'template':template,
                  'prep':{'consumer_config':'/consumer.json','consumer_config_sha256':'c'*64}}
        readiness = {'dataset_path':'/frozen32.json','dataset_sha256':'d'*64, 'producer_manifest_sha256':self.registration['sha256']}
        proof = {'state':'CPU_CLOSURE_AND_ACCEPTED_HIP_INPUTS_VERIFIED','rows':32,
                 'gpu_loaded':False,'gate_admission_granted':False,'fit_started':False}
        with patch.object(warm, 'assemble_files', return_value={'bin/hepta-trader-neuron-native':native}), \
                patch.object(continuation, 'child', return_value=subprocess.CompletedProcess([],0,json.dumps(proof),'')), \
                patch.object(continuation, 'source_check'):
            _, stage, config = continuation.stage_fit(request, inputs, readiness)
        self.assertEqual(config['background_guards'], self.reference)
        self.assertEqual(json.loads(Path(stage['config']).read_bytes())['background_guards'], self.reference)
        self.assertEqual(stage['config_sha256'], sha(stage['config']))

    def test_warm_role_configuration_carries_exact_binding_and_does_not_inherit_stale_binding(self):
        prior = {'schema':'heptatrader.current100k-forward-worker-config.v1', 'automatic_trading':False,
            'actor_update':False, 'period_sha256':'period', 'oms_path':'/oms',
            'decision_attribution_registrations':{}, 'worker_read_only_paths':[], 'background_guards':{'stale':True}}
        fit = {'period_sha256':'period', 'hip_runtime':None, 'python':'/python', 'guard_config':'/guard',
            'guard_config_sha256':'g', 'source_commit':'source', 'runtime_directory':'/fit', 'background_guards':self.reference}
        consumer = {'period_sha256':'period', 'reward_config':{'oms_path':'/oms'}, 'registration':self.registration}
        args = (prior, fit, consumer, {'journal_directory':'/journal'}, Path('/release'), {}, Path('/forward'), Path('/candidate'), Path('/data'))
        self.assertEqual(warm.role_configuration(*args)['background_guards'], self.reference)
        fit.pop('background_guards')
        self.assertNotIn('background_guards', warm.role_configuration(*args))


if __name__ == '__main__':
    unittest.main()
