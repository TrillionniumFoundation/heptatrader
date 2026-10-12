"""Test-only physical Mac Ed25519 -> Pocket4 fsynced-file latency.

Measures WARM remote processes, not native LedgerWriter, original market,
product-root trust, or a prediction. Ephemeral Mac private key is RAM only.
"""
from __future__ import annotations
import argparse,hashlib,json,os,select,shlex,subprocess,time
from pathlib import Path
from datetime import datetime,timezone

DOMAIN=b"HEPTA.TRAIN_V2.TEST_ONLY.PREWARM.V1\x00"
SIGNER=r'''
import sys,json,time,socket,hashlib
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
D=b"HEPTA.TRAIN_V2.TEST_ONLY.PREWARM.V1\x00"
key=Ed25519PrivateKey.generate()
pub=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()
print(json.dumps({"kind":"READY","host":socket.gethostname(),"pub":pub,
"memory_only":True,"test_only":True}),flush=True)
for line in sys.stdin:
 try:
  x=json.loads(line); b=bytes.fromhex(x["body"])
  if x["kind"]!="SIGN" or not b.startswith(D) or len(b)>1024:raise ValueError("DOMAIN")
  t=time.perf_counter_ns();sig=key.sign(b).hex();elapsed=(time.perf_counter_ns()-t)//1000
  print(json.dumps({"kind":"SIGNED","seq":x["seq"],"sig":sig,
   "body_sha":hashlib.sha256(b).hexdigest(),"crypto_us":elapsed,
   "signed_at":time.time_ns()//1000000,"test_only":True}),flush=True)
 except Exception as e: print(json.dumps({"kind":"REJECT","error":type(e).__name__}),flush=True)
'''
RECEIVER=r'''
import os,sys,time,json,hashlib,socket
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
D=b"HEPTA.TRAIN_V2.TEST_ONLY.PREWARM.V1\x00"
root=Path(sys.argv[1])
root.mkdir(parents=True,mode=0o700,exist_ok=False);root.chmod(0o700)
print(json.dumps({"kind":"READY","host":socket.gethostname(),"test_only":True}),flush=True)
for line in sys.stdin:
 try:
  x=json.loads(line);b=bytes.fromhex(x["body"]);pub=bytes.fromhex(x["pub"])
  sig=bytes.fromhex(x["sig"]);seq=x["seq"]
  if (x["kind"]!="ACK" or type(seq)is not int or seq<0 or seq>32
      or not b.startswith(D) or len(b)>1024 or len(pub)!=32 or len(sig)!=64):
   raise ValueError("SHAPE")
  Ed25519PublicKey.from_public_bytes(pub).verify(sig,b)
  body={"schema":"heptatrader.train-v2-test-only-fsynced-signature.v1",
   "seq":seq,"msg_sha256":hashlib.sha256(b).hexdigest(),
   "pub_sha256":hashlib.sha256(pub).hexdigest(),
   "signature_sha256":hashlib.sha256(sig).hexdigest(),
   "ack_at_ms":time.time_ns()//1000000,"original_market_data":False,
   "native_writer":False,"product_owner":False,"trading":False}
  raw=(json.dumps(body,sort_keys=True)+"\n").encode()
  fd=os.open(root/("receipt-%02d.json"%seq),
    os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
  with os.fdopen(fd,"wb") as f:f.write(raw);f.flush();os.fsync(f.fileno())
  dd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
  try:os.fsync(dd)
  finally:os.close(dd)
  print(json.dumps({"kind":"DURABLE","seq":seq,"hash":hashlib.sha256(raw).hexdigest(),
                    "test_only":True}),flush=True)
 except Exception as e:print(json.dumps({"kind":"REJECT","error":type(e).__name__}),flush=True)
'''

def receive(pipe,secs=7):
 if not select.select([pipe],[],[],secs)[0]:raise TimeoutError("REMOTE_NOT_RESPONDING")
 line=pipe.readline()
 if not line or len(line)>14000:raise RuntimeError("REMOTE_EXIT_OR_SIZE")
 return json.loads(line)

def session(host,code,*args):
 cmd="python3 -u -c "+shlex.quote(code)
 for arg in args:cmd+=" "+shlex.quote(str(arg))
 start=time.perf_counter_ns()
 p=subprocess.Popen(["ssh","-o","BatchMode=yes","-o","ConnectTimeout=5",
      "-o","StrictHostKeyChecking=yes",host,cmd],
      stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
 try:
  ready=receive(p.stdout,10)
  if ready.get("kind")!="READY" or ready.get("test_only") is not True:
   raise RuntimeError("REMOTE_REJECTED_STARTUP")
 except BaseException:
  p.kill();p.communicate(timeout=3);raise
 return p,ready,(time.perf_counter_ns()-start)/1e6

def exchange(proc,doc):
 proc.stdin.write(json.dumps(doc,sort_keys=True)+"\n");proc.stdin.flush()
 return receive(proc.stdout)

def close(proc):
 if proc.poll() is None:
  proc.stdin.close()
  try:proc.wait(timeout=4)
  except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=4)
 if proc.returncode:raise RuntimeError("REMOTE_FAILED")

