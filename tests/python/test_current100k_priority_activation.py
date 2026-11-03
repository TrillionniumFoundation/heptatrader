"""Private CPU fixtures; signature qualification remains at the original validator boundary."""
import copy, hashlib, json, os, stat, sys, tempfile, types, unittest
from pathlib import Path
from unittest.mock import patch
import current100k_priority_activation as activation
from historical_training import resident_resource_guard as guard
from laya_cell import financial_forward_evaluate as evaluate, immutable_files

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path, value):
    path=Path(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if path.exists():path.chmod(0o600)
    path.write_bytes(value if isinstance(value,bytes) else activation.canonical(value)+b'\n')
    path.chmod(0o400);return path

class PreparationTests(unittest.TestCase):
    def setUp(self):
        previous_umask = os.umask(0o077); self.addCleanup(os.umask, previous_umask)
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.home=self.root/'home';self.home.mkdir(mode=0o700)
        self.release=self.root/'worker';inventory={};pins={}
        for name in ('common.py','resident_live_source.py','resident_resource_guard.py','value_priority_admission.py'):
            relative='research/historical_training/'+name
            p=write(self.release/relative,Path(guard.__file__).with_name(name).read_bytes())
            inventory[relative]=pins[name]=sha(p)
        identity=activation.checksum(activation.canonical(inventory))
        period=write(self.root/'period.json',{'fixture':'period'})
        journal=self.root/'journal';journal.mkdir(mode=0o700)
        manifest=write(self.root/'producer.json',{'schema':'heptatrader.attribution-producer.v1',
            'instrument':'EUR.USD','period_sha256':sha(period),'trading_behavior_changed':True,'journal_directory':str(journal)})
        registration={'manifest':str(manifest),'sha256':sha(manifest)}
        self.old_plan=write(self.root/'old-plan.json',{'schema':'heptatrader.current100k-value-forward-plan.v1',
            'input_start_ms':100,'input_end_ms':200,'automatic_effect_authority':False,
            'worker_runtime_inventory_digest':'f'*64,'period_sha256':sha(period),'producer_registration':registration})
        runtime=self.root/'original-runtime';resident=write(runtime/'learn/resident.py',b'# fixture\n')
        owner={'automatic_trading':False,'period_sha256':sha(period),'runtime_directory':str(runtime),
            'runtime_sha256':{'learn/resident.py':sha(resident)},'python':'/usr/bin/python3','hip_runtime':None}
        self.owner_path=write(self.root/'resident.json',owner)
        self.wrapper={'schema':'heptatrader.resident-admission-wrapper-config.v1','trade_authority':False,
            'resource_rule':copy.deepcopy(guard.RESOURCE_RULE),'max_heavy_workers':1,'period_path':str(period),
            'period_sha256':sha(period),'state_root':str(self.root/'old-state'),
            'manual_hold_path':str(self.root/'old-state/manual-hold.json'),'original_config':str(self.owner_path),
            'original_config_sha256':sha(self.owner_path),'guard_files_sha256':pins,
            'value_priority':{'schema':'heptatrader.original-value-priority.v2',
                'plan_path':str(self.old_plan),'plan_sha256':sha(self.old_plan)}}
        self.wrapper_path=write(self.root/'trainer.json',self.wrapper)
        self.config={'automatic_trading':False,'actor_update':False,'worker_source_root':str(self.release),
            'worker_runtime_sha256':inventory,'worker_runtime_inventory_digest':identity,'source_commit':'a'*40,
            'forward_root':str(self.root/'trial5'),'guard_config':str(self.wrapper_path),
            'guard_config_sha256':sha(self.wrapper_path),'period_path':str(period),'period_sha256':sha(period),
            'producer_registration':registration,'hip_runtime':None,'python':'/usr/bin/python3'}
        self.config_path=write(self.root/'registrar.json',self.config)
        self.plan={'schema':'heptatrader.current100k-value-forward-plan.v1','registration_scope':'SAME_PRODUCER_REFIT',
            'trial':5,'candidate_id':'c'*64,'registry_root':self.config['forward_root'],
            'worker_runtime_inventory_digest':identity,'producer_registration':registration,'period_sha256':sha(period),
            'automatic_effect_authority':False,'input_start_ms':300000,'input_end_ms':400000,'hip_runtime_manifest_sha256':None}
        self.plan['plan_id']=activation.checksum(activation.canonical(self.plan))
        self.plan_path=write(Path(self.config['forward_root'])/'plans'/self.plan['plan_id']/'plan.json',self.plan)
        self.seal=write(self.plan_path.with_name('registration.json'),{'fixture':'validator boundary'})
        self.stage={'schema':'heptatrader.current100k-warm-flat-stage.v1','state':'STAGED_CPU_VERIFIED_NOT_REGISTERED',
            'trial_registered':False,'release':str(self.release),'release_id':identity,'source_commit':self.config['source_commit'],
            'forward_root':self.config['forward_root'],'expected_trial':5,'candidate_id':self.plan['candidate_id'],
            'guard_config':str(self.wrapper_path),'guard_config_sha256':sha(self.wrapper_path),
            'configs':{'registrar':{'path':str(self.config_path),'sha256':sha(self.config_path)}}}
        self.output=self.home/'.local/share/heptatrader/laya-guard/releases'
        for item in (patch.object(Path,'home',return_value=self.home),patch.object(activation,'check_source'),
                patch.object(evaluate,'validate_registration',return_value={'fixture':'candidate'})):
            value=item.start();self.addCleanup(item.stop)
            if item.attribute=='validate_registration':self.validator=value
    def prepare(self): return activation.prepare_priority(self.stage,self.config,self.plan_path,self.plan)
    def test_only_priority_paths_change_and_registrar_is_unchanged(self):
        inputs={p:p.read_bytes() for p in (self.config_path,self.wrapper_path,self.old_plan,self.plan_path,self.seal)}
        before=copy.deepcopy(self.stage);candidate=self.prepare();expected=copy.deepcopy(self.wrapper)
        expected['value_priority'].update(plan_path=str(self.plan_path),plan_sha256=sha(self.plan_path))
        self.assertEqual(json.loads(Path(candidate['wrapper']).read_bytes()),expected)
        self.assertEqual({p:p.read_bytes() for p in inputs},inputs);self.assertEqual(self.stage,before)
        self.assertFalse(candidate['activated']);self.assertFalse(candidate['registration_performed'])
        self.assertFalse(candidate['registrar_config_modified']);self.assertFalse((self.root/'old-state').exists())
        self.validator.assert_any_call(self.config,self.plan_path,self.plan)
        self.assertEqual(stat.S_IMODE(Path(candidate['wrapper']).stat().st_mode),0o400)
        self.assertEqual(stat.S_IMODE(Path(candidate['release']).stat().st_mode),0o700)
    def test_same_plan_resume_preserves_inodes(self):
        first=self.prepare();files=[p for p in Path(first['release']).rglob('*') if p.is_file()]
        before={str(p):(p.stat().st_ino,p.stat().st_mtime_ns,p.read_bytes()) for p in files}
        self.assertEqual(self.prepare(),first)
        self.assertEqual({str(p):(p.stat().st_ino,p.stat().st_mtime_ns,p.read_bytes()) for p in files},before)
        self.assertEqual(len(list(self.output.iterdir())),1)
    def test_implicit_same_producer_scope_remains_compatible_with_original_registrar(self):
        self.plan.pop('registration_scope'); write(self.plan_path,self.plan)
        self.assertEqual(self.prepare()['plan_id'],self.plan['plan_id'])
    def test_bad_signature_and_missing_seal_refuse_before_output(self):
        self.validator.side_effect=ValueError('SIGNATURE_INVALID')
        with self.assertRaisesRegex(ValueError,'SIGNATURE_INVALID'):self.prepare()
        self.assertFalse(self.output.exists());self.seal.unlink()
        with self.assertRaises(FileNotFoundError):self.prepare()
        self.assertFalse(self.output.exists())
    def test_substituted_plan_or_registrar_are_refused(self):
        with self.assertRaisesRegex(ValueError,'PLAN_OBJECT_CHANGED'):
            activation.prepare_priority(self.stage,self.config,self.plan_path,{**self.plan,'trial':4})
        with self.assertRaisesRegex(ValueError,'ORIGINAL_REGISTRAR_CONFIG_CHANGED'):
            activation.prepare_priority(self.stage,{**self.config,'guard_config':'/other'},self.plan_path,self.plan)
        self.assertFalse(self.output.exists())
    def test_resource_rule_is_enforced_by_original_guard(self):
        self.wrapper['max_heavy_workers']=2;write(self.wrapper_path,self.wrapper)
        self.config['guard_config_sha256']=self.stage['guard_config_sha256']=sha(self.wrapper_path)
        write(self.config_path,self.config);self.stage['configs']['registrar']['sha256']=sha(self.config_path)
        with self.assertRaisesRegex(ValueError,'RESOURCE_RULE_CHANGED'):self.prepare()
        self.assertFalse(self.output.exists())
    def test_changed_guard_source_refuses_before_output(self):
        write(self.release/'research/historical_training/common.py',b'# changed\n')
        with self.assertRaisesRegex(ValueError,'GUARD_SOURCE_CHANGED'):self.prepare()
        self.assertFalse(self.output.exists())
    def test_signature_record_drift_refuses_before_write(self):
        self.validator.side_effect=lambda *args:write(self.seal,{'changed':True})
        with self.assertRaisesRegex(ValueError,'INPUT_CHANGED_DURING_PREPARATION'):self.prepare()
        self.assertFalse(self.output.exists())
    def test_conflicting_wrapper_is_not_overwritten(self):
        candidate=self.prepare();path=Path(candidate['wrapper']);write(path,b'conflict\n')
        with self.assertRaisesRegex(ValueError,'IMMUTABLE_PRIORITY_CANDIDATE_CONFLICT'):self.prepare()
        self.assertEqual(path.read_bytes(),b'conflict\n')
    def test_interrupted_publication_resumes_same_release(self):
        original=immutable_files.write_immutable
        def interrupt(path,*args,**kwargs):
            if path.name=='MANIFEST.json':raise OSError('fixture interruption')
            return original(path,*args,**kwargs)
        with patch.object(immutable_files,'write_immutable',side_effect=interrupt):
            with self.assertRaisesRegex(OSError,'fixture interruption'):self.prepare()
        release=next(self.output.iterdir());before=(release/'wrapper.json').read_bytes()
        self.assertFalse((release/'MANIFEST.json').exists());candidate=self.prepare()
        self.assertEqual(candidate['release'],str(release));self.assertEqual((release/'wrapper.json').read_bytes(),before)
    def test_verification_never_repairs_missing_output(self):
        candidate=self.prepare();Path(candidate['manifest']).unlink()
        with patch.object(immutable_files,'write_immutable') as writer:
            with self.assertRaises(FileNotFoundError):
                activation.verify_prepared_priority(self.stage,self.config,self.plan_path,self.plan,candidate)
        writer.assert_not_called();self.assertFalse(Path(candidate['manifest']).exists())
    def test_equal_background_wrapper_is_only_config_proof(self):
        candidate=self.prepare();path=write(self.root/'evaluator.json',self.wrapper)
        proof=activation.check_background_wrapper(candidate,path,sha(path))
        self.assertFalse(proof['runtime_identity_verified']);self.assertFalse(proof['cooperative_drain_verified'])
        self.assertFalse(proof['activated']);self.assertEqual(proof['service_actions'],[])
    def test_distinct_owner_and_policy_are_not_silently_replaced(self):
        candidate=self.prepare()
        for field in ('state_root','policy_schema'):
            value=copy.deepcopy(self.wrapper)
            if field=='policy_schema':value['value_priority']['schema']='heptatrader.original-value-priority.v1'
            else:value[field]=str(self.root/'different-state')
            path=write(self.root/(field+'.json'),value)
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'REQUIRES_DISTINCT_CANDIDATE'):
                activation.check_background_wrapper(candidate,path,sha(path))

    def shared_fixture(self):
        import activate_prepared_driver as driver
        files={};units={};wrapper_files={};properties={};processes={}
        owner=json.loads(self.owner_path.read_bytes())
        for name in self.wrapper['guard_files_sha256']:
            write(self.root/'research/historical_training'/name,Path(guard.__file__).with_name(name).read_bytes())
        for offset,(role,unit) in enumerate(activation.SHARED_UNITS.items()):
            wrapper=write(self.root/(role+'.json'),self.wrapper)
            wrapper_files[str(wrapper)]={'text':wrapper.read_text(),'sha256':sha(wrapper)}
            fragment=write(self.home/'.config/systemd/user'/unit,b'[Service]\nRestart=on-failure\n')
            dropin=write(fragment.parent/(unit+'.d/90-resident-guard.conf'),b'# pinned original\n')
            for p in (fragment,dropin):files[str(p)]={'text':p.read_text(),'sha256':sha(p),'mode':'0o400'}
            argv=['/usr/bin/python3','-B','-m','historical_training.resident_resource_guard','--config',str(wrapper),'--role',role]
            environment='PYTHONPATH='+':'.join([str(self.root/'research'),owner['runtime_directory']+'/learn',owner['runtime_directory']+'/scripts'])
            command='{ path='+argv[0]+' ; argv[]='+' '.join(argv)+' ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }'
            pid=str(101+offset)
            import os
            group=f'/user.slice/user-{os.geteuid()}.slice/user@{os.geteuid()}.service/app.slice/'+unit
            props={'Id':unit,'LoadState':'loaded','ActiveState':'active','SubState':'running','MainPID':pid,
                'InvocationID':'invocation-'+role,'ExecStart':command,'Environment':environment,'FragmentPath':str(fragment),
                'DropInPaths':str(dropin),'WorkingDirectory':owner['runtime_directory'],'ControlGroup':group,
                'Restart':'on-failure','Type':'simple','KillMode':'control-group','KillSignal':'15',
                'TimeoutStopUSec':'20s','NeedDaemonReload':'no'}
            units[unit]={'rc':0,'properties':props};properties[unit]=props
            processes[int(pid)]={'cmdline':b'\0'.join(x.encode() for x in argv)+b'\0',
                'environ':environment.encode()+b'\0INVOCATION_ID=invocation-'+role.encode()+b'\0','cgroup':('0::'+group+'\n').encode()}
        request=activation.selection_request({'units':units,'files':files},wrapper_files,activation_deadline_ms=2000000)
        for item in (patch.object(driver,'unit_properties',side_effect=lambda unit,*args:copy.deepcopy(properties[unit])),
                patch.object(driver,'pid_key',side_effect=lambda pid:(str(pid),'start')),
                patch.object(driver,'proc_bytes',side_effect=lambda pid,name:processes[pid][name]),
                patch.object(activation.time,'time',return_value=1.0)):
            item.start();self.addCleanup(item.stop)
        return request,properties,processes

    def test_shared_precheck_binds_actual_processes_without_writes(self):
        request,_,_=self.shared_fixture();before={str(p) for p in self.root.rglob('*')}
        proof=activation.precheck_shared_priority(self.stage,self.config,request)
        self.assertEqual(proof['state'],'SHARED_PRIORITY_ORIGINAL_RUNTIME_VERIFIED_NO_WRITE')
        self.assertTrue(all(x['runtime_identity_verified'] for x in proof['roles'].values()))
        self.assertFalse(proof['activated']);self.assertFalse(proof['cooperative_drain_verified'])
        self.assertEqual(proof['service_actions'],[]);self.assertEqual({str(p) for p in self.root.rglob('*')},before)
        self.assertNotIn('MainPID',json.dumps(request));self.assertNotIn('InvocationID',json.dumps(request))
    def test_wrong_running_environment_is_not_a_static_config_success(self):
        request,_,processes=self.shared_fixture();processes[101]['environ']=b'PYTHONPATH=/wrong\0'
        with self.assertRaisesRegex(ValueError,'RUNNING_GUARD_SELECTION_CHANGED'):
            activation.precheck_shared_priority(self.stage,self.config,request)
        self.assertFalse(self.output.exists())
    def test_process_invocation_must_match_the_loaded_unit(self):
        request,_,processes=self.shared_fixture()
        processes[101]['environ']=processes[101]['environ'].replace(b'invocation-trainer',b'different')
        with self.assertRaisesRegex(ValueError,'RUNNING_GUARD_SELECTION_CHANGED'):
            activation.precheck_shared_priority(self.stage,self.config,request)
    def test_changed_original_dropin_refuses_before_registration(self):
        request,_,_=self.shared_fixture();path=next(iter(request['roles']['trainer']['files']))
        write(path,b'# changed original\n')
        with self.assertRaisesRegex(ValueError,'ORIGINAL_UNIT_BYTES_CHANGED'):
            activation.precheck_shared_priority(self.stage,self.config,request)
    def test_inactive_or_restart_always_requires_real_recovery(self):
        request,properties,_=self.shared_fixture();props=properties[activation.SHARED_UNITS['trainer']]
        props['Restart']='always'
        with self.assertRaisesRegex(ValueError,'LOADED_UNIT_POLICY_CHANGED'):
            activation.precheck_shared_priority(self.stage,self.config,request)
        props['Restart']='on-failure';props['ActiveState']='inactive';props['MainPID']='0'
        with self.assertRaisesRegex(ValueError,'ORIGINAL_CONTROLLER_NOT_RUNNING'):
            activation.precheck_shared_priority(self.stage,self.config,request)
    def test_late_same_plan_activation_keeps_original_window(self):
        request={'activation_deadline_ms':5000};plan={'input_start_ms':2000,'input_end_ms':4000}
        proof=activation.activation_window(request,plan,now_ms=3000)
        self.assertFalse(proof['activation_before_input_start']);self.assertEqual(proof['effective_deadline_ms'],3999)
        self.assertFalse(proof['original_window_modified'])
        with self.assertRaisesRegex(ValueError,'ACTIVATION_WINDOW_EXPIRED'):
            activation.activation_window(request,plan,now_ms=4000)
    def test_process_restart_during_read_is_not_a_valid_precheck(self):
        import activate_prepared_driver as driver
        request,_,_=self.shared_fixture()
        with patch.object(driver,'pid_key',side_effect=[('101','first'),('101','second')]):
            with self.assertRaisesRegex(ValueError,'CONTROLLER_CHANGED_DURING_PRECHECK'):
                activation.precheck_shared_priority(self.stage,self.config,request)

