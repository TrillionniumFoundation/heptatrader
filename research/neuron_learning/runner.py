#!/usr/bin/env python3
"""Pocket4-only candidate learner. Remote PAPER never consumes these artifacts.

Training, checkpointing and evaluation are independent of execution readiness.
Selected remote parameters, strategy gates, driver state and sockets are untouched.
"""
from __future__ import annotations
import argparse
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import numpy as np
from data import canonical,digest,freeze,strict,HORIZONS
from gpu import GpuHead,initial_weights,project_candidate
from economics import replay_economics

MODEL_CLASS='hepta-neuron-native16-hip-tanh16-return2-v1'
OBJECTIVE={'name':'independent-future-mid-huber-v1','return_scale_bps':100,
 'discount_time_constant_ms':3600000,'teacher_weight':0.1,'pnl_utility_is_separate':True,
 'epochs':16,'lr':0.01,'max_training_rows':256,'min_mature_rows':16,
 'per_layer_relative_norm_cap':0.005,'global_relative_norm_cap':0.0025,
 'remote_selected_model_changed':False}


def write_once(path,value):
    path=Path(path);path.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    raw=(canonical(value)+'\n').encode()
    if path.exists():
        if path.read_bytes()!=raw:raise ValueError('IMMUTABLE_ARTIFACT_CONFLICT')
        return
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    parent=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(parent)
    finally:os.close(parent)


def atomic(path,value):
    path=Path(path);temp=path.with_name(path.name+'.tmp')
    raw=(canonical(value)+'\n').encode()
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    os.replace(temp,path)


def connect(path):
    db=sqlite3.connect(path,timeout=2)
    db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
    db.executescript('''CREATE TABLE IF NOT EXISTS models(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS predictions(id TEXT PRIMARY KEY,sample_id TEXT NOT NULL,created_ms INTEGER NOT NULL,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS trains(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS predictions_sample_id ON predictions(sample_id);
    CREATE INDEX IF NOT EXISTS predictions_created_ms ON predictions(created_ms);''')
    return db


def config_load(path):
    config=strict(Path(path).read_bytes())
    if config.get('schema')!='heptatrader.neuron-learning-config.v1' or config.get('gpu_arch')!='gfx1150':
        raise ValueError('LEARNER_CONFIG_SCOPE')
    if config.get('execution_authority') is not False or config.get('automatic_cutover') is not False:
        raise ValueError('LEARNER_MUST_HAVE_NO_EXECUTION_AUTHORITY')
    if config.get('objective')!=OBJECTIVE:raise ValueError('FROZEN_OBJECTIVE_CHANGED')
    for key in ('native_binary','gpu_library','accounting_module'):
        p=Path(config[key]);sha=hashlib.sha256(p.read_bytes()).hexdigest()
        if sha!=config[key+'_sha256']:raise ValueError('BINARY_IDENTITY_CHANGED:'+key)
    for name,expected in config.get('python_sources_sha256',{}).items():
        if Path(name).name!=name or hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()!=expected:
            raise ValueError('LEARNER_SOURCE_IDENTITY_CHANGED')
    return config


class Native:
    def __init__(self,config):self.config=config
    def call(self,value):
        p=subprocess.run([self.config['native_binary']],input=canonical(value),capture_output=True,text=True,timeout=30)
        out=strict(p.stdout) if p.stdout else {}
        if p.returncode or out.get('native_source_sha')!=self.config['native_source_sha'] or out.get('authorization_effect')!='NONE':
            raise RuntimeError('NATIVE_REJECTED:'+str(out.get('error','IDENTITY_OR_TRANSPORT')))
        return out
    def encode(self,samples):
        out=self.call({'op':'neuron','episodes':[s['native_episode'] for s in samples]})
        return np.asarray([s['raw_features']+e['activation'] for s,e in zip(samples,out['episodes'])],dtype=np.float32),out
    def utility(self,samples,pred,mask,y,dataset_digest):
        err=np.asarray(pred)-y
        per=(np.where(np.abs(err)<=1,.5*err*err,np.abs(err)-.5)*mask).sum(axis=1)/np.maximum(mask.sum(axis=1),1)
        events=[]
        for i,(s,loss) in enumerate(zip(samples,per)):
            dt=0 if i+1==len(samples) else max(0,samples[i+1]['anchor_ms']-s['anchor_ms'])
            events.append({'instant':-float(loss)/len(samples),'discount':math.exp(-dt/3600000),
                           'event_digest':digest({'sample':s['sample_id'],'outcomes':s['outcome_receipts']})})
        return self.call({'op':'utility','events':events,'dataset_digest':dataset_digest,
                          'objective_digest':digest(OBJECTIVE),'outcome_digest':digest([s['outcome_receipts'] for s in samples])})


