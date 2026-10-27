#!/usr/bin/env python3
"""Independent forward-time shadow observer. Never trains, selects or submits."""
from __future__ import annotations
import argparse
from collections import defaultdict,Counter
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from data import canonical,digest,freeze,strict,HORIZONS
from runner import config_load,atomic

MAX_PREDICTION_BATCH=4096


def pending_predictions(root):
    # Completed recent rows must not push older unscored predictions outside the
    # processing limit after evaluator downtime. Both attached databases are RO.
    with closing(sqlite3.connect((root/'bridge.sqlite').as_uri()+'?mode=ro',uri=True,timeout=2)) as source:
        source.execute('PRAGMA query_only=ON')
        source.execute('ATTACH DATABASE ? AS shadow',((root/'shadow-evaluation.sqlite').as_uri()+'?mode=ro',))
        missing=' OR '.join('NOT EXISTS (SELECT 1 FROM shadow.outcomes o WHERE o.key=p.id||\':\'||?)' for _ in HORIZONS)
        rows=source.execute('SELECT p.id,p.payload FROM predictions p WHERE '+missing+
            ' ORDER BY p.created_ms,p.id LIMIT ?',(*HORIZONS,MAX_PREDICTION_BATCH)).fetchall()
    return [(pid,strict(payload)) for pid,payload in rows]


def evaluate(config):
    if hashlib.sha256(Path(config['period_path']).read_bytes()).hexdigest()!=config['period_sha256']:
        raise ValueError('ACCOUNTING_PERIOD_CHANGED_STOP_EVALUATION')
    now=int(time.time()*1000);root=Path(config['output_dir']).resolve()
    if not (root/'bridge.sqlite').exists():return {'state':'NO_PREDICTIONS','strategy_accepted':False}
    with closing(sqlite3.connect(root/'shadow-evaluation.sqlite',timeout=2)) as db:
        db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
        db.execute('CREATE TABLE IF NOT EXISTS outcomes(key TEXT PRIMARY KEY,model TEXT NOT NULL,horizon INTEGER NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL)')
        db.commit()
        return evaluate_batch(config,root,db,now,pending_predictions(root))


def evaluate_batch(config,root,db,now,predictions):
    dataset=freeze(config,now,prediction_samples=[p['sample'] for _,p in predictions]) if predictions else None
    by_id={s['sample_id']:s for s in dataset['prediction_samples']} if dataset else {}
    pending=0
    for pid,p in predictions:
        s=by_id.get(p['sample']['sample_id'])
        for j,horizon in enumerate(HORIZONS):
            key=pid+':'+str(horizon)
            if db.execute('SELECT 1 FROM outcomes WHERE key=?',(key,)).fetchone():continue
            if s is None:
                pending+=1;continue
            reason=s['label_reasons'][j]
            if reason in ('OUTCOME_NOT_MATURE','OUTCOME_NOT_KNOWN'):pending+=1;continue
            status='OBSERVED' if reason=='OBSERVED' else 'CENSORED'
            if (p['model_trained_until_ms']>s['anchor_ms'] or p['created_at_ms']>=s['anchor_ms']+horizon*1000):
                status='INVALID_TIMING';reason='MODEL_OR_PREDICTION_LOOKAHEAD'
            row={'prediction_id':pid,'model_id':p['model_id'],'sample_id':s['sample_id'],
              'horizon_sec':horizon,'prediction_created_at_ms':p['created_at_ms'],'anchor_ms':s['anchor_ms'],
              'status':status,'reason':reason,'outcome_receipt':s['outcome_receipts'][j],
              'prediction_scaled':p['prediction_scaled'][j],'target_scaled':s['targets'][j],
              'evaluation_at_ms':now,'effect_authority':False}
            if status=='OBSERVED':
                target=s['targets'][j];error=p['prediction_scaled'][j]-target
                huber=lambda d: .5*d*d if abs(d)<=1 else abs(d)-.5
                row['student_huber']=huber(error);row['zero_return_huber']=huber(target)
                t=s.get('teacher')
                if t and t.get('distillation_allowed') and t['forecast'].get('horizon_sec')==horizon:
                    guess=sum(float(x['probability'])*float(x['move_bps']) for x in t['forecast']['scenarios'])/100
                    row['same_input_remote_huber']=huber(guess-target)
                    row['remote_candidate_id']=t['candidate_id']
            db.execute('INSERT INTO outcomes VALUES (?,?,?,?,?)',(key,p['model_id'],horizon,status,canonical(row)))
    db.commit()
    groups=defaultdict(list);counts=Counter()
    for model,h,status,payload in db.execute('SELECT model,horizon,status,payload FROM outcomes'):
        counts[status]+=1
        if status=='OBSERVED':groups[(model,h)].append(json.loads(payload))
    metrics=[]
    for (model,h),rows in groups.items():
        ordered=sorted(rows,key=lambda r:r['anchor_ms']);last=-1;independent=0
        for r in ordered:
            if r['anchor_ms']>last:independent+=1;last=r['anchor_ms']+h*1000
        paired=[r for r in rows if 'same_input_remote_huber' in r]
        metrics.append({'model_id':model,'horizon_sec':h,'observed_rows':len(rows),
          'nonoverlapping_windows':independent,'mean_student_huber':sum(r['student_huber'] for r in rows)/len(rows),
          'mean_zero_return_huber':sum(r['zero_return_huber'] for r in rows)/len(rows),
          'same_input_remote_comparisons':len(paired),
          'mean_same_input_remote_huber':sum(r['same_input_remote_huber'] for r in paired)/len(paired) if paired else None,
          'mean_student_huber_on_remote_pairs':sum(r['student_huber'] for r in paired)/len(paired) if paired else None})
    result={'schema':'heptatrader.neuron-forward-shadow.v1','at_ms':now,
      'state':'OBSERVING' if counts['OBSERVED'] else 'WAITING_FOR_FORWARD_OUTCOMES',
      'outcomes':dict(counts),'pending':pending,'by_model_and_horizon':metrics,
      'prediction_batch_size':len(predictions),'prediction_batch_limit':MAX_PREDICTION_BATCH,
      'pending_scope':'OLDEST_UNFINISHED_PREDICTION_BATCH',
      'metric_basis':'FORWARD_PRICE_PREDICTION_NOT_EXECUTION_NET_PNL',
      'strategy_accepted':False,'model_replacement_performed':False,'broker_orders_sent':0,
      'training_performed_by_evaluator':False,'no_zero_trade_profitability_claim':True}
    atomic(root/'shadow-status.json',result);return result


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--config',type=Path,required=True);ap.add_argument('--once',action='store_true');a=ap.parse_args()
    os.umask(0o077);config=config_load(a.config);root=Path(config['output_dir']);root.mkdir(parents=True,mode=0o700,exist_ok=True)
    lock=os.open(root/'evaluation.lock',os.O_RDWR|os.O_CREAT|os.O_CLOEXEC,0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if config.get('require_sandbox') is True:
        from sandbox import enter
        atomic(root/'sandbox-evaluator.json',enter(config))
    while True:
        try:print(canonical(evaluate(config)),flush=True)
        except Exception as exc:
            out={'state':'EVALUATION_UNAVAILABLE','reason':str(exc),'strategy_accepted':False,'broker_orders_sent':0}
            atomic(root/'shadow-status.json',out);print(canonical(out),flush=True)
            if a.once:raise
        if a.once:break
        time.sleep(30)
if __name__=='__main__':main()
