"""Conservative host admission for offloaded frozen encoder jobs.
A cgroup cap alone is not a host headroom guarantee, especially on a UMA host.
"""
from __future__ import annotations
import argparse,math,platform,time,sys
from pathlib import Path
from .common import EvidenceError,read,sha,write_once


def assess(mem,pressure,host,expected):
    fields=('MemTotal','MemAvailable','SwapTotal','SwapFree')
    if any(type(mem.get(k)) is not int or mem[k]<0 for k in fields):raise EvidenceError('HOST_MEMORY_TELEMETRY_INVALID')
    if expected!='qian-qi' or host!=expected:raise EvidenceError('HEAVY_ENCODER_HOST_NOT_APPROVED')
    if not math.isfinite(pressure) or pressure<0:raise EvidenceError('PRESSURE_TELEMETRY_INVALID')
    if mem['MemTotal']<64*1024**3 or mem['MemAvailable']<32*1024**3:raise EvidenceError('HOST_ENCODER_HEADROOM_INSUFFICIENT')
    if mem['SwapTotal'] and mem['SwapFree']<min(1024**3,mem['SwapTotal']//4):raise EvidenceError('HOST_SWAP_EXHAUSTION')
    if pressure>.25:raise EvidenceError('HOST_MEMORY_PRESSURE')
    return {'host':host,'memory_bytes':mem,'memory_full_avg10':pressure,'min_available_bytes':32*1024**3,
        'job_memory_cap_bytes':4*1024**3,'admitted':True,'host_independence_from_trading_required':True,
        'continuous_global_resource_guarantee':False}


def current(expected):
    mem={}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key,value=line.split(':',1)
        if key in ('MemTotal','MemAvailable','SwapTotal','SwapFree'):mem[key]=int(value.split()[0])*1024
    lines=Path('/proc/pressure/memory').read_text().splitlines();full=next(x for x in lines if x.startswith('full '))
    pressure=float(dict(x.split('=') for x in full.split()[1:])['avg10'])
    return assess(mem,pressure,platform.node(),expected)


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);a=p.parse_args();cfg=read(a.config)
    proof=current(cfg['approved_compute_host']);root=Path(cfg['output']);plan=read(root/'plan.json')
    if sha(root/'plan.json')!=cfg['original_plan_sha256']:raise EvidenceError('RECOVERY_PLAN_CHANGED')
    if sha(root/'input.json')!=plan['input_sha256']:raise EvidenceError('RECOVERY_POPULATION_CHANGED')
    for name,wanted in plan['source_files_sha256'].items():
        if sha(Path(__file__).parent/name)!=wanted:raise EvidenceError('ORIGINAL_PAIRED_SOURCE_CHANGED:'+name)
    proof.update(schema='heptatrader.offload-headroom-admission.v1',at_ms=int(time.time()*1000),
        original_plan_sha256=sha(root/'plan.json'),input_sha256=sha(root/'input.json'),
        recovery_reason='ORIGINAL_HOST_GLOBAL_OOM_NO_COMPLETED_COMPARISON',
        backend_change='SAME_TORCH_2_11_CPU_OPERATIONS_NEW_CPU_WHEEL_PYTHON312; BOTH_ARMS_SAME_BACKEND',
        original_failed_attempt_retained=True,guard_source_sha256=sha(__file__))
    write_once(root/'host-admission.json',proof)
    from .matched_encode import encode
    encode(cfg,a.config)
if __name__=='__main__':main()
