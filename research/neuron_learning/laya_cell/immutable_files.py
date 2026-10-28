"""Verified no-clobber bytes for research package installers.

Source aliases retain the existing follow-symlink policy. Canonical destination
leaves are ordinary single-link files in the requested mode; unknown competing files are
preserved. This module has no service, runtime-selection or trading authority.
"""
from __future__ import annotations
import errno,hashlib,io,os,stat,sys,tempfile
from pathlib import Path
from .integrity import ContractError


def _version(s):
 return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)


def text_snapshot(path,reason):
 """Read an ordinary file once, retaining leaf and followed-target identity."""
 leaf=path.lstat()
 try:
  target=path.stat()
  raw=b''.join(_chunks(path,reason,follow_symlinks=True))
  after=(path.lstat(),path.stat())
 except FileNotFoundError as exc:raise ContractError(reason) from exc
 versions=(_version(leaf),_version(target))
 if versions!=tuple(_version(s) for s in after):raise ContractError(reason)
 with io.TextIOWrapper(io.BytesIO(raw)) as stream:text=stream.read()
 return text,raw,versions


def replace_snapshot(path,raw,mode,reason,previous):
 """Replace a checked file using only an owned unique temporary.

 The final snapshot check rejects observed target changes. POSIX rename does
 not provide compare-and-swap: a change after that check can still race it.
 """
 fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
 temporary=Path(name)
 try:
  offset=0
  while offset<len(raw):
   written=os.write(fd,raw[offset:])
   if written<=0:raise OSError('UNIT_SHORT_WRITE')
   offset+=written
  os.fchmod(fd,mode);os.fsync(fd)
  staged=_version(os.fstat(fd))
  if not _matches(temporary,hashlib.sha256(raw).hexdigest(),reason,raw,mode):raise ContractError(reason)
  try:current=text_snapshot(path,reason)
  except FileNotFoundError as exc:raise ContractError(reason) from exc
  if current!=previous:raise ContractError(reason)
  if staged!=_version(os.fstat(fd)) or staged!=_version(temporary.lstat()):raise ContractError(reason)
  os.replace(temporary,path)
  directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
  try:os.fsync(directory)
  finally:
   primary=sys.exc_info()[0] is not None
   try:os.close(directory)
   except Exception:
    if not primary:raise
 finally:
  primary=sys.exc_info()[0] is not None;cleanup=[]
  try:
   found=temporary.lstat();opened=os.fstat(fd)
   if (found.st_dev,found.st_ino)==(opened.st_dev,opened.st_ino):temporary.unlink()
   else:cleanup.append(ContractError(reason))
  except FileNotFoundError:pass
  except BaseException as exc:cleanup.append(exc)
  try:os.close(fd)
  except BaseException as exc:cleanup.append(exc)
  for exc in cleanup:
   if not isinstance(exc,Exception):raise exc
  if cleanup and not primary:raise cleanup[0]


def _chunks(path,reason,*,follow_symlinks,mode=None):
 flags=os.O_RDONLY|os.O_NONBLOCK|os.O_CLOEXEC
 if not follow_symlinks:flags|=os.O_NOFOLLOW
 try:fd=os.open(path,flags)
 except OSError as exc:
  if exc.errno==errno.ELOOP:raise ContractError(reason) from exc
  raise
 try:
  before=os.fstat(fd)
  if not stat.S_ISREG(before.st_mode):raise ContractError(reason)
  if mode is not None and (stat.S_IMODE(before.st_mode)!=mode or before.st_nlink!=1):raise ContractError(reason)
  remaining=before.st_size+1;length=0
  while remaining:
   chunk=os.read(fd,min(1024**2,remaining))
   if not chunk:break
   remaining-=len(chunk);length+=len(chunk)
   yield chunk
  version=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
  try:after_path=Path(path).stat(follow_symlinks=follow_symlinks)
  except FileNotFoundError as exc:raise ContractError(reason) from exc
  if (length!=before.st_size or version(before)!=version(os.fstat(fd))
      or version(before)!=version(after_path)):
   raise ContractError(reason)
 finally:
  primary=sys.exc_info()[0] is not None
  try:os.close(fd)
  except Exception:
   if not primary:raise


def _matches(path,expected,reason,raw,mode):
 checksum=hashlib.sha256();length=0
 try:
  for chunk in _chunks(path,reason,follow_symlinks=False,mode=mode):
   if raw is not None and chunk!=raw[length:length+len(chunk)]:raise ContractError(reason)
   checksum.update(chunk);length+=len(chunk)
 except FileNotFoundError:return False
 if checksum.hexdigest()!=expected or raw is not None and length!=len(raw):raise ContractError(reason)
 return True


def write_immutable(path,content,mode,reason,expected=None):
 """Publish verified bytes without replacing a competing canonical name."""
 raw=content if isinstance(content,bytes) else None
 if raw is not None:expected=hashlib.sha256(raw).hexdigest()
 if _matches(path,expected,reason,raw,mode):return
 fd,temporary=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
 temporary=Path(temporary)
 try:
  checksum=hashlib.sha256();length=0
  chunks=(raw,) if raw is not None else _chunks(content,reason,follow_symlinks=True)
  try:
   for chunk in chunks:
    checksum.update(chunk);length+=len(chunk);offset=0
    while offset<len(chunk):
     written=os.write(fd,chunk[offset:])
     if written<=0:raise OSError('IMMUTABLE_SHORT_WRITE')
     offset+=written
  finally:
   if raw is None:
    primary=sys.exc_info()[0] is not None
    try:chunks.close()
    except Exception:
     if not primary:raise
  if checksum.hexdigest()!=expected:raise ContractError(reason)
  os.fsync(fd);os.lseek(fd,0,os.SEEK_SET)
  # Verify the actual staged inode, not only the bytes supplied to write().
  checksum=hashlib.sha256();remaining=length+1;read_length=0
  while remaining:
   chunk=os.read(fd,min(1024**2,remaining))
   if not chunk:break
   checksum.update(chunk);remaining-=len(chunk);read_length+=len(chunk)
  if checksum.hexdigest()!=expected or read_length!=length:raise ContractError(reason)
  os.fchmod(fd,mode);os.fsync(fd)
  identity=lambda s:(s.st_dev,s.st_ino)
  if identity(temporary.lstat())!=identity(os.fstat(fd)):raise ContractError(reason)
  try:os.link(temporary,path,follow_symlinks=False)
  except FileExistsError:
   if not _matches(path,expected,reason,raw,mode):raise ContractError(reason)
  else:
   if identity(path.lstat())!=identity(os.fstat(fd)):raise ContractError(reason)
 finally:
  primary=sys.exc_info()[0] is not None;cleanup=[]
  try:
   found=temporary.lstat();opened=os.fstat(fd)
   if (found.st_dev,found.st_ino)==(opened.st_dev,opened.st_ino):
    temporary.unlink()
   else:cleanup.append(ContractError(reason))
  except FileNotFoundError:pass
  except BaseException as exc:cleanup.append(exc)
  try:os.close(fd)
  except BaseException as exc:cleanup.append(exc)
  for exc in cleanup:
   if not isinstance(exc,Exception):raise exc
  if cleanup and not primary:raise cleanup[0]
 directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
 try:os.fsync(directory)
 finally:
  primary=sys.exc_info()[0] is not None
  try:os.close(directory)
  except Exception:
   if not primary:raise