def target_arrays(samples):
    n=len(samples);y=np.zeros((n,2),np.float32);mask=np.zeros_like(y);teacher=np.zeros_like(y);tm=np.zeros_like(y)
    for i,s in enumerate(samples):
        for j,target in enumerate(s['targets']):
            if target is not None:
                y[i,j]=target;mask[i,j]=1
        t=s.get('teacher')
        if not t or t.get('distillation_allowed') is not True:continue
        f=t['forecast'];h=f.get('horizon_sec')
        if h not in HORIZONS:continue
        j=HORIZONS.index(h)
        if not mask[i,j]:continue # Teacher alone can never manufacture a mature reward.
        scenarios=f.get('scenarios',[])
        probabilities=[float(x['probability']) for x in scenarios]
        if not probabilities or abs(sum(probabilities)-1)>1e-8 or any(p<=0 for p in probabilities):continue
        mean=sum(float(x['move_bps'])*p for x,p in zip(scenarios,probabilities))/100
        if not math.isfinite(mean) or abs(mean)>8:continue
        teacher[i,j]=mean;tm[i,j]=1
    return y,mask,teacher,tm


def independent_counts(samples):
    result={}
    for j,h in enumerate(HORIZONS):
        last=-1;count=0
        for s in samples:
            if s['targets'][j] is not None and s['anchor_ms']>last:
                last=s['anchor_ms']+h*1000;count+=1
        result[str(h)]=count
    return result


