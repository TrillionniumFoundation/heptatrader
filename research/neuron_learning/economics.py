"""Read committed OMS economics using the existing HeptaTrader replay owner code.

No broker connection, order, permit or token read. Native original-command and
commission-epoch checks remain in hepta_pnl_accounting; this does not invent them.
"""
from pathlib import Path
import hashlib
from decimal import Decimal
from data import prefix,strict,digest


def replay_economics(config,now,settlements):
    import hepta_pnl_accounting as accounting
    period_raw=Path(config['period_path']).read_bytes()
    if hashlib.sha256(period_raw).hexdigest()!=config['period_sha256']:
        raise ValueError('ACCOUNTING_PERIOD_CHANGED_STOP_LEARNING')
    period=strict(period_raw);accounting.validate_period(period)
    raw,meta=prefix(config['oms_path'])
    result={'schema':'heptatrader.neuron-economics-bridge.v1','period_sha256':config['period_sha256'],
            'source_prefix':meta,'fee_model':'EXISTING_COMMITTED_OMS_COMMISSION_REPLAY',
            'pnl_reward_ready':False,'net_reward':None,'effect_authority':False}
    if meta['trailing_uncommitted_bytes']:
        return {**result,'reason':'OMS_INCOMPLETE_TAIL'}
    try:
        events=[strict(line) for line in raw.splitlines()]
        if len(events)>100000:raise ValueError('OMS_EVENT_BOUND')
        report=accounting.replay(events,period,config['instrument'],now)
        out={k:report[k] for k in ('fees_complete','fee_missing_execution_ids','fee_conflicting_execution_ids',
             'fees_known_by_currency','settlement_pending_command_ids','executions','command_count','execution_count')}
        book=report['book']
        out['book']={k:str(v) for k,v in vars(book).items() if isinstance(v,(Decimal,int,float))}
        observed_commands={s.get('reconciliation',{}).get('command_id') for s in settlements}
        executed_commands={e['command_id'] for e in report['executions']}
        out['all_executed_commands_joined_to_exported_settlement']=executed_commands<=observed_commands
        ready=(report['execution_count']>=2 and report['command_count']>=2 and report['fees_complete']
               and not report['settlement_pending_command_ids'] and book.quantity==0
               and executed_commands<=observed_commands)
        # Preserve actual numeric economics; no zero reward for missing execution.
        result.update(out,pnl_reward_ready=ready,
                      reason='COMPLETE_CLOSED_PAPER_ECONOMICS' if ready else 'NO_COMPLETE_SETTLED_PAPER_REWARD')
        if ready:
            result['net_reward']=str(book.realized-book.paid_fees)
            result['net_reward_basis']='CLOSED_PAPER_EXECUTION_AFTER_CONFIRMED_COMMISSIONS_NOT_ACCOUNT_NAV'
        result['economics_digest']=digest(out)
        return result
    except (accounting.EvidenceError,ValueError,KeyError,TypeError) as exc:
        return {**result,'reason':str(exc),'fees_complete':False}
