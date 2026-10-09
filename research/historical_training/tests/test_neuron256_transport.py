"""Synthetic negative tests: no host, broker, real future slot, or secret access."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from historical_training.common import EvidenceError,digest,write_once,sha
from historical_training.neuron256_transport import (
 FIRST_ANCHOR,PARENT_ID,LAYA_END_MS,LAYA_ID,FILES,MAX_CPU_SECOND,
 future_anchors,terminal_laya,_checked_path,_remote,cpu_window,gpu_window,active_anchor)
from historical_training.neuron256_prospective import validate_plan,BASELINES,FAMILY

ANCHORS=(1791457800,1791465000,1791472200,1791479400,
         1791544200,1791551400,1791558600,1791565800,
         1791803400,1791810600,1791817800,1791825000,
         1791889800,1791897000,1791904200,1791911400,
         1791976200,1791983400,1791990600,1791997800,
         1792062600,1792069800,1792077000,1792084200)


class CrossHostBridgeTests(unittest.TestCase):
 def parent(self):
  return {'campaign_id':PARENT_ID,'anchors':list(ANCHORS),
          'trade_authority':False,'automatic_promotion':False}
 def test_calendar_only_selects_current_unexpired_fixed_slot(self):
  c={'anchors':list(ANCHORS[-18:])}
  self.assertIsNone(active_anchor(c,(FIRST_ANCHOR-1)*1000))
  self.assertEqual(active_anchor(c,(FIRST_ANCHOR+46)*1000),FIRST_ANCHOR)
  self.assertIsNone(active_anchor(c,(FIRST_ANCHOR+239)*1000))
  wrong={'anchors':list(ANCHORS[-18:])}
  wrong['anchors'][2]+=300
  with self.assertRaises(EvidenceError):active_anchor({'anchors':[FIRST_ANCHOR]},FIRST_ANCHOR*1000)
 def test_exact_existing_18_windows_no_extra_query(self):
  a=future_anchors(self.parent(),LAYA_END_MS+600000,require_registration=True)
  self.assertEqual(a,list(ANCHORS[-18:]))
  self.assertEqual(a[0],FIRST_ANCHOR)
  self.assertEqual(len(a),18)
 def test_before_laya_cutoff_cannot_register(self):
  for now in (LAYA_END_MS-1,LAYA_END_MS,LAYA_END_MS+1):
   if now==LAYA_END_MS+1:continue
   with self.assertRaisesRegex(EvidenceError,'AFTER_LAYA'):
    future_anchors(self.parent(),now,require_registration=True)
 def test_after_first_slot_start_cannot_register(self):
  with self.assertRaisesRegex(EvidenceError,'AFTER_LAYA'):
   future_anchors(self.parent(),FIRST_ANCHOR*1000-59000,require_registration=True)
 def test_parent_identity_cannot_be_changed(self):
  for key,value in [('campaign_id','forged'),('trade_authority',True),
                    ('automatic_promotion',True),('anchors',list(ANCHORS[:-1]))]:
   p=self.parent();p[key]=value
   with self.assertRaises(EvidenceError):future_anchors(p,LAYA_END_MS+600000)
 def test_no_past_window_reallocation(self):
  p=self.parent();p['anchors'][6]=1791551400
  with self.assertRaises(EvidenceError):future_anchors(p,LAYA_END_MS+600000)
 def test_transport_path_escape_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   for path in (Path(tmp)/'../outside',Path('/etc/shadow'),Path(tmp)/'../bad\nname'):
    with self.assertRaises(EvidenceError):_checked_path(path,tmp)
   good=Path(tmp)/'slots/anchor/proof/input.json'
   self.assertEqual(_checked_path(good,tmp),good)
 def test_transport_destination_host_pinned(self):
  with tempfile.TemporaryDirectory() as tmp:
   c={'remote_host':'pocket4','remote_allow_root':tmp}
   self.assertEqual(_remote(c,Path(tmp)/'canonical-cpu/123/raw.safetensors'),
                    'pocket4:'+str(Path(tmp)/'canonical-cpu/123/raw.safetensors'))
 def test_terminal_laya_cannot_use_only_passing_service_status(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);p={'plan_id':LAYA_ID,'candidate_id':'genuine',
                     'outcome_end_ms':LAYA_END_MS,'rule':{'slots':3,'max_censored':2,'min_observed':2}}
   plan=root/'plan.json';write_once(plan,p)
   c={'laya29_plan_path':str(plan),'laya29_plan_sha256':sha(plan),'laya29_candidate_id':'genuine'}
   with self.assertRaises(EvidenceError):terminal_laya(c,LAYA_END_MS)
   for i in range(3):
    write_once(root/('outcome-%03d.json'%i),{'plan_id':LAYA_ID,'slot':i,'status':'CENSORED'})
   with self.assertRaises(EvidenceError):terminal_laya(c,LAYA_END_MS+1)
   write_once(root/'qualification.json',{'plan_id':LAYA_ID,'candidate_id':'genuine',
                   'state':'REJECTED','observed':0,'censored':3,'promotion_performed':False})
   self.assertEqual(terminal_laya(c,LAYA_END_MS+1)[1:],(0,3))
 def test_parent_outcome_cannot_be_recovered_as_future(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);p={'plan_id':LAYA_ID,'candidate_id':'genuine','outcome_end_ms':LAYA_END_MS,
      'rule':{'slots':3,'max_censored':2,'min_observed':2}}
   plan=root/'plan.json';write_once(plan,p)
   c={'laya29_plan_path':str(plan),'laya29_plan_sha256':sha(plan),'laya29_candidate_id':'genuine'}
   for i,status in enumerate(('OBSERVED','CENSORED','CENSORED')):
    write_once(root/('outcome-%03d.json'%i),{'plan_id':LAYA_ID,'slot':i,'status':status})
   write_once(root/'qualification.json',{'plan_id':LAYA_ID,'candidate_id':'genuine',
       'state':'PENDING','observed':1,'censored':2,'promotion_performed':False})
   with self.assertRaises(EvidenceError):terminal_laya(c,LAYA_END_MS+1)
 def test_missing_public_registration_never_starts_cpu(self):
  with tempfile.TemporaryDirectory() as tmp:
   a=int(FIRST_ANCHOR)
   cfg={'local_root':tmp,'remote_root':'/home/alex/study',
         'remote_host':'pocket4','remote_allow_root':'/home/alex'}
   with patch('historical_training.neuron256_transport.time.time',return_value=a+30):
    with patch('historical_training.neuron256_transport._transfer',return_value=False):
     from historical_training.neuron256_transport import cpu_once
     self.assertEqual(cpu_once(cfg,a),'WAIT_REGISTRATION')
   self.assertFalse(list(Path(tmp).glob('claims/*')))
 def test_gpu_interrupted_claim_never_replayed(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);a=FIRST_ANCHOR
   claim=root/'slots'/str(a)/'claim.json'
   write_once(claim,{'plan_id':'trial','anchor':a,'started_at_ms':a*1000+20000})
   cfg={'root':str(root)}
   with patch('historical_training.neuron256_transport.time.time',return_value=a+80):
    with patch('historical_training.neuron256_prospective.validate_runtime',
               return_value=({'anchors':[a]},{'candidate_id':'candidate'})):
     with patch('historical_training.neuron256_prospective.predict_one',
                side_effect=AssertionError('MUST_NOT_RELOAD_ON_INTERRUPTED_CLAIM')):
      self.assertEqual(gpu_window(cfg,a),'GPU_INTERRUPTED_AFTER_CLAIM_NO_REPLAY')
 def test_gpu_expired_input_never_creates_prediction(self):
  with tempfile.TemporaryDirectory() as tmp:
   cfg={'root':tmp}
   with patch('historical_training.neuron256_prospective.validate_runtime',
              return_value=({'anchors':[FIRST_ANCHOR]},{})):
    with patch('historical_training.neuron256_transport.time.time',
               return_value=FIRST_ANCHOR+240):
     self.assertEqual(gpu_window(cfg,FIRST_ANCHOR),'MISSED_NO_REPLAY')
   self.assertFalse(list(Path(tmp).rglob('prediction.json')))
 def test_changed_cpu_source_inventory_is_rejected(self):
  from historical_training.neuron256_prospective import validate_plan
  p={'schema':'heptatrader.neuron256-seven-bin-future-plan.v1',
     'family':FAMILY,'trial':1,'alpha':.025,'baselines':list(BASELINES),
     'anchors':list(ANCHORS[-18:]),'parent_campaign_id':PARENT_ID,
     'registered_at_ms':LAYA_END_MS+1000,'laya29_end_ms':LAYA_END_MS,
     'min_observed':16,'max_censored':2,'min_days':4,'min_mean_brier_gain':.001,
     'cpu_public_key_hex':'aa'*32,'cpu_backend':'2.11.0+cpu','cpu_emitter_sha256':'bb'*32,
     'cpu_source_inventory':{'research/laya/common.py':'bb'*32,
       'research/historical_training/neuron256_transport.py':'bb'*32,
       'research/historical_training/neuron256_cpu_custody.py':'bb'*32,
       **{'research/historical_training/x'+str(i)+'.py':'bb'*32 for i in range(7)}},
     'runtime_sha256':{'research/historical_training/neuron256_cpu_custody.py':'bb'*32},
     'trade_authority':False,'automatic_selection':False,'replay_missed_windows':False}
  p['plan_id']=digest(p);validate_plan(p)
  p['cpu_source_inventory']['research/laya/common.py']='xyz'
  p['plan_id']=digest({k:v for k,v in p.items() if k!='plan_id'})
  with self.assertRaisesRegex(EvidenceError,'PINNED_CANONICAL_CPU_SOURCE_REQUIRED'):
   validate_plan(p)
 def test_expired_cpu_window_cannot_encode(self):
  with tempfile.TemporaryDirectory() as tmp:
   c={'local_root':tmp}
   with patch('historical_training.neuron256_transport.time.time',
              return_value=FIRST_ANCHOR+MAX_CPU_SECOND+1):
    self.assertEqual(cpu_window(c,FIRST_ANCHOR),'MISSED_NO_REPLAY')
 def test_signed_seven_bin_plan_only_after_laya_terminal(self):
  from historical_training.neuron256_prospective import BASELINES,FAMILY
  p={'schema':'heptatrader.neuron256-seven-bin-future-plan.v1',
     'family':FAMILY,'trial':1,'alpha':.025,'baselines':list(BASELINES),
     'anchors':list(ANCHORS[-18:]),'laya29_end_ms':LAYA_END_MS,
     'parent_campaign_id':PARENT_ID,
     'registered_at_ms':LAYA_END_MS-1,'min_observed':16,'max_censored':2,
     'min_days':4,'min_mean_brier_gain':.001,
     'cpu_public_key_hex':'ab'*32,'cpu_backend':'2.11.0+cpu',
     'cpu_emitter_sha256':'ab'*32,
     'cpu_source_inventory':{'research/laya/common.py':'ab'*32,
       'research/historical_training/neuron256_cpu_custody.py':'ab'*32,
       'research/historical_training/neuron256_transport.py':'ab'*32,
       **{'research/historical_training/module_'+str(i)+'.py':'ab'*32 for i in range(7)}},
     'runtime_sha256':{'research/historical_training/neuron256_cpu_custody.py':'ab'*32},
     'trade_authority':False,'automatic_selection':False,'replay_missed_windows':False}
  p['plan_id']=digest(p)
  with self.assertRaisesRegex(EvidenceError,'FUTURE_AND_AFTER_LAYA29'):validate_plan(p)
  p['registered_at_ms']=LAYA_END_MS+60000;p['plan_id']=digest({k:v for k,v in p.items() if k!='plan_id'})
  self.assertEqual(validate_plan(p),p)


if __name__=='__main__':unittest.main()
