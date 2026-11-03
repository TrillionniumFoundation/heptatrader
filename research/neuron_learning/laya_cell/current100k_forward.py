"""Current USD100k Value scope on the existing signed prospective framework.

Never relabel this head as the old 25,000 EUR experiment. Reuse only the common
future timing, signed custody and bounded-loss comparison; no actor selection.
"""
from __future__ import annotations
import ctypes, fcntl, hashlib, math, os, re, stat, time
from contextlib import contextmanager
from pathlib import Path
from .integrity import ContractError, digest, read_json, sha_file
from .resident_store import write_once
from .financial_value_profile import check_readout_profile

PLAN_SCHEMA='heptatrader.current100k-value-forward-plan.v1'
CANDIDATE_SCHEMA='heptatrader.current100k-value-candidate.v1'
SAME_PRODUCER_REFIT='SAME_PRODUCER_REFIT'
NEW_PRODUCER_SCOPE='NEW_PRODUCER_SCOPE'
REGISTRATION_PUBLISH_PROFILE='ATOMIC_SIGNED_PLAN_DIRECTORY_V1'
REGISTRATION_PENDING='.current100k-registration.pending'

def validate_value_candidate(directory,c=None):
 from historical_training.current100k import BEHAVIOR
 directory=Path(directory);c=read_json(directory/'candidate.json') if c is None else c
 check_readout_profile(c.get('readout_profile'))
 if c.get('schema')!=CANDIDATE_SCHEMA or c.get('candidate_id')!=digest({k:v for k,v in c.items() if k!='candidate_id'}):
  raise ContractError('CURRENT100K_FORWARD_CANDIDATE_HASH')
 if (c.get('training_scope')!=BEHAVIOR or c.get('pnl_reward_used') is not True
     or c.get('reward_revalidated_before_and_after') is not True
     or c.get('pretrained_backbone_unchanged') is not True
     or any(c.get(k) is not False for k in ('scorer_or_actor_updated','on_policy_policy_gradient','automatic_selection','canonical_owner_accepted'))
     or c.get('broker_orders_sent')!=0 or c.get('scale_usd')!=100 or c.get('status')!='UNVALIDATED_BEHAVIOR_VALUE_ONLY'):
  raise ContractError('CURRENT100K_FORWARD_CANDIDATE_SCOPE')
 if (type(c.get('rows'))is not int or not 1<=c['rows']<=64 or len(c['row_ids'])!=c['rows']
     or len(set(c['row_ids']))!=c['rows'] or sha_file(directory/'value-head.safetensors')!=c['head_sha256']):
  raise ContractError('CURRENT100K_FORWARD_HEAD_OR_ROWS')
 from .resident_hip_runtime import check_publication_proof
 check_publication_proof(c.get('hip_runtime_proof'),c.get('hip_runtime_manifest_sha256'),'train')
 return c

def validate_bound_plan(plan,c):
 from historical_training.current100k import BEHAVIOR,to_row
 from .financial_value_data import ExactRequestIndex
 from .financial_forward_protocol import validate_plan
 check_readout_profile(c.get('readout_profile'))
 validate_plan(plan,int(time.time()*1000))
 if plan.get('schema')!=PLAN_SCHEMA:raise ContractError('CURRENT100K_FORWARD_PLAN_SCHEMA')
 if plan.get('warm_flat_profile') is not None:
  from .current100k_warm_flat import check_profile
  check_profile(plan['warm_flat_profile'])
  if 'current100k_warm_flat.py' not in plan.get('runtime_sha256',{}):raise ContractError('VALUE_WARM_PLAN_SOURCE_PIN_REQUIRED')
 if plan.get('cpu_standby_profile') is not None:
  from .current100k_cpu_standby import check_profile,MODULE
  check_profile(plan['cpu_standby_profile'])
  if plan.get('warm_flat_profile') is not None:raise ContractError('VALUE_CPU_STANDBY_MIXED_PROFILE')
  if MODULE not in plan.get('runtime_sha256',{}):raise ContractError('VALUE_CPU_STANDBY_PLAN_SOURCE_PIN_REQUIRED')
 scope=plan.get('behavior_scope',{})
 if (scope.get('behavior_profile')!=BEHAVIOR or scope.get('reference_notional_usd')!=100000
     or scope.get('quantity') is not None or plan.get('producer_registration',{}).get('sha256')!=scope.get('producer_manifest_sha256') or scope.get('instrument')!='EUR.USD' or scope.get('currency')!='USD'
     or plan.get('candidate_id')!=c['candidate_id'] or plan.get('candidate_created_at_ms')!=c['created_at_ms']
     or plan.get('head_tensor_sha256')!=c['head_sha256'] or plan.get('training_dataset_sha256')!=c['dataset_sha256']
     or plan.get('hip_runtime_manifest_sha256')!=c.get('hip_runtime_manifest_sha256')
     or plan.get('readout_profile')!=c.get('readout_profile')):
  raise ContractError('CURRENT100K_FORWARD_PLAN_SCOPE')
 path=Path(plan['training_dataset_path'])
 if sha_file(path)!=c['dataset_sha256']:raise ContractError('CURRENT100K_FORWARD_DATASET_PIN')
 data=read_json(path,32*1024**2);rows=data['rows']
 if (data['period_sha256']!=plan['period_sha256'] or data['producer_manifest_sha256']!=scope['producer_manifest_sha256']
     or [x['row_id'] for x in rows]!=c['row_ids'] or [x['reward_id'] for x in rows]!=plan['training_reward_ids']
     or [x['input_id'] for x in rows]!=plan['training_input_ids']
     or {x['behavior_scope']['model_id'] for x in rows}!={scope['behavior_model_id']}):
  raise ContractError('CURRENT100K_FORWARD_TRAINING_BINDING')
 source_index=ExactRequestIndex([x['reward'] for x in rows])
 for x in rows:
  if x!=to_row(x['reward'],x['source_request'],x['source_binding'],data['producer_manifest_sha256'],data['period_sha256'],source_index=source_index):
   raise ContractError('CURRENT100K_FORWARD_REWARD_RECOMPUTATION')
 expected=sum(float(x['net_usd']) for x in rows)/len(rows)
 if plan['training_mean_usd']!=expected:raise ContractError('CURRENT100K_FORWARD_BASELINE_CHANGED')
 return plan

