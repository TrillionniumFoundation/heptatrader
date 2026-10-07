"""Structural regression fixtures; native checks use the compiled real bridge."""
import copy,hashlib,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import torch
from torch import nn
from laya_cell.integrity import ContractError,LAYA_REPO,LAYA_REVISION,REQUIRED,canonical,digest,safe_child,verify_snapshot,read_json,sha_file
from laya_cell.model import TradingDecisionCell,RowLoRALinear,require_pocket4_gpu
from laya_cell.dataset import causal_state,rows_for_training,teacher_bins,question
from laya_cell.train import bounded_step,supervised_loss,verify_old_learner_inactive
from laya_cell.native import NativePorts
T=1791321000000
class Toy(nn.Module):
 def __init__(self):
  super().__init__();self.encoder=nn.Embedding(32,16);self.dropout=nn.Dropout(.9);self.scorer=nn.Sequential(nn.LayerNorm(16),nn.Linear(16,16),nn.GELU(),nn.Linear(16,1));self.act_head=nn.Linear(16,2)
 def forward(self,input_ids,attention_mask,marker_pos,marker_mask,qtype):
  h=self.dropout(self.encoder(input_ids));m=torch.gather(h,1,marker_pos[...,None].expand(-1,-1,h.shape[-1]));return self.scorer(m).squeeze(-1).masked_fill(~marker_mask,-1e4),self.act_head(h[:,0])
def toy():
 with torch.random.fork_rng():torch.manual_seed(20261007);return Toy().eval()
def batch():return {'input_ids':torch.arange(14).reshape(2,7),'attention_mask':torch.ones(2,7,dtype=torch.int64),'marker_pos':torch.arange(7).expand(2,-1),'marker_mask':torch.ones(2,7,dtype=torch.bool),'qtype':torch.zeros(2,dtype=torch.int64)}
def datum():
 history=[{'t':T+i*10000,'bid':'1.00000','ask':'1.00002','mid':1.00001} for i in range(6)];inp={'scope':'scope','quotes':history,'input_kind':'EXACT_REMOTE_INPUT','source_input_id':'input1'};anchor=history[-1]['t']
 s={'input_snapshot':inp,'sample_id':digest(inp),'scope':'scope','anchor_ms':anchor,'known_at_ms':anchor+100,'reference_mid':1.00001,'input_gap':False,'source_input_id':'input1','targets':[.02,None],'label_reasons':['OBSERVED','OUTCOME_NOT_MATURE'],'outcome_receipts':[{'observed_at_ms':anchor+300000,'available_at_ms':anchor+300100,'target_scaled':.02},None],'teacher':None}
 d={'schema':'heptatrader.neuron-dataset.v1','source_kind':'REAL_PAPER_RUNTIME','scope':'scope','instrument':'EUR.USD','samples':[s]};w={'training_end_ms':anchor+301000,'validation_start_ms':anchor+901000,'validation_end_ms':anchor+1901000};return s,d,w
