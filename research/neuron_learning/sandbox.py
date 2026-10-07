"""Unprivileged, fail-closed Landlock + seccomp sandbox for pocket4 sidecars.

No kernel/sysctl/capability changes. Restrictions are irreversible for the worker
and inherited by its children. Apply while single-threaded, before GPU loading.
"""
from __future__ import annotations
import ctypes as C
import errno
import json
import os
from pathlib import Path
import platform
import socket
import stat
import uuid

class RuleSet(C.Structure):
    _fields_=[('fs',C.c_uint64),('net',C.c_uint64),('scoped',C.c_uint64)]
class PathRule(C.Structure):
    _pack_=1
    _fields_=[('access',C.c_uint64),('parent_fd',C.c_int32)]


def enter(config):
    if platform.machine()!='x86_64':raise RuntimeError('SANDBOX_X86_64_REQUIRED')
    if len(list(Path('/proc/self/task').iterdir()))!=1:
        raise RuntimeError('SANDBOX_MUST_PRECEDE_WORKER_THREADS')
    workspace=Path(config["output_dir"])/"gpu-tmp"
    workspace.mkdir(mode=0o700,parents=True,exist_ok=True)
    os.environ["TMPDIR"]=str(workspace)
    os.environ["AMD_COMGR_CACHE_DIR"]=str(workspace/"comgr-cache")
    os.environ["XDG_CACHE_HOME"]=str(workspace/"cache")
    # MIOpen otherwise falls back to ~/.cache/miopen, intentionally unwritable
    # after Landlock. Keep all GPU compilation/database state inside the job root.
    miopen_db=workspace/"miopen-db";miopen_cache=workspace/"miopen-cache"
    miopen_db.mkdir(mode=0o700,exist_ok=True);miopen_cache.mkdir(mode=0o700,exist_ok=True)
    os.environ["MIOPEN_USER_DB_PATH"]=str(miopen_db)
    os.environ["MIOPEN_CUSTOM_CACHE_DIR"]=str(miopen_cache)
    libc=C.CDLL(None,use_errno=True);libc.syscall.restype=C.c_long
    abi=libc.syscall(C.c_long(444),C.c_void_p(),C.c_size_t(0),C.c_uint(1))
    if abi<6:raise RuntimeError('LANDLOCK_ABI_6_REQUIRED_NO_UNSANDBOXED_FALLBACK')
    # ABI3 filesystem rights, ABI4 TCP denial, ABI6 signal/abstract-UNIX scope.
    handled=(1<<15)-1
    attrs=RuleSet(handled,3,3)
    rs=libc.syscall(C.c_long(444),C.byref(attrs),C.c_size_t(C.sizeof(attrs)),C.c_uint(0))
    if rs<0:raise OSError(C.get_errno(),'LANDLOCK_CREATE')
    read=(1<<0)|(1<<2)|(1<<3)
    write=handled
    source=Path(__file__).resolve().parent
    readonly=[Path('/usr'),Path('/lib'),Path('/lib64'),Path('/etc'),Path('/opt/rocm'),Path('/sys'),
              Path('/proc/self'),Path('/proc/cpuinfo'),Path('/proc/meminfo'),Path('/proc/sys'),
              source,Path(config['period_path']),Path(config['legacy_capture']),Path(config['oms_path']),
              Path(config['export_dir'])]
    for extra in config.get('read_only_paths',[]):
        path=Path(extra)
        if not path.is_absolute() or path==Path('/'):
            raise RuntimeError('SANDBOX_EXTRA_PATH_INVALID')
        readonly.append(path)
    readwrite=[Path(config['output_dir']),Path('/dev/kfd'),Path('/dev/dri'),Path('/dev/null'),
               Path('/dev/urandom'),Path('/dev/random')]
    try:
        for paths,rights in ((readonly,read),(readwrite,write)):
            for path in paths:
                if not path.exists():continue
                fd=os.open(path,os.O_PATH|os.O_CLOEXEC)
                try:
                    allowed=rights
                    if not stat.S_ISDIR(os.fstat(fd).st_mode):
                        allowed&=(1<<0)|(1<<1)|(1<<2)|(1<<14)
                    rule=PathRule(allowed,fd)
                    if libc.syscall(C.c_long(445),C.c_int(rs),C.c_int(1),C.byref(rule),C.c_uint(0))<0:
                        raise OSError(C.get_errno(),'LANDLOCK_PATH_RULE:'+str(path))
                finally:os.close(fd)
        if libc.prctl(C.c_int(38),C.c_ulong(1),C.c_ulong(0),C.c_ulong(0),C.c_ulong(0)):
            raise OSError(C.get_errno(),'NO_NEW_PRIVS')
        if libc.syscall(C.c_long(446),C.c_int(rs),C.c_uint(0))<0:
            raise OSError(C.get_errno(),'LANDLOCK_RESTRICT')
    finally:os.close(rs)
    sec=C.CDLL('libseccomp.so.2',use_errno=True)
    sec.seccomp_init.argtypes=[C.c_uint32];sec.seccomp_init.restype=C.c_void_p
    sec.seccomp_syscall_resolve_name.argtypes=[C.c_char_p];sec.seccomp_syscall_resolve_name.restype=C.c_int
    sec.seccomp_rule_add.argtypes=[C.c_void_p,C.c_uint32,C.c_int,C.c_uint]
    sec.seccomp_load.argtypes=[C.c_void_p];sec.seccomp_release.argtypes=[C.c_void_p]
    ctx=sec.seccomp_init(0x7fff0000)
    if not ctx:raise RuntimeError('SECCOMP_INIT')
    try:
        for name in ('socket','connect','bind','listen','accept','accept4','ptrace','process_vm_readv','process_vm_writev'):
            nr=sec.seccomp_syscall_resolve_name(name.encode())
            if nr<0 or sec.seccomp_rule_add(ctx,0x00050000|errno.EPERM,nr,0):
                raise RuntimeError('SECCOMP_RULE:'+name)
        if sec.seccomp_load(ctx):raise RuntimeError('SECCOMP_LOAD')
    finally:sec.seccomp_release(ctx)
    # Real denial probes: never read a bearer or send bytes to a broker endpoint.
    report={'landlock_abi':abi,'landlock_enforced':True,'seccomp_enforced':True,
            'only_output_writable':False,'bearer_read_denied':False,'socket_creation_denied':False,
            'no_privileged_namespace_or_sysctl_change':True}
    token=Path.home()/'.config/heptatrader/paper.token'
    try:
        fd=os.open(token,os.O_RDONLY|os.O_CLOEXEC);os.close(fd)
    except PermissionError:report['bearer_read_denied']=True
    except FileNotFoundError:report['bearer_read_denied']='ABSENT_AND_NOT_ALLOWLISTED'
    else:raise RuntimeError('SANDBOX_BEARER_ACCESS_NOT_DENIED')
    try:
        sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM);sock.close()
    except PermissionError:report['socket_creation_denied']=True
    else:raise RuntimeError('SANDBOX_NETWORK_NOT_DENIED')
    test=Path.home()/('.hepta-learning-forbidden-'+uuid.uuid4().hex)
    try:
        fd=os.open(test,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC,0o600);os.close(fd)
    except PermissionError:report['only_output_writable']=True
    else:
        test.unlink();raise RuntimeError('SANDBOX_PARENT_WRITABLE')
    permitted=Path(config['output_dir'])/('.sandbox-probe-'+uuid.uuid4().hex)
    fd=os.open(permitted,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC,0o600);os.close(fd);permitted.unlink()
    report['output_write_verified']=True
    return report
