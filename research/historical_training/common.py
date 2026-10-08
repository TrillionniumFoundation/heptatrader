"""Small evidence helpers; no network or execution imports."""
from __future__ import annotations
import hashlib,json,os,tempfile
from pathlib import Path

class EvidenceError(ValueError):
    pass

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()

def read(path,limit=64*1024*1024):
    p=Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size>limit:
        raise EvidenceError('INVALID_OR_OVERSIZED_FILE:'+str(p))
    def pairs(items):
        out={}
        for k,v in items:
            if k in out: raise EvidenceError('DUPLICATE_JSON_KEY')
            out[k]=v
        return out
    return json.loads(p.read_bytes(),object_pairs_hook=pairs,
        parse_constant=lambda _:(_ for _ in ()).throw(EvidenceError('NONFINITE_JSON')))

def atomic(path,value):
    path=Path(path)
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if path.is_symlink(): raise EvidenceError('OUTPUT_SYMLINK')
    fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(canonical(value)+b'\n');f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
        dfd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def write_once(path,value):
    path=Path(path);raw=canonical(value)+b'\n'
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if path.exists():
        if path.is_symlink() or path.read_bytes()!=raw: raise EvidenceError('IMMUTABLE_EVIDENCE_CONFLICT')
        return
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400)
    with os.fdopen(fd,'wb') as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
