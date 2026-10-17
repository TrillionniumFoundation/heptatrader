"""Resident read-only price feed, using the existing bounded shadow collector.

Source segments never get overwritten or silently pruned. No trading activation,
no new IB client, and no mutation tool is invoked by this process.
"""
from __future__ import annotations
import argparse,fcntl,json,os,signal,subprocess,sys,time,uuid
from pathlib import Path
from .integrity import ContractError,read_json,sha_file,canonical
from .feed_health import atomic_status, feed_status, historical_last_quote, UPDATE_SECONDS


def run(config_path):
    config=read_json(config_path);root=Path(config['feed_directory']);root.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(root/'producer.lock',os.O_WRONLY|os.O_CREAT|os.O_CLOEXEC,0o600);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    stop=False;child=None
    last_quote,prior_open_failure=historical_last_quote(root,config['period_sha256'])
    def publish(segment,progress,rc):
        nonlocal last_quote,prior_open_failure
        at_ms=int(time.time()*1000)
        health=feed_status(progress,last_quote,prior_open_failure,at_ms)
        last_quote=health['last_valid_quote']
        prior_open_failure=health['prior_regular_session_outage_unrecovered']
        status={'schema':'heptatrader.laya-feed-status.v1','at_ms':at_ms,
                'last_segment':str(segment),'active_segment':str(segment) if rc is None else None,
                'child_exit':rc,'child_pid':child.pid if child else None,
                'process_state':'RUNNING' if rc is None else 'COMPLETED',
                'period_sha256':config['period_sha256'],'data_health':health,
                'broker_orders_sent':0,'source_data_deleted':False}
        atomic_status(root/'status.json',status)
    def progress_read(segment):
        try:
            progress=read_json(segment/'collector-status.json',65536)
            if (progress.get('period_sha256')!=config['period_sha256']
                or progress.get('source_path')!=str(segment/'quotes.jsonl')
                or progress.get('collector_pid')!=child.pid
                or progress.get('schema')!='heptatrader.laya-feed-data-health.v1'
                or type(progress.get('at_ms')) is not int):
                return None
            return progress
        except (OSError,ValueError,ContractError):
            return None
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
                publish(segment,None,None)
                next_update=time.monotonic()
                while child.poll() is None:
                    if time.monotonic()>=next_update:
                        publish(segment,progress_read(segment),None)
                        next_update=time.monotonic()+UPDATE_SECONDS
                    time.sleep(.2)
                rc=child.wait()
            publish(segment,progress_read(segment),rc)
            if rc and not stop:time.sleep(10)
    finally:
        if child is not None and child.poll() is None:child.terminate();child.wait(timeout=12)
        os.close(fd)


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);a=p.parse_args();os.umask(0o077);run(a.config)
if __name__=='__main__':main()
