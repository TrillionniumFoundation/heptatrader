"""Read-only EUR CASH exposure reconciliation, never an account-PnL assertion.

IB FX CASH trades settle into currency ledger balances; a zero securities
portfolio position count is not evidence that the EUR/USD trade is flat.
"""
from __future__ import annotations
import argparse
from decimal import Decimal,InvalidOperation
import hashlib,json,os,time
from pathlib import Path


def amount(value):
    if isinstance(value,bool) or value is None:
        raise ValueError('UNPROVED_CASH_EXPOSURE')
    try:
        val=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError) as exc:
        raise ValueError('UNPROVED_CASH_EXPOSURE') from exc
    if not val.is_finite():
        raise ValueError('UNPROVED_CASH_EXPOSURE')
    return val


def reconcile(snapshot,nav,driver,now_ms):
    if (snapshot.get('schema')!='heptatrader.pnl.v1'
        or nav.get('schema')!='heptatrader.account-nav.v1'
        or driver.get('schema')!='heptatrader.paper-driver.v1'
        or driver.get('instrument')!='EUR.USD'
        or snapshot.get('scope')!='STRATEGY_PERIOD_NOT_ACCOUNT_NAV'):
        raise ValueError('EXPOSURE_INPUT_SCOPE_INVALID')
    if (not snapshot.get('period_sha256')
        or snapshot['period_sha256']!=nav.get('period_sha256')
        or snapshot.get('journal_fingerprint')!=nav.get('journal_fingerprint')):
        raise ValueError('EXPOSURE_PERIOD_OR_JOURNAL_UNBOUND')
    account=snapshot.get('account',{}).get('account')
    if not account or account!=nav.get('account'):
        raise ValueError('EXPOSURE_BROKER_ACCOUNT_MISMATCH')
    if (nav.get('authoritative') is not True or
            nav.get('positions_complete') is not True or
            nav.get('source')!='IB_ACCOUNT_UPDATES_MULTI_PAIRED_LEDGER'):
        raise ValueError('EXPOSURE_ACCOUNT_SOURCE_UNREADY')
    for key,doc in (('pnl',snapshot),('nav',nav)):
        observed=doc.get('observed_at_ms')
        if type(observed) is not int or not 0<=now_ms-observed<=60000:
            raise ValueError('EXPOSURE_'+key.upper()+'_STALE_OR_FUTURE')
    components=[r for r in snapshot.get('components',[]) if r.get('instrument')=='EUR.USD']
    if len(components)!=1:raise ValueError('EXPOSURE_FX_COMPONENT_AMBIGUOUS')
    fx=components[0]
    witness=snapshot.get('observation_details',{}).get('EUR.USD')
    if (not isinstance(witness,dict) or
            witness.get('authoritative') is not True or
            witness.get('position_scope')!='PAPER_BASELINE_DELTA' or
            fx.get('ledger_complete') is not True):
        raise ValueError('EXPOSURE_EXECUTION_WITNESS_UNREADY')
    if (fx.get('settlement_pending_command_ids_total',0)!=0 or
        (witness.get('active_order_ids') or []) or
        (witness.get('unsettled_native_command_ids') or [])):
        raise ValueError('EXPOSURE_PENDING_BROKER_ORDERS')
    eur=(nav.get('ledgers') or {}).get('EUR')
    if not isinstance(eur,dict) or eur.get('CashBalance') is None:
        raise ValueError('EXPOSURE_EUR_CASH_LEDGER_MISSING')
    amounts={
        'nav_eur_cash_balance':amount(eur['CashBalance']),
        'pnl_broker_position':amount(fx.get('broker_position')),
        'pnl_strategy_position':amount(fx.get('position')),
        'execution_observation':amount(witness.get('quantity')),
        'strategy_driver_state':amount(driver.get('position')),
    }
    if len(set(amounts.values()))!=1:
        raise ValueError('EXPOSURE_EUR_CASH_STRATEGY_MISMATCH')
    if amount(snapshot.get('account_nav'))!=amount(nav.get('nav')):
        raise ValueError('EXPOSURE_NAV_COHORT_MISMATCH')
    return {
        'schema':'heptatrader.fx-cash-exposure-audit.v1',
        'state':'EXPOSURE_MATCH_READ_ONLY_NOT_ATOMIC_ACCOUNT_RETURN',
        'eur_cash_balance':str(amounts['nav_eur_cash_balance']),
        'compared_sources':sorted(amounts),
        'account_identifier_emitted':False,
        'securities_positions_count':len(nav.get('positions',[])),
        'securities_positions_not_fx_cash_truth':True,
        'broker_valuation_tick_attested':nav.get('capture_cohort',{}).get(
            'broker_valuation_tick_attested') is True,
        'pnl_complete':snapshot.get('complete') is True,
        'unrealized_pnl_qualified':fx.get('valuation_complete') is True,
        'quote_error':witness.get('quote_error'),
        'trade_authority':False,
        'broker_orders_sent':0,
        'production_mutation':False,
    }


def read_exact_input(path):
    # Mutable producer files: bind parsed contents and SHA to one byte read.
    with path.open('rb') as stream:
        raw=stream.read(512*1024+1)
    if len(raw)>512*1024:raise ValueError('EXPOSURE_INPUT_SIZE_BOUND')
    def unique(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('EXPOSURE_DUPLICATE_JSON_KEY')
            result[key]=value
        return result
    def forbid_nonfinite(_):
        raise ValueError('EXPOSURE_NONFINITE_JSON')
    document=json.loads(raw,object_pairs_hook=unique,
                        parse_constant=forbid_nonfinite)
    if not isinstance(document,dict):raise ValueError('EXPOSURE_INPUT_OBJECT_REQUIRED')
    return document,hashlib.sha256(raw).hexdigest()


def main():
    ap=argparse.ArgumentParser()
    for flag in ('snapshot','nav','driver','output'):
        ap.add_argument('--'+flag,required=True,type=Path)
    args=ap.parse_args()
    sources={'snapshot':args.snapshot,'nav':args.nav,'driver':args.driver}
    loaded={key:read_exact_input(path) for key,path in sources.items()}
    obj={key:pair[0] for key,pair in loaded.items()}
    receipt=reconcile(obj['snapshot'],obj['nav'],obj['driver'],int(time.time()*1000))
    receipt['input_sha256']={key:pair[1] for key,pair in loaded.items()}
    args.output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(args.output,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:
        json.dump(receipt,f,sort_keys=True,indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    print(json.dumps({'state':receipt['state'],
                      'eur_cash_balance':receipt['eur_cash_balance'],
                      'unrealized_pnl_qualified':receipt['unrealized_pnl_qualified'],
                      'broker_orders_sent':0}))


if __name__=='__main__':
    main()
