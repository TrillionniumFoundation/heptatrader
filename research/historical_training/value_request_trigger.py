"""Lightweight request-trigger for the ORIGINAL Value worker; never makes predictions.

Watches the owner-local original attribution journal. On startup only still-
fresh requests or requests within a bounded terminal-only recovery horizon
are considered; older history is never replayed. The frozen worker alone
checks original deadlines and publishes any signed prediction or missed receipt.
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
ORIGINAL_DEADLINE_MS=60000
MAX_TERMINAL_AGE_MS=180000
MAX_TERMINAL_STARTS=2
TERMINAL_RETRY_AFTER_MS=15000

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
                        or not (start<=written<=now)
                        or not (0<=now-start<=MAX_TERMINAL_AGE_MS)
                        or rid in self.last_ids):continue
                    self.last_ids[rid]=now
                    event={'request_id':rid,'started_at_ms':start}
                    if now-start>MAX_START_AGE_MS:
                        # Delayed original ingestion may only CLOSE a missed
                        # request through the frozen worker, never backdate it.
                        event['closure_only']=True
                    events.append(event)
            finally:os.close(fd)
        # Avoid unbounded memory; only a three-minute *terminal* horizon.
        self.last_ids={k:v for k,v in self.last_ids.items() if v>=now-MAX_TERMINAL_AGE_MS}
        return events

    def recent_expired(self,max_age_ms=MAX_TERMINAL_AGE_MS):
        """Recover only recent terminal-only original flat request candidates.

        Requests aged 55–60 seconds remain WAITING until strictly past
        the original 60-second deadline. They can never be offered as a
        new prediction. These are post-deadline *failure terminalization*
        candidates, not
        inputs for new prediction. The frozen worker is the sole writer of
        their original immutable failure receipt. No old signed result is
        fabricated, and the search has a strict three-minute horizon.
        """
        now=self.clock();out=[]
        for p in self._files():
            fd,st=self._open(p)
            try:
                length=min(st.st_size,MAX_POLL)
                os.lseek(fd,st.st_size-length,os.SEEK_SET)
                raw=os.read(fd,length)
                if length!=st.st_size:
                    raw=raw.partition(b'\n')[2]
                for line in raw.split(b'\n'):
                    if not line or len(line)>MAX_LINE:continue
                    try:e=json.loads(line)
                    except (ValueError,UnicodeError):continue
                    if e.get('kind')!='REQUEST' or not isinstance(e.get('data'),dict):
                        continue
                    d=e['data'];rid=d.get('request_id')
                    ts=d.get('started_at_ms');written=e.get('written_at_ms')
                    if (not isinstance(rid,str) or not REQUEST_ID.fullmatch(rid)
                        or type(ts) is not int or type(written) is not int
                        or d.get('position_at_request')!=0
                        or not ts<=written<=now
                        or not MAX_START_AGE_MS<now-ts<=max_age_ms):
                        continue
                    out.append({'request_id':rid,'started_at_ms':ts,
                                'closure_only':True})
            finally:os.close(fd)
        return out

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
            closure_only=e.get('closure_only') is True
            if not isinstance(rid,str) or not REQUEST_ID.fullmatch(rid) or type(ts) is not int:
                continue
            age=now-ts
            if not (MAX_START_AGE_MS<age<=MAX_TERMINAL_AGE_MS
                    if closure_only else 0<=age<=MAX_START_AGE_MS):
                continue
            self.pending.setdefault(rid,{'started_at_ms':ts,'attempts':0,
                'next_at_ms':now,'closure_only':closure_only,
                'terminal_attempts':0,
                'terminal_next_at_ms':max(now,ts+ORIGINAL_DEADLINE_MS+1)})
    def tick(self,state_of_worker,trigger_worker):
        now=self.clock();actions=[];started_this_tick=False
        # New eligible requests first, followed by expired terminal-only work.
        # Both use the SINGLE original systemd worker and original source.
        ordered=sorted(list(self.pending.items()),
            key=lambda kv:(now-kv[1]['started_at_ms']>ORIGINAL_DEADLINE_MS,
                           kv[1]['started_at_ms']))
        for rid,data in ordered:
            age=now-data['started_at_ms']
            folder=self.root/'plans'/self.plan_id/'predictions'/rid
            if (age<0 or age>MAX_TERMINAL_AGE_MS
                    or (folder/'commit.json').exists()
                    or (folder/'failure.json').exists()):
                self.pending.pop(rid,None);continue
            expired=age>ORIGINAL_DEADLINE_MS
            if expired:
                # Strict >60s: the frozen worker distinguishes old requests
                # with now-started_at_ms >60000, NOT >=60000. Never trigger
                # an expired closure at the equality boundary or load a
                # GPU because the timestamp was precisely 60 seconds.
                # This is ONLY a bounded request to close its own missed input.
                if (data['terminal_attempts']>=MAX_TERMINAL_STARTS
                        or now<data['terminal_next_at_ms']):
                    continue
            else:
                # Keep earlier bounded prediction retry unchanged; retain
                # the request after 55s for terminal followthrough at >=60s.
                if (data['closure_only'] or age>MAX_START_AGE_MS
                        or data['attempts']>=MAX_ATTEMPTS
                        or now<data['next_at_ms']):
                    continue
            if started_this_tick:
                if expired:data['terminal_next_at_ms']=now+1000
                else:data['next_at_ms']=now+1000
                continue
            state=state_of_worker()
            if state in ('active','activating','deactivating','reloading'):
                if expired:data['terminal_next_at_ms']=now+1000
                else:data['next_at_ms']=now+1000
                continue
            try:
                trigger_worker()
                started_this_tick=True
                if expired:
                    data['terminal_attempts']+=1
                    data['terminal_next_at_ms']=now+TERMINAL_RETRY_AFTER_MS
                    actions.append({'request_id':rid,'attempt':data['terminal_attempts'],
                        'request_age_ms':age,'purpose':'EXPIRED_ORIGINAL_TERMINAL_FOLLOWTHROUGH',
                        'prediction_backdated':False,'worker_start_requested':True})
                else:
                    data['attempts']+=1
                    data['next_at_ms']=now+RETRY_AFTER_MS
                    actions.append({'request_id':rid,'attempt':data['attempts'],
                                    'request_age_ms':age,'worker_start_requested':True})
            except (OSError,subprocess.SubprocessError) as exc:
                if expired:
                    data['terminal_attempts']+=1
                    data['terminal_next_at_ms']=now+TERMINAL_RETRY_AFTER_MS
                else:data['next_at_ms']=now+RETRY_AFTER_MS
                action={'request_id':rid,'request_age_ms':age,
                        'worker_start_requested':False,'reason':type(exc).__name__}
                if expired:
                    action.update(purpose='EXPIRED_ORIGINAL_TERMINAL_FOLLOWTHROUGH',
                                  prediction_backdated=False)
                actions.append(action)
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
    q.offer(w.recent_expired())
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