def feature_rows(plan,request):
 from paired_net_100k_preflight.core import feature_text
 from historical_training.current100k import BEHAVIOR
 scope=plan.get('behavior_scope',{})
 if (plan.get('schema')!=PLAN_SCHEMA or scope.get('behavior_profile')!=BEHAVIOR
     or scope.get('reference_notional_usd')!=100000 or request.get('model_id')!=scope.get('behavior_model_id')):
  raise ContractError('CURRENT100K_FORWARD_FEATURE_SCOPE')
 return [{'feature_text':feature_text(request,side)} for side in ('BUY','SELL')]

def verify_scored_row(plan,binding,prediction,reward,request_item):
 from historical_training.current100k import to_row
 if reward is None:return
 if request_item is None:raise ContractError('CURRENT100K_FORWARD_ORIGINAL_REQUEST_MISSING')
 row=to_row(reward,request_item,binding,plan['behavior_scope']['producer_manifest_sha256'],plan['period_sha256'])
 if row['input_id'] in plan['training_input_ids'] or row['reward_id'] in plan['training_reward_ids']:
  raise ContractError('CURRENT100K_FORWARD_TRAIN_TEST_OVERLAP')
 if prediction is not None:
  text=feature_rows(plan,request_item['request'])
  hashes={side:hashlib.sha256(x['feature_text'].encode()).hexdigest() for side,x in zip(('BUY','SELL'),text)}
  if prediction.get('feature_text_sha256')!=hashes:raise ContractError('CURRENT100K_FORWARD_FEATURE_IDENTITY')

