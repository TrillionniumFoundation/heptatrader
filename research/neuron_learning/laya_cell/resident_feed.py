"""Resident read-only price feed, using the existing bounded shadow collector.

Source segments never get overwritten or silently pruned. No trading activation,
no new IB client, and no mutation tool is invoked by this process.
"""
from __future__ import annotations
import argparse,fcntl,json,os,signal,subprocess,sys,time,uuid
from pathlib import Path
from .integrity import ContractError,read_json,sha_file,canonical


def run(config_path):
    config=read_json(config_path);root=Path(config['feed_directory']);root.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(root/'producer.lock',os.O_WRONLY|os.O_CREAT|os.O_CLOEXEC,0o600);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    stop=False;child=None
    def finish(*_):
        nonlocal stop
        stop=True
        if child is not None and child.poll() is None:child.terminate()
    signal.signal(signal.SIGTERM,finish);signal.signal(signal.SIGINT,finish)
    try:
        while not stop:
            if sha_file(Path(config['period_path']))!=config['period_sha256']:raise ContractError('FEED_PERIOD_CHANGED')
            used=sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
            if used>config['feed_max_bytes']:raise ContractError('FEED_BUDGET_FULL_HISTORY_PRESERVED')
            segment=root/('segment-'+str(time.time_ns())+'-'+uuid.uuid4().hex[:8])
            with (root/'collector.log').open('a') as log:
                child=subprocess.Popen([sys.executable,'-B','-m','laya_cell.shadow_source','--output',str(segment),'--seconds','1800'],stdout=log,stderr=log)
                rc=child.wait()
            status={'schema':'heptatrader.laya-feed-status.v1','at_ms':int(time.time()*1000),'last_segment':str(segment),
                    'child_exit':rc,'period_sha256':config['period_sha256'],'broker_orders_sent':0,'source_data_deleted':False}
            p=root/'status.json';tmp=root/'status.next';tmp.write_bytes(canonical(status)+b'\n');tmp.chmod(0o600);os.replace(tmp,p)
            if rc and not stop:time.sleep(10)
    finally:
        if child is not None and child.poll() is None:child.terminate();child.wait(timeout=12)
        os.close(fd)


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);a=p.parse_args();os.umask(0o077);run(a.config)
if __name__=='__main__':main()
