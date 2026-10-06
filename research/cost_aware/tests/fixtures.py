from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from hepta_cost_policy import *
import copy

NOW = 1791260000000

def quote(at=NOW, instrument='BTC.USD', mid='100', spread='0'):
    m, h = dec(mid), dec(spread) / 2
    return {'source':'IB', 'authoritative':True, 'instrument':instrument, 'subscription_id':'IB:7:1:1000001',
            'subscription_state':'active','observed_at_ms':at,'stale_after_ms':at+5000,'stale':False,
            'bid':str(m-h),'ask':str(m+h)}

def forecast(at=NOW, instrument='BTC.USD', action='BUY', move='80', mid='100', horizon=300):
    m=dec(move)
    f={'action':action,'confidence':.8,'rationale':'SYNTHETIC UNIT FIXTURE', 'horizon_sec':horizon,
       'scenarios':[{'probability':'.5','move_bps':str(m-2)},{'probability':'.5','move_bps':str(m+2)}],
       'invalidation_price':str(dec(mid)* (D('.999') if action=='BUY' else D('1.001'))) if action!='HOLD' else '0',
       'forecast_version':FORECAST_VERSION,'model_id':MODEL_ID,'issued_at_ms':at,
       'reference_mid':str(mid),'instrument':instrument,'position_neutral':True}
    validate_forecast(f)
    return f

def samples(instrument='BTC.USD', action='BUY', count=40):
    rows=[]
    for i in range(count):
        at=NOW-4*86400000+i*2*3600000
        move=(20 if i%2 else 100)*(1 if action=='BUY' else -1)
        f=forecast(at,instrument,action,str(move))
        end=at+f['horizon_sec']*1000
        rows.append({'forecast':f, 'label_observed_at_ms':end,'label_available_at_ms':end+1,
                     'forecast_available_at_ms':at+1,'source_kind':'SYNTHETIC',
                     'realized_underlying_move_bps':str(move-(5 if action=='BUY' else -5))})
    return rows

def artifact(instrument='BTC.USD', action='BUY', *, synthetic=True):
    rows=samples(instrument,action)
    if not synthetic:
        # Test-double source flag only. Never installed/promoted to an operator path.
        for row in rows: row['source_kind']='IB_CAPTURE'
    return build_calibration(rows,NOW-1000)

def reseal(a):
    body={k:v for k,v in a.items() if k!='calibration_id'}
    a['calibration_id']=digest(body)
    return a