class SourceBindingTests(unittest.TestCase):
    def fixture(self,root):
        mapping={**activation.MODULE_FILES,'current100k_priority_activation':'scripts/current100k_priority_activation.py'}
        modules={};inventory={}
        for name,relative in mapping.items():
            path=write(root/relative,('# source '+name+'\n').encode());inventory[relative]=sha(path)
            module=types.ModuleType(name);module.__file__=str(path);modules[name]=module
        return {'worker_source_root':str(root),'worker_runtime_sha256':inventory,
            'worker_runtime_inventory_digest':activation.checksum(activation.canonical(inventory))},modules
    def test_same_release_source_and_outside_import_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();config,modules=self.fixture(root)
            with patch.dict(sys.modules,modules),patch.object(activation,'__file__',str(root/'scripts/current100k_priority_activation.py')):
                activation.check_source(config)
                modules['laya_cell.financial_forward_witness'].__file__='/outside/financial_forward_witness.py'
                with self.assertRaisesRegex(ValueError,'IMPORTED_SOURCE_OUTSIDE_RUNTIME'):activation.check_source(config)
    def test_old_helper_and_changed_source_cannot_borrow_new_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();config,modules=self.fixture(root)
            with self.assertRaisesRegex(ValueError,'IMPORTED_HELPER_NOT_PINNED'):activation.check_source(config)
            write(root/'scripts/precheck_current100k_registration.py',b'# changed\n')
            with patch.dict(sys.modules,modules),patch.object(activation,'__file__',str(root/'scripts/current100k_priority_activation.py')):
                with self.assertRaisesRegex(ValueError,'SOURCE_PIN_CHANGED'):activation.check_source(config)



