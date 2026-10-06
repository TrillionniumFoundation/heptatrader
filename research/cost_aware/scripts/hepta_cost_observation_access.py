"""New-reset-cycle WATCH access for the read-only sampler only.

This file is not imported by the trading driver's launcher and cannot unmask a
service, remove a reset hold, provision a PAPER session, or issue an order.
Native Gateway capabilities and the native observation-only execution profile
remain the authority; these checks prevent accidental old-cycle reuse.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import stat
import subprocess
import threading
import time
from hepta_cost_policy import CostError, read_private_json, strict_loads

SCHEMA = 'heptatrader.observation-access.v1'

def _private_bytes(path, limit=65536):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_nlink!=1 or st.st_mode&0o077 or not 0<st.st_size<=limit:
            raise CostError('OBSERVATION_PRIVATE_FILE_UNSAFE')
        raw=b''
        while len(raw)<st.st_size:
            part=os.read(fd,st.st_size-len(raw))
            if not part:raise CostError('OBSERVATION_PRIVATE_FILE_SHORT_READ')
            raw+=part
        end=os.fstat(fd)
        if (st.st_ino,st.st_size,st.st_mtime_ns)!=(end.st_ino,end.st_size,end.st_mtime_ns):
            raise CostError('OBSERVATION_PRIVATE_FILE_CHANGED')
        return raw,st
    finally:os.close(fd)

class ObservationAccess:
    def __init__(self,path,instrument,state_dir,*,home=None,clock_ms=None):
        self.home=Path.home() if home is None else Path(home)
        self.path=Path(path)
        self.clock_ms=clock_ms or (lambda:int(time.time()*1000))
        self.lock=threading.RLock()
        self.instrument=instrument
        self.profile=read_private_json(self.path,max_bytes=65536)
        self.original_profile_digest=hashlib.sha256(_private_bytes(self.path)[0]).hexdigest()
        p=self.profile
        if (p.get('schema')!=SCHEMA or p.get('mode')!='WATCH' or p.get('allow_trade') is not False or
            p.get('execution_observation_only') is not True or p.get('instrument')!=instrument or
            instrument not in ('BTC.USD','EUR.USD') or p.get('operator_uid')!=os.geteuid() or
            not re.fullmatch(r'obs-[a-z0-9-]{8,48}',p.get('cycle_id',''))):
            raise CostError('OBSERVATION_PROFILE_SCOPE_INVALID')
        cycle=p['cycle_id'];base=self.home/'.config/heptatrader/observations'/cycle
        state=self.home/'.local/state/heptatrader/observations'/cycle/'cost'/instrument
        token_path=Path(p.get('token_file',''))
        lease_path=Path(p.get('lease_file',''))
        # Access-segment rotation may change the private token/lease leaf name
        # within the same reset cycle. The profile digest and token SHA bind the
        # exact files; do not hard-code one historical canonical leaf.
        scoped_files = (
            token_path.is_absolute() and lease_path.is_absolute() and
            token_path.parent == base and lease_path.parent == base and
            token_path != lease_path and
            token_path.name.startswith(instrument + '.') and
            token_path.name.endswith('.watch.token') and
            lease_path.name.startswith(instrument + '.') and
            lease_path.name.endswith('.watch-lease.json'))
        if (self.path!=base/(instrument+'.json') or Path(state_dir)!=state or
            p.get('state_dir')!=str(state) or not scoped_files):
            raise CostError('OBSERVATION_CYCLE_PATH_MISMATCH')
        runtime=Path('/run/user')/str(os.geteuid())/cycle
        if p.get('socket')!=str(runtime/'tools.sock') or p.get('supervisor_socket')!=str(runtime/'supervisor.sock'):
            raise CostError('OBSERVATION_SOCKET_SCOPE_MISMATCH')
        for key in ('heptactl','sessionctl'):
            binary=Path(p.get(key,''))
            root=self.home/'.local/share/heptatrader/observation/releases'
            if not binary.is_absolute() or not binary.is_relative_to(root) or binary.is_symlink():
                raise CostError('OBSERVATION_BINARY_PATH_INVALID')
            st=binary.stat()
            if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o022:
                raise CostError('OBSERVATION_BINARY_UNSAFE')
            if hashlib.sha256(binary.read_bytes()).hexdigest()!=p.get(key+'_sha256'):
                raise CostError('OBSERVATION_BINARY_HASH_CHANGED')
        self.endpoint=[p['heptactl'],'--socket',p['socket'],'--token-file',p['token_file']]
        self.context={key:p[key] for key in ('cycle_id','reset_sha256','account','agent_id','session_id','source_sha')}
        self.context.update(mode='WATCH',broker_mutation_authorized=False)
        self.check(allow_renew=False)

    def check(self,*,allow_renew=True):
        with self.lock:
            p=self.profile;now=self.clock_ms()
            if hashlib.sha256(_private_bytes(self.path)[0]).hexdigest()!=self.original_profile_digest:
                raise CostError('OBSERVATION_PROFILE_CHANGED_RESTART_REQUIRED')
            marker=self.home/'.local/state/heptatrader/RESET-STOPPED.json'
            raw,st=_private_bytes(marker);reset=strict_loads(raw)
            from datetime import datetime
            reset_ms=int(datetime.fromisoformat(reset['time'].replace('Z','+00:00')).timestamp()*1000)
            if (hashlib.sha256(raw).hexdigest()!=p.get('reset_sha256') or reset.get('account')!=p.get('account') or
                type(p.get('created_at_ms')) is not int or not reset_ms<p['created_at_ms']<=now or
                reset.get('state')!='factory_accounting_reset_auto_trading_stopped'):
                raise CostError('OBSERVATION_RESET_CYCLE_CHANGED')
            token,token_stat=_private_bytes(p['token_file'])
            if token_stat.st_mtime_ns//1000000<=reset_ms or hashlib.sha256(token).hexdigest()!=p.get('watch_token_sha256'):
                raise CostError('OBSERVATION_STALE_OR_CHANGED_TOKEN')
            lease=read_private_json(p['lease_file'],max_bytes=65536)
            lease_issued=lease.get('issued_at_ms')
            profile_created=p.get('created_at_ms')
            if (lease.get('mode')!='WATCH' or lease.get('cycle_id')!=p['cycle_id'] or
                lease.get('session_id')!=p['session_id'] or lease.get('token_sha256')!=p['watch_token_sha256'] or
                type(lease.get('lease_generation')) is not int or lease['lease_generation']<=0 or
                type(lease.get('expires_at_ms')) is not int or lease.get('accepted') is not True or
                type(lease_issued) is not int or type(profile_created) is not int or
                not reset_ms < lease_issued <= now < lease['expires_at_ms'] or
                not reset_ms < profile_created <= now or
                abs(profile_created-lease_issued)>60000 or
                lease['expires_at_ms']-lease_issued>86400000):
                raise CostError('OBSERVATION_WATCH_LEASE_UNVERIFIED_OR_EXPIRED')
            if allow_renew and lease['expires_at_ms']-now<120000:
                # WATCH renewal changes only OS session lifetime, never Broker
                # state or the configured no-trade capabilities. No resume path.
                result=subprocess.run([p['sessionctl'],'--socket',p['supervisor_socket'],'renew',
                    '--token-file',p['token_file'],'--generation',str(lease['lease_generation']),
                    '--ttl-sec','86400','--token-owner-uid',str(os.geteuid())],capture_output=True,text=True,timeout=10)
                reply=strict_loads(result.stdout) if result.stdout else {}
                if result.returncode!=0 or reply.get('accepted') is not True or type(reply.get('lease_generation')) is not int or reply['lease_generation']<=0:
                    raise CostError('OBSERVATION_WATCH_RENEWAL_UNCONFIRMED')
                lease.update(lease_generation=reply['lease_generation'],issued_at_ms=now,expires_at_ms=now+86400000)
                target=Path(p['lease_file']);temporary=target.with_name(target.name+'.next')
                fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                try:
                    data=(json.dumps(lease,sort_keys=True)+'\n').encode();offset=0
                    while offset<len(data):offset+=os.write(fd,data[offset:])
                    os.fsync(fd)
                finally:os.close(fd)
                os.replace(temporary,target)
                parent=os.open(target.parent,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(parent)
                finally:os.close(parent)
            return self.context
