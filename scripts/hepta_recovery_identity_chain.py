#!/usr/bin/env python3
"""Read-only join: original OMS command -> Broker orderRef/permId/execId.

An absent historical execution is UNKNOWN, not proof of non-fill. Matching
this capture does not certify a runtime restart, freeze or common NAV mark.
"""
from __future__ import annotations
import argparse
import base64
from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from hepta_broker_recovery_snapshot import private_bytes, private_execution_journal_bytes, save, sha


def correlation(order_ref):
    if not isinstance(order_ref,str) or not re.fullmatch(r'H1[A-Za-z0-9_-]{43}',order_ref):
        raise ValueError('ORDER_REF_INVALID')
    data=base64.b64decode(order_ref[2:]+'=',altchars=b'-_',validate=True)
    if len(data)!=32 or base64.urlsafe_b64encode(data).decode().rstrip('=')!=order_ref[2:]:
        raise ValueError('ORDER_REF_NONCANONICAL')
    return 'hepta-v1-sha256:'+data.hex()


def number(x):
    if isinstance(x,bool) or not isinstance(x,(str,int,float,Decimal)):
        raise ValueError('FINITE_ECONOMIC_VALUE_REQUIRED')
    try:d=Decimal(str(x))
    except InvalidOperation:raise ValueError('FINITE_ECONOMIC_VALUE_REQUIRED') from None
    if not d.is_finite():raise ValueError('FINITE_ECONOMIC_VALUE_REQUIRED')
    return d


def identity(e):
    return (e.get('account'),e.get('execution_domain'),e.get('req_id'),e.get('client_req_id'),
            e.get('trace_id'),e.get('request_hash'),e.get('venue_correlation_id'))


