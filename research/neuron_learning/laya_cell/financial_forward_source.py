"""Verified append-only attribution ingress and immutable financial plan registry."""
from __future__ import annotations
import copy,hashlib,json,stat,time
from pathlib import Path
from .integrity import ContractError,read_json,sha_file,digest
_original_sha_file=sha_file
def sha_file(path):return _original_sha_file(Path(path))


def file_identity(path):
 """Cheap invalidation of a file already read with read_private this turn."""
 p=Path(path);s=p.lstat()
 if not stat.S_ISREG(s.st_mode):raise ContractError('VALUE_SNAPSHOT_FILE_TYPE')
 return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_mode,s.st_uid,s.st_gid,s.st_nlink)


def ingress_identity(registration,period_sha256):
 """Re-read business health, not heartbeat timestamps, before cache reuse."""
 from hepta_decision_attribution import read_private,strict
 raw=read_private(registration['manifest'],65536)
 if hashlib.sha256(raw).hexdigest()!=registration['sha256']:raise ContractError('REGISTERED_PRODUCER_CHANGED')
 manifest=strict(raw)
 if manifest.get('instrument')!='EUR.USD' or manifest.get('execution_domain')!='PAPER' or manifest.get('period_sha256')!=period_sha256:
  raise ContractError('FORWARD_PRODUCER_SCOPE')
 root=Path(manifest['journal_directory']);segments=sorted(root.glob('*.jsonl'))
 if len(segments)>128:raise ContractError('ATTRIBUTION_STORAGE_BOUND')
 health={}
 for path in sorted(root.glob('*.health.json')):
  value=strict(read_private(path,65536))
  if not isinstance(value,dict):raise ContractError('VALUE_HEALTH_SHAPE')
  health[str(path)]={k:v for k,v in value.items() if k!='at_ms'}
 return {'registration':digest(registration),'period_sha256':period_sha256,
         'segments':[(str(p),file_identity(p)) for p in segments],'health':health}


class VerifiedIngressSnapshot:
 """One bounded turn only; never an on-disk or cross-process authorization."""
 def __init__(self,registration,period_sha256):
  self.registration=copy.deepcopy(registration);self.period_sha256=period_sha256
  self.full_scans=0;self.reuses=0;self._capture()
 def _capture(self):
  before=ingress_identity(self.registration,self.period_sha256)
  value=ingress(self.registration,self.period_sha256)
  after=ingress_identity(self.registration,self.period_sha256)
  if before!=after:raise ContractError('VALUE_SOURCE_CHANGED_DURING_INGRESS')
  self._identity=after;self._value=value;self.full_scans+=1
  self.captured_at_ms=int(time.time()*1000)
 def read(self,registration,period_sha256):
  if digest(registration)!=digest(self.registration) or period_sha256!=self.period_sha256:
   raise ContractError('VALUE_INGRESS_SNAPSHOT_SCOPE')
  if ingress_identity(registration,period_sha256)!=self._identity:self._capture()
  else:self.reuses+=1
  return copy.deepcopy(self._value)


