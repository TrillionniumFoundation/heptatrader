#!/usr/bin/env python3
"""Read-only, finite-prefix PRICE gate audit; no model, order or write API."""
from __future__ import annotations
import argparse, hashlib, json, os, re, stat, sys, time
from pathlib import Path
sys.dont_write_bytecode=True
import hepta_decision_attribution as attribution
import hepta_pnl_accounting as accounting
import hepta_price_bootstrap_gate as gate

MAX_NEW_BYTES=16*1024*1024
MAX_REQUESTS=32
MAX_SEGMENTS=128
MAX_JOURNAL_BYTES=32*1024*1024
REPORT_SCHEMA='heptatrader.price-bootstrap-readonly-audit.v1'
BAD_INPUT_ERRORS=(ValueError,KeyError,TypeError,AttributeError,IndexError,OverflowError,OSError)

class AuditError(ValueError): pass

def need(value,reason):
    if not value:raise AuditError(reason)

def sha(raw):return hashlib.sha256(raw).hexdigest()

def private_info(path):
    path=Path(path);need(path.is_absolute() and path.resolve()==path,'SOURCE_PATH_ALIAS')
    item=path.lstat()
    need(stat.S_ISREG(item.st_mode) and item.st_uid==os.geteuid() and item.st_nlink==1
         and not item.st_mode&0o077,'SOURCE_FILE_SCOPE')
    return item

def record(path):
    before=private_info(path);raw=attribution.read_private(path,gate.MAX_BYTES)
    pin={'path':str(path),'bytes':len(raw),'sha256':sha(raw),'ctime_ns':before.st_ctime_ns}
    gate.read_exact(path,{'sha256':pin['sha256'],'stat':gate.identity(before)})
    return attribution.strict(raw),pin

def read_oms_range(path,base_bytes,base_sha256):
    """Hash the old pinned prefix once; parse only its bounded new suffix."""
    before=private_info(path)
    need(type(base_bytes) is int and 0<=base_bytes<=attribution.MAX_TOTAL,'OMS_BASE_BOUND')
    need(re.fullmatch('[0-9a-f]{64}',base_sha256) is not None,'OMS_BASE_HASH')
    need(base_bytes<=before.st_size<=base_bytes+MAX_NEW_BYTES,'OMS_NEW_RANGE_BOUND')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        opened=os.fstat(fd)
        need((opened.st_dev,opened.st_ino)==(before.st_dev,before.st_ino),'OMS_REPLACED')
        running=hashlib.sha256();remaining=base_bytes;last=b''
        while remaining:
            block=os.read(fd,min(1048576,remaining));need(block,'OMS_BASE_SHORT_READ')
            running.update(block);remaining-=len(block);last=block[-1:]
        need(running.hexdigest()==base_sha256 and (not base_bytes or last==b'\n'),'OMS_BASE_CHANGED')
        remaining=before.st_size-base_bytes;chunks=[]
        while remaining:
            block=os.read(fd,min(1048576,remaining));need(block,'OMS_RANGE_SHORT_READ')
            chunks.append(block);remaining-=len(block)
        after=os.fstat(fd)
        need((after.st_dev,after.st_ino)==(before.st_dev,before.st_ino)
             and after.st_size>=before.st_size,'OMS_CHANGED_DURING_READ')
    finally:os.close(fd)
    tail=b''.join(chunks);complete=tail[:tail.rfind(b'\n')+1];running.update(complete);rows=[]
    for line in complete.splitlines(keepends=True):
        need(len(line)<=attribution.MAX_RECORD,'OMS_RECORD_BOUND')
        value=attribution.strict(line);need(type(value) is dict,'OMS_RECORD_SHAPE');rows.append(value)
    return rows,{'path':str(path),'bytes':base_bytes+len(complete),'sha256':running.hexdigest(),
        'base_bytes':base_bytes,'base_sha256':base_sha256,'new_bytes':len(complete),'new_sha256':sha(complete),
        'trailing_uncommitted_bytes':len(tail)-len(complete),'parsed_new_records':len(rows)}

