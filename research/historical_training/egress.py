"""History-only wire boundary. No socket, credentials or order implementation."""
from __future__ import annotations
import copy,time
from decimal import Decimal,InvalidOperation
from .common import EvidenceError

class HistoryGuard:
    def __init__(self,client_id,spacing_sec=12):
        if client_id!=8984 or not 12<=spacing_sec<=60:raise EvidenceError('HISTORY_ID_OR_PACING')
        self.client_id=client_id;self.spacing=spacing_sec
        self.plans={};self.issued={};self.cancelled=set();self.started=False;self.last=None
    def register(self,rid,plan):
        if type(rid) is not int or not 7800<=rid<=7830 or rid in self.plans:
            raise EvidenceError('DUPLICATE_OR_FOREIGN_HISTORY_ID')
        kind=plan.get('kind');what=plan.get('what')
        if kind not in ('head','bars','schedule') or what not in ('MIDPOINT','BID','ASK','SCHEDULE'):
            raise EvidenceError('HISTORY_REQUEST_KIND')
        if (kind=='schedule')!=(what=='SCHEDULE'):raise EvidenceError('HISTORY_REQUEST_KIND')
        self.plans[rid]=copy.deepcopy(plan)
    def check(self,msg,paper,server_version,now=None):
        if not isinstance(msg,str) or not msg.endswith('\0'):raise EvidenceError('WIRE_FORMAT')
        f=msg[:-1].split('\0')
        try:code=int(f[0])
        except (ValueError,IndexError):raise EvidenceError('WIRE_FORMAT')
        if code==71:
            if self.started or f!=['71','2',str(self.client_id),'']:
                raise EvidenceError('START_API_IDENTITY_OR_REPEAT')
            self.started=True;return
        if code not in (20,25,87,90):raise EvidenceError('NON_HISTORY_MESSAGE_REFUSED')
        if not self.started or not paper:raise EvidenceError('PAPER_NOT_VERIFIED')
        # Modern pinned SDK layout; any different layout fails closed below.
        if server_version<180:raise EvidenceError('UNVERIFIED_WIRE_VERSION')
        try:rid=int(f[2] if code==25 else f[1])
        except (ValueError,IndexError):raise EvidenceError('WIRE_REQUEST_ID')
        if rid not in self.plans:raise EvidenceError('FOREIGN_HISTORY_ID')
        plan=self.plans[rid]
        if code in (25,90):
            wanted=90 if plan['kind']=='head' else 25
            expected=[str(wanted),str(rid)] if wanted==90 else ['25','1',str(rid)]
            if rid not in self.issued or rid in self.cancelled or code!=wanted or f!=expected:
                raise EvidenceError('UNISSUED_WRONG_OR_REPEATED_CANCEL')
            self.cancelled.add(rid);return
        wanted=87 if plan['kind']=='head' else 20
        if code!=wanted or rid in self.issued:raise EvidenceError('WRONG_OR_REPEATED_HISTORY_REQUEST')
        if len(f)!=(18 if code==87 else 23):raise EvidenceError('WIRE_LAYOUT')
        fixed={2:'12087792',3:'EUR',4:'CASH',5:'',7:'',8:'',9:'IDEALPRO',10:'',11:'USD',12:'',13:'',14:'0'}
        if any(f[i]!=v for i,v in fixed.items()):raise EvidenceError('CONTRACT_SCOPE')
        try:
            if Decimal(f[6])!=0:raise EvidenceError('CONTRACT_STRIKE')
        except InvalidOperation:raise EvidenceError('CONTRACT_STRIKE')
        if code==87:
            if f[15:]!=['0',plan['what'],'2']:raise EvidenceError('HEAD_REQUEST_PAYLOAD')
        else:
            size='1 day' if plan['kind']=='schedule' else '5 mins'
            if f[15:]!=[plan['end'],size,'30 D','0',plan['what'],'2','0','']:
                raise EvidenceError('HISTORY_REQUEST_PAYLOAD')
        now=time.monotonic() if now is None else now
        if self.last is not None and now-self.last<self.spacing:
            raise EvidenceError('WIRE_PACING_LIMIT')
        # Mark before I/O; failed/unknown transport must not permit resend.
        self.issued[rid]=code;self.last=now
    def check_response(self,rid,kind):
        if rid not in self.issued or rid in self.cancelled or self.plans[rid]['kind']!=kind:
            raise EvidenceError('UNBOUND_HISTORY_RESPONSE')
