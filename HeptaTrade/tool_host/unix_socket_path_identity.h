#pragma once

#include <cstdint>
#include <cstring>
#include <cerrno>
#include <string>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

// A Unix socket pathname is a replaceable directory entry. Keep its dentry
// identity separate from the socket object's fstat identity and require both
// an owned listener witness and an unchanged pathname before cleanup.
class UnixSocketPathIdentity
{
public:
    bool Capture(const std::string& path)
    {
        struct stat metadata;
        if (::lstat(path.c_str(), &metadata) != 0 ||
            !S_ISSOCK(metadata.st_mode)) return false;
        m_device = static_cast<std::uint64_t>(metadata.st_dev);
        m_inode = static_cast<std::uint64_t>(metadata.st_ino);
        m_valid = true;
        return true;
    }

    bool ListenerWitness(int fd) const
    {
        struct stat metadata;
        return m_listenerValid && fd >= 0 && ::fstat(fd, &metadata) == 0 &&
            S_ISSOCK(metadata.st_mode) &&
            static_cast<std::uint64_t>(metadata.st_dev) == m_listenerDevice &&
            static_cast<std::uint64_t>(metadata.st_ino) == m_listenerInode;
    }

    bool Prepare(int fd, const std::string& path, int backlog,
                 std::string& reason)
    {
        if (!Capture(path))
        {
            reason = "socket path identity unavailable";
            // The caller has just bound this descriptor, so a failed first
            // lstat must not strand the pathname.  Only remove it when the
            // pathname still names the exact socket object represented by
            // `fd`; a concurrent replacement is left untouched.
            struct stat listener;
            struct stat pathname;
            if (::fstat(fd, &listener) == 0 &&
                ::lstat(path.c_str(), &pathname) == 0 &&
                S_ISSOCK(listener.st_mode) && S_ISSOCK(pathname.st_mode) &&
                static_cast<std::uint64_t>(listener.st_dev) ==
                    static_cast<std::uint64_t>(pathname.st_dev) &&
                static_cast<std::uint64_t>(listener.st_ino) ==
                    static_cast<std::uint64_t>(pathname.st_ino))
                ::unlink(path.c_str());
            ::close(fd);
            return false;
        }
        struct stat listener;
        if (::fstat(fd, &listener) != 0 || !S_ISSOCK(listener.st_mode))
        {
            reason = "socket listener identity unavailable";
            ::close(fd);
            UnlinkIfUnchanged(path);
            return false;
        }
        m_listenerDevice = static_cast<std::uint64_t>(listener.st_dev);
        m_listenerInode = static_cast<std::uint64_t>(listener.st_ino);
        m_listenerValid = true;
        if (::fchmod(fd, 0600) != 0 || ::chmod(path.c_str(), 0600) != 0)
        {
            reason = std::strerror(errno);
            ::close(fd);
            UnlinkIfUnchanged(path);
            return false;
        }
        if (!Unchanged(path))
        {
            reason = "socket path identity changed during preparation";
            ::close(fd);
            UnlinkIfUnchanged(path);
            return false;
        }
        if (::listen(fd, backlog) != 0)
        {
            reason = std::strerror(errno);
            ::close(fd);
            UnlinkIfUnchanged(path);
            return false;
        }
        return true;
    }

    bool Unchanged(const std::string& path) const
    {
        struct stat metadata;
        return m_valid && ::lstat(path.c_str(), &metadata) == 0 &&
            S_ISSOCK(metadata.st_mode) &&
            static_cast<std::uint64_t>(metadata.st_dev) == m_device &&
            static_cast<std::uint64_t>(metadata.st_ino) == m_inode;
    }

    bool UnlinkIfUnchanged(const std::string& path) const
    {
        struct stat metadata;
        if (!m_valid || ::lstat(path.c_str(), &metadata) != 0 ||
            !S_ISSOCK(metadata.st_mode) ||
            static_cast<std::uint64_t>(metadata.st_dev) != m_device ||
            static_cast<std::uint64_t>(metadata.st_ino) != m_inode)
            return false;
        return ::unlink(path.c_str()) == 0;
    }

    bool Valid() const { return m_valid; }
    void Reset()
    {
        m_device = m_inode = m_listenerDevice = m_listenerInode = 0;
        m_valid = m_listenerValid = false;
    }

private:
    std::uint64_t m_device = 0;
    std::uint64_t m_inode = 0;
    std::uint64_t m_listenerDevice = 0;
    std::uint64_t m_listenerInode = 0;
    bool m_valid = false;
    bool m_listenerValid = false;
};