class AdapterTests(unittest.TestCase):
 def test_zero_delta(self):
  m=toy();a=m(**batch())[0].detach().clone();c=TradingDecisionCell(m,'1'*64);torch.testing.assert_close(c(**batch())[0],a,atol=0,rtol=0)
 def test_trainable_mask(self):
  c=TradingDecisionCell(toy(),'1'*64);names=[n for n,p in c.named_parameters() if p.requires_grad];self.assertTrue(names);self.assertTrue(all(n.endswith(('.A','.B')) for n in names));self.assertFalse(any('encoder' in n or 'act_head' in n for n in names))
 def test_pretrained_unchanged(self):
  m=toy();before=m.encoder.weight.detach().clone();c=TradingDecisionCell(m,'1'*64);torch.testing.assert_close(c.model.encoder.weight,before,atol=0,rtol=0)
 def test_actual_update_only_adapter(self):
  c=TradingDecisionCell(toy(),'1'*64);c.train();before=c.trainable_state();o=torch.optim.AdamW([p for p in c.parameters() if p.requires_grad],lr=.0001,weight_decay=0);r=bounded_step(c,o,batch(),torch.tensor([0,3]));self.assertTrue(r['applied']);c.assert_frozen();self.assertTrue(any(not torch.equal(v,before[n]) for n,v in c.trainable_state().items()))
 def test_dropout_frozen(self):
  c=TradingDecisionCell(toy(),'1'*64);c.train();self.assertFalse(c.model.dropout.training);torch.testing.assert_close(c(**batch())[0],c(**batch())[0],atol=0,rtol=0)
 def test_frozen_corruption(self):
  c=TradingDecisionCell(toy(),'1'*64)
  with torch.no_grad():c.model.encoder.weight[0,0]+=1
  with self.assertRaisesRegex(ContractError,'BASE_CHANGED'):c.assert_frozen()
 def test_invalid_rows(self):
  with self.assertRaisesRegex(ContractError,'MASK'):RowLoRALinear(nn.Linear(8,8),[2,1],2,1)
 def test_restore_mask(self):
  c=TradingDecisionCell(toy(),'1'*64);s=c.trainable_state();s.pop(next(iter(s)))
  with self.assertRaisesRegex(ContractError,'TENSOR_MASK'):c.restore_trainable(s)
 def test_effective_norm_gauge(self):
  c=TradingDecisionCell(toy(),'1'*64)
  with torch.no_grad():
   for a in c.adapters.values():a.B.fill_(.0001)
  report=c.norm_report();rows=c.native_parameter_rows()
  with torch.no_grad():
   for a in c.adapters.values():a.A.mul_(2);a.B.div_(2)
  self.assertEqual(report,c.norm_report());self.assertEqual(rows,c.native_parameter_rows())
 def test_effective_coordinates(self):
  c=TradingDecisionCell(toy(),'1'*64)
  with torch.no_grad():
   for a in c.adapters.values():a.B.fill_(.001)
  layers=c.native_parameter_rows();self.assertLessEqual(sum(len(x['deltas']) for x in layers),4096);self.assertTrue(all(d['row']<4 for x in layers for d in x['deltas']))
 def test_no_cpu_training(self):
  with patch('torch.cuda.is_available',return_value=False):
   with self.assertRaisesRegex(ContractError,'NO_CPU'):require_pocket4_gpu()
 def test_unknown_profile(self):
  with self.assertRaisesRegex(ContractError,'UNREVIEWED'):TradingDecisionCell(toy(),'1'*64,{'targets':{}})
 def test_excess_norm(self):
  c=TradingDecisionCell(toy(),'1'*64)
  with torch.no_grad():
   for a in c.adapters.values():a.B.fill_(100)
  self.assertFalse(c.norm_report()['within_profile'])
 def test_real_shape_budget(self):
  a=RowLoRALinear(nn.Linear(768,768),[0,1,2,3],2,1);b=RowLoRALinear(nn.Linear(768,1),[0],1,2);self.assertEqual(a.A.numel()+a.B.numel()+b.A.numel()+b.B.numel(),2313)
