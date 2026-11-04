#!/usr/bin/env python3
"""Bounded resident Current100k research observer; no order or training APIs."""
from __future__ import annotations
import argparse,gc,hashlib,importlib,json,os,signal,sys,time,types,uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'research'),str(ROOT/'research/neuron_learning')]
from laya_cell.current100k_preopen import (
 ResidentIngress,RequestLedger,load,publish_once,require,run_resident,wall_ms)
from hepta_decision_attribution import read_private


def pinned(path,wanted,limit=16*1024*1024):
 require(Path(path).is_absolute(),'PREOPEN_ABSOLUTE_PATH')
 raw=read_private(path,limit)
 require(hashlib.sha256(raw).hexdigest()==wanted,'PREOPEN_PIN_CHANGED:'+str(path))
 return raw


def runtime_pins(c):
 root=Path(c['runtime_root']);inventory=c['runtime_files_sha256']
 require(root.resolve()==ROOT and isinstance(inventory,dict) and bool(inventory),'PREOPEN_RUNTIME_ROOT')
 required={
  'scripts/run_current100k_preopen_research.py','scripts/hepta_decision_attribution.py',
  'research/neuron_learning/laya_cell/current100k_preopen.py',
  'research/neuron_learning/laya_cell/financial_forward_source.py',
  'research/neuron_learning/laya_cell/current100k_weight_shadow.py',
  'research/weight_edit/current100k_study.py','research/weight_edit/batch_feature.py'}
 require(required<=set(inventory),'PREOPEN_RUNTIME_INVENTORY')
 for name,want in inventory.items():
  p=Path(name);require(not p.is_absolute() and '..' not in p.parts,'PREOPEN_RUNTIME_PATH')
  require(hashlib.sha256((root/p).read_bytes()).hexdigest()==want,'PREOPEN_RUNTIME_CHANGED:'+name)


def validate_config(c):
 require(c.get('schema')=='heptatrader.current100k-preopen-research-config.v1','PREOPEN_CONFIG_SCHEMA')
 require(all(c.get(k)is False for k in ('trade_authority','automatic_selection','actor_update')),'PREOPEN_NO_EFFECT_AUTHORITY')
 require(type(c['encoder_threads'])is int and c['encoder_threads'] in (1,2,4),'PREOPEN_CPU_THREADS')
 require(type(c['encoder_batch_size'])is int and c['encoder_batch_size'] in (1,2),'PREOPEN_CPU_BATCH')
 require(1<=c.get('max_resident_seconds',1800)<=1800,'PREOPEN_RESIDENT_BOUND')
 require(60<=c.get('cooldown_seconds',60)<=900,'PREOPEN_COOLDOWN_BOUND')
 cpus=c['cpu_affinity']
 require(isinstance(cpus,list) and len(cpus)>=c['encoder_threads'] and len(cpus)==len(set(cpus))
         and all(type(x)is int and 0<=x<4096 for x in cpus),'PREOPEN_CPU_AFFINITY')
 require(set(cpus)<=os.sched_getaffinity(0),'PREOPEN_AFFINITY_OUTSIDE_SERVICE_SCOPE')
 runtime_pins(c)
 pinned(c['period_path'],c['period_sha256'])
 pinned(c['study_plan_path'],c['study_plan_sha256'])
 pinned(c['guard_config'],c['guard_config_sha256'])
 pinned(c['source_registration']['manifest'],c['source_registration']['sha256'],65536)
 pinned(c['warmup_request_path'],c['warmup_request_sha256'])
 require(Path(c['state_root']).is_absolute(),'PREOPEN_STATE_ROOT')
 require(Path(c['encoder_runtime']).is_absolute() and Path(c['encoder_base']).is_absolute(),'PREOPEN_ENCODER_PATHS')
 inventory=c['encoder_runtime_files_sha256']
 require(isinstance(inventory,dict) and {'learn/laya_cell/model.py','research/paired_net_100k_preflight/core.py'}<=set(inventory),'PREOPEN_ENCODER_RUNTIME_INVENTORY')
 for name,wanted in inventory.items():
  p=Path(name);require(not p.is_absolute() and '..' not in p.parts,'PREOPEN_ENCODER_RUNTIME_PATH')
  require(hashlib.sha256((Path(c['encoder_runtime'])/p).read_bytes()).hexdigest()==wanted,'PREOPEN_ENCODER_RUNTIME_CHANGED:'+name)