def journal_snapshot(manifest,producer_sha,checked=None,explicit=(),initial_writer=None):
    """Inspect bounded direct headers, reading bodies only for this producer."""
    root=Path(manifest['journal_directory'])
    need(root.is_absolute() and root.resolve()==root,'JOURNAL_PATH_ALIAS')
    candidates=[]
    if explicit:candidates=list(map(Path,explicit))
    elif root.exists():
        for path in root.glob('*.jsonl'):
            candidates.append(path);need(len(candidates)<=MAX_SEGMENTS,'JOURNAL_SEGMENT_BOUND')
        candidates.sort()
    need(len(candidates)<=MAX_SEGMENTS,'JOURNAL_SEGMENT_BOUND')
    groups={};pins=[];trailing=0;total_bytes=0
    for path in candidates:
        need(path.parent==root,'JOURNAL_OUTSIDE_DECLARED_DIRECTORY');info=private_info(path)
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        with os.fdopen(fd,'rb') as stream:first=stream.readline(attribution.MAX_RECORD+1)
        if not first.endswith(b'\n'):trailing+=len(first);continue
        header=attribution.strict(first);need(header.get('kind')=='HEADER','JOURNAL_HEADER_MISSING')
        if header.get('data',{}).get('manifest_sha256')!=producer_sha:
            need(not explicit,'FOREIGN_PRODUCER_JOURNAL');continue
        writer=header.get('producer_id');need(re.fullmatch('[0-9a-f]{32}',writer or '') is not None,'JOURNAL_WRITER_ID')
        total_bytes+=info.st_size;need(total_bytes<=MAX_JOURNAL_BYTES,'JOURNAL_TOTAL_BOUND')
        raw=attribution.read_private(path,attribution.MAX_STREAM,prefix_bytes=info.st_size)
        complete=raw[:raw.rfind(b'\n')+1];trailing+=len(raw)-len(complete)
        pins.append({'path':str(path),'bytes':len(complete),'sha256':sha(complete)})
        for line in complete.splitlines():
            need(len(line)<=attribution.MAX_RECORD,'JOURNAL_RECORD_BOUND');value=attribution.strict(line)
            need(value.get('schema')==attribution.SCHEMA and value.get('producer_id')==writer
                 and value.get('sha256')==attribution.digest({k:v for k,v in value.items() if k!='sha256'}),'JOURNAL_RECORD_HASH')
            groups.setdefault(writer,[]).append(value)
    if initial_writer is not None:
        need(re.fullmatch('[0-9a-f]{32}',initial_writer) is not None,'INITIAL_WRITER_ID')
        need(initial_writer in groups,'INITIAL_WRITER_NOT_FOUND')
    checked=checked if checked is not None else time.time_ns()//1000000
    requests={};responses={};decisions={};commands={};command_rows={};gaps=[];health_pins=[]
    for writer,rows in groups.items():
        rows.sort(key=lambda value:value['sequence']);previous='0'*64
        health_path=root/(writer+'.health.json')
        if health_path.exists():
            health_raw=attribution.read_private(health_path,65536);health=attribution.strict(health_raw)
            need(health.get('producer_id')==writer,'JOURNAL_HEALTH_IDENTITY')
            health_pins.append({'path':str(health_path),'sha256':sha(health_raw)})
            if health.get('dropped') or health.get('error'):gaps.append({'writer':writer,'reason':'JOURNAL_WRITER_LOSS'})
        else:gaps.append({'writer':writer,'reason':'JOURNAL_HEALTH_MISSING'})
        for index,row in enumerate(rows):
            need(row.get('sequence')==index and row.get('previous')==previous,'JOURNAL_CHAIN_GAP')
            previous=row['sha256'];kind=row.get('kind');value=row.get('data')
            need(type(value) is dict and type(row.get('captured_at_ms')) is int
                 and type(row.get('written_at_ms')) is int
                 and 0<row['captured_at_ms']<=row['written_at_ms']<=checked,'JOURNAL_RECORD_TIME')
            if row.get('dropped_total'):gaps.append({'writer':writer,'sequence':index,'reason':'JOURNAL_DROPPED_RECORDS'})
            if kind=='HEADER':
                need(value.get('manifest_sha256')==producer_sha and value.get('instrument')=='EUR.USD'
                     and value.get('period_sha256')==manifest['period_sha256'] and value.get('execution_domain')=='PAPER'
                     and value.get('source_kind')=='PINNED_DRIVER_CALLSITE' and value.get('trading_authority') is False,'JOURNAL_HEADER_SCOPE')
            elif index==0:raise AuditError('JOURNAL_HEADER_MISSING')
            elif kind in ('REQUEST','RESPONSE','DECISION'):
                target={'REQUEST':requests,'RESPONSE':responses,'DECISION':decisions}[kind];rid=value.get('request_id')
                need(rid not in target,'DUPLICATE_REQUEST_GRAPH');target[rid]=(value,row)
            elif kind=='COMMAND_BINDING' and value.get('command',{}).get('kind')=='OPEN':
                command=value['command']['command_id'];need(command not in commands,'DUPLICATE_COMMAND_BINDING')
                commands[command]=value;command_rows[command]=row
    return {'requests':requests,'responses':responses,'decisions':decisions,'commands':commands,'command_rows':command_rows,
        'gaps':gaps,'prefixes':pins,'health_pins':health_pins,'trailing_uncommitted_bytes':trailing,
        'writer_ids':sorted(groups),'initial_writer_id':initial_writer}

