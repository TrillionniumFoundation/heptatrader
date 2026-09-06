        !SameFileObservation(before, after) || digest.FinalHex() != segment.digest)
        valid = false;
    if (!valid) IncrementSaturating(m_segmentIntegrityRejects);
    return valid;
}

namespace
{
int OpenPrivateDirectoryNoSymlinks(const std::string& directory,
                                   struct stat& metadata)
{
#if !defined(O_DIRECTORY) || !defined(O_NOFOLLOW)
    (void)directory;
    (void)metadata;
    errno = ENOTSUP;
    return -1;
#else
    if (directory.empty() || directory.size() >= PATH_MAX ||
        directory.find('\0') != std::string::npos)
    {
        errno = EINVAL;
        return -1;
    }

    int flags = O_RDONLY | O_DIRECTORY | O_NOFOLLOW;
#ifdef O_CLOEXEC
    flags |= O_CLOEXEC;
#endif
    int current;
    do
    {
        current = ::open(directory.front() == '/' ? "/" : ".", flags);
    } while (current < 0 && errno == EINTR);
    if (current < 0) return -1;
#ifndef O_CLOEXEC
    if (::fcntl(current, F_SETFD, FD_CLOEXEC) != 0)
    {
        const int saved = errno;
        ::close(current);
        errno = saved;
        return -1;
    }
#endif

    std::size_t cursor = 0;
    while (cursor < directory.size())
    {
        while (cursor < directory.size() && directory[cursor] == '/') ++cursor;
        if (cursor == directory.size()) break;
        const std::size_t separator = directory.find('/', cursor);
        const std::size_t length = separator == std::string::npos ?
            directory.size() - cursor : separator - cursor;
        const std::string component = directory.substr(cursor, length);
        cursor = separator == std::string::npos ? directory.size() : separator + 1;
        if (component == ".") continue;
        if (component == "..")
        {
            ::close(current);
            errno = EINVAL;
            return -1;
        }

        int next;
        do
        {
            next = ::openat(current, component.c_str(), flags);
        } while (next < 0 && errno == EINTR);
        if (next < 0)
        {
            const int saved = errno;
            ::close(current);
            errno = saved;
            return -1;
        }
#ifndef O_CLOEXEC
        if (::fcntl(next, F_SETFD, FD_CLOEXEC) != 0)
        {
            const int saved = errno;
            ::close(next);
            ::close(current);
            errno = saved;
            return -1;
        }
#endif
        ::close(current);
        current = next;
    }

    if (!StatFileDescriptor(current, metadata) ||
        !PrivateDirectoryMetadata(metadata))
    {
        const int saved = errno == 0 ? EPERM : errno;
        ::close(current);
        errno = saved;
        return -1;
    }
    return current;
#endif
}
}

bool OmsSegmentedJournal::ScanSegmentsLocked()
{
    m_segments.clear();
    m_sealedBytes = 0;
    m_sealedRecords = 0;
    m_nextSequence = 1;

    const int duplicate = ::fcntl(m_directoryFd, F_DUPFD_CLOEXEC, 0);
    if (duplicate < 0) return false;
    DIR* directory = ::fdopendir(duplicate);
    if (directory == nullptr)
    {
        ::close(duplicate);
        return false;
    }
    std::map<std::uint64_t, OmsJournalSegmentDescriptor> ordered;
    for (;;)
    {
        errno = 0;
        struct dirent* entry = ::readdir(directory);
        if (entry == nullptr) break;
        const std::string filename(entry->d_name);
        if (filename == "." || filename == ".." ||
            filename == m_activeName || filename == m_lockName) continue;
        if (filename.compare(0, m_segmentPrefix.size(), m_segmentPrefix) == 0)
        {
            OmsJournalSegmentDescriptor descriptor;
            if (!ParseSegmentFilename(m_segmentPrefix, filename, descriptor) ||
                ordered.find(descriptor.sequence) != ordered.end())
            {
                ::closedir(directory);
                IncrementSaturating(m_segmentIntegrityRejects);
                return false;
            }
            struct stat metadata;
            if (::fstatat(m_directoryFd, filename.c_str(), &metadata,
                          AT_SYMLINK_NOFOLLOW) != 0 ||
                !PrivateSegmentMetadata(metadata) || metadata.st_size < 0)
            {
                ::closedir(directory);
                IncrementSaturating(m_segmentIntegrityRejects);
                return false;
            }
            descriptor.bytes = static_cast<std::size_t>(metadata.st_size);
            std::size_t observedRecords = 0;
            if (descriptor.bytes > m_limits.maximumActiveBytes ||
                !ReadSegmentLocked(descriptor, nullptr, observedRecords) ||
                observedRecords != descriptor.records)
            {
                ::closedir(directory);
                return false;
            }
            ordered.emplace(descriptor.sequence, descriptor);
            continue;
        }
        if (filename.compare(0, m_baseName.size() + 1,
                             m_baseName + '.') == 0)
        {
            ::closedir(directory);
            IncrementSaturating(m_segmentIntegrityRejects);
            return false;
        }
    }
    const int readError = errno;
    ::closedir(directory);
    if (readError != 0) return false;

    std::uint64_t expectedSequence = 1;
    for (const auto& item : ordered)
    {
        const OmsJournalSegmentDescriptor& descriptor = item.second;
        if (descriptor.sequence != expectedSequence ||
            m_segments.size() >= m_limits.maximumSealedSegments ||
            descriptor.bytes > m_limits.maximumTotalBytes - m_sealedBytes ||
            descriptor.records > m_limits.maximumTotalRecords - m_sealedRecords)
        {
            IncrementSaturating(m_segmentIntegrityRejects);
            return false;
        }
        m_segments.push_back(descriptor);
        m_sealedBytes += descriptor.bytes;
        m_sealedRecords += descriptor.records;
        if (expectedSequence == std::numeric_limits<std::uint64_t>::max())
        {
            IncrementSaturating(m_segmentIntegrityRejects);
            return false;
        }
        ++expectedSequence;
    }
    m_nextSequence = expectedSequence;
    return true;
}

