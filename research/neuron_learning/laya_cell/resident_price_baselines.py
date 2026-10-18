"""Frozen CPU references on exactly the resident input, target and future window."""
from __future__ import annotations
import math
from collections import Counter
from copy import deepcopy
from .integrity import ContractError,digest
from .dataset import BIN_EDGES_BPS
from .resident_row_accounting import row_id,MAX_TRAINING_ROWS

SCHEMA='heptatrader.laya-resident-price-baselines.v1'
FEATURES=('horizon_over_300s','history_minutes','last_step_return_bps',
 'last_three_steps_return_bps','history_return_bps','history_range_bps',
 'rms_step_return_bps','mean_spread_bps','last_spread_bps','slope_bps_per_minute')
SPEC={'kind':'MULTINOMIAL_LOGISTIC_FLOAT64','features':list(FEATURES),
 'optimizer':'FULL_BATCH_GRADIENT_DESCENT','steps':128,'learning_rate':.05,
 'l2_weights_only':.01,'standardization':'TRAIN_ONLY_POPULATION_STD',
 'minimum_nonconstant_scale':1e-6,'constant_feature_scale':1.,
 'standardized_feature_clip':6.,'initial_weights':'ZERO',
 'initial_bias':'LOG_LAPLACE_1_GLOBAL_TRAIN_PRIOR'}
MODELS=('laya_candidate','frozen_parent','uniform','train_prior','linear_softmax')
VALUE_RULE={'schema':'heptatrader.laya-price-value-rule.v1',
 'primary_horizon_seconds':300,'comparators':['uniform','train_prior','linear_softmax'],
 'metric':'PAIRED_SAME_WINDOW_MEAN_BRIER', 'minimum_mean_improvement':1e-6,
 'additional_to_existing_parent_and_observation_rules':True,
 'scope':'PRICE_PREDICTION_NOT_EXECUTION_PROFIT', 'automatic_execution_selection':False}


def _number(value):
 if isinstance(value,bool):raise ContractError('PRICE_BASELINE_NUMBER')
 try:result=float(value)
 except (ValueError,TypeError):raise ContractError('PRICE_BASELINE_NUMBER') from None
 if not math.isfinite(result):raise ContractError('PRICE_BASELINE_NONFINITE')
 return result


def features(state):
 """Read only the existing sealed quote state; never a target or outcome field."""
 if (not isinstance(state,dict) or state.get('columns')!=['offset_ms','bid','ask']
     or type(state.get('anchor_ms')) is not int or state['anchor_ms']<0
     or type(state.get('horizon_sec')) is not int or state['horizon_sec'] not in (300,900)
     or state.get('instrument') not in ('EUR.USD','BTC.USD')):
  raise ContractError('PRICE_BASELINE_INPUT_SCHEMA')
 quotes=state.get('quotes')
 if not isinstance(quotes,list) or not 6<=len(quotes)<=24:raise ContractError('PRICE_BASELINE_QUOTE_HISTORY')
 offsets=[];mid=[];spread=[]
 for q in quotes:
  if not isinstance(q,list) or len(q)!=3 or type(q[0]) is not int or q[0]>0:
   raise ContractError('PRICE_BASELINE_QUOTE_HISTORY')
  if offsets and q[0]<=offsets[-1]:raise ContractError('PRICE_BASELINE_QUOTE_CHRONOLOGY')
  bid,ask=_number(q[1]),_number(q[2])
  if not 0<bid<=ask:raise ContractError('PRICE_BASELINE_QUOTE_PRICES')
  offsets.append(q[0]);mid.append((bid+ask)/2);spread.append((ask-bid)/mid[-1]*10000)
 if offsets[-1]!=0:raise ContractError('PRICE_BASELINE_ANCHOR')
 reference=_number(state['reference_mid'])
 if not math.isclose(reference,mid[-1],rel_tol=1e-12,abs_tol=1e-12):raise ContractError('PRICE_BASELINE_REFERENCE')
 changes=[(b/a-1)*10000 for a,b in zip(mid,mid[1:])]
 minutes=[t/60000 for t in offsets];returns=[(m/reference-1)*10000 for m in mid]
 mt=sum(minutes)/len(minutes);mr=sum(returns)/len(returns)
 slope=sum((x-mt)*(y-mr) for x,y in zip(minutes,returns))/sum((x-mt)**2 for x in minutes)
 result=[state['horizon_sec']/300,-offsets[0]/60000,changes[-1],
  (mid[-1]/mid[-4]-1)*10000,(mid[-1]/mid[0]-1)*10000,
  (max(mid)-min(mid))/reference*10000,
  math.sqrt(sum(x*x for x in changes)/len(changes)),
  sum(spread)/len(spread),spread[-1],slope]
 if any(not math.isfinite(x) for x in result):raise ContractError('PRICE_BASELINE_NONFINITE_FEATURE')
 return result


