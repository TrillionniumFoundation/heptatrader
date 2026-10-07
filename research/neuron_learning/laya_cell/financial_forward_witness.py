"""Local Ed25519 role witnesses, not canonical Agentd admission certificates.

Predictor, registrar, evaluator and selector have separate keys. Deployed process
file confinement denies the other roles' secret keys. Public trust is pinned in
configuration. This does not claim protection from the host's privileged owner.
"""
from __future__ import annotations
import argparse,base64,json,subprocess,sys,time
from pathlib import Path


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def tool(mode,key,data,signature=None):
 if mode=='sign':
  from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
  return Ed25519PrivateKey.from_private_bytes(Path(key).read_bytes()).sign(data)
 from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
 Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(signature,data)
 return b'OK'

def verify_trust(config):
 from .integrity import read_json,digest,sha_file,ContractError
 trust=read_json(Path(config['witness_trust_path']))
 if sha_file(Path(config['witness_trust_path']))!=config['witness_trust_sha256'] or digest(trust)!=config['witness_trust_digest']:
  raise ContractError('FORWARD_WITNESS_TRUST_CHANGED')
 if trust.get('scope')!=config['period_sha256'] or trust.get('canonical_agentd_issuer') is not False:
  raise ContractError('FORWARD_WITNESS_SCOPE')
 roles=trust['roles']
 if len(set(roles.values()))!=len(roles) or set(roles)!={'registrar','predictor','evaluator','selector'}:
  raise ContractError('FORWARD_ROLE_KEYS_NOT_DISTINCT')
 return trust

def _run(mode,key,payload,signature=None):
 cmd=['/usr/bin/python3',str(Path(__file__)),mode,'--key',str(key)]
 if signature:cmd+=['--signature',signature]
 p=subprocess.run(cmd,input=payload,capture_output=True,timeout=5)
 if p.returncode:raise ValueError('LOCAL_ROLE_SIGNATURE_'+mode.upper()+'_FAILED')
 return p.stdout

def sign_record(config,role,record):
 from .integrity import digest,ContractError
 trust=verify_trust(config)
 if role!=config.get('signing_role'):raise ContractError('WRONG_PROCESS_SIGNING_ROLE')
 if any(k in record for k in ('signature','role_witness')):raise ContractError('ALREADY_SIGNED_RECORD')
 payload={**record,'role_witness':{'role':role,'public_key':trust['roles'][role],
           'trust_digest':config['witness_trust_digest'],'scope':config['period_sha256'],
           'signed_at_ms':int(time.time()*1000),'canonical_agentd_admission':False}}
 signed=_run('sign',config['role_key_path'],canonical(payload))
 # System helper emits base64; do not place private key bytes in parent memory.
 out={**payload,'signature':signed.decode().strip()}
 verify_record(config,role,out)
 return out

def verify_record(config,role,record):
 from .integrity import ContractError
 trust=verify_trust(config);w=record.get('role_witness') or {}
 if (w.get('role')!=role or w.get('public_key')!=trust['roles'][role]
     or w.get('scope')!=config['period_sha256'] or w.get('trust_digest')!=config['witness_trust_digest']
     or w.get('canonical_agentd_admission') is not False):raise ContractError('LOCAL_WITNESS_ROLE_OR_SCOPE')
 if type(w.get('signed_at_ms'))is not int or w['signed_at_ms']>int(time.time()*1000):raise ContractError('LOCAL_WITNESS_TIME')
 _run('verify',trust['roles'][role],canonical({k:v for k,v in record.items() if k!='signature'}),record.get('signature'))
 return record

def main():
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['sign','verify']);p.add_argument('--key',required=True);p.add_argument('--signature')
 a=p.parse_args();data=sys.stdin.buffer.read(2*1024**2+1)
 if len(data)>2*1024**2:raise SystemExit('SIGNATURE_PAYLOAD_BOUND')
 if a.mode=='sign':sys.stdout.buffer.write(base64.b64encode(tool('sign',a.key,data)))
 else:sys.stdout.buffer.write(tool('verify',a.key,data,base64.b64decode(a.signature,validate=True)))
if __name__=='__main__':main()
