"""Persisted, isolated historical fits; no resident runtime mutation."""
from dataclasses import asdict, dataclass
from pathlib import Path
import os, resource, sys, time
import numpy as np
from .data import validate_dataset, regular_context, training_series, GRID, need, _checked_quotes
from .splits import validate_split, PARTITIONS
from .metrics import score, fit_temperature, apply_temperature
from .baselines import Prior, LinearSoftmax, CatBoostCPU, restore
from research.neuron_learning.laya_cell.dataset import BIN_EDGES_BPS, causal_state
from research.neuron_learning.laya_cell.resident_price_baselines import features, FEATURES
from research.neuron_learning.laya_cell.integrity import canonical, digest, read_json_snapshot, sha_file

@dataclass(frozen=True)
class ExperimentConfig:
    model: str = 'prior'
    mode: str = 'frozen'
    model_path: str | None = None
    revision: str | None = None
    device: str = 'cpu'
    threads: int = 2
    batch_size: int = 4
    seed: int = 20261102
    linear_steps: int = 400
    catboost_iterations: int = 200
    catboost_depth: int = 4
    lora_steps: int = 16
    lora_context_length: int = 24

def write_new(path, value):
    with Path(path).open('xb') as stream:
        stream.write(canonical(value)+b'\n'); stream.flush(); os.fsync(stream.fileno())
    return {'path':str(path),'sha256':sha_file(Path(path))}

def code_identity():
    here=Path(__file__).parent
    names=('data.py','source_proof.py','baselines.py','metrics.py','experiment.py','forecast_models.py')
    pins={name:sha_file(here/name) for name in names}
    resident=here.parent/'neuron_learning'/'laya_cell'
    for name in ('dataset.py','resident_price_baselines.py','integrity.py'):
        pins['resident/'+name]=sha_file(resident/name)
    return pins

def validate_backbone_load_identity(expected, actual):
    """Bind restored inference to the sealed implementation and base weights.

    Loading time and the local source path can change without changing model
    identity. Execution settings and observed dependency/parameter identities
    must still match, including before a LoRA adapter is restored.
    """
    need(isinstance(expected,dict) and isinstance(actual,dict),'MODEL_BACKBONE_LOAD_RECEIPT_REQUIRED')
    for field in ('versions','implementation_sha256'):
        need(isinstance(expected.get(field),dict) and bool(expected[field])
             and isinstance(actual.get(field),dict) and bool(actual[field]),
             'MODEL_BACKBONE_LOAD_IDENTITY_REQUIRED:'+field)
    fields=('schema','model_kind','model_id','source_revision','files_sha256','artifact_sha256',
            'device','dtype','seed','threads','interop_threads','batch_size','context_length_cap',
            'offline_only','training_mode','trainable_parameters','total_parameters',
            'parameters_sha256','versions','implementation_sha256')
    for field in fields:
        need(field in expected and field in actual and expected[field]==actual[field],
             'MODEL_BACKBONE_LOAD_IDENTITY_CHANGED:'+field)

def prediction_row(sample, instrument='EUR.USD', horizon_sec=300):
    state=causal_state(sample,instrument,horizon_sec)
    return {'sample_id':sample['sample_id'],'anchor_ms':sample['anchor_ms'],
        'input_available_at_ms':sample['known_at_ms'],'quotes':_checked_quotes(sample),
        'state':state,'numeric_features':features(state)}