def _softmax(z):
 import numpy as np
 z=z-np.max(z,axis=-1,keepdims=True)
 values=np.exp(z)
 return values/values.sum(axis=-1,keepdims=True)


def freeze(rows,cutoff_ms):
 """One deterministic CPU fit on the same unique mature rows given to Laya."""
 import numpy as np
 if type(cutoff_ms) is not int or not 16<=len(rows)<=MAX_TRAINING_ROWS:
  raise ContractError('PRICE_BASELINE_TRAINING_POPULATION')
 ids=[row_id(r) for r in rows]
 if len(set(ids))!=len(ids):raise ContractError('PRICE_BASELINE_DUPLICATE_ROW')
 instruments=set()
 for r in rows:
  if (type(r.get('target_class')) is not int or not 0<=r['target_class']<7
      or type(r.get('outcome_available_at_ms')) is not int
      or not r['anchor_ms']<r['outcome_available_at_ms']<=cutoff_ms
      or r['state']['anchor_ms']!=r['anchor_ms']
      or r['state']['horizon_sec']!=r['horizon_sec']):
   raise ContractError('PRICE_BASELINE_NONCAUSAL_ROW')
  instruments.add(r['state']['instrument'])
 if len(instruments)!=1:raise ContractError('PRICE_BASELINE_INSTRUMENT_MIX')
 x=np.asarray([features(r['state']) for r in rows],dtype=np.float64)
 y=np.asarray([r['target_class'] for r in rows],dtype=np.int64)
 mean=x.mean(axis=0);std=x.std(axis=0)
 scale=np.where(std>=SPEC['minimum_nonconstant_scale'],std,SPEC['constant_feature_scale'])
 z=np.clip((x-mean)/scale,-SPEC['standardized_feature_clip'],SPEC['standardized_feature_clip'])
 counts=np.bincount(y,minlength=7)
 weights=np.zeros((len(FEATURES),7),dtype=np.float64)
 bias=np.log((counts+1)/(len(rows)+7));targets=np.eye(7,dtype=np.float64)[y]
 for _ in range(SPEC['steps']):
  residual=(_softmax(z@weights+bias)-targets)/len(rows)
  weights-=SPEC['learning_rate']*(z.T@residual+SPEC['l2_weights_only']*weights)
  bias-=SPEC['learning_rate']*residual.sum(axis=0)
 if not np.isfinite(weights).all() or not np.isfinite(bias).all():
  raise ContractError('PRICE_BASELINE_NONFINITE_FIT')
 priors={}
 for horizon in (300,900):
  labels=Counter(r['target_class'] for r in rows if r['horizon_sec']==horizon)
  n=sum(labels.values());histogram=[labels[i] for i in range(7)]
  priors[str(horizon)]={'rows':n,'class_counts':histogram,
   'probs':[(value+1)/(n+7) for value in histogram]}
 result={'schema':SCHEMA,'training_cutoff_ms':cutoff_ms,'training_rows':len(rows),
  'training_row_ids_digest':digest(sorted(ids)),'instrument':next(iter(instruments)),
  'bin_edges_bps':list(BIN_EDGES_BPS),'uniform_probs':[1/7]*7,
  'prior_pseudocount_per_class':1,'train_prior_by_horizon':priors,
  'simple_model_spec':deepcopy(SPEC),'mean':mean.tolist(),'scale':scale.tolist(),
  'weights':weights.tolist(),'bias':bias.tolist(),
  'selection_used_future_metrics':False,'model_or_trade_permission':False}
 result['profile_id']=digest(result)
 return validate(result)