def _new_producer_lineage(config,data,prior,prior_directory,history,prior_plan_path):
 """A changed producer starts a fresh fit, retaining the original trial budget.

 Validate historical evidence once at registration. New prediction workers do
 not acquire an ongoing dependency on an old producer's retired runtime.
 """
 from .financial_forward_protocol import validate_plan
 from historical_training.current100k import validate_config
 if (prior_plan_path is None or config.get('automatic_trading') is not False
     or config.get('actor_update') is not False):
  raise ContractError('NEW_PRODUCER_SCOPE_REQUIRES_PRIOR_PLAN_NO_ACTOR')
 prior_plan_path=Path(prior_plan_path);previous=read_json(prior_plan_path)
 for plan in history:
  validate_plan(plan,int(time.time()*1000))
  if (plan.get('schema') not in (PLAN_SCHEMA,'heptatrader.financial-forward-plan.v1')
      or any(not isinstance(plan.get(key),list) for key in ('training_input_ids','training_reward_ids'))):
   raise ContractError('NEW_PRODUCER_SCOPE_COMPLETE_TRIAL_HISTORY_REQUIRED')
 trials=[plan['trial'] for plan in history]
 if (not trials or len({p['plan_id'] for p in history})!=len(history)
     or sorted(trials)!=list(range(1,len(trials)+1))
     or previous not in history or previous['trial']!=max(trials)):
  raise ContractError('NEW_PRODUCER_SCOPE_COMPLETE_TRIAL_HISTORY_REQUIRED')
 if (previous.get('schema')!=PLAN_SCHEMA or previous.get('candidate_id')!=prior['candidate_id']
     or previous.get('candidate_manifest_sha256')!=sha_file(prior_directory/'candidate.json')):
  raise ContractError('NEW_PRODUCER_SCOPE_PRIOR_CANDIDATE_BINDING')
 validate_bound_plan(previous,prior)
 old_scope=previous['behavior_scope']
 if (data['period_sha256']!=previous['period_sha256'] or config['period_sha256']!=previous['period_sha256']
     or {r['behavior_scope']['model_id'] for r in data['rows']}!={old_scope['behavior_model_id']}):
  raise ContractError('NEW_PRODUCER_SCOPE_BEHAVIOR_OR_PERIOD_CHANGED')
 manifest=validate_config({'automatic_trading':False,'automatic_training':False,
  'period_path':config['period_path'],'period_sha256':config['period_sha256'],
  'registration':config['producer_registration']})
 producer=data['producer_manifest_sha256']
 if (manifest.get('schema')!='heptatrader.attribution-producer.v1'
     or producer!=config['producer_registration']['sha256']
     or producer==old_scope['producer_manifest_sha256']):
  raise ContractError('NEW_PRODUCER_SCOPE_REQUIRES_CHANGED_REGISTERED_PRODUCER')
 if (set(prior['row_ids']) & {r['row_id'] for r in data['rows']}
     or {rid for p in history for rid in p['training_input_ids']} & {r['input_id'] for r in data['rows']}
     or {rid for p in history for rid in p['training_reward_ids']} & {r['reward_id'] for r in data['rows']}):
  raise ContractError('NEW_PRODUCER_SCOPE_HISTORICAL_TRAINING_OVERLAP')
 return {'registration_scope':NEW_PRODUCER_SCOPE,'prior_plan_id':previous['plan_id'],
  'prior_plan_sha256':sha_file(prior_plan_path),'prior_trial':previous['trial'],
  'prior_producer_manifest_sha256':old_scope['producer_manifest_sha256'],
  'previous_rows_reused_in_refit':0,'fit_relation':'FRESH_PRODUCER_SCOPE_FRESH_HEAD_NO_HISTORICAL_ROWS'}

