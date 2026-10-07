"""Versioned NeuronOutput[256]; identity is evidence, not execution authority.

Descriptors are not forecasts: missing probability/calibration stays missing.
No network, filesystem mutation, broker or model-selection operations.
"""
from __future__ import annotations
from dataclasses import dataclass,asdict
import hashlib,json,math,re
from typing import Any,Sequence

DIM=256
SCHEMA='heptatrader.neuron-output.v2.256'
HEX=re.compile(r'[0-9a-f]{64}\Z')
KINDS={'price','semantic','execution','regime','risk'}
class ContractError(ValueError):pass

def digest(value:Any)->str:
 return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def identity(x,label):
 if not isinstance(x,str) or not HEX.fullmatch(x):raise ContractError('IDENTITY:'+label)
def integer(x,label,minimum=0):
 if type(x)is not int or x<minimum:raise ContractError('INTEGER:'+label)
def number(x,label):
 if type(x) not in (int,float) or not math.isfinite(x):raise ContractError('FINITE:'+label)
 return float(x)
def entropy(probabilities):return -sum(p*math.log(p) for p in probabilities if p>0)/math.log(7)

@dataclass(frozen=True)
class Context:
 period_id:str
 instrument:str
 input_sha256:str
 anchor_ms:int
 input_available_ms:int
 horizon_ms:int
 behavior_policy_sha256:str
 quantity_policy_sha256:str
 target_schema:str='FUTURE_MID_BPS_7_BINS_V1'
 def validate(self):
  if type(self.period_id)is not str or not 1<=len(self.period_id)<=128:raise ContractError('PERIOD')
  if self.instrument not in ('EUR.USD','BTC.USD'):raise ContractError('INSTRUMENT')
  for name in ('input_sha256','behavior_policy_sha256','quantity_policy_sha256'):identity(getattr(self,name),name)
  for name in ('anchor_ms','input_available_ms','horizon_ms'):integer(getattr(self,name),name,1)
  if not self.anchor_ms<=self.input_available_ms<self.anchor_ms+self.horizon_ms:raise ContractError('INPUT_TIME')
  if self.target_schema!='FUTURE_MID_BPS_7_BINS_V1':raise ContractError('TARGET_SCHEMA_DO_NOT_MIX_NET_USD_WITH_MID_BPS')

@dataclass(frozen=True)
class Metric:
 name:str
 value:float|None
 unit:str
 def validate(self):
  if type(self.name)is not str or not 1<=len(self.name)<=80:raise ContractError('METRIC_NAME')
  if self.unit not in ('bps','ms','count','ratio','USD','base_units'):raise ContractError('METRIC_UNIT')
  if self.value is not None:number(self.value,'metric')

@dataclass(frozen=True)
class Uncertainty:
 kind:str
 value:float|None
 calibrated:bool=False
 def validate(self,probabilities):
  if self.calibrated is not False:raise ContractError('NO_CALIBRATION_CLAIM_WITHOUT_INDEPENDENT_PROTOCOL')
  if probabilities is None:
   if self.kind!='unestimated' or self.value is not None:raise ContractError('DESCRIPTOR_IS_NOT_CALIBRATED_FORECAST')
  else:
   if self.kind!='normalized_class_entropy' or self.value is None:raise ContractError('UNCERTAINTY_KIND')
   v=number(self.value,'uncertainty')
   if not 0<=v<=1.000001 or abs(v-entropy(probabilities))>1e-6:raise ContractError('UNCERTAINTY_NOT_ENTROPY')