def ingress(registration,period_sha256):
 from hepta_decision_attribution import read_private,strict,digest as provenance_digest
 raw=read_private(registration['manifest'],65536)
 if hashlib.sha256(raw).hexdigest()!=registration['sha256']:raise ContractError('REGISTERED_PRODUCER_CHANGED')
 m=strict(raw)
 if m.get('instrument')!='EUR.USD' or m.get('execution_domain')!='PAPER' or m.get('period_sha256')!=period_sha256:
  raise ContractError('FORWARD_PRODUCER_SCOPE')
 paths=sorted(Path(m['journal_directory']).glob('*.jsonl'))
 if len(paths)>128:raise ContractError('ATTRIBUTION_STORAGE_BOUND')
 records={};prefixes={}
 for p in paths:
  data=read_private(p);data=data[:data.rfind(b'\n')+1];h=hashlib.sha256();offset=0
  for line in data.splitlines(keepends=True):
   r=strict(line);h.update(line);offset+=len(line)
   if r.get('schema')!='heptatrader.exact-decision-attribution.v1' or provenance_digest({k:v for k,v in r.items() if k!='sha256'})!=r.get('sha256'):
    raise ContractError('FORWARD_SOURCE_HASH')
   producer=r['producer_id'];records.setdefault(producer,[]).append(r)
   if r['kind']=='REQUEST':prefixes[(producer,r['sequence'])]={'path':str(p),'bytes':offset,'sha256':h.hexdigest()}
 requests={};bindings=[];issues=[]
 for producer,rs in records.items():
  rs.sort(key=lambda x:x['sequence']);previous='0'*64;invalid=False
  health=Path(m['journal_directory'])/(producer+'.health.json')
  if health.exists():
   status=strict(read_private(health,65536));invalid=bool(status.get('dropped') or status.get('error'))
  headers=[x['data'] for x in rs if x.get('kind')=='HEADER'];owners={x.get('manifest_sha256') for x in headers}
  if len(owners)!=1 or not headers or any(not isinstance(x,str) or len(x)!=64 for x in owners):raise ContractError('FORWARD_PRODUCER_MANIFEST_MIXED')
  producer_manifest=next(iter(owners))
  for i,r in enumerate(rs):
   if r['sequence']!=i or r['previous']!=previous:raise ContractError('FORWARD_CHAIN_DISCONTINUITY')
   previous=r['sha256'];invalid|=bool(r.get('dropped_total'));v=r['data']
   if r['kind']=='HEADER':
    if (v.get('manifest_sha256')!=producer_manifest or v.get('period_sha256')!=period_sha256 or v.get('trading_authority') is not False
        or v.get('instrument')!='EUR.USD' or v.get('execution_domain')!='PAPER' or v.get('source_kind')!='PINNED_DRIVER_CALLSITE'):
     raise ContractError('FORWARD_SOURCE_HEADER')
   elif i==0:raise ContractError('FORWARD_SOURCE_HEADER_MISSING')
  if producer_manifest!=registration['sha256']:continue
  if invalid:issues.append({'producer_id':producer,'reason':'PRODUCER_GAP_OR_LOSS'});continue
  for r in rs:
   v=r['data']
   if r['kind']=='REQUEST':
    if (v.get('period_sha256')!=period_sha256 or v.get('instrument')!='EUR.USD'
        or hashlib.sha256(v['prompt'].encode()).hexdigest()!=v['prompt_sha256']):raise ContractError('FORWARD_REQUEST_SCOPE')
    item={'request':v,'request_sha256':provenance_digest(v),'producer_manifest_sha256':registration['sha256'],
     'source_record_sha256':r['sha256'],'source_written_at_ms':r['written_at_ms'],'source_prefix':prefixes[(producer,r['sequence'])]}
    rid=v['request_id']
    if rid in requests and requests[rid]!=item:raise ContractError('FORWARD_REQUEST_COLLISION')
    requests[rid]=item
   elif r['kind']=='COMMAND_BINDING':bindings.append(v)
 # Commands get attributed only by the original independently hardened importer.
 from hepta_decision_attribution import verified_bindings
 verified,problems=verified_bindings(registration,'EUR.USD',period_sha256);issues+=problems
 normalized=[]
 for b in bindings:
  cmd=b['command'];cid=cmd.get('command_id');rid=b['request_id']
  if cid not in verified or rid not in requests:continue
  r=requests[rid]
  if verified[cid]['request_id']!=rid or b['request_sha256']!=r['request_sha256']:raise ContractError('FORWARD_BINDING_MISMATCH')
  if any(x['command']['command_id']==cid for x in normalized):
   prior=next(x for x in normalized if x['command']['command_id']==cid)
   if prior['request_id']!=rid:raise ContractError('MULTIPLE_REQUESTS_FOR_COMMAND')
   continue
  normalized.append({'command':cmd,'request_id':rid,'request_sha256':r['request_sha256'],
   'request_started_at_ms':r['request']['started_at_ms'],'producer_manifest_sha256':registration['sha256'],
   'decision_sha256':b['decision_sha256']})
 return m,requests,normalized,issues