class SelectionTests(unittest.TestCase):
    setUp = PreparationTests.setUp
    prepare = PreparationTests.prepare
    shared_fixture = PreparationTests.shared_fixture

    def selection_fixture(self):
        import activate_prepared_driver as driver
        request, properties, processes = self.shared_fixture()
        candidate = self.prepare(); selected, documents = activation.selection_dropins(request, candidate)
        calls = []; descriptors = {}; signal_pids = []
        def proc_key(pid): return (str(pid), 'start') if int(pid) in processes else None
        def open_pid(pid, flags):
            descriptor = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
            descriptors[descriptor] = pid; return descriptor
        def send(descriptor, signum):
            self.assertEqual(signum, activation.signal.SIGUSR1)
            pid = descriptors[descriptor]; signal_pids.append(pid)
            processes.pop(pid)
            p = next(p for p in properties.values() if p['MainPID'] == str(pid))
            p.update(ActiveState='inactive', SubState='dead', MainPID='0')
        def command(argv, **options):
            calls.append(argv)
            if argv == ['systemctl', '--user', 'daemon-reload']:
                for role, unit in activation.SHARED_UNITS.items():
                    expected = selected['roles'][role]; p = properties[unit]
                    p['ExecStart'] = '{ path=' + expected['old_argv'][0] + ' ; argv[]=' + ' '.join(expected['old_argv']) + ' ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }'
                    p['Environment'] = ' '.join(k + '=' + v for k, v in expected['old_environment'].items())
                    p['DropInPaths'] = ' '.join(path for path in expected['files'] if path != expected['fragment_path'])
                    p['NeedDaemonReload'] = 'no'
            elif argv[:3] == ['systemctl', '--user', 'start']:
                for offset, (role, unit) in enumerate(activation.SHARED_UNITS.items()):
                    if unit not in argv[3:]: continue
                    pid = 201 + offset; p = properties[unit]; expected = selected['roles'][role]
                    p.update(ActiveState='active', SubState='running', MainPID=str(pid), InvocationID='selected-' + role)
                    processes[pid] = {'cmdline': b'\0'.join(value.encode() for value in expected['old_argv']) + b'\0',
                        'environ': p['Environment'].encode() + b'\0INVOCATION_ID=selected-' + role.encode() + b'\0',
                        'cgroup': ('0::' + p['ControlGroup'] + '\n').encode()}
            else: raise AssertionError('unexpected native mutation:' + repr(argv))
            return types.SimpleNamespace(returncode=0, stdout='', stderr='')
        for item in (patch.object(driver, 'pid_key', side_effect=proc_key),
                     patch.object(activation.os, 'pidfd_open', side_effect=open_pid),
                     patch.object(activation.signal, 'pidfd_send_signal', side_effect=send),
                     patch.object(activation, '_empty_group', return_value=True),
                     patch.object(activation.subprocess, 'run', side_effect=command)):
            item.start(); self.addCleanup(item.stop)
        return request, candidate, properties, processes, calls, signal_pids, documents

    def select(self, request, candidate, **kwargs):
        return activation.select_shared_priority(self.stage, self.config, self.plan_path, self.plan, candidate, request, **kwargs)

    def test_natural_drain_selects_only_new_dropins_and_same_plan_resume(self):
        request, candidate, _, _, calls, signals, documents = self.selection_fixture()
        originals = {path: Path(path).read_bytes() for role in request['roles'].values() for path in role['files']}
        result = self.select(request, candidate)
        self.assertEqual(result['state'], 'SAME_PLAN_SHARED_PRIORITY_SELECTED')
        self.assertEqual(signals, [101, 102])
        self.assertTrue(all(p['wrapper'] == candidate['wrapper'] for p in result['roles'].values()))
        self.assertEqual({path: Path(path).read_bytes() for path in originals}, originals)
        self.assertEqual({path: Path(path).read_bytes() for path in documents}, documents)
        self.assertFalse(result['future_admission_granted']); self.assertFalse(result['registration_performed'])
        before_calls = copy.deepcopy(calls)
        self.select(request, candidate)
        self.assertEqual(calls, before_calls)
        self.assertEqual(signals, [101, 102])
        self.assertEqual(sum(command[:3] == ['systemctl', '--user', 'start'] for command in calls), 1)

    def test_unknown_reload_result_resumes_only_the_original_plan(self):
        request, candidate, _, _, _, signals, _ = self.selection_fixture()
        original = activation.subprocess.run
        def fail_after_reload(argv, **kwargs):
            result = original(argv, **kwargs)
            if argv[-1] == 'daemon-reload': raise OSError('lost reload result')
            return result
        with patch.object(activation.subprocess, 'run', side_effect=fail_after_reload):
            with self.assertRaisesRegex(OSError, 'lost reload result'): self.select(request, candidate)
        self.assertEqual(signals, [101, 102])
        result = self.select(request, candidate)
        self.assertEqual(result['plan_id'], self.plan['plan_id']); self.assertEqual(signals, [101, 102])
        self.assertEqual(len(list(Path(self.config['forward_root']).glob('plans/*/plan.json'))), 1)

    def test_visible_intent_after_fsync_error_never_advances_controls_without_sync(self):
        request, candidate, _, _, calls, signals, _ = self.selection_fixture()
        output = Path(self.config['forward_root']) / 'activation' / self.plan['plan_id']
        original = os.fsync
        def fail(fd):
            item = os.fstat(fd)
            if output.exists() and item.st_ino == output.stat().st_ino: raise OSError('intent directory EIO')
            return original(fd)
        with patch.object(os, 'fsync', side_effect=fail):
            for _ in range(2):
                with self.assertRaisesRegex(OSError, 'intent directory EIO'): self.select(request, candidate)
                self.assertEqual(calls, []); self.assertEqual(signals, [])
        self.assertTrue((output / 'PRIORITY_SELECTION.json').is_file())
        seen = []
        def capture(fd): seen.append(os.fstat(fd).st_ino); return original(fd)
        with patch.object(os, 'fsync', side_effect=capture): self.select(request, candidate)
        for path in (output / 'PRIORITY_SELECTION.json', output, output.parent, output.parent.parent):
            self.assertIn(path.stat().st_ino, seen)

    def test_insufficient_shared_time_refuses_before_signalling(self):
        request, candidate, _, _, calls, signals, _ = self.selection_fixture()
        with self.assertRaisesRegex(ValueError, 'INSUFFICIENT_BUDGET_BEFORE_COOPERATIVE_DRAIN'):
            self.select(request, candidate, remaining_seconds=lambda: 149.)
        self.assertEqual(signals, []); self.assertEqual(calls, [])

    def test_unit_read_must_fit_same_budget_before_and_after_read(self):
        import activate_prepared_driver as driver
        request, _, _, _, _, _, _ = self.selection_fixture()
        with patch.object(driver, 'unit_properties') as observe:
            with self.assertRaisesRegex(ValueError, 'INSUFFICIENT_BUDGET_BEFORE_UNIT_READ'):
                activation._unit_properties(activation.SHARED_UNITS['trainer'], lambda: 14.9)
        observe.assert_not_called()
        remaining = iter((20., ValueError('shared budget expired')))
        def limit():
            item = next(remaining)
            if isinstance(item, Exception): raise item
            return item
        with self.assertRaisesRegex(ValueError, 'shared budget expired'):
            activation._unit_properties(activation.SHARED_UNITS['trainer'], limit)


