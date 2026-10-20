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
  self.units[name]={'Id':name,'ActiveState':active,'SubState':'dead','MainPID':'0','UnitFileState':enabled,
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
 def test_waiting_rollout_keeps_paused_training_evidence(self):
  _,_=self.fixture_learning();self.units['heptatrader-laya-trainer.service'].update(ActiveState='inactive',UnitFileState='disabled')
  self.assertEqual(self.collect()['learning'],[])
  self.write(self.state/'operator-maintenance/training-episode-rollout/status.json',{'state':'WAIT_FROZEN_TRIAL_43','trainer_paused':True,'at_ms':NOW})
  out=self.collect();self.assertEqual(len(out['learning']),1);self.assertTrue(out['training_rollout']['trainer_paused'])
 def test_status_still_displays_progress_when_service_check_fails(self):
  target=self.home/'cli';target.mkdir();shutil.copyfile(ROOT/'scripts/heptatrader-user',target/'heptatrader-user')
  (target/'hepta_progress.py').write_text('print("PROGRESS_SEEN")\n')
  stub=target/'systemctl';stub.write_text('#!/bin/sh\nexit 3\n');stub.chmod(0o700)
  env=dict(os.environ,HOME=str(self.home),PATH=str(target)+':'+os.environ.get('PATH',''),XDG_RUNTIME_DIR=str(self.home/'run'))
  result=subprocess.run(['bash',str(target/'heptatrader-user'),'status'],env=env,text=True,capture_output=True,timeout=10)
  self.assertEqual(result.returncode,3);self.assertIn('PROGRESS_SEEN',result.stdout)

if __name__=='__main__':unittest.main()
