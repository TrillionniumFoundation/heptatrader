"""Local scheduling spool only; not a canonical Agentd fact/authority store."""
from __future__ import annotations
import json,os,sqlite3
from pathlib import Path
from .integrity import ContractError,canonical,read_json,digest


def write_once(path,value):
 path=Path(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);raw=canonical(value)+b'\n'
 if path.exists():
  if path.read_bytes()!=raw:raise ContractError('IMMUTABLE_RECORD_CONFLICT')
  return
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
 with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 parent=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
 try:os.fsync(parent)
 finally:os.close(parent)

def atomic(path,value):
 path=Path(path);tmp=path.with_name(path.name+'.next');raw=canonical(value)+b'\n'
 fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
 with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 os.replace(tmp,path)

def database(path):
 db=sqlite3.connect(path,timeout=5);db.row_factory=sqlite3.Row
 db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
 db.executescript('''CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,fingerprint TEXT UNIQUE NOT NULL,status TEXT NOT NULL,created_ms INTEGER NOT NULL,candidate_id TEXT,payload TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS consumed_rows(id TEXT PRIMARY KEY,job_id TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS rewards(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,at_ms INTEGER NOT NULL,payload TEXT NOT NULL);''')
 return db

def get_meta(db,key,default=None):
 r=db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone();return json.loads(r[0]) if r else default

def put_meta(db,key,value):db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',(key,json.dumps(value,sort_keys=True)))

def admit_job(db,job_id,fingerprint,created_ms,job,rows):
 with db:
  if db.execute('SELECT 1 FROM jobs WHERE fingerprint=?',(fingerprint,)).fetchone():return False
  if any(db.execute('SELECT 1 FROM consumed_rows WHERE id=?',(r,)).fetchone() for r in rows):
   raise ContractError('ROW_ALREADY_CONSUMED_BY_ANOTHER_JOB')
  db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',(job_id,fingerprint,'ADMITTED',created_ms,None,canonical(job).decode()))
  for r in rows:db.execute('INSERT INTO consumed_rows VALUES (?,?)',(r,job_id))
  put_meta(db,'last_job_created_ms',created_ms)
 return True

def complete_job(db,job_id,candidate):
 with db:
  r=db.execute('SELECT status,candidate_id FROM jobs WHERE id=?',(job_id,)).fetchone()
  if r is None:raise ContractError('NO_ADMITTED_JOB')
  if r['status']=='COMPLETED':
   if r['candidate_id']!=candidate['candidate_id']:raise ContractError('JOB_CANDIDATE_CHANGED')
   return False
  db.execute('UPDATE jobs SET status=?,candidate_id=? WHERE id=?',('COMPLETED',candidate['candidate_id'],job_id))
  put_meta(db,'last_completed_candidate',candidate['candidate_id'])
 return True