class SelectionDurabilityTests(unittest.TestCase):
    def test_unrelated_status_rename_does_not_change_directory_identity(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve(); selected = write(root / 'selected.json', {'fixed': True})
            raw = selected.read_bytes(); original = os.fsync; count = [0]
            def update_status(fd):
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    count[0] += 1
                    pending = root / 'status.tmp'; pending.write_text(str(count[0])); pending.replace(root / 'status.json')
                return original(fd)
            with patch.object(os, 'fsync', side_effect=update_status):
                activation._sync_selected_files({str(selected): raw}, (root,))
            self.assertEqual(count[0], 1); self.assertEqual(selected.read_bytes(), raw)

    def test_selected_file_change_still_refuses_even_when_directory_is_unchanged(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve(); selected = write(root / 'selected.json', {'fixed': True})
            raw = selected.read_bytes(); original = os.fsync
            def change_selected(fd):
                if stat.S_ISDIR(os.fstat(fd).st_mode): write(selected, {'fixed': False})
                return original(fd)
            with patch.object(os, 'fsync', side_effect=change_selected):
                with self.assertRaisesRegex(ValueError, 'SELECTION_BYTES_CHANGED_DURING_SYNC'):
                    activation._sync_selected_files({str(selected): raw}, (root,))


class SharedDrainBudgetTests(unittest.TestCase):
    setUp = PreparationTests.setUp
    prepare = PreparationTests.prepare
    shared_fixture = PreparationTests.shared_fixture
    selection_fixture = SelectionTests.selection_fixture
    select = SelectionTests.select

    def test_both_controllers_share_one_drain_deadline(self):
        request, candidate, _, _, calls, _, _ = self.selection_fixture()
        request['drain_timeout_seconds'] = 1; elapsed = [0.]
        def sleep(seconds): elapsed[0] += seconds
        with patch.object(activation.signal, 'pidfd_send_signal') as signal_call, \
                patch.object(activation.time, 'monotonic', side_effect=lambda: elapsed[0]), \
                patch.object(activation.time, 'sleep', side_effect=sleep):
            with self.assertRaisesRegex(ValueError, 'COOPERATIVE_DRAIN_TIMEOUT_SAME_PLAN_PENDING'):
                self.select(request, candidate)
        self.assertEqual(signal_call.call_count, 2)
        self.assertEqual(elapsed[0], 1.)
        self.assertEqual(calls, [])


if __name__ == '__main__': unittest.main()