class DataTests(unittest.TestCase):
 def test_mature_only(self):
  s,d,w=datum();r,e=rows_for_training(d,w);self.assertEqual(len(r),1);self.assertEqual(r[0]['target_class'],4);self.assertEqual(len(e),1)
 def test_synthetic_refused(self):
  s,d,w=datum();d['source_kind']='SYNTHETIC'
  with self.assertRaisesRegex(ContractError,'REAL_PAPER'):rows_for_training(d,w)
 def test_teacher_not_input(self):
  s,d,w=datum();s['teacher']={'confidence':.99,'forecast':{'action':'BUY'}};text=canonical(causal_state(s,'EUR.USD',300)).decode();self.assertNotIn('confidence',text);self.assertNotIn('BUY',text);self.assertNotIn('target',text)
 def test_future_quote(self):
  s,d,w=datum();s['input_snapshot']['quotes'][-1]['t']+=10;s['sample_id']=digest(s['input_snapshot'])
  with self.assertRaisesRegex(ContractError,'FUTURE'):causal_state(s,'EUR.USD',300)
 def test_input_tamper(self):
  s,d,w=datum();s['input_snapshot']['quotes'][0]['bid']='2'
  with self.assertRaisesRegex(ContractError,'IDENTITY'):causal_state(s,'EUR.USD',300)
 def test_missing_not_zero(self):
  s,d,w=datum();s['targets'][0]=None;self.assertEqual(rows_for_training(d,w)[0],[])
 def test_late_label(self):
  s,d,w=datum();s['outcome_receipts'][0]['available_at_ms']=w['training_end_ms']+1;self.assertEqual(rows_for_training(d,w)[0],[])
 def test_gap_exclusion(self):
  s,d,w=datum();s['input_gap']=True;r,e=rows_for_training(d,w);self.assertEqual(r,[]);self.assertTrue(e)
 def test_units(self):
  s,d,w=datum();s['outcome_receipts'][0]['target_scaled']=2
  with self.assertRaisesRegex(ContractError,'UNITS'):rows_for_training(d,w)
 def test_dedup(self):
  s,d,w=datum();d['samples'].append(copy.deepcopy(s));self.assertEqual(len(rows_for_training(d,w)[0]),1)
 def test_cross_scope(self):
  s,d,w=datum();s['scope']='other'
  with self.assertRaisesRegex(ContractError,'CROSS_PERIOD'):rows_for_training(d,w)
 def test_late_teacher(self):
  s,d,w=datum();s['teacher']={'distillation_allowed':True,'input_id':'input1','available_at_ms':s['anchor_ms']+61000,'forecast':{'horizon_sec':300,'issued_at_ms':s['anchor_ms']}};self.assertIsNone(teacher_bins(s,300,w['training_end_ms']))
 def test_hold_auxiliary(self):
  s,d,w=datum();s['teacher']={'distillation_allowed':True,'input_id':'input1','available_at_ms':s['anchor_ms']+1000,'forecast':{'action':'HOLD','confidence':.9,'horizon_sec':300,'issued_at_ms':s['anchor_ms'],'reference_mid':s['reference_mid'],'scenarios':[{'probability':.4,'move_bps':-2},{'probability':.6,'move_bps':2}]}};self.assertAlmostEqual(sum(teacher_bins(s,300,w['training_end_ms'])),1)
 def test_question_key(self):self.assertIn('ins',question(300));self.assertNotIn('inst',question(300))
 def test_nonfinite_loss(self):
  with self.assertRaisesRegex(ContractError,'LOSS_INPUT'):supervised_loss(torch.full((1,7),float('nan')),torch.tensor([0]))
 def test_bad_teacher(self):
  with self.assertRaisesRegex(ContractError,'PROBABILITIES'):supervised_loss(torch.zeros(1,7),torch.tensor([0]),torch.ones(1,7),torch.tensor([True]))
class IntegrityTests(unittest.TestCase):
 def snapshot(self,root):
  paths={n:b'fixture bytes' for n in REQUIRED};paths['encoder/config.json']=b'{"model_type":"modernbert"}'
  for n,raw in paths.items():p=root/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
  return {'schema':'heptatrader.laya-base.v1','repository':LAYA_REPO,'revision':LAYA_REVISION,'files':{n:{'size':len(v),'sha256':hashlib.sha256(v).hexdigest()} for n,v in paths.items()}}
 def test_full_bound(self):
  with tempfile.TemporaryDirectory() as t:m=self.snapshot(Path(t));self.assertEqual(verify_snapshot(Path(t),m),digest(m))
 def test_no_tokenizer_rewrite(self):
  with tempfile.TemporaryDirectory() as t:
   r=Path(t);m=self.snapshot(r);p=r/'tokenizer/tokenizer_config.json';p.write_bytes(b'changed')
   with self.assertRaisesRegex(ContractError,'SIZE_MISMATCH'):verify_snapshot(r,m)
   self.assertEqual(p.read_bytes(),b'changed')
 def test_no_encoder_only(self):
  with tempfile.TemporaryDirectory() as t:
   m=self.snapshot(Path(t));m['files'].pop('model.safetensors')
   with self.assertRaisesRegex(ContractError,'FULL_LAYA'):verify_snapshot(Path(t),m)
 def test_wrong_revision(self):
  with tempfile.TemporaryDirectory() as t:
   m=self.snapshot(Path(t));m['revision']='f'*40
   with self.assertRaisesRegex(ContractError,'BASE_IDENTITY'):verify_snapshot(Path(t),m)
 def test_no_pickle(self):
  with tempfile.TemporaryDirectory() as t:
   m=self.snapshot(Path(t));(Path(t)/'weights.pt').write_bytes(b'x')
   with self.assertRaisesRegex(ContractError,'PICKLE'):verify_snapshot(Path(t),m)
 def test_no_symlink(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);(p/'linked').symlink_to('/tmp')
   with self.assertRaisesRegex(ContractError,'SYMLINK'):safe_child(p,'linked/x')
   with self.assertRaisesRegex(ContractError,'UNSAFE'):safe_child(p,'../x')
 def test_active_mlp_blocks(self):
  r=type('P',(),{'returncode':0,'stdout':'ActiveState=active\nMainPID=123\nUnitFileState=enabled\n'})()
  with patch('subprocess.run',return_value=r):
   with self.assertRaisesRegex(ContractError,'MLP_NOT_FROZEN'):verify_old_learner_inactive()
 def test_disabled_required(self):
  r=type('P',(),{'returncode':0,'stdout':'ActiveState=inactive\nMainPID=0\nUnitFileState=enabled\n'})()
  with patch('subprocess.run',return_value=r):
   with self.assertRaisesRegex(ContractError,'MLP_NOT_FROZEN'):verify_old_learner_inactive()
 def test_no_stop_in_training(self):
  r=type('P',(),{'returncode':0,'stdout':'ActiveState=inactive\nMainPID=0\nUnitFileState=disabled\n'})()
  with patch('subprocess.run',return_value=r) as call:verify_old_learner_inactive();self.assertNotIn('stop',call.call_args.args[0])
