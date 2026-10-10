"""Explain frozen EUR/USD validation non-admission without rescoring an unseen test.

Only previously observed, revised IB historical bars and already calibrated
PAPER cost evidence are used. This never sends orders or promotes models.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path

import numpy as np

from .eurusd_walkforward_cost import (
    DAY, EMBARGO, TEST_DAYS, THRESHOLDS, TRAIN_DAYS, VALID_DAYS,
    features, predict, regression, simulate, verify_inputs,
)
from laya_cell.integrity import read_json, sha_file

MIN_VALIDATION_TRADES = 30


def threshold_frontier(at, predictions, returns, cost_bps):
    """Use the identical non-overlapping simulation for gross and net."""
    points = []
    for t in THRESHOLDS:
        gross = simulate(at, predictions, returns, t, 0.)
        net = simulate(at, predictions, returns, t, cost_bps)
        if gross['trades'] != net['trades']:
            raise ValueError('SIMULATOR_TRADE_IDENTITY_CHANGED')
        points.append({
            'threshold_bps': t,
            'trades': net['trades'],
            'gross_usd_hypothetical': gross['net_usd'],
            'net_usd_hypothetical': net['net_usd'],
            'cost_usd_hypothetical': round(gross['net_usd']-net['net_usd'],2),
            'qualifies_positive_validation': (
                net['trades'] >= MIN_VALIDATION_TRADES and net['net_usd']>0
            ),
            'small_sample_positive_not_qualified': (
                0 < net['trades'] < MIN_VALIDATION_TRADES
                and net['net_usd']>0
            ),
        })
    return points


def evaluate(bars, coverage, cost):
    spread, _stress, commission = verify_inputs(bars, coverage, cost)
    # Exactly the original validation fee/slippage scenario.
    cost_bps = spread + 2*commission + 2.0
    feat=features(bars['bars'])
    X, at, mature, y=(feat[key] for key in ('x','at','mature','returns'))
    first=int(at.min())//DAY*DAY
    folds=[]
    for index in range(40):
        start=first+index*TEST_DAYS*DAY
        tr_end=start+TRAIN_DAYS*DAY
        val_start=tr_end+EMBARGO
        val_end=val_start+VALID_DAYS*DAY
        test_start=val_end+EMBARGO
        test_end=test_start+TEST_DAYS*DAY
        if test_end>int(mature.max()):
            break
        tr=(at>=start)&(mature<tr_end-EMBARGO)
        va=(at>=val_start)&(mature<val_end-EMBARGO)
        te=(at>=test_start)&(mature<test_end)
        if int(tr.sum())<5000 or int(va.sum())<500 or int(te.sum())<300:
            raise ValueError('WALKFORWARD_ROW_BUDGET')
        # Training only uses its own mature labels, validation determines choice.
        model=regression(X[tr],y[tr])
        pred=predict(model,X[va])
        points=threshold_frontier(at[va], pred, y[va],cost_bps)
        eligible=[r for r in points if r['qualifies_positive_validation']]
        original_choice=max(
            eligible,key=lambda r:(r['net_usd_hypothetical'],
                                   -r['threshold_bps'])
        )['threshold_bps'] if eligible else None
        folds.append({
            'fold':index,
            'training_rows':int(tr.sum()),
            'validation_rows':int(va.sum()),
            'test_rows_count_only':int(te.sum()),
            'trained_labels_matured_before_cutoff':bool(
                (mature[tr]<tr_end-EMBARGO).all()
            ),
            'selected_threshold_bps':original_choice,
            'validation':points,
        })
    if len(folds)<2:
        raise ValueError('INSUFFICIENT_OOS_FOLDS')
    return {
        'schema':'heptatrader.eurusd-validation-cost-frontier.v1',
        'status':'FROZEN_PREVIOUSLY_VIEWED_VALIDATION_ONLY_NO_NEW_EFFICACY',
        'dataset_role':'REVISED_IB_HISTORICAL_NOT_PAST_LIVE_QUOTE',
        'price_fill_model':'NEXT_BAR_MID_OPEN_HYPOTHETICAL',
        'scorer':'SAME_DETERMINISTIC_RIDGE_AS_FROZEN_20261010',
        'min_validation_trades':MIN_VALIDATION_TRADES,
        'validation_cost_bps':round(cost_bps,7),
        'folds':folds,
        'all_folds_choose_no_trade':all(r['selected_threshold_bps'] is None for r in folds),
        'test_labels_consulted_for_selection':False,
        'new_independent_holdout_claim':False,
        'trade_authority':False,
        'actor_updated':False,
        'promotion_allowed':False,
        'broker_orders_sent':0,
    }


def main():
    p=argparse.ArgumentParser()
    for k in ('bars','coverage','cost','output'):
        p.add_argument('--'+k,required=True,type=Path)
    a=p.parse_args()
    b=read_json(a.bars,64*1024**2)
    v=read_json(a.coverage,1024**2)
    c=read_json(a.cost,1024**2)
    r=evaluate(b,v,c)
    r['input_sha256']={
        'bars':sha_file(a.bars),
        'coverage':sha_file(a.coverage),
        'cost':sha_file(a.cost),
    }
    a.output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(a.output,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:
        json.dump(r,f,indent=2,sort_keys=True)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    print(json.dumps({
        'status':r['status'],
        'folds':len(r['folds']),
        'all_folds_choose_no_trade':r['all_folds_choose_no_trade'],
        'cost_bps':r['validation_cost_bps'],
        'orders_sent':0,
    }))


if __name__=='__main__':
    main()
