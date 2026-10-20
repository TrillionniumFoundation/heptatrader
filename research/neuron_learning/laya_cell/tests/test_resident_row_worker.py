"""Actual CPU autograd through run_job, with synthetic model/data only."""
import json,tempfile,unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import torch
from safetensors.torch import save_file
from laya_cell import resident_train as t
from laya_cell import resident_training_v2 as v2_profile
from laya_cell import resident_training_v3 as v3_profile
from laya_cell import resident_training_v4 as v4_profile
from laya_cell.resident_model import ResidentDecisionCell,PROFILE
from laya_cell.resident_row_accounting import SCHEMA,row_id,verify_candidate
from laya_cell.integrity import digest,sha_file
from laya_cell.tests.test_resident import TinyPretrained


class NativeFixture:
 def __init__(self,*args):pass
 def predictive_utility(self,*args):return {'receipt_digest':'e'*64}
 def propose(self,*args):return {'proposal_digest':'f'*64}


class WorkerRowIntegration(unittest.TestCase):
 def run_fixture(self,n,rollback=False,*,v2=False,v3=False,v4=False,correct_rows=0):
  torch.set_num_threads(1)
  with tempfile.TemporaryDirectory() as td,ExitStack() as stack:
   root=Path(td);base=root/'base';base.mkdir();(base/'base-manifest.json').write_text('{}')
   parent=root/'parent';parent.mkdir();cell=ResidentDecisionCell(TinyPretrained(),'a'*64)
   save_file(cell.trainable_state(),str(parent/'adapter.safetensors'))
   manifest={'base_id':'a'*64,'base_revision':'cpu-synthetic-only','window':{'training_end_ms':0},
             'adapter_sha256':sha_file(parent/'adapter.safetensors')}
   manifest['candidate_id']=digest(manifest);(parent/'candidate.json').write_text(json.dumps(manifest))
   rows=[{'sample_id':str(i),'horizon_sec':300,'outcome_receipt_digest':str(i),'anchor_ms':100+i,
          'target_class':i%7,'teacher_probs':None} for i in range(n)]
   encoding_calls=[0]
   def encode(tok,selected):
    encoding_calls[0]+=1
    return {'x':torch.tensor([[int(r['sample_id'])/100,0.1,0.2] for r in selected])}
   dataset={};cutoff=999
   if v2 or v3 or v4:
    dataset={'samples':[]}
    with torch.no_grad():
     for i,r in enumerate(rows):
      choice=int(cell(**encode(None,[r]))[0].argmax(-1)[0])
      r['target_class']=choice if i<correct_rows else (choice+1)%7
      r['anchor_ms']+=1000000
      bid=1.+(i%5)*.00001
      r['state']={'instrument':'EUR.USD','anchor_ms':r['anchor_ms'],
       'reference_mid':str(bid+.00001),'horizon_sec':300,
       'columns':['offset_ms','bid','ask'],
       'quotes':[[j*10000,str(bid+j*.000001),str(bid+j*.000001+.00002)] for j in range(-5,1)]}
      outcome={'observed_at_ms':r['anchor_ms']+300000,
               'available_at_ms':r['anchor_ms']+300001}
      r['outcome_receipt_digest']=digest(outcome)
      dataset['samples'].append({'sample_id':r['sample_id'],'outcome_receipts':[outcome,None]})
    cutoff=rows[-1]['anchor_ms']+300002
   data=root/'dataset.json';data.write_text(json.dumps(dataset))
   ids=[row_id(r) for r in rows]
   cfg={'objective':v4_profile.OBJECTIVE if v4 else v3_profile.OBJECTIVE if v3 else v2_profile.OBJECTIVE if v2 else t.OBJECTIVE,'profile':PROFILE,
        'base_directory':str(base),'output_directory':str(root/'out'),
        'baseline_directory':str(root/'baseline'),'parent_directory':str(parent),'parent_id':manifest['candidate_id'],
        'dataset':str(data),'dataset_sha256':sha_file(data),'window':{'training_end_ms':cutoff},
        'training_row_ids':ids,'training_row_accounting_schema':v4_profile.ROW_ACCOUNTING_SCHEMA if v4 else SCHEMA,
        'parent_manifest_sha256':sha_file(parent/'candidate.json'),'native_binary':'fixture-only',
        'native_binary_sha256':'b'*64,'data_fingerprint':digest(ids),'generation':2}
   replacements={'verify_baseline':lambda p:'d'*64,'verify_source_prefixes':lambda d:None,
                 'rows_for_training':lambda d,w:(rows,{}),'require_pocket4_gpu':lambda:torch.device('cpu'),
                 'load_verified_laya':lambda b,m:(TinyPretrained(),None,'a'*64),
                 'encode_rows':encode,'ResidentNative':NativeFixture}
   for name,value in replacements.items():stack.enter_context(patch.object(t,name,value))
   stack.enter_context(patch.object(torch.cuda,'reset_peak_memory_stats'))
   stack.enter_context(patch.object(torch.cuda,'get_device_properties',return_value=SimpleNamespace(gcnArchName='CPU_SYNTHETIC_FIXTURE')))
   stack.enter_context(patch.object(torch.cuda,'max_memory_allocated',return_value=0))
   active_cell=[None];action_snapshots=[];action_gradient_absent=[]
   optimizer_ids=set();optimizer_price_before=[];encoding_calls[0]=0
   original_init=ResidentDecisionCell.__init__;original_step=torch.optim.AdamW.step
   def remember_cell(current,*args,**kwargs):
    original_init(current,*args,**kwargs);active_cell[0]=current
   def observe_step(optimizer,*args,**kwargs):
    optimizer_ids.add(id(optimizer))
    price_param=next(p for name,p in active_cell[0].named_parameters()
                     if p.requires_grad and '.scorer.' in name)
    optimizer_price_before.append(int(optimizer.state.get(price_param,{}).get('step',0)))
    action={name:p for name,p in active_cell[0].named_parameters()
            if p.requires_grad and '.act_head.' in name}
    self.assertTrue(action)
    action_gradient_absent.append(all(p.grad is None for p in action.values()))
    result=original_step(optimizer,*args,**kwargs)
    action_snapshots.append({name:p.detach().clone() for name,p in action.items()})
    return result
   stack.enter_context(patch.object(ResidentDecisionCell,'__init__',remember_cell))
   stack.enter_context(patch.object(torch.optim.AdamW,'step',observe_step))
   if rollback:
    original=ResidentDecisionCell.norm_report
    def reject(current):return {**original(current),'within_profile':False}
    stack.enter_context(patch.object(ResidentDecisionCell,'norm_report',reject))
   candidate=t.run_job(cfg,old_learner_verified=True);verify_candidate(cfg,candidate)
   loaded=json.loads((root/'out/candidate.json').read_text());verify_candidate(cfg,loaded)
   self.assertEqual(loaded['row_accounting']['gradient_attempted_count'],n)
   self.assertEqual(len(loaded['steps']),(n+1)//2*(4 if v4 else 1))
   self.assertEqual(loaded['row_accounting']['applied_count'],0 if rollback else n)
   self.assertEqual(loaded['changed_parameters']==0,rollback)
   if v4:
    self.assertEqual(len(optimizer_ids),1)
    applied=0
    for before,step in zip(optimizer_price_before,loaded['steps']):
     self.assertEqual(before,applied)
     applied+=int(step['applied'])
    self.assertLessEqual(encoding_calls[0],n+(n+1)//2)
    self.assertEqual(loaded['row_accounting']['gradient_attempted_presentations'],4*n)
    self.assertEqual(loaded['row_accounting']['applied_presentations'],0 if rollback else 4*n)
   return loaded,action_snapshots,action_gradient_absent
 def test_32_reserved_rows_have_32_real_backward_pass_inputs(self):self.run_fixture(32)
 def test_odd_last_batch_trains_all_21_rows(self):self.run_fixture(21)
 def test_rejected_steps_are_logged_as_rollback_not_applied(self):self.run_fixture(16,True)
 def test_v2_one_class_updates_price_and_never_updates_action(self):
  c,_,absent=self.run_fixture(32,v2=True)
  self.assertEqual(c['objective'],v2_profile.OBJECTIVE)
  self.assertEqual(c['action_training_profile']['labels'],{'ACT_correct':0,'ESCALATE_wrong':32})
  self.assertFalse(c['action_optimizer_admitted'])
  self.assertEqual(c['action_update_steps'],0)
  self.assertTrue(all(absent))
  self.assertTrue(all(step['action_loss']==0 for step in c['steps']))
  self.assertEqual(sum(v for n,v in c['changed_by_tensor'].items() if '.act_head.' in n),0)
  self.assertEqual(c['row_statistics']['anchor_span_ms'],31)
  self.assertEqual(c['row_statistics']['maximum_nonoverlapping_target_intervals'],1)
 def test_v2_majority_only_tail_does_not_update_action_via_adam_momentum(self):
  c,states,absent=self.run_fixture(32,v2=True,correct_rows=4)
  self.assertEqual(c['action_training_profile']['labels'],{'ACT_correct':4,'ESCALATE_wrong':28})
  self.assertTrue(c['action_optimizer_admitted'])
  self.assertEqual(c['action_update_steps'],4)
  self.assertEqual(absent,[False]*4+[True]*12)
  self.assertTrue(all(step['action_loss']==0 for step in c['steps'][4:]))
  for state in states[4:]:
   for name,value in state.items():self.assertTrue(torch.equal(value,states[3][name]),name)
 def test_v2_norm_rejection_restores_real_parameters_and_accounting(self):
  c,_,_=self.run_fixture(16,True,v2=True,correct_rows=4)
  self.assertEqual(c['action_update_steps'],0)
  self.assertEqual(c['changed_parameters'],0)
 def test_v2_odd_batch_keeps_every_original_price_row(self):
  c,_,_=self.run_fixture(21,v2=True)
  self.assertEqual(c['row_statistics']['rows'],21)
  self.assertEqual(c['row_accounting']['applied_count'],21)

 def test_v3_one_class_updates_price_without_action_gradients(self):
  c,_,absent=self.run_fixture(32,v3=True)
  self.assertEqual(c['objective'],v3_profile.OBJECTIVE)
  self.assertEqual(c['action_training_profile']['batch_balance_strategy'],v3_profile.ACTION_BALANCE)
  self.assertFalse(c['action_optimizer_admitted'])
  self.assertEqual(c['action_update_steps'],0)
  self.assertTrue(all(absent))
  self.assertEqual(sum(v for n,v in c['changed_by_tensor'].items() if '.act_head.' in n),0)
  self.assertEqual(c['row_statistics']['horizon_counts'],{'300':32})
 def test_v3_majority_tail_cannot_move_action_via_adam_momentum(self):
  c,states,absent=self.run_fixture(32,v3=True,correct_rows=4)
  self.assertEqual(c['action_training_profile']['labels'],{'ACT_correct':4,'ESCALATE_wrong':28})
  self.assertEqual(c['action_update_steps'],4)
  self.assertEqual(absent,[False]*4+[True]*12)
  self.assertEqual(c['row_accounting']['applied_count'],32)
  for state in states[4:]:
   for name,value in state.items():self.assertTrue(torch.equal(value,states[3][name]),name)
 def test_v3_rollback_preserves_original_tensors_and_row_accounting(self):
  c,_,_=self.run_fixture(16,True,v3=True,correct_rows=4)
  self.assertEqual(c['action_update_steps'],0)
  self.assertEqual(c['changed_parameters'],0)
  self.assertEqual(c['row_accounting']['applied_count'],0)

 def test_v4_first_epoch_is_same_control_then_optimizer_continues(self):
  legacy,_,_=self.run_fixture(32,v3=True)
  current,_,absent=self.run_fixture(32,v4=True)
  curve=current['learning_curve']['checkpoints']
  self.assertEqual([p['epoch'] for p in curve],[0,1,2,4])
  self.assertEqual([p['optimizer_steps_attempted'] for p in curve],[0,16,32,64])
  self.assertAlmostEqual(curve[1]['ce'],legacy['measurement']['candidate_ce'],places=7)
  self.assertEqual([s['gradient_digest'] for s in current['steps'][:16]],
                   [s['gradient_digest'] for s in legacy['steps']])
  self.assertEqual(curve[-1]['ce'],current['measurement']['candidate_ce'])
  self.assertEqual(curve[-1]['unique_gradient_rows_applied'],32)
  self.assertEqual(curve[-1]['gradient_presentations_applied'],128)
  self.assertFalse(current['learning_curve']['future_value_proven'])
  self.assertFalse(current['training_lineage']['optimizer_reset_between_epochs'])
  self.assertEqual(current['action_update_steps'],0)
  self.assertTrue(all(absent))
  self.assertEqual(sum(v for n,v in current['changed_by_tensor'].items() if '.act_head.' in n),0)
 def test_v4_action_tail_cannot_move_adam_momentum_in_any_epoch(self):
  c,states,absent=self.run_fixture(32,v4=True,correct_rows=4)
  self.assertEqual(c['action_training_profile']['labels'],{'ACT_correct':4,'ESCALATE_wrong':28})
  self.assertEqual(absent,([False]*4+[True]*12)*4)
  self.assertEqual(c['action_update_steps'],16)
  for epoch in range(4):
   last_balanced=states[epoch*16+3]
   for state in states[epoch*16+4:epoch*16+16]:
    for name,value in state.items():self.assertTrue(torch.equal(value,last_balanced[name]),name)
 def test_v4_full_rollback_preserves_optimizer_and_weights(self):
  c,_,_=self.run_fixture(16,True,v4=True,correct_rows=4)
  self.assertEqual(c['action_update_steps'],0)
  self.assertEqual(c['row_accounting']['applied_count'],0)
  self.assertEqual(c['row_accounting']['rolled_back_count'],16)
  self.assertEqual(c['row_accounting']['rolled_back_presentations'],64)
  curve=c['learning_curve']['checkpoints']
  self.assertEqual(curve[0]['ce'],curve[-1]['ce'])
 def test_v4_odd_batch_counts_original_rows_once(self):
  c,_,_=self.run_fixture(21,v4=True)
  self.assertEqual(c['row_accounting']['applied_count'],21)
  self.assertEqual(c['row_accounting']['applied_presentations'],84)
  self.assertEqual(len(c['steps']),44)


if __name__=='__main__':unittest.main()
