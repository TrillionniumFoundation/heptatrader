from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import resource
import sys


class SandboxError(RuntimeError):
    pass


_NETWORK_SYSCALLS = (
    "socket",
    "socketcall",
    "connect",
    "bind",
    "listen",
    "accept",
    "accept4",
    "sendto",
    "sendmsg",
    "sendmmsg",
    "recvfrom",
    "recvmsg",
    "recvmmsg",
    "shutdown",
    "getsockname",
    "getpeername",
    "setsockopt",
    "getsockopt",
    "io_uring_setup",
)


def close_inherited_descriptors() -> None:
    """Remove ambient descriptors while preserving stdio for CLI results."""
    soft_limit = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    if soft_limit == resource.RLIM_INFINITY:
        soft_limit = 1_048_576
    os.closerange(3, min(int(soft_limit), 1_048_576))


def apply_no_network_filter() -> None:
    """Install an exec-persistent Linux seccomp filter for offline jobs."""
    if not sys.platform.startswith("linux"):
        raise SandboxError("offline job sandbox requires Linux seccomp")
    library_name = ctypes.util.find_library("seccomp")
    if not library_name:
        raise SandboxError("libseccomp is required for offline operations")
    library = ctypes.CDLL(library_name, use_errno=True)
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    library.seccomp_rule_add.restype = ctypes.c_int
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_load.restype = ctypes.c_int
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    library.seccomp_release.restype = None

    action_allow = 0x7FFF0000
    action_errno = 0x00050000 | errno.EPERM
    context = library.seccomp_init(action_allow)
    if not context:
        raise SandboxError("seccomp filter allocation failed")
    resolved = 0
    try:
        for name in _NETWORK_SYSCALLS:
            number = library.seccomp_syscall_resolve_name(name.encode("ascii"))
            if number < 0:
                continue
            result = library.seccomp_rule_add(
                context, action_errno, number, 0)
            if result != 0:
                raise SandboxError(
                    f"seccomp rule failed for {name}: errno {-result}")
            resolved += 1
        if resolved == 0:
            raise SandboxError("seccomp resolved no network syscalls")
        result = library.seccomp_load(context)
        if result != 0:
            raise SandboxError(f"seccomp load failed: errno {-result}")
    finally:
        library.seccomp_release(context)
