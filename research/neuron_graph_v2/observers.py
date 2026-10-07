"""Causal Regime/Risk/Execution descriptor nodes, not trained trading experts.

These deterministic observations never fabricate a price forecast, missing fee,
calibrated uncertainty, native risk decision, or permission token.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal,InvalidOperation
import hashlib,math
from pathlib import Path
from .contracts import Context,Metric,Uncertainty,NeuronOutput,SCHEMA,ContractError,digest,number,integer,identity

@dataclass(frozen=True)
class Quote:
 observed_ms:int
 known_ms:int
 bid:float
 ask:float
 evidence_sha256:str
 source:str='IB'
 authoritative:bool=True
 def validate(self,cutoff):
  integer(self.observed_ms,'quote_observed',1);integer(self.known_ms,'quote_known',1)
  b=number(self.bid,'bid');a=number(self.ask,'ask');identity(self.evidence_sha256,'quote')
  if not 0<b<=a or not self.observed_ms<=self.known_ms<=cutoff:raise ContractError('QUOTE_VALUE_OR_KNOWLEDGE_TIME')
  if self.source!='IB' or self.authoritative is not True:raise ContractError('QUOTE_AUTHORITY')
  return (a+b)/2

@dataclass(frozen=True)
class ExecutionFact:
 execution_id:str
 known_ms:int
 notional_usd:str
 fee_usd:str|None
 fill_latency_ms:float|None
 period_id:str
 behavior_policy_sha256:str
 evidence_sha256:str

def decimal(x,label):
 if type(x)is not str:raise ContractError('DECIMAL_TEXT:'+label)
 try:v=Decimal(x)
 except InvalidOperation:raise ContractError('DECIMAL:'+label) from None
 if not v.is_finite():raise ContractError('FINITE_DECIMAL:'+label)
 return v

def descriptor_embedding(kind,metrics):
 # Fixed signed hash features in a declared basis. This is not pretrained skill.
 vec=[0.0]*256;vec[0]=1.0
 scale={'bps':100.,'ms':10000.,'count':100.,'ratio':1.,'USD':1000.,'base_units':100000.}
 for m in metrics:
  key=(kind+':'+m.name+':'+m.unit+(':missing' if m.value is None else ':value')).encode()
  h=hashlib.sha256(key).digest();value=1.0 if m.value is None else math.tanh(m.value/scale[m.unit])
  for i in range(4):
   index=1+int.from_bytes(h[i*2:i*2+2],'little')%255
   sign=1.0 if h[16+i]&1 else -1.0;vec[index]+=sign*value/2
 norm=math.sqrt(sum(x*x for x in vec));return tuple(x/norm for x in vec)

BASIS=digest({'version':'CAUSAL_DIAGNOSTIC_SIGNED_HASH_V1','embedding_dim':256,'untrained':True,
              'scales':{'bps':100,'ms':10000,'count':100,'ratio':1,'USD':1000,'base_units':100000}})

def output(context,kind,metrics,evidence,available_at_ms,valid_until_ms,algorithm_frozen_at_ms):
 # Algorithm is immutable; this is its implementation identity, not model weights.
 integer(algorithm_frozen_at_ms,'algorithm_frozen_at_ms',1)
 mid=digest({'node':kind,'basis':BASIS,'schema':SCHEMA,'implementation_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
 n=NeuronOutput(SCHEMA,kind+'-descriptor-v1',kind,mid,context,descriptor_embedding(kind,metrics),BASIS,None,
  Uncertainty('unestimated',None),tuple(metrics),context.input_available_ms,algorithm_frozen_at_ms,available_at_ms,valid_until_ms,tuple(sorted(set(evidence))))
 return n.validate(available_at_ms)

def market_observers(context:Context,quotes,available_at_ms,valid_until_ms,*,algorithm_frozen_at_ms):
 context.validate()
 if not isinstance(quotes,(tuple,list)) or not 6<=len(quotes)<=60:raise ContractError('HISTORY_BOUND')
 mids=[];previous=0
 for q in quotes:
  if not isinstance(q,Quote):raise ContractError('QUOTE_TYPE')
  mids.append(q.validate(context.input_available_ms))
  if q.observed_ms<=previous or (previous and q.observed_ms-previous>30000):raise ContractError('HISTORY_GAP_OR_ORDER')
  previous=q.observed_ms
 if quotes[-1].observed_ms!=context.anchor_ms:raise ContractError('ANCHOR_MISMATCH')
 if context.input_available_ms-context.anchor_ms>5000:raise ContractError('STALE_QUOTE')
 if quotes[-1].known_ms>available_at_ms:raise ContractError('RESULT_BEFORE_INPUT')
 returns=[(b/a-1)*10000 for a,b in zip(mids,mids[1:])]
 mean=sum(returns)/len(returns);variance=sum((x-mean)**2 for x in returns)/len(returns)
 realized_vol=math.sqrt(variance)
 trend=(mids[-1]/mids[0]-1)*10000;variation=sum(abs(x) for x in returns)
 efficiency=abs(trend)/variation if variation>0 else 0.
 # Bounds protect only an approximate path descriptor, not a trade or volatility forecast.
 efficiency=min(efficiency,1.)
 high=mids[0];drawdown=0.
 for mid in mids:
  high=max(high,mid);drawdown=max(drawdown,(1-mid/high)*10000)
 spread=(quotes[-1].ask-quotes[-1].bid)/mids[-1]*10000
 evidence=[digest([q.evidence_sha256 for q in quotes])]
 regime=[Metric('realized_return',trend,'bps'),Metric('realized_step_volatility',realized_vol,'bps'),
  Metric('path_efficiency',efficiency,'ratio'),Metric('quote_count',len(quotes),'count')]
 risk=[Metric('historical_peak_to_trough',drawdown,'bps'),Metric('max_observed_step_move',max(abs(x) for x in returns),'bps'),
  Metric('observed_spread',spread,'bps'),Metric('source_quote_age',context.input_available_ms-context.anchor_ms,'ms')]
 return (output(context,'regime',regime,evidence,available_at_ms,valid_until_ms,algorithm_frozen_at_ms),
         output(context,'risk',risk,evidence,available_at_ms,valid_until_ms,algorithm_frozen_at_ms))

def execution_observer(context:Context,facts,available_at_ms,valid_until_ms,*,algorithm_frozen_at_ms):
 context.validate()
 if not isinstance(facts,(tuple,list)) or len(facts)>512:raise ContractError('EXECUTION_FACT_BOUND')
 seen=set();notional=Decimal(0);fees=Decimal(0);missing=0;latencies=[];evidence=[]
 for f in facts:
  if not isinstance(f,ExecutionFact):raise ContractError('EXECUTION_FACT_TYPE')
  if type(f.execution_id)is not str or not f.execution_id or f.execution_id in seen:raise ContractError('DUPLICATE_EXECUTION_ID')
  seen.add(f.execution_id);integer(f.known_ms,'execution_known',1)
  if f.known_ms>context.input_available_ms:raise ContractError('FUTURE_EXECUTION_INFORMATION')
  if f.period_id!=context.period_id or f.behavior_policy_sha256!=context.behavior_policy_sha256:raise ContractError('EXECUTION_SCOPE')
  identity(f.evidence_sha256,'execution');evidence.append(f.evidence_sha256)
  value=decimal(f.notional_usd,'notional')
  if value<=0:raise ContractError('NOTIONAL_POSITIVE')
  notional+=value
  if f.fee_usd is None:missing+=1
  else:fees+=decimal(f.fee_usd,'fee')
  if f.fill_latency_ms is not None:
   latency=number(f.fill_latency_ms,'latency')
   if latency<0:raise ContractError('NEGATIVE_LATENCY')
   latencies.append(latency)
 effective=None if missing or not facts else float(fees/notional*10000)
 metrics=[Metric('reported_commission_rate',effective,'bps'),Metric('missing_fee_count',missing,'count'),
  Metric('execution_count',len(facts),'count'),Metric('observed_max_fill_latency',max(latencies) if latencies else None,'ms')]
 # A no-history descriptor is explicit; its hash is an empty evidence-set witness,
 # not a claim of observed zero fee. The fee value above remains None.
 return output(context,'execution',metrics,[digest(evidence)],available_at_ms,valid_until_ms,algorithm_frozen_at_ms)