def original_guard(c):
 """Import the pinned deployed guard under its own package name."""
 config=load(c['guard_config'])
 directory=Path(c['guard_config']).parent/'research'/'historical_training'
 require(directory.is_dir() and not directory.is_symlink(),'PREOPEN_ORIGINAL_GUARD_DIRECTORY')
 for name,wanted in config['guard_files_sha256'].items():
  require(Path(name).name==name,'PREOPEN_GUARD_PATH')
  require(hashlib.sha256((directory/name).read_bytes()).hexdigest()==wanted,'PREOPEN_GUARD_SOURCE_CHANGED')
 package=types.ModuleType('_preopen_original_guard');package.__path__=[str(directory)]
 sys.modules[package.__name__]=package
 module=importlib.import_module(package.__name__+'.resident_resource_guard')
 previous=list(sys.path)
 try:module.verify_config(config)
 finally:sys.path[:]=previous
 require(config['period_sha256']==c['period_sha256'],'PREOPEN_GUARD_PERIOD')
 underlying=module.Gate(config,'value-predictor')
 class Guard:
  def __init__(self):self.acquired_at_ms=None
  def invoke(self,name,*args):
   previous=list(sys.path)
   try:return getattr(underlying,name)(*args)
   finally:sys.path[:]=previous
  def acquire(self,subject):
   admitted=self.invoke('acquire',subject)
   if admitted:self.acquired_at_ms=wall_ms()
   return admitted
  def check(self):
   pinned(c['period_path'],c['period_sha256'])
   pinned(c['guard_config'],c['guard_config_sha256'])
   return self.invoke('check')
  def release(self):return self.invoke('release')
  def should_yield(self):
   # Negative-only handoff. An old denial cannot repeatedly evict new workers.
   if os.path.lexists(config['manual_hold_path']):return True
   if self.acquired_at_ms is None:return False
   now=wall_ms()
   for role in ('trainer','evaluator','owner-advisory'):
    p=Path(config['state_root'])/(role+'-admission.json')
    if not p.exists():continue
    try:
     report=load(p);at=report.get('at_ms')
     if (report.get('role')==role and type(at)is int and self.acquired_at_ms<=at<=now
         and now-at<=15000 and report.get('admit_new_worker')is False
         and 'OTHER_RESEARCH_WORKER_ACTIVE' in report.get('reasons',[])):return True
    except (OSError,ValueError,TypeError):continue
   return False
 return Guard()


