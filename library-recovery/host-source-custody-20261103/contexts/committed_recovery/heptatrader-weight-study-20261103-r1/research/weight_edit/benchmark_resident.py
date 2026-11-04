"""One CPU model load: known-row batch-two latency and numeric parity only."""
import argparse,inspect,json,os,resource,time
from pathlib import Path
import current100k_study as study


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('study','runtime','base'):p.add_argument('--'+name,required=True)
    p.add_argument('--threads',type=int,choices=(2,4),default=4)
    args=p.parse_args();root=Path(args.study);torch=study.configure_cpu(args.threads)
    from safetensors.torch import load_file
    _,candidate,rows=study.snapshot(root);identity=json.loads((root/'features/identity.json').read_text())
    started=time.monotonic();encoder=study.WarmCpuEncoder(args.runtime,args.base,threads=args.threads,batch_size=2)
    load_seconds=time.monotonic()-started
    module=study.pinned_module(Path(args.runtime)/'learn/laya_cell','model')
    actual_loader_sha=study.sha(inspect.getfile(module))
    if actual_loader_sha!=identity['model_loader_sha256']:raise RuntimeError('ACTUAL_LOADER_DIFFERS_FROM_FEATURE_CACHE')
    cache=load_file(str(root/'features/original-fp32.safetensors'))['features']
    parent=load_file(str(root/'snapshot/value-head.safetensors'));results=[]
    for i in sorted(set((0,len(rows)//3,2*len(rows)//3,len(rows)-1))):
        request=rows[i]['source_request']['request'];t=time.monotonic();doc=encoder.encode_request(request);wall=time.monotonic()-t
        side=rows[i]['action'];opposite='SELL' if side=='BUY' else 'BUY'
        if doc['feature_text_sha256'][side]!=rows[i]['feature_text_sha256']:raise RuntimeError('REQUEST_FEATURE_IDENTITY')
        pair={k:torch.tensor(v,dtype=torch.float32).reshape(1,768) for k,v in doc['features'].items()}
        flip=load_file(str(root/'features/probes'/f'row-{i:04d}-action_flip.safetensors'))['features']
        reference={side:cache[i:i+1],opposite:flip}
        results.append({'row_index':i,'wall_seconds':wall,'backend':doc['backend'],
                        'max_feature_abs_difference':max(float((pair[k]-reference[k]).abs().max()) for k in pair),
                        'max_parent_usd_difference':max(float((study.head_forward(parent,pair[k])-study.head_forward(parent,reference[k])).abs().max()) for k in pair),
                        'token_counts':{k:v['tokens'] for k,v in doc['receipts'].items()}})
        study.log('resident_batch_probe',**results[-1])
    report={'schema':'heptatrader.current100k-batch2-benchmark.v1','scope':'KNOWN_DEVELOPMENT_REQUESTS_NOT_PROSPECTIVE','samples':results,'load_seconds':load_seconds,'threads':args.threads,'batch_size':2,'cpu_affinity':sorted(os.sched_getaffinity(0)),'actual_loader_path':inspect.getfile(module),'actual_loader_sha256':actual_loader_sha,'gpu_initialized':torch.cuda.is_initialized(),'max_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,'total_seconds':time.monotonic()-started}
    study.json_write(root/f'batch2-threads{args.threads}-benchmark.json',report)
    study.log('resident_benchmark_complete',total_seconds=report['total_seconds'])

if __name__=='__main__':main()
