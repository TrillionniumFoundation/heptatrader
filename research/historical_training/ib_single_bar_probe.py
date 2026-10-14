"""One bounded historical MIDPOINT request in a fresh IB PAPER session.

This is a diagnostic for whether reqHistoricalData works even when named
cashhmds headTimestamp requests time out. It cannot retrieve a full data set,
has no order API/authority and never retries an unknown request.
"""
from __future__ import annotations
import argparse,hashlib,json,os,threading,time
from pathlib import Path
from .common import EvidenceError,read,sha,write_once,atomic,digest
from .ib_history import (
    validate,make_app,global_history_lock,CONTRACT,assert_cash_hmds_admitted,
    sync_dir,completion_state
)

def bounded_plan(c):
    end=validate(c)
    if (c.get('history_probe_only') is not True
            or c.get('unknown_probe_kind')!='bars'
            or c.get('head_probe_on_unknown') is not True
            or c.get('chunks')!=1 or c.get('window_days')!=1):
        raise EvidenceError('SINGLE_BAR_SCOPE_REQUIRED')
    return {'kind':'bars','what':'MIDPOINT','end':c['end_utc'],
            'duration':'1 D','bar_size':'5 mins',
            'requested_start_utc':__import__('datetime').datetime.fromtimestamp(
                 end.timestamp()-86400,__import__('datetime').timezone.utc).isoformat(),
            'requested_end_utc':end.isoformat()}

def run(c):
    plan=bounded_plan(c);root=Path(c['output_root']);started=int(time.time()*1000)
    with global_history_lock(root):
        if root.exists():raise EvidenceError('FRESH_PROBE_ROOT_REQUIRED')
        root.mkdir(parents=True,mode=0o700)
        write_once(root/'request.json',plan)
        sync_dir(root)
        app,contract=make_app(c);thread=None;sent=0
        status={'schema':'heptatrader.ib-single-bar-diagnostic.v1','at_ms':started,
                'plan':plan,'config_sha256':digest(c),'trade_authority':False,
                'broker_orders_sent':0,'diagnostic_request_limit':1,'request_count':0}
        try:
            app.connect(c['host'],c['port'],c['client_id'])
            thread=threading.Thread(target=app.run,daemon=True);thread.start()
            if not app.ready.wait(12) or not app.account_event.wait(3) or not app.paper:
                raise EvidenceError('PAPER_HANDSHAKE_FAILED')
            state=app.farm.wait_or_probe(
                c['cash_hmds_startup_wait_sec'],
                allow_unknown=True,alive=lambda:app.isConnected() and not app.dead)
            status['startup_readiness']=state
            assert_cash_hmds_admitted(app)
            rid=7800
            app.own.add(rid);app.active.add(rid);app.guard.register(rid,plan)
            app.events[rid]=threading.Event();app.rows[rid]=[]
            sent=1;status['request_count']=sent
            # Guard enforces single unknown-farm MIDPOINT historical-bar dispatch.
            app.reqHistoricalData(rid,contract,plan['end'],'1 D','5 mins','MIDPOINT',0,2,False,[])
            done=app.events[rid].wait(45)
            errs=[x for x in app.errors if x['request_id']==rid]
            ended=app.ends.get(rid)
            if not ended:app.cancel_own(rid)
            payload={'bars':app.rows[rid],'errors':errs,'end_callback':ended}
            write_once(root/'response.json',payload)
            good=bool(done and ended and len(payload['bars'])>0 and not errs)
            status.update(state='RECEIVED' if good else 'NO_COMPLETE_BAR_RESPONSE',
                          bars=len(payload['bars']),end_callback=ended,
                          request_error_codes=[e['code'] for e in errs],
                          response_sha256=sha(root/'response.json'))
        except Exception as exc:
            status.update(state='REFUSED_OR_UNAVAILABLE',exception=type(exc).__name__+':'+str(exc))
        finally:
            app.disconnect()
            if thread:thread.join(timeout=2)
            status.update(ended_ms=int(time.time()*1000),paper_verified=app.paper,
                          actual_outbound_codes=dict(app.sent),
                          farm=app.farm.snapshot() if app.farm else None,
                          errors=[{'req_id':x['request_id'],'code':x['code'],'message':x['message']}
                                  for x in app.errors])
            atomic(root/'session.json',status)
        return status

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True)
    a=p.parse_args();os.umask(0o077);result=run(read(Path(a.config)))
    print(json.dumps({k:result.get(k) for k in (
        'state','startup_readiness','paper_verified','request_count','bars',
        'request_error_codes','actual_outbound_codes','exception')}),flush=True)
    if result.get('state')!='RECEIVED':raise SystemExit(2)
if __name__=='__main__':main()