def run_experiment(dataset_path, split_path, output, config):
    start=time.perf_counter(); output=Path(output).absolute(); output.mkdir(parents=True,exist_ok=False)
    need(config.model in ('prior','linear','catboost','chronos2_small','timestral_5m'),'MODEL_KIND')
    need(config.mode in ('frozen','lora') and (config.mode!='lora' or config.model=='chronos2_small'), 'TRAINING_MODE')
    dataset,dataset_sha=read_json_snapshot(Path(dataset_path),256*1024**2)
    split,split_sha=read_json_snapshot(Path(split_path),32*1024**2)
    validate_dataset(dataset); rows=validate_split(split,dataset)
    write_new(output/'frozen-inputs.json',{'dataset':str(dataset_path),'dataset_sha256':dataset_sha,
        'dataset_id':dataset['dataset_id'],'split':split,'split_sha256':split_sha,
        'config':asdict(config),'code_sha256':code_identity()})
    x={n:np.asarray([r['numeric_features'] for r in rows[n]],dtype=np.float64) for n in PARTITIONS}
    y={n:np.asarray([r['target_class'] for r in rows[n]],dtype=np.int64) for n in PARTITIONS}
    backbone=None; names=list(FEATURES)
    if config.model in ('chronos2_small','timestral_5m'):
        x,backbone,names=forecast_features(rows,x,config,output,dataset['horizon_sec'])
    model=(Prior() if config.model=='prior' else
           CatBoostCPU(config.catboost_iterations,config.catboost_depth,config.threads,config.seed)
           if config.model=='catboost' else LinearSoftmax(steps=config.linear_steps))
    model.fit(x['train'],y['train'],x['validation'],y['validation'])
    raw={name:model.predict_proba(x[name]) for name in ('validation','calibration','test')}
    calibration=fit_temperature(raw['calibration'],y['calibration'])
    calibration['row_ids_sha256']=digest(split['partitions']['calibration'])
    learned=model.save(output)
    state={'schema':'heptatrader.model-replacement-state.v1','model':learned,'backbone':backbone,
        'calibration':calibration,'class_order':list(range(7)),'bin_edges_bps':list(BIN_EDGES_BPS),
        'feature_names':names,'grid':GRID,'instrument':dataset['instrument'],'horizon_sec':dataset['horizon_sec'],
        'dataset_id':dataset['dataset_id'],'split_id':split['split_id'],'code_sha256':code_identity(),
        'training_cutoff_ms':max(r['outcome_available_at_ms'] for r in rows['train']),
        'validation_cutoff_ms':max(r['outcome_available_at_ms'] for r in rows['validation']),
        'calibration_cutoff_ms':max(r['outcome_available_at_ms'] for r in rows['calibration']),
        'usable_after_ms':max(r['outcome_available_at_ms'] for r in rows['calibration'])+1,
        'created_at_ms':int(time.time()*1000),'training_row_ids':split['partitions']['train'],
        'evaluation_kind':'HISTORICAL_OFFLINE_REPLAY','trade_permission':False}
    state['model_state_id']=digest(state); state_pin=write_new(output/'model-state.json',state)
    return finish_run(output,start,config,dataset,split,rows,x,y,raw,calibration,state_pin)

def forecast_features(rows,x,config,output,horizon_sec):
    from .forecast_models import ForecastModelConfig, LoRAFitConfig, create_forecast_adapter
    need(config.model_path and config.revision,'PINNED_LOCAL_MODEL_REQUIRED')
    cfg=ForecastModelConfig(model_kind=config.model,model_path=config.model_path,revision=config.revision,
        device=config.device,threads=config.threads,batch_size=config.batch_size,seed=config.seed)
    adapter=create_forecast_adapter(cfg); loaded=adapter.load()
    horizon=horizon_sec*1000//GRID['step_ms']; lora=None; series_receipt=None
    if config.mode=='lora':
        series,series_receipt=training_series(rows['train'],min_length=6+horizon)
        options=LoRAFitConfig(output_dir=str(output/'lora'),num_steps=config.lora_steps,
                             context_length=config.lora_context_length)
        lora=adapter.fit_lora(series,prediction_length=horizon,options=options)
    receipts={}; names=None
    for name in PARTITIONS:
        inputs=[regular_context(row) for row in rows[name]]
        batch=adapter.predict([value for value,_ in inputs],horizon_steps=horizon)
        need(batch.features.shape==(len(rows[name]),7) and np.isfinite(batch.features).all(),
             'FORECAST_FEATURE_SHAPE')
        current=list(FEATURES)+list(batch.feature_names)
        need(names is None or names==current,'FORECAST_FEATURE_ORDER_CHANGED'); names=current
        x[name]=np.concatenate([x[name],batch.features],axis=1)
        receipts[name]=write_new(output/('forecast-'+name+'.json'),{
            'evaluation_kind':'HISTORICAL_OFFLINE_REPLAY','created_at_ms':int(time.time()*1000),
            'row_ids':[r['row_id'] for r in rows[name]],'input_grids':[proof for _,proof in inputs],
            'feature_names':list(batch.feature_names),'features':batch.features.tolist(),
            'quantile_levels':list(batch.quantile_levels),'quantiles':batch.quantiles.tolist(),
            'metadata':batch.metadata})
    config_state={**asdict(cfg),'expected_files_sha256':loaded['files_sha256']}
    return x,{'config':config_state,'load':loaded,'lora':lora,'training_series':series_receipt,
              'forecast_files':receipts,'mode':config.mode},names