def _prepare_registration(config,candidate_directory,dataset_path,prior_candidate_path,legacy_plans_root,
             *,registration_scope=SAME_PRODUCER_REFIT,prior_plan_path=None,input_start_delay_ms=300000,
             request_sha256):
 from .financial_forward_protocol import RULE
 from historical_training.current100k_value_trial import selected_rows
 # Preparation may need a longer prospective lead time; it must never backdate
 # an input window or change the frozen observation and outcome durations.
 if type(input_start_delay_ms)is not int or not 300000<=input_start_delay_ms<=900000:
  raise ContractError('CURRENT100K_FORWARD_PREPARATION_DELAY_BOUND')
 c=validate_value_candidate(candidate_directory);data=read_json(Path(dataset_path),32*1024**2);selected_rows(data,data)
 prior_directory=Path(prior_candidate_path).parent;prior=validate_value_candidate(prior_directory)
 roots=[legacy_plans_root] if isinstance(legacy_plans_root,(str,Path)) else list(legacy_plans_root)
 legacy=[read_json(x) for root in roots for x in sorted(Path(root).glob('*/plan.json'))]
 lineage={'previous_rows_reused_in_refit':len(prior['row_ids']),
          'fit_relation':'FRESH_HEAD_REFIT_NOT_INCREMENTAL_ACTOR_UPDATES'}
 if registration_scope==SAME_PRODUCER_REFIT:
  if not set(prior['row_ids'])<=set(c['row_ids']):raise ContractError('PRIOR_ELEVEN_ROWS_NOT_PRESERVED')
 elif registration_scope==NEW_PRODUCER_SCOPE:
  lineage=_new_producer_lineage(config,data,prior,prior_directory,legacy,prior_plan_path)
 else:raise ContractError('CURRENT100K_REGISTRATION_SCOPE_UNKNOWN')
 trial=max([x['trial'] for x in legacy]+[0])+1
 now=int(time.time()*1000);start=now+input_start_delay_ms;rows=data['rows'];models={x['behavior_scope']['model_id'] for x in rows}
 if len(models)!=1:raise ContractError('MIXED_BEHAVIOR_MODELS')
 source=Path(__file__).parent
 plan={'schema':PLAN_SCHEMA,'candidate_id':c['candidate_id'],'candidate_directory':str(candidate_directory),
  'registration_publish_profile':REGISTRATION_PUBLISH_PROFILE,'registration_request_sha256':request_sha256,
  'candidate_manifest_sha256':sha_file(Path(candidate_directory)/'candidate.json'),'head_tensor_sha256':c['head_sha256'],'base_id':c['base_id'],
  'candidate_created_at_ms':c['created_at_ms'],'registered_at_ms':now,'input_start_ms':start,
  'input_end_ms':start+RULE['window_ms'],'outcome_end_ms':start+RULE['window_ms']+RULE['closure_grace_ms'],
  'training_dataset_sha256':sha_file(Path(dataset_path)),'training_dataset_path':str(dataset_path),
  'training_reward_ids':[x['reward_id'] for x in rows],'training_input_ids':[x['input_id'] for x in rows],
  'training_mean_usd':sum(float(x['net_usd']) for x in rows)/len(rows),'prior_candidate_id':prior['candidate_id'],
  'prior_fit_rows':prior['rows'],'current_fit_rows':len(rows),**lineage,
  'behavior_scope':{'behavior_profile':data['behavior_profile'],'reference_notional_usd':100000,'instrument':'EUR.USD','currency':'USD',
                    'behavior_model_id':next(iter(models)),'producer_manifest_sha256':data['producer_manifest_sha256']},
  'producer_registration':config['producer_registration'],'period_sha256':config['period_sha256'],'rule':dict(RULE),'trial':trial,'synthetic':False,
  'runtime_sha256':{name:sha_file(source/name) for name in ('current100k_forward.py','financial_forward_protocol.py','financial_forward_source.py',
                   'financial_forward_predict.py','financial_forward_evaluate.py','financial_forward_witness.py')},
  'witness_trust_digest':config['witness_trust_digest'],'registry_root':config['forward_root'],
  'worker_runtime_inventory_digest':config['worker_runtime_inventory_digest'],
  'automatic_effect_authority':False,'candidate_refit_allowed':False,'old_trial_ids_retained':[x['plan_id'] for x in legacy]}
 if config.get('hip_runtime') is not None:
  plan['hip_runtime_manifest_sha256']=config['hip_runtime']['sha256']
 if c.get('readout_profile') is not None:
  plan['readout_profile']=dict(c['readout_profile'])
  for name in ('financial_value_profile.py','financial_value_train.py'):
   plan['runtime_sha256'][name]=sha_file(source/name)
 if config.get('warm_flat_profile') is not None:
  from .current100k_warm_flat import check_profile,check_source_pin
  check_profile(config['warm_flat_profile']);check_source_pin(config)
  plan['warm_flat_profile']=dict(config['warm_flat_profile'])
  plan['runtime_sha256']['current100k_warm_flat.py']=sha_file(source/'current100k_warm_flat.py')
 if config.get('cpu_standby_profile') is not None:
  from .current100k_cpu_standby import check_source_pin,MODULE
  check_source_pin(config)
  plan['cpu_standby_profile']=dict(config['cpu_standby_profile'])
  plan['runtime_sha256'][MODULE]=sha_file(source/MODULE)
 plan['plan_id']=digest(plan);validate_bound_plan(plan,c)
 return plan


def _registration_request(config,candidate_directory,dataset_path,prior_candidate_path,legacy_plans_root,
                          *,registration_scope=SAME_PRODUCER_REFIT,prior_plan_path=None,input_start_delay_ms=300000):
 """Stable request identity; wall clock and a proposed plan are not inputs."""
 if type(input_start_delay_ms)is not int or not 300000<=input_start_delay_ms<=900000:
  raise ContractError('CURRENT100K_FORWARD_PREPARATION_DELAY_BOUND')
 def canonical_path(value):
  path=Path(value)
  if not path.is_absolute() or path.resolve(strict=True)!=path:
   raise ContractError('CURRENT100K_REGISTRATION_CANONICAL_INPUT_REQUIRED')
  return path
 roots=[legacy_plans_root] if isinstance(legacy_plans_root,(str,Path)) else list(legacy_plans_root)
 roots=[canonical_path(path) for path in roots]
 if len(set(roots))!=len(roots) or any(not path.is_dir() for path in roots):
  raise ContractError('CURRENT100K_REGISTRATION_HISTORY_ROOTS')
 candidate=canonical_path(candidate_directory);prior=canonical_path(prior_candidate_path)
 paths=[candidate/'candidate.json',candidate/'value-head.safetensors',canonical_path(dataset_path),
        prior.parent/'candidate.json',prior.parent/'value-head.safetensors',canonical_path(config['period_path'])]
 if prior_plan_path is not None:paths.append(canonical_path(prior_plan_path))
 history=[canonical_path(path) for root in roots for path in sorted(root.glob('*/plan.json'))]
 return {'config_sha256':digest(config),'candidate_directory':str(candidate),
  'dataset_path':str(Path(dataset_path)),'prior_candidate_path':str(prior),
  'legacy_plans_roots':[str(path) for path in roots],
  'legacy_plans':[(str(path),sha_file(path)) for path in history],
  'input_sha256':{str(canonical_path(path)):sha_file(path) for path in paths},
  'registration_scope':registration_scope,'prior_plan_path':None if prior_plan_path is None else str(Path(prior_plan_path)),
  'input_start_delay_ms':input_start_delay_ms}


