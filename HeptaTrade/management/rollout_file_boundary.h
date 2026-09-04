#pragma once

// Private Linux local-filesystem transaction. The parent directory belongs to
// the trusted management UID. flock coordinates cooperating writers only; it
// is not distributed consensus or protection from malicious same-UID code.
#include <atomic>
#include <cerrno>
#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>
#if defined(__linux__)
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace hepta_rollout_detail {
class FileTransaction {
public:
    FileTransaction(const std::filesystem::path& path, std::size_t maximumBytes)
        : path_(path), maximumBytes_(maximumBytes) {}
    FileTransaction(const FileTransaction&) = delete;
    FileTransaction& operator=(const FileTransaction&) = delete;
    ~FileTransaction() noexcept
    {
#if defined(__linux__)
        if (lock_ >= 0) ::close(lock_);
        if (directory_ >= 0) ::close(directory_);
#endif
    }

    const char* Open()
    {
#if defined(__linux__)
        leaf_ = path_.filename().string();
        if (leaf_.empty() || leaf_ == "." || leaf_ == ".." || leaf_.size() > 128 ||
            path_.string().find('\0') != std::string::npos)
            return "ROLLOUT_STORE_PATH_INVALID";
        directory_ = OpenParent(true);
        if (directory_ < 0) return "ROLLOUT_STORE_DIRECTORY_INVALID";
        lockName_ = leaf_ + ".lock";
        lock_ = ::openat(directory_, lockName_.c_str(),
            O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK, 0600);
        if (lock_ < 0 || !BoundFile(lock_, lockName_))
            return "ROLLOUT_STORE_LOCK_INVALID";
        if (::flock(lock_, LOCK_EX | LOCK_NB) != 0)
            return "ROLLOUT_STORE_BUSY";
        return Binding() ? "" : "ROLLOUT_STORE_PATH_CHANGED";
#else
        return "ROLLOUT_STORE_PLATFORM_UNSUPPORTED";
#endif
    }

    const char* Read(std::string& document, bool& present)
    {
        document.clear(); present = false;
#if defined(__linux__)
        if (!Binding()) return "ROLLOUT_STORE_PATH_CHANGED";
        Fd file(::openat(directory_, leaf_.c_str(),
                        O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK));
        if (file.value < 0)
        {
            if (errno == ENOENT && Binding()) return "";
            return "ROLLOUT_STORE_OPEN_FAILED";
        }
        struct stat before{}, after{};
        if (!BoundFile(file.value, leaf_) || ::fstat(file.value, &before) != 0)
            return "ROLLOUT_STORE_FILE_INVALID";
        if (before.st_size <= 0 || static_cast<std::uintmax_t>(before.st_size) > maximumBytes_)
            return "ROLLOUT_STORE_SIZE_INVALID";
        document.resize(static_cast<std::size_t>(before.st_size));
        std::size_t offset = 0;
        while (offset < document.size())
        {
            const ssize_t count = ::pread(file.value, &document[offset],
                                         document.size() - offset, static_cast<off_t>(offset));
            if (count < 0 && errno == EINTR) continue;
            if (count <= 0) return "ROLLOUT_STORE_READ_FAILED";
            offset += static_cast<std::size_t>(count);
        }
        char extra;
        ssize_t tail;
        do { tail = ::pread(file.value, &extra, 1, before.st_size); }
        while (tail < 0 && errno == EINTR);
        if (tail != 0 || ::fstat(file.value, &after) != 0 || !Same(before, after) ||
            !BoundFile(file.value, leaf_) || !Binding())
            return "ROLLOUT_STORE_CHANGED_DURING_READ";
        present = true;
        return "";
#else
        return "ROLLOUT_STORE_PLATFORM_UNSUPPORTED";
#endif
    }

    const char* Write(const std::string& document)
    {
#if defined(__linux__)
        if (document.empty() || document.size() > maximumBytes_)
            return "ROLLOUT_STORE_SIZE_LIMIT";
        if (!Binding()) return "ROLLOUT_STORE_PATH_CHANGED";
        Fd file;
        std::string name;
        for (unsigned int attempt = 0; attempt < 32; ++attempt)
        {
            const std::uint64_t nonce = next_.fetch_add(1);
            if (nonce == 0) return "ROLLOUT_STORE_TEMP_ID_EXHAUSTED";
            name = leaf_ + ".tmp." + std::to_string(::getpid()) + "." + std::to_string(nonce);
            file.value = ::openat(directory_, name.c_str(),
                O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
            if (file.value >= 0) break;
            if (errno != EEXIST) return "ROLLOUT_STORE_TEMP_OPEN_FAILED";
        }
        if (file.value < 0) return "ROLLOUT_STORE_TEMP_COLLISION";
        Temporary cleanup(directory_, name, file.value);
        if (!BoundFile(file.value, name)) return "ROLLOUT_STORE_TEMP_INVALID";
        std::size_t offset = 0;
        while (offset < document.size())
        {
            const ssize_t count = ::write(file.value, document.data() + offset,
                                          document.size() - offset);
            if (count < 0 && errno == EINTR) continue;
            if (count <= 0) return "ROLLOUT_STORE_TEMP_WRITE_FAILED";
            offset += static_cast<std::size_t>(count);
        }
        if (!Sync(file.value)) return "ROLLOUT_STORE_TEMP_SYNC_FAILED";
        if (!BoundFile(file.value, name) || !Binding())
            return "ROLLOUT_STORE_PATH_CHANGED";
        if (::renameat(directory_, name.c_str(), directory_, leaf_.c_str()) != 0)
            return "ROLLOUT_STORE_RENAME_FAILED";
        cleanup.renamed = true;
        if (!Sync(directory_)) return "ROLLOUT_STORE_DIRECTORY_SYNC_FAILED";
        if (!BoundFile(file.value, leaf_) || !Binding())
            return "ROLLOUT_STORE_PATH_CHANGED";
        // The record may already be durable if close reports an error. The
        // caller must close admission and reload, never assume no mutation.
        const int raw = file.value; file.value = -1;
        if (::close(raw) != 0) return "ROLLOUT_STORE_CLOSE_FAILED";
        return "";
#else
        (void)document;
        return "ROLLOUT_STORE_PLATFORM_UNSUPPORTED";
#endif
    }

private:
#if defined(__linux__)
    struct Fd {
        int value;
        explicit Fd(int fd = -1) : value(fd) {}
        ~Fd() { if (value >= 0) ::close(value); }
        Fd(const Fd&) = delete;
        Fd& operator=(const Fd&) = delete;
    };
    struct Temporary {
        int directory; const std::string& name; int descriptor; bool renamed = false;
        Temporary(int d, const std::string& n, int f) : directory(d), name(n), descriptor(f) {}
        ~Temporary()
        {
            if (renamed) return;
            struct stat fd{}, named{};
            if (::fstat(descriptor, &fd) == 0 &&
                ::fstatat(directory, name.c_str(), &named, AT_SYMLINK_NOFOLLOW) == 0 &&
                fd.st_dev == named.st_dev && fd.st_ino == named.st_ino)
                ::unlinkat(directory, name.c_str(), 0);
        }
    };
    static bool Sync(int fd)
    {
        int result; do { result = ::fsync(fd); } while (result != 0 && errno == EINTR);
        return result == 0;
    }
    static bool Private(const struct stat& s)
    {
        return S_ISREG(s.st_mode) && s.st_uid == ::geteuid() &&
               (s.st_mode & 07777) == 0600 && s.st_nlink == 1;
    }
    static bool Same(const struct stat& a, const struct stat& b)
    {
        return a.st_dev == b.st_dev && a.st_ino == b.st_ino && a.st_size == b.st_size &&
               a.st_mtim.tv_sec == b.st_mtim.tv_sec && a.st_mtim.tv_nsec == b.st_mtim.tv_nsec &&
               a.st_ctim.tv_sec == b.st_ctim.tv_sec && a.st_ctim.tv_nsec == b.st_ctim.tv_nsec;
    }
    bool BoundFile(int fd, const std::string& name) const
    {
        struct stat pinned{}, named{};
        return fd >= 0 && ::fstat(fd, &pinned) == 0 &&
            ::fstatat(directory_, name.c_str(), &named, AT_SYMLINK_NOFOLLOW) == 0 &&
            Private(pinned) && Private(named) && Same(pinned, named);
    }
    int OpenParent(bool create) const
    {
        Fd current(::open(path_.is_absolute() ? "/" : ".",
                          O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW));
        if (current.value < 0) return -1;
        for (const auto& component : path_.parent_path().relative_path())
        {
            const std::string part = component.string();
            if (part.empty() || part == ".") continue;
            if (part == ".." || part.find('\0') != std::string::npos) return -1;
            int next = ::openat(current.value, part.c_str(),
                               O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
            if (next < 0 && errno == ENOENT && create)
            {
                if (::mkdirat(current.value, part.c_str(), 0700) != 0 && errno != EEXIST)
                    return -1;
                next = ::openat(current.value, part.c_str(),
                                O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
                if (next >= 0 && (!Sync(next) || !Sync(current.value)))
                { ::close(next); return -1; }
            }
            if (next < 0) return -1;
            ::close(current.value); current.value = next;
        }
        struct stat directory{};
        if (::fstat(current.value, &directory) != 0 || directory.st_uid != ::geteuid() ||
            (directory.st_mode & 0022) != 0) return -1;
        const int result = current.value; current.value = -1; return result;
    }
    bool Binding() const
    {
        Fd current(OpenParent(false));
        struct stat a{}, b{};
        return directory_ >= 0 && current.value >= 0 &&
            ::fstat(directory_, &a) == 0 && ::fstat(current.value, &b) == 0 &&
            a.st_dev == b.st_dev && a.st_ino == b.st_ino && BoundFile(lock_, lockName_);
    }
    inline static std::atomic<std::uint64_t> next_{1};
    int directory_ = -1;
    int lock_ = -1;
    std::string leaf_, lockName_;
#endif
    std::filesystem::path path_;
    std::size_t maximumBytes_;
};
} // namespace hepta_rollout_detail
