"""External admission wrapper for the pinned resident research supervisors.
No model/trading code is replaced. Original slot dates, outcome rules, source
hashes and scheduler state remain authoritative. A global nonblocking lease
serializes NEW heavyweight training/prediction; existing children are not killed.
"""
from __future__ import annotations
import argparse,fcntl,hashlib,json,math,os,signal,stat,sys,time
from pathlib import Path
from .common import EvidenceError,read,sha,atomic,write_once,digest

GIB=1024**3
SOURCE_RULE={'input_probes':24,'minimum_usable':20,'maximum_missing':4,'source_ttl_ms':5000,
             'endpoint_tolerance_ms':5000,'path_gap_max_ms':30000}
RESOURCE_RULE={'profile':'ROLE_MEMORY_PSI_V2','min_available_bytes':8*GIB,
               'registration_min_available_bytes':2*GIB,'swap_free_policy':'DIAGNOSTIC_ONLY',
               'max_full_psi_avg10':1.,'max_full_psi_avg60':.1}
ATTRIBUTION_LIMITS={'max_pids':512,'max_proc_entries':2048,'max_cgroups':8,
    'max_directories':640,'max_files':1200,'max_bytes':512*1024,
    'max_file_bytes':16384,'max_elapsed_ms':50,'cache_seconds':30}
PRIORITY_CGROUP_UNITS=('heptatrader-laya-trainer.service','heptatrader-laya-evaluator.service',
                       'heptatrader-learning-source-health.service')
RESOURCE_DENIALS={'HOST_AVAILABLE_MEMORY_BELOW_8GIB',
    'HOST_AVAILABLE_MEMORY_BELOW_REGISTRATION_RESERVE','HOST_FULL_MEMORY_PSI_PRESSURE',
    'OTHER_RESEARCH_WORKER_ACTIVE'}


