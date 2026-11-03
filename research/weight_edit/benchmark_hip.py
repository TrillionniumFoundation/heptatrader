"""One admitted HIP encoder, known-input latency/parity; no production publishing."""
from __future__ import annotations
import argparse,importlib,importlib.util,json,os,sys,time,types
from pathlib import Path


def namespace():
    package=types.ModuleType('weight_edit');package.__path__=[str(Path(__file__).parent)]
    sys.modules['weight_edit']=package
    return importlib.import_module('weight_edit.current100k_study')


def load_helper(study,path,wanted,runtime):
    if study.sha(path)!=wanted:raise RuntimeError('HIP_HELPER_PIN')
    sys.path[:0]=[str(runtime/'scripts'),str(runtime/'research'),str(runtime/'learn')]
    integrity=study.pinned_module(runtime/'learn/laya_cell','integrity')
    name=integrity.__package__+'.weight_study_hip_encoder'
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module
    spec.loader.exec_module(module)
    return module


def invoke(guard,name,*args):
    previous=list(sys.path)
    try:return getattr(guard,name)(*args)
    finally:sys.path[:]=previous


def original_guard(study,c):
    path=Path(c['guard_config'])
    if study.sha(path)!=c['guard_config_sha256']:raise RuntimeError('ORIGINAL_GUARD_CONFIG_PIN')
    config=json.loads(path.read_text());directory=path.parent/'research/historical_training'
    for name,wanted in config['guard_files_sha256'].items():
        if Path(name).name!=name or study.sha(directory/name)!=wanted:raise RuntimeError('ORIGINAL_GUARD_SOURCE_PIN')
    package=types.ModuleType('_hepta_study_guard');package.__path__=[str(directory)]
    sys.modules[package.__name__]=package
    module=importlib.import_module(package.__name__+'.resident_resource_guard')
    previous=list(sys.path)
    try:original=module.verify_config(config)
    finally:sys.path[:]=previous
    if config['period_sha256']!=c['period_sha256']:raise RuntimeError('ORIGINAL_GUARD_PERIOD')
    if study.sha(c['period_path'])!=c['period_sha256']:raise RuntimeError('ORIGINAL_PERIOD_PIN')
    return module.Gate(config,'value-predictor'),original


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('study','runtime','base','config','config-sha256','helper','helper-sha256'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--batch-size',type=int,choices=(1,2),default=1)
    args=p.parse_args();study=namespace();root=Path(args.study);runtime=Path(args.runtime)
    os.umask(0o077)
    if study.sha(args.config)!=args.config_sha256:raise RuntimeError('PREDICTOR_CONFIG_PIN')
    scope=json.loads(Path(args.config).read_text());helper=load_helper(study,args.helper,args.helper_sha256,runtime)
    c,hip,environment=helper.hip_binding(args.config,args.config_sha256,args.runtime,args.base,scope['period_sha256'])
    environment.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',USE_TF='0',TOKENIZERS_PARALLELISM='false',
        OPENBLAS_NUM_THREADS='2',OMP_NUM_THREADS='2',MKL_NUM_THREADS='2',NUMEXPR_NUM_THREADS='2')
    if not helper.environment_is_bound(c,hip,environment):
        os.execve(c['python'],[c['python'],'-B',str(Path(__file__).resolve()),*sys.argv[1:]],environment)
    if 'torch' in sys.modules:raise RuntimeError('TORCH_IMPORTED_BEFORE_ADMISSION')
    manifest,candidate,rows=study.snapshot(root);identity=json.loads((root/'features/identity.json').read_text())
    gate,original=original_guard(study,c);encoder=None;released=False;cleanup_attempted=False;started=time.monotonic()
    subject={'kind':'CURRENT100K_WEIGHT_STUDY_FIXED_INPUT_HIP_PARITY','dataset_sha256':manifest['files']['dataset-selected.json']['sha256'],
        'batch_size':args.batch_size,'threads':2,'broker_orders_sent':0}
    if not invoke(gate,'acquire',subject):
        study.log('hip_benchmark_not_admitted',reasons=(gate.last_admission or {}).get('reasons',[]));return
    study.log('hip_benchmark_admitted',batch_size=args.batch_size)
    try:
        t=time.monotonic();encoder=helper.WarmHipEncoder(args.runtime,args.base,c,guard_original=original,threads=2,batch_size=args.batch_size)
        load_seconds=time.monotonic()-t;torch=encoder.torch
        from safetensors.torch import load_file
        actual=study.pinned_module(runtime/'learn/laya_cell','model')
        actual_sha=study.sha(actual.__file__)
        if actual_sha!=identity['model_loader_sha256'] or encoder.base_id!=candidate['base_id']:raise RuntimeError('CACHE_MODEL_IDENTITY')
        cache=load_file(str(root/'features/original-fp32.safetensors'))['features']
        bundle=json.loads((root/'candidate-bundle.json').read_text());heads={}
        for arm in bundle['candidates']:
            if study.sha(arm['candidate_path'])!=arm['sha256']:raise RuntimeError('FROZEN_HEAD_PIN')
            heads[arm['name']]=load_file(arm['candidate_path'])
        def predict(values):
            x=torch.tensor([values[s] for s in ('BUY','SELL')],dtype=torch.float32)
            with torch.inference_mode():return {n:torch.nn.functional.linear(x,h['value.weight'],h['value.bias']).flatten()*100 for n,h in heads.items()}
        t=time.monotonic();warm=encoder.encode_request(rows[36]['source_request']['request']);predict(warm['features'])
        warm_seconds=time.monotonic()-t;study.log('hip_warmup_complete',seconds=warm_seconds,peak_bytes=warm['peak_allocated_bytes'])
        samples=[]
        for i in (0,18,36,53):
            admission=invoke(gate,'check')
            if not admission['admit_new_worker']:raise RuntimeError('GATE_WITHDREW:'+','.join(admission['reasons']))
            if time.monotonic()-started>180:raise RuntimeError('FINITE_EXPERIMENT_BUDGET')
            row=rows[i];side=row['action'];opposite='SELL' if side=='BUY' else 'BUY'
            t=time.monotonic();doc=encoder.encode_request(row['source_request']['request']);scores=predict(doc['features']);total=time.monotonic()-t
            if doc['feature_text_sha256'][side]!=row['feature_text_sha256']:raise RuntimeError('REAL_FEATURE_TEXT_IDENTITY')
            probe=root/'features/probes'/f'row-{i:04d}-action_flip.safetensors'
            reference={side:cache[i:i+1],opposite:load_file(str(probe))['features']}
            flip_receipt=json.loads(probe.with_suffix('.json').read_text())
            if doc['feature_text_sha256'][opposite]!=flip_receipt['feature_text_sha256']:raise RuntimeError('ACTION_FEATURE_TEXT_IDENTITY')
            features={s:torch.tensor(v).reshape(1,768) for s,v in doc['features'].items()}
            reference_values={s:reference[s][0].tolist() for s in ('BUY','SELL')};old=predict(reference_values)
            feature_error=max(float((features[s]-reference[s]).abs().max()) for s in features)
            head_error={n:float((scores[n]-old[n]).abs().max()) for n in heads}
            samples.append({'row_index':i,'backend':doc['backend'],'total_encode_and_heads_seconds':total,
                'encode_seconds':doc['request_encode_seconds'],'max_feature_abs_error':feature_error,'head_max_abs_usd_errors':head_error,
                'parity_passed':feature_error<=2e-5 and max(head_error.values())<=.001,
                'receipts':doc['receipts'],'peak_allocated_bytes':doc['peak_allocated_bytes']})
            study.log('hip_probe_complete',**samples[-1])
        report={'schema':'heptatrader.current100k-hip-parity-benchmark.v1','scope':'KNOWN_DEVELOPMENT_REQUESTS_NOT_PROSPECTIVE',
            'backend':encoder.backend,'encoder_threads':2,'encoder_batch_size':args.batch_size,'heads_backend':'TORCH_CPU_FP32',
            'load_seconds':load_seconds,'warmup_row_index':36,'warmup_seconds':warm_seconds,'samples':samples,
            'parity_limits':{'feature_max_abs':2e-5,'all_heads_max_abs_usd':.001},'actual_loader_sha256':actual_sha,
            'helper_sha256':args.helper_sha256,'benchmark_source_sha256':study.sha(__file__),'config_sha256':args.config_sha256,
            'feature_cache_sha256':study.sha(root/'features/original-fp32.safetensors'),'candidate_bundle_sha256':study.sha(root/'candidate-bundle.json'),
            'hip_runtime_manifest_sha256':c['hip_runtime']['sha256'],'last_hip_proof':doc['hip_runtime_proof'],
            'cpu_affinity':sorted(os.sched_getaffinity(0)),'memory_fraction_limit_bytes':helper.MEMORY_LIMIT_BYTES,
            'peak_allocated_bytes':torch.cuda.max_memory_allocated(),'peak_reserved_bytes':torch.cuda.max_memory_reserved(),
            'total_seconds':time.monotonic()-started,'broker_orders_sent':0,'readout_training_performed':False}
        cleanup_attempted=True
        try:
            encoder.close();report['allocation_after_close_bytes']=torch.cuda.memory_allocated();report['cleanup_verified']=True
            report['cleanup_receipt']=getattr(encoder,'cleanup_receipt',None)
        except Exception as exc:
            report.update(cleanup_verified=False,cleanup_error=str(exc),allocation_after_close_bytes=torch.cuda.memory_allocated())
            study.json_write(root/f'hip-batch{args.batch_size}-threads2-cleanup-failure-{time.time_ns()}.json',report)
            raise
        invoke(gate,'release');released=True
        study.json_write(root/f'hip-batch{args.batch_size}-threads2-benchmark.json',report)
        study.log('hip_benchmark_complete',seconds=report['total_seconds'],cleanup_verified=True,all_parity_passed=all(s['parity_passed'] for s in samples))
    finally:
        if encoder is not None and not released and not cleanup_attempted:
            encoder.close();invoke(gate,'release')
        # If construction or cleanup is uncertain, process exit owns the lease.


if __name__=='__main__':main()