def check_timing(source,prediction,seal,admission,binding,pins):
    request=source['request'];response=source['response'];decision=source['decision']
    prepared=binding['prepared_command'];receipt=prepared['price_bootstrap_forecast']
    times=[request['started_at_ms'],response['available_at_ms'],decision['available_at_ms'],
        prediction['computed_at_ms'],seal['payload_files_and_directories_fsync_completed_at_ms'],
        admission['seal_durable_completed_at_ms'],admission['verified_at_ms'],
        receipt['admission_durable_completed_at_ms'],receipt['verified_before_native_prepare_at_ms'],
        prepared['created_at_ms'],binding['prepared_state_file_and_directory_fsync_completed_at_ms']]
    need(all(type(value) is int and value>0 for value in times),'TIMESTAMP_INVALID')
    need(times==sorted(times) and times[7]<times[8]<=times[9],'LATE_OR_REVERSED_PREOPEN_SEAL')
    need(times[7]<admission['original_signal_expires_at_ms'],'LATE_SIGNAL_EXPIRED')
    for filename,completed in (('input.json',times[4]),('prediction.json',times[4]),('seal.json',times[5]),('admission.json',times[7])):
        # ctime only detects an inconsistency; it never supplies a missing
        # post-fsync completion time. Receipts use millisecond floor timestamps.
        need(pins[filename]['ctime_ns']<(completed+1)*1000000,'LATE_FILE_MATERIALIZATION:'+filename)
    return {'request_started_at_ms':times[0],'computed_at_ms':times[3],
        'payload_fsync_completed_at_ms':times[4],'seal_fsync_completed_at_ms':times[5],
        'admission_fsync_completed_at_ms':times[7],'before_native_prepare_at_ms':times[8],
        'original_prepared_created_at_ms':times[9],'prepared_state_fsync_completed_at_ms':times[10]}

