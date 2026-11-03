from __future__ import annotations
import copy
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import retire_current100k_pre_fit as r

FIXTURE = Path(__file__).parent / 'fixtures/current100k_pre_fit_legacy_2e65d1b'

def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = value if isinstance(value, bytes) else r.canonical(value) + b'\n'
    if path.exists():path.chmod(0o600)
    path.write_bytes(raw); path.chmod(0o600)
    return r.digest(raw)

def units(path, checksum, request):
    name = 'heptatrader-current100k-newscope-' + checksum[:12]
    return {name + '.service': '\n'.join(('[Unit]',
        'ConditionPathExists=!' + request['controller_root'] + '/CONTINUATION_COMPLETE.json',
        '[Service]', 'Type=oneshot',
        'ExecStart=/usr/bin/python3 -B ' + request['source_repository']
        + '/scripts/continue_current100k_new_scope.py --request ' + str(path) + ' --sha256 ' + checksum,
        'WorkingDirectory=' + request['source_repository'], 'Environment=PYTHONDONTWRITEBYTECODE=1', '')),
        name + '.timer': '[Timer]\nOnCalendar=*-*-* *:*:00\nUnit=' + name + '.service\n'}

class LegacyCompatibility(unittest.TestCase):
    def test_actual_request_bytes_keep_the_frozen_sha(self):
        raw = (FIXTURE / 'CONTINUATION_REQUEST.json').read_bytes()
        self.assertEqual(r.digest(raw), '2e65d1be19d60dea5704fa6826fd3935bef162322722ef74ec335c3fb6c19c6a')
        request = json.loads(raw)
        self.assertEqual(request['source_commit'], '61165afac115ba9642f6e70e6bd26a9676d1f4ba')
        self.assertEqual(request['preparation_request_sha256'], '2229c79c7d4b0b5b88532106dbbbbc42bcce6103386c5508f718b461d152ea4a')

    def test_actual_old_check_uses_original_executor_and_unit_inventory(self):
        raw=(FIXTURE/'CONTINUATION_REQUEST.json').read_bytes(); request=json.loads(raw)
        proof=json.loads((FIXTURE/'original-check-only-proof.json').read_bytes())
        path=Path(request['controller_root'])/'CONTINUATION_REQUEST.json'; checksum=r.digest(raw)
        with patch.object(r,'repository') as repository, patch.object(r,'command',return_value=json.dumps(proof)) as run:
            result=r.original_check(path,checksum,request,r.now_ms()+10000)
        self.assertEqual(result,proof)
        self.assertEqual(run.call_args.args[0],['/usr/bin/python3','-B',
            '/home/alex/work/heptatrader-current100k-continuation-v2/scripts/continue_current100k_new_scope.py',
            '--request',str(path),'--sha256',checksum,'--check-only'])
        self.assertEqual(repository.call_args.args[:2],(Path(request['source_repository']),request['source_commit']))
        self.assertEqual(run.call_args.kwargs['env']['PYTHONPATH'],':'.join(request['source_repository']+'/'+p
            for p in ('research','research/neuron_learning','scripts')))
        self.assertIn('CPUQuota=150%',next(v for k,v in result['unit_candidates'].items() if k.endswith('.service')))
        self.assertNotIn('hepta_price_bootstrap_gate','\n'.join(result['unit_candidates'].values()))
        self.assertNotIn('precheck_current100k_registration',r.__dict__)
        self.assertEqual((FIXTURE/'CONTINUATION_REQUEST.json').read_bytes(),raw)

    def test_changed_executor_proof_is_not_accepted(self):
        raw=(FIXTURE/'CONTINUATION_REQUEST.json').read_bytes(); request=json.loads(raw)
        proof=json.loads((FIXTURE/'original-check-only-proof.json').read_bytes()); proof['source_commit']='0'*40
        with patch.object(r,'repository'),patch.object(r,'command',return_value=json.dumps(proof)):
            with self.assertRaisesRegex(ValueError,'ORIGINAL_CPU_PROOF'):
                r.original_check(Path(request['controller_root'])/'CONTINUATION_REQUEST.json',r.digest(raw),request,r.now_ms()+10000)

class RetirementFixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.clock=1793609221115; self.addCleanup(patch.stopall)
        patch.object(r,'now_ms',return_value=self.clock).start()
        patch.object(Path,'home',return_value=self.root/'home').start()
        patch.object(r,'repository').start()
        self.root_v2=self.root/'v2'; self.root_v1=self.root/'v1'; self.prep_root=self.root/'preparation'
        self.consumer_root=self.root/'consumer'; self.output=self.root/'retirement'
        old_manifest=self.root/'old-producer.json'
        producer={'schema':'heptatrader.attribution-producer.v1','instrument':'EUR.USD','execution_domain':'PAPER',
            'period_sha256':'p'*64,'behavior_profile':'ORIGINAL'}
        old_sha=put(old_manifest,producer)
        consumer_path=self.root/'consumer-config.json'
        consumer={'registration':{'manifest':str(old_manifest),'sha256':old_sha},'period_sha256':'p'*64,'output_root':str(self.consumer_root)}
        consumer_sha=put(consumer_path,consumer)
        prior_path=self.root/'historical/plans/prior/plan.json'
        prior_sha=put(prior_path,{'plan_id':'prior','behavior_scope':{'behavior_model_id':'model'},'trial':4})
        prep_path=self.prep_root/'PREPARATION_REQUEST.json'
        prep={'minimum_rows':32,'selection_policy':'FIRST_32_CHRONOLOGICAL_CLOSED_NEW_PRODUCER_V1',
            'registration_scope':'NEW_PRODUCER_SCOPE','source_commit':'prep-source',
            'consumer_config':str(consumer_path),'consumer_config_sha256':consumer_sha,
            'producer_registration':consumer['registration'],'legacy_plans_sha256':{str(prior_path):prior_sha},
            'prior_plan_id':'prior','expected_trial':5}
        prep_sha=put(prep_path,prep); put(self.prep_root/'preparation.lock',b'')
        base={'schema':r.CONTINUATION_SCHEMA,'trade_authority':False,'old_trials_modified':False,
            'preparation_request':str(prep_path),'preparation_request_sha256':prep_sha,'preparation_source_commit':'prep-source'}
        self.v1={**base,'controller_root':str(self.root_v1),'fit_output_root':str(self.root/'fit-v1'),
            'forward_root':str(self.root/'forward-v1'),'source_repository':str(self.root/'source-v1'),'source_commit':'source1'}
        self.v1path=self.root_v1/'CONTINUATION_REQUEST.json'; self.v1sha=put(self.v1path,self.v1)
        put(self.root_v1/'continuation.lock',b'')
        self.v2={**base,'worker_profile':'cpu-standby-v2','controller_root':str(self.root_v2),
            'fit_output_root':str(self.root/'fit-v2'),'forward_root':str(self.root/'forward-v2'),
            'source_repository':str(self.root/'source-v2'),'source_commit':'source2',
            'supersedes_continuation':{'request':str(self.v1path),'sha256':self.v1sha}}
        self.v2path=self.root_v2/'CONTINUATION_REQUEST.json'; self.v2sha=put(self.v2path,self.v2)
        put(self.root_v2/'continuation.lock',b'')
        binding={'request_sha256':self.v2sha,'supersedes_continuation':self.v2['supersedes_continuation'],
                 'preparation_request_sha256':prep_sha}
        put(self.root_v2/'PREDECESSOR_HANDOFF_INTENT.json',binding)
        put(self.root_v2/'PREDECESSOR_HANDOFF.json',{'state':'PREDECESSOR_TIMER_RETIRED_NEW_TIMER_VERIFIED',
            'binding':binding,'preserved_cohort_sha256':{}})
        self.control={'state':r.WAITING,'training_started':False,'trial_registered':False,'source_commit':'source2',
            'preparation_request_sha256':prep_sha,'producer_manifest_sha256':old_sha,'distinct_settled_rows':2,'at_ms':self.clock}
        put(self.root_v2/'status.json',self.control)
        rows=[]
        for i in range(2):
            row={'behavior_scope':{'producer_manifest_sha256':old_sha,'model_id':'model'},'period_sha256':'p'*64,
                 'input_id':f'input-{i}','reward_id':f'reward-{i}','episode_id':f'episode-{i}'}
            rows.append({**row,'row_id':r.digest(r.canonical(row))})
        dataset={'schema':'heptatrader.current100k-value-dataset.v1','producer_manifest_sha256':old_sha,
                 'period_sha256':'p'*64,'synthetic_rewards':0,'old25k_rewards':0,'trade_authority':False,'rows':rows}
        self.dataset={**dataset,'fingerprint':r.digest(r.canonical(dataset))}
        self.dataset_path=self.consumer_root/'incremental-v1/dataset-current.json'
        dataset_sha=put(self.dataset_path,self.dataset)
        self.consumer_status={'state':'READY_VALUE_LABELS_NOT_EFFICACY','at_ms':self.clock,'input_checked_at_ms':self.clock,
            'dataset_path':str(self.dataset_path),'dataset_sha256':dataset_sha,'row_count':2,'observed_original_requests':20,'bound_opens':3}
        put(self.consumer_root/'status.json',self.consumer_status)
        next_manifest=self.root/'new-producer.json'
        next_sha=put(next_manifest,{**producer,'prepared_from_manifest_sha256':old_sha,'price_bootstrap_gate':{'profile':'candidate'}})
        self.args=SimpleNamespace(request=self.v2path,sha256=self.v2sha,expected_source_commit='helper-source',
            next_producer_manifest=next_manifest,next_producer_sha256=next_sha,output_directory=self.output,
            deadline_ms=self.clock+300000,retire=False)
        self.proofs={}; self.native={}
        for request,path,checksum in ((self.v1,self.v1path,self.v1sha),(self.v2,self.v2path,self.v2sha)):
            proof={'unit_candidates':units(path,checksum,request)}; self.proofs[checksum]=proof
            for name,text in proof['unit_candidates'].items():
                unit=self.root/'home/.config/systemd/user'/name; put(unit,text.encode())
                value={'LoadState':'loaded','FragmentPath':str(unit),'DropInPaths':'','NeedDaemonReload':'no',
                    'MainPID':'0','ActiveState':'inactive','SubState':'dead','UnitFileState':'static'}
                if name.endswith('.service'):
                    settings=dict(line.split('=',1) for line in text.splitlines() if '=' in line)
                    value.update(ExecStart='{ path=/usr/bin/python3 ; argv[]='+settings['ExecStart']+' ; }',
                        WorkingDirectory=request['source_repository'],Type='oneshot',Environment='PYTHONDONTWRITEBYTECODE=1')
                else:
                    value.update(UnitFileState='disabled' if checksum==self.v1sha else 'enabled',
                        ActiveState='inactive' if checksum==self.v1sha else 'active',
                        SubState='dead' if checksum==self.v1sha else 'waiting',NextElapseUSecRealtime='later')
                self.native[name]=value
        patch.object(r,'original_check',side_effect=lambda path,sha,request,deadline:copy.deepcopy(self.proofs[sha])).start()
        patch.object(r,'native_unit',side_effect=lambda name,deadline:copy.deepcopy(self.native[name])).start()
        self.actions=[]; self.fail_disable=False; self.fail_reload=False
        patch.object(r,'command',side_effect=self.command).start()

    def command(self,argv,deadline,**kw):
        self.actions.append(argv)
        if argv[:4]==['systemctl','--user','disable','--now']:
            name=argv[4]; self.native[name].update(UnitFileState='disabled',ActiveState='inactive',SubState='dead')
            if self.fail_disable: raise subprocess.TimeoutExpired(argv,1)
        elif argv==['systemctl','--user','daemon-reload']:
            if self.fail_reload: raise subprocess.TimeoutExpired(argv,1)
            data=r.inputs(self.args); guard=r.guard_spec(data,self.proofs[self.v2sha])
            name=next(n for n in self.proofs[self.v2sha]['unit_candidates'] if n.endswith('.service'))
            self.native[name]['DropInPaths']=str(guard[0]); self.native[name]['NeedDaemonReload']='no'
        else: self.fail('unexpected mutation command '+str(argv))
        return ''

    def test_read_only_check_makes_no_files_or_systemd_changes(self):
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result=r.run(self.args)
        self.assertEqual(result['state'],'PRE_FIT_RETIREMENT_ELIGIBLE_NO_MUTATION')
        self.assertEqual(result['old_scope']['settled_rows_preserved'],2)
        self.assertEqual(result['original_lock_order'],[str(self.root_v2/'continuation.lock'),
            str(self.root_v1/'continuation.lock'),str(self.prep_root/'preparation.lock')])
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertEqual(self.actions,[])

    def test_each_original_lock_is_exclusive_and_never_force_stopped(self):
        for path in (self.root_v2/'continuation.lock',self.root_v1/'continuation.lock',self.prep_root/'preparation.lock'):
            with self.subTest(path=path),path.open('rb') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaisesRegex(ValueError,'ORIGINAL_LOCK_BUSY'):r.run(self.args)
        self.assertEqual(self.actions,[])

    def test_all_progress_artifacts_refuse_retirement(self):
        targets=[self.prep_root/name for name in ('FROZEN_NEW_PRODUCER_32.json','READY_FOR_GUARDED_FIT.json')]
        targets += [self.root_v2/name for name in ('fit-stage','fit-attempts','warm-stage','WARM_STAGE_BOUND.json',
            'SELECTION_REQUEST.json','registration-wake','WAKE_INSTALLED.json','CONTINUATION_COMPLETE.json','unrecognized')]
        targets += [Path(self.v2[key]) for key in ('fit_output_root','forward_root')]
        for target in targets:
            with self.subTest(path=target):
                put(target,{})
                with self.assertRaises(ValueError):r.run(self.args)
                target.unlink()
        self.assertEqual(self.actions,[])

    def test_symlink_even_to_absent_effect_path_is_rejected(self):
        Path(self.v2['forward_root']).symlink_to(self.root/'missing')
        with self.assertRaises(ValueError):r.run(self.args)

    def test_started_nonwaiting_or_stale_controller_refuses(self):
        for key,value in (('training_started',True),('trial_registered',True),('state','BLOCKED'),('distinct_settled_rows',32),('at_ms',self.clock-180001)):
            with self.subTest(key=key):
                put(self.root_v2/'status.json',{**self.control,key:value})
                with self.assertRaisesRegex(ValueError,'FRESH_WAITING'):r.run(self.args)
        put(self.root_v2/'status.json',self.control)

    def test_fresh_consumer_and_same_producer_rows_required(self):
        put(self.consumer_root/'status.json',{**self.consumer_status,'input_checked_at_ms':self.clock-180001})
        with self.assertRaisesRegex(ValueError,'FRESH_CONSUMER'):r.run(self.args)
        put(self.consumer_root/'status.json',self.consumer_status)
        changed=copy.deepcopy(self.dataset);changed['rows'][0]['behavior_scope']['producer_manifest_sha256']='other'
        changed['fingerprint']=r.digest(r.canonical({k:v for k,v in changed.items() if k!='fingerprint'}))
        sha=put(self.dataset_path,changed)
        put(self.consumer_root/'status.json',{**self.consumer_status,'dataset_sha256':sha})
        with self.assertRaisesRegex(ValueError,'OLD_ROW_IDENTITY'):r.run(self.args)

    def test_retire_preserves_old_bytes_disables_exact_timer_and_adds_condition(self):
        originals={str(p):p.read_bytes() for root in (self.root_v1,self.root_v2,self.prep_root,self.consumer_root)
                   for p in root.rglob('*') if p.is_file()}
        self.args.retire=True;result=r.run(self.args)
        self.assertEqual(result['state'],'PRE_FIT_RETIRED')
        self.assertFalse(result['training_started']);self.assertFalse(result['trial_registered'])
        self.assertFalse(result['continuation_complete_written']);self.assertFalse(result['driver_activation_performed'])
        self.assertEqual(result['old_scope']['counted_as_successor_training_rows'],0)
        self.assertEqual(self.actions,[['systemctl','--user','disable','--now','heptatrader-current100k-newscope-'+self.v2sha[:12]+'.timer'],
                                      ['systemctl','--user','daemon-reload']])
        for path,raw in originals.items():self.assertEqual(Path(path).read_bytes(),raw)
        self.assertEqual((self.output/'OLD_SCOPE_DATASET.json').read_bytes(),self.dataset_path.read_bytes())
        self.assertTrue((self.output/'RETIREMENT_INTENT.json').is_file())
        self.assertTrue((self.output/'PRE_FIT_RETIRED.json').is_file())
        guard=Path(result['service_guard']['path'])
        self.assertIn(('ConditionPathExists=!'+str(self.output/'RETIREMENT_INTENT.json')).encode(),guard.read_bytes())
        self.assertFalse((self.root_v2/'CONTINUATION_COMPLETE.json').exists())

    def test_repeat_complete_is_read_only_and_detects_accidental_reenable(self):
        self.args.retire=True;r.run(self.args);self.actions.clear()
        self.assertTrue(r.run(self.args)['rechecked']);self.assertEqual(self.actions,[])
        timer=next(n for n in self.proofs[self.v2sha]['unit_candidates'] if n.endswith('.timer'))
        self.native[timer].update(UnitFileState='enabled',ActiveState='active')
        with self.assertRaisesRegex(ValueError,'TIMER_NOT_RETIRED'):r.run(self.args)
        self.assertEqual(self.actions,[])

    def test_unknown_disable_keeps_intent_no_complete_and_explicit_resume(self):
        self.args.retire=True;self.fail_disable=True
        with self.assertRaises(subprocess.TimeoutExpired):r.run(self.args)
        self.assertTrue((self.output/'RETIREMENT_INTENT.json').exists())
        self.assertFalse((self.output/'PRE_FIT_RETIRED.json').exists())
        self.fail_disable=False;self.args.retire=False
        self.assertEqual(r.run(self.args)['state'],'PRE_FIT_RETIREMENT_INTENT_REQUIRES_EXPLICIT_RESUME')
        self.args.resume_intent_sha256=r.digest((self.output/'RETIREMENT_INTENT.json').read_bytes())
        self.args.retire=True;self.assertEqual(r.run(self.args)['state'],'PRE_FIT_RETIRED')

    def test_unknown_reload_can_resume_only_exact_guard(self):
        self.args.retire=True;self.fail_reload=True
        with self.assertRaises(subprocess.TimeoutExpired):r.run(self.args)
        self.assertFalse((self.output/'PRE_FIT_RETIRED.json').exists())
        self.fail_reload=False
        self.args.resume_intent_sha256=r.digest((self.output/'RETIREMENT_INTENT.json').read_bytes())
        result=r.run(self.args);self.assertEqual(result['state'],'PRE_FIT_RETIRED')
        put(Path(result['service_guard']['path']),b'[Unit]\nConditionPathExists=/not-the-intent\n')
        with self.assertRaisesRegex(ValueError,'RETIREMENT_GUARD_CHANGED'):r.run(self.args)

    def test_changed_snapshot_is_not_rewritten_on_resume(self):
        self.args.retire=True;self.fail_disable=True
        with self.assertRaises(subprocess.TimeoutExpired):r.run(self.args)
        self.fail_disable=False;put(self.output/'OLD_SCOPE_DATASET.json',b'{}\n')
        with self.assertRaisesRegex(ValueError,'PRESERVED_SCOPE_SNAPSHOT_CHANGED'):r.run(self.args)

    def test_unknown_dropin_or_original_unit_change_rejected(self):
        name=next(n for n in self.proofs[self.v2sha]['unit_candidates'] if n.endswith('.service'))
        self.native[name]['DropInPaths']='/unexpected.conf'
        with self.assertRaisesRegex(ValueError,'LOADED_UNIT_CHANGED'):r.run(self.args)
        self.native[name]['DropInPaths']=''
        put(self.root/'home/.config/systemd/user'/name,b'[Service]\nExecStart=/something-else\n')
        with self.assertRaisesRegex(ValueError,'ORIGINAL_UNIT_BYTES_CHANGED'):r.run(self.args)

    def test_no_long_or_expired_action_window(self):
        for deadline in (self.clock,self.clock-1,self.clock+r.MAX_ACTION_MS+1):
            with self.subTest(deadline=deadline):
                self.args.deadline_ms=deadline
                with self.assertRaisesRegex(ValueError,'FINITE_ACTION_DEADLINE'):r.run(self.args)
        self.assertEqual(self.actions,[])

    def test_existing_or_nested_outputs_not_reused(self):
        self.args.output_directory=self.root_v2/'retirement'
        with self.assertRaisesRegex(ValueError,'SEPARATE_CANONICAL'):r.run(self.args)
        self.args.output_directory=self.output;put(self.output/'unrelated.json',{})
        with self.assertRaisesRegex(ValueError,'PARTIAL_STAGING_WITHOUT_INTENT'):r.run(self.args)

    def test_predecessor_must_remain_idle_and_retired(self):
        name=next(n for n in self.proofs[self.v1sha]['unit_candidates'] if n.endswith('.timer'))
        self.native[name].update(UnitFileState='enabled',ActiveState='active')
        with self.assertRaisesRegex(ValueError,'TIMER_NOT_RETIRED'):r.run(self.args)
        self.assertEqual(self.actions,[])

    def test_changed_request_pin_never_repaired(self):
        put(self.v2path,{**self.v2,'source_commit':'new'})
        with self.assertRaisesRegex(ValueError,'INPUT_CHANGED'):r.run(self.args)
        self.assertEqual(self.actions,[])

    def replace_rows(self,count):
        dataset=copy.deepcopy(self.dataset);rows=[]
        for i in range(count):
            row={k:v for k,v in self.dataset['rows'][0].items() if k!='row_id'}
            row.update(input_id=f'input-{i}',reward_id=f'reward-{i}',episode_id=f'episode-{i}')
            rows.append({**row,'row_id':r.digest(r.canonical(row))})
        dataset['rows']=rows
        dataset['fingerprint']=r.digest(r.canonical({k:v for k,v in dataset.items() if k!='fingerprint'}))
        sha=put(self.dataset_path,dataset)
        put(self.consumer_root/'status.json',{**self.consumer_status,'dataset_sha256':sha,'row_count':count})

    def test_actual_scope_is_counted_dynamically_at_eleven_not_fixed_at_ten(self):
        self.replace_rows(11)
        put(self.root_v2/'status.json',{**self.control,'distinct_settled_rows':10})
        result=r.run(self.args)
        self.assertEqual(result['old_scope']['settled_rows_preserved'],11)
        self.assertEqual(len(result['old_scope']['rows']),11)
        self.assertEqual(result['old_scope']['counted_as_successor_training_rows'],0)

    def test_consumer_reaching_32_blocks_even_before_controller_catches_up(self):
        self.replace_rows(32)
        with self.assertRaisesRegex(ValueError,'OLD_SCOPE_ALREADY_HAS_32_ROWS'):r.run(self.args)
        self.assertEqual(self.actions,[])

    def test_consumer_dataset_pin_is_not_trusted_after_change(self):
        put(self.dataset_path,{**self.dataset,'rows':[]})
        with self.assertRaisesRegex(ValueError,'OLD_DATASET_IDENTITY_CHANGED'):r.run(self.args)

    def test_selected_service_busy_blocks_without_stopping_it(self):
        name=next(n for n in self.proofs[self.v2sha]['unit_candidates'] if n.endswith('.service'))
        self.native[name].update(ActiveState='activating',MainPID='123')
        self.args.retire=True
        with self.assertRaisesRegex(ValueError,'SERVICE_BUSY_NO_FORCE_STOP'):r.run(self.args)
        self.assertEqual(self.actions,[])

    def test_guard_without_bound_intent_cannot_be_adopted(self):
        data=r.inputs(self.args);guard=r.guard_spec(data,self.proofs[self.v2sha])
        put(guard[0],guard[1])
        with self.assertRaisesRegex(ValueError,'RETIREMENT_GUARD_WITHOUT_THIS_INTENT'):r.run(self.args)

    def test_new_target_must_be_separate_and_from_old_producer(self):
        target=json.loads(self.args.next_producer_manifest.read_bytes());target['prepared_from_manifest_sha256']='other'
        self.args.next_producer_sha256=put(self.args.next_producer_manifest,target)
        with self.assertRaisesRegex(ValueError,'EXPLICIT_SEPARATE_BOOTSTRAP_SUCCESSOR'):r.run(self.args)

    def interrupted_disable(self):
        self.args.retire=True;self.fail_disable=True
        with self.assertRaises(subprocess.TimeoutExpired):r.run(self.args)
        self.fail_disable=False;self.actions.clear()
        raw=(self.output/'RETIREMENT_INTENT.json').read_bytes()
        self.args.resume_intent_sha256=r.digest(raw)
        return raw

    def test_resume_after_stale_heartbeat_and_expired_original_action(self):
        initial=self.interrupted_disable()
        later=self.clock+3600000
        self.args.deadline_ms=later+300000
        self.args.expected_source_commit='reviewed-new-helper-source'
        with patch.object(r,'now_ms',return_value=later):result=r.run(self.args)
        self.assertEqual(result['state'],'PRE_FIT_RETIRED')
        self.assertEqual((self.output/'RETIREMENT_INTENT.json').read_bytes(),initial)
        self.assertEqual(result['binding']['deadline_ms'],self.clock+300000)
        self.assertEqual(result['binding']['source_commit'],'helper-source')
        review=json.loads(Path(result['completion_resume_review']['path']).read_bytes())
        self.assertEqual(review['retirement_intent_sha256'],r.digest(initial))
        self.assertEqual(review['action_deadline_ms'],later+300000)
        self.assertEqual(review['source_commit'],'reviewed-new-helper-source')
        self.assertTrue(review['initial_eligibility_revalidated_from_sealed_bytes'])
        self.assertEqual(review['current_old_scope']['controller_status_sha256'],
                         r.digest((self.root_v2/'status.json').read_bytes()))

    def test_new_deadline_without_explicit_old_intent_pin_cannot_resume(self):
        self.interrupted_disable();self.args.resume_intent_sha256=None
        later=self.clock+3600000;self.args.deadline_ms=later+300000
        with patch.object(r,'now_ms',return_value=later):
            with self.assertRaisesRegex(ValueError,'EXPLICIT_RESUME_INTENT_PIN_REQUIRED'):r.run(self.args)
        self.assertEqual(self.actions,[])
        self.assertFalse((self.output/'PRE_FIT_RETIRED.json').exists())

    def test_readonly_resume_review_accepts_stopped_status_without_mutating(self):
        initial=self.interrupted_disable();self.args.retire=False;self.args.resume_intent_sha256=None
        later=self.clock+3600000;self.args.deadline_ms=later+300000
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch.object(r,'now_ms',return_value=later):result=r.run(self.args)
        self.assertEqual(result['required_resume_intent_sha256'],r.digest(initial))
        self.assertEqual(result['proposed_resume_review']['action_deadline_ms'],later+300000)
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertEqual(self.actions,[])

    def test_resume_checks_new_side_effects_and_current_preserved_rows(self):
        self.interrupted_disable()
        put(self.prep_root/'FROZEN_NEW_PRODUCER_32.json',{})
        with self.assertRaisesRegex(ValueError,'FROZEN32_READY'):r.run(self.args)
        (self.prep_root/'FROZEN_NEW_PRODUCER_32.json').unlink()
        self.replace_rows(1)
        with self.assertRaisesRegex(ValueError,'OLD_SCOPE_WITHDRAWN_OR_REWRITTEN'):r.run(self.args)
        self.assertEqual(self.actions,[])

    def test_resume_revalidates_sealed_initial_freshness_not_just_hashes(self):
        self.interrupted_disable()
        intent=json.loads((self.output/'RETIREMENT_INTENT.json').read_bytes())
        intent['old_scope']['observed_at_ms']=self.clock+180001;intent['at_ms']=self.clock+180002
        self.args.resume_intent_sha256=put(self.output/'RETIREMENT_INTENT.json',intent)
        with self.assertRaisesRegex(ValueError,'FRESH_WAITING'):r.run(self.args)
        self.assertEqual(self.actions,[])

    def test_parent_directories_and_intent_are_synced_before_systemd(self):
        seen=[];real_fsync=os.fsync;original_command=self.command
        def sync(fd):
            seen.append(os.readlink('/proc/self/fd/'+str(fd)));return real_fsync(fd)
        def mutation(argv,deadline,**kwargs):
            self.assertIn(str(self.output.parent),seen)
            self.assertIn(str(self.output),seen)
            self.assertIn(str(self.output/'RETIREMENT_INTENT.json'),seen)
            if argv==['systemctl','--user','daemon-reload']:
                guard=r.guard_spec(r.inputs(self.args),self.proofs[self.v2sha])
                self.assertIn(str(guard[0].parent),seen)
                self.assertIn(str(guard[0].parent.parent),seen)
                self.assertIn(str(guard[0]),seen)
            return original_command(argv,deadline,**kwargs)
        self.args.retire=True
        with patch.object(os,'fsync',side_effect=sync),patch.object(r,'command',side_effect=mutation):r.run(self.args)

    def test_existing_intent_and_snapshot_files_are_resynced_before_resume_effect(self):
        self.interrupted_disable();seen=[];real_fsync=os.fsync;original_command=self.command
        def sync(fd):
            seen.append(os.readlink('/proc/self/fd/'+str(fd)));return real_fsync(fd)
        def mutation(argv,deadline,**kwargs):
            self.assertIn(str(self.output/'RETIREMENT_INTENT.json'),seen)
            self.assertIn(str(self.output/'OLD_SCOPE_DATASET.json'),seen)
            self.assertIn(str(self.output.parent),seen)
            return original_command(argv,deadline,**kwargs)
        with patch.object(os,'fsync',side_effect=sync),patch.object(r,'command',side_effect=mutation):r.run(self.args)

    def test_resync_failure_never_attempts_another_systemd_effect(self):
        self.interrupted_disable();real_fsync=os.fsync
        def sync(fd):
            if os.readlink('/proc/self/fd/'+str(fd))==str(self.output/'RETIREMENT_INTENT.json'):
                raise OSError('fixture intent fsync failed')
            return real_fsync(fd)
        with patch.object(os,'fsync',side_effect=sync):
            with self.assertRaisesRegex(OSError,'intent fsync failed'):r.run(self.args)
        self.assertEqual(self.actions,[])
        self.assertFalse((self.output/'PRE_FIT_RETIRED.json').exists())

    def test_parent_directory_fsync_failure_stops_initial_action(self):
        self.args.retire=True
        with patch.object(os,'fsync',side_effect=OSError('fixture directory fsync failed')):
            with self.assertRaisesRegex(OSError,'directory fsync failed'):r.run(self.args)
        self.assertEqual(self.actions,[])

if __name__=='__main__':unittest.main()