def percentile(values,q):
 v=sorted(values);idx=(len(v)-1)*q;lo=int(idx);hi=min(lo+1,len(v)-1)
 return round(v[lo]*(hi-idx)+v[hi]*(idx-lo),3)

def measure(*,mac,receiver,remote_dir,samples):
 from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
 if not 3<=samples<=20 or not remote_dir.is_absolute():raise ValueError("BAD_LIMIT")
 a=b=None
 try:
  a,ar,astart=session(mac,SIGNER)
  b,br,bstart=session(receiver,RECEIVER,remote_dir)
  if (ar["host"]!="MacBook-Pro.local" or br["host"]!="alex-G1628-04"
      or ar["memory_only"] is not True):raise ValueError("UNEXPECTED_REAL_HOST")
  key=Ed25519PublicKey.from_public_bytes(bytes.fromhex(ar["pub"]))
  rows=[]
  for i in range(samples):
   msg=DOMAIN+hashlib.sha256((str(i)+":"+str(time.time_ns())).encode()).digest()
   t=time.perf_counter_ns()
   signed=exchange(a,{"kind":"SIGN","seq":i,"body":msg.hex()})
   if signed["kind"]!="SIGNED" or signed["seq"]!=i or signed["body_sha"]!=hashlib.sha256(msg).hexdigest():
    raise RuntimeError("MAC_INVALID_REPLY")
   key.verify(bytes.fromhex(signed["sig"]),msg)
   ms_signed=(time.perf_counter_ns()-t)/1e6
   receipt=exchange(b,{"kind":"ACK","seq":i,"body":msg.hex(),
                         "pub":ar["pub"],"sig":signed["sig"]})
   ms_ack=(time.perf_counter_ns()-t)/1e6
   if receipt["kind"]!="DURABLE" or receipt["seq"]!=i or receipt["test_only"] is not True:
    raise RuntimeError("POCKET4_WITNESS_NOT_DURABLE")
   rows.append({"seq":i,"mac_sign_roundtrip_ms":round(ms_signed,3),
                "mac_crypto_us":signed["crypto_us"],
                "two_host_signed_file_fsync_ms":round(ms_ack,3),
                "pocket4_receipt_sha256":receipt["hash"]})
  return {"schema":"heptatrader.train-v2-physical-prewarmed-testonly-latency.v1",
   "at_utc":datetime.now(timezone.utc).isoformat(),
   "mac_host":ar["host"],"pocket4_host":br["host"],
   "mac_ssh_startup_ms":round(astart,3),
   "pocket4_ssh_startup_ms":round(bstart,3),
   "samples":len(rows),"mac_p95_ms":percentile([x["mac_sign_roundtrip_ms"] for x in rows],.95),
   "test_twohost_signature_file_fsync_p95_ms":percentile([x["two_host_signed_file_fsync_ms"] for x in rows],.95),
   "all_test_twohost_samples_below_5s":all(x["two_host_signed_file_fsync_ms"]<5000 for x in rows),
   "test_only_memory_key_not_exported":True,
   "signature_domain_sha256":hashlib.sha256(DOMAIN).hexdigest(),
   "synthetic_test_data_only":True,"original_source_used":False,
   "native_ledger_or_witness_acked":False,"canonical_product_owner_enrolled":False,
   "actual_model_prediction":False,"trade_authority":False,
   "broker_requests_sent":0,"broker_orders_sent":0,"rows":rows}
 finally:
  try:
   if a:close(a)
  finally:
   if b:close(b)

def main():
 p=argparse.ArgumentParser()
 p.add_argument("--mac",default="mac-ts")
 p.add_argument("--pocket4",default="pocket4")
 p.add_argument("--remote-dir",required=True,type=Path)
 p.add_argument("--output",required=True,type=Path)
 p.add_argument("--samples",type=int,default=12)
 z=p.parse_args();os.umask(0o077)
 report=measure(mac=z.mac,receiver=z.pocket4,remote_dir=z.remote_dir,samples=z.samples)
 if z.output.exists() or z.output.is_symlink():raise ValueError("IMMUTABLE_NEW_OUTPUT")
 z.output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 raw=(json.dumps(report,sort_keys=True,indent=2)+"\n").encode()
 fd=os.open(z.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,"wb") as f:f.write(raw);f.flush();os.fsync(f.fileno())
 print(json.dumps({"test_only_p95_ms":report["test_twohost_signature_file_fsync_p95_ms"],
   "warm_signer_p95_ms":report["mac_p95_ms"],
   "native_witness_acked":False,"broker_orders":0,
   "sha256":hashlib.sha256(raw).hexdigest()},sort_keys=True))

if __name__=="__main__":main()
