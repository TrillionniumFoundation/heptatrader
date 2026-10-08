"""Explicit IBKR outbound allowlist for a EUR.USD PAPER data-only client.

Network bytes are allowed only for StartApi, LIVE data-type, EUR/CASH/USD/
IDEALPRO streaming Bid/Ask, own tick-by-tick BidAsk, and cancellation of those
two exact request IDs. No order/account/global-cancel/other-symbol packet.
"""
from __future__ import annotations
from dataclasses import dataclass,field
import re

class RejectWire(ValueError):pass
CODES={71,59,1,2,97,98}
DATA_IDS=(3101,3102)

@dataclass
class SendBoundary:
 client_id:int
 started:bool=False
 mkt_type:bool=False
 active:set[int]=field(default_factory=set)
 data_ids:tuple[int,int]=(3101,3102)
 sent:dict[int,int]=field(default_factory=dict)
 def check(self,msg):
  if not isinstance(msg,str) or len(msg)>4096 or not msg.endswith('\0'):raise RejectWire('INVALID_WIRE')
  v=msg[:-1].split('\0')
  if not v or not v[0].isdigit():raise RejectWire('NO_CODE')
  code=int(v[0])
  if code not in CODES:raise RejectWire('MUTATING_OR_OTHER_IB_API_FORBIDDEN')
  if code==71:
   valid=v in (['71','2',str(self.client_id)],['71','2',str(self.client_id),'']) and not self.started
   if valid:self.started=True
  elif not self.started:
   raise RejectWire('START_API_REQUIRED')
  elif code==59:
   valid=v==['59','1','1'] and not self.mkt_type
   if valid:self.mkt_type=True
  elif not self.mkt_type:
   raise RejectWire('LIVE_DATA_TYPE_REQUIRED')
  elif code in (1,97):
   reqid=self.data_ids[0 if code==1 else 1]
   marker=str(reqid)
   if code==1:
    # Current IBAPI streaming contract wire v11 with conId and tradingClass.
    valid=(len(v)==20 and v[:4]==['1','11',marker,'0']
       and v[4]=='EUR' and v[5]=='CASH' and v[6]==''
       and v[7] in ('0','0.0') and v[8]==''
       and v[9]=='' and v[10]=='IDEALPRO' and v[11]==''
       and v[12]=='USD' and v[13]=='' and v[14]==''
       and v[15] in ('0','false') and v[16]=='' and v[17]=='0'
       and v[18]=='0' and v[19]=='')
   else:
    valid=(len(v)==17 and v[:3]==['97',marker,'0']
      and v[3]=='EUR' and v[4]=='CASH' and v[5]==''
      and v[6] in ('0','0.0') and v[7]=='' and v[8]==''
      and v[9]=='IDEALPRO' and v[10]=='' and v[11]=='USD'
      and v[12]=='' and v[13]=='' and v[14]=='BidAsk'
      and v[15]=='0' and v[16]=='0')
   valid=valid and reqid not in self.active
   if valid:self.active.add(reqid)
  elif code==2:
   valid=v==['2','2',str(self.data_ids[0])] and self.data_ids[0] in self.active
   if valid:self.active.remove(self.data_ids[0])
  elif code==98:
   valid=v==['98',str(self.data_ids[1])] and self.data_ids[1] in self.active
   if valid:self.active.remove(self.data_ids[1])
  else:valid=False
  if not valid:raise RejectWire('READ_ONLY_SCOPE_OR_DUPLICATE_REJECTED:'+str(code))
  self.sent[code]=self.sent.get(code,0)+1
  return code
 def advance_cycle(self):
  if self.active or not self.mkt_type or self.data_ids[1]>=3298:raise RejectWire('CYCLE_REQUIRES_PRIOR_CANCEL')
  self.data_ids=(self.data_ids[0]+2,self.data_ids[1]+2)
 def signed_receipt(self):
  return {'client_id':self.client_id,'permitted_codes':sorted(CODES),
          'requests_only_own_ids':list(self.data_ids),'counts':dict(self.sent),
          'active_requests':sorted(self.active),'mutating_codes_sent':0}

def valid_pair(bid,ask):
 from math import isfinite
 return type(bid) in (int,float) and type(ask) in (int,float) and isfinite(bid) and isfinite(ask) and 0<float(bid)<=float(ask)<1000000
