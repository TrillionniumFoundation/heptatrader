"""BTC-only opening economics and anti-churn controls; never an exit policy.

Upfront IB/Paxos 18bps and minimum USD1.75 are conservative cash-charge assumptions.
No future tier/1%-cap rebates are credited before receipt. Spread is counted once.
Cost units, model forecast age and reservations are explicit and deterministic.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
import hashlib,json

D=Decimal
PROFILE={"schema":"heptatrader.btc-entry-profile.v1","instrument":"BTC.USD","execution_mode":"PAPER",
 "confidence_min":"0.62","commission_rate":"0.0018","commission_min_usd":"1.75",
 "slippage_roundtrip_bps":"2","minimum_net_edge_bps":"2","quote_ttl_ms":5000,
 "forecast_ttl_ms":60000,"minimum_entry_interval_ms":1800000,"after_flat_cooldown_ms":1800000,
 "rolling_entry_window_ms":3600000,"maximum_opens_per_window":2,"max_cash_usd":"25000",
 "rebates_assumed":False,"exit_controls_changed":False}
HORIZONS=(900,1800,3600)
FIELDS={'action','confidence','rationale','expected_move_bps','horizon_sec','issued_at_ms',
        'available_at_ms','reference_mid','input_sha256','model_id','forecast_schema'}
class PolicyError(ValueError):pass

def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(x):return hashlib.sha256(canonical(x).encode()).hexdigest()
def number(value):
 if isinstance(value,bool) or value is None:raise PolicyError('BTC_NUMBER_INVALID')
 try:n=D(str(value))
 except (InvalidOperation,ValueError,TypeError):raise PolicyError('BTC_NUMBER_INVALID') from None
 if not n.is_finite():raise PolicyError('BTC_NUMBER_NONFINITE')
 return n

def validate_forecast(f):
 if not isinstance(f,dict) or set(f)!=FIELDS:raise PolicyError('BTC_FORECAST_SCHEMA')
 if f['forecast_schema']!='heptatrader.btc-return-forecast.v1' or f['model_id']!='gpt-6-luna':raise PolicyError('BTC_MODEL_IDENTITY')
 if f['action'] not in ('BUY','SELL','HOLD') or type(f['horizon_sec'])is not int or f['horizon_sec'] not in HORIZONS:raise PolicyError('BTC_DIRECTION_OR_HORIZON')
 if not isinstance(f['rationale'],str) or not 1<=len(f['rationale'])<=2000:raise PolicyError('BTC_RATIONALE_BOUND')
 if type(f['confidence']) not in (int,float) or type(f['expected_move_bps']) not in (int,float):raise PolicyError('BTC_FORECAST_NUMERIC_TYPE')
 if not 0<=number(f['confidence'])<=1 or abs(number(f['expected_move_bps']))>1000:raise PolicyError('BTC_FORECAST_BOUND')
 if not 0<number(f['reference_mid'])<D('1e12'):raise PolicyError('BTC_REFERENCE_MID')
 if any(type(f[k])is not int or f[k]<=0 for k in ('issued_at_ms','available_at_ms')) or f['available_at_ms']<f['issued_at_ms']:
  raise PolicyError('BTC_FORECAST_CLOCK')
 if not isinstance(f['input_sha256'],str) or len(f['input_sha256'])!=64 or any(c not in '0123456789abcdef' for c in f['input_sha256']):raise PolicyError('BTC_INPUT_IDENTITY')
 return f

def validate_control(c,now):
 if not isinstance(c,dict) or c.get('schema')!='heptatrader.btc-entry-control.v1':raise PolicyError('BTC_CONTROL_REQUIRED')
 events=c.get('open_reservations')
 if not isinstance(events,list) or len(events)>32:raise PolicyError('BTC_RESERVATION_BOUND')
 seen=set()
 for e in events:
  if not isinstance(e,dict) or set(e)!={'command_id','prepared_at_ms'} or not isinstance(e['command_id'],str) or not e['command_id'] or len(e['command_id'])>160:raise PolicyError('BTC_RESERVATION_IDENTITY')
  if e['command_id'] in seen:raise PolicyError('BTC_DUPLICATE_RESERVATION')
  seen.add(e['command_id'])
  if type(e['prepared_at_ms'])is not int or not 0<e['prepared_at_ms']<=now:raise PolicyError('BTC_CONTROL_CLOCK_REGRESSION')
 t=c.get('last_flat_at_ms',0)
 if type(t)is not int or not 0<=t<=now:raise PolicyError('BTC_CONTROL_CLOCK_REGRESSION')
 return c

def reserve(c,command,now):
 validate_control(c,now)
 for e in c['open_reservations']:
  if e['command_id']==command:return
 c['open_reservations']=[e for e in c['open_reservations'] if e['prepared_at_ms']>now-PROFILE['rolling_entry_window_ms']]
 c['open_reservations'].append({'command_id':command,'prepared_at_ms':now})
 validate_control(c,now)

def evaluate(f,q,control,now,cash='25000',*,ignore_command=None):
 reasons=[]
 result={'schema':'heptatrader.btc-entry-check.v1','profile_sha256':digest(PROFILE),'at_ms':now,
         'allowed':False,'reasons':reasons,'cost_basis':'UPFRONT_ASSUMPTION_NOT_ACCOUNT_FEE_RECEIPT',
         'rebate_credit_usd':'0','only_applies_to':'NEW_BTC_LONG','confidence_is_win_probability':False}
 try:
  if type(now)is not int or now<=0:raise PolicyError('BTC_EVALUATION_CLOCK')
  validate_forecast(f);validate_control(control,now)
  if f['action']!='BUY':reasons.append('BTC_NOT_BUY')
  if number(f['confidence'])<number(PROFILE['confidence_min']):reasons.append('BTC_CONFIDENCE_BELOW_062')
  if f['issued_at_ms']>now or f['available_at_ms']>now or now-f['issued_at_ms']>PROFILE['forecast_ttl_ms']:reasons.append('BTC_FORECAST_STALE')
  if (not isinstance(q,dict) or q.get('source')!='IB' or q.get('authoritative') is not True or q.get('instrument')!='BTC.USD' or q.get('stale') is not False):raise PolicyError('BTC_QUOTE_NOT_AUTHORITATIVE')
  t=q.get('observed_at_ms');end=q.get('stale_after_ms')
  if type(t)is not int or type(end)is not int or not 0<t<=now<=min(end,t+PROFILE['quote_ttl_ms']):raise PolicyError('BTC_QUOTE_STALE')
  bid=number(q['bid']);ask=number(q['ask'])
  if not 0<bid<=ask<D('1e12'):raise PolicyError('BTC_QUOTE_PRICE')
  n=number(cash)
  if not 0<n<=number(PROFILE['max_cash_usd']):raise PolicyError('BTC_CASH_BOUND')
  reservations=[e for e in control['open_reservations'] if e['command_id']!=ignore_command]
  recent=[e for e in reservations if e['prepared_at_ms']>now-PROFILE['rolling_entry_window_ms']]
  if len(recent)>=PROFILE['maximum_opens_per_window']:reasons.append('BTC_ROLLING_OPEN_LIMIT')
  if reservations and now-max(e['prepared_at_ms'] for e in reservations)<PROFILE['minimum_entry_interval_ms']:reasons.append('BTC_ENTRY_INTERVAL')
  if control['last_flat_at_ms'] and now-control['last_flat_at_ms']<PROFILE['after_flat_cooldown_ms']:reasons.append('BTC_AFTER_FLAT_COOLDOWN')
  target=number(f['reference_mid'])*(1+number(f['expected_move_bps'])/10000)
  # Entry at current ask, predicted exit at future mid minus current half-spread.
  # This already charges the full spread; it is NOT subtracted a second time.
  exit_bid=target-(ask-bid)/2
  if exit_bid<=0:raise PolicyError('BTC_TARGET_PRICE')
  qty=n/ask;exit_notional=qty*exit_bid
  fee_in=max(number(PROFILE['commission_min_usd']),n*number(PROFILE['commission_rate']))
  fee_out=max(number(PROFILE['commission_min_usd']),exit_notional*number(PROFILE['commission_rate']))
  gross=exit_notional-n;slippage=n*number(PROFILE['slippage_roundtrip_bps'])/10000
  margin=n*number(PROFILE['minimum_net_edge_bps'])/10000
  net=gross-fee_in-fee_out-slippage
  if net<=margin:reasons.append('BTC_EDGE_BELOW_ROUNDTRIP_COST')
  result.update(forecast_sha256=digest(f),forecast_age_ms=now-f['issued_at_ms'],quote_age_ms=now-t,
    cash_usd=str(n),reference_mid=str(f['reference_mid']),entry_ask=str(ask),target_mid=str(target),
    expected_exit_bid=str(exit_bid),expected_gross_after_spread_usd=str(gross),
    expected_entry_fee_usd=str(fee_in),expected_exit_fee_usd=str(fee_out),
    slippage_reserve_usd=str(slippage),minimum_net_edge_usd=str(margin),
    expected_net_edge_usd=str(net),expected_gross_after_spread_bps=str(gross/n*10000))
 except (PolicyError,KeyError,TypeError,InvalidOperation) as exc:
  reasons.append(str(exc) if isinstance(exc,PolicyError) else 'BTC_INVALID_INPUT')
 result['allowed']=not reasons
 return result
