"""Explicit immutable continuation of one existing weight-study window.

The operator pins a record; runtime never authorizes its own replacement.
"""
from __future__ import annotations
import ast,copy,hashlib,json,time
from pathlib import Path
from historical_training.common import canonical
from laya_cell import current100k_weight_shadow as W
from run_verified_weight_research import verify_release

SCHEMA='heptatrader.weight-shadow-runtime-continuation.v1'
ALLOWED_RUNTIME_FILES=frozenset({
 'research/neuron_learning/laya_cell/current100k_preopen.py',
 'research/neuron_learning/laya_cell/current100k_weight_shadow_eval.py',
 'scripts/supervise_current100k_weight_shadow.py',
 'scripts/current100k_weight_continuation.py',
 'scripts/run_current100k_preopen_research.py',
 'tests/python/test_current100k_weight_shadow.py',
 'tests/python/test_weight_shadow_supervisor.py',
 'tests/python/test_current100k_preopen_capture_retry.py',
 'tests/python/test_current100k_weight_continuation.py',
 'research/neuron_learning/laya_cell/current100k_preopen_evidence.py',
 'research/neuron_learning/laya_cell/current100k_weight_shadow.py',
 'research/neuron_learning/laya_cell/resident_rewards.py',
})
EVALUATOR='research/neuron_learning/laya_cell/current100k_weight_shadow_eval.py'
RUNNER='scripts/run_current100k_preopen_research.py'
PROTECTED_STATISTICS=('clusters','metrics','percentile','paired_interval','mixture_evidence','assessment')
EVALUATOR_RUNTIME_METHODS=frozenset({'__init__','identity','snapshot','evaluate_snapshot'})
SHADOW='research/neuron_learning/laya_cell/current100k_weight_shadow.py'
REWARD_CUTOFF='research/neuron_learning/laya_cell/resident_rewards.py'
# This one optional source-time patch is reviewed by exact complete-file pins.
REWARD_CUTOFF_SHA256=(
 '711dbc8ab1b89edd3c7d141da769e876b6bcec591692a272c7e8f428143e8365',
 '55715aaff6ef42d109bdb2cca97380275cf2d5acdf5b1a64ae472a13608a4ea0')

def pinned(path,wanted):
 path=Path(path);W.require(path.is_absolute() and not path.is_symlink(),'CONTINUATION_ABSOLUTE_FILE')
 limit=64*1024*1024
 W.require(path.is_file() and path.stat().st_size<=limit,'INVALID_OR_OVERSIZED_FILE:'+str(path))
 with path.open('rb') as stream:raw=stream.read(limit+1)
 W.require(len(raw)<=limit,'INVALID_OR_OVERSIZED_FILE:'+str(path))
 W.require(hashlib.sha256(raw).hexdigest()==wanted,'CONTINUATION_PIN_CHANGED:'+str(path))
 def pairs(items):
  result={}
  for key,value in items:
   W.require(key not in result,'DUPLICATE_JSON_KEY');result[key]=value
  return result
 def nonfinite(_):raise W.EvidenceError('NONFINITE_JSON')
 return json.loads(raw,object_pairs_hook=pairs,parse_constant=nonfinite)

def plain_config(config):
 return {k:copy.deepcopy(v) for k,v in config.items() if k not in ('continuation','_sha256')}

def protect_shadow_math(old_root,new_root):
 before=ast.parse((old_root/SHADOW).read_text());after=ast.parse((new_root/SHADOW).read_text())
 classes=[n for n in before.body if isinstance(n,ast.ClassDef) and n.name=='ShadowStore']
 W.require(len(classes)==1,'CONTINUATION_SHADOW_INTERFACE')
 methods=[n for n in classes[0].body if isinstance(n,ast.FunctionDef) and n.name=='remember_commands']
 W.require(len(methods)==1,'CONTINUATION_SHADOW_INTERFACE')
 method=methods[0]
 W.require([a.arg for a in method.args.args]==['self','bindings'] and not method.args.kwonlyargs
           and method.args.vararg is None and method.args.kwarg is None,'CONTINUATION_SHADOW_INTERFACE')
 first=method.body[0];expected=ast.parse("self.root/'seen-commands'",mode='eval').body
 W.require(isinstance(first,ast.Assign) and len(first.targets)==1 and isinstance(first.targets[0],ast.Name)
           and first.targets[0].id=='folder' and ast.dump(first.value,include_attributes=False)==ast.dump(expected,include_attributes=False),'CONTINUATION_SHADOW_INTERFACE')
 method.args.kwonlyargs=[ast.arg(arg='state_root')];method.args.kw_defaults=[ast.Constant(value=None)]
 first.value=ast.parse("(self.root if state_root is None else Path(state_root))/'seen-commands'",mode='eval').body
 W.require(ast.dump(before,include_attributes=False)==ast.dump(after,include_attributes=False),'CONTINUATION_SHADOW_MATH_CHANGED')