def collect_resource_attribution(*,proc_root=Path('/proc'),cgroup_root=Path('/sys/fs/cgroup'),
                                 limits=None,clock=time.monotonic,observer_pid=None):
    """Bounded kernel telemetry, never a directory-size scan or admission rule.

    The observer and known research units precede RSS-ranked cgroups: a small
    working set can still be swap-heavy or repeatedly hit memory.max. Pressure
    describes stalls experienced there; counters are cumulative, not rates.
    Only fixed proc/cgroup files are read, without argv, environment or fd paths.
    """
    limits=dict(ATTRIBUTION_LIMITS if limits is None else limits)
    started=clock();deadline=started+limits['max_elapsed_ms']/1000
    scan={'proc_entries':0,'pids_attempted':0,'pids_observed':0,'directories_opened':0,
          'files_attempted':0,'bytes_read':0,'read_errors':0,'limits_reached':[]}
    result={'at_ms':int(time.time()*1000),'diagnostic_only':True,
        'causality':'NOT_ESTABLISHED','counter_semantics':'CUMULATIVE_NOT_RATES',
        'snapshot_atomic':False,
        'ranking':'OBSERVER_AND_RESEARCH_UNITS_THEN_RSS_NOT_COMPLETE_HOST_CENSUS','limits':limits,
        'scan':scan,'processes':[],'cgroups':[]}
    class BudgetReached(Exception):pass
    def stopped(reason):
        if reason not in scan['limits_reached']:scan['limits_reached'].append(reason)
        raise BudgetReached(reason)
    def check():
        if clock()>=deadline:stopped('TIME_BUDGET')
    def directory(name,fd=None):
        check()
        if scan['directories_opened']>=limits['max_directories']:stopped('DIRECTORY_BUDGET')
        scan['directories_opened']+=1
        return os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW,dir_fd=fd)
    def read_file(fd,name):
        check()
        if scan['files_attempted']>=limits['max_files']:stopped('FILE_BUDGET')
        remaining=limits['max_bytes']-scan['bytes_read']
        if remaining<=0:stopped('BYTE_BUDGET')
        scan['files_attempted']+=1;cap=min(limits['max_file_bytes'],remaining)
        item=os.open(name,os.O_RDONLY|os.O_NONBLOCK|os.O_CLOEXEC|os.O_NOFOLLOW,dir_fd=fd)
        try:raw=os.read(item,cap)
        finally:os.close(item)
        scan['bytes_read']+=len(raw)
        if len(raw)==cap:
            if cap==remaining:stopped('BYTE_BUDGET')
            raise ValueError('DIAGNOSTIC_FILE_TOO_LARGE')
        return raw.decode('utf-8')
    def optional(fd,name,parse):
        try:return parse(read_file(fd,name))
        except (OSError,ValueError,IndexError,KeyError,StopIteration):
            scan['read_errors']+=1;return None
    def integers(text,keys):
        values={}
        for line in text.splitlines():
            fields=line.split()
            if len(fields)==2 and fields[0] in keys and fields[1].isdecimal():
                values[fields[0]]=int(fields[1])
        return values
    def psi(text):
        return {**parse_full_psi(text),'full_total_us':int(next(
            part[6:] for line in text.splitlines() if line.startswith('full ')
            for part in line.split() if part.startswith('total=')))}
    def memory(text):
        keys={'MemAvailable','SwapFree','Slab','SReclaimable','SUnreclaim','Dirty','Writeback'}
        return {fields[0].rstrip(':'):int(fields[1])*1024 for line in text.splitlines()
            if len(fields:=line.split())==3 and fields[0].rstrip(':') in keys
            and fields[1].isdecimal() and fields[2]=='kB'}
    def io(text):
        values={key:0 for key in ('rbytes','wbytes','rios','wios')}
        for line in text.splitlines():
            for part in line.split()[1:]:
                key,sep,value=part.partition('=')
                if sep and key in values and value.isdecimal():values[key]+=int(value)
        return values
    def unified_group(text):
        paths=[line[3:] for line in text.splitlines() if line.startswith('0::')]
        if len(paths)!=1:raise ValueError('CGROUP_V2_UNAVAILABLE')
        path=paths[0];parts=path.split('/')[1:]
        if (not path.startswith('/') or len(path)>512 or len(parts)>12
            or any(part in ('.','..') for part in parts)
            or any(ord(c)<32 for c in path)):raise ValueError('CGROUP_PATH_INVALID')
        # The nearest service/scope is the useful workload, including its children.
        last=max((i for i,p in enumerate(parts) if p.endswith(('.service','.scope'))),default=len(parts)-1)
        return '/'.join(parts[:last+1]).strip('/')
    proc_fd=None;cg_fd=None;processes=[];groups={}
    def group_row(path):
        return groups.setdefault(path,{'path':path,'observed_processes':0,'observed_process_rss_bytes':0})
    def observe_process(pid):
        if scan['pids_attempted']>=limits['max_pids']:stopped('PID_BUDGET')
        scan['pids_attempted']+=1;pid_fd=None
        try:
            pid_fd=directory(str(pid),proc_fd)
            raw=read_file(pid_fd,'stat');left,sep,right=raw.rpartition(')');fields=right.split()
            if not sep or int(left.split(' ',1)[0])!=pid or len(fields)<22:
                raise ValueError('PROCESS_STAT_INVALID')
            row={'pid':pid,'name':left.split('(',1)[1][:64],
                 'start_ticks':int(fields[19]),'cpu_ticks':int(fields[11])+int(fields[12]),
                 'rss_bytes':max(0,int(fields[21]))*os.sysconf('SC_PAGE_SIZE')}
            row['cgroup']=optional(pid_fd,'cgroup',unified_group)
            processes.append(row);scan['pids_observed']+=1
            if row['cgroup'] is not None:
                group=group_row(row['cgroup'])
                group['observed_processes']+=1;group['observed_process_rss_bytes']+=row['rss_bytes']
            return row['cgroup']
        except (OSError,ValueError,IndexError):scan['read_errors']+=1
        finally:
            if pid_fd is not None:os.close(pid_fd)
    try:
        proc_fd=directory(proc_root)
        result['host_memory_bytes']=optional(proc_fd,'meminfo',memory)
        result['host_vmstat']=optional(proc_fd,'vmstat',lambda raw:integers(raw,
            {'pswpin','pswpout','pgmajfault','pgscan_kswapd','pgscan_direct','pgpgin','pgpgout'}))
        observer_pid=os.getpid() if observer_pid is None else observer_pid
        own_group=observe_process(observer_pid)
        pids=[]
        with os.scandir(proc_fd) as entries:
            for entry in entries:
                check();scan['proc_entries']+=1
                if entry.name.isdecimal() and int(entry.name)!=observer_pid:pids.append(int(entry.name))
                if scan['proc_entries']>=limits['max_proc_entries']:
                    scan['limits_reached'].append('PROC_ENTRY_BUDGET');break
        remaining_pids=max(0,limits['max_pids']-scan['pids_attempted'])
        if len(pids)>remaining_pids:scan['limits_reached'].append('PID_BUDGET')
        # Inspect newer PIDs first when partial; reserve time for cgroup details.
        for pid in sorted(pids,reverse=True)[:remaining_pids]:
            if clock()>=started+limits['max_elapsed_ms']*.0006:
                scan['limits_reached'].append('PROCESS_TIME_SLICE');break
            observe_process(pid)
        result['processes']=sorted(processes,key=lambda row:(row['rss_bytes'],row['pid']),reverse=True)[:8]
        cg_fd=directory(cgroup_root)
        priority=[]
        if own_group is not None:
            group_row(own_group)['selection_reason']='OBSERVER_CGROUP';priority.append(own_group)
            parts=own_group.split('/');parent=own_group.rpartition('/')[0]
            manager=next(('/'.join(parts[:i+1]) for i,p in enumerate(parts)
                          if p.startswith('user@') and p.endswith('.service')),parent)
            for unit in PRIORITY_CGROUP_UNITS:
                matches=[path for path in groups if path.rsplit('/',1)[-1]==unit
                         and (not manager or path.startswith(manager+'/'))]
                # Fixed sibling paths also cover services omitted by the PID
                # budget. They are not discovered by traversing an app tree.
                if not matches and parts[-1] in PRIORITY_CGROUP_UNITS:
                    matches=[(parent+'/' if parent else '')+unit]
                for path in matches:
                    if path not in priority:
                        group_row(path)['selection_reason']='RELATED_RESEARCH_UNIT';priority.append(path)
        ordered=[groups[path] for path in priority]
        ordered.extend(sorted((group for path,group in groups.items() if path not in priority),
                              key=lambda row:row['observed_process_rss_bytes'],reverse=True))
        if len(groups)>limits['max_cgroups']:scan['limits_reached'].append('CGROUP_BUDGET')
        for group in ordered[:limits['max_cgroups']]:
            group.setdefault('selection_reason','OBSERVED_PROCESS_RSS')
            current=None
            try:
                current=directory('.',cg_fd)
                for part in filter(None,group['path'].split('/')):
                    child=directory(part,current);os.close(current);current=child
                group['memory_current_bytes']=optional(current,'memory.current',lambda raw:int(raw.strip()))
                group['memory_swap_current_bytes']=optional(current,'memory.swap.current',lambda raw:int(raw.strip()))
                group['memory_max']=optional(current,'memory.max',lambda raw:raw.strip())
                group['memory_stat']=optional(current,'memory.stat',lambda raw:integers(raw,
                    {'anon','file','kernel','slab','slab_reclaimable','slab_unreclaimable'}))
                group['memory_events']=optional(current,'memory.events',lambda raw:integers(raw,
                    {'low','high','max','oom','oom_kill','oom_group_kill'}))
                group['memory_full_psi']=optional(current,'memory.pressure',psi)
                group['cpu_stat']=optional(current,'cpu.stat',lambda raw:integers(raw,
                    {'usage_usec','user_usec','system_usec','nr_throttled','throttled_usec'}))
                group['io_stat']=optional(current,'io.stat',io)
            except (OSError,ValueError) as exc:
                scan['read_errors']+=1;group['read_error_class']=type(exc).__name__
            finally:
                result['cgroups'].append(group)
                if current is not None:os.close(current)
    except BudgetReached:pass
    except (OSError,ValueError,IndexError,StopIteration):scan['read_errors']+=1
    finally:
        if proc_fd is not None:os.close(proc_fd)
        if cg_fd is not None:os.close(cg_fd)
    result['processes']=sorted(processes,key=lambda row:(row['rss_bytes'],row['pid']),reverse=True)[:8]
    result['elapsed_ms']=round((clock()-started)*1000,3)
    result['partial']=bool(scan['limits_reached'] or scan['read_errors'])
    return result