def private_chain(folder,manifest,producer_sha,base_observed_at_ms):
    names=('input.json','prediction.json','seal.json','admission.json','command-binding.json')
    missing=[name for name in names if not (folder/name).exists()]
    if missing:return {'request_id':folder.name,'status':'PARTIAL_PRIVATE_RECEIPT','missing_files':missing}
    values={};pins={}
    for name in names:values[name],pins[name]=record(folder/name)
    source,prediction,seal,admission,binding=(values[name] for name in names)
    request=source['request'];response=source['response'];decision=source['decision']
    rid=folder.name;request_sha=attribution.digest(request);decision_sha=attribution.digest(decision['decision'])
    need(source.get('schema')=='heptatrader.price-bootstrap-original-input.v1'
         and source.get('producer_manifest_sha256')==producer_sha
         and request.get('request_id')==rid and request.get('instrument')=='EUR.USD'
         and request.get('period_sha256')==manifest['period_sha256'] and request.get('position_at_request')==0
         and request['started_at_ms']>base_observed_at_ms
         and request.get('prompt_sha256')==sha(request['prompt'].encode()) and source['request_sha256']==request_sha,
         'PRIVATE_REQUEST_IDENTITY')
    need(source['response_sha256']==attribution.digest(response)
         and response['request_id']==rid and response['request_sha256']==request_sha and response['returncode']==0
         and decision['request_id']==rid and decision['request_sha256']==request_sha
         and decision['response_sha256']==source['response_sha256'] and decision['decision_sha256']==decision_sha,
         'PRIVATE_RESPONSE_DECISION_IDENTITY')
    envelope=attribution.strict(response['stdout'])
    need(envelope.get('ok') is True and len(envelope.get('outputs',[]))==1,'ORIGINAL_RESPONSE_ENVELOPE')
    decoded=attribution.strict(envelope['outputs'][0]['text'])
    need(type(decoded) is dict and all(decision['decision'].get(k)==v for k,v in decoded.items()),'ORIGINAL_RESPONSE_DECISION')
    quote=gate.past_quote(request)
    fields={'schema','profile','kind','producer_manifest_sha256','period_sha256','instrument','request_id','request_sha256',
        'decision_sha256','original_input_sha256','selection_sha256','implementation_sha256','source_quote','horizon_ms',
        'target_at_ms','point_forecast_mid','computed_at_ms','unit','parameter_updates','learned_value_head',
        'value_coverage_credit','efficacy_claim','forecast_value_selects_action'}
    need(set(prediction)==fields and prediction.get('schema')=='heptatrader.price-persistence-prediction.v1'
         and prediction.get('profile')==gate.PROFILE and prediction.get('kind')=='PRICE_PERSISTENCE_BASELINE'
         and prediction.get('producer_manifest_sha256')==producer_sha and prediction.get('period_sha256')==manifest['period_sha256']
         and prediction.get('instrument')=='EUR.USD' and prediction.get('request_id')==rid and prediction.get('request_sha256')==request_sha
         and prediction.get('decision_sha256')==decision_sha and prediction.get('original_input_sha256')==pins['input.json']['sha256']
         and prediction.get('selection_sha256')==gate.digest(manifest['price_bootstrap_gate'])
         and prediction.get('implementation_sha256')==manifest['runtime_sha256'][gate.MODULE]
         and prediction.get('source_quote')==quote and prediction.get('point_forecast_mid')==quote['mid']
         and prediction.get('horizon_ms')==gate.HORIZON_MS and prediction.get('target_at_ms')==request['started_at_ms']+gate.HORIZON_MS
         and prediction.get('unit')=='USD_PER_EUR_PRICE_NOT_PROFIT' and type(prediction.get('parameter_updates')) is int
         and prediction['parameter_updates']==0
         and all(prediction.get(key) is False for key in ('learned_value_head','value_coverage_credit','efficacy_claim','forecast_value_selects_action')),
         'PRICE_BASELINE_SCOPE_OR_HASH')
    need(seal.get('schema')=='heptatrader.price-bootstrap-seal.v1' and seal.get('producer_manifest_sha256')==producer_sha
         and seal.get('request_id')==rid and seal.get('request_sha256')==request_sha
         and seal.get('original_input_sha256')==pins['input.json']['sha256'] and seal.get('prediction_sha256')==pins['prediction.json']['sha256'],
         'PRIVATE_SEAL_HASH')
    need(admission.get('schema')=='heptatrader.price-bootstrap-admission.v1' and admission.get('profile')==gate.PROFILE
         and admission.get('producer_manifest_sha256')==producer_sha and admission.get('request_id')==rid
         and admission.get('request_sha256')==request_sha and admission.get('decision_sha256')==decision_sha
         and admission.get('prediction_sha256')==pins['prediction.json']['sha256'] and admission.get('seal_sha256')==pins['seal.json']['sha256']
         and admission.get('computed_at_ms')==prediction['computed_at_ms']
         and admission.get('original_signal_expires_at_ms')==quote['observed_at_ms']+gate.SIGNAL_TTL_MS
         and admission.get('native_risk_checks_bypassed') is False and admission.get('value_coverage_credit') is False,'PRIVATE_ADMISSION_HASH')
    prepared=binding['prepared_command'];receipt=prepared['price_bootstrap_forecast']
    boundary={'schema':'heptatrader.price-bootstrap-pre-submit-boundary.v1','command_id':prepared.get('command_id'),
        'forecast_receipt_sha256':gate.digest(receipt),'required_next_transport_state':'POSSIBLY_SENT'}
    need(binding.get('schema')=='heptatrader.price-bootstrap-command-binding.v1'
         and binding.get('producer_manifest_sha256')==producer_sha and binding.get('request_id')==rid
         and binding.get('request_sha256')==request_sha and binding.get('decision_sha256')==decision_sha
         and binding.get('command_id')==prepared.get('command_id') and prepared.get('kind')=='OPEN'
         and re.fullmatch('[0-9a-f]{64}',binding.get('original_prepared_state_sha256','')) is not None
         and prepared.get('instrument')=='EUR.USD' and prepared.get('send_state')=='PREPARED'
         and prepared.get('side')==admission.get('side')==decision['decision'].get('action')
         and prepared.get('position_before_send')==0 and prepared.get('price_bootstrap_pre_submit_boundary')==boundary
         and receipt.get('admission_sha256')==pins['admission.json']['sha256'] and receipt.get('receipt_directory')==str(folder)
         and all(receipt.get(key)==value for key,value in admission.items())
         and binding.get('transport_submit_observed') is False and binding.get('value_coverage_credit') is False,'PRIVATE_COMMAND_IDENTITY')
    return {'request_id':rid,'request_sha256':request_sha,'command_id':prepared['command_id'],'status':'PRIVATE_SEAL_VERIFIED',
        'pins':pins,'times':check_timing(source,prediction,seal,admission,binding,pins),
        'source':source,'prepared':prepared,'receipt':receipt}

