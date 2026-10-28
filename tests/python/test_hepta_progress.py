"""Progress describes live authority and evidence without modifying either."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('hepta_progress',ROOT/'scripts/hepta_progress.py')
progress=importlib.util.module_from_spec(spec);spec.loader.exec_module(progress)
NOW=1792494000000

class ProgressTests(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.home=Path(temp.name)
  self.runtime=self.home/'run/heptatrader';self.state=self.home/'.local/state/heptatrader'
  self.units={n:{'Id':n,'ActiveState':'active','SubState':'running','MainPID':'7','UnitFileState':'enabled'} for n in progress.CORE_UNITS}
  for lane in ('paper','crypto'):
   self.write(self.state/(lane+'-driver/state.json'),{'resident_loop_progress':{'at_ms':NOW,'pid':7},
    'last_error':'OLD_EXECUTION_ERROR','last_error_at_ms':NOW-86400000})
 def write(self,path,data):
  path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data));return path
 def collect(self,**kwargs):
  return progress.collect(self.home,self.runtime,self.units,NOW,**kwargs)
 def service(self,name,module,cfg,active='inactive',enabled='disabled',extra=()):
  self.units[name]={'Id':name,'LoadState':'loaded','ActiveState':active,'SubState':'dead','MainPID':'0','UnitFileState':enabled,
   '_argv':['python','-m',module,'--config',str(cfg),*extra]}
 def test_history_never_becomes_current_fault_and_reads_do_not_write(self):
  paths=list(self.state.glob('*-driver/state.json'));before={p:p.read_bytes() for p in paths}
  out=self.collect();trade=out['trading']
  self.assertEqual(trade['current_blockers'],[]);self.assertEqual(trade['permission'],'NATIVE_ADMISSION_NOT_ATTESTED')
  for d in trade['drivers'].values():
   self.assertEqual(d['historical_last_error'],'OLD_EXECUTION_ERROR');self.assertFalse(d['last_error_is_current_fault'])
  self.assertEqual(before,{p:p.read_bytes() for p in paths})
 def test_old_persistent_hold_and_native_marker_remain_blocking(self):
  self.write(self.state/'operator-maintenance/maintenance-hold.json',{'created_at_ms':NOW-86400000,'reason':'operator'})
  marker=self.runtime/'control/kill-switch';marker.parent.mkdir(parents=True);marker.touch()
  out=self.collect()['trading'];reasons={r['reason'] for r in out['current_blockers']}
  self.assertEqual(out['permission'],'BLOCKED');self.assertIn('PERSISTENT_MAINTENANCE_HOLD',reasons)
  self.assertIn('NATIVE_KILL_SWITCH_PRESENT',reasons)
  self.assertTrue(out['maintenance_hold']['present'])
  self.assertEqual(out['native_stop']['origin'],'INDEPENDENT_EMPTY_NATIVE')
  self.assertFalse(out['native_stop']['creator_verified'])
  self.assertFalse(out['native_stop']['mtime_is_creation_time'])
  self.assertFalse(out['native_stop']['automatic_resume'])
 def test_native_stop_identity_is_read_only_and_does_not_claim_its_creator(self):
  marker=self.runtime/'control/kill-switch';marker.parent.mkdir(parents=True)
  for raw,origin in [(b'','INDEPENDENT_EMPTY_NATIVE'),(b'engaged\n','CLI_TAGGED_PAUSE'),
                     (b'unknown-origin','UNRECOGNIZED_INDEPENDENT_NATIVE')]:
   with self.subTest(origin=origin):
    if marker.exists():marker.chmod(0o600)
    marker.write_bytes(raw);marker.chmod(0o440);before=marker.stat()
    with mock.patch.object(progress.subprocess,'run',side_effect=AssertionError('status cannot call resume')):
     out=self.collect()['trading'];stop=out['native_stop']
    self.assertEqual(stop['origin'],origin)
    self.assertEqual(stop['metadata']['inode'],before.st_ino)
    self.assertEqual(stop['metadata']['mtime_ms'],before.st_mtime_ns//1000000)
    self.assertEqual(stop['origin_evidence'],'PAYLOAD_SHAPE_ONLY')
    self.assertFalse(stop['native_release_verified']);self.assertEqual(out['permission'],'BLOCKED')
    self.assertEqual(marker.read_bytes(),raw);self.assertEqual(marker.stat().st_ino,before.st_ino)
  marker.unlink();out=self.collect()['trading']
  self.assertEqual(out['native_stop']['origin'],'ABSENT')
  self.assertEqual(out['permission'],'NATIVE_ADMISSION_NOT_ATTESTED')
  self.assertFalse((self.state/'operator-maintenance').exists())
 def test_native_stop_never_follows_symlink_or_reads_oversized_payload(self):
  marker=self.runtime/'control/kill-switch';marker.parent.mkdir(parents=True)
  target=self.home/'unrelated-secret';target.write_bytes(b'not-a-marker')
  marker.symlink_to(target)
  with mock.patch.object(progress.os,'read',side_effect=AssertionError('no payload read')):
   symlink=progress.native_stop_observation(marker,NOW)
  self.assertTrue(symlink['present']);self.assertEqual(symlink['origin'],'UNREADABLE')
  self.assertNotIn('sha256',symlink)
  marker.unlink();marker.write_bytes(b'x'*4097)
  with mock.patch.object(progress.os,'read',side_effect=AssertionError('no oversized read')):
   oversized=progress.native_stop_observation(marker,NOW)
  self.assertTrue(oversized['present']);self.assertIn('NOT_SMALL_REGULAR',oversized['error'])
  self.assertEqual(target.read_bytes(),b'not-a-marker')
 def test_marker_replacement_during_read_cannot_reuse_the_old_classification(self):
  marker=self.runtime/'control/kill-switch';marker.parent.mkdir(parents=True);marker.touch()
  original_read=progress.os.read
  def replaced(fd,count):
   raw=original_read(fd,count);marker.unlink();marker.write_bytes(b'engaged\n');return raw
  with mock.patch.object(progress.os,'read',side_effect=replaced):
   result=progress.native_stop_observation(marker,NOW)
  self.assertEqual(result['origin'],'UNREADABLE');self.assertIn('MARKER_CHANGED',result['error'])
  self.assertNotIn('sha256',result)
 def test_pause_needs_fresh_matching_process(self):
  p=self.state/'paper-driver/state.json'
  state={'resident_loop_progress':{'at_ms':NOW,'pid':8},'entry_inference_pause':{'checked_at_ms':NOW,'reason_code':'NATIVE_KILL_SWITCH_PRESENT'}}
  self.write(p,state);out=self.collect()['trading'];self.assertEqual(out['drivers']['paper']['current_pauses'],[])
  self.assertEqual(len(out['drivers']['paper']['stale_pauses']),1)
  state['resident_loop_progress']['pid']=7;self.write(p,state)
  self.assertEqual(len(self.collect()['trading']['drivers']['paper']['current_pauses']),1)
  state['entry_inference_pause']['checked_at_ms']=NOW-300001;self.write(p,state)
  self.assertEqual(self.collect()['trading']['drivers']['paper']['current_pauses'],[])
 def fixture_learning(self):
  root=self.home/'resident';cfg={'candidate_directory':str(root/'candidates'),'evaluation_directory':str(root/'evaluation'),
   'control_directory':str(root/'control'),'runtime_directory':'frozen-runtime','objective':{'schema':'v4'}}
  status={'at_ms':NOW,'job_counts':{'COMPLETED':57},'last_candidate_id':'c1','latest_candidate_registration':{'job_id':'j1','candidate_id':'c1','plan_id':'p1'}}
  self.write(root/'control/status.json',status)
  candidate={'candidate_id':'c1','created_at_ms':NOW-1000,'training_diagnostics':{'optimizer_steps':{'applied':3},
   'action_support_diagnostic':{'ACT':0,'ESCALATE':32}},'action_head_trained':False,'action_update_steps':0,
   'learning_curve':{'training_only':True,'checkpoints':[{'epoch':0},{'epoch':4}]}}
  self.write(root/'candidates/j1/artifact/candidate.json',candidate)
  self.write(root/'evaluation/j1/progress.json',{'at_ms':NOW,'candidate_id':'c1','plan_id':'p1','state':'PENDING','observed':3})
  cfgpath=self.write(self.home/'trainer.json',cfg)
  self.service('heptatrader-laya-trainer.service','laya_cell.resident',cfgpath,active='active')
  return root,cfgpath
 def test_terminal_forward_and_real_updates_are_separate_from_job_count(self):
  root,_=self.fixture_learning()
  self.write(root/'evaluation/j1/qualification.json',{'candidate_id':'c1','plan_id':'p1','state':'REJECTED','observed':94,'censored':0})
  entry=self.collect()['learning'][0]
  self.assertEqual(entry['status']['job_counts']['COMPLETED'],57)
  self.assertEqual(entry['price']['optimizer_steps']['applied'],3);self.assertTrue(entry['price']['actual_update_proven'])
  self.assertEqual(entry['action']['optimizer_steps_applied'],0);self.assertFalse(entry['action']['head_trained'])
  self.assertEqual(entry['latest_forward']['state'],'REJECTED');self.assertEqual(entry['latest_forward']['observed'],94)
  self.assertFalse(entry['price']['future_value_proven'])
 def test_additional_forward_is_latest_while_original_result_stays_visible(self):
  root,_=self.fixture_learning()
  self.write(root/'evaluation/j1/qualification.json',{'candidate_id':'c1','plan_id':'p1','state':'REJECTED','observed':24,'censored':73})
  path=root/'control/status.json';status=json.loads(path.read_text())
  status['latest_candidate_registration'].update(trial=56,additional_evaluation_attempts=[
   {'evaluation_role':'ADDITIONAL','attempt_id':'a'*64,'plan_id':'b'*64,
    'trial':57,'trial_allocated':True,'state':'PUBLISHED'}])
  self.write(path,status)
  self.write(root/('evaluation/attempt-'+'a'*64)/'progress.json',
   {'at_ms':NOW,'candidate_id':'c1','plan_id':'b'*64,'state':'PENDING','observed':18,'censored':0})
  entry=self.collect()['learning'][0]
  self.assertEqual(entry['latest_forward']['evaluation_role'],'ADDITIONAL')
  self.assertEqual(entry['latest_forward']['trial'],57)
  self.assertEqual(entry['latest_forward']['state'],'PENDING')
  self.assertEqual(entry['latest_forward']['observed'],18)
  self.assertEqual(entry['original_forward']['evaluation_role'],'ORIGINAL')
  self.assertEqual(entry['original_forward']['state'],'REJECTED')
  self.assertEqual(entry['original_forward']['observed'],24)
 def test_deferred_request_cannot_hide_highest_allocated_forward_trial(self):
  root,_=self.fixture_learning();path=root/'control/status.json';status=json.loads(path.read_text())
  latest={'attempt_id':'c'*64,'plan_id':'d'*64,'trial':59,'trial_allocated':True,'state':'PUBLISHED'}
  older={'attempt_id':'a'*64,'plan_id':'b'*64,'trial':57,'trial_allocated':True,'state':'PUBLISHED'}
  deferred={'attempt_id':'e'*64,'trial_allocated':False,'state':'DEFERRED','checked_at_ms':NOW+1000}
  status['latest_candidate_registration'].update(trial=56,additional_evaluation_attempts=[latest,deferred,older])
  self.write(path,status)
  self.write(root/('evaluation/attempt-'+'a'*64)/'progress.json',
   {'at_ms':NOW,'candidate_id':'c1','plan_id':'b'*64,'state':'PENDING','observed':30})
  self.write(root/('evaluation/attempt-'+'c'*64)/'qualification.json',
   {'candidate_id':'c1','plan_id':'d'*64,'state':'REJECTED','observed':91})
  entry=self.collect()['learning'][0]
  self.assertEqual(entry['latest_forward']['trial'],59)
  self.assertEqual(entry['latest_forward']['plan_id'],'d'*64)
  self.assertEqual(entry['latest_forward']['state'],'REJECTED')
  status['latest_candidate_registration']['additional_evaluation_attempts']=[deferred]
  self.write(path,status);entry=self.collect()['learning'][0]
  self.assertEqual(entry['latest_forward']['evaluation_role'],'ORIGINAL')
  self.assertEqual(entry['latest_forward']['plan_id'],'p1')
 def test_additional_forward_identity_mismatch_cannot_display_original_as_latest(self):
  root,_=self.fixture_learning();path=root/'control/status.json';status=json.loads(path.read_text())
  status['latest_candidate_registration'].update(trial=56,additional_evaluation_attempts=[
   {'attempt_id':'a'*64,'plan_id':'b'*64,'trial':57,'trial_allocated':True,'state':'PUBLISHED'}])
  self.write(path,status)
  output=root/('evaluation/attempt-'+'a'*64)/'progress.json'
  for candidate,plan in [('c1','p1'),('another-candidate','b'*64)]:
   with self.subTest(candidate=candidate,plan=plan):
    self.write(output,{'at_ms':NOW,'candidate_id':candidate,'plan_id':plan,'state':'PENDING','observed':99})
    entry=self.collect()['learning'][0]
    self.assertFalse(entry['latest_forward']['available'])
    self.assertEqual(entry['latest_forward']['error'],'FORWARD_IDENTITY_MISMATCH')
    self.assertEqual(entry['latest_forward']['evaluation_role'],'ADDITIONAL')
    self.assertEqual(entry['original_forward']['plan_id'],'p1')
 def test_idle_current_consumer_and_disabled_old_config(self):
  root=self.home/'inventory';cfg=self.write(self.home/'consumer.json',{'output_root':str(root)})
  self.write(root/'status.json',{'at_ms':NOW-900000,'input_checked_at_ms':NOW,'last_full_evaluation_at_ms':NOW-900000,
    'evaluation_skipped':True,'incremental':{'new_episodes':99},'row_count':335,'last_inventory_growth_at_ms':None})
  name='heptatrader-current100k-consumer.service';self.service(name,'historical_training.consumer_launch',cfg)
  self.units[name.replace('.service','.timer')]={'ActiveState':'active','UnitFileState':'enabled'}
  self.service('heptatrader-current100k-retired.service','historical_training.consumer_launch',self.home/'missing.json')
  out=self.collect();self.assertEqual(len(out['value_inventory']),1);self.assertEqual(out['source_errors'],[])
  item=out['value_inventory'][0];self.assertEqual(item['freshness'],'FRESH');self.assertEqual(item['new_episodes_this_check'],0)
  self.assertEqual(item['last_full_evaluation_at_ms'],NOW-900000);self.assertIsNone(item['last_inventory_growth_age_ms'])
 def test_open_timing_denominator_and_tail_are_explicit(self):
  report=self.write(self.home/'timing.json',{'captured_at_ms':NOW,'open_coverage':{'total_actual_opens':303,'preopen_verified':63},
   'groups':{'late':{'request_to_signed_commit_ms':{'p99':65437}}},'signed_outcome_set_verified':True})
  value=self.collect(timing_reports=[report])['value_timing'][0]
  self.assertEqual(value['open_coverage']['total_actual_opens'],303);self.assertEqual(value['groups']['late']['request_to_signed_commit_ms']['p99'],65437)
  self.assertTrue(value['signed_outcome_set_verified'])
 def test_running_process_overrides_next_config(self):
  listing=subprocess.CompletedProcess([],0,'heptatrader-laya-trainer.service enabled\n','')
  output='Id=heptatrader-laya-trainer.service\nMainPID=321\nActiveState=active\nExecStart={ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -m laya_cell.resident --config /next.json ; ignore_errors=no ; }\n'
  details=subprocess.CompletedProcess([],0,output,'')
  actual=b'/usr/bin/python3\0-m\0laya_cell.resident\0--config\0/running.json\0'
  with mock.patch.object(progress.subprocess,'run',side_effect=[listing,details]),mock.patch.object(Path,'read_bytes',return_value=actual):
   item=progress.discover_units()['heptatrader-laya-trainer.service']
  self.assertEqual(item['configured_config_path'],'/next.json');self.assertEqual(item['running_config_path'],'/running.json')
  self.assertFalse(item['configuration_matches_process']);self.assertEqual(item['_argv'][-1],'/running.json')
 def test_escaped_paths_and_future_evidence(self):
  argv=progress.exec_argv(r'{ argv[]=/usr/bin/python3 --config /path\x20with\x20space/config.json ; ignore_errors=no ; }')
  self.assertEqual(argv[-1],'/path with space/config.json')
  self.assertEqual(progress.freshness(NOW+1,NOW,300000)['freshness'],'FUTURE')
 def test_paused_canonical_trainer_keeps_historical_completed_jobs_without_rollout(self):
  root,_=self.fixture_learning()
  path=root/'control/status.json';status=json.loads(path.read_text())
  status['job_counts']['COMPLETED']=61;self.write(path,status)
  name='heptatrader-laya-trainer.service'
  self.units[name].update(ActiveState='inactive',UnitFileState='disabled')
  self.service('heptatrader-laya-retired.service','laya_cell.resident',self.home/'missing.json')
  out=self.collect();self.assertEqual(out['source_errors'],[])
  self.assertEqual(len(out['learning']),1);entry=out['learning'][0]
  self.assertEqual(entry['status']['job_counts']['COMPLETED'],61)
  self.assertEqual(entry['service']['ActiveState'],'inactive')
  self.assertEqual(entry['service']['UnitFileState'],'disabled')
  self.assertFalse(entry['activity']['supervisor_running'])
  self.assertTrue(entry['activity']['status_is_historical'])
 def test_stale_rollout_does_not_select_unloaded_trainer_or_override_activity(self):
  _,_=self.fixture_learning();name='heptatrader-laya-trainer.service'
  self.write(self.state/'operator-maintenance/training-episode-rollout/status.json',
   {'state':'WAIT_FROZEN_TRIAL_43','trainer_paused':True,'at_ms':NOW-300001})
  self.units[name].update(ActiveState='inactive',UnitFileState='disabled',LoadState='not-found')
  self.assertEqual(self.collect()['learning'],[])
  self.units[name].update(ActiveState='active',MainPID='9',LoadState='loaded')
  out=self.collect();self.assertEqual(out['training_rollout']['freshness'],'STALE')
  self.assertTrue(out['learning'][0]['activity']['supervisor_running'])
  self.assertFalse(out['learning'][0]['activity']['status_is_historical'])
 def test_stopped_supervisor_does_not_report_fresh_error_as_current_failure(self):
  root,cfgpath=self.fixture_learning()
  guard=self.home/'guard'
  wrapper=self.write(self.home/'wrapper.json',{'original_config':str(cfgpath),'state_root':str(guard)})
  name='heptatrader-laya-trainer.service'
  self.service(name,'historical_training.resident_resource_guard',wrapper,enabled='enabled',extra=('--role','trainer'))
  self.write(guard/'trainer-admission.json',{'at_ms':NOW,'admit_new_worker':False,'reasons':['OLD_HOLD']})
  self.write(guard/'trainer-error.json',{'at_ms':NOW,'active':True,'error':'OLD_FAILURE'})
  out=self.collect();entry=out['learning'][0]
  self.assertFalse(entry['activity']['supervisor_running'])
  self.assertTrue(entry['activity']['status_is_historical'])
  self.assertTrue(entry['resource_admission']['snapshot_is_historical'])
  self.assertFalse(entry['guard_error']['current_failure'])
  rendered=progress.format_text(out)
  self.assertIn('ADMISSION historical ',rendered);self.assertNotIn('GUARD current ',rendered)
 def test_live_evaluator_retains_futile_work_diagnostics_and_stale_admission(self):
  root,cfgpath=self.fixture_learning();guard=self.home/'guard'
  self.units.pop('heptatrader-laya-trainer.service')
  wrapper=self.write(self.home/'wrapper.json',{'original_config':str(cfgpath),'state_root':str(guard)})
  name='heptatrader-laya-evaluator.service'
  self.service(name,'historical_training.resident_resource_guard',wrapper,active='active',extra=('--role','evaluator'))
  self.units[name]['MainPID']='9'
  self.write(root/'evaluation/status.json',{'at_ms':NOW,'active_prediction_workers':0,
   'futile_pending_plans':1,'new_prediction_launches_suppressed':4,'source_scan_performed':False})
  self.write(guard/'evaluator-admission.json',{'at_ms':NOW-300001,'admit_new_worker':False,'reasons':['OLD_HOLD']})
  self.write(self.state/'operator-maintenance/training-episode-rollout/status.json',{'at_ms':NOW-300001,'state':'COMMITTED'})
  out=self.collect();entry=out['learning'][0]
  self.assertTrue(entry['activity']['supervisor_running']);self.assertFalse(entry['activity']['status_is_historical'])
  self.assertTrue(entry['resource_admission']['snapshot_is_historical'])
  self.assertEqual(entry['status']['futile_pending_plans'],1)
  self.assertEqual(entry['status']['new_prediction_launches_suppressed'],4)
  self.assertFalse(entry['status']['source_scan_performed'])
  rendered=progress.format_text(out)
  self.assertIn('"active_prediction_workers":0',rendered);self.assertIn('"source_scan_performed":false',rendered)
  self.assertIn('"freshness":"STALE"',rendered.splitlines()[1])
 def test_admission_attribution_keeps_its_original_sample_age_and_scope(self):
  root,cfgpath=self.fixture_learning();guard=self.home/'guard'
  wrapper=self.write(self.home/'wrapper.json',{'original_config':str(cfgpath),'state_root':str(guard)})
  self.service('heptatrader-laya-trainer.service','historical_training.resident_resource_guard',
   wrapper,active='active',extra=('--role','trainer'))
  self.units['heptatrader-laya-trainer.service']['MainPID']='9'
  attribution={'at_ms':NOW-20000,'sample_age_ms':20000,'diagnostic_only':True,
   'partial':True,'causality':'NOT_ESTABLISHED','processes':[{'pid':123,'name':'du'}]}
  self.write(guard/'trainer-admission.json',{'at_ms':NOW,'admit_new_worker':False,
   'reasons':['HOST_FULL_MEMORY_PSI_PRESSURE'],'workload_attribution':attribution})
  out=self.collect()['learning'][0]
  self.assertEqual(out['resource_admission']['workload_attribution'],attribution)
  self.assertIn('HOST_FULL_MEMORY_PSI_PRESSURE',{row['reason'] for row in out['current_blockers']})
 def test_calendar_wait_keeps_old_persistent_gpu_hold_current_without_admission_refresh(self):
  root,cfgpath=self.fixture_learning();guard=self.home/'guard';hold=guard/'manual-hold.json'
  self.write(hold,{'created_ms':NOW-86400000,'reason':'RECURRENT_NATIVE_HIP_STARTUP_SIGSEGV'})
  wrapper=self.write(self.home/'wrapper.json',{'original_config':str(cfgpath),'state_root':str(guard),'manual_hold_path':str(hold)})
  name='heptatrader-laya-trainer.service'
  self.service(name,'historical_training.resident_resource_guard',wrapper,active='active',extra=('--role','trainer'))
  self.units[name]['MainPID']='9'
  status=root/'control/status.json'
  self.write(status,{'at_ms':NOW,'state':'HOLD_NEW_TRAINING_FORWARD_WINDOW',
   'reason':'TRAINING_AND_FORWARD_WINDOW_DO_NOT_FIT','next_training_at_ms':NOW+10000})
  admission=self.write(guard/'trainer-admission.json',{'at_ms':NOW-86400000,'admit_new_worker':False,'reasons':['OPERATOR_RESOURCE_HOLD']})
  before={p:p.read_bytes() for p in (status,hold,admission)}
  out=self.collect();entry=out['learning'][0]
  self.assertTrue(entry['resource_admission']['snapshot_is_historical'])
  self.assertEqual({r['reason'] for r in entry['current_blockers']},
   {'TRAINING_AND_FORWARD_WINDOW_DO_NOT_FIT','OPERATOR_RESOURCE_HOLD'})
  self.assertFalse(entry['resume']['automatic_resume_expected'])
  self.assertEqual(entry['resume']['next_calendar_eligible_at_ms'],NOW+10000)
  self.assertTrue(entry['manual_hold']['present'])
  self.assertEqual(before,{p:p.read_bytes() for p in before})
  self.assertIn('RECURRENT_NATIVE_HIP_STARTUP_SIGSEGV',progress.format_text(out))
  hold.unlink()
  resumed=self.collect()['learning'][0]
  self.assertNotIn('OPERATOR_RESOURCE_HOLD',{r['reason'] for r in resumed['current_blockers']})
  self.assertIsNone(resumed['resume']['automatic_resume_expected'])
  self.assertEqual(resumed['resume']['conditions_to_recheck'],['TRAINING_AND_FORWARD_WINDOW_DO_NOT_FIT'])
 def test_status_still_displays_progress_when_service_check_fails(self):
  target=self.home/'cli';target.mkdir();shutil.copyfile(ROOT/'scripts/heptatrader-user',target/'heptatrader-user')
  (target/'hepta_progress.py').write_text('print("PROGRESS_SEEN")\n')
  stub=target/'systemctl';stub.write_text('#!/bin/sh\nexit 3\n');stub.chmod(0o700)
  env=dict(os.environ,HOME=str(self.home),PATH=str(target)+':'+os.environ.get('PATH',''),XDG_RUNTIME_DIR=str(self.home/'run'))
  result=subprocess.run(['bash',str(target/'heptatrader-user'),'status'],env=env,text=True,capture_output=True,timeout=10)
  self.assertEqual(result.returncode,3);self.assertIn('PROGRESS_SEEN',result.stdout)

 def test_next_value_evaluator_is_visible_in_both_launch_modes(self):
  root=self.home/'next-value';cfg=self.write(self.home/'next-value.json',{'forward_root':str(root)})
  status=root/'evaluator-status.json'
  self.write(status,{'schema':'heptatrader.financial-forward-evaluator-status.v2','at_ms':NOW,
   'plans':1,'states':{'INSUFFICIENT_EVIDENCE':1},'observed_trades':2,'original_command_count':3,
   'canonical_agentd_accepted':False,'financial_candidate_selected':False,'actor_updated':False,'broker_orders_sent':0})
  before=status.read_bytes()
  name='heptatrader-value-next-evaluator.service'
  for module,extra in [('laya_cell.current100k_forward_worker',('--mode','evaluate')),('laya_cell.financial_forward_evaluate',())]:
   with self.subTest(module=module):
    self.service(name,module,cfg,active='active',extra=extra)
    out=self.collect();self.assertEqual(out['source_errors'],[]);self.assertEqual(out['value_prediction'],[])
    self.assertEqual(len(out['value_forward']),1);entry=out['value_forward'][0]
    self.assertEqual(entry['freshness'],'FRESH');self.assertEqual(entry['plans'],1)
    self.assertEqual(entry['states'],{'INSUFFICIENT_EVIDENCE':1})
    self.assertEqual(entry['observed_trades'],2);self.assertEqual(entry['original_command_count'],3)
    self.assertFalse(entry['financial_candidate_selected']);self.assertEqual(entry['broker_orders_sent'],0)
    rendered=progress.format_text(out);self.assertIn('VALUE forward ',rendered)
    self.assertIn('"original_command_count":3',rendered);self.assertEqual(status.read_bytes(),before)
  self.write(status,{'at_ms':NOW,'state':'WAIT_INPUT_WINDOW','reason':'BEFORE_INPUT_START','plan_id':'p-next'})
  entry=self.collect()['value_forward'][0]
  self.assertEqual(entry['state'],'WAIT_INPUT_WINDOW');self.assertEqual(entry['reason'],'BEFORE_INPUT_START')

if __name__=='__main__':unittest.main()