@dataclass(frozen=True)
class NeuronOutput:
 schema:str
 node_id:str
 kind:str
 model_sha256:str
 context:Context
 embedding:tuple[float,...]
 embedding_basis_sha256:str
 probabilities:tuple[float,...]|None
 uncertainty:Uncertainty
 metrics:tuple[Metric,...]
 source_cutoff_ms:int
 model_frozen_at_ms:int
 available_at_ms:int
 valid_until_ms:int
 evidence_sha256:tuple[str,...]
 effect_authority:bool=False
 def validate(self,now_ms:int):
  if self.schema!=SCHEMA:raise ContractError('SCHEMA_V2_256_REQUIRED')
  if type(self.node_id)is not str or not 1<=len(self.node_id)<=128:raise ContractError('NODE_ID')
  if self.kind not in KINDS:raise ContractError('KIND')
  if self.effect_authority is not False:raise ContractError('NO_EXECUTION_AUTHORITY')
  if not isinstance(self.context,Context):raise ContractError('CONTEXT_TYPE')
  self.context.validate();identity(self.model_sha256,'model');identity(self.embedding_basis_sha256,'embedding_basis')
  if type(self.embedding)is not tuple or len(self.embedding)!=DIM:raise ContractError('EMBEDDING_256_REQUIRED')
  emb=[number(x,'embedding') for x in self.embedding]
  if abs(math.sqrt(sum(x*x for x in emb))-1)>1e-4:raise ContractError('EMBEDDING_NORM')
  if self.probabilities is not None:
   if type(self.probabilities)is not tuple or len(self.probabilities)!=7:raise ContractError('PROBABILITY_DIM')
   probs=[number(x,'probability') for x in self.probabilities]
   if any(x<0 or x>1 for x in probs) or abs(sum(probs)-1)>1e-6:raise ContractError('PROBABILITY_SIMPLEX')
   if self.kind not in ('price','semantic'):raise ContractError('DESCRIPTIVE_NODE_CANNOT_INVENT_PRICE_PROBABILITIES')
  if not isinstance(self.uncertainty,Uncertainty):raise ContractError('UNCERTAINTY_TYPE')
  self.uncertainty.validate(self.probabilities)
  if type(self.metrics)is not tuple or len(self.metrics)>32:raise ContractError('METRICS')
  names=[]
  for m in self.metrics:
   if not isinstance(m,Metric):raise ContractError('METRIC_TYPE')
   m.validate();names.append(m.name)
  if len(names)!=len(set(names)):raise ContractError('DUPLICATE_METRIC')
  for name in ('source_cutoff_ms','model_frozen_at_ms','available_at_ms','valid_until_ms'):integer(getattr(self,name),name,1)
  integer(now_ms,'now',1)
  if self.model_frozen_at_ms>self.context.anchor_ms or self.source_cutoff_ms>self.context.input_available_ms:raise ContractError('FUTURE_INFORMATION')
  if not self.context.input_available_ms<=self.available_at_ms<=now_ms<=self.valid_until_ms:raise ContractError('STALE_OR_FUTURE_NODE')
  if self.available_at_ms>=self.context.anchor_ms+self.context.horizon_ms:raise ContractError('LATE_PREDICTION')
  if type(self.evidence_sha256)is not tuple or not 1<=len(self.evidence_sha256)<=32:raise ContractError('EVIDENCE_REQUIRED')
  for h in self.evidence_sha256:identity(h,'evidence')
  return self
 def wire(self):return asdict(self)

def align(nodes:Sequence[NeuronOutput],now_ms:int):
 if not isinstance(nodes,(list,tuple)) or not 1<=len(nodes)<=32:raise ContractError('NODE_COUNT')
 for n in nodes:
  if not isinstance(n,NeuronOutput):raise ContractError('NODE_TYPE')
  n.validate(now_ms)
 if len({n.node_id for n in nodes})!=len(nodes):raise ContractError('DUPLICATE_NODE')
 if any(n.context!=nodes[0].context for n in nodes):raise ContractError('CROSS_INPUT_SCOPE')
 return sorted(nodes,key=lambda n:n.node_id)

def lift_legacy32(vector):
 """Explicit isometry only. No new learned information and no schema relabeling."""
 if not isinstance(vector,(tuple,list)) or len(vector)!=32:raise ContractError('LEGACY32_SHAPE')
 v=tuple(number(x,'legacy_embedding') for x in vector)
 if abs(sum(x*x for x in v)-1)>1e-4:raise ContractError('LEGACY32_NORM')
 return tuple(x/math.sqrt(8) for _ in range(8) for x in v)

LEGACY32_BASIS=digest({'mapping':'REPEAT_8_SCALED_BY_SQRT_8','input_dim':32,'output_dim':256,'added_information':False})


def encode_packet(node:NeuronOutput,now_ms:int)->bytes:
 node.validate(now_ms)
 return json.dumps(node.wire(),sort_keys=True,separators=(',',':'),allow_nan=False).encode()

def decode_packet(raw:bytes,now_ms:int)->NeuronOutput:
 """Strict JSON wire boundary; unknown authority fields and duplicate keys fail."""
 from dataclasses import fields
 if type(raw)is not bytes or len(raw)>65536:raise ContractError('WIRE_BYTES_BOUND')
 def unique(items):
  value={}
  for k,v in items:
   if k in value:raise ContractError('WIRE_DUPLICATE_KEY')
   value[k]=v
  return value
 def bad(_):raise ContractError('WIRE_NONFINITE')
 try:obj=json.loads(raw,object_pairs_hook=unique,parse_constant=bad)
 except (UnicodeDecodeError,json.JSONDecodeError) as exc:raise ContractError('WIRE_JSON') from exc
 def exact(value,cls):
  if type(value)is not dict or set(value)!={f.name for f in fields(cls)}:raise ContractError('WIRE_FIELDS:'+cls.__name__)
 exact(obj,NeuronOutput);exact(obj['context'],Context);exact(obj['uncertainty'],Uncertainty)
 if type(obj['metrics'])is not list:raise ContractError('WIRE_METRICS')
 for m in obj['metrics']:exact(m,Metric)
 for name in ('embedding','evidence_sha256'):
  if type(obj[name])is not list:raise ContractError('WIRE_LIST:'+name)
 if obj['probabilities'] is not None and type(obj['probabilities'])is not list:raise ContractError('WIRE_PROBABILITIES')
 obj['context']=Context(**obj['context']);obj['uncertainty']=Uncertainty(**obj['uncertainty'])
 obj['metrics']=tuple(Metric(**m) for m in obj['metrics']);obj['embedding']=tuple(obj['embedding'])
 obj['evidence_sha256']=tuple(obj['evidence_sha256'])
 if obj['probabilities'] is not None:obj['probabilities']=tuple(obj['probabilities'])
 return NeuronOutput(**obj).validate(now_ms)