def register_available(config):
 from .resident_store import write_once
 from .financial_forward_protocol import RULE,validate_candidate,validate_plan
 root=Path(config['forward_root']);plans=root/'plans';plans.mkdir(mode=0o700,parents=True,exist_ok=True)
 candidates=sorted(Path(config['value_output_root']).glob('jobs/*/artifact/candidate.json'))
 if len(candidates)>256:raise ContractError('FORWARD_CANDIDATE_CAPACITY')
 existing=[read_json(p) for p in plans.glob('*/plan.json')]
 for plan in existing:validate_plan(plan)
 registered={x['candidate_id'] for x in existing};active_scopes={digest(x['behavior_scope']) for x in existing if int(time.time()*1000)<x['outcome_end_ms']}
 regs={r['sha256']:r for r in config['decision_attribution_registrations'].get('EUR.USD',[])}
 admitted=[]
 for path in sorted(candidates,key=lambda p:read_json(p)['created_at_ms']):
  c=validate_candidate(path.parent);scope=c['behavior_scope'];key=digest(scope)
  # Retain every candidate, but do not consume future trials for stopped behavior.
  if scope['producer_manifest_sha256']!=config.get('active_behavior_manifest_sha256'):continue
  if c['candidate_id'] in registered or key in active_scopes:continue
  if scope['producer_manifest_sha256'] not in regs:continue
  # Full retained training rows bind baselines/overlap checks before future input.
  dataset=path.parent.parent/'dataset.json'
  if sha_file(dataset)!=c['dataset_sha256']:raise ContractError('FORWARD_TRAINING_DATASET_HASH')
  data=read_json(dataset,32*1024**2);rows=data['rows']
  if [r['reward_id'] for r in rows]!=c['reward_ids']:raise ContractError('FORWARD_TRAINING_REWARD_SET')
  registered_ms=int(time.time()*1000);start=registered_ms+5000;trial=len(existing)+len(admitted)+1
  root_src=Path(__file__).parent
  plan={'schema':'heptatrader.financial-forward-plan.v1','candidate_id':c['candidate_id'],'candidate_directory':str(path.parent),
   'candidate_manifest_sha256':sha_file(path),'head_tensor_sha256':c['head_tensor_sha256'],'base_id':c['base_id'],
   'candidate_created_at_ms':c['created_at_ms'],'registered_at_ms':registered_ms,'input_start_ms':start,
   'input_end_ms':start+RULE['window_ms'],'outcome_end_ms':start+RULE['window_ms']+RULE['closure_grace_ms'],
   'training_dataset_sha256':c['dataset_sha256'],'training_dataset_path':str(dataset),
   'training_reward_ids':c['reward_ids'],'training_input_ids':[r['input_id'] for r in rows],
   'training_mean_usd':sum(float(r['net_usd']) for r in rows)/len(rows),
   'behavior_scope':scope,'producer_registration':regs[scope['producer_manifest_sha256']],
   'period_sha256':config['period_sha256'],'rule':RULE,'trial':trial,'synthetic':False,
   'runtime_sha256':{name:sha_file(root_src/name) for name in ('financial_forward_protocol.py','financial_forward_source.py','financial_forward_predict.py','financial_forward_evaluate.py','financial_forward_witness.py')},
   'witness_trust_digest':config['witness_trust_digest'],'registry_root':config['forward_root'],
   'automatic_effect_authority':False,'candidate_refit_allowed':False}
  plan['plan_id']=digest(plan);validate_plan(plan,registered_ms)
  write_once(plans/plan['plan_id']/'plan.json',plan)
  from .financial_forward_witness import sign_record
  seal=sign_record(config,'registrar',{'plan_id':plan['plan_id'],'plan_file_sha256':sha_file(plans/plan['plan_id']/'plan.json'),'at_ms':int(time.time()*1000)})
  write_once(plans/plan['plan_id']/'registration.json',seal);admitted.append(plan);active_scopes.add(key)
 return existing+admitted
