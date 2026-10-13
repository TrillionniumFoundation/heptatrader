"""Pack exact naturally completed 480-cycle original health research.

No extraction, broker sockets, orders, production service change, or new
readiness decision. This is a read-only portability aid; only the separately
pinned full480 verifier can give an independent integrity verdict.
"""
from __future__ import annotations
import argparse,hashlib,io,json,os,stat,tarfile
from pathlib import Path

PIN={
 "plan":"490375e6cfc4cea69b0aeb8ece2bd836228d1b3d086793555ed35d920bc8f6be",
 "worker":"3b880a8bd59b515b128a5566e713767ac822862d695f1f8c4237bb018fbc0d59",
 "sandbox":"b18abaf07255b4183f96dcdad8ed5059cbbd8e2f82616c81cc5597fef513acaa",
}
COHORT="heptatrader.fx-original-sustained-source-archive.v1"
class ExportHold(ValueError):pass
def require(value,why):
 if not value:raise ExportHold(why)
def sha(x):return hashlib.sha256(x).hexdigest()
def canonical(x):return json.dumps(x,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def strict(x):
 def unique(pairs):
  out={}
  for k,v in pairs:
   require(k not in out,"DUPLICATE_JSON_KEY")
   out[k]=v
  return out
 return json.loads(x,object_pairs_hook=unique,
  parse_constant=lambda _:(_ for _ in ()).throw(ExportHold("NONFINITE_JSON")))
def exact(root,name):
 p=root/name
 require(not p.is_symlink(),"UNSAFE_ORIGINAL_SOURCE_LINK")
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  first=os.fstat(fd)
  require(stat.S_ISREG(first.st_mode) and first.st_uid==os.geteuid()
          and first.st_nlink==1 and not first.st_mode&0o077
          and 0<first.st_size<1024*1024,"UNSAFE_ORIGINAL_SOURCE_BYTES")
  data=b""
  while len(data)<first.st_size:
   block=os.read(fd,min(65536,first.st_size-len(data)))
   require(bool(block),"TRUNCATED_ORIGINAL_READ")
   data+=block
  last=os.fstat(fd)
  require((first.st_dev,first.st_ino,first.st_size,first.st_mtime_ns)==
          (last.st_dev,last.st_ino,last.st_size,last.st_mtime_ns),
          "ORIGINAL_SOURCE_CHANGED_DURING_READ")
  return data
 finally:os.close(fd)

def collect(root,pins=PIN):
 root=Path(root)
 require(root.is_absolute() and not root.is_symlink() and root.is_dir()
         and root.stat().st_uid==os.geteuid()
         and stat.S_IMODE(root.stat().st_mode)==0o700,
         "PRIVATE_ORIGINAL_ARCHIVE_ROOT")
 source={}
 for k,f in (("plan","plan.json"),("worker","worker-start.json"),
             ("sandbox","sandbox.json")):
  b=exact(root,f)
  require(sha(b)==pins[k],"PRESEALED_ORIGINAL_ROOT_CHANGED")
  source[f]=b
 full=exact(root,"final.json")
 result=strict(full)
 require(result.get("schema")==COHORT+".final.v1"
         and result.get("plan_sha256")==pins["plan"]
         and result.get("broker_orders_sent")==0
         and result.get("production_trading_grant") is False
         and result.get("no_prod_monitor_cutover") is True
         and type(result.get("cycles")) is list
         and len(result["cycles"])==480,
         "FULL_480_NATURAL_TERMINAL_NOT_PRESENT")
 source["final.json"]=full
 previous="0"*64
 for i in range(480):
  name=f"cycle-{i:02d}-receipt.json"
  raw=exact(root,name)
  r=strict(raw);h=sha(raw)
  require(r.get("schema")==COHORT+".cycle.v1"
          and r.get("index")==i and r.get("previous_receipt_sha256")==previous
          and r.get("trade_authority") is False and r.get("orders_sent")==0
          and result["cycles"][i]=={
             "index":i,"status":r["state"],"receipt_sha256":h,
             "source_ready":r["source_ready"]},
          "FULL_480_RECEIPT_CHAIN_DISAGREES")
  previous=h
  source[name]=raw
  label=f"cycle-{i:02d}-status.json"
  if r.get("status_sha256") is not None:
   q=exact(root,label)
   require(sha(q)==r["status_sha256"],
           "FULL_480_ORIGINAL_SOURCE_PREFIX_MISMATCH")
   source[label]=q
  else:
   require(not (root/label).exists(),
           "CENSORED_ORIGINAL_STATUS_RELABELED")
 require(result["receipt_chain_head"]==previous
         and result["full_snapshots_written"]==sum(
             1 for n in source if n.endswith("-status.json")),
         "FULL_480_TERMINAL_COUNTS_CHANGED")
 return source

def pack(root,output,pins=PIN):
 files=collect(root,pins)
 output=Path(output)
 require(output.is_absolute() and not output.exists() and
         not output.is_symlink() and output.parent.is_dir()
         and output.parent.stat().st_uid==os.geteuid()
         and stat.S_IMODE(output.parent.stat().st_mode)==0o700,
         "PRIVATE_NEW_ARCHIVE_DESTINATION")
 fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,"wb") as f:
  with tarfile.open(fileobj=f,mode="w:gz",compresslevel=7) as tf:
   for name,raw in sorted(files.items()):
    entry=tarfile.TarInfo(name)
    entry.size=len(raw);entry.mode=0o600
    tf.addfile(entry,io.BytesIO(raw))
  f.flush();os.fsync(f.fileno())
 d=os.open(output.parent,os.O_RDONLY|os.O_DIRECTORY)
 try:os.fsync(d)
 finally:os.close(d)
 return {"state":"READONLY_EXACT_480_ARCHIVE_CREATED_NOT_A_PRODUCTION_PASS",
         "original_files":len(files),"original_health_status_files":
             sum(n.endswith("-status.json") for n in files),
         "archive_sha256":sha(output.read_bytes()),
         "broker_orders_sent":0,"production_cutover_authorized":False}

def main():
 p=argparse.ArgumentParser()
 p.add_argument("--root",required=True,type=Path)
 p.add_argument("--output",required=True,type=Path)
 a=p.parse_args()
 print(json.dumps(pack(a.root,a.output),sort_keys=True))
if __name__=="__main__":main()
