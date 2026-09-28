#include "../execution/ib_paper_execution_profile.h"
#include <sstream>
#include <fcntl.h>
#include <cerrno>
#include <iostream>
#include <map>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

// Operator configuration utility. It emits only the existing bounded profile
// credential; it cannot connect to a Broker or claim external qualification.
int main(int argc, char** argv)
{
    if (argc != 3 || std::string(argv[1]) != "--env")
    { std::cerr << "usage: hepta-ib-profile --env <private-config>\n"; return 64; }
    const int fd = ::open(argv[2], O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    if (fd < 0) return 66;
    struct stat st;
    if (::fstat(fd, &st) != 0 || !S_ISREG(st.st_mode) || st.st_nlink != 1 ||
        st.st_uid != ::geteuid() || (st.st_mode & 0022) != 0 ||
        st.st_size <= 0 || st.st_size > 65536)
    { ::close(fd); std::cerr << "unsafe operator profile\n"; return 65; }
    std::string bytes(static_cast<std::size_t>(st.st_size), '\0');
    std::size_t offset = 0;
    while (offset < bytes.size())
    {
        const ssize_t n = ::read(fd, &bytes[offset], bytes.size() - offset);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) { ::close(fd); return 74; }
        offset += static_cast<std::size_t>(n);
    }
    char extra;
    struct stat after, named;
    const bool stable = ::read(fd, &extra, 1) == 0 && ::fstat(fd, &after) == 0 &&
        ::lstat(argv[2], &named) == 0 && st.st_dev == named.st_dev &&
        st.st_ino == named.st_ino && st.st_size == after.st_size &&
        st.st_mtim.tv_sec == after.st_mtim.tv_sec && st.st_mtim.tv_nsec == after.st_mtim.tv_nsec &&
        st.st_ctim.tv_sec == after.st_ctim.tv_sec && st.st_ctim.tv_nsec == after.st_ctim.tv_nsec;
    ::close(fd);
    if (!stable) { std::cerr << "operator profile changed\n"; return 74; }
    std::istringstream input(bytes);
    std::map<std::string, std::string> values;
    std::string line;
    while (std::getline(input, line))
    {
        if (line.empty() || line[0] == '#') continue;
        const std::size_t split = line.find('=');
        if (split == std::string::npos || split == 0 ||
            !values.insert(std::make_pair(line.substr(0, split), line.substr(split + 1))).second)
        { std::cerr << "invalid or duplicate profile field\n"; return 65; }
    }
    if (!input.eof()) return 74;
    IbPaperExecutionProfileConfig profile;
    std::string reason, credential;
    if (!IbPaperExecutionProfileConfig::FromValues(values, profile, reason) ||
        !profile.userScope || !profile.BuildAuthorizationCredential(credential, reason))
    { std::cerr << "user PAPER profile rejected: " << reason << '\n'; return 1; }
    std::cout << credential << '\n';
    return std::cout ? 0 : 74;
}