def proof(broker, journal, period):
    required={'open','completed','positions','ledger','aggregate','EUR.USD','BTC.USD'}
    if (broker.get('schema')!='heptatrader.broker-recovery-download.v1' or
        broker.get('account')!=period.get('account') or period.get('execution_domain')!='PAPER' or
        broker.get('transport_complete') is not True or set(broker.get('ends',{}))!=required or
        broker.get('period_unchanged') is not True or broker.get('journal_stable') is not True or
        type(broker.get('order_sends')) is not int or broker['order_sends']!=0 or
        type(broker.get('quote_subscriptions')) is not int or broker['quote_subscriptions']!=0):
        raise ValueError('INCOMPLETE_OR_MOVING_RECOVERY_COHORT')
    for at in broker['ends'].values():
        if type(at) is not int or not broker['started_at_ms']<=at<=broker['observed_at_ms']:
            raise ValueError('END_OUTSIDE_COHORT')
    events=broker['events'];executions=[e for e in events if e['kind']=='execution']
    sends=[j for j in journal if j.get('event') in ('place_sent','flatten_sent')]
    native=[j for j in journal if j.get('event')=='broker_execution']
    result=[];seen_exec={};seen_perm={};covered=set();conflicts=[]
    completed=[e for e in events if e['kind']=='completed_order']
    for e in executions:
        try:
            contract=e['contract'];instrument=contract['symbol']+'.'+contract['currency'];owner=period['instruments'][instrument]
            if e['account']!=period['account'] or e['client_id']!=owner['broker_client_id']:
                raise ValueError('BROKER_OWNER_MISMATCH')
            if contract['secType']!=('CASH' if instrument=='EUR.USD' else 'CRYPTO') or type(contract['conId']) is not int or contract['conId']<=0:
                raise ValueError('BROKER_CONTRACT_MISMATCH')
            if type(e['permanent_order_id']) is not int or e['permanent_order_id']<=0 or not e['execution_id']:
                raise ValueError('PERMANENT_OR_EXECUTION_ID_MISSING')
            corr=correlation(e['order_ref'])
            ek=(e['execution_id'],e['account']); semantic=(e['client_id'],e['order_id'],e['permanent_order_id'],corr,
                instrument,contract['conId'],e['side'],number(e['shares']),number(e['price']),number(e['cumulative_quantity']))
            if ek in seen_exec:
                if seen_exec[ek]!=semantic:raise ValueError('CONFLICTING_EXECUTION_REPLAY')
                continue
            seen_exec[ek]=semantic
            pk=(e['account'],e['permanent_order_id'])
            if pk in seen_perm and seen_perm[pk]!=(e['client_id'],e['order_id'],corr):
                raise ValueError('PERMANENT_ORDER_ALIAS_CONFLICT')
            seen_perm[pk]=(e['client_id'],e['order_id'],corr)
            originals=[s for s in sends if s.get('account')==e['account'] and s.get('venue_correlation_id')==corr and s.get('order_id')==e['order_id']]
            if len(originals)!=1:raise ValueError('ORIGINAL_SEND_NOT_UNIQUE')
            original=originals[0];key=identity(original)
            if (any(not isinstance(x,str) or not x for x in key) or original['execution_domain']!='PAPER' or
                    original.get('venue')!='IB' or original.get('instrument')!=instrument):
                raise ValueError('ORIGINAL_COMMAND_SCOPE_INVALID')
            if original['side']!=('BUY' if e['side']=='BOT' else 'SELL' if e['side']=='SLD' else ''):
                raise ValueError('ORIGINAL_SIDE_MISMATCH')
            if (original.get('source') not in ('agent:'+owner['agent_id'],'agent.tool:'+owner['agent_id']) or
                    original['trace_id']!=owner['session_id']):
                raise ValueError('ORIGINAL_REGISTERED_OWNER_MISMATCH')
            intents=[j for j in journal if j.get('event') in ('order_intent','flatten_intent') and identity(j)==key]
            if len(intents)!=1:
                raise ValueError('ORIGINAL_INTENT_HASH_NOT_UNIQUE')
            counterparts=[j for j in native if j.get('broker_execution_id')==e['execution_id'] and
                          j.get('account')==e['account'] and j.get('broker_econ_permanent_order_id')==e['permanent_order_id']]
            if not counterparts:raise ValueError('NATIVE_EXECUTION_PROOF_MISSING')
            for j in counterparts:
                if (any(j.get(k)!=original.get(k) for k in ('account','execution_domain','req_id','client_req_id','trace_id','venue_correlation_id')) or
                    (j.get('request_hash') not in ('',original['request_hash'])) or
                    j.get('source') not in ('agent:'+owner['agent_id'],'agent.tool:'+owner['agent_id']) or
                    j.get('order_id')!=e['order_id'] or j.get('broker_client_id')!=e['client_id'] or
                    type(j.get('broker_econ_version')) is not int or j['broker_econ_version']!=1 or
                    j.get('broker_econ_kind')!='EXECUTION' or
                    not (type(j.get('broker_econ_valid')) is int and j['broker_econ_valid']==1) or j.get('broker_econ_contract_id')!=contract['conId'] or
                    number(j.get('broker_econ_shares'))!=number(e['shares']) or
                    number(j.get('broker_econ_execution_price'))!=number(e['price']) or
                    number(j.get('broker_econ_cumulative_quantity'))!=number(e['cumulative_quantity'])):
                    raise ValueError('NATIVE_BROKER_EXECUTION_CONFLICT')
            terminal=[c for c in completed if c['account']==e['account'] and c['permanent_order_id']==e['permanent_order_id'] and c['order_ref']==e['order_ref']]
            terminal_status=None
            if terminal:
                if any(c['contract']['conId']!=contract['conId'] or c['status']!=terminal[0]['status'] for c in terminal):
                    raise ValueError('COMPLETED_ORDER_CONFLICT')
                terminal_status=terminal[0]['status']
                # CompletedOrders may have local orderId/clientId==0. Never use
                # those missing local fields to change the original ownership.
            covered.add(key)
            result.append({'instrument':instrument,'order_id':e['order_id'],'permanent_order_id':e['permanent_order_id'],
                'execution_id':e['execution_id'],'command_identity_sha256':sha(json.dumps(key).encode()),
                'original_send_event':original['event'],'original_send_count':1,'native_callback_matches':len(counterparts),
                'completed_status':terminal_status,'order_ref_exact':True,'economic_fields_exact':True,
                'request_hash_source':'MATCHED_ORIGINAL_INTENT_AND_SEND',
                'native_callback_request_hash_present':all(bool(j.get('request_hash')) for j in counterparts)})
        except (ValueError,KeyError,TypeError) as error:
            conflicts.append({'execution_id':e.get('execution_id'),'reason':str(error) if isinstance(error,ValueError) else 'MALFORMED_PROOF'})
    positions=[e for e in events if e['kind']=='position']
    btc=[e for e in positions if e['account']==period['account'] and e['contract']['symbol']=='BTC' and e['contract']['secType']=='CRYPTO' and e['contract']['currency']=='USD']
    if len(btc)>1:conflicts.append({'reason':'BTC_POSITION_SCOPE_AMBIGUOUS'})
    cash=[e for e in events if e['kind']=='account_value' and e['request_id']==7302 and e['key'].removeprefix('$LEDGER-')=='CashBalance' and e['currency']=='EUR']
    if len(cash)!=1:conflicts.append({'reason':'EUR_CASH_SCOPE_MISSING_OR_AMBIGUOUS'})
    # Financial values remain in the private result. Virtual CASH positions
    # are kept distinct from actual currency cash and strategy-owned deltas.
    return {'schema':'heptatrader.original-command-broker-chain.v1',
        'raw_hash_bound_by_caller':False,'verified_execution_count':len(result),'conflicts':conflicts,
        'matched_original_commands':len(covered),'original_sent_records':len(sends),
        'sent_commands_outside_returned_execution_scope':len({identity(s) for s in sends}-covered),
        'outside_scope_is_zero_fill':False,'execution_proofs':result,
        'current_open_orders':sum(e['kind']=='open_order' for e in events),
        'btc_physical_position':str(number(btc[0]['quantity'])) if len(btc)==1 else ('0' if not btc else None),
        'eur_currency_cash':str(number(cash[0]['value'])) if len(cash)==1 else None,
        'virtual_cash_position_is_strategy_position':False,'position_source':'COMPLETED_EXACT_ACCOUNT_POSITION_MULTI',
        'owner_transfer_performed':False,'candidate_full_runtime_restarted':False,
        'all_historical_orders_reconciled':False,'full_runtime_recovery_qualified':False,
        'strict_nav_eligible':False,'order_sends':0}