bool OmsSegmentedJournal::OpenActiveLocked()
{
    if (!ValidateDirectoryLocked()) return false;
    OmsJournalLimits limits;
    limits.maximumQueuedRecords = m_limits.maximumQueuedRecords;
    limits.maximumQueuedBytes = m_limits.maximumQueuedBytes;
    limits.maximumReplayRecords = m_limits.maximumTotalRecords;
    limits.maximumReplayBytes = m_limits.maximumActiveBytes;
    std::unique_ptr<OmsJournal> active;
    try
    {
        active.reset(new OmsJournal(limits));
    }
    catch (...)
    {
        return false;
    }
    if (!active->Init(DescriptorPathLocked(m_activeName))) return false;
    const int activeRecords = active->Replay({});
    if (activeRecords < 0)
    {
        active.reset();
        IncrementSaturating(m_segmentIntegrityRejects);
        return false;
    }
    std::size_t activeOnDisk = 0, activeRetained = 0;
    if (!ObserveJournal(*active, activeOnDisk, activeRetained) ||
        activeRetained != 0 ||
        activeOnDisk > m_limits.maximumActiveBytes ||
        activeOnDisk > m_limits.maximumTotalBytes - m_sealedBytes ||
        static_cast<std::size_t>(activeRecords) >
            m_limits.maximumTotalRecords - m_sealedRecords)
    {
        active.reset();
        IncrementSaturating(m_segmentIntegrityRejects);
        return false;
    }
    m_activeRecords = static_cast<std::size_t>(activeRecords);
    m_activeLogicalBytes = activeOnDisk;
    m_active = std::move(active);
    return true;
}

bool OmsSegmentedJournal::Init(const std::string& directory,
                               const std::string& baseName)
{
    std::lock_guard<std::mutex> lock(m_segmentMutex);
    if (m_initialized || m_directoryFd >= 0 || m_lockFd >= 0 ||
        !ValidSegmentedLimits(m_limits) || !CanonicalSegmentBaseName(baseName))
        return false;

    struct stat descriptorMetadata;
    m_directoryFd = OpenPrivateDirectoryNoSymlinks(directory, descriptorMetadata);
    if (m_directoryFd < 0) return false;
    // The retained descriptor is authoritative. This procfs name continues
    // to address the opened directory even if its pathname is renamed.
    m_directory = std::string("/proc/self/fd/") +
        std::to_string(m_directoryFd);
    m_baseName = baseName;
    m_activeName = baseName + ".active.jsonl";
    m_lockName = baseName + ".writer.lock";
    m_segmentPrefix = baseName + ".segment.";
    m_directoryDevice = static_cast<std::uintmax_t>(descriptorMetadata.st_dev);
    m_directoryInode = static_cast<std::uintmax_t>(descriptorMetadata.st_ino);

    int lockFlags = O_RDWR;
#ifdef O_CLOEXEC
    lockFlags |= O_CLOEXEC;
#endif
#ifdef O_NOFOLLOW
    lockFlags |= O_NOFOLLOW;
#endif
    bool created = false;
    m_lockFd = ::openat(m_directoryFd, m_lockName.c_str(),
                        lockFlags | O_CREAT | O_EXCL, 0600);
    if (m_lockFd >= 0) created = true;
    else if (errno == EEXIST)
        m_lockFd = ::openat(m_directoryFd, m_lockName.c_str(), lockFlags);
    if (created && m_lockFd >= 0 && ::fchmod(m_lockFd, 0600) != 0)
    {
        ::close(m_lockFd);
        m_lockFd = -1;
    }
    struct stat lockMetadata;
    if (m_lockFd < 0 || !StatFileDescriptor(m_lockFd, lockMetadata) ||
        !HasPrivateRegularFileMetadata(lockMetadata) ||
        ::flock(m_lockFd, LOCK_EX | LOCK_NB) != 0 ||
        (created && (!SyncFileData(m_lockFd) || !SyncDirectory(m_directoryFd))))
    {
        if (m_lockFd >= 0) ::close(m_lockFd);
        ::close(m_directoryFd);
        m_lockFd = -1;
        m_directoryFd = -1;
        return false;
    }

    bool ready = false;
    try
    {
        m_segments.reserve(m_limits.maximumSealedSegments);