def join_journal(case,journal):
    rid=case['request_id'];command=case['command_id'];source=case['source']
    matching=[key for key,value in journal['commands'].items() if value.get('request_id')==rid]
    need(len(matching)<=1,'REQUEST_REUSED_FOR_MULTIPLE_OPEN_COMMANDS')
    if rid not in journal['requests'] or rid not in journal['responses'] or rid not in journal['decisions'] or command not in journal['commands']:
        return False
    rows=[journal[k][rid] for k in ('requests','responses','decisions')];bound=journal['commands'][command];row=journal['command_rows'][command]
    need(rows[0][0]==source['request'] and rows[1][0]==source['response'] and rows[2][0]==source['decision'],'JOURNAL_PRIVATE_INPUT_CONFLICT')
    sequence=[value[1]['sequence'] for value in rows]+[row['sequence']]
    need(len({value[1]['producer_id'] for value in rows}|{row['producer_id']})==1 and sequence==sorted(set(sequence)),'JOURNAL_CAUSAL_ORDER')
    need(bound.get('request_id')==rid and bound.get('request_sha256')==case['request_sha256']
         and bound.get('response_sha256')==source['response_sha256'] and bound.get('decision_sha256')==source['decision']['decision_sha256']
         and bound.get('state_write_succeeded') is True and bound.get('basis')=='IDENTICAL_RETURNED_OBJECT_AT_ORIGINAL_DURABLE_PREPARED'
         and bound.get('price_bootstrap_forecast')==case['receipt']
         and all(case['prepared'].get(key)==value for key,value in bound['command'].items()),'JOURNAL_COMMAND_IDENTITY')
    case['times']['command_binding_emitted_at_ms']=row['captured_at_ms']
    need(case['times']['prepared_state_fsync_completed_at_ms']<=row['captured_at_ms']
         and case['pins']['command-binding.json']['ctime_ns']<(row['captured_at_ms']+1)*1000000,'LATE_COMMAND_BINDING')
    case['journal_command_record_sha256']=row['sha256'];return True

def local_discard(folder,producer_sha):
    """Report a local no-transport record, never a broker rejection or fill."""
    path=folder/'local-non-send.json'
    if not path.exists():return None
    archive,pin=record(path);pending=archive['pending'];receipt=pending['price_bootstrap_forecast']
    boundary={'schema':'heptatrader.price-bootstrap-pre-submit-boundary.v1','command_id':pending['command_id'],
        'forecast_receipt_sha256':gate.digest(receipt),'required_next_transport_state':'POSSIBLY_SENT'}
    need(archive.get('schema')=='heptatrader.price-bootstrap-local-non-send.v1'
         and archive.get('disposition')=='LOCAL_PRE_SUBMIT_DISCARD_NOT_NATIVE_REJECTION'
         and archive.get('broker_order_created') is False and archive.get('replacement_or_resubmit') is False
         and archive.get('rule')=='DURABLE_PRE_SUBMIT_BOUNDARY_AND_FRESH_UNCHANGED_ORIGINAL_SNAPSHOT'
         and pending.get('kind')=='OPEN' and pending.get('send_state') in ('PREPARED','LOCAL_PRE_SUBMIT_BLOCKED')
         and pending.get('broker_order_id') is None and pending.get('price_bootstrap_pre_submit_boundary')==boundary
         and receipt.get('producer_manifest_sha256')==producer_sha and receipt.get('request_id')==folder.name
         and receipt.get('receipt_directory')==str(folder),'LOCAL_NO_SEND_IDENTITY')
    need(not any(key in pending for key in ('last_submit_transport_ok','last_submit_reason','application_status',
        'application_reason','application_observed_at_ms','last_status','venue_correlation_id','broker_execution_id')),
        'LOCAL_NO_SEND_TRANSPORT_CONFLICT')
    if pending['send_state']=='LOCAL_PRE_SUBMIT_BLOCKED':
        local=pending.get('price_bootstrap_local_non_send') or {}
        need(local.get('schema')=='heptatrader.price-bootstrap-local-transport-boundary.v1'
             and local.get('command_id')==pending['command_id'] and local.get('forecast_receipt_sha256')==gate.digest(receipt)
             and local.get('transport_call_entered') is False
             and local.get('phase') in ('PREPARED','POSSIBLY_SENT_DURABILITY','BEFORE_SUBMIT'),'LOCAL_NO_SEND_BOUNDARY')
    values={};pins={'local-non-send.json':pin}
    for name in ('input.json','prediction.json','seal.json','admission.json'):
        values[name],pins[name]=record(folder/name)
    admission=values['admission.json'];seal=values['seal.json'];source=values['input.json'];prediction=values['prediction.json']
    need(pins['admission.json']['sha256']==receipt['admission_sha256']
         and pins['seal.json']['sha256']==receipt['seal_sha256']
         and pins['prediction.json']['sha256']==receipt['prediction_sha256']
         and pins['input.json']['sha256']==seal['original_input_sha256']==prediction['original_input_sha256']
         and seal['prediction_sha256']==receipt['prediction_sha256']
         and all(receipt.get(key)==value for key,value in admission.items())
         and source['producer_manifest_sha256']==producer_sha
         and attribution.digest(source['request'])==receipt['request_sha256']==source['request_sha256']
         and source['request']['request_id']==folder.name
         and source['decision']['decision_sha256']==receipt['decision_sha256'],'LOCAL_NO_SEND_INPUT_HASH')
    return {'request_id':folder.name,'request_sha256':receipt['request_sha256'],'command_id':pending['command_id'],
        'status':'LOCAL_PRE_SUBMIT_NO_SEND_RECORDED','pins':pins,
        'basis':'PINNED_GATE_LOCAL_RECORD_NOT_NATIVE_REJECTION_OR_FILL',
        'fresh_snapshot_independently_replayed':False,'value_coverage_credit':False}

