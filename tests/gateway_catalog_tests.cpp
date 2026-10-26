#include "../HeptaTrade/tool_host/session_supervisor_lease_store.h"
#include <cassert>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

namespace {
std::string Read(const std::string& path)
{
    std::ifstream input(path, std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(input), {});
}
void Put(const std::string& path, const std::string& value, mode_t mode = 0600)
{
    const int fd = ::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL, mode);
    assert(fd >= 0 && ::write(fd, value.data(), value.size()) == static_cast<ssize_t>(value.size()));
    assert(::close(fd) == 0);
}
int Run(const std::string& store, const std::string& key, const std::string& lock, const std::string& output)
{
    const pid_t pid = ::fork(); assert(pid >= 0);
    if (pid == 0) {
        const int fd = ::open(output.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0600);
        if (fd < 0 || ::dup2(fd, STDOUT_FILENO) < 0) _exit(98);
        ::close(fd);
        ::execl(HEPTA_CATALOG_BINARY, HEPTA_CATALOG_BINARY, "--store", store.c_str(),
            "--key", key.c_str(), "--cleanup-lock", lock.c_str(), static_cast<char*>(nullptr));
        _exit(99);
    }
    int status = 0; assert(::waitpid(pid, &status, 0) == pid && WIFEXITED(status));
    return WEXITSTATUS(status);
}
}
int main()
{
    if (::geteuid() == 0 || ::getegid() == 0) return 0;
    char pattern[] = "/tmp/hepta-catalog-XXXXXX";
    char* directory = ::mkdtemp(pattern); assert(directory);
    const std::string root(directory), storePath = root + "/leases", keyPath = root + "/key";
    const std::string badKey = root + "/bad-key", output = root + "/output";
    const std::string locks = root + "/interlock", lock = locks + "/cleanup.lock";
    assert(::mkdir(locks.c_str(), 0711) == 0 && ::chmod(locks.c_str(), 0711) == 0);
    Put(lock, "", 0644); assert(::chmod(lock.c_str(), 0644) == 0);
    Put(keyPath, std::string(32, 'k')); Put(badKey, std::string(32, 'x'));
    std::string reason;
    {
        SessionSupervisorLeaseStore store;
        assert(store.Init(storePath, keyPath, lock, ::geteuid(), ::getegid(), reason));
        SessionSupervisorLeaseRecord first;
        first.templateId = "paper"; first.issuer = "hepta.os.bootstrap";
        first.token = "private-original-token-one"; first.agentId = "original-agent";
        first.sessionId = "original-session"; first.ownerAccount = "PRIVATE_ACCOUNT";
        first.ownerExecutionDomain = "PAPER"; first.peerUid = ::geteuid();
        first.leaseGeneration = 112; first.expiresAtMs = 9900000000000;
        assert(store.Put(first, reason));
        auto second = first; second.token = "private-original-token-two";
        second.sessionId = "second-session"; second.leaseGeneration = 4;
        second.recoveryOnly = true; second.recoveryCommandId = "original-close-command";
        assert(store.Put(second, reason));
    }
    const auto before = Read(storePath); struct stat initial, after;
    assert(::stat(storePath.c_str(), &initial) == 0);
    assert(Run(storePath, keyPath, lock, output) == 0);
    const auto projection = Read(output);
    assert(projection.find("heptatrader.gateway-catalog.v1") != std::string::npos);
    assert(projection.find("\"generation\":112") != std::string::npos);
    assert(projection.find("\"recovery_only\":true") != std::string::npos);
    for (const char* secret : {"private-original-token", "PRIVATE_ACCOUNT", "original-agent", "original-close-command"})
        assert(projection.find(secret) == std::string::npos);
    assert(Run(storePath, badKey, lock, output) != 0);
    assert(Run(root + "/missing", keyPath, lock, output) != 0);
    assert(::access((root + "/missing").c_str(), F_OK) != 0);
    assert(Read(storePath) == before && ::stat(storePath.c_str(), &after) == 0);
    assert(initial.st_ino == after.st_ino && initial.st_size == after.st_size && initial.st_mtime == after.st_mtime);
    for (const auto& path : {storePath, keyPath, badKey, output, lock}) ::unlink(path.c_str());
    ::rmdir(locks.c_str()); ::rmdir(root.c_str());
    std::cout << "gateway_catalog_tests: PASS\n";
}
