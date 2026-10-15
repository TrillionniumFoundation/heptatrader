"""Lightweight request-trigger for the ORIGINAL Value worker; never makes predictions.

Watches the owner-local attribution journal's newly appended records. Historical
records are skipped on startup. The frozen worker remains responsible for all
admission, provenance, time, model and publication checks.
"""
from __future__ import annotations
import argparse,json,os,re,stat,subprocess,time
from pathlib import Path
UNIT='heptatrader-current100k-forward-predictor-trial3.service'
MAX_LINE=1024*1024
MAX_POLL=8*1024*1024
REQUEST_ID=re.compile(r"[0-9a-f]{32}")
RETRY_AFTER_MS=14000
MAX_ATTEMPTS=4
MAX_START_AGE_MS=55000

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
            try:
                # An append in progress must not make the next continuation
                # disappear when the watcher is started/restarted mid-record.
                n=min(st.st_size,MAX_LINE+1)
                os.lseek(fd,st.st_size-n,os.SEEK_SET)
                tail=os.read(fd,n).rsplit(b'\n',1)[-1]
                if len(tail)>MAX_LINE:raise ValueError('ATTRIBUTION_OVERSIZED_RECORD')
                self.files[p.name]=(st.st_dev,st.st_ino,st.st_size,tail)
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

    def recent_fresh(self,max_age_ms=MAX_START_AGE_MS):
        """Recover ONLY still-fresh original requests after a watcher restart."""
        now=self.clock();out=[]
        for p in self._files():
            fd,st=self._open(p)
            try:
                length=min(st.st_size,MAX_POLL)
                os.lseek(fd,st.st_size-length,os.SEEK_SET)
                data=os.read(fd,length)
                if length!=st.st_size:
                    # Partial first line belongs to a prior record, never parse it.
                    data=data.partition(b'\n')[2]
                for raw in data.split(b'\n'):
                    if not raw or len(raw)>MAX_LINE:continue
                    try:r=json.loads(raw)
                    except (ValueError,UnicodeError):continue
                    if r.get('kind')!='REQUEST' or not isinstance(r.get('data'),dict):continue
                    q=r['data'];rid=q.get('request_id')
                    ts=q.get('started_at_ms')
                    if (not isinstance(rid,str) or not REQUEST_ID.fullmatch(rid)
                        or type(ts) is not int or q.get('position_at_request')!=0
                        or not 0<=now-ts<=max_age_ms):continue
                    out.append({'request_id':rid,'started_at_ms':ts})
            finally:os.close(fd)
        return out

class BoundedRetry:
    """Retry original *worker start*, not prediction bytes or historical requests.

    Only the frozen worker may check the signed request, load the original model
    and publish a signed prediction before its unchanged 60-second deadline.
    """
    def __init__(self,root,plan_id,clock=lambda:int(time.time()*1000)):
        self.root=Path(root);self.plan_id=plan_id;self.clock=clock
        self.pending={}
    def offer(self,events):
        now=self.clock()
        for e in events:
            rid=e['request_id'];ts=e['started_at_ms']
            if (not isinstance(rid,str) or not REQUEST_ID.fullmatch(rid)
                    or type(ts) is not int or not 0<=now-ts<=MAX_START_AGE_MS):
                continue
            self.pending.setdefault(rid,{'started_at_ms':ts,'attempts':0,'next_at_ms':now})
    def tick(self,state_of_worker,trigger_worker):
        now=self.clock();actions=[];started_this_tick=False
        for rid,data in sorted(list(self.pending.items()),key=lambda kv:kv[1]['started_at_ms']):
            age=now-data['started_at_ms']
            folder=self.root/'plans'/self.plan_id/'predictions'/rid
            if (age<0 or age>MAX_START_AGE_MS or data['attempts']>=MAX_ATTEMPTS
                    or (folder/'commit.json').exists() or (folder/'failure.json').exists()):
                self.pending.pop(rid,None);continue
            if now<data['next_at_ms']:continue
            # A single original worker scans all fresh requests; avoid issuing
            # overlapping non-blocking starts before systemd updates its state.
            if started_this_tick:
                data['next_at_ms']=now+1000
                continue
            state=state_of_worker()
            if state in ('active','activating','deactivating','reloading'):
                data['next_at_ms']=now+1000
                continue
            try:
                trigger_worker()
                started_this_tick=True
                data['attempts']+=1
                data['next_at_ms']=now+RETRY_AFTER_MS
                actions.append({'request_id':rid,'attempt':data['attempts'],
                                'request_age_ms':age,'worker_start_requested':True})
            except (OSError,subprocess.SubprocessError) as exc:
                data['next_at_ms']=now+RETRY_AFTER_MS
                actions.append({'request_id':rid,'request_age_ms':age,
                                'worker_start_requested':False,
                                'reason':type(exc).__name__})
        return actions


def main():
    a=argparse.ArgumentParser()
    a.add_argument('--manifest',required=True)
    a.add_argument('--once',action='store_true')
    a.add_argument('--dry-run',action='store_true')
    a.add_argument('--input-end-ms',type=int,required=True)
    a.add_argument('--forward-root',required=True)
    opt=a.parse_args();os.umask(0o077)
    if opt.input_end_ms<=0:raise ValueError('INVALID_FIXED_END')
    manifest=Path(opt.manifest)
    m=json.loads(manifest.read_text())
    if m.get('schema')!='heptatrader.attribution-producer.v1' or m.get('instrument')!='EUR.USD':
        raise ValueError('NOT_ORIGINAL_FX_ATTRIBUTION')
    root=Path(opt.forward_root)
    plans=list(root.glob('plans/*/plan.json'))
    if len(plans)!=1:raise ValueError('ORIGINAL_SINGLE_PLAN_REQUIRED')
    plan=json.loads(plans[0].read_text())
    if (plan['input_end_ms']!=opt.input_end_ms
            or plan['producer_registration']['manifest']!=str(manifest)
            or plan['producer_registration']['sha256']!=__import__('hashlib').sha256(manifest.read_bytes()).hexdigest()):
        raise ValueError('ORIGINAL_PLAN_OR_PRODUCER_SCOPE')
    w=Watcher(m['journal_directory'])
    q=BoundedRetry(root,plan['plan_id'],w.clock)
    q.offer(w.recent_fresh())
    def worker_state():
        if opt.dry_run:return 'inactive'
        p=subprocess.run(['systemctl','--user','show',UNIT,'-p','ActiveState','--value'],
                         capture_output=True,text=True,timeout=5,check=True)
        return p.stdout.strip()
    def start_original():
        if opt.dry_run:return
        subprocess.run(['systemctl','--user','start','--no-block',UNIT],
                       check=True,stdout=subprocess.DEVNULL,timeout=5)
    while w.clock()<opt.input_end_ms:
        try:
            q.offer(w.poll())
            actions=q.tick(worker_state,start_original)
            for action in actions:
                if opt.dry_run:
                    action={**action,'worker_start_requested':False,'dry_run':True}
                print(json.dumps({'at_ms':w.clock(),**action}),flush=True)
        except Exception as exc:
            print(json.dumps({'state':'WATCH_ERROR','reason':str(exc)[:200]}),flush=True)
            raise
        if opt.once:break
        time.sleep(1)
if __name__=='__main__':main()