def native_evidence(case,rows,period,checked):
    """Use the existing exact native accounting validator, without valuing PnL."""
    command=case['command_id']
    direct=[row for row in rows if command in (row.get('req_id'),row.get('client_req_id'))]
    sends=[row for row in direct if row.get('event')=='place_sent' and accounting._owns(row,period,'EUR.USD')]
    if not sends:return None
    correlations={row.get('venue_correlation_id') for row in direct if row.get('venue_correlation_id')}
    related=[row for row in rows if command in (row.get('req_id'),row.get('client_req_id'))
             or row.get('venue_correlation_id') in correlations]
    execution_ids={row.get('broker_execution_id') for row in related if row.get('broker_execution_id')}
    selected=[row for row in rows if command in (row.get('req_id'),row.get('client_req_id'))
              or row.get('venue_correlation_id') in correlations or row.get('broker_execution_id') in execution_ids]
    try:ledger=accounting.replay(selected,period,'EUR.USD',checked)
    except accounting.EvidenceError as error:
        # A send alone is an unfinished observation, not a fabricated zero fill.
        # All other native identity/economics failures remain failures.
        if str(error)!='SUBMITTED_COMMAND_WITHOUT_FILL_OR_ZERO_FILL_PROOF' or any(
                row.get('event')=='broker_execution' for row in selected):raise
        ledger={'executions':[]}
    sent=min(sends,key=lambda row:row['ts_ms'])
    need(sent['side']==case['prepared']['side'],'NATIVE_OPEN_SIDE_CONFLICT')
    need(case['times']['original_prepared_created_at_ms']<=sent['ts_ms']
         and case['times']['command_binding_emitted_at_ms']<=sent['ts_ms'],'LATE_NATIVE_SEND')
    attempts=[row for row in direct if row.get('event') in ('order_intent','place_send_attempt')
              and accounting._owns(row,period,'EUR.USD')]
    for attempt in attempts:
        need(attempt.get('req_id')==command==attempt.get('client_req_id')
             and attempt.get('venue_correlation_id')==sent['venue_correlation_id']
             and attempt.get('side')==sent['side'],'NATIVE_SEND_ATTEMPT_BINDING')
        need(type(attempt.get('ts_ms')) is int
             and case['times']['command_binding_emitted_at_ms']<=attempt['ts_ms']<=sent['ts_ms'],
             'LATE_NATIVE_SEND_ATTEMPT')
    valid_ids={value['execution_id'] for value in ledger['executions'] if value['command_id']==command}
    callbacks=[row for row in selected if row.get('event')=='broker_execution' and row.get('broker_execution_id') in valid_ids]
    first=min(callbacks,key=lambda row:row['ts_ms']) if callbacks else None
    if first is not None:
        owner=period['instruments']['EUR.USD']
        need(first.get('source') in ('ib-api-callback','agent:'+owner['agent_id'])
             and first.get('broker_callback_type')=='execDetails','NATIVE_FILL_SOURCE')
        need(case['times']['admission_fsync_completed_at_ms']<first['ts_ms'] and sent['ts_ms']<=first['ts_ms'],
             'LATE_NATIVE_FIRST_FILL')
    return {'command_id':command,'send_at_ms':sent['ts_ms'],'send_record_sha256':attribution.digest(sent),
        'first_send_attempt_at_ms':min(row['ts_ms'] for row in attempts) if attempts else None,
        'send_attempt_record_sha256':[attribution.digest(row) for row in attempts],
        'venue_correlation_id':sent['venue_correlation_id'],'order_id':sent['order_id'],
        'execution_ids':sorted(valid_ids),'first_fill_at_ms':first['ts_ms'] if first else None,
        'first_fill_record_sha256':attribution.digest(first) if first else None,
        'first_fill_time_basis':'NATIVE_OMS_EXECDETAILS_LOCAL_RECEIPT_TIME_NOT_IB_SERVER_EXECUTION_TIME',
        'ib_server_execution_time_relation_proven':False,'settlement_or_value_label_claimed':False}