def parse_meminfo(text):
    out={}
    for line in text.splitlines():
        key,sep,value=line.partition(':')
        if key not in ('MemTotal','MemAvailable','SwapTotal','SwapFree'):continue
        fields=value.split()
        if not sep or len(fields)!=2 or fields[1]!='kB' or not fields[0].isdecimal() or key in out:
            raise EvidenceError('MEMINFO_INVALID')
        out[key]=int(fields[0])*1024
    if set(out)!={'MemTotal','MemAvailable','SwapTotal','SwapFree'} or out['MemAvailable']>out['MemTotal'] or out['SwapFree']>out['SwapTotal']:
        raise EvidenceError('MEMINFO_SCOPE')
    return out


def parse_full_psi(text):
    lines=[s for s in text.splitlines() if s.startswith('full ')]
    if len(lines)!=1:raise EvidenceError('FULL_PSI_MISSING')
    pairs={}
    for item in lines[0].split()[1:]:
        key,value=item.split('=',1)
        if key in pairs:raise EvidenceError('DUPLICATE_PSI_FIELD')
        pairs[key]=value
    out={}
    for key in ('avg10','avg60'):
        v=float(pairs[key])
        if not math.isfinite(v) or not 0<=v<=100:raise EvidenceError('PSI_NONFINITE_OR_RANGE')
        out[key]=v
    return out


def assess_resources(memory,pressure,now_ms,manual_hold,*,role='trainer'):
    if role not in ('trainer','evaluator','value-predictor','owner-advisory','forward_registration'):
        raise EvidenceError('RESOURCE_ADMISSION_ROLE_REQUIRED')
    # Keep the original heavy-worker RAM floor. Cold pages occupying swap are
    # not evidence of current pressure and cannot starve otherwise safe work.
    registration=role=='forward_registration'
    minimum=RESOURCE_RULE['registration_min_available_bytes' if registration else 'min_available_bytes']
    reasons=[]
    if type(manual_hold) is not bool or manual_hold:reasons.append('OPERATOR_RESOURCE_HOLD')
    if memory['MemAvailable']<minimum:
        reasons.append('HOST_AVAILABLE_MEMORY_BELOW_REGISTRATION_RESERVE' if registration
                       else 'HOST_AVAILABLE_MEMORY_BELOW_8GIB')
    if (pressure['avg10']>RESOURCE_RULE['max_full_psi_avg10']
            or pressure['avg60']>RESOURCE_RULE['max_full_psi_avg60']):
        reasons.append('HOST_FULL_MEMORY_PSI_PRESSURE')
    return {'schema':'heptatrader.resident-resource-admission.v1','at_ms':now_ms,'admit_new_worker':not reasons,
        'reasons':list(dict.fromkeys(reasons)),'memory_bytes':memory,'full_memory_psi':pressure,
        'resource_profile':RESOURCE_RULE['profile'],'resource_role':role,
        'minimum_available_bytes':minimum,'swap_free_policy':'DIAGNOSTIC_ONLY',
        'max_new_heavy_workers_across_roles':1,'existing_workers_interrupted':False,
        'automatic_trading':False,'statistical_rule_changed':False}


def assess(memory,pressure,source,now_ms,period_sha256,manual_hold,*,role='trainer'):
    report=assess_resources(memory,pressure,now_ms,manual_hold,role=role)
    reasons=report['reasons']
    try:
        fields=('at_ms','latest_source_quote_ms','usable','unusable')
        if any(type(source[k]) is not int for k in fields):raise ValueError('typed source')
        if source.get('schema')!='heptatrader.quote-readiness-status.v1' or source.get('period_sha256')!=period_sha256:
            reasons.append('SOURCE_PERIOD_OR_SCHEMA_UNBOUND')
        if any(source.get('rule',{}).get(k)!=v for k,v in SOURCE_RULE.items()):reasons.append('SOURCE_RULE_CHANGED')
        if not 0<=now_ms-source['at_ms']<=120000:reasons.append('SOURCE_STATUS_STALE_OR_FUTURE')
        if not 0<=now_ms-source['latest_source_quote_ms']<=5000:reasons.append('LATEST_SOURCE_QUOTE_NOT_FRESH')
        if (not 20<=source['usable']<=24 or not 0<=source['unusable']<=4
            or source['usable']+source['unusable']!=24 or source.get('state')!='READY_FOR_REVIEW_NOT_AUTHORIZATION'):
            reasons.append('SOURCE_CONTINUITY_NOT_READY')
    except (KeyError,TypeError,ValueError):reasons.append('SOURCE_READINESS_INVALID')
    report['reasons']=list(dict.fromkeys(reasons))
    report['admit_new_worker']=not report['reasons']
    return report



def preflight_source_health_hold(source):
    """Expose already-denied probe quality without falsely acquiring live proof.

    Only the exact original source-health HOLD can take this optimization.
    READY continues to require an independently verified fresh original
    source extension. The returned data is DIAGNOSTIC, never authorization.
    """
    if source.get('state') != 'HOLD_NEW_FUSION_WINDOW':
        return None
    reasons=source.get('reasons')
    if (not isinstance(reasons,list) or len(reasons)>24
        or any(type(x) is not str or not 3<=len(x)<=90
               or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_' for c in x)
               for x in reasons)):
        raise EvidenceError('SOURCE_HEALTH_HOLD_REASON_FORMAT')
    def integer_or_null(value):
        return value if type(value) is int else None
    return {
        'schema':'heptatrader.readiness-held-before-live-refresh.v1',
        'observed_source_state':'HOLD_NEW_FUSION_WINDOW',
        'cached_probe_status_at_ms':integer_or_null(source.get('at_ms')),
        'cached_probe_usable':integer_or_null(source.get('usable')),
        'cached_probe_unusable':integer_or_null(source.get('unusable')),
        'reported_source_reasons':list(reasons),
        'fresh_original_live_source_proven':False,
        'live_quote_relabelled':False,
        'broker_queries_sent':0,
        'orders_sent':0,
    }