def finish_run(output,start,config,dataset,split,rows,x,y,raw,calibration,state_pin):
    prior=Prior().fit(x['train'],y['train'],x['validation'],y['validation'])
    metrics={}; predictions=[]
    for name in ('validation','calibration','test'):
        calibrated=apply_temperature(raw[name],calibration['temperature'])
        reference=prior.predict_proba(x[name])
        metrics[name]={'raw':score(raw[name],y[name]),'calibrated':score(calibrated,y[name]),
                       'train_prior':score(reference,y[name])}
        metrics[name]['brier_improvement_vs_prior']=metrics[name]['train_prior']['mean_brier']-metrics[name]['calibrated']['mean_brier']
        for i,row in enumerate(rows[name]):
            predictions.append({'row_id':row['row_id'],'sample_id':row['sample_id'],'partition':name,
                'anchor_ms':row['anchor_ms'],'input_available_at_ms':row['input_available_at_ms'],
                'outcome_available_at_ms':row['outcome_available_at_ms'],
                'outcome_receipt_digest':row['outcome_receipt_digest'],'target_class':row['target_class'],
                'raw_probabilities':raw[name][i].tolist(),'calibrated_probabilities':calibrated[i].tolist(),
                'train_prior_probabilities':reference[i].tolist(),'evaluation_kind':'HISTORICAL_OFFLINE_REPLAY'})
    pred_pin=write_new(output/'predictions.json',{'schema':'heptatrader.model-replacement-predictions.v1',
        'created_at_ms':int(time.time()*1000),'historical_predictions_not_pretrade_commits':True,
        'model_state_sha256':state_pin['sha256'],'rows':predictions})
    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)
    report={'schema':'heptatrader.model-replacement-result.v1','status':'COMPLETED',
        'evaluation_kind':'HISTORICAL_OFFLINE_REPLAY','config':asdict(config),
        'dataset_id':dataset['dataset_id'],'split_id':split['split_id'],
        'accounting':dataset['accounting'],'coverage':split['coverage'],'purged_rows':len(split['purged']),
        'metrics':metrics,'calibration':calibration,'model_state':state_pin,'predictions':pred_pin,
        'wall_seconds':time.perf_counter()-start,'max_process_rss_bytes':int(rss),
        'rss_gpu_not_additive_on_uma':True,'broker_orders_sent':0,'promoted':False,
        'efficacy_claim':False,'usd_value_model':None,'metric_scope':'PRICE_PROBABILITIES_NOT_EXECUTION_PNL'}
    write_new(output/'report.json',report)
    return report

class SavedPredictor:
    """Hash-pinned load once; reuse warm inference for newly sealed inputs."""
    def __init__(self,path,expected_sha256):
        state,sha=read_json_snapshot(Path(path),16*1024**2)
        need(sha==expected_sha256,'MODEL_STATE_FILE_CHANGED')
        need(state.get('schema')=='heptatrader.model-replacement-state.v1'
             and state.get('model_state_id')==digest({k:v for k,v in state.items() if k!='model_state_id'}),
             'MODEL_STATE_CONTENT_ID')
        need(state['class_order']==list(range(7)) and state['bin_edges_bps']==list(BIN_EDGES_BPS)
             and state['grid']==GRID,'MODEL_OUTPUT_POLICY_CHANGED')
        need(state['code_sha256']==code_identity(),'MODEL_INFERENCE_CODE_CHANGED')
        self.state=state; self.model=restore(state['model']); self.adapter=None
        if state['backbone']:
            from .forecast_models import ForecastModelConfig, create_forecast_adapter
            self.adapter=create_forecast_adapter(ForecastModelConfig(**state['backbone']['config']))
            loaded=self.adapter.load()
            validate_backbone_load_identity(state['backbone'].get('load'),loaded)
            if state['backbone']['lora']:
                self.adapter.restore_lora(state['backbone']['lora'])
    def predict(self,rows):
        need(rows,'PREDICTION_ROWS_REQUIRED'); start=time.perf_counter(); vectors=[]
        for row in rows:
            need(row['anchor_ms']>=max(self.state['usable_after_ms'],self.state['created_at_ms']),
                 'FRESH_INPUT_AFTER_MODEL_SEAL_REQUIRED')
            state=row['state']; need(state['instrument']==self.state['instrument']
                and state['horizon_sec']==self.state['horizon_sec'],'PREDICTION_SCOPE_CHANGED')
            need(state['quotes']==[[q['t']-row['anchor_ms'],q['bid'],q['ask']] for q in row['quotes']],
                 'PREDICTION_QUOTE_STATE_CHANGED')
            vectors.append(features(state))
        x=np.asarray(vectors,dtype=np.float64)
        if self.adapter is not None:
            batch=self.adapter.predict([regular_context(r)[0] for r in rows],
                horizon_steps=self.state['horizon_sec']*1000//GRID['step_ms'])
            need(list(FEATURES)+list(batch.feature_names)==self.state['feature_names'],'FEATURE_ORDER_CHANGED')
            x=np.concatenate([x,batch.features],axis=1)
        raw=self.model.predict_proba(x)
        return {'model_state_id':self.state['model_state_id'],'created_at_ms':int(time.time()*1000),
            'sample_ids':[r['sample_id'] for r in rows],'raw_probabilities':raw.tolist(),
            'probabilities':apply_temperature(raw,self.state['calibration']['temperature']).tolist(),
            'prediction_seconds':time.perf_counter()-start,'trade_permission':False}
