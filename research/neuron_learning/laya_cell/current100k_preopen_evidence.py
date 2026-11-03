"""Validate original terminals and reconcile canonical commands without inference."""
from pathlib import Path
from . import current100k_weight_shadow as W
from .financial_forward_source import file_identity
from .current100k_preopen import RequestLedger,load

class PreopenEvidence:
 def __init__(self,study_root):
  self.study_root=Path(study_root);self.plan=W.ShadowStore(self.study_root).plan
  self.activation_path=self.study_root/'activation.json';self.config_path=self.study_root/'predictor-config.json'
  self.activation=load(self.activation_path);self.config=load(self.config_path)
  self.activation_sha256=W.sha(self.activation_path);self.config_sha256=W.sha(self.config_path)
  self.plan_sha256=W.sha(self.study_root/'plan.json');self.registration_sha256=W.sha(self.study_root/'registration.json')
  W.require(self.activation['plan_id']==self.plan['plan_id'],'STRICT_PREOPEN_ACTIVATION_PLAN')
  W.require(self.activation['predictor_config_sha256']==self.config_sha256,'STRICT_PREOPEN_CONFIG_PIN')
  W.require(Path(self.config['study_plan_path'])==self.study_root/'plan.json' and self.config['study_plan_sha256']==W.sha(self.study_root/'plan.json'),'STRICT_PREOPEN_PLAN_PIN')
  W.require(self.config['source_registration']==self.plan['producer_registration'] and self.config['period_sha256']==self.plan['period_sha256'],'STRICT_PREOPEN_SOURCE_SCOPE')
  W.require(all(self.config.get(k)is False for k in ('trade_authority','automatic_selection','actor_update')),'STRICT_PREOPEN_NO_EFFECT_AUTHORITY')
  self.root=Path(self.config['state_root']);W.require(self.root.is_absolute() and not self.root.is_symlink(),'STRICT_PREOPEN_ROOT')
 def identity(self):
  paths=[self.activation_path,self.config_path,self.study_root/'plan.json',self.study_root/'registration.json',self.root/'protocol.json']
  names={'input.json','attempt.json','terminal.json','original-command.json'}
  paths+=sorted(p for p in (self.root/'requests').glob('*/*.json') if p.name in names)
  paths+=sorted((self.root/'bindings').glob('*.json'))
  return [(str(p),file_identity(p) if p.exists() else None) for p in paths]
 def _checked_terminal(self,binding,prediction):
  W.require(W.sha(self.activation_path)==self.activation_sha256 and W.sha(self.config_path)==self.config_sha256
            and W.sha(self.study_root/'plan.json')==self.plan_sha256 and W.sha(self.study_root/'registration.json')==self.registration_sha256,'STRICT_PREOPEN_PINS_CHANGED')
  p=self.plan;protocol=load(self.root/'protocol.json')
  for key in ('plan_id','input_start_ms','input_end_ms','period_sha256','producer_registration'):
   W.require(protocol[key]==p[key],'STRICT_PREOPEN_PROTOCOL:'+key)
  age=self.config.get('max_request_age_ms',60000)
  W.require(type(age)is int and 1<=age<=60000 and protocol['max_request_age_ms']==age,'STRICT_PREOPEN_REQUEST_DEADLINE')
  W.require(protocol['study_plan_sha256']==self.config['study_plan_sha256'],'STRICT_PREOPEN_PROTOCOL_PLAN_PIN')
  rid,cid=W.safe_id(binding['request_id']),W.safe_id(binding['command']['command_id']);folder=self.root/'requests'/rid
  terminal=load(folder/'terminal.json')
  W.require(terminal.get('status')=='SEALED_AWAITING_ORIGINAL_COMMAND','STRICT_PREOPEN_TERMINAL:'+str(terminal.get('status')))
  for key,expected in (('plan_id',p['plan_id']),('request_id',rid),('request_sha256',binding['request_sha256']),('producer_manifest_sha256',p['producer_registration']['sha256'])):
   W.require(terminal.get(key)==expected,'STRICT_PREOPEN_TERMINAL_IDENTITY:'+key)
  item_record=load(folder/'input.json');item=item_record['request_item']
  W.require(item_record['plan_id']==p['plan_id'] and item['request_sha256']==binding['request_sha256'] and W.digest(item['request'])==binding['request_sha256'] and item['request']['started_at_ms']==binding['request_started_at_ms'],'STRICT_PREOPEN_ORIGINAL_INPUT')
  attempt=load(folder/'attempt.json')
  for key,expected in (('plan_id',p['plan_id']),('request_id',rid),('request_sha256',binding['request_sha256'])):
   W.require(attempt.get(key)==expected,'STRICT_PREOPEN_ATTEMPT_IDENTITY:'+key)
  deadline=min(binding['request_started_at_ms']+age,p['input_end_ms']);start=W.integer(attempt['started_at_ms']);observed=W.integer(terminal['publication_durability_observed_at_ms'])
  W.require(attempt['deadline_ms']==deadline and binding['request_started_at_ms']<=start<=prediction['committed_at_ms']<=observed<deadline and observed<binding['command']['created_at_ms'],'STRICT_PREOPEN_LATE_OR_DEADLINE')
  elapsed=W.finite(terminal['elapsed_ms']);W.require(0<=elapsed<deadline-start,'STRICT_PREOPEN_MONOTONIC_DEADLINE')
  W.require(terminal.get('publication')==prediction,'STRICT_PREOPEN_PUBLICATION_RECEIPT')
  return rid,cid,folder,terminal,protocol
 def check(self,binding,prediction):
  rid,cid,folder,terminal,_=self._checked_terminal(binding,prediction);p=self.plan
  original=load(folder/'original-command.json')
  W.require(original=={'request_id':rid,'request_sha256':binding['request_sha256'],'command_id':cid},'STRICT_PREOPEN_ORIGINAL_COMMAND')
  linked=load(self.root/'bindings'/(cid+'.json'))
  W.require(linked.get('plan_id')==p['plan_id'] and linked.get('binding')==binding and linked.get('publication')==prediction and linked.get('status')=='PREOPEN_BOUND_RESEARCH' and linked.get('prediction_terminal')==terminal['status'] and linked.get('before_original_command')is True,'STRICT_PREOPEN_BINDING_NO_CREDIT')
  return {'terminal_sha256':W.sha(folder/'terminal.json'),'binding_sha256':W.sha(self.root/'bindings'/(cid+'.json')),'prediction_terminal':terminal['status'],'publication_durability_observed_at_ms':terminal['publication_durability_observed_at_ms']}
 def reconcile_settlement(self,binding,prediction,first_fill_ms):
  """Bind an existing successful seal to a verified real fill without inference.

  Frozen cutoffs apply to event timestamps. This audit observation uses the
  current clock, including a final assessment of earlier events. No missing
  attempt/terminal is recovered and no prediction is replayed.
  """
  now=W.clock_ms();p=self.plan;cmd=binding['command']
  W.require(now>=p['input_end_ms'],'STRICT_PREOPEN_SETTLEMENT_PHASE')
  started=W.integer(binding['request_started_at_ms']);prepared=W.integer(cmd['created_at_ms'])
  first=W.integer(first_fill_ms)
  W.require(p['input_start_ms']<=started<p['input_end_ms'],'STRICT_PREOPEN_SETTLEMENT_REQUEST_WINDOW')
  W.require(cmd.get('kind')=='OPEN' and cmd.get('instrument')=='EUR.USD' and cmd.get('side') in W.SIDES
            and binding['producer_manifest_sha256']==p['producer_registration']['sha256'],'STRICT_PREOPEN_SETTLEMENT_COMMAND_SCOPE')
  W.require(started<=prepared<=first<=now and first<=p['outcome_end_ms'],'STRICT_PREOPEN_SETTLEMENT_EXECUTION_CUTOFF')
  W.require(started<=prediction['computed_at_ms']<=prediction['committed_at_ms']<prepared
            and prediction['committed_at_ms']<first,'STRICT_PREOPEN_SETTLEMENT_PREDICTION_CLOCK')
  _,cid,_,_,protocol=self._checked_terminal(binding,prediction)
  if (self.root/'bindings'/(cid+'.json')).exists():return self.check(binding,prediction)
  ledger=RequestLedger(self.root,protocol,clock=W.clock_ms)
  try:
   # Revalidate under the original writer's nonblocking lock. Only an existing
   # successful terminal can reach reconcile's publication path.
   self._checked_terminal(binding,prediction)
   linked=ledger.reconcile(binding,existing_success_only=True)
   W.require(linked is not None and linked.get('status')=='PREOPEN_BOUND_RESEARCH','STRICT_PREOPEN_SETTLEMENT_NO_CREDIT')
  finally:ledger.close()
  return self.check(binding,prediction)
 def provenance(self):
  directory=Path(__file__).resolve().parent
  return {'schema':'heptatrader.weight-shadow-strict-review.v1','plan_id':self.plan['plan_id'],'source_root':str(self.study_root),'original_plan_sha256':W.sha(self.study_root/'plan.json'),'original_registration_sha256':W.sha(self.study_root/'registration.json'),'activation_sha256':self.activation_sha256,'predictor_config_sha256':self.config_sha256,'review_implementation_sha256':{name:W.sha(directory/name) for name in ('current100k_preopen_evidence.py','current100k_weight_shadow_eval.py','current100k_weight_shadow.py','current100k_outcome_export.py')},'original_window_modified':False,'original_reports_modified':False,'request_replayed':False,'broker_orders_sent':0,'review_reason':'Require original successful pre-open terminal and exact binding; no credit for durable predictions from failed or late attempts.'}