def check_runner_capture_retry(old_root,new_root):
 """Permit only the reviewed deadline-forwarding adapter inside run.Source."""
 old_tree=ast.parse((old_root/RUNNER).read_text())
 new_tree=ast.parse((new_root/RUNNER).read_text())
 def one(nodes,kind,name):
  values=[n for n in nodes if isinstance(n,kind) and n.name==name]
  W.require(len(values)==1,'CONTINUATION_RUNNER_SCOPE')
  return values[0]
 run=one(old_tree.body,ast.FunctionDef,'run')
 source=one(run.body,ast.ClassDef,'Source')
 method=one(source.body,ast.FunctionDef,'read')
 expected_args=ast.parse('def read(self): pass').body[0].args
 W.require(ast.dump(method.args,include_attributes=False)==ast.dump(expected_args,include_attributes=False),'CONTINUATION_RUNNER_ORIGINAL_READ')
 expected_call=ast.parse('value=ingress.read()').body[0]
 W.require(method.body and ast.dump(method.body[0],include_attributes=False)==ast.dump(expected_call,include_attributes=False),'CONTINUATION_RUNNER_ORIGINAL_CAPTURE')
 W.require(not any(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name=='read_with_deadline' for n in source.body),'CONTINUATION_RUNNER_EXISTING_DEADLINE_ADAPTER')
 method.args.kwonlyargs=[ast.arg(arg='retry_deadline_ms')]
 method.args.kw_defaults=[ast.Constant(value=None)]
 method.body[0].value.keywords=[ast.keyword(arg='retry_deadline_ms',value=ast.Name(id='retry_deadline_ms',ctx=ast.Load()))]
 adapter=ast.parse('def read_with_deadline(self,deadline_ms): return self.read(retry_deadline_ms=deadline_ms)').body[0]
 source.body.insert(source.body.index(method)+1,adapter)
 W.require(ast.dump(old_tree,include_attributes=False)==ast.dump(new_tree,include_attributes=False),'CONTINUATION_RUNNER_OUTSIDE_CAPTURE_RETRY_CHANGED')


def release_changes(old_root,old,new_root,new):
 W.require(old.get('external_files')==new.get('external_files') and bool(old.get('external_files')),'CONTINUATION_EXTERNAL_PINS_CHANGED')
 before,after=old['files'],new['files'];W.require(set(before)<=set(after),'CONTINUATION_RUNTIME_REMOVAL')
 changed={k:{'old_sha256':before.get(k),'new_sha256':after[k]} for k in after if before.get(k)!=after[k]}
 W.require(bool(changed) and set(changed)<=ALLOWED_RUNTIME_FILES,'CONTINUATION_UNREVIEWED_RUNTIME_CHANGE')
 if RUNNER in changed:check_runner_capture_retry(old_root,new_root)
 if SHADOW in changed:protect_shadow_math(old_root,new_root)
 if REWARD_CUTOFF in changed:
  W.require((before.get(REWARD_CUTOFF),after[REWARD_CUTOFF])==REWARD_CUTOFF_SHA256,'CONTINUATION_REWARD_CUTOFF_PATCH_CHANGED')
  W.require((W.sha(old_root/REWARD_CUTOFF),W.sha(new_root/REWARD_CUTOFF))==REWARD_CUTOFF_SHA256,'CONTINUATION_REWARD_CUTOFF_SOURCE_CHANGED')
 if EVALUATOR in changed:
  def protected(root):
   tree=ast.parse((root/EVALUATOR).read_text())
   nodes={n.name:n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))}
   names=(*PROTECTED_STATISTICS,'Evaluator')
   W.require(set(names)<=set(nodes),'CONTINUATION_STATISTICS_MISSING')
   evaluator=nodes['Evaluator']
   snapshots=[n for n in evaluator.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in EVALUATOR_RUNTIME_METHODS]
   W.require({n.name for n in snapshots}==EVALUATOR_RUNTIME_METHODS,'CONTINUATION_RUNTIME_METHODS_REQUIRED')
   evaluator.body=[n for n in evaluator.body if n not in snapshots]
   return {k:ast.dump(nodes[k],include_attributes=False) for k in names}
  W.require(protected(old_root)==protected(new_root),'CONTINUATION_STATISTICS_CHANGED')
 return changed