class Learner:
    def __init__(self,config):
        self.c=config;self.out=Path(config['output_dir']);self.out.mkdir(parents=True,mode=0o700,exist_ok=True)
        self.db=connect(self.out/'bridge.sqlite')
        self.native=Native(config)
        self.gpu=GpuHead(config['gpu_library'],config['gpu_library_sha256'])
        self.selected_pointer=self.out/'latest-training-candidate.json'
        current=self.db.execute("SELECT value FROM meta WHERE key='current_model_id'").fetchone()
        if current or self.selected_pointer.exists():
            if current:
                stored=self.db.execute('SELECT payload FROM models WHERE id=?',(current[0],)).fetchone()
                if stored is None:raise ValueError('COMMITTED_MODEL_MISSING')
                self.model=strict(stored[0])
            else:
                self.model=strict(self.selected_pointer.read_bytes())
            payload={k:v for k,v in self.model.items() if k!='model_id'}
            if self.model['model_id']!=digest(payload) or self.model['model_class']!=MODEL_CLASS:
                raise ValueError('CANDIDATE_IDENTITY_CORRUPT')
            if self.model['scope']!=config['period_sha256']+':'+config['instrument']:
                raise ValueError('CANDIDATE_PERIOD_CHANGED')
        else:
            self.model={'model_class':MODEL_CLASS,'scope':config['period_sha256']+':'+config['instrument'],
                'generation':1,'weights':initial_weights().astype(float).tolist(),'trained_until_ms':0,
                'created_at_ms':int(time.time()*1000),'parent':None,'objective_digest':digest(OBJECTIVE),
                'status':'UNTRAINED_BOOTSTRAP_SHADOW_ONLY','effect_authority':False}
            self.model['model_id']=digest(self.model)
            self.save_model(self.model)
        self.db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)',('current_model_id',self.model['model_id']));self.db.commit()
        atomic(self.selected_pointer,self.model) # Repair only from the committed local model record.
        stamp=self.db.execute("SELECT value FROM meta WHERE key='last_training_ms'").fetchone()
        prior=self.db.execute('SELECT payload FROM trains ORDER BY rowid DESC LIMIT 1').fetchone()
        self.last_train_ms=int(stamp[0]) if stamp else (int(strict(prior[0])['at_ms']) if prior else 0)
        if not 0<=self.last_train_ms<=int(time.time()*1000):raise ValueError('TRAINING_CLOCK_REGRESSION')
        self.last_fingerprint=None
        row=self.db.execute("SELECT value FROM meta WHERE key='training_fingerprint'").fetchone()
        if row:self.last_fingerprint=row[0]
        self.rounds=0

    def save_model(self,model):
        write_once(self.out/'candidates'/(model['model_id']+'.json'),model)
        self.db.execute('INSERT OR IGNORE INTO models VALUES (?,?)',(model['model_id'],canonical(model)));self.db.commit()
        atomic(self.selected_pointer,model) # A training/shadow pointer only; no execution consumer.

    def predict_before_learning(self,dataset,now):
        samples=[s for s in dataset['samples'] if s['known_at_ms']<=now and now-s['anchor_ms']<=60000
                 and self.model['trained_until_ms']<=s['anchor_ms'] and not s['input_gap']]
        samples=samples[-12:]
        if not samples:return 0
        # First prediction per sample is the forward-time comparison; do not rerank retrospectively.
        fresh=[]
        for s in samples:
            if not self.db.execute('SELECT 1 FROM predictions WHERE sample_id=? LIMIT 1',(s['sample_id'],)).fetchone():fresh.append(s)
        if not fresh:return 0
        x,native=self.native.encode(fresh);pred=self.gpu.run(x,self.model['weights'])
        made=int(time.time()*1000);count=0
        for s,p in zip(fresh,pred['prediction']):
            if made>=s['anchor_ms']+min(HORIZONS)*1000:continue
            payload={'schema':'heptatrader.neuron-prequential-prediction.v1','model_id':self.model['model_id'],
                'model_trained_until_ms':self.model['trained_until_ms'],'created_at_ms':made,'sample':s,
                'prediction_scaled':p.astype(float).tolist(),'zero_return_baseline_scaled':[0,0],
                'native_source_sha':self.c['native_source_sha'],'effect_authority':False}
            key=digest({'model':self.model['model_id'],'sample':s['sample_id']})
            self.db.execute('INSERT OR IGNORE INTO predictions VALUES (?,?,?,?)',(key,s['sample_id'],made,canonical(payload)));count+=1
        self.db.commit();return count

    def step(self,force_train=False):
        now=int(time.time()*1000)
        # Current accounting-period identity is checked before any data use.
        if hashlib.sha256(Path(self.c['period_path']).read_bytes()).hexdigest()!=self.c['period_sha256']:
            raise ValueError('ACCOUNTING_PERIOD_CHANGED_STOP_LEARNING')
        dataset=freeze(self.c,now)
        economics=replay_economics(self.c,now,dataset['settlements'])
        atomic(self.out/'economics.json',economics)
        predictions=self.predict_before_learning(dataset,now)
        mature=[s for s in dataset['samples'] if any(v is not None for v in s['targets'])][-OBJECTIVE['max_training_rows']:]
        stats={'at_ms':now,'state':'COLLECTING','round':self.rounds,'gpu':self.gpu.identity,
            'source_kind':dataset['source_kind'],'sample_count':len(dataset['samples']),'mature_rows':len(mature),
            'nonoverlapping_outcome_windows':independent_counts(mature),'label_reason_counts':dataset['label_reason_counts'],
            'exact_remote_inputs':dataset['exact_remote_inputs'],'teacher_actions':dataset['teacher_actions'],
            'legacy_teachers_without_exact_input_not_distilled':dataset['legacy_teachers_without_exact_input_not_distilled'],
            'new_forward_shadow_predictions':predictions,'candidate_model_id':self.model['model_id'],
            'pnl_reward_ready':economics.get('pnl_reward_ready',False),'broker_orders_sent':0,
            'selected_remote_model_changed':False,'strategy_accepted':False,'automatic_cutover':False}
        self.rounds+=1
        if len(mature)<OBJECTIVE['min_mature_rows']:
            stats['state']='NO_UPDATE_INSUFFICIENT_MATURE_ROWS';atomic(self.out/'status.json',stats);return stats
        fingerprint=digest([{'id':s['sample_id'],'y':s['targets'],'teacher':s['teacher']} for s in mature])
        if fingerprint==self.last_fingerprint or (not force_train and now-self.last_train_ms<300000):
            stats['state']='SHADOW_COLLECTING';atomic(self.out/'status.json',stats);return stats
        if sum(p.stat().st_size for p in self.out.rglob('*') if p.is_file())>1024**3:
            raise ValueError('LEARNING_STORAGE_BUDGET_NO_PRUNING')
        frozen={**dataset,'samples':mature,'objective':OBJECTIVE,'teacher_is_auxiliary_only':True}
        dataset_id=digest(frozen);write_once(self.out/'datasets'/(dataset_id+'.json'),frozen)
        x,native=self.native.encode(mature);y,mask,teacher,tm=target_arrays(mature)
        before=self.gpu.run(x,self.model['weights'],y=y,mask=mask,teacher=teacher,teacher_mask=tm)
        raw=self.gpu.run(x,self.model['weights'],y=y,mask=mask,teacher=teacher,teacher_mask=tm,epochs=16,lr=.01)
        candidate=project_candidate(before['weights'],raw['weights'])
        after=self.gpu.run(x,candidate,y=y,mask=mask,teacher=teacher,teacher_mask=tm)
        u0=self.native.utility(mature,before['prediction'],mask,y,dataset_id)
        u1=self.native.utility(mature,after['prediction'],mask,y,dataset_id)
        changed=int(np.count_nonzero(candidate!=before['weights']))
        diagnostic={'training_only':True,'dataset_digest':dataset_id,'huber_before':before['loss'],'huber_after':after['loss'],
                    'ndu_before':u0,'ndu_after':u1,'changed_parameters':changed}
        if u1['value']<u0['value'] or changed==0:
            candidate=before['weights'].copy();changed=0
        to_q32=lambda a:np.rint(np.asarray(a,dtype=np.float64)*4294967296).astype(np.int64).tolist()
        proposal=self.native.call({'op':'proposal','baseline_q32':to_q32(before['weights']),
            'candidate_q32':to_q32(candidate),'generation':self.model['generation'],'dataset_digest':dataset_id,
            'update_rule_digest':digest({'objective':OBJECTIVE,'gpu_binary':self.gpu.sha256}),
            'modulator_digest':u1['receipt_digest'],'eligibility_digest':digest(native['episodes']),
            'evaluation_digest':digest(diagnostic)})
        record={'schema':'heptatrader.neuron-training-receipt.v1','at_ms':int(time.time()*1000),
            'dataset_digest':dataset_id,'parent_model_id':self.model['model_id'],'diagnostic':diagnostic,
            'native_plasticity':proposal,'gpu_backend':self.gpu.identity,'gpu_kernel_ms':raw['gpu_kernel_ms'],
            'training_rows':len(mature),'mature_labels':int(mask.sum()),'teacher_auxiliary_labels':int(tm.sum()),
            'nonoverlapping_outcome_windows':independent_counts(mature),'effect_authority':False,
            'future_shadow_evaluation_required':True,'production_selection_performed':False,
            'pnl_reward_ready':economics.get('pnl_reward_ready',False)}
        next_model=self.model
        if changed:
            model={'model_class':MODEL_CLASS,'scope':dataset['scope'],'generation':self.model['generation']+1,
              'weights':candidate.astype(float).tolist(),'trained_until_ms':max(r['available_at_ms'] for s in mature for r in s['outcome_receipts'] if r),
              'created_at_ms':record['at_ms'],'parent':self.model['model_id'],'objective_digest':digest(OBJECTIVE),
              'dataset_digest':dataset_id,'native_proposal_digest':proposal['proposal_digest'],
              'status':'CANDIDATE_REQUIRES_INDEPENDENT_SHADOW','effect_authority':False}
            model['model_id']=digest(model)
            write_once(self.out/'candidates'/(model['model_id']+'.json'),model)
            next_model=model
        record['result_model_id']=next_model['model_id'];record['actual_parameter_update']=bool(changed)
        receipt_id=digest(record);write_once(self.out/'receipts'/(receipt_id+'.json'),record)
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO models VALUES (?,?)',(next_model['model_id'],canonical(next_model)))
            self.db.execute('INSERT INTO trains VALUES (?,?)',(receipt_id,canonical(record)))
            self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('training_fingerprint',fingerprint))
            self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('last_training_ms',str(record['at_ms'])))
            self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('current_model_id',next_model['model_id']))
        self.model=next_model
        atomic(self.selected_pointer,self.model)
        self.last_fingerprint=fingerprint;self.last_train_ms=record['at_ms']
        stats.update(state='GPU_CANDIDATE_TRAINED' if changed else 'NO_ADMISSIBLE_UPDATE',
            training_receipt=receipt_id,candidate_model_id=self.model['model_id'],changed_parameters=changed,
            training_huber_before=before['loss'],training_huber_after=after['loss'],gpu_kernel_ms=raw['gpu_kernel_ms'],
            native_ndu_receipt=u1['receipt_digest'],native_plasticity_proposal=proposal['proposal_digest'],
            teacher_auxiliary_labels=int(tm.sum()))
        atomic(self.out/'status.json',stats);return stats


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--config',type=Path,required=True);ap.add_argument('--once',action='store_true');a=ap.parse_args()
    os.umask(0o077);config=config_load(a.config);out=Path(config['output_dir']);out.mkdir(parents=True,mode=0o700,exist_ok=True)
    lock=os.open(out/'learner.lock',os.O_RDWR|os.O_CREAT|os.O_CLOEXEC,0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if config.get('require_sandbox') is True:
        from sandbox import enter
        atomic(out/'sandbox-learner.json',enter(config))
    learner=Learner(config)
    while True:
        try:result=learner.step(force_train=a.once);print(canonical(result),flush=True)
        except Exception as exc:
            result={'state':'LEARNING_UNAVAILABLE_NO_EXECUTION_EFFECT','reason':str(exc),'at_ms':int(time.time()*1000),
                    'broker_orders_sent':0,'selected_remote_model_changed':False}
            atomic(out/'status.json',result);print(canonical(result),flush=True)
            if a.once:raise
        if a.once:break
        time.sleep(20)
if __name__=='__main__':main()