class Lease:
    def __init__(self,path):self.path=Path(path);self.fd=None
    def acquire(self):
        if self.fd is not None:return True
        self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        fd=os.open(self.path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        info=os.fstat(fd)
        if info.st_uid!=os.geteuid() or not stat.S_ISREG(info.st_mode) or info.st_mode&0o077:
            os.close(fd);raise EvidenceError('LEASE_OWNER_OR_MODE')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:os.close(fd);return False
        self.fd=fd;return True
    def release(self):
        if self.fd is not None:os.close(self.fd);self.fd=None


class Gate:
    def __init__(self,config,role):
        if role not in ('trainer','evaluator','value-predictor','owner-advisory'):raise EvidenceError('RESEARCH_ROLE_REQUIRED')
        self.c=config;self.role=role;self.root=Path(config['state_root']);self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        # Explicit read-only model consumers obey the existing evaluator's
        # original-source and Value priority policy, with their own receipts.
        self.priority_role='evaluator' if role=='owner-advisory' else role
        self.lease=Lease(self.root/'heavy-worker.lock');self.draining=False
        self.last_admission=None
        if self.c.get('value_priority') is not None and self.priority_role in ('trainer','evaluator'):
            from .value_priority_admission import verify_policy
            verify_policy(self.c['value_priority'])
    def attach_resource_attribution(self,report):
        """Reuse a bounded snapshot across denial retries; never affect admission."""
        if not RESOURCE_DENIALS.intersection(report.get('reasons',[])):
            return report
        tick=time.monotonic();previous=getattr(self,'_resource_attribution',None)
        if previous is None or not 0<=tick-previous[0]<ATTRIBUTION_LIMITS['cache_seconds']:
            try:snapshot=collect_resource_attribution()
            except Exception as exc:
                snapshot={'at_ms':int(time.time()*1000),'diagnostic_only':True,
                    'partial':True,'error_class':type(exc).__name__,'causality':'NOT_ESTABLISHED'}
            previous=(tick,snapshot);self._resource_attribution=previous
        report['workload_attribution']={**previous[1],
            'sample_age_ms':max(0,int((tick-previous[0])*1000)),
            'reused_sample':tick!=previous[0]}
        return report
    def record_native_exit(self,returncode,subject):
        """Stop new heavy work after a native crash; preserve CPU closeout.

        The ordinary worker failure remains authoritative for its job/slot.
        This receipt only owns resource admission and never rewrites a trial.
        """
        fatal=(-signal.SIGSEGV,-signal.SIGBUS,-signal.SIGABRT,-signal.SIGILL)
        if type(returncode) is not int or returncode not in fatal:return None
        identity={'role':self.role,'subject':subject,'exit_code':returncode}
        path=self.root/'native-failures'/(digest(identity)+'.json')
        try:
            if path.exists():
                receipt=read(path)
                if any(receipt.get(k)!=v for k,v in identity.items()):
                    raise EvidenceError('NATIVE_FAILURE_IDENTITY_CHANGED')
            else:
                receipt={'schema':'heptatrader.resident-native-failure.v1',**identity,
                    'at_ms':int(time.time()*1000),'signal':signal.Signals(-returncode).name,
                    'automatic_trading':False,'original_trial_assessment_changed':False}
                write_once(path,receipt)
            manual=self.c.get('manual_hold_path')
            if not isinstance(manual,str) or not Path(manual).is_absolute():
                raise EvidenceError('NATIVE_FAILURE_HOLD_PATH_REQUIRED')
            hold={'schema':'heptatrader.operator-diagnostic-hold.v1',
                'created_ms':receipt['at_ms'],'owner':'heptatrader-resident-resource-guard',
                'reason':'NATIVE_WORKER_FATAL_SIGNAL',
                'scope':'NEW_HEAVY_GPU_WORK_ONLY_ORIGINAL_CPU_CLOSEOUT_CONTINUES',
                'evidence':{'receipt_path':str(path),'receipt_sha256':sha(path),
                            'role':self.role,'signal':receipt['signal'],'subject':subject},
                'ordinary_training_retry_is_not_stability_verification':True,
                'release_conditions':'Controlled diagnosis and finite stability acceptance; remove only this exact marker.'}
            # Any existing marker, including a concurrently created one, already
            # blocks admission. Never overwrite another owner's hold.
            if not os.path.lexists(manual):
                try:write_once(manual,hold)
                except (OSError,EvidenceError):
                    if not os.path.lexists(manual):raise
            for directory in {path.parent,Path(manual).parent}:
                fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
                try:os.fsync(fd)
                finally:os.close(fd)
            return receipt
        except Exception:
            # If durable fault admission cannot be recorded, this controller
            # drains instead of retrying the crashed workload.
            self.draining=True
            raise

    def status_diagnostics(self):
        """Cheap current blockers even when a prior calendar/queue gate returned.

        This does not refresh sources, take a lease or grant worker admission.
        A persistent hold's age never makes its current presence historical.
        """
        now=int(time.time()*1000);manual=self.c.get('manual_hold_path')
        present=manual is not None and os.path.lexists(manual)
        hold={'source':manual,'checked_at_ms':now,'present':present,
              'scope':'NEW_HEAVY_GPU_WORK_ONLY','has_expiration':False}
        if present:
            try:
                # A symlink or malformed marker still holds; do not follow it
                # merely to display a reason from an arbitrary target.
                fd=os.open(manual,os.O_RDONLY|os.O_NONBLOCK|os.O_CLOEXEC|os.O_NOFOLLOW)
                with os.fdopen(fd,'rb') as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise ValueError('HOLD_NOT_REGULAR')
                    raw=stream.read(16385)
                if len(raw)>16384:raise ValueError('HOLD_TOO_LARGE')
                value=json.loads(raw)
                if not isinstance(value,dict):raise ValueError('HOLD_NOT_OBJECT')
                hold.update({key:value[key] for key in ('reason','created_at_ms','created_ms','resume_when') if key in value})
            except (OSError,ValueError,TypeError) as exc:
                hold['reason_unavailable']=type(exc).__name__
        try:
            resources=assess_resources(parse_meminfo(Path('/proc/meminfo').read_text()),
                parse_full_psi(Path('/proc/pressure/memory').read_text()),now,present,role=self.role)
            resources.pop('admit_new_worker',None)
        except (OSError,ValueError,TypeError,KeyError) as exc:
            resources={'at_ms':now,'reasons':['RESOURCE_DIAGNOSTICS_UNAVAILABLE'],
                       'error_class':type(exc).__name__}
            if present:resources['reasons'].append('OPERATOR_RESOURCE_HOLD')
        reasons=list(resources['reasons'])
        if self.draining:reasons.append('SUPERVISOR_DRAIN_REQUESTED')
        self.attach_resource_attribution(resources)
        return {'checked_at_ms':now,'manual_hold':hold,'resource_diagnostics':resources,
                'current_resource_blockers':list(dict.fromkeys(reasons)),
                'diagnostic_only':True,'worker_launch_authority':False,
                'source_check':'NOT_PERFORMED_FOR_STATUS','historical_cpu_closeout_blocked':False}
    def _priority_negative_preflight(self):
        # Read the SHA256-pinned original Value producer BEFORE an expensive
        # source-health refresh, but use this shortcut ONLY to refuse new
        # background work. Every positive admission still requires the full
        # original memory/PSI/source check, the one global flock and a second
        # source+priority check after taking that flock.
        manual=self.c.get('manual_hold_path')
        if (self.priority_role not in ('trainer','evaluator') or self.c.get('value_priority') is None or self.draining or
                (manual is not None and os.path.lexists(manual))):
            return None
        try:
            from .value_priority_admission import decision
            held,reason,proof=decision(
                self.c['value_priority'],self.priority_role,int(time.time()*1000))
        except (OSError,ValueError,KeyError,TypeError) as exc:
            held=True;reason='VALUE_PRIORITY_UNVERIFIED_SOURCE'
            proof={'source':'UNVERIFIED','error_class':type(exc).__name__,
                   'no_order_authority':True,'existing_workers_interrupted':False}
        if not held:
            return None
        report={'schema':'heptatrader.resident-resource-admission.v1',
                'at_ms':int(time.time()*1000),'role':self.role,
                'admit_new_worker':False,'reasons':[reason],
                'value_priority_evidence':proof,
                'original_resource_and_quote_refresh_skipped_for_denial':True,
                'original_resource_admission_not_claimed':True,
                'automatic_trading':False,'existing_workers_interrupted':False,
                'broker_orders_sent':0}
        self.last_admission=report
        atomic(self.root/(self.role+'-admission.json'),report)
        return report
    def _prioritize_background(self,report):
        if (self.priority_role not in ('trainer','evaluator') or not report['admit_new_worker'] or self.c.get('value_priority') is None):
            self.last_admission=report
            return report
        try:
            from .value_priority_admission import decision
            held,reason,proof=decision(self.c['value_priority'],self.priority_role,int(time.time()*1000))
        except (OSError,ValueError,KeyError,TypeError) as exc:
            held=True;reason='VALUE_PRIORITY_UNVERIFIED_SOURCE'
            proof={'source':'UNVERIFIED','error_class':type(exc).__name__,
                   'no_order_authority':True,'existing_workers_interrupted':False}
        if held:
            report['admit_new_worker']=False
            report['reasons'].append(reason)
        report['value_priority_evidence']=proof
        self.last_admission=report
        atomic(self.root/(self.role+'-admission.json'),report)
        return report
    def check(self, *, forward_registration=None, prediction_ready=False):
        # Registration checks the same prospective source requirements as the
        # evaluator, without acquiring a worker lease or allocating a trial.
        if type(prediction_ready) is not bool or (prediction_ready and forward_registration is None):
            raise EvidenceError('PREDICTION_READINESS_REQUIRES_FORWARD_REGISTRATION')
        if forward_registration is not None:
            if (self.role!='trainer' or type(forward_registration) is not dict
                or set(forward_registration)!={'job_id','candidate_id'}
                or any(type(v) is not str or not v
                       for v in forward_registration.values())):
                raise EvidenceError('FORWARD_PREFLIGHT_SUBJECT_INVALID')
        try:
            if sha(self.c['original_config'])!=self.c['original_config_sha256']:raise EvidenceError('ORIGINAL_CONFIG_CHANGED')
            if sha(self.c['period_path'])!=self.c['period_sha256']:raise EvidenceError('ORIGINAL_PERIOD_CHANGED')
            original=read(self.c['original_config'])
            current=int(time.time()*1000)
            memory=parse_meminfo(Path('/proc/meminfo').read_text())
            pressure=parse_full_psi(Path('/proc/pressure/memory').read_text())
            resource_role=('evaluator' if prediction_ready else
                'forward_registration' if forward_registration is not None else self.role)
            resources=assess_resources(memory,pressure,current,
                os.path.lexists(self.c['manual_hold_path']),role=resource_role)
            if self.role=='trainer' and forward_registration is None:
                # The resident still validates immutable sources, causal mature
                # labels and unused rows before training. Current live readiness
                # is a requirement for prospective evaluation, not that dataset.
                report=resources
                report['admission_scope']='HISTORICAL_TRAINING_RESOURCES_ONLY'
                report['historical_dataset_validation']='PENDING_IN_RESIDENT_STEP'
                report['original_live_source_evidence']={
                    'status':'NOT_REQUESTED_FOR_HISTORICAL_TRAINING',
                    'source_ttl_ms':5000,
                    'fresh_original_live_source_proven':False,
                    'broker_queries_sent':0}
            elif not resources['admit_new_worker'] or self.draining:
                # Refusal needs no source authority. Never refresh a large raw
                # segment when the original resource rule already forbids work.
                report=resources
                report['admit_new_worker']=False
                report['source_refresh_skipped_for_resource_denial']=True
                report['original_live_source_evidence']={
                    'status':'NOT_COLLECTED_WHILE_RESOURCES_HELD',
                    'source_ttl_ms':5000,
                    'fresh_original_live_source_proven':False,
                    'broker_queries_sent':0}
            else:
                from .resident_live_source import refresh
                source=read(self.c['source_health_path'],2*1024*1024)
                if type(source) is not dict:
                    raise EvidenceError('CONTINUITY_SOURCE_STATUS_NOT_OBJECT')
                prefixes=source.get('source_prefixes')
                if type(prefixes) is not list or not prefixes:
                    raise EvidenceError('CONTINUITY_SOURCE_PREFIXES_MISSING')
                if source.get('source_prefix_digest')!=digest(prefixes):
                    raise EvidenceError('CONTINUITY_PREFIX_DIGEST_CHANGED')
                current=int(time.time()*1000)
                held=preflight_source_health_hold(source)
                if held is not None:
                    # New real quotes cannot retroactively repair already-rejected
                    # mature quality probes. Do not confuse a closed market with
                    # a corrupted historical archive.
                    report=assess(memory,pressure,source,current,
                        self.c['period_sha256'],os.path.lexists(self.c['manual_hold_path']),role=resource_role)
                    report['admit_new_worker']=False
                    report['reasons']=list(dict.fromkeys(
                        report['reasons']+['SOURCE_QUALITY_HOLD_NO_REFRESH_AUTHORITY']))
                    report['cached_probe_diagnostic']=held
                    report['original_live_source_evidence']={
                        'status':'NOT_COLLECTED_WHILE_SOURCE_PROBES_HELD',
                        'source_ttl_ms':5000,
                        'fresh_original_live_source_proven':False,
                        'broker_queries_sent':0}
                else:
                    source,proof=refresh(original,source,current)
                    report=assess(memory,pressure,source,int(time.time()*1000),
                        self.c['period_sha256'],os.path.lexists(self.c['manual_hold_path']),role=resource_role)
                    report['original_live_source_evidence']=proof
        except (OSError,ValueError,TypeError,KeyError) as exc:
            report={'schema':'heptatrader.resident-resource-admission.v1','at_ms':int(time.time()*1000),
                'admit_new_worker':False,'reasons':['RESOURCE_OR_SOURCE_UNAVAILABLE:'+str(exc)],'automatic_trading':False}
        if self.draining:
            report['admit_new_worker']=False;report['reasons'].append('SUPERVISOR_DRAIN_REQUESTED')
        report['role']=self.role
        name=self.role+'-admission.json'
        if forward_registration is not None:
            report.update(admission_scope='FORWARD_REGISTRATION_PREFLIGHT',
                          subject=dict(forward_registration),trial_allocated=False,
                          worker_launched=False,worker_launch_authority=False,
                          actual_launch_guard_still_required=True,
                           first_prediction_resource_readiness_checked=prediction_ready,
                           future_resource_capacity_reserved=False)
            name=self.role+'-forward-admission.json'
        self.attach_resource_attribution(report)
        atomic(self.root/name,report)
        return report
    def acquire(self,subject):
        fast_denial=self._priority_negative_preflight()
        report=fast_denial if fast_denial is not None else self._prioritize_background(self.check())
        if report['admit_new_worker'] and not self.lease.acquire():
            report['admit_new_worker']=False;report['reasons']=['OTHER_RESEARCH_WORKER_ACTIVE']
        # Recheck BOTH original admission and Value priority after taking the
        # same original lease. Never stop another worker or extend any deadline.
        if report['admit_new_worker']:
            try:
                report=self._prioritize_background(self.check())
            except BaseException:
                # No worker has been dispatched yet. A failed recheck must not
                # leave this controller holding the global lease while its
                # next queue/calendar preflight returns without launching.
                self.lease.release()
                raise
            if report['admit_new_worker']:return True
            self.lease.release()
        self.attach_resource_attribution(report)
        self.last_admission=report
        atomic(self.root/(self.role+'-admission.json'),report)
        name=digest([self.role,subject,report['reasons']]);path=self.root/'denials'/(name+'.json')
        if not path.exists():write_once(path,{'subject':subject,'decision':report,'orders_sent':0})
        return False
    def maintain_caches(self):
        if self.role=='value-predictor' or self.c.get('cache_maintenance')!='TERMINAL_TASK_COMGR_ONLY_V1':
            return
        now=time.monotonic()
        if self.lease.fd is None and now-getattr(self,'_last_cache_sweep',0)<60:
            return
        self._last_cache_sweep=now
        try:
            from .resident_comgr_cache import sweep_terminal
            try:
                report=sweep_terminal(self.c,existing_lease_fd=self.lease.fd)
            except BlockingIOError:
                report={'phase':'HEAVY_LEASE_BUSY','removed_files':0,'removed_bytes':0}
            atomic(self.root/(self.role+'-cache-maintenance.json'),{
                'at_ms':int(time.time()*1000),'phase':report['phase'],
                'removed_files':report.get('removed_files',0),'removed_bytes':report.get('removed_bytes',0),
                'training_result_changed':False})
        except Exception as exc:
            failure={'at_ms':int(time.time()*1000),'phase':'MAINTENANCE_FAILED',
                     'error':str(exc),'training_result_changed':False}
            try:
                atomic(self.root/(self.role+'-cache-maintenance.json'),failure)
            except Exception:
                try:print(json.dumps(failure),flush=True)
                except Exception:pass

    def release(self):
        # Parent wait/reap is complete on normal worker paths; the terminal
        # selector also refuses paths referenced by an unreaped/orphan worker.
        try:self.maintain_caches()
        finally:self.lease.release()



def evaluator_idle_poll_seconds(gate,tasks):
    if tasks or gate.draining or gate.role!='evaluator' or not gate.c.get('value_priority'):
        return 2
    try:
        from .value_priority_admission import decision
        held,reason,_=decision(gate.c['value_priority'],'evaluator',int(time.time()*1000))
    except (OSError,ValueError,TypeError,KeyError):
        return 2
    if held and reason in ('VALUE_PRIORITY_DEFER_NEW_LONG_EVALUATION',
                          'VALUE_PRIORITY_FRESH_FLAT_ORIGINAL_REQUEST'):
        return 10
    return 2


def _prediction_retry_window(path,slot,attempt,now_ms):
    # Timing-only negative cache; a returned value cannot admit a worker.
    try:
        plan=read(path)
        rule=plan['rule']
        if (plan.get('plan_id')!=digest({k:v for k,v in plan.items() if k!='plan_id'})
                or type(slot) is not int or not 0<=slot<rule['slots']
                or type(plan.get('start_ms')) is not int
                or type(rule.get('slot_spacing_ms')) is not int):
            return None
        start=plan['start_ms']+slot*rule['slot_spacing_ms']
        if now_ms>=start:return None
        delay_ms=min(10000,2000*2**min(max(attempt-1,0),3),start-now_ms)
        return {'plan':str(path),'slot':slot,'attempt':attempt,
                'denied_at_ms':now_ms,'slot_start_ms':start,
                'next_probe_at_ms':now_ms+delay_ms}
    except (OSError,ValueError,KeyError,TypeError):
        return None


def guarded_evaluator_class(base,gate):
    class GuardedEvaluator(base):
        def launch(self,path,slot):
            if self.tasks:
                # Older frozen supervisor classes don't implement the new
                # diagnostic method. Never crash their preexisting guard.
                receipt=getattr(self,'record_admission_deferral',None)
                if callable(receipt):
                    receipt(path,slot,['PRIOR_EVALUATOR_CHILD_ACTIVE'])
                return
            key=(str(path),slot);tick=time.monotonic();wall=int(time.time()*1000)
            previous=getattr(self,'_launch_retry',None)
            if (isinstance(previous,dict) and previous.get('key')==key
                and not getattr(gate,'draining',False)
                and previous['denied_at_ms']<=wall<previous['slot_start_ms']
                and previous['checked_tick']<=tick<previous['next_tick']):
                return
            if not gate.acquire({'plan':str(path),'slot':slot}):
                prior=getattr(gate,'last_admission',None)
                reasons=prior.get('reasons') if isinstance(prior,dict) else None
                if not isinstance(reasons,list) or not reasons:
                    reasons=['RESOURCE_ADMISSION_UNCLASSIFIED']
                receipt=getattr(self,'record_admission_deferral',None)
                if callable(receipt):
                    receipt(path,slot,reasons)
                same=(isinstance(previous,dict) and previous.get('key')==key
                    and previous['denied_at_ms']<=wall and previous['checked_tick']<=tick
                    and not getattr(gate,'draining',False))
                attempt=previous.get('attempt',0)+1 if same else 1
                wall=int(time.time()*1000);tick=time.monotonic()
                retry=_prediction_retry_window(path,slot,attempt,wall)
                self._launch_retry=({**retry,'key':key,'checked_tick':tick,
                    'next_tick':tick+(retry['next_probe_at_ms']-wall)/1000,
                    'reason_codes':list(reasons)} if retry is not None else None)
                return
            self._launch_retry=None
            self._worker_lease_fd=getattr(getattr(gate,'lease',None),'fd',None)
            try:
                super().launch(path,slot)
                if not self.tasks:gate.release();self._worker_lease_fd=None
            except Exception:gate.release();raise
        def reap(self):
            record=getattr(gate,'record_native_exit',None)
            previous=([(key,task[0]) for key,task in self.tasks.items()]
                      if callable(record) else [])
            try:super().reap()
            finally:
                try:
                    if callable(record):
                        for (plan,slot),child in previous:
                            record(child.poll(),{'plan':str(plan),'slot':slot})
                finally:
                    if not self.tasks:gate.release()
    return GuardedEvaluator


class _ForwardRegistrationHold(Exception):
    def __init__(self,report):
        self.report=report
        super().__init__('FORWARD_REGISTRATION_RESOURCE_OR_SOURCE_HOLD')


def guarded_trainer_class(base,gate):
    class GuardedTrainer(base):
        def _complete_worker(self,job_id,rc,state,*,failure=None):
            record=getattr(gate,'record_native_exit',None)
            try:
                if callable(record):record(rc,{'job_id':job_id})
            finally:
                # A failed hold write drains the guard, but must not suppress
                # the original job's terminal receipt after its child is gone.
                result=super()._complete_worker(job_id,rc,state,failure=failure)
            return result

        def register_evaluation(self,job_id,candidate,job):
            if self.c.get('future_plan_admission_profile') is None:
                return super().register_evaluation(job_id,candidate,job)
            previous=getattr(self,'_forward_registration_context',None)
            self._forward_registration_context=(job_id,candidate['candidate_id'])
            try:
                result=super().register_evaluation(job_id,candidate,job)
            except _ForwardRegistrationHold as exc:
                if self.db.in_transaction:
                    raise EvidenceError('FORWARD_PREFLIGHT_ROLLBACK_REQUIRED') from exc
                self._forward_registration_preflight=exc.report
                return {'state':'FORWARD_REGISTRATION_GUARD_HOLD',
                        'job_id':job_id,'candidate_id':candidate['candidate_id'],
                        'trial_allocated':False,'performance_assessment':False,
                        'resource_admission':exc.report}
            else:
                last=getattr(self,'_forward_registration_preflight',None)
                if last and last.get('subject',{}).get('job_id')==job_id:
                    self._forward_registration_preflight=None
                return result
            finally:
                self._forward_registration_context=previous

        def _evaluation_plan(self,job_id,candidate,job,trial,now):
            proposal=super()._evaluation_plan(job_id,candidate,job,trial,now)
            context=getattr(self,'_forward_registration_context',None)
            if context is None:
                return proposal
            if context!=(job_id,candidate['candidate_id']):
                raise EvidenceError('FORWARD_PREFLIGHT_CONTEXT_CHANGED')
            # This existing virtual call runs INSIDE the frozen registration
            # transaction, after recovery and pending-trial checks. The exact
            # proposal/time below is what the frozen calendar will assess next.
            from laya_cell.resident_plan_calendar import calendar_admission
            if calendar_admission(candidate,proposal,now)['ready']:
                options={}
                if self.c.get('future_trial_overlap_profile') is not None:
                    from laya_cell.resident_queue import overlap_enrolled
                    created=self.db.execute('SELECT created_ms FROM jobs WHERE id=?',(job_id,)).fetchone()[0]
                    if overlap_enrolled(self.c,job,created):options['prediction_ready']=True
                report=gate.check(forward_registration={
                    'job_id':job_id,'candidate_id':candidate['candidate_id']},**options)
                if report.get('admit_new_worker') is not True:
                    raise _ForwardRegistrationHold(report)
            return proposal

        def write_status(self,**extra):
            preflight=getattr(self,'_forward_registration_preflight',None)
            if preflight is not None:
                extra['forward_registration_preflight']=preflight
                if extra.get('forward_registration_blocking_trials',extra.get('pending_original_trials'))==0 and extra.get('deferred_training_candidates',0)>0:
                    extra['forward_registration_wait_reasons']=list(preflight.get('reasons',[]))
                    extra['forward_registration_wait_checked_at_ms']=preflight.get('at_ms')
            diagnostics=getattr(gate,'status_diagnostics',None)
            if callable(diagnostics):
                current=diagnostics();reasons=list(current['current_resource_blockers'])
                state=extra.get('state','')
                if (isinstance(state,str) and state.startswith(('HOLD_','WAITING_','CANDIDATE_READY_AWAITING_','EVALUATING_'))
                        or state=='WORKER_TERMINATION_PENDING'):
                    reasons.append(extra.get('reason') or state)
                extra.update(current_blockers=list(dict.fromkeys(reasons)),
                    current_blockers_checked_at_ms=current['checked_at_ms'],
                    resource_diagnostics=current,
                    next_calendar_eligible_at_ms=extra.get('next_training_at_ms'),
                    automatic_resume_expected=False if ('OPERATOR_RESOURCE_HOLD' in reasons
                        or state=='WORKER_TERMINATION_PENDING') else None,
                    resume_conditions_to_recheck=list(dict.fromkeys(reasons)),
                    calendar_time_is_not_worker_admission=True)
            return super().write_status(**extra)

        def step(self):
            self._reuse_queue_preflight_once=None
            reap=getattr(self,'reap_pending_worker',None)
            held=reap() if callable(reap) else None
            if held is not None:
                child=getattr(self,'child',None)
                if child is None or child.poll() is not None:gate.release()
                return held
            preflight=getattr(self,'queue_preflight',None)
            held=preflight() if preflight is not None else None
            if held is not None:return held
            if not gate.acquire({'role':'trainer'}):
                report=getattr(gate,'last_admission',None)
                return self.write_status(state='HOLD_NEW_TRAINING_RESOURCE_OR_SOURCE',
                                         storage=getattr(self,'storage',None),
                                         waiting_stage='NEW_TRAINING_RESOURCE_PREFLIGHT',
                                         training_data_refresh_performed=False,worker_lease_acquired=False,
                                         resource_admission=report if report is not None else gate.check())
            lease=getattr(gate,'lease',None)
            self._worker_lease_fd=getattr(lease,'fd',None)
            self._reuse_queue_preflight_once=getattr(self,'_queue_preflight_snapshot',None)
            try:return super().step()
            finally:
                self._reuse_queue_preflight_once=None;self._queue_preflight_snapshot=None
                child=getattr(self,'child',None)
                if child is None or child.poll() is not None:
                    gate.release();self._worker_lease_fd=None
    return GuardedTrainer


def verify_config(c):
    if c.get('cache_maintenance') not in (None,'TERMINAL_TASK_COMGR_ONLY_V1'):
        raise EvidenceError('UNREVIEWED_CACHE_MAINTENANCE_PROFILE')
    if c.get('cache_maintenance') and not c.get('guard_files_sha256',{}).get('resident_comgr_cache.py'):
        raise EvidenceError('CACHE_MAINTENANCE_RUNTIME_NOT_PINNED')
    if c.get('schema')!='heptatrader.resident-admission-wrapper-config.v1' or c.get('trade_authority') is not False:
        raise EvidenceError('GUARD_CONFIG_SCOPE')
    if c.get('resource_rule')!=RESOURCE_RULE or c.get('max_heavy_workers')!=1:raise EvidenceError('RESOURCE_RULE_CHANGED')
    if c.get('value_priority') is not None:
        from .value_priority_admission import verify_policy,_load_pinned
        verify_policy(c['value_priority'])
        _load_pinned(c['value_priority'])
        expected={'common.py','resident_live_source.py','resident_resource_guard.py',
                  'value_priority_admission.py'}
        if c.get('cache_maintenance'):expected.add('resident_comgr_cache.py')
        if set(c.get('guard_files_sha256',{}))!=expected:
            raise EvidenceError('PRIORITY_RUNTIME_NOT_PINNED')
    if sha(c['original_config'])!=c['original_config_sha256']:raise EvidenceError('ORIGINAL_CONFIG_CHANGED')
    original=read(c['original_config'])
    if original['automatic_trading'] is not False or original['period_sha256']!=c['period_sha256']:
        raise EvidenceError('ORIGINAL_RESEARCH_SCOPE')
    for name,wanted in original['runtime_sha256'].items():
        relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts or sha(Path(original['runtime_directory'])/name)!=wanted:
            raise EvidenceError('PINNED_ORIGINAL_RUNTIME_CHANGED')
    for name,wanted in c['guard_files_sha256'].items():
        if Path(name).name!=name or sha(Path(__file__).parent/name)!=wanted:raise EvidenceError('GUARD_RUNTIME_CHANGED')
    return original


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--role',choices=['trainer','evaluator'],required=True)
    p.add_argument('--check-only',action='store_true');a=p.parse_args();os.umask(0o077)
    c=read(a.config);original=verify_config(c);gate=Gate(c,a.role)
    if a.check_only:
        fast=gate._priority_negative_preflight()
        report=fast if fast is not None else gate._prioritize_background(gate.check())
        print(json.dumps(report),flush=True)
        raise SystemExit(0 if report['admit_new_worker'] else 1)
    runtime=Path(original['runtime_directory']);sys.path[:0]=[str(runtime/'learn'),str(runtime/'scripts')]
    if a.role=='trainer':
        from laya_cell.resident import Supervisor
        base=guarded_trainer_class(Supervisor,gate);directory=Path(original['control_directory']);lockname='scheduler.lock'
    else:
        from laya_cell.resident_evaluate import Evaluator
        base=guarded_evaluator_class(Evaluator,gate);directory=Path(original['evaluation_directory']);lockname='evaluator.lock'
    fd=os.open(directory/lockname,os.O_WRONLY|os.O_CREAT|os.O_CLOEXEC|os.O_NOFOLLOW,0o600)
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    service=base(Path(c['original_config']))
    # A surviving worker must also prevent a new controller from recovering
    # its still-running job as interrupted or launching the same forward slot.
    service._controller_lock_fd=fd
    signal.signal(signal.SIGTERM,service.stop_signal if a.role=='trainer' else service.signal)
    signal.signal(signal.SIGINT,service.stop_signal if a.role=='trainer' else service.signal)
    def request_drain(*_):gate.draining=True
    signal.signal(signal.SIGUSR1,request_drain)
    try:
        if a.role=='trainer':service.recover()
        while not service.stop:
            try:
                service.step()
                error_path=gate.root/(a.role+'-error.json')
                if error_path.exists():
                    previous=read(error_path)
                    if previous.get('active') is not False:
                        atomic(error_path,{**previous,'active':False,'resolved_at_ms':int(time.time()*1000)})
            except Exception as exc:
                failed_at=int(time.time()*1000)
                atomic(gate.root/(a.role+'-error.json'),{'at_ms':failed_at,'error':str(exc),'orders_sent':0,'active':True})
                if a.role=='trainer':
                    service.write_status(state='LEARNING_UNAVAILABLE_NO_TRADE_EFFECT',reason=str(exc))
                else:
                    atomic(service.root/'status.json',{'at_ms':failed_at,'state':'INDEPENDENT_EVALUATION_UNAVAILABLE',
                        'reason':str(exc),'promotion_performed':False,'broker_orders_sent':0})
            if gate.draining and (a.role=='trainer' or not service.tasks):break
            poll_seconds=30 if a.role=='trainer' else evaluator_idle_poll_seconds(gate,service.tasks)
            for _ in range(poll_seconds):
                if service.stop:break
                time.sleep(1)
    finally:
        try:
            if a.role=='trainer':
                child=getattr(service,'child',None)
                if child is not None:
                    from laya_cell.resident import terminate_worker
                    terminate_worker(child)
                reaped=child is None or child.poll() is not None
            else:
                stop_workers=getattr(service,'stop_workers',None)
                if callable(stop_workers):stop_workers()
                reaped=not any(p.poll() is None for p,_,_ in service.tasks.values())
            # Close only; LOCK_UN would unlock the child's shared description.
            if reaped:gate.release()
        finally:
            if a.role=='trainer':service.db.close()
            os.close(fd)
if __name__=='__main__':main()