def _prepare(new_config,root,old_config_path,old_config_sha256,recorded_at_ms):
 root=Path(root);W.require(root.is_absolute() and not root.is_symlink(),'CONTINUATION_ROOT')
 old=pinned(old_config_path,old_config_sha256)
 W.require('continuation' not in old,'CONTINUATION_REQUIRES_ORIGINAL_CONFIG')
 body=plain_config(new_config)
 expected={**old,'release_manifest':body.get('release_manifest'),'release_manifest_sha256':body.get('release_manifest_sha256')}
 W.require(body==expected and body['state_root']==str(root),'CONTINUATION_SUPERVISOR_SCOPE_CHANGED')
 W.require(Path(old_config_path)==root/'supervisor-config.json','CONTINUATION_ORIGINAL_CONFIG_PATH')
 old_root,old_release=verify_release(old['release_manifest'],old['release_manifest_sha256'])
 new_root,new_release=verify_release(body['release_manifest'],body['release_manifest_sha256'])
 changes=release_changes(old_root,old_release,new_root,new_release)
 plan=W.ShadowStore(root).plan;activation=W.read(root/'activation.json')
 W.require(activation['plan_id']==plan['plan_id'] and activation['supervisor_config_sha256']==old_config_sha256,'CONTINUATION_ORIGINAL_ACTIVATION')
 W.require(activation['study_config_sha256']==old['study_config_sha256'],'CONTINUATION_STUDY_BINDING')
 pinned(old['study_config_path'],old['study_config_sha256'])
 template=pinned(old['predictor_template_path'],old['predictor_template_sha256'])
 old_predictor=pinned(root/'predictor-config.json',activation['predictor_config_sha256'])
 expected_predictor=dict(template,study_plan_path=str(root/'plan.json'),study_plan_sha256=W.sha(root/'plan.json'))
 W.require(old_predictor==expected_predictor,'CONTINUATION_ORIGINAL_PREDICTOR_CHANGED')
 W.require(old_predictor['runtime_root']==str(old_root) and old_predictor['runtime_files_sha256']==old_release['files'],'CONTINUATION_ORIGINAL_RUNTIME_BINDING')
 W.require(type(recorded_at_ms)is int and activation['activated_at_ms']<=recorded_at_ms<=time.time_ns()//1000000,'CONTINUATION_RECORD_CLOCK')
 predictor=dict(old_predictor,runtime_root=str(new_root),runtime_files_sha256=copy.deepcopy(new_release['files']))
 path=root/'continuations'/body['release_manifest_sha256']/'predictor-config.json'
 record={'schema':SCHEMA,'recorded_at_ms':recorded_at_ms,'purpose':'CAPTURE_RETRY_AND_ORIGINAL_EVIDENCE_FINALIZATION_ONLY',
  'state_root':str(root),'plan_id':plan['plan_id'],
  'original_supervisor_config':{'path':str(old_config_path),'sha256':old_config_sha256},
  'original_plan_sha256':W.sha(root/'plan.json'),'original_registration_sha256':W.sha(root/'registration.json'),
  'original_activation_sha256':W.sha(root/'activation.json'),'original_predictor_config_sha256':activation['predictor_config_sha256'],
  'new_release_manifest':{'path':body['release_manifest'],'sha256':body['release_manifest_sha256']},
  'new_config_without_continuation_sha256':W.digest(body),'changed_runtime_files':changes,
  'external_files_sha256':W.digest(old_release['external_files']),
  'predictor_config':{'path':str(path),'sha256':hashlib.sha256(canonical(predictor)+b'\n').hexdigest()},
  'window_reset':False,'statistical_rules_changed':False,'candidate_or_encoder_changed':False,'trade_authority':False}
 return record,plan,path,predictor


def prepare_record(new_config,old_config_path,old_config_sha256,*,recorded_at_ms=None):
 """Operator-only preparation; returns a reviewable record and writes nothing."""
 stamp=time.time_ns()//1000000 if recorded_at_ms is None else recorded_at_ms
 return _prepare(new_config,new_config['state_root'],old_config_path,old_config_sha256,stamp)[0]


def continue_activation(config,root):
 """Verify pinned authorization, preserve old records, derive runtime pins only.

 Call at the start of ensure_activation, before its original activation check:
     if config.get('continuation') is not None:
         return continue_activation(config, root)
 """
 reference=config.get('continuation')
 W.require(isinstance(reference,dict) and set(reference)=={'path','sha256'},'CONTINUATION_RECORD_REQUIRED')
 record=pinned(reference['path'],reference['sha256'])
 W.require(record.get('schema')==SCHEMA,'CONTINUATION_RECORD_SCHEMA')
 source=record['original_supervisor_config']
 expected,plan,path,predictor=_prepare(config,root,source['path'],source['sha256'],record['recorded_at_ms'])
 W.require(record==expected,'CONTINUATION_RECORD_SCOPE_CHANGED')
 W.durable_once(path,predictor)
 W.require(W.sha(path)==record['predictor_config']['sha256'],'CONTINUATION_DERIVED_PREDICTOR_CHANGED')
 return plan,path
