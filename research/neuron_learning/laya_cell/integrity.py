"""Pin complete upstream Laya weights; never substitute encoder-only initialization."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib,json,os,stat
from pathlib import Path,PurePosixPath
from typing import Any
LAYA_REPO='convaiinnovations/laya-multilingual'
LAYA_REVISION='e4e9ddf21a7b1903b7acffd8814ad4307bf63a67'
LAYA_CODE_COMMIT='a4a8921afebfd852bba0000475cfb6ab737a124c'
LAYA_VERSION='0.3.28'
CODE_BLOBS={'common.py':'45b5e2a43d437cb50e4ac41320c4c18181749cda','revisions.py':'85a31f3d4bf902b9c9cde4d22787eb3618510f29','__init__.py':'ba200d91f214e062ea7b0c86c47d1caf89ef8298'}
REQUIRED={'model.safetensors','rl_agent_config.json','encoder/config.json','tokenizer/tokenizer.json','tokenizer/tokenizer_config.json'}
MAX_FILES=128
MAX_MODEL_BYTES=3*1024**3
class ContractError(ValueError):pass
def canonical(value:Any)->bytes:return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(value:Any)->str:return hashlib.sha256(canonical(value)).hexdigest()
@contextmanager
def _regular_stream(path:Path,error:str):
 # Keep callers' existing path/symlink policies; reject streams before reading.
 fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_CLOEXEC)
 try:
  st=os.fstat(fd)
  if not stat.S_ISREG(st.st_mode):raise ContractError(error)
  with os.fdopen(fd,'rb',closefd=False) as stream:yield stream,st
 finally:os.close(fd)
def _read_json_bytes(path:Path,maximum:int)->bytes:
 with _regular_stream(path,'JSON_FILE_TYPE') as (stream,st):
  if st.st_size>maximum:raise ContractError('JSON_SIZE_BOUND')
  raw=stream.read(maximum+1)
  if len(raw)>maximum:raise ContractError('JSON_SIZE_BOUND')
 return raw
def _parse_json(raw:bytes)->Any:
 def unique(items):
  out={}
  for k,v in items:
   if k in out:raise ContractError('DUPLICATE_JSON_KEY')
   out[k]=v
  return out
 return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(ContractError('NONFINITE_JSON')))
def read_json(path:Path,maximum:int=1024**2)->Any:
 return _parse_json(_read_json_bytes(path,maximum))
def read_json_snapshot(path:Path,maximum:int=1024**2)->tuple[Any,str]:
 """Parse and hash the same bounded bytes, without reopening the pathname."""
 raw=_read_json_bytes(path,maximum)
 return _parse_json(raw),hashlib.sha256(raw).hexdigest()
def safe_child(root:Path,relative:str)->Path:
 if not isinstance(relative,str):raise ContractError('UNSAFE_ARTIFACT_PATH')
 p=PurePosixPath(relative)
 if '\\' in relative or p.is_absolute() or '..' in p.parts or not p.parts:raise ContractError('UNSAFE_ARTIFACT_PATH')
 here=root
 for part in p.parts:
  here/=part
  if here.is_symlink():raise ContractError('ARTIFACT_SYMLINK')
 return here
def sha_file(path:Path)->str:
 h=hashlib.sha256()
 with _regular_stream(path,'HASH_FILE_TYPE') as (s,_):
  for c in iter(lambda:s.read(1024**2),b''):h.update(c)
 return h.hexdigest()
def validate_candidate_artifact(path:Path)->dict:
 """Verify resident manifests/adapters without importing a model or trainer.

 Keep the original parent-validation errors for existing callers and receipts.
 """
 candidate=read_json(path/'candidate.json')
 if candidate.get('candidate_id')!=digest({k:v for k,v in candidate.items() if k!='candidate_id'}):raise ContractError('PARENT_MANIFEST_HASH')
 if sha_file(path/'adapter.safetensors')!=candidate['adapter_sha256']:raise ContractError('PARENT_ADAPTER_HASH')
 return candidate
def git_blob(raw:bytes)->str:return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
def verify_snapshot(root:Path,manifest:dict)->str:
 if root.is_symlink() or not root.is_dir():raise ContractError('MODEL_DIRECTORY_INVALID')
 if manifest.get('schema')!='heptatrader.laya-base.v1' or manifest.get('repository')!=LAYA_REPO or manifest.get('revision')!=LAYA_REVISION:raise ContractError('BASE_IDENTITY_MISMATCH')
 inv=manifest.get('files')
 if not isinstance(inv,dict) or not REQUIRED<=set(inv) or len(inv)>MAX_FILES:raise ContractError('FULL_LAYA_CHECKPOINT_REQUIRED')
 if any(type(v.get('size')) is not int or v['size']<0 for v in inv.values()) or sum(v['size'] for v in inv.values())>MAX_MODEL_BYTES:raise ContractError('MODEL_SIZE_BOUND')
 for name,item in inv.items():
  path=safe_child(root,name);st=path.stat()
  if not stat.S_ISREG(st.st_mode) or st.st_size!=item['size']:raise ContractError('SNAPSHOT_SIZE_MISMATCH:'+name)
  if sha_file(path)!=item.get('sha256'):raise ContractError('SNAPSHOT_HASH_MISMATCH:'+name)
 cfg=read_json(root/'encoder/config.json')
 if cfg.get('model_type')!='modernbert' or cfg.get('auto_map'):raise ContractError('UNREVIEWED_ENCODER_ARCHITECTURE')
 if any(p.suffix in {'.bin','.pt','.pth','.pkl'} for p in root.rglob('*') if p.is_file()):raise ContractError('PICKLE_CHECKPOINT_FORBIDDEN')
 return digest(manifest)
def acquire_pinned_snapshot(destination:Path,cache:Path|None=None)->dict:
 """Verify cached or downloaded bytes against pinned Hub LFS/Git identities."""
 from huggingface_hub import HfApi,hf_hub_download
 if destination.exists():raise ContractError('SNAPSHOT_DESTINATION_EXISTS')
 info=HfApi(token=False).model_info(LAYA_REPO,revision=LAYA_REVISION,files_metadata=True)
 if info.sha!=LAYA_REVISION:raise ContractError('UPSTREAM_REVISION_CHANGED')
 selected=[s for s in info.siblings if s.rfilename in ('model.safetensors','rl_agent_config.json') or s.rfilename.startswith(('encoder/','tokenizer/')) or s.rfilename.upper().startswith(('LICENSE','NOTICE'))]
 if not REQUIRED<={s.rfilename for s in selected} or len(selected)>MAX_FILES:raise ContractError('UPSTREAM_REQUIRED_FILES_MISSING')
 if any(type(s.size) is not int or s.size<0 for s in selected) or sum(s.size for s in selected)>MAX_MODEL_BYTES:raise ContractError('UPSTREAM_FILE_BUDGET')
 destination.mkdir(mode=0o700,parents=True);inventory={}
 for sibling in selected:
  target=safe_child(destination,sibling.rfilename);target.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
  cached=safe_child(cache,sibling.rfilename) if cache else None
  downloaded=cached if cached and cached.is_file() else Path(hf_hub_download(LAYA_REPO,sibling.rfilename,revision=LAYA_REVISION,token=False))
  raw=downloaded.read_bytes()
  if len(raw)!=sibling.size:raise ContractError('UPSTREAM_SIZE_MISMATCH:'+sibling.rfilename)
  lfs=sibling.lfs;upstream=(getattr(lfs,'sha256',None) or (lfs.get('sha256') if isinstance(lfs,dict) else None)) if lfs else None
  if upstream:
   if hashlib.sha256(raw).hexdigest()!=upstream:raise ContractError('UPSTREAM_LFS_HASH_MISMATCH')
  elif git_blob(raw)!=sibling.blob_id:raise ContractError('UPSTREAM_GIT_BLOB_MISMATCH')
  with target.open('xb') as out:out.write(raw);out.flush();os.fsync(out.fileno())
  target.chmod(0o400)
  inventory[sibling.rfilename]={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'upstream_blob':sibling.blob_id,'upstream_lfs_sha256':upstream}
 result={'schema':'heptatrader.laya-base.v1','repository':LAYA_REPO,'revision':LAYA_REVISION,'code_commit':LAYA_CODE_COMMIT,'files':inventory,'provenance_scope':'PINNED_UPSTREAM_HASH_CHECK_NOT_VENDOR_SIGNATURE'}
 verify_snapshot(destination,result)
 with (destination/'base-manifest.json').open('xb') as out:out.write(canonical(result)+b'\n');out.flush();os.fsync(out.fileno())
 (destination/'base-manifest.json').chmod(0o400)
 return result