class NativeConformance(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  c=read_json(Path(os.environ['LAYA_TEST_CONFIG']));cls.n=NativePorts(Path(c['native_binary']),c['native_binary_sha256'])
 def request(self):
  return {'op':'laya_proposal',**{k:'1'*64 for k in ('base_id','selected_artifact_digest','dataset_digest','update_rule_digest','modulator_digest','eligibility_digest','evaluation_digest')},'generation':1,'layers':[{'name':'scorer.1','shape':[768,768],'baseline_squared_q64':str(2**80),'deltas':[]},{'name':'scorer.3','shape':[1,768],'baseline_squared_q64':str(2**76),'deltas':[]}]}
 def test_native_new_operation(self):
  r=self.n.call(self.request());self.assertEqual(r['candidate_count'],1);self.assertFalse(r['selection_performed'])
 def test_native_small_delta(self):
  r=self.request();r['layers'][0]['deltas']=[{'row':0,'col':0,'delta_q32':100}];o=self.n.call(r);self.assertEqual(o['candidate_count'],2)
 def test_native_same_coordinate_different_layers(self):
  r=self.request()
  for layer in r['layers']:layer['deltas']=[{'row':0,'col':0,'delta_q32':100}]
  out=self.n.call(r);self.assertEqual(out['candidate_count'],2)
 def test_native_wrong_mask(self):
  r=self.request();r['layers'][0]['deltas']=[{'row':4,'col':0,'delta_q32':100}]
  with self.assertRaisesRegex(ContractError,'EFFECTIVE_DELTA_MASK'):self.n.call(r)
 def test_native_duplicate_coordinates(self):
  r=self.request();r['layers'][0]['deltas']=[{'row':0,'col':0,'delta_q32':100}]*2
  with self.assertRaisesRegex(ContractError,'EFFECTIVE_DELTA_MASK'):self.n.call(r)
 def test_native_zero_denominator(self):
  r=self.request();r['layers'][0]['baseline_squared_q64']='0'
  with self.assertRaisesRegex(ContractError,'NORM_BOUND'):self.n.call(r)
 def test_native_unknown_fields(self):
  r=self.request();r['enable_trading']=True
  with self.assertRaisesRegex(ContractError,'UNKNOWN_CRITICAL'):self.n.call(r)
 def test_native_utility_oracle(self):
  r=self.n.call({'op':'utility','dataset_digest':'1'*64,'objective_digest':'2'*64,'outcome_digest':'3'*64,'events':[{'instant':.5,'discount':.5,'event_digest':'4'*64},{'instant':.25,'discount':1,'event_digest':'5'*64}]});self.assertEqual(r['value'],.625);self.assertFalse(r['effect_authority'])
if __name__=='__main__':torch.set_num_threads(1);unittest.main(verbosity=2)
