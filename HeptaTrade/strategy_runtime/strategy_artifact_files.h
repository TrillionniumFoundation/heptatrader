#pragma once

// Private read-only Linux loader. Never mmap/dlopen/exec candidate bytes.
#include <cerrno>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>
#if defined(__linux__)
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace hepta_artifact_detail
{
class Fd
{
public:
    explicit Fd(int value = -1) noexcept : value_(value) {}
    ~Fd() { if (value_ >= 0) ::close(value_); }
    Fd(Fd&& other) noexcept : value_(other.value_) { other.value_ = -1; }
    Fd& operator=(Fd&& other) noexcept
    {
        if (this != &other) { if (value_ >= 0) ::close(value_); value_ = other.value_; other.value_ = -1; }
        return *this;
    }
    Fd(const Fd&) = delete; Fd& operator=(const Fd&) = delete;
    int Get() const noexcept { return value_; }
    bool Close() noexcept { const int old = value_; value_ = -1; return old < 0 || ::close(old) == 0; }
private:
    int value_;
};
inline bool Inode(const struct stat& a, const struct stat& b)
{ return a.st_dev == b.st_dev && a.st_ino == b.st_ino; }
inline bool Version(const struct stat& a, const struct stat& b)
{
    return Inode(a, b) && a.st_size == b.st_size && a.st_uid == b.st_uid &&
        a.st_mode == b.st_mode && a.st_nlink == b.st_nlink &&
        a.st_mtim.tv_sec == b.st_mtim.tv_sec && a.st_mtim.tv_nsec == b.st_mtim.tv_nsec &&
        a.st_ctim.tv_sec == b.st_ctim.tv_sec && a.st_ctim.tv_nsec == b.st_ctim.tv_nsec;
}
inline bool Private(const struct stat& s)
{ return S_ISREG(s.st_mode) && s.st_uid == ::geteuid() && (s.st_mode & 07777) == 0600 && s.st_nlink == 1; }
inline bool Leaf(const std::string& name)
{
    if (name.empty() || name.size() > 96 || name == "." || name == "..") return false;
    for (unsigned char c : name)
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-')) return false;
    return true;
}

class Files
{
    struct Directory { std::string name; Fd fd; };
    struct Record { std::string name; Fd fd; struct stat version{}; };
    std::vector<Directory> dirs_;
    std::vector<Record> records_;
    int Parent() const noexcept { return dirs_.back().fd.Get(); }
public:
    const char* Open(const std::string& path)
    {
        if (path.size() < 2 || path[0] != '/' || path.size() > 4096 || path.back() == '/' ||
            path.find('\0') != std::string::npos) return "STRATEGY_ARTIFACT_PATH_INVALID";
        Directory root; root.fd = Fd(::open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC));
        if (root.fd.Get() < 0) return "STRATEGY_ARTIFACT_DIRECTORY_OPEN_FAILED";
        dirs_.push_back(std::move(root));
        std::size_t start = 1;
        while (start < path.size())
        {
            const auto end = path.find('/', start);
            Directory d; d.name = path.substr(start, end - start);
            if (d.name.empty() || d.name == "." || d.name == ".." || dirs_.size() >= 65)
                return "STRATEGY_ARTIFACT_PATH_INVALID";
            d.fd = Fd(::openat(Parent(), d.name.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
            if (d.fd.Get() < 0) return "STRATEGY_ARTIFACT_DIRECTORY_OPEN_FAILED";
            dirs_.push_back(std::move(d));
            if (end == std::string::npos) break;
            start = end + 1;
        }
        return Bound() ? nullptr : "STRATEGY_ARTIFACT_DIRECTORY_INVALID";
    }
    bool Bound() const
    {
        struct stat a{}, b{};
        for (std::size_t i = 1; i < dirs_.size(); ++i)
            if (::fstat(dirs_[i].fd.Get(), &a) != 0 ||
                ::fstatat(dirs_[i - 1].fd.Get(), dirs_[i].name.c_str(), &b, AT_SYMLINK_NOFOLLOW) != 0 ||
                !S_ISDIR(b.st_mode) || !Inode(a, b)) return false;
        return !dirs_.empty() && ::fstat(Parent(), &a) == 0 &&
            a.st_uid == ::geteuid() && (a.st_mode & 07777) == 0700;
    }
    const char* Read(const std::string& name, std::size_t limit, std::string& bytes)
    {
        if (!Leaf(name) || records_.size() >= 3 || limit == 0) return "STRATEGY_ARTIFACT_PATH_INVALID";
        Record r; r.name = name;
        r.fd = Fd(::openat(Parent(), name.c_str(), O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK));
        if (r.fd.Get() < 0) return "STRATEGY_ARTIFACT_FILE_OPEN_FAILED";
        if (::fstat(r.fd.Get(), &r.version) != 0 || !Private(r.version) ||
            r.version.st_size <= 0 || static_cast<std::uintmax_t>(r.version.st_size) > limit)
            return "STRATEGY_ARTIFACT_FILE_INVALID";
        bytes.resize(static_cast<std::size_t>(r.version.st_size));
        std::size_t offset = 0;
        while (offset < bytes.size())
        {
            const auto n = ::pread(r.fd.Get(), &bytes[offset], bytes.size() - offset, static_cast<off_t>(offset));
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0) return "STRATEGY_ARTIFACT_READ_FAILED";
            offset += static_cast<std::size_t>(n);
        }
        char extra; ssize_t n;
        do { n = ::pread(r.fd.Get(), &extra, 1, r.version.st_size); } while (n < 0 && errno == EINTR);
        if (n != 0) return "STRATEGY_ARTIFACT_FILE_CHANGED";
        records_.push_back(std::move(r));
        return Validate() ? nullptr : "STRATEGY_ARTIFACT_FILE_CHANGED";
    }
    bool Validate() const
    {
        if (!Bound()) return false;
        for (const auto& r : records_)
        {
            struct stat a{}, named{};
            if (::fstat(r.fd.Get(), &a) != 0 || !Private(a) || !Version(r.version, a) ||
                ::fstatat(Parent(), r.name.c_str(), &named, AT_SYMLINK_NOFOLLOW) != 0 ||
                !Version(a, named)) return false;
        }
        return true;
    }
    bool Close() noexcept
    {
        bool success = true;
        for (auto& r : records_) if (!r.fd.Close()) success = false;
        for (auto& d : dirs_) if (!d.fd.Close()) success = false;
        return success;
    }
};
}
#endif
