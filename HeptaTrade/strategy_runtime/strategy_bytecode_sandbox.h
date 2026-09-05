#pragma once

// Private Linux x86-64 defense-in-depth boundary for the allocation-free VM.
// This is NOT a general native-code sandbox: fork inherits the caller image.
#if defined(__linux__) && defined(__x86_64__)
#include <cerrno>
#include <climits>
#include <cstddef>
#include <cstdint>
#include <fcntl.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <signal.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <unistd.h>

namespace hepta_bytecode_detail
{
inline bool NarrowLimit(int resource, rlim_t value) noexcept
{
    struct rlimit previous{}, limit{};
    if (::getrlimit(resource, &previous) != 0) return false;
    limit.rlim_cur = limit.rlim_max = value < previous.rlim_cur ? value : previous.rlim_cur;
    return ::setrlimit(resource, &limit) == 0;
}
inline bool InstallGuards(int pipeFd, pid_t parent, std::uint64_t addressSpace,
                          unsigned cpuSeconds, unsigned wireBytes) noexcept
{
    if (::prctl(PR_SET_PDEATHSIG, SIGKILL, 0, 0, 0) != 0 || ::getppid() != parent ||
        ::prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0) return false;
    if (pipeFd != 3 && ::dup2(pipeFd, 3) != 3) return false;
    if (::syscall(SYS_close_range, 0u, 2u, 0u) != 0 ||
        ::syscall(SYS_close_range, 4u, UINT_MAX, 0u) != 0) return false;
    if (!NarrowLimit(RLIMIT_CORE, 0) || !NarrowLimit(RLIMIT_FSIZE, 0) ||
        !NarrowLimit(RLIMIT_NOFILE, 4) || !NarrowLimit(RLIMIT_NPROC, 0) ||
        !NarrowLimit(RLIMIT_CPU, cpuSeconds) || !NarrowLimit(RLIMIT_AS, addressSpace) ||
        ::prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0) return false;
    const struct sock_filter filter[] = {
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, arch)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_X86_64, 1, 0),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SYS_exit, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SYS_exit_group, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SYS_write, 1, 0),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, args[0]) + 4),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, 0, 1, 0),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, args[0])),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, 3, 1, 0),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, args[2]) + 4),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, 0, 1, 0),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, args[2])),
        BPF_JUMP(BPF_JMP | BPF_JGT | BPF_K, wireBytes, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW)
    };
    const struct sock_fprog program = {static_cast<unsigned short>(sizeof(filter) / sizeof(filter[0])),
        const_cast<struct sock_filter*>(filter)};
    return ::prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &program, 0, 0) == 0;
}
}
#endif