def audit(manifest_path,manifest_sha256,oms_path,base_bytes,base_sha256,base_observed_at_ms,
          *,journal_files=(),initial_writer=None,checked_at_ms=None):
    manifest_path=Path(manifest_path);private_info(manifest_path)
    raw=attribution.read_private(manifest_path,65536)
    need(sha(raw)==manifest_sha256,'MANIFEST_SHA256_CHANGED');manifest=attribution.strict(raw)
    need(gate.validate_selection(manifest) is not None,'EXPLICIT_PRICE_PROFILE_REQUIRED')
    runtime=manifest['runtime_sha256'];runtime_pins={}
    for name,expected in runtime.items():
        need(Path(name).name==name and name.endswith('.py'),'RUNTIME_MEMBER_NAME')
        member=manifest_path.parent/name;private_info(member)
        actual=sha(attribution.read_private(member,1048576));need(actual==expected,'RUNTIME_SOURCE_CHANGED:'+name)
        runtime_pins[name]=actual
    auditor_sources={}
    for path in (Path(__file__),Path(gate.__file__),Path(attribution.__file__),Path(accounting.__file__)):
        with path.open('rb') as stream:source=stream.read(1048577)
        need(len(source)<=1048576,'AUDITOR_SOURCE_BOUND');auditor_sources[path.name]=sha(source)
    need(auditor_sources[gate.MODULE]==runtime[gate.MODULE],'AUDITOR_GATE_CODEC_CHANGED')
    need(auditor_sources['hepta_decision_attribution.py']==runtime['hepta_decision_attribution.py'],'AUDITOR_CAPTURE_CODEC_CHANGED')
    period_path=Path(manifest['period_path']);private_info(period_path)
    period_raw=attribution.read_private(period_path,65536)
    need(sha(period_raw)==manifest['period_sha256'],'PERIOD_CHANGED')
    period=attribution.strict(period_raw);accounting.validate_period(period)
    need(type(base_observed_at_ms) is int and 0<base_observed_at_ms<=(checked_at_ms or time.time_ns()//1000000),
         'OMS_BASE_OBSERVATION_TIME')
    journal=journal_snapshot(manifest,manifest_sha256,checked_at_ms,journal_files,initial_writer)
    root=Path(manifest['price_bootstrap_gate']['receipt_root'])/manifest_sha256
    folders={}
    if root.exists():
        need(root.resolve()==root,'PRIVATE_ROOT_ALIAS')
        for folder in root.iterdir():
            if re.fullmatch('[0-9a-f]{32}',folder.name):
                need(folder.is_dir() and folder.resolve()==folder,'PRIVATE_REQUEST_DIRECTORY')
                folders[folder.name]=folder;need(len(folders)<=MAX_REQUESTS,'PRIVATE_REQUEST_BOUND')
    for value in journal['commands'].values():
        rid=value.get('request_id');need(re.fullmatch('[0-9a-f]{32}',rid or '') is not None,'BOUND_REQUEST_ID')
        folders.setdefault(rid,root/rid)
    need(len(folders)<=MAX_REQUESTS,'PRIVATE_REQUEST_BOUND')
    # Ordinary HOLD/low-confidence observations do not justify another read of
    # the large native prefix. A stat is diagnostic, never evidence of no OPEN.
    has_command=bool(journal['commands']) or any((folder/'command-binding.json').exists() for folder in folders.values())
    rows=[];oms_pin=None;oms_info=private_info(Path(oms_path))
    need(type(base_bytes) is int and 0<=base_bytes<=attribution.MAX_TOTAL and oms_info.st_size>=base_bytes,'OMS_BASE_BOUND')
    need(re.fullmatch('[0-9a-f]{64}',base_sha256) is not None,'OMS_BASE_HASH')
    if has_command:rows,oms_pin=read_oms_range(Path(oms_path),base_bytes,base_sha256)
    checked=checked_at_ms or time.time_ns()//1000000
    actual_sends={row['req_id'] for row in rows if row.get('event')=='place_sent' and accounting._owns(row,period,'EUR.USD')}
    cases=[];joined=set()
    for rid,folder in sorted(folders.items()):
        case=None
        try:
            case=local_discard(folder,manifest_sha256)
            if case is not None:
                need(case['command_id'] not in actual_sends,'LOCAL_NO_SEND_NATIVE_SEND_CONFLICT')
            else:
                case=private_chain(folder,manifest,manifest_sha256,base_observed_at_ms)
                expected=[command for command,value in journal['commands'].items() if value.get('request_id')==rid]
                if case['status']=='PARTIAL_PRIVATE_RECEIPT':
                    if expected:case.update(status='MISSING_PREOPEN_SEAL',command_ids=expected)
                else:
                    joined.add(case['command_id'])
                    if not join_journal(case,journal):case['status']='MISSING_JOURNAL_BINDING'
                    else:
                        native=native_evidence(case,rows,period,checked);case['native']=native
                        case['status']=('WAITING_NATIVE_SEND' if native is None else
                            'WAITING_NATIVE_FIRST_FILL' if native['first_fill_at_ms'] is None else 'VERIFIED_OBSERVED')
        except BAD_INPUT_ERRORS as error:
            case=case or {'request_id':rid};reason=str(error)[:240]
            case.update(status='LATE' if reason.startswith('LATE_') else 'INVALID',reason=reason)
        for key in ('source','prepared','receipt'):case.pop(key,None)
        cases.append(case)
    unmapped=sorted(actual_sends-joined)
    verified=sum(case['status']=='VERIFIED_OBSERVED' for case in cases)
    bad=bool(unmapped) or any(case['status'] in ('INVALID','LATE','MISSING_PREOPEN_SEAL') for case in cases)
    complete=bool(actual_sends) and verified==len(actual_sends) and not bad and not journal['gaps']
    status='FAILED' if bad else 'VERIFIED_OBSERVED' if complete else 'PARTIAL' if cases or journal['gaps'] else 'WAITING'
    decisions={};eligible=0
    for value,_ in journal['decisions'].values():
        action=value.get('decision',{}).get('action','UNKNOWN');decisions[action]=decisions.get(action,0)+1
        request=(journal['requests'].get(value.get('request_id')) or ({},))[0]
        confidence=value.get('decision',{}).get('confidence')
        if (request.get('position_at_request')==0 and action in ('BUY','SELL')
                and type(confidence) in (int,float) and confidence>=.62):eligible+=1
    native_size=(oms_pin['bytes']+oms_pin['trailing_uncommitted_bytes']) if oms_pin else oms_info.st_size
    return {'schema':REPORT_SCHEMA,'status':status,'checked_at_ms':checked,'read_only':True,'auditor_sources_sha256':auditor_sources,
        'producer':{'manifest_path':str(manifest_path),'manifest_sha256':manifest_sha256,'runtime_sha256':runtime_pins,
                    'period_sha256':manifest['period_sha256'],'profile':gate.PROFILE},
        'scope':{'native_prefix_anchor_observed_at_ms':base_observed_at_ms,
                 'only_requests_strictly_after_anchor':True,'native_prefix_parsed':has_command,
                 'native_read_skipped_reason':None if has_command else 'NO_ACTUAL_COMMAND_BINDING',
                 'first_fill_is_local_native_receipt':True,'finite_observation_not_future_guarantee':True},
        'journal':{key:journal[key] for key in ('prefixes','health_pins','gaps','writer_ids','initial_writer_id','trailing_uncommitted_bytes')},
        'native':{'pinned_prefix':oms_pin,'file_size_observed':native_size,'bytes_after_anchor_observed':native_size-base_bytes,
                  'unverified_bytes_after_anchor':oms_pin['trailing_uncommitted_bytes'] if oms_pin else native_size-base_bytes,
                  'unmatched_open_command_ids':unmapped},
        'counts':{'original_requests':len(journal['requests']),'decision_actions':decisions,
                  'original_entry_eligible_decisions':eligible,'journal_open_bindings':len(journal['commands']),
                  'private_request_directories':sum(folder.is_dir() for folder in folders.values()),
                  'local_pre_submit_archives':sum(case['status']=='LOCAL_PRE_SUBMIT_NO_SEND_RECORDED' for case in cases),
                  'observed_native_open_commands':len(actual_sends) if has_command else None,
                  'verified_open_first_fill_chains':verified,
                  'observed_acceptance_fraction':verified/len(actual_sends) if actual_sends else None},
        'cases':cases,'value_coverage_credit':False,'efficacy_claim':False,'synthetic_predictions_created':False,
        'broker_orders_sent':False,'files_written':False}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True,type=Path)
    parser.add_argument('--manifest-sha256',required=True)
    parser.add_argument('--oms-journal',required=True,type=Path)
    parser.add_argument('--oms-base-bytes',required=True,type=int)
    parser.add_argument('--oms-base-sha256',required=True)
    parser.add_argument('--oms-base-observed-at-ms',required=True,type=int)
    parser.add_argument('--initial-writer-id',required=True)
    parser.add_argument('--journal-file',action='append',default=[])
    args=parser.parse_args()
    try:
        result=audit(args.manifest,args.manifest_sha256,args.oms_journal,args.oms_base_bytes,args.oms_base_sha256,
                     args.oms_base_observed_at_ms,journal_files=args.journal_file,initial_writer=args.initial_writer_id)
    except BAD_INPUT_ERRORS as error:
        result={'schema':REPORT_SCHEMA,'status':'INVALID_SOURCE','reason':str(error)[:240],'read_only':True,
                'broker_orders_sent':False,'files_written':False,'value_coverage_credit':False,'efficacy_claim':False}
    print(json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False))
    return 2 if result['status'] in ('FAILED','INVALID_SOURCE') else 0

if __name__=='__main__':raise SystemExit(main())