def run(c):
 validate_config(c)
 # All runtime libraries see the same CPU limits before their first import.
 threads=c['encoder_threads']
 os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',USE_TF='0',
  TOKENIZERS_PARALLELISM='false',HIP_VISIBLE_DEVICES='',ROCR_VISIBLE_DEVICES='',CUDA_VISIBLE_DEVICES='',
  OPENBLAS_NUM_THREADS=str(threads),OMP_NUM_THREADS=str(threads),MKL_NUM_THREADS=str(threads),NUMEXPR_NUM_THREADS=str(threads))
 os.sched_setaffinity(0,c['cpu_affinity'])
 from laya_cell.current100k_weight_shadow import HeadEnsemble,ShadowStore,FEATURE_SCHEMA
 store=ShadowStore(Path(c['study_plan_path']).parent);plan=store.plan
 require(plan['producer_registration']==c['source_registration'] and plan['period_sha256']==c['period_sha256'],'PREOPEN_STUDY_SOURCE_SCOPE')
 require(plan['encoder_backend']==c['encoder_backend'],'PREOPEN_STUDY_BACKEND')
 for field,config_field in (('encoder_threads','encoder_threads'),('encoder_batch_size','encoder_batch_size')):
  require(plan[field]==c[config_field],'PREOPEN_STUDY_NUMERIC_CONFIGURATION')
 protocol={k:plan[k] for k in ('plan_id','input_start_ms','input_end_ms','period_sha256','producer_registration')}
 protocol.update(max_request_age_ms=c.get('max_request_age_ms',60000),max_requests=c.get('max_requests',10000),
                 encoder_backend=c['encoder_backend'],encoder_threads=threads,encoder_batch_size=c['encoder_batch_size'],
                 cpu_affinity=c['cpu_affinity'],study_plan_sha256=c['study_plan_sha256'])
 ledger=RequestLedger(c['state_root'],protocol)
 ingress=ResidentIngress(c['source_registration'],c['period_sha256'])
 class Source:
  def __init__(self):self.remembered={}
  def read(self):
   value=ingress.read()
   if not value[3]:
    fresh=[]
    for binding in value[2]:
     cid=binding["command"]["command_id"]
     if cid in self.remembered:require(self.remembered[cid]==binding,"PREOPEN_REMEMBERED_COMMAND_CHANGED")
     else:fresh.append(binding);self.remembered[cid]=binding
    if fresh:store.remember_commands(fresh)
   return value
  def statistics(self):return ingress.statistics()
 models={}
 def warm():
  from weight_edit.current100k_study import WarmCpuEncoder
  previous=list(sys.path)
  started=time.monotonic()
  try:
   encoder=WarmCpuEncoder(c['encoder_runtime'],c['encoder_base'],threads=threads,batch_size=c['encoder_batch_size'])
   models['encoder']=encoder
  finally:sys.path[:]=previous
  require(encoder.base_id==plan['base_id'],'PREOPEN_ENCODER_BASE_ID')
  models['heads']=HeadEnsemble(plan)
  warmup=load(c['warmup_request_path'])
  if 'request' in warmup:warmup=warmup['request']
  require(warmup['started_at_ms']<plan['registered_at_ms'],'PREOPEN_WARMUP_MUST_BE_DEVELOPMENT')
  features=encoder.encode_request(warmup);require(features['backend']==c['encoder_backend'],'PREOPEN_ENCODER_BACKEND')
  models['heads'].predict(features['features'])
  publish_once(ledger.root/('warmup-'+uuid.uuid4().hex+'.json'),{
   'plan_id':plan['plan_id'],'completed_at_ms':wall_ms(),'load_and_warmup_seconds':time.monotonic()-started,
   'backend':features['backend'],'encoder_threads':threads,'encoder_batch_size':c['encoder_batch_size'],
   'cpu_affinity':sorted(os.sched_getaffinity(0)),'warmup_request_sha256':c['warmup_request_sha256'],
   'warmup_receipts':features.get('receipts'),'source':'DEVELOPMENT_ONLY_NOT_FUTURE_PREDICTION','broker_orders_sent':0})
 def predict_and_seal(item):
  r=item['request'];features=models['encoder'].encode_request(r)
  require(features['backend']==c['encoder_backend'],'PREOPEN_ENCODER_BACKEND')
  feature_document={**features,'schema':FEATURE_SCHEMA,'request_id':r['request_id'],
   'request_sha256':item['request_sha256'],'producer_manifest_sha256':c['source_registration']['sha256'],
   'period_sha256':plan['period_sha256'],'base_id':plan['base_id'],'input_started_at_ms':r['started_at_ms'],
   'encoder_threads':threads,'encoder_batch_size':c['encoder_batch_size']}
  predictions=models['heads'].predict(features['features'])
  return store.seal(item,feature_document,predictions)
 def close_model():models.clear();gc.collect()
 stopped=[False]
 def request_stop(signum,frame):stopped[0]=True
 previous_handlers={sig:signal.signal(sig,request_stop) for sig in (signal.SIGTERM,signal.SIGINT)}
 try:
  gate=original_guard(c)
  return run_resident(Source(),ledger,warm,predict_and_seal,close_model,gate,
   max_seconds=c.get('max_resident_seconds',1800),poll_seconds=c.get('poll_ms',250)/1000,
   guard_check_seconds=c.get('guard_check_seconds',5),cooldown_seconds=c.get('cooldown_seconds',60),
   yield_requested=gate.should_yield,stop=lambda:stopped[0])
 finally:
  for sig,handler in previous_handlers.items():signal.signal(sig,handler)
  ledger.close()


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--config',required=True);parser.add_argument('--sha256',required=True)
 args=parser.parse_args();os.umask(0o077)
 pinned(args.config,args.sha256);result=run(load(args.config))
 print(json.dumps(result,sort_keys=True,allow_nan=False),flush=True)


if __name__=='__main__':main()