def _vector(value,n,*,positive=False):
 if (not isinstance(value,list) or len(value)!=n
     or any(type(x) not in (int,float) or not math.isfinite(x) or (positive and x<=0) for x in value)):
  raise ContractError('PRICE_BASELINE_PARAMETER_VECTOR')


def validate(profile):
 if (not isinstance(profile,dict) or profile.get('schema')!=SCHEMA
     or profile.get('profile_id')!=digest({k:v for k,v in profile.items() if k!='profile_id'})
     or profile.get('simple_model_spec')!=SPEC or profile.get('bin_edges_bps')!=list(BIN_EDGES_BPS)
     or profile.get('uniform_probs')!=[1/7]*7 or profile.get('prior_pseudocount_per_class')!=1
     or profile.get('model_or_trade_permission') is not False
     or profile.get('selection_used_future_metrics') is not False
     or type(profile.get('training_cutoff_ms')) is not int or profile['training_cutoff_ms']<0
     or type(profile.get('training_rows')) is not int or not 16<=profile['training_rows']<=MAX_TRAINING_ROWS
     or profile.get('instrument') not in ('EUR.USD','BTC.USD')):
  raise ContractError('PRICE_BASELINE_FROZEN_PROFILE')
 _vector(profile.get('mean'),len(FEATURES));_vector(profile.get('scale'),len(FEATURES),positive=True)
 _vector(profile.get('bias'),7)
 if not isinstance(profile.get('weights'),list) or len(profile['weights'])!=len(FEATURES):
  raise ContractError('PRICE_BASELINE_WEIGHT_SHAPE')
 for weights in profile['weights']:_vector(weights,7)
 priors=profile.get('train_prior_by_horizon')
 if not isinstance(priors,dict) or set(priors)!={'300','900'}:raise ContractError('PRICE_BASELINE_PRIOR_HORIZONS')
 total=0
 for prior in priors.values():
  counts=prior.get('class_counts')
  if (not isinstance(counts,list) or len(counts)!=7
      or any(type(n) is not int or n<0 for n in counts)
      or type(prior.get('rows')) is not int or prior['rows']!=sum(counts)
      or prior.get('probs')!=[(n+1)/(sum(counts)+7) for n in counts]):
   raise ContractError('PRICE_BASELINE_PRIOR_COUNTS')
  total+=prior['rows']
 if total!=profile['training_rows']:raise ContractError('PRICE_BASELINE_PRIOR_POPULATION')
 return profile


def forecast(profile,state):
 import numpy as np
 p=validate(profile)
 if state.get('instrument')!=p['instrument'] or state.get('anchor_ms',-1)<p['training_cutoff_ms']:
  raise ContractError('PRICE_BASELINE_FUTURE_INPUT')
 x=np.asarray(features(state),dtype=np.float64)
 z=np.clip((x-np.asarray(p['mean']))/np.asarray(p['scale']),-SPEC['standardized_feature_clip'],SPEC['standardized_feature_clip'])
 probs=_softmax(z@np.asarray(p['weights'])+np.asarray(p['bias'])).tolist()
 return {'profile_id':p['profile_id'],'input_id':digest(state),'horizon_sec':state['horizon_sec'],
  'uniform_probs':p['uniform_probs'],
  'train_prior_probs':p['train_prior_by_horizon'][str(state['horizon_sec'])]['probs'],
  'linear_softmax_probs':probs,'model_or_trade_permission':False}


def probability_metrics(probs,target):
 if (type(target) is not int or not 0<=target<7 or not isinstance(probs,list) or len(probs)!=7
     or any(type(p) not in (int,float) or not math.isfinite(p) or not 0<=p<=1 for p in probs)
     or abs(sum(probs)-1)>1e-5):raise ContractError('PRICE_BASELINE_PROBABILITY')
 mass=probs[target]
 return {'brier':sum((p-(i==target))**2 for i,p in enumerate(probs)),
  'nll':-math.log(mass) if mass>0 else None,'nll_is_infinite':mass==0,
  'hit':max(range(7),key=lambda i:probs[i])==target}


