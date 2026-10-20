"""Sandboxed one-shot consumer for a dedicated systemd timer.
Only its research output is writable; no new sockets or trade authority.
"""
from __future__ import annotations
import argparse,fcntl,json,os,sys,time
from pathlib import Path
from .common import EvidenceError,read,atomic,write_once
from .current100k import validate_config

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True)
    args=parser.parse_args();os.umask(0o077);config=read(args.config)
    manifest=validate_config(config)
    if not config.get('runtime_sha256'):
        raise EvidenceError('PINNED_RUNTIME_REQUIRED_FOR_RESIDENT_CONSUMER')
    output=Path(config['output_root'])
    if output.is_symlink():raise EvidenceError('OUTPUT_ROOT_SYMLINK')
    output.mkdir(mode=0o700,parents=True,exist_ok=True)
    lock=os.open(output/'consumer.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    from sandbox import enter
    restrictions=enter({'output_dir':str(output),'period_path':config['period_path'],
        'legacy_capture':config['period_path'],'oms_path':config['reward_config']['oms_path'],
        'export_dir':manifest['journal_directory'],
        'read_only_paths':[config['runtime_root'],str(args.config),config['registration']['manifest'],sys.prefix]})
    write_once(output/'sandbox-proof.json',restrictions)
    try:
        from .export_current100k import export_validated
        status=export_validated(config,manifest)
    except Exception as error:
        atomic(output/'status.json',{'state':'UNAVAILABLE_FAIL_CLOSED','at_ms':int(time.time()*1000),
            'reason':str(error),'broker_orders_sent':0,'automatic_training':False})
        raise
    finally:
        os.close(lock)
    print(json.dumps({k:status[k] for k in ('at_ms','state','row_count','dataset_fingerprint','evaluation_skipped','refresh_duration_ms')}))
if __name__=='__main__':main()
