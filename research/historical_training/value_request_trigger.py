"""Lightweight request-trigger for the ORIGINAL Value worker; never makes predictions.

Watches the owner-local attribution journal's newly appended records. Historical
records are skipped on startup. The frozen worker remains responsible for all
admission, provenance, time, model and publication checks.
"""
from __future__ import annotations
import argparse,json,os,stat,subprocess,time
from pathlib import Path
UNIT='heptatrader-current100k-forward-predictor-trial3.service'
MAX_LINE=1024*1024
MAX_POLL=8*1024*1024

class Watcher:
    def __init__(self,directory,clock=lambda:int(time.time()*1000)):
        self.directory=Path(directory);self.clock=clock;self.files={};self.last_ids={}
        self._check_directory()
        self.bootstrap()
    def _check_directory(self):
        st=self.directory.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o077:
            raise ValueError('ATTRIBUTION_DIRECTORY_NOT_PRIVATE')
    def _files(self):
        self._check_directory()
        return sorted((p for p in self.directory.iterdir() if p.name.endswith('.jsonl')),
                      key=lambda p:p.name)
    def _open(self,p):
        fd=os.open(p,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o077:
            os.close(fd);raise ValueError('ATTRIBUTION_FILE_NOT_PRIVATE')
        return fd,st
    def bootstrap(self):
        for p in self._files():
            fd,st=self._open(p)
            try:self.files[p.name]=(st.st_dev,st.st_ino,st.st_size,b'')
            finally:os.close(fd)
    def poll(self):
        now=self.clock();events=[]
        for p in self._files():
            fd,st=self._open(p)
            try:
                prior=self.files.get(p.name)
                if prior is None: # New segment: consume new, not old, records.
                    self.files[p.name]=(st.st_dev,st.st_ino,0,b'')
                    prior=self.files[p.name]
                dev,ino,offset,tail=prior
                if st.st_dev!=dev or st.st_ino!=ino or st.st_size<offset:
                    # Never interpret a replaced/truncated file as a fresh request.
                    self.files[p.name]=(st.st_dev,st.st_ino,st.st_size,b'')
                    continue
                available=st.st_size-offset
                if available>MAX_POLL:raise ValueError('ATTRIBUTION_TAIL_BACKLOG_TOO_LARGE')
                os.lseek(fd,offset,os.SEEK_SET)
                chunk=os.read(fd,available)
                if len(chunk)!=available:raise ValueError('ATTRIBUTION_SHORT_APPEND')
                buf=tail+chunk;lines=buf.split(b'\n');tail=lines.pop()
                if len(tail)>MAX_LINE:raise ValueError('ATTRIBUTION_OVERSIZED_RECORD')
                self.files[p.name]=(dev,ino,offset+len(chunk),tail)
                for line in lines:
                    if len(line)>MAX_LINE:raise ValueError('ATTRIBUTION_OVERSIZED_RECORD')
                    obj=json.loads(line)
                    if obj.get('kind')!='REQUEST' or not isinstance(obj.get('data'),dict):continue
                    req=obj['data'];rid=req.get('request_id')
                    start=req.get('started_at_ms');written=obj.get('written_at_ms')
                    if (not isinstance(rid,str) or len(rid)>96 or type(start) is not int
                        or type(written) is not int or req.get('position_at_request')!=0
                        or not (start<=written<=now) or not (0<=now-start<60000)
                        or rid in self.last_ids):continue
                    self.last_ids[rid]=now
                    events.append({'request_id':rid,'started_at_ms':start})
            finally:os.close(fd)
        # Avoid unbounded memory; no retry of expired old IDs.
        self.last_ids={k:v for k,v in self.last_ids.items() if v>=now-180000}
        return events

def main():
    a=argparse.ArgumentParser()
    a.add_argument('--manifest',required=True)
    a.add_argument('--once',action='store_true')
    a.add_argument('--dry-run',action='store_true')
    a.add_argument('--input-end-ms',type=int,required=True)
    opt=a.parse_args();os.umask(0o077)
    if opt.input_end_ms<=0:raise ValueError('INVALID_FIXED_END')
    manifest=Path(opt.manifest)
    m=json.loads(manifest.read_text())
    if m.get('schema')!='heptatrader.attribution-producer.v1' or m.get('instrument')!='EUR.USD':
        raise ValueError('NOT_ORIGINAL_FX_ATTRIBUTION')
    w=Watcher(m['journal_directory'])
    while w.clock()<opt.input_end_ms:
        try:
            events=w.poll()
            for e in events:
                if not opt.dry_run:
                    subprocess.run(['systemctl','--user','start','--no-block',UNIT],
                                   check=True,stdout=subprocess.DEVNULL,timeout=5)
                print(json.dumps({'at_ms':w.clock(),'request_id':e['request_id'],
                                  'dispatched_original_worker':not opt.dry_run}),flush=True)
        except Exception as exc:
            print(json.dumps({'state':'WATCH_ERROR','reason':str(exc)[:200]}),flush=True)
            raise
        if opt.once:break
        time.sleep(1)
if __name__=='__main__':main()