def measure(profile,prediction,target):
 expected=forecast(profile,prediction['input'])
 observed=prediction.get('price_baseline_forecast')
 if (not isinstance(observed,dict)
     or any(observed.get(k)!=expected[k] for k in
      ('profile_id','input_id','horizon_sec','model_or_trade_permission','uniform_probs','train_prior_probs'))):
  raise ContractError('PRICE_BASELINE_FORECAST_CHANGED')
 linear=observed.get('linear_softmax_probs')
 linear_metrics=probability_metrics(linear,target)
 # Tiny float64 BLAS roundoff is immaterial; score the committed probability.
 if any(not math.isclose(a,b,rel_tol=1e-12,abs_tol=0.)
        for a,b in zip(linear,expected['linear_softmax_probs'])):
  raise ContractError('PRICE_BASELINE_FORECAST_CHANGED')
 vectors={'laya_candidate':prediction['candidate_probs'],'frozen_parent':prediction['parent_probs'],
  'uniform':expected['uniform_probs'],'train_prior':expected['train_prior_probs']}
 return {name:linear_metrics if name=='linear_softmax' else probability_metrics(vectors[name],target)
         for name in MODELS}


def summarize(outcomes):
 observed=[o for o in outcomes if o.get('status')=='OBSERVED']
 result={'observed':len(observed),'censored':sum(o.get('status')=='CENSORED' for o in outcomes),
  'same_outcomes_for_every_model':True,'metric_scope':'PRICE_PREDICTION_NOT_EXECUTION_PNL','models':{}}
 for name in MODELS:
  values=[o['same_window_price_scores'][name] for o in observed]
  infinite=any(v['nll_is_infinite'] for v in values)
  result['models'][name]={'mean_brier':sum(v['brier'] for v in values)/len(values) if values else None,
   'mean_nll':sum(v['nll'] for v in values)/len(values) if values and not infinite else None,
   'nll_is_infinite':infinite,'hits':sum(v['hit'] for v in values)}
 return result


def validate_value_rule(plan):
 """A missing declaration is legacy; a changed declaration is never inferred."""
 rule=plan.get('price_value_rule')
 if rule is None:return None
 if rule!=VALUE_RULE or plan.get('rule',{}).get('horizon_ms')!=300000:
  raise ContractError('PRICE_VALUE_RULE_CHANGED')
 validate(plan.get('price_baselines'))
 return rule


def value_assessment(plan,observed):
 rule=validate_value_rule(plan)
 if rule is None:return None,[]
 profile_id=plan['price_baselines']['profile_id']
 values=[]
 for outcome in observed:
  if (outcome.get('plan_id')!=plan['plan_id'] or outcome.get('horizon_sec')!=300
      or outcome.get('price_baseline_profile_id')!=profile_id):
   raise ContractError('PRICE_VALUE_SAME_WINDOW_IDENTITY')
  scores=outcome.get('same_window_price_scores')
  if not isinstance(scores,dict) or set(scores)!=set(MODELS):
   raise ContractError('PRICE_VALUE_SCORE_POPULATION')
  scores={name:score.get('brier') if isinstance(score,dict) else None for name,score in scores.items()}
  if any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=2 for v in scores.values()):
   raise ContractError('PRICE_VALUE_BRIER_SCORE')
  if (not math.isclose(scores['laya_candidate'],outcome['candidate_brier'],rel_tol=0,abs_tol=1e-12)
      or not math.isclose(scores['frozen_parent'],outcome['parent_brier'],rel_tol=0,abs_tol=1e-12)):
   raise ContractError('PRICE_VALUE_CANDIDATE_SCORE_CHANGED')
  values.append(scores)
 report={'rule_id':digest(rule),'primary_horizon_seconds':300,'observed':len(values),
  'same_observations_for_every_comparator':True,'comparisons':{},
  'scope':rule['scope'],'execution_profitability_proven':False}
 reasons=[]
 for name in rule['comparators']:
  deltas=[scores[name]-scores['laya_candidate'] for scores in values]
  mean=sum(deltas)/len(deltas) if deltas else None
  passed=mean is not None and mean>rule['minimum_mean_improvement']
  report['comparisons'][name]={'mean_brier_improvement':mean,'value_floor_passed':passed}
  if not passed:reasons.append('NO_POSITIVE_BRIER_IMPROVEMENT_VS_'+name.upper())
 report['all_comparators_beaten']=not reasons
 return report,reasons
