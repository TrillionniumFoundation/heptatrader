"""Progress describes live authority and evidence without modifying either."""
import copy
import hashlib
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
  self.write(root/'evaluation/j1/qualification.json',{'candidate_id':'c1','plan_id':'p1','state':'REJECTED',
   'reasons':['INSUFFICIENT_POSSIBLE_NON_TIE_POWER_SAMPLE'],'observed':94,'censored':0})
  entry=self.collect()['learning'][0]
  self.assertEqual(entry['status']['job_counts']['COMPLETED'],57)
  self.assertEqual(entry['price']['optimizer_steps']['applied'],3);self.assertTrue(entry['price']['actual_update_proven'])
  self.assertEqual(entry['action']['optimizer_steps_applied'],0);self.assertFalse(entry['action']['head_trained'])
  self.assertEqual(entry['latest_forward']['state'],'REJECTED');self.assertEqual(entry['latest_forward']['observed'],94)
  self.assertEqual(entry['latest_forward']['reasons'],['INSUFFICIENT_POSSIBLE_NON_TIE_POWER_SAMPLE'])
  self.assertFalse(entry['price']['future_value_proven'])
 def test_pending_forward_exposes_original_futility_coverage_without_early_closeout_or_causal_inference(self):
  root,_=self.fixture_learning();path=root/'evaluation/j1/progress.json'
  coverage={'schema':'heptatrader.laya-coverage-diagnostics.v1','observational_only':True,
   'frozen_rule':{'slots':24,'min_observed':22,'min_non_ties':22,'max_censored':2},
   'counts':{'OBSERVED':17,'CENSORED':6,'pending':1},
   'censor_budget':{'scope':'RECORDED_CENSORED_OUTCOMES_ONLY','remaining':0,'excess':4,'maximum_possible_observed':18},
   'non_tie_budget':{'minimum_required':22,'maximum_possible_non_ties':18,'impossible_from_recorded_outcomes':True},
   'censored_reasons':{'NO_PREDICTION_IN_FIXED_SLOT':6},
   'no_prediction_dispositions':{'FUTILITY_SUPPRESSED':3,'ADMISSION_DENIED':3},
   'first_admission_denial_reasons':{'VALUE_PRIORITY_FRESH_FLAT_ORIGINAL_REQUEST':2,
    'HOST_AVAILABLE_MEMORY_BELOW_8GIB':1,'HOST_FULL_MEMORY_PSI_PRESSURE':1},
   'first_admission_denial_scope':'FIRST_DENIAL_RECORDED_IN_OUTCOME_PER_SLOT_NOT_EVERY_ATTEMPT_OR_COMPLETE_CAUSAL_ATTRIBUTION'}
  self.write(path,{'at_ms':NOW,'candidate_id':'c1','plan_id':'p1','state':'PENDING',
   'reason':'FIXED_FUTURE_WINDOW_NOT_COMPLETE','observed':17,'censored':6,'coverage_diagnostics':coverage})
  before=path.read_bytes();out=self.collect();forward=out['learning'][0]['latest_forward']
  self.assertEqual(forward['state'],'PENDING');self.assertEqual(forward['reason'],'FIXED_FUTURE_WINDOW_NOT_COMPLETE')
  self.assertEqual(forward['coverage_diagnostics'],coverage);self.assertEqual(path.read_bytes(),before)
  rendered=progress.format_text(out)
  self.assertIn('"impossible_from_recorded_outcomes":true',rendered)
  self.assertIn(coverage['first_admission_denial_scope'],rendered)
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
    self.write(output,{'at_ms':NOW,'candidate_id':candidate,'plan_id':plan,'state':'PENDING','observed':99,
     'coverage_diagnostics':{'non_tie_budget':{'impossible_from_recorded_outcomes':True}}})
    entry=self.collect()['learning'][0]
    self.assertFalse(entry['latest_forward']['available'])
    self.assertEqual(entry['latest_forward']['error'],'FORWARD_IDENTITY_MISMATCH')
    self.assertNotIn('coverage_diagnostics',entry['latest_forward'])
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
  self.write(guard/'trainer-admission.json',{'schema':'heptatrader.resident-resource-admission.v1',
   'role':'trainer','at_ms':NOW,'admit_new_worker':False,
   'reasons':['HOST_FULL_MEMORY_PSI_PRESSURE'],'workload_attribution':attribution})
  out=self.collect()['learning'][0]
  self.assertEqual(out['resource_admission']['workload_attribution'],attribution)
  self.assertIn('HOST_FULL_MEMORY_PSI_PRESSURE',{row['reason'] for row in out['current_blockers']})
 def learning_guard_hold_fixture(self,role='trainer',reasons=('HOST_FULL_MEMORY_PSI_PRESSURE',)):
  root,cfgpath=self.fixture_learning();guard=self.home/'guard';hold=guard/'manual-hold.json'
  self.units.pop('heptatrader-laya-trainer.service')
  wrapper=self.write(self.home/'wrapper.json',{'original_config':str(cfgpath),'state_root':str(guard),'manual_hold_path':str(hold)})
  name='heptatrader-laya-'+role+'.service'
  self.service(name,'historical_training.resident_resource_guard',wrapper,active='active',extra=('--role',role))
  self.units[name]['MainPID']='9'
  if role=='trainer':
   status={'schema':'heptatrader.laya-resident-status.v1','at_ms':NOW,
    'state':'HOLD_NEW_TRAINING_RESOURCE_OR_SOURCE','current_blockers':list(reasons)+['HOLD_NEW_TRAINING_RESOURCE_OR_SOURCE']}
   path=root/'control/status.json'
  else:
   status={'schema':'heptatrader.laya-resident-evaluation-status.v2','at_ms':NOW,'plans':1,
    'counts':{'PENDING':1},'counts_include_closed_plans':True,'active_prediction_workers':0,'futile_pending_plans':0}
   path=root/'evaluation/status.json'
  self.write(path,status)
  admission=self.write(guard/(role+'-admission.json'),{'schema':'heptatrader.resident-resource-admission.v1',
   'role':role,'resource_role':role,'at_ms':NOW-100,'admit_new_worker':False,'reasons':list(reasons)})
  return name,path,admission,hold
 def test_learning_resource_refusal_has_explicit_recheck_without_launch_or_policy_copy(self):
  name,path,admission,hold=self.learning_guard_hold_fixture()
  for reason in ('HOST_FULL_MEMORY_PSI_PRESSURE','HOST_AVAILABLE_MEMORY_BELOW_8GIB','OTHER_RESEARCH_WORKER_ACTIVE'):
   with self.subTest(reason=reason):
    status=json.loads(path.read_text());status['current_blockers']=[reason,'HOLD_NEW_TRAINING_RESOURCE_OR_SOURCE'];self.write(path,status)
    report=json.loads(admission.read_text());report['reasons']=[reason];self.write(admission,report)
    before={p:p.read_bytes() for p in (path,admission)};out=self.collect();resume=out['learning'][0]['resume']
    self.assertEqual(resume['next_action'],'ORIGINAL_CONTROLLER_RECHECKS_RESOURCE_SOURCE_AND_SHARED_LEASE')
    self.assertFalse(resume['automatic_resume_expected']);self.assertFalse(resume['worker_launch_authority'])
    self.assertTrue(resume['recovery_conditions_known']);self.assertTrue(resume['recovery_conditions']['original_guard_recheck_required'])
    self.assertTrue(resume['recovery_conditions']['same_heavy_worker_lease_required'])
    condition='existing_worker_must_finish_under_original_owner' if reason=='OTHER_RESEARCH_WORKER_ACTIVE' else 'memory_and_psi_must_satisfy_original_guard'
    self.assertTrue(resume['recovery_conditions'][condition]);self.assertEqual(before,{p:p.read_bytes() for p in before})
    self.assertIn(resume['next_action'],progress.format_text(out))
  self.write(hold,{'owner':'original-owner','reason':'manual','release_conditions':'original signed closeout'})
  resume=self.collect()['learning'][0]['resume']
  self.assertEqual(resume['next_action'],'OWNER_REVIEW_EXACT_HOLD_RELEASE_CONDITIONS')
  self.assertFalse(resume['automatic_resume_expected'])
  # A trainer may apply the registration/evaluator budget under its same owner role.
  hold.unlink();status=json.loads(path.read_text());status.update(state='CANDIDATE_READY_AWAITING_FORWARD_ADMISSION',
   next_action='ORIGINAL_TRAINER_RECHECKS_SAME_CANDIDATE_FORWARD_ADMISSION');self.write(path,status)
  for resource_role in ('forward_registration','evaluator'):
   report=json.loads(admission.read_text());report['resource_role']=resource_role;self.write(admission,report)
   entry=self.collect()['learning'][0]
   self.assertFalse(entry['resource_admission']['snapshot_is_historical'])
   self.assertEqual(entry['resume']['next_action'],'ORIGINAL_TRAINER_RECHECKS_SAME_CANDIDATE_FORWARD_ADMISSION')
 def test_learning_source_refusal_needs_current_role_admission_and_viable_original_work(self):
  name,path,admission,hold=self.learning_guard_hold_fixture('evaluator',('SOURCE_CONTINUITY_NOT_READY',))
  original=json.loads(admission.read_text());resume=self.collect()['learning'][0]['resume']
  self.assertEqual(resume['next_action'],'ORIGINAL_CONTROLLER_RECHECKS_RESOURCE_SOURCE_AND_SHARED_LEASE')
  self.assertTrue(resume['recovery_conditions']['original_source_identity_freshness_and_continuity_required'])
  for changes in ({'at_ms':NOW-300001},{'at_ms':NOW+1},{'role':'value-predictor'},{'schema':'unbound'}):
   with self.subTest(changes=changes):
    self.write(admission,{**original,**changes});resume=self.collect()['learning'][0]['resume']
    self.assertNotIn('recovery_conditions',resume)
    self.assertNotEqual(resume.get('next_action'),'ORIGINAL_CONTROLLER_RECHECKS_RESOURCE_SOURCE_AND_SHARED_LEASE')
    self.assertNotIn('SOURCE_CONTINUITY_NOT_READY',{row['reason'] for row in self.collect()['learning'][0]['current_blockers']})
  self.write(admission,original);status=json.loads(path.read_text())
  for changes in ({'counts':{'REJECTED':1}},{'futile_pending_plans':1}):
   with self.subTest(closed_or_futile=changes):
    self.write(path,{**status,**changes});entry=self.collect()['learning'][0];resume=entry['resume']
    self.assertNotIn('recovery_conditions',resume)
    self.assertNotEqual(resume.get('next_action'),'ORIGINAL_CONTROLLER_RECHECKS_RESOURCE_SOURCE_AND_SHARED_LEASE')
    self.assertTrue(entry['resource_admission']['snapshot_is_historical'])
    self.assertTrue(entry['resource_admission']['superseded_by_no_viable_heavy_work'])
    self.assertNotIn('SOURCE_CONTINUITY_NOT_READY',{row['reason'] for row in entry['current_blockers']})
    self.assertNotIn('SOURCE_CONTINUITY_NOT_READY',resume['conditions_to_recheck'])
    self.write(hold,{'owner':'original-owner','reason':'manual','release_conditions':'original signed closeout'})
    entry=self.collect()['learning'][0]
    self.assertIn('OPERATOR_RESOURCE_HOLD',{row['reason'] for row in entry['current_blockers']})
    self.assertEqual(entry['resume']['next_action'],'OWNER_REVIEW_EXACT_HOLD_RELEASE_CONDITIONS');hold.unlink()
  # A newer/equal refusal or an incomplete closeout snapshot cannot erase a blocker.
  for changes,checked in (({},NOW-1),({},NOW),({'plans':2},NOW-100),
                         ({'active_prediction_workers':1},NOW-100),
                         ({'counts_include_closed_plans':False},NOW-100)):
   with self.subTest(unproven_closeout=changes,admission_at=checked):
    self.write(path,{**status,'at_ms':NOW-1,'futile_pending_plans':1,**changes})
    self.write(admission,{**original,'at_ms':checked})
    entry=self.collect()['learning'][0]
    self.assertFalse(entry['resource_admission']['snapshot_is_historical'])
    self.assertIn('SOURCE_CONTINUITY_NOT_READY',{row['reason'] for row in entry['current_blockers']})
 def test_learning_cleanup_hold_uses_original_reap_and_terminal_status_does_not_revive_it(self):
  name,path,admission,hold=self.learning_guard_hold_fixture()
  status={'schema':'heptatrader.laya-resident-status.v1','at_ms':NOW,
   'state':'WORKER_TERMINATION_PENDING','reason':'TRAINING_WORKER_NOT_REAPED','worker_pid':123,'new_worker_allowed':False}
  self.write(path,status);resume=self.collect()['learning'][0]['resume']
  self.assertEqual(resume['next_action'],'WAIT_FOR_ORIGINAL_CONTROLLER_TO_REAP_RECORDED_WORKER')
  self.assertFalse(resume['automatic_resume_expected']);self.assertEqual(resume['recovery_conditions']['worker_pid'],123)
  self.assertTrue(resume['recovery_conditions']['original_worker_must_be_reaped_without_replacing_its_locks'])
  self.write(path,{**status,'state':'FORWARD_WORK_COMPLETE_AWAITING_NEW_CANDIDATE','reason':None,
   'next_action':'NEW_CANDIDATE_AFTER_TRAINING_ADMISSION'})
  resume=self.collect()['learning'][0]['resume']
  self.assertEqual(resume['next_action'],'NEW_CANDIDATE_AFTER_TRAINING_ADMISSION');self.assertNotIn('recovery_conditions',resume)
 def test_learning_unknown_or_unproven_hold_has_read_reconcile_action_without_invented_conditions(self):
  name,path,admission,hold=self.learning_guard_hold_fixture(reasons=('UNRECOGNIZED_ADMISSION_REFUSAL',))
  original=json.loads(path.read_text());resume=self.collect()['learning'][0]['resume']
  self.assertEqual(resume['next_action'],'READ_ORIGINAL_CONTROLLER_AND_GUARD_STATUS_THEN_RECONCILE_HOLD')
  self.assertFalse(resume['recovery_conditions_known']);self.assertNotIn('recovery_conditions',resume)
  for changes in ({'at_ms':NOW-300001},{'at_ms':NOW+1}):
   self.write(path,{**original,**changes});resume=self.collect()['learning'][0]['resume']
   self.assertEqual(resume['next_action'],'REFRESH_ORIGINAL_TRAINER_STATUS');self.assertNotIn('recovery_conditions',resume)
  self.write(path,original);self.units[name].update(ActiveState='inactive',MainPID='0')
  resume=self.collect()['learning'][0]['resume']
  self.assertEqual(resume['next_action'],'READ_ORIGINAL_CONTROLLER_AND_GUARD_STATUS_THEN_RECONCILE_HOLD')
  self.assertFalse(resume['automatic_resume_expected']);self.assertNotIn('recovery_conditions',resume)
  self.units[name].update(ActiveState='active',MainPID='9')
  self.write(path,{'schema':'heptatrader.laya-resident-status.v1','at_ms':NOW,
   'state':'LEARNING_UNAVAILABLE_NO_TRADE_EFFECT','reason':'ORIGINAL_CONFIG_CHANGED'})
  admission.unlink();resume=self.collect()['learning'][0]['resume']
  self.assertEqual(resume['next_action'],'READ_ORIGINAL_CONTROLLER_AND_GUARD_STATUS_THEN_RECONCILE_HOLD')
  self.assertFalse(resume['recovery_conditions_known']);self.assertNotIn('recovery_conditions',resume)
 def test_learning_storage_hold_keeps_original_budget_and_evidence(self):
  name,path,admission,hold=self.learning_guard_hold_fixture()
  self.write(path,{'schema':'heptatrader.laya-resident-status.v1','at_ms':NOW,
   'state':'HOLD_NEW_TRAINING_STORAGE','reason':'STATE_BUDGET_FULL_NO_EVIDENCE_PRUNING'})
  resume=self.collect()['learning'][0]['resume']
  self.assertEqual(resume['next_action'],'REVIEW_ORIGINAL_STORAGE_BUDGET_WITH_EVIDENCE_RETAINED')
  self.assertTrue(resume['recovery_conditions']['original_storage_admission_must_pass'])
  self.assertTrue(resume['recovery_conditions']['existing_evidence_must_be_preserved'])
  self.assertFalse(resume['automatic_resume_expected'])
 def test_learning_unreadable_manual_hold_takes_precedence_over_resource_wait(self):
  name,path,admission,hold=self.learning_guard_hold_fixture()
  observed=progress.presence
  original=json.loads(path.read_text())
  for changes in ({},{'state':'HOLD_NEW_TRAINING_FORWARD_WINDOW','reason':'TRAINING_AND_FORWARD_WINDOW_DO_NOT_FIT',
                      'next_calendar_eligible_at_ms':NOW+10000}):
   with self.subTest(changes=changes):
    self.write(path,{**original,**changes})
    with mock.patch.object(progress,'presence',side_effect=lambda path:None if str(path)==str(hold) else observed(path)):
     resume=self.collect()['learning'][0]['resume']
    self.assertEqual(resume['next_action'],'READ_ORIGINAL_MANUAL_HOLD_AND_RECONCILE_OWNER_STATE')
    self.assertFalse(resume['recovery_conditions_known']);self.assertNotIn('recovery_conditions',resume)
    self.assertFalse(resume['automatic_resume_expected'])
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
  self.assertEqual(entry['resume']['next_action'],'OWNER_REVIEW_EXACT_HOLD_RELEASE_CONDITIONS')
  self.assertTrue(entry['manual_hold']['present'])
  self.assertEqual(before,{p:p.read_bytes() for p in before})
  self.assertIn('RECURRENT_NATIVE_HIP_STARTUP_SIGSEGV',progress.format_text(out))
  hold.unlink()
  resumed=self.collect()['learning'][0]
  self.assertNotIn('OPERATOR_RESOURCE_HOLD',{r['reason'] for r in resumed['current_blockers']})
  self.assertFalse(resumed['resume']['automatic_resume_expected'])
  self.assertEqual(resumed['resume']['next_action'],'WAIT_FOR_ORIGINAL_CALENDAR_THEN_TRAINER_RECHECKS_WINDOW_AND_GUARDS')
  self.assertEqual(resumed['resume']['conditions_to_recheck'],['TRAINING_AND_FORWARD_WINDOW_DO_NOT_FIT'])
 def test_calendar_wait_explains_next_original_recheck_without_promising_launch(self):
  root,_=self.fixture_learning();path=root/'control/status.json'
  self.units['heptatrader-laya-trainer.service']['MainPID']='9'
  status={'at_ms':NOW,'state':'HOLD_NEW_TRAINING_FORWARD_WINDOW',
   'reason':'TRAINING_AND_FORWARD_WINDOW_DO_NOT_FIT','next_calendar_eligible_at_ms':NOW+10000}
  self.write(path,status);before=path.read_bytes();out=self.collect();resume=out['learning'][0]['resume']
  action='WAIT_FOR_ORIGINAL_CALENDAR_THEN_TRAINER_RECHECKS_WINDOW_AND_GUARDS'
  self.assertEqual(resume['next_action'],action);self.assertFalse(resume['automatic_resume_expected'])
  self.assertEqual(resume['next_calendar_eligible_at_ms'],NOW+10000)
  self.assertTrue(resume['calendar_time_is_not_worker_admission']);self.assertFalse(resume['worker_launch_authority'])
  self.assertEqual(resume['calendar_recheck_conditions'],{'original_trainer_recheck_expected':True,
   'full_training_and_forward_window_must_fit':True,'original_resource_and_fresh_source_checks_required':True,
   'original_manual_hold_and_worker_cleanup_checks_required':True})
  self.assertIn(action,progress.format_text(out));self.assertEqual(path.read_bytes(),before)
  for at in (NOW,NOW-1,None):
   with self.subTest(calendar_at=at):
    self.write(path,{**status,'next_calendar_eligible_at_ms':at})
    resume=self.collect()['learning'][0]['resume']
    self.assertEqual(resume['next_action'],'ORIGINAL_TRAINER_RECHECKS_CALENDAR_WINDOW_AND_GUARDS')
    self.assertFalse(resume['automatic_resume_expected']);self.assertFalse(resume['worker_launch_authority'])
 def test_calendar_recheck_projection_requires_fresh_running_original_wait(self):
  root,_=self.fixture_learning();path=root/'control/status.json';name='heptatrader-laya-trainer.service'
  self.units[name]['MainPID']='9'
  status={'at_ms':NOW,'state':'HOLD_NEW_TRAINING_FORWARD_WINDOW',
   'reason':'TRAINING_AND_FORWARD_WINDOW_DO_NOT_FIT','next_calendar_eligible_at_ms':NOW+10000}
  for at in (NOW-300001,NOW+1):
   with self.subTest(status_at=at):
    self.write(path,{**status,'at_ms':at});resume=self.collect()['learning'][0]['resume']
    self.assertNotIn('calendar_recheck_conditions',resume)
    self.assertEqual(resume['next_action'],'REFRESH_ORIGINAL_TRAINER_STATUS')
  self.write(path,status);self.units[name].update(ActiveState='inactive',MainPID='0')
  resume=self.collect()['learning'][0]['resume']
  self.assertNotIn('calendar_recheck_conditions',resume);self.assertFalse(resume['automatic_resume_expected'])
 def test_status_still_displays_progress_when_service_check_fails(self):
  target=self.home/'cli';target.mkdir();shutil.copyfile(ROOT/'scripts/heptatrader-user',target/'heptatrader-user')
  (target/'hepta_progress.py').write_text('print("PROGRESS_SEEN")\n')
  stub=target/'systemctl';stub.write_text('#!/bin/sh\nexit 3\n');stub.chmod(0o700)
  env=dict(os.environ,HOME=str(self.home),PATH=str(target)+':'+os.environ.get('PATH',''),XDG_RUNTIME_DIR=str(self.home/'run'))
  result=subprocess.run(['bash',str(target/'heptatrader-user'),'status'],env=env,text=True,capture_output=True,timeout=10)
  self.assertEqual(result.returncode,3);self.assertIn('PROGRESS_SEEN',result.stdout)

 def test_same_candidate_wait_action_and_conditions_are_visible_without_claiming_admission(self):
  root,cfgpath=self.fixture_learning();name='heptatrader-laya-trainer.service'
  self.units[name]['MainPID']='9'
  action='ORIGINAL_TRAINER_RECHECKS_SAME_CANDIDATE_FORWARD_ADMISSION'
  conditions={'same_candidate_retry':True,'blocking_original_trials':0,
   'original_calendar_and_fresh_source_checks_required':True,
   'original_registration_stability':{'required_samples':3,'minimum_span_ms':60000},
   'waiting_reasons':['HOST_FULL_MEMORY_PSI_PRESSURE'],'current_admission_granted':False,'worker_launch_authority':False}
  status={'at_ms':NOW,'state':'CANDIDATE_READY_AWAITING_FORWARD_ADMISSION','next_action':action,
   'current_blockers':['HOST_FULL_MEMORY_PSI_PRESSURE'],
   'forward_registration_resume_conditions':conditions,'resume_conditions_to_recheck':['HOST_FULL_MEMORY_PSI_PRESSURE']}
  path=self.write(root/'control/status.json',status);before=path.read_bytes()
  out=self.collect();entry=out['learning'][0]
  self.assertEqual(entry['resume']['next_action'],action)
  self.assertEqual(entry['resume']['forward_registration_conditions'],conditions)
  self.assertFalse(entry['resume']['worker_launch_authority'])
  self.assertIn(action,progress.format_text(out));self.assertIn('minimum_span_ms',progress.format_text(out))
  self.assertEqual(path.read_bytes(),before)
  self.units[name].update(ActiveState='inactive',MainPID='0')
  stopped=self.collect()['learning'][0]
  self.assertNotIn('forward_registration_conditions',stopped['resume'])
  self.assertFalse(stopped['resume']['automatic_resume_expected'])
  self.units[name].update(ActiveState='active',MainPID='9');self.write(path,{**status,'at_ms':NOW-300001})
  stale=self.collect()['learning'][0]
  self.assertNotIn('forward_registration_conditions',stale['resume'])
  self.assertEqual(stale['resume']['next_action'],'REFRESH_ORIGINAL_TRAINER_STATUS')

 def test_terminal_hold_projection_keeps_owner_conditions_and_current_marker(self):
  root,cfgpath=self.fixture_learning();guard=self.home/'guard';hold=guard/'manual-hold.json'
  value={'created_ms':NOW-86400000,'owner':'original-owner',
   'reason':'PAUSE_NEW_BUDGET_PENDING_SAME_CANDIDATE_FORWARD','release_conditions':'exact marker after finite validation'}
  self.write(hold,value)
  wrapper=self.write(self.home/'wrapper.json',{'original_config':str(cfgpath),'state_root':str(guard),'manual_hold_path':str(hold)})
  self.service('heptatrader-laya-trainer.service','historical_training.resident_resource_guard',wrapper,extra=('--role','trainer'))
  path=root/'control/status.json';status=json.loads(path.read_text())
  status.update(state='FORWARD_WORK_COMPLETE_AWAITING_NEW_CANDIDATE',next_action='NEW_CANDIDATE_AFTER_TRAINING_ADMISSION',
   status_refresh_only=True,resource_diagnostics={'manual_hold':{**value,
    'sha256':hashlib.sha256(hold.read_bytes()).hexdigest(),
    'waiting_condition_state':'NO_PENDING_FORWARD_TRIAL',
    'next_action':'OWNER_CLOSE_TERMINAL_FORWARD_WAIT_THEN_RECHECK_TRAINING_ADMISSION'}})
  self.write(path,status);before=hold.read_bytes();entry=self.collect()['learning'][0]
  self.assertEqual(entry['manual_hold']['release_conditions'],value['release_conditions'])
  self.assertEqual(entry['manual_hold']['waiting_condition_state'],'NO_PENDING_FORWARD_TRIAL')
  self.assertEqual(entry['resume']['next_action'],'OWNER_CLOSE_TERMINAL_FORWARD_WAIT_THEN_RECHECK_TRAINING_ADMISSION')
  self.assertFalse(entry['resume']['automatic_resume_expected']);self.assertEqual(hold.read_bytes(),before)
  self.write(hold,{**value,'owner':'different-owner'})
  entry=self.collect()['learning'][0]
  self.assertNotIn('waiting_condition_state',entry['manual_hold'])
  self.assertEqual(entry['resume']['next_action'],'OWNER_REVIEW_EXACT_HOLD_RELEASE_CONDITIONS')
  # A replacement may retain owner/time/reason while changing its conditions.
  # Only the exact observed bytes can inherit the old terminal-wait projection.
  self.write(hold,{**value,'release_conditions':'new independent condition'})
  entry=self.collect()['learning'][0]
  self.assertNotIn('waiting_condition_state',entry['manual_hold'])
  self.assertEqual(entry['manual_hold']['release_conditions'],'new independent condition')
  self.assertEqual(entry['resume']['next_action'],'OWNER_REVIEW_EXACT_HOLD_RELEASE_CONDITIONS')

 def test_native_hold_resume_is_explicit_workflow_guidance_not_permission(self):
  marker=self.runtime/'control/kill-switch';marker.parent.mkdir(parents=True);marker.touch()
  trade=self.collect()['trading'];resume=trade['resume']
  self.assertEqual(resume['check_command'],'heptatrader paper-on --resume-native-hold --check')
  self.assertFalse(resume['automatic_resume']);self.assertFalse(resume['resume_authority_granted'])
  self.write(self.state/'operator-maintenance/maintenance-hold.json',{'created_at_ms':NOW,'reason':'maintenance'})
  trade=self.collect()['trading']
  self.assertEqual(trade['resume']['next_action'],'REVIEW_AND_RELEASE_EXACT_PERSISTENT_MAINTENANCE_HOLD')
  self.assertIsNone(trade['resume']['check_command']);self.assertTrue(marker.exists())

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

 def launcher_fixture(self,mode,**extra):
  role='predictor' if mode=='predict' else 'evaluator';root=self.home/('forward-'+role)
  release=self.home/('release-'+role);launcher=release/'bin/current100k-forward-launch.py'
  launcher.parent.mkdir(parents=True);launcher.write_bytes(b'raise AssertionError("progress must never execute the launcher")\n')
  canonical=lambda value:json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
  inventory={'bin/current100k-forward-launch.py':hashlib.sha256(launcher.read_bytes()).hexdigest()}
  config={'schema':'heptatrader.current100k-forward-worker-config.v1','signing_role':role,
   'automatic_trading':False,'actor_update':False,'python':'/original/pinned/python','forward_root':str(root),
   'worker_source_root':str(release),'worker_runtime_sha256':inventory,
   'worker_runtime_inventory_digest':hashlib.sha256(canonical(inventory)).hexdigest(),**extra}
  path=self.write(self.home/(role+'-'+hashlib.sha256(canonical(config)).hexdigest()+'.json'),config)
  name='heptatrader-current100k-forward-'+role+'.service'
  argv=[config['python'] if mode=='predict' else '/usr/bin/python3','-B',str(launcher),'--config',str(path),
        '--sha256',hashlib.sha256(path.read_bytes()).hexdigest(),'--mode',mode]
  self.units[name]={'Id':name,'ActiveState':'inactive','SubState':'dead','MainPID':'0','UnitFileState':'static',
                    'config_source':'CONFIGURED_NEXT_INVOCATION','_argv':argv}
  self.units[name.removesuffix('.service')+'.timer']={'ActiveState':'active','UnitFileState':'enabled'}
  self.write(root/('predictor-status.json' if mode=='predict' else 'evaluator-status.json'),
   {'at_ms':NOW,'state':'WAIT_INPUT_WINDOW','reason':'BEFORE_INPUT_START','plans':1,'new_predictions':0})
  return name,path,launcher

 def test_pinned_preexec_launcher_is_visible_before_first_tick_and_after_exec(self):
  for mode,key in [('predict','value_prediction'),('evaluate','value_forward')]:
   name,path,launcher=self.launcher_fixture(mode);before={p:p.read_bytes() for p in (path,launcher)}
   with mock.patch.object(progress.subprocess,'run',side_effect=AssertionError('read-only progress must not launch')):
    out=self.collect()
   self.assertEqual(out['source_errors'],[]);self.assertEqual(len(out[key]),1)
   self.assertEqual(out[key][0]['state'],'WAIT_INPUT_WINDOW');self.assertEqual(out[key][0]['plans'],1)
   self.assertEqual(before,{p:p.read_bytes() for p in before})
   self.units[name].update(ActiveState='active',MainPID='7',config_source='RUNNING_PROCESS_ARGV')
   self.assertEqual(len(self.collect()[key]),1)
   self.units[name]['_argv']=[self.units[name]['_argv'][0],'-B','-m','laya_cell.current100k_forward_worker','--config',str(path),'--mode',mode]
   self.assertEqual(len(self.collect()[key]),1)

 def test_launcher_name_alone_or_changed_config_inventory_bytes_cannot_create_value_progress(self):
  name,path,launcher=self.launcher_fixture('predict');original=list(self.units[name]['_argv'])
  for change in ('config_sha','role','arbitrary_path','bytes','inventory'):
   with self.subTest(change=change):
    argv=list(original);config_raw=path.read_bytes();launcher_raw=launcher.read_bytes()
    if change=='config_sha':argv[6]='0'*64
    elif change=='role':argv[8]='evaluate'
    elif change=='arbitrary_path':
     other=self.home/'arbitrary/current100k-forward-launch.py';other.parent.mkdir();other.write_bytes(launcher_raw);argv[2]=str(other)
    elif change=='bytes':launcher.write_bytes(b'changed launcher')
    else:
     config=json.loads(config_raw);config['worker_runtime_inventory_digest']='0'*64
     path.write_text(json.dumps(config));argv[6]=hashlib.sha256(path.read_bytes()).hexdigest()
    self.units[name]['_argv']=argv;out=self.collect()
    self.assertEqual(out['value_prediction'],[]);self.assertEqual(out['value_forward'],[])
    self.assertEqual(len(out['source_errors']),1);self.assertIn('FORWARD_LAUNCHER_',out['source_errors'][0]['error'])
    path.write_bytes(config_raw);launcher.write_bytes(launcher_raw)

 def test_fresh_bounded_queue_wait_has_action_and_terminal_evidence_or_zero_pending_ends_wait(self):
  root,cfgpath=self.fixture_learning();name='heptatrader-laya-trainer.service';self.units[name]['MainPID']='9'
  path=root/'control/status.json';status=json.loads(path.read_text())
  status.update(state='EVALUATING_BOUNDED_CANDIDATE_QUEUE',pending_original_trials=1)
  self.write(path,status)
  waiting='WAIT_FOR_ORIGINAL_EVALUATOR_CLOSEOUT_THEN_TRAINER_QUEUE_RECHECK'
  closed='ORIGINAL_TRAINER_RECHECKS_QUEUE_AFTER_FORWARD_CLOSEOUT'
  self.assertEqual(self.collect()['learning'][0]['resume']['next_action'],waiting)
  self.write(path,{**status,'pending_original_trials':0})
  self.assertEqual(self.collect()['learning'][0]['resume']['next_action'],closed)
  self.write(path,status)
  self.write(root/'evaluation/j1/qualification.json',{'candidate_id':'c1','plan_id':'p1','state':'REJECTED'})
  self.assertEqual(self.collect()['learning'][0]['resume']['next_action'],closed)
  hold=self.write(self.home/'manual-hold.json',{'owner':'original','reason':'NATIVE_WORKER_FATAL_SIGNAL'})
  wrapper=self.write(self.home/'wrapper.json',{'original_config':str(cfgpath),'manual_hold_path':str(hold)})
  self.units[name]['_argv']=['python','-m','historical_training.resident_resource_guard','--config',str(wrapper),'--role','trainer']
  self.assertEqual(self.collect()['learning'][0]['resume']['next_action'],'OWNER_REVIEW_EXACT_HOLD_RELEASE_CONDITIONS')
  hold.unlink();self.write(path,{**status,'at_ms':NOW-300001})
  self.assertEqual(self.collect()['learning'][0]['resume']['next_action'],'REFRESH_ORIGINAL_TRAINER_STATUS')

 def test_inactive_crypto_blocker_is_scoped_without_claiming_paper_admission(self):
  self.units['heptatrader-crypto-driver.service'].update(ActiveState='inactive',SubState='dead',MainPID='0',UnitFileState='disabled')
  out=self.collect();trade=out['trading']
  self.assertEqual(trade['permission'],'BLOCKED');self.assertEqual(trade['permission_scope'],'AGGREGATE_OF_CONFIGURED_LANES')
  self.assertEqual(trade['current_blockers'],[{'reason':'SERVICE_NOT_ACTIVE','unit':'heptatrader-crypto-driver.service',
   'state':'inactive','scope':'CRYPTO_DRIVER'}])
  self.assertFalse(trade['trade_authority_granted']);self.assertIn('CRYPTO_DRIVER',progress.format_text(out))

 def financial_hold_fixture(self):
  guard_root=self.home/'shared-guard';health=self.home/'source-health.json';hold=guard_root/'manual-hold.json'
  wrapper=self.write(self.home/'guard.json',{'schema':'heptatrader.resident-admission-wrapper-config.v1',
   'period_sha256':'a'*64,'state_root':str(guard_root),'source_health_path':str(health),
   'manual_hold_path':str(hold),'resource_rule':{'min_available_bytes':8589934592}})
  name,path,launcher=self.launcher_fixture('predict',guard_config=str(wrapper),
   guard_config_sha256=hashlib.sha256(wrapper.read_bytes()).hexdigest(),period_sha256='a'*64)
  config=json.loads(path.read_text());status=Path(config['forward_root'])/'predictor-status.json'
  self.write(status,{'state':'HOLD_SHARED_RESOURCE_OR_SOURCE','at_ms':NOW,'plan_id':'p4',
   'input_start_ms':NOW-10000,'input_end_ms':NOW+86400000,
   'front_timing':{'worker_started_at_ms':NOW-1000,'plan_id':'p4'}})
  admission=self.write(guard_root/'value-predictor-admission.json',{'at_ms':NOW-100,
   'role':'value-predictor','resource_role':'value-predictor','admit_new_worker':False,
   'reasons':['LATEST_SOURCE_QUOTE_NOT_FRESH','SOURCE_CONTINUITY_NOT_READY','SOURCE_QUALITY_HOLD_NO_REFRESH_AUTHORITY'],
   'cached_probe_diagnostic':{'cached_probe_usable':1,'cached_probe_unusable':23},
   'original_live_source_evidence':{'status':'NOT_COLLECTED_WHILE_SOURCE_PROBES_HELD','source_ttl_ms':5000}})
  self.write(health,{'schema':'heptatrader.quote-readiness-status.v1','period_sha256':'a'*64,
   'at_ms':NOW-200,'state':'HOLD_NEW_FUSION_WINDOW','usable':1,'unusable':23,
   'reasons':['INSUFFICIENT_ENDPOINT_AND_PATH_COVERAGE'],'rule':{'minimum_usable':20,'input_probes':24}})
  return name,status,admission,health,wrapper,hold

 def test_financial_hold_uses_selected_guard_and_exposes_original_owner_recovery_without_writes(self):
  name,status,admission,health,wrapper,hold=self.financial_hold_fixture()
  before={path:path.read_bytes() for path in (status,admission,health,wrapper)}
  with mock.patch.object(progress.subprocess,'run',side_effect=AssertionError('progress must not dispatch')):
   out=self.collect()
  self.assertEqual(out['source_errors'],[]);entry=out['value_prediction'][0]
  self.assertTrue(entry['guard_admission']['current_denial_observed'])
  self.assertFalse(entry['guard_admission']['subject_bound'])
  self.assertEqual(entry['guard_admission']['cached_probe_diagnostic']['cached_probe_usable'],1)
  self.assertEqual(entry['resume']['next_action'],'WAIT_FOR_ORIGINAL_SOURCE_HEALTH_THEN_FINITE_PREDICTOR_RECHECK')
  conditions=entry['resume']['recovery_conditions']
  self.assertEqual(conditions['source_health_owner'],'heptatrader-learning-source-health.service')
  self.assertEqual(conditions['observed_source_rule']['minimum_usable'],20)
  self.assertFalse(entry['resume']['worker_launch_authority'])
  self.assertIn('SOURCE_QUALITY_HOLD_NO_REFRESH_AUTHORITY',progress.format_text(out))
  self.assertEqual(before,{path:path.read_bytes() for path in before});self.assertFalse(hold.exists())

 def test_financial_psi_hold_without_counts_is_explained_only_by_this_worker_admission(self):
  name,status,admission,health,wrapper,hold=self.financial_hold_fixture()
  row=json.loads(admission.read_text());row['reasons']=['HOST_FULL_MEMORY_PSI_PRESSURE'];self.write(admission,row)
  source=json.loads(health.read_text());source.update(state='READY_FOR_REVIEW_NOT_AUTHORIZATION',usable=24,unusable=0,reasons=[])
  self.write(health,source);before={p:p.read_bytes() for p in (status,admission,health,wrapper)}
  entry=self.collect()['value_prediction'][0]
  self.assertEqual(entry['resume']['next_action'],'ORIGINAL_FINITE_PREDICTOR_RECHECKS_SHARED_RESOURCE_ADMISSION')
  self.assertTrue(entry['guard_admission']['current_denial_observed']);self.assertNotIn('sealed_predictions',entry)
  self.assertTrue(entry['resume']['recovery_conditions']['same_heavy_lease_required'])
  self.assertFalse(entry['resume']['worker_launch_authority']);self.assertEqual(before,{p:p.read_bytes() for p in before})
  # Still globally fresh, but from a worker before the current interval.
  self.write(admission,{**row,'at_ms':NOW-2000});entry=self.collect()['value_prediction'][0]
  self.assertFalse(entry['guard_admission']['current_denial_observed'])
  self.assertEqual(entry['resume']['next_action'],'REFRESH_SELECTED_PREDICTOR_AND_ORIGINAL_GUARD_STATUS')

 def test_financial_old_or_other_role_denial_cannot_claim_current_worker_hold(self):
  name,status,admission,health,wrapper,hold=self.financial_hold_fixture()
  original=json.loads(admission.read_text());original_status=json.loads(status.read_text())
  for change in ('old','future_turn','other_role','unbound_front','stale_status','guard_changed'):
   with self.subTest(change=change):
    row=dict(original);state=copy.deepcopy(original_status);raw=wrapper.read_bytes()
    if change=='old':row['at_ms']=NOW-300001
    elif change=='future_turn':row['at_ms']=NOW+1
    elif change=='other_role':row['role']='trainer'
    elif change=='unbound_front':state['front_timing']['plan_id']='other-plan'
    elif change=='stale_status':state['at_ms']=NOW-300001
    else:wrapper.write_bytes(raw+b' ')
    self.write(admission,row);self.write(status,state)
    entry=self.collect()['value_prediction'][0]
    self.assertIsNot(entry['guard_admission'].get('current_denial_observed'),True)
    self.assertEqual(entry['resume']['next_action'],'REFRESH_SELECTED_PREDICTOR_AND_ORIGINAL_GUARD_STATUS')
    wrapper.write_bytes(raw)

 def test_financial_source_recovery_closed_window_and_persistent_hold_have_distinct_actions(self):
  name,status,admission,health,wrapper,hold=self.financial_hold_fixture()
  source=json.loads(health.read_text());source.update(state='READY_FOR_REVIEW_NOT_AUTHORIZATION',usable=24,unusable=0,reasons=[])
  self.write(health,source)
  self.assertEqual(self.collect()['value_prediction'][0]['resume']['next_action'],
   'ORIGINAL_FINITE_PREDICTOR_RECHECKS_SHARED_RESOURCE_ADMISSION')
  value=json.loads(status.read_text());self.write(status,{**value,'input_end_ms':NOW})
  entry=self.collect()['value_prediction'][0]
  self.assertEqual(entry['resume']['next_action'],'ORIGINAL_EVALUATOR_CLOSES_FIXED_WINDOW_NO_PREDICTION_RETRY')
  self.assertFalse(entry['guard_admission']['current_denial_observed'])
  self.write(hold,{'owner':'original-guard','reason':'NATIVE_WORKER_FATAL_SIGNAL','release_conditions':'inspect exact failure'})
  self.assertEqual(self.collect()['value_prediction'][0]['resume']['next_action'],'ORIGINAL_OWNER_REVIEW_EXACT_MANUAL_HOLD')

 def model_failure_fixture(self):
  message='model HOLD: model decision failed: 429 The usage limit has been reached provider openai model original-model'
  return {'resident_loop_progress':{'at_ms':NOW,'pid':7},
   'last_error':message,'last_error_observation':{'message':message,'at_ms':NOW-5000,'scope':'LAST_OBSERVED_LOOP_ERROR'},
   'last_decision_ms':NOW-86400000,'last_decision_attempt_ms':NOW-9000,
   'model_completion_observation':{'schema':'heptatrader.model-completion-observation.v1',
    'input_observed_at_ms':NOW-9200,'completed_at_ms':NOW-5004,'consumed_at_ms':NOW-5000,'outcome':'DISCARDED'},
   'loop_observation':{'checked_at_ms':NOW,'phase':'OBSERVING'}}

 def test_fresh_correlated_429_is_model_unavailability_independent_of_native_permission(self):
  path=self.write(self.state/'paper-driver/state.json',self.model_failure_fixture());raw=path.read_bytes()
  out=self.collect();trade=out['trading'];driver=trade['drivers']['paper'];model=driver['model_availability']
  self.assertTrue(driver['last_error_is_current_fault']);self.assertIsNone(driver['historical_last_error'])
  self.assertEqual(model['state'],'LATEST_DATED_MODEL_ATTEMPT_FAILED_NO_LATER_SUCCESS')
  self.assertEqual(model['failure_kind'],'PROVIDER_USAGE_LIMIT');self.assertFalse(model['record_has_process_identity'])
  self.assertEqual(model['retry_not_before_ms'],NOW-9000+120000)
  self.assertEqual(model['next_action'],'RESTORE_EXISTING_PROVIDER_CAPACITY_THEN_ORIGINAL_DRIVER_RETRIES')
  self.assertEqual(trade['permission'],'NATIVE_ADMISSION_NOT_ATTESTED');self.assertEqual(trade['current_blockers'],[])
  self.assertIn('MODEL paper ',progress.format_text(out));self.assertEqual(path.read_bytes(),raw)

 def test_later_success_and_retained_or_undated_error_stay_historical(self):
  path=self.state/'paper-driver/state.json'
  for change in ('used','decision','cleared','stale','undated','message_changed'):
   with self.subTest(change=change):
    state=self.model_failure_fixture()
    if change=='used':state['model_completion_observation'].update(outcome='USED',consumed_at_ms=NOW-1000)
    elif change=='decision':state['last_decision_ms']=NOW-1000
    elif change=='cleared':state['last_error']=''
    elif change=='stale':state['last_error_observation']['at_ms']=NOW-300001
    elif change=='undated':state['last_error_observation'].update(at_ms=None,scope='LEGACY_UNDATED_DIAGNOSTIC')
    else:state['last_error']='another error'
    self.write(path,state);driver=self.collect()['trading']['drivers']['paper']
    self.assertFalse(driver['last_error_is_current_fault']);self.assertNotIn('model_availability',driver)

 def test_unmatched_process_or_uncorrelated_completion_is_unknown_and_inflight_is_not_success(self):
  path=self.state/'paper-driver/state.json'
  for change in ('pid','heartbeat','completion_time','completion_missing','malformed_used'):
   with self.subTest(change=change):
    state=self.model_failure_fixture()
    if change=='pid':state['resident_loop_progress']['pid']=8
    elif change=='heartbeat':state['resident_loop_progress']['at_ms']=NOW-300001
    elif change=='completion_time':state['model_completion_observation']['consumed_at_ms']=NOW-4000
    elif change=='completion_missing':state.pop('model_completion_observation')
    else:state['model_completion_observation']={'outcome':'USED','consumed_at_ms':NOW}
    self.write(path,state);driver=self.collect()['trading']['drivers']['paper']
    self.assertIsNone(driver['last_error_is_current_fault'])
    self.assertEqual(driver['model_availability']['state'],'CURRENT_MODEL_AVAILABILITY_UNCONFIRMED')
  state=self.model_failure_fixture();state['loop_observation']['phase']='MODEL_INFLIGHT'
  state['last_decision_attempt_ms']=NOW-100;self.write(path,state)
  model=self.collect()['trading']['drivers']['paper']['model_availability']
  self.assertTrue(model['retry_in_flight']);self.assertEqual(model['failure_kind'],'PROVIDER_USAGE_LIMIT')

 def test_expected_discards_are_not_provider_quota_failures(self):
  path=self.state/'paper-driver/state.json'
  for cause in ('inference hold','authoritative position change','market snapshot expiry'):
   state=self.model_failure_fixture();message='model HOLD: model decision discarded after '+cause
   state['last_error']=message;state['last_error_observation']['message']=message;self.write(path,state)
   model=self.collect()['trading']['drivers']['paper']['model_availability']
   self.assertEqual(model['failure_kind'],'MODEL_RESULT_DISCARDED')
   self.assertFalse(model['recovery_conditions']['existing_provider_usage_limit_must_clear'])

 def test_clock_rollback_cannot_turn_a_future_dated_model_failure_into_history(self):
  state=self.model_failure_fixture();state['last_error_observation']['at_ms']=NOW+1000
  self.write(self.state/'paper-driver/state.json',state)
  trade=self.collect()['trading'];driver=trade['drivers']['paper'];model=driver['model_availability']
  self.assertIsNone(driver['last_error_is_current_fault']);self.assertIsNone(driver['historical_last_error'])
  self.assertEqual(model['state'],'CURRENT_MODEL_AVAILABILITY_UNCONFIRMED')
  self.assertEqual(model['freshness'],'FUTURE');self.assertEqual(model['age_ms'],-1000)
  self.assertEqual(trade['permission'],'NATIVE_ADMISSION_NOT_ATTESTED');self.assertEqual(trade['current_blockers'],[])

if __name__=='__main__':unittest.main()