def _registration_inputs(*args,**kwargs):
 request=_registration_request(*args,**kwargs)
 # Reuse all candidate/prior-head, exact training row and scope validation for
 # read-only precheck too. Constructing this proposal allocates no trial.
 proposal=_prepare_registration(*args,**kwargs,request_sha256=digest(request))
 if _registration_request(*args,**kwargs)!=request:
  raise ContractError('CURRENT100K_REGISTRATION_INPUT_CHANGED')
 return request,proposal


def _registration_directory(path):
 path=Path(path);info=path.lstat()
 if (path.resolve(strict=True)!=path or not stat.S_ISDIR(info.st_mode)
     or info.st_uid!=os.geteuid() or info.st_mode&0o077):
  raise ContractError('CURRENT100K_REGISTRATION_PRIVATE_DIRECTORY')
 return (info.st_dev,info.st_ino,info.st_uid,info.st_gid,info.st_mode)


def _registration_file(path,descriptor=None):
 info=Path(path).lstat() if descriptor is None else os.fstat(descriptor)
 if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.geteuid()
     or info.st_mode&0o077 or info.st_size>1024**2):
  raise ContractError('CURRENT100K_REGISTRATION_PRIVATE_FILE')
 return (info.st_dev,info.st_ino,info.st_uid,info.st_gid,info.st_mode,info.st_nlink,
         info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def _registration_root(config):
 root=Path(config['forward_root'])
 if not root.is_absolute() or root.resolve()!=root:
  raise ContractError('CURRENT100K_REGISTRATION_CANONICAL_ROOT_REQUIRED')
 return root


def _registration_inspect(config,request,proposal,*,with_identity=False):
 """Observe complete published bytes or the original private preparation."""
 root=_registration_root(config)
 if not root.exists():return {'state':'EMPTY','plan':None}
 original_root=_registration_directory(root)
 plans=root/'plans';pending=root/REGISTRATION_PENDING
 if plans.is_symlink() or pending.is_symlink():
  raise ContractError('CURRENT100K_REGISTRATION_PRIVATE_DIRECTORY')
 if plans.exists():_registration_directory(plans)
 entries=list(plans.iterdir()) if plans.exists() else []
 if len(entries)>1 or (pending.exists() and entries):
  raise ContractError('CURRENT100K_REGISTRATION_MULTIPLE_OR_UNKNOWN_RESULTS')
 folder=pending if pending.exists() else entries[0] if entries else None
 if folder is None:return {'state':'EMPTY','plan':None}
 original_folder=_registration_directory(folder)
 path=folder/'plan.json';seal_path=folder/'registration.json'
 if not path.exists():raise ContractError('CURRENT100K_REGISTRATION_ORIGINAL_PLAN_UNAVAILABLE')
 before=_registration_file(path);plan=read_json(path)
 if (plan.get('registration_publish_profile')!=REGISTRATION_PUBLISH_PROFILE
     or plan.get('registration_request_sha256')!=digest(request)):
  raise ContractError('CURRENT100K_FORWARD_ALREADY_REGISTERED_NO_REPLAY')
 from .financial_forward_evaluate import validate_registration
 candidate=validate_value_candidate(Path(request['candidate_directory']))
 validate_bound_plan(plan,candidate)
 # A private plan has not yet acquired the registrar signature. Reconstruct
 # its full original intent with the stored clock, including source pins and
 # trial lineage; a self-consistent digest alone cannot authorize changed data.
 expected=dict(proposal);start=plan['registered_at_ms']+request['input_start_delay_ms']
 expected.update(registered_at_ms=plan['registered_at_ms'],input_start_ms=start,
  input_end_ms=start+plan['rule']['window_ms'],
  outcome_end_ms=start+plan['rule']['window_ms']+plan['rule']['closure_grace_ms'])
 expected['plan_id']=digest({k:v for k,v in expected.items() if k!='plan_id'})
 if plan!=expected:raise ContractError('CURRENT100K_REGISTRATION_ORIGINAL_SCOPE_CHANGED')
 if (plan.get('worker_runtime_inventory_digest')!=config['worker_runtime_inventory_digest']
     or plan.get('registry_root')!=config['forward_root']
     or plan.get('producer_registration')!=config['producer_registration']):
  raise ContractError('CURRENT100K_REGISTRATION_ORIGINAL_SCOPE_CHANGED')
 if seal_path.is_symlink():raise ContractError('CURRENT100K_REGISTRATION_PRIVATE_FILE')
 seal_present=seal_path.exists()
 if seal_present:
  seal_before=_registration_file(seal_path)
  validate_registration(config,path,plan)
  if _registration_file(seal_path)!=seal_before:
   raise ContractError('CURRENT100K_REGISTRATION_RECORD_CHANGED')
 if folder==pending:
  if set(p.name for p in folder.iterdir())-{'plan.json','registration.json'}:
   raise ContractError('CURRENT100K_REGISTRATION_UNKNOWN_PREPARATION_BYTES')
  if not plan['registered_at_ms']<=int(time.time()*1000)<plan['input_start_ms']:
   raise ContractError('CURRENT100K_REGISTRATION_PREPARATION_EXPIRED_NO_BACKFILL')
  state='PREPARED'
 else:
  if folder.name!=plan['plan_id'] or re.fullmatch('[0-9a-f]{64}',folder.name) is None or not seal_present:
   raise ContractError('CURRENT100K_REGISTRATION_PUBLISHED_BUNDLE_INCOMPLETE')
  state='PUBLISHED'
 if (_registration_file(path)!=before or _registration_directory(folder)!=original_folder
     or _registration_directory(root)!=original_root):
  raise ContractError('CURRENT100K_REGISTRATION_RECORD_CHANGED')
 result={'state':state,'plan':plan,'registration_present':seal_present}
 if with_identity:
  result['_directory_identity']=original_folder
  result['_record_identities']={'plan.json':before}
  if seal_present:result['_record_identities']['registration.json']=seal_before
 return result


def registration_state(config,candidate_directory,dataset_path,prior_candidate_path,legacy_plans_root,
                       *,registration_scope=SAME_PRODUCER_REFIT,prior_plan_path=None,input_start_delay_ms=300000):
 """Full, read-only original-input and result check; never creates a root."""
 request,proposal=_registration_inputs(config,candidate_directory,dataset_path,prior_candidate_path,legacy_plans_root,
  registration_scope=registration_scope,prior_plan_path=prior_plan_path,input_start_delay_ms=input_start_delay_ms)
 return _registration_inspect(config,request,proposal)


def existing_registration(*args,**kwargs):
 result=registration_state(*args,**kwargs)
 if result['state']=='PREPARED':raise ContractError('CURRENT100K_REGISTRATION_RESUMABLE_PREPARED')
 return result['plan']


@contextmanager
def _registration_lock(root,root_parent_fd=None):
 # Share the existing financial registrar's lock name. This protects one
 # forward_root; independent roots do not share a global trial allocator.
 # A sandboxed caller opens this parent before confinement. Retain only its
 # bound descriptor, never add the parent tree to the sandbox read allowlist.
 # Always sync, including an existing root left by an uncertain earlier mkdir.
 parent=directory=lock=None
 try:
  def identity(info):return (info.st_dev,info.st_ino,info.st_uid,info.st_gid,info.st_mode)
  named_parent=root.parent.lstat()
  if not stat.S_ISDIR(named_parent.st_mode) or root.parent.resolve(strict=True)!=root.parent:
   raise ContractError('CURRENT100K_REGISTRATION_PARENT_DIRECTORY')
  expected_parent=identity(named_parent)
  if root_parent_fd is None:
   parent=os.open(root.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
  else:
   if type(root_parent_fd)is not int or root_parent_fd<0:
    raise ContractError('CURRENT100K_REGISTRATION_PARENT_FD_INVALID')
   try:
    if os.get_inheritable(root_parent_fd):raise ContractError('CURRENT100K_REGISTRATION_PARENT_FD_MUST_BE_CLOEXEC')
    parent=os.dup(root_parent_fd)
   except OSError as exc:raise ContractError('CURRENT100K_REGISTRATION_PARENT_FD_INVALID') from exc
  os.set_inheritable(parent,False)
  flags=fcntl.fcntl(parent,fcntl.F_GETFL)
  if flags&os.O_ACCMODE!=os.O_RDONLY or flags&getattr(os,'O_PATH',0):
   raise ContractError('CURRENT100K_REGISTRATION_PARENT_FD_INVALID')
  def check_parent():
   if (identity(os.fstat(parent))!=expected_parent or identity(root.parent.lstat())!=expected_parent
       or root.parent.resolve(strict=True)!=root.parent):
    raise ContractError('CURRENT100K_REGISTRATION_PARENT_CHANGED')
  check_parent()
  if not root.exists():
   try:os.mkdir(root.name,0o700,dir_fd=parent)
   except FileExistsError:pass
  expected=_registration_directory(root)
  os.fsync(parent);check_parent()
  if _registration_directory(root)!=expected:
   raise ContractError('CURRENT100K_REGISTRATION_ROOT_CHANGED')
  directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
  opened=os.fstat(directory)
  if identity(opened)!=expected:
   raise ContractError('CURRENT100K_REGISTRATION_ROOT_CHANGED')
  lock=os.open('registrar.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=directory)
  info=os.fstat(lock)
  if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1 or info.st_mode&0o077:
   raise ContractError('CURRENT100K_REGISTRATION_LOCK_SCOPE')
  binding=(info.st_dev,info.st_ino,info.st_uid,info.st_gid,info.st_mode)
  def check():
   check_parent()
   actual=os.stat('registrar.lock',dir_fd=directory,follow_symlinks=False)
   if ((actual.st_dev,actual.st_ino,actual.st_uid,actual.st_gid,actual.st_mode)!=binding
       or actual.st_nlink!=1 or _registration_directory(root)!=expected):
    raise ContractError('CURRENT100K_REGISTRATION_LOCK_CHANGED')
  check();fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);check()
  yield directory,check
 finally:
  if lock is not None:os.close(lock)
  if directory is not None:os.close(directory)
  if parent is not None:os.close(parent)


def _publish_registration_directory(root_fd,plans_fd,plan_id):
 # A normal directory rename could replace an unknown empty destination.
 # Keep atomic NOREPLACE even for this case; no unsafe portability fallback.
 libc=ctypes.CDLL(None,use_errno=True)
 try:rename=libc.renameat2
 except AttributeError as exc:raise ContractError('CURRENT100K_ATOMIC_DIRECTORY_RENAME_UNAVAILABLE') from exc
 rename.argtypes=(ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint)
 rename.restype=ctypes.c_int
 if rename(root_fd,REGISTRATION_PENDING.encode(),plans_fd,plan_id.encode(),1):
  number=ctypes.get_errno();raise OSError(number,os.strerror(number))


def _check_registration_bundle(folder,observed):
 if (_registration_directory(folder)!=observed['_directory_identity']
     or any(_registration_file(folder/name)!=before for name,before in observed['_record_identities'].items())):
  raise ContractError('CURRENT100K_REGISTRATION_BUNDLE_CHANGED')


def _sync_registration_bundle(folder,observed):
 """Sync the exact validated records, including a visible unknown result."""
 _check_registration_bundle(folder,observed)
 if set(observed['_record_identities'])!={'plan.json','registration.json'}:
  raise ContractError('CURRENT100K_REGISTRATION_PUBLISHED_BUNDLE_INCOMPLETE')
 for name,before in observed['_record_identities'].items():
  descriptor=os.open(folder/name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
  try:
   if _registration_file(folder/name,descriptor)!=before:
    raise ContractError('CURRENT100K_REGISTRATION_RECORD_CHANGED')
   os.fsync(descriptor)
  finally:os.close(descriptor)
 directory=os.open(folder,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  info=os.fstat(directory)
  if (info.st_dev,info.st_ino,info.st_uid,info.st_gid,info.st_mode)!=observed['_directory_identity']:
   raise ContractError('CURRENT100K_REGISTRATION_RECORD_CHANGED')
  os.fsync(directory)
 finally:os.close(directory)
 _check_registration_bundle(folder,observed)


def register(config,candidate_directory,dataset_path,prior_candidate_path,legacy_plans_root,
             *,registration_scope=SAME_PRODUCER_REFIT,prior_plan_path=None,input_start_delay_ms=300000,
             admission_check=None,root_parent_fd=None):
 """Publish one signed bundle, or finish the same private original proposal.

 Unknown published replies reconcile from original signed bytes. A complete
 private plan can retry its local signature before its original input start;
 partial/conflicting bytes and expired proposals are retained, never replaced.
 Optional admission_check raises when a launcher's original fresh producer or
 deadline has expired. It is rechecked around preparation and publication, not
 persisted as new authority and never requested for a published reconciliation.
 A confined caller may pass an already-open read-only root_parent_fd; it is
 duplicated, verified and synced, not treated as a boolean durability claim.
 """
 if admission_check is not None and not callable(admission_check):
  raise ContractError('CURRENT100K_REGISTRATION_ADMISSION_CALLBACK_REQUIRED')
 def admit():
  if admission_check is not None:admission_check()
 args=(config,candidate_directory,dataset_path,prior_candidate_path,legacy_plans_root)
 options=dict(registration_scope=registration_scope,prior_plan_path=prior_plan_path,input_start_delay_ms=input_start_delay_ms)
 request,proposal=_registration_inputs(*args,**options)
 root=_registration_root(config)
 # A rejected first admission allocates no root/lock. Complete published
 # evidence is recovery of the existing result, not a fresh trial admission.
 if admission_check is not None and _registration_inspect(config,request,proposal)['state']!='PUBLISHED':admit()
 with _registration_lock(root,root_parent_fd) as (root_fd,check):
  observed=_registration_inspect(config,request,proposal,with_identity=True)
  plan=observed['plan'] if observed['state']!='EMPTY' else proposal
  if observed['state']!='PUBLISHED':
   admit()
   if not plan['registered_at_ms']<=int(time.time()*1000)<plan['input_start_ms']:
    raise ContractError('CURRENT100K_REGISTRATION_PREPARATION_EXPIRED_NO_BACKFILL')
  plans=root/'plans';pending=root/REGISTRATION_PENDING
  if not plans.exists():os.mkdir('plans',0o700,dir_fd=root_fd);os.fsync(root_fd)
  expected_plans=_registration_directory(plans)
  plans_fd=os.open('plans',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=root_fd)
  try:
   opened=os.fstat(plans_fd)
   if (opened.st_dev,opened.st_ino,opened.st_uid,opened.st_gid,opened.st_mode)!=expected_plans:
    raise ContractError('CURRENT100K_REGISTRATION_ROOT_CHANGED')
   if _registration_request(*args,**options)!=request:
    raise ContractError('CURRENT100K_REGISTRATION_INPUT_CHANGED')
   check()
   if observed['state']=='PUBLISHED':
    # A prior visible rename can lack a durable directory acknowledgement.
    # Retry only fsync of the original valid bundle; never sign/rename again.
    published=plans/plan['plan_id']
    _sync_registration_bundle(published,observed)
    os.fsync(plans_fd);os.fsync(root_fd);check()
    if _registration_directory(plans)!=expected_plans:
     raise ContractError('CURRENT100K_REGISTRATION_ROOT_CHANGED')
    _check_registration_bundle(published,observed)
    return plan
   admit()
   if observed['state']=='EMPTY':
    os.mkdir(REGISTRATION_PENDING,0o700,dir_fd=root_fd);os.fsync(root_fd)
    write_once(pending/'plan.json',plan)
   pending_identity=_registration_directory(pending)
   check()
   if not (pending/'registration.json').exists():
    from .financial_forward_witness import sign_record
    admit()
    seal=sign_record(config,'registrar',{'plan_id':plan['plan_id'],
     'plan_file_sha256':sha_file(pending/'plan.json'),'at_ms':int(time.time()*1000)})
    admit()
    write_once(pending/'registration.json',seal)
   # Includes the ordinary signed registration validator, candidate/training
   # binding and original time window. No private partial record is repaired.
   ready=_registration_inspect(config,request,proposal,with_identity=True)
   if ready['state']!='PREPARED' or ready['plan']!=plan or not ready['registration_present']:
    raise ContractError('CURRENT100K_REGISTRATION_PREPARATION_CHANGED')
   _sync_registration_bundle(pending,ready)
   if (_registration_request(*args,**options)!=request or _registration_directory(pending)!=pending_identity
       or _registration_directory(plans)!=expected_plans):
    raise ContractError('CURRENT100K_REGISTRATION_INPUT_CHANGED')
   admit();check();_check_registration_bundle(pending,ready)
   if not plan['registered_at_ms']<=int(time.time()*1000)<plan['input_start_ms']:
    raise ContractError('CURRENT100K_REGISTRATION_PREPARATION_EXPIRED_NO_BACKFILL')
   _publish_registration_directory(root_fd,plans_fd,plan['plan_id'])
   # Rename may already be visible when either sync fails. Preserve that exact
   # result; a later call validates and syncs it without a new now/plan/trial.
   os.fsync(plans_fd);os.fsync(root_fd);check()
   published=plans/plan['plan_id']
   _check_registration_bundle(published,ready)
   return plan
  finally:os.close(plans_fd)