def verify_journal_coherence(before: bytes, after: bytes, broker: dict, plan: dict) -> None:
    if (sha(before)!=plan['journal_before_sha256'] or
        sha(before)!=broker['journal_before_sha256'] or
        sha(after)!=broker['journal_after_sha256'] or before!=after or
        broker.get('journal_stable') is not True):
        raise ValueError('JOURNAL_HASH_OR_STABILITY_MISMATCH')


def checkpoint_alignment(text: str, account: str, eur_cash: str) -> dict:
    lines=text.splitlines()
    if not lines or lines[0]!='HFXR1' or len(lines)!=2:
        raise ValueError('EXACT_SINGLE_CASH_CHECKPOINT_REQUIRED')
    fields=lines[1].split('|')
    if len(fields)!=9 or 'sha256:'+sha('|'.join(fields[:8]).encode())!=fields[8]:
        raise ValueError('CASH_CHECKPOINT_DIGEST_MISMATCH')
    if fields[:3]!=[account,'EUR.USD','EUR'] or re.fullmatch(r'sha256:[0-9a-f]{64}',fields[7]) is None:
        raise ValueError('CASH_CHECKPOINT_SCOPE_MISMATCH')
    baseline,observed,delta=map(number,fields[3:6])
    if not fields[6].isdigit() or int(fields[6])<=0 or abs(baseline+delta-observed)>Decimal('0.000001'):
        raise ValueError('CASH_CHECKPOINT_ALGEBRA_INVALID')
    return {'schema':'heptatrader.captured-cash-checkpoint-alignment.v1',
        'native_checkpoint_digest_valid':True,'original_anchor_proof':fields[7],
        'native_baseline_cash':str(baseline),'native_observed_cash':str(observed),
        'native_campaign_delta':str(delta),'checkpoint_observed_at_ms':int(fields[6]),
        'independent_broker_cash_matches_native_checkpoint':abs(number(eur_cash)-observed)<=Decimal('0.000001'),
        'original_anchor_authenticated_by_this_check':False,
        'candidate_restart_position_continuity_proven':False,'strict_nav_eligible':False}


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--capture',type=Path,required=True)
    ap.add_argument('--raw-sha256',required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args(argv)
    raw_bytes=private_bytes(a.capture/'broker-raw.json');plan=json.loads(private_bytes(a.capture/'plan.json'))
    if sha(raw_bytes)!=a.raw_sha256:raise ValueError('RAW_HASH_MISMATCH')
    broker=json.loads(raw_bytes);journal_bytes=private_execution_journal_bytes(a.capture/'journal-before.jsonl')
    after_bytes=private_execution_journal_bytes(a.capture/'journal-after.jsonl')
    verify_journal_coherence(journal_bytes,after_bytes,broker,plan)
    pb=private_bytes(Path.home()/'.config/heptatrader/pnl-period.json')
    if sha(pb)!=plan['period_sha256'] or broker['period_sha256']!=plan['period_sha256']:
        raise ValueError('PERIOD_HASH_MISMATCH')
    result=proof(broker,[json.loads(l) for l in journal_bytes.splitlines()],json.loads(pb))
    result.update(raw_hash_bound_by_caller=True,raw_sha256=sha(raw_bytes),journal_sha256=sha(journal_bytes),
                  validator_sha256=sha(Path(__file__).read_bytes()))
    checkpoint=a.capture/'fx-checkpoint-before.txt'
    result['native_checkpoint_alignment']=None
    if checkpoint.exists():
        cb=private_bytes(checkpoint,16384)
        ca=private_bytes(a.capture/'fx-checkpoint-after.txt',16384)
        if (cb!=ca or sha(ca)!=broker.get('fx_checkpoint_after_sha256') or
            sha(cb)!=plan.get('fx_checkpoint_before_sha256') or
            sha(cb)!=broker.get('fx_checkpoint_before_sha256') or
            broker.get('fx_checkpoint_stable') is not True):
            raise ValueError('CASH_CHECKPOINT_SOURCE_MOVED')
        result['native_checkpoint_alignment']=checkpoint_alignment(cb.decode('utf8'),broker['account'],result['eur_currency_cash'])
    save(a.output,result)
    print(json.dumps({k:result[k] for k in ('verified_execution_count','matched_original_commands','original_sent_records',
        'sent_commands_outside_returned_execution_scope','current_open_orders','conflicts','full_runtime_recovery_qualified','strict_nav_eligible')},sort_keys=True))
    return 0 if not result['conflicts'] and result['matched_original_commands'] else 2


if __name__=='__main__':raise SystemExit(main())
