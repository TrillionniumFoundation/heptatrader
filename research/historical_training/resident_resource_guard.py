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
RESOURCE_RULE={'min_available_bytes':8*GIB,'min_swap_free_bytes':GIB,'max_full_psi_avg10':1.,'max_full_psi_avg60':.1}


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


def assess(memory,pressure,source,now_ms,period_sha256,manual_hold):
    reasons=[]
    if type(manual_hold) is not bool or manual_hold:reasons.append('OPERATOR_RESOURCE_HOLD')
    if memory['MemAvailable']<RESOURCE_RULE['min_available_bytes']:reasons.append('HOST_AVAILABLE_MEMORY_BELOW_8GIB')
    if memory['SwapFree']<RESOURCE_RULE['min_swap_free_bytes']:reasons.append('HOST_SWAP_RESERVE_BELOW_1GIB')
    if pressure['avg10']>1 or pressure['avg60']>.1:reasons.append('HOST_FULL_MEMORY_PSI_PRESSURE')
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
    return {'schema':'heptatrader.resident-resource-admission.v1','at_ms':now_ms,'admit_new_worker':not reasons,
        'reasons':list(dict.fromkeys(reasons)),'memory_bytes':memory,'full_memory_psi':pressure,
        'max_new_heavy_workers_across_roles':1,'existing_workers_interrupted':False,
        'automatic_trading':False,'statistical_rule_changed':False}



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
        if role not in ('trainer','evaluator'):raise EvidenceError('RESEARCH_ROLE_REQUIRED')
        self.c=config;self.role=role;self.root=Path(config['state_root']);self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.lease=Lease(self.root/'heavy-worker.lock');self.draining=False
        self.last_admission=None
        if self.c.get('value_priority') is not None and role in ('trainer','evaluator'):
            from .value_priority_admission import verify_policy
            verify_policy(self.c['value_priority'])
    def _priority_negative_preflight(self):
        # Read the SHA256-pinned original Value producer BEFORE an expensive
        # source-health refresh, but use this shortcut ONLY to refuse new
        # background work. Every positive admission still requires the full
        # original memory/PSI/source check, the one global flock and a second
        # source+priority check after taking that flock.
        manual=self.c.get('manual_hold_path')
        if (self.c.get('value_priority') is None or self.draining or
                (manual is not None and os.path.lexists(manual))):
            return None
        try:
            from .value_priority_admission import decision
            held,reason,proof=decision(
                self.c['value_priority'],self.role,int(time.time()*1000))
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
        if not report['admit_new_worker'] or self.c.get('value_priority') is None:
            self.last_admission=report
            return report
        try:
            from .value_priority_admission import decision
            held,reason,proof=decision(self.c['value_priority'],self.role,int(time.time()*1000))
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
    def check(self):
        try:
            if sha(self.c['original_config'])!=self.c['original_config_sha256']:raise EvidenceError('ORIGINAL_CONFIG_CHANGED')
            if sha(self.c['period_path'])!=self.c['period_sha256']:raise EvidenceError('ORIGINAL_PERIOD_CHANGED')
            from .resident_live_source import refresh
            original=read(self.c['original_config'])
            source=read(self.c['source_health_path'],2*1024*1024)
            if type(source) is not dict:
                raise EvidenceError('CONTINUITY_SOURCE_STATUS_NOT_OBJECT')
            prefixes=source.get('source_prefixes')
            if type(prefixes) is not list or not prefixes:
                raise EvidenceError('CONTINUITY_SOURCE_PREFIXES_MISSING')
            if source.get('source_prefix_digest')!=digest(prefixes):
                raise EvidenceError('CONTINUITY_PREFIX_DIGEST_CHANGED')
            current=int(time.time()*1000)
            memory=parse_meminfo(Path('/proc/meminfo').read_text())
            pressure=parse_full_psi(Path('/proc/pressure/memory').read_text())
            held=preflight_source_health_hold(source)
            if held is not None:
                # New real quotes cannot retroactively repair already-rejected
                # mature quality probes. Do not confuse an unavailable live
                # extension during a closed market with a corrupted archive.
                report=assess(memory,pressure,source,current,
                    self.c['period_sha256'],os.path.lexists(self.c['manual_hold_path']))
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
                    self.c['period_sha256'],os.path.lexists(self.c['manual_hold_path']))
                report['original_live_source_evidence']=proof
        except (OSError,ValueError,TypeError,KeyError) as exc:
            report={'schema':'heptatrader.resident-resource-admission.v1','at_ms':int(time.time()*1000),
                'admit_new_worker':False,'reasons':['RESOURCE_OR_SOURCE_UNAVAILABLE:'+str(exc)],'automatic_trading':False}
        if self.draining:
            report['admit_new_worker']=False;report['reasons'].append('SUPERVISOR_DRAIN_REQUESTED')
        report['role']=self.role;atomic(self.root/(self.role+'-admission.json'),report);return report
    def acquire(self,subject):
        fast_denial=self._priority_negative_preflight()
        report=fast_denial if fast_denial is not None else self._prioritize_background(self.check())
        if report['admit_new_worker'] and not self.lease.acquire():
            report['admit_new_worker']=False;report['reasons']=['OTHER_RESEARCH_WORKER_ACTIVE']
        # Recheck BOTH original admission and Value priority after taking the
        # same original lease. Never stop another worker or extend any deadline.
        if report['admit_new_worker']:
            report=self._prioritize_background(self.check())
            if report['admit_new_worker']:return True
            self.lease.release()
        self.last_admission=report
        atomic(self.root/(self.role+'-admission.json'),report)
        name=digest([self.role,subject,report['reasons']]);path=self.root/'denials'/(name+'.json')
        if not path.exists():write_once(path,{'subject':subject,'decision':report,'orders_sent':0})
        return False
    def maintain_caches(self):
        if self.c.get('cache_maintenance')!='TERMINAL_TASK_COMGR_ONLY_V1':
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
            if not gate.acquire({'plan':str(path),'slot':slot}):
                prior=getattr(gate,'last_admission',None)
                reasons=prior.get('reasons') if isinstance(prior,dict) else None
                if not isinstance(reasons,list) or not reasons:
                    reasons=['RESOURCE_ADMISSION_UNCLASSIFIED']
                receipt=getattr(self,'record_admission_deferral',None)
                if callable(receipt):
                    receipt(path,slot,reasons)
                return
            try:super().launch(path,slot)
            except Exception:gate.release();raise
        def reap(self):
            super().reap()
            if not self.tasks:gate.release()
    return GuardedEvaluator


def guarded_trainer_class(base,gate):
    class GuardedTrainer(base):
        def step(self):
            preflight=getattr(self,'queue_preflight',None)
            held=preflight() if preflight is not None else None
            if held is not None:return held
            if not gate.acquire({'role':'trainer'}):
                report=getattr(gate,'last_admission',None)
                return self.write_status(state='HOLD_NEW_TRAINING_RESOURCE_OR_SOURCE',
                                         resource_admission=report if report is not None else gate.check())
            try:return super().step()
            finally:gate.release()
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
        gate.release()
        if a.role=='trainer':service.db.close()
        os.close(fd)
if __name__=='__main__':main()
