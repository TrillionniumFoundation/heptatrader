#include "ib_paper_kill_switch.h"

#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <pwd.h>
#include <sstream>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#if defined(__linux__)
#include <linux/openat2.h>
#include <sys/syscall.h>
#endif

namespace
{
const mode_t kDirectoryMode = 0750;
const mode_t kMarkerMode = 0440;
const mode_t kMaintenanceDirectoryMode = 0700;
const mode_t kMaintenanceHoldMode = 0600;
const char* const kMaintenanceHoldName = "maintenance-hold.json";

bool SafeAbsoluteDirectoryPath(const std::string& path)
{
    if (path.size() < 2 || path[0] != '/' || path[path.size() - 1] == '/' ||
        path.find(':') != std::string::npos)
        return false;
    std::size_t offset = 1;
    while (offset <= path.size())
    {
        const std::size_t slash = path.find('/', offset);
        const std::string component = path.substr(
            offset, slash == std::string::npos ? slash : slash - offset);
        if (component.empty() || component == "." || component == "..")
            return false;
        if (slash == std::string::npos) break;
        offset = slash + 1;
    }
    return true;
}

std::string ErrnoDetail(const char* operation)
{
    const int saved = errno;
    std::ostringstream detail;
    detail << operation << ": errno=" << saved << " (" << std::strerror(saved) << ")";
    return detail.str();
}

bool SameIdentity(const struct stat& left, const struct stat& right)
{
    return left.st_dev == right.st_dev && left.st_ino == right.st_ino;
}

bool ValidateDirectoryMetadata(const struct stat& metadata,
                               std::uint32_t expectedOwnerUid,
                               std::uint32_t expectedGroupGid,
                               std::string& detail)
{
    if (!S_ISDIR(metadata.st_mode))
        detail = "control path is not a directory";
    else if (metadata.st_uid != static_cast<uid_t>(expectedOwnerUid))
        detail = "control directory owner mismatch";
    else if (metadata.st_gid != static_cast<gid_t>(expectedGroupGid))
        detail = "control directory group mismatch";
    else if ((metadata.st_mode & 07777) != kDirectoryMode)
        detail = "control directory mode must be 0750";
    else if (metadata.st_nlink != 2)
        detail = "control directory link count must be 2";
    else
    {
        detail.clear();
        return true;
    }
    return false;
}

int OpenDirectoryByComponents(int rootFd, const std::string& relativePath)
{
    const int initial = ::fcntl(rootFd, F_DUPFD_CLOEXEC, 3);
    if (initial < 0) return -1;
    int current = initial;
    std::size_t offset = 0;
    while (offset <= relativePath.size())
    {
        const std::size_t slash = relativePath.find('/', offset);
        const std::string component = relativePath.substr(
            offset, slash == std::string::npos ? slash : slash - offset);
        if (component.empty() || component == "." || component == "..")
        {
            ::close(current);
            errno = EINVAL;
            return -1;
        }
        const int next = ::openat(current, component.c_str(),
                                  O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
        const int saved = errno;
        ::close(current);
        if (next < 0)
        {
            errno = saved;
            return -1;
        }
        current = next;
        if (slash == std::string::npos) break;
        offset = slash + 1;
    }
    return current;
}

int OpenDirectoryAnchored(int rootFd, const std::string& absolutePath)
{
    const std::string relativePath = absolutePath.substr(1);
#if defined(__linux__) && defined(SYS_openat2)
    struct open_how how;
    std::memset(&how, 0, sizeof(how));
    how.flags = O_RDONLY | O_DIRECTORY | O_CLOEXEC;
    how.resolve = RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS;
    const int opened = static_cast<int>(::syscall(
        SYS_openat2, rootFd, relativePath.c_str(), &how, sizeof(how)));
    if (opened >= 0) return opened;
    if (errno != ENOSYS && errno != EINVAL) return -1;
#endif
    return OpenDirectoryByComponents(rootFd, relativePath);
}

IbPaperKillSwitchObservation Observation(IbPaperKillSwitchState state,
                                         const char* reason,
                                         const std::string& detail)
{
    IbPaperKillSwitchObservation result;
    result.state = state;
    result.reasonCode = reason;
    result.detail = detail;
    return result;
}
}

bool IbPaperKillSwitchReader::BlocksRiskIncrease(std::string& reason) const
{
    const IbPaperKillSwitchObservation observation = Observe();
    if (observation.state == IbPaperKillSwitchState::Disarmed)
    {
        reason.clear();
        return false;
    }
    reason = observation.reasonCode.empty() ?
        "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN" : observation.reasonCode;
    return true;
}

const char* IbPaperKillSwitch::MarkerName()
{
    return "kill-switch";
}

IbPaperKillSwitch::IbPaperKillSwitch(const std::string& controlDirectory,
                                     int rootFd,
                                     int directoryFd,
                                     std::uint32_t expectedOwnerUid,
                                     std::uint32_t expectedGroupGid,
                                     std::uint64_t device,
                                     std::uint64_t inode, bool persistentMode,
                                     const std::string& maintenanceHoldDirectory)
    : m_controlDirectory(controlDirectory), m_rootFd(rootFd),
      m_directoryFd(directoryFd), m_expectedOwnerUid(expectedOwnerUid),
      m_expectedGroupGid(expectedGroupGid), m_device(device), m_inode(inode),
      m_persistentMode(persistentMode),
      m_maintenanceHoldDirectory(maintenanceHoldDirectory),
      m_directoryUncertain(false)
{
}

IbPaperKillSwitch::~IbPaperKillSwitch()
{
    if (m_directoryFd >= 0) ::close(m_directoryFd);
    if (m_rootFd >= 0) ::close(m_rootFd);
}

bool IbPaperKillSwitch::OpenAndPinProduction(
    const std::string& controlDirectory,
    std::shared_ptr<IbPaperKillSwitch>& result,
    std::string& reason)
{
    return OpenAndPin(controlDirectory, 0,
                      static_cast<std::uint32_t>(::getegid()), true, false,
                      std::string(), result, reason);
}

bool IbPaperKillSwitch::OpenAndPinUserOwned(
    const std::string& controlDirectory,
    std::shared_ptr<IbPaperKillSwitch>& result, std::string& reason)
{
    // Only the authenticated user PAPER runtime binds this extra durable
    // operator marker. A transient arbitrary fixture control directory does
    // not implicitly acquire permission to read or change the user's home.
    const std::string persistent = UserPersistentControlDirectoryPath();
    const std::string runtime = "/run/user/" + std::to_string(::geteuid()) +
        "/heptatrader/control";
    // Never silently fall back to the ephemeral marker when passwd identity
    // lookup failed for the real /run user-control pathname.
    if (controlDirectory == runtime && persistent.empty())
    {
        result.reset();
        reason = "IB_PAPER_MAINTENANCE_OWNER_HOME_UNAVAILABLE";
        return false;
    }
    const bool authenticUserControl = !persistent.empty() &&
        (controlDirectory == runtime || controlDirectory == persistent);
    if (!authenticUserControl)
    {
        result.reset();
        reason = "IB_PAPER_USER_CONTROL_SCOPE_UNTRUSTED";
        return false;
    }
    const std::string maintenance = authenticUserControl ?
        persistent.substr(0, persistent.size() -
                          std::strlen("operator-control")) +
            "operator-maintenance" : std::string();
    return OpenAndPin(controlDirectory, static_cast<std::uint32_t>(::geteuid()),
        static_cast<std::uint32_t>(::getegid()), true,
        controlDirectory == persistent, maintenance, result, reason);
}

bool IbPaperKillSwitch::OpenAndPinForTesting(
    const std::string& controlDirectory,
    std::uint32_t expectedOwnerUid,
    std::uint32_t expectedGroupGid,
    std::shared_ptr<IbPaperKillSwitch>& result,
    std::string& reason, bool persistentMode,
    const std::string& maintenanceHoldDirectory)
{
    return OpenAndPin(controlDirectory, expectedOwnerUid, expectedGroupGid,
                      false, persistentMode, maintenanceHoldDirectory,
                      result, reason);
}

bool IbPaperKillSwitch::OpenAndPin(
    const std::string& controlDirectory,
    std::uint32_t expectedOwnerUid,
    std::uint32_t expectedGroupGid,
    bool requireNonRootService, bool persistentMode,
    const std::string& maintenanceHoldDirectory,
    std::shared_ptr<IbPaperKillSwitch>& result,
    std::string& reason)
{
    result.reset();
    if (!SafeAbsoluteDirectoryPath(controlDirectory) ||
        (!maintenanceHoldDirectory.empty() &&
         !SafeAbsoluteDirectoryPath(maintenanceHoldDirectory)))
    {
        reason = "IB_PAPER_KILL_SWITCH_CONTROL_PATH_INVALID";
        return false;
    }
    if (requireNonRootService && (::geteuid() == 0 || ::getegid() == 0))
    {
        reason = "IB_PAPER_KILL_SWITCH_SERVICE_MUST_BE_NON_ROOT";
        return false;
    }
    const int rootFd = ::open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (rootFd < 0)
    {
        reason = "IB_PAPER_KILL_SWITCH_ROOT_OPEN_FAILED";
        return false;
    }
    const int directoryFd = OpenDirectoryAnchored(rootFd, controlDirectory);
    if (directoryFd < 0)
    {
        ::close(rootFd);
        reason = "IB_PAPER_KILL_SWITCH_CONTROL_OPEN_FAILED";
        return false;
    }
    struct stat metadata;
    std::string detail;
    if (::fstat(directoryFd, &metadata) != 0 ||
        !ValidateDirectoryMetadata(metadata, expectedOwnerUid,
                                   expectedGroupGid, detail))
    {
        ::close(directoryFd);
        ::close(rootFd);
        reason = "IB_PAPER_KILL_SWITCH_CONTROL_UNSAFE";
        return false;
    }
    result.reset(new IbPaperKillSwitch(
        controlDirectory, rootFd, directoryFd, expectedOwnerUid,
        expectedGroupGid, static_cast<std::uint64_t>(metadata.st_dev),
        static_cast<std::uint64_t>(metadata.st_ino), persistentMode,
        maintenanceHoldDirectory));
    reason.clear();
    return true;
}

void IbPaperKillSwitch::LatchDirectoryUncertain(const std::string& detail) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!m_directoryUncertain)
    {
        m_directoryUncertain = true;
        m_directoryUncertainDetail = detail;
    }
}

IbPaperKillSwitchObservation IbPaperKillSwitch::Uncertain(
    const std::string& detail) const
{
    return Observation(IbPaperKillSwitchState::Uncertain,
                       "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN", detail);
}

bool IbPaperKillSwitch::DirectoryIdentityValid(std::string& detail) const
{
    struct stat pinned;
    if (::fstat(m_directoryFd, &pinned) != 0)
    {
        detail = ErrnoDetail("fstat pinned control directory");
        return false;
    }
    if (static_cast<std::uint64_t>(pinned.st_dev) != m_device ||
        static_cast<std::uint64_t>(pinned.st_ino) != m_inode ||
        !ValidateDirectoryMetadata(pinned, m_expectedOwnerUid,
                                   m_expectedGroupGid, detail))
        return false;
    const int reopened = OpenDirectoryAnchored(m_rootFd, m_controlDirectory);
    if (reopened < 0)
    {
        detail = ErrnoDetail("reopen configured control directory");
        return false;
    }
    struct stat current;
    const bool statOk = ::fstat(reopened, &current) == 0;
    const int saved = errno;
    ::close(reopened);
    errno = saved;
    if (!statOk)
    {
        detail = ErrnoDetail("fstat configured control directory");
        return false;
    }
    if (!SameIdentity(pinned, current))
    {
        detail = "configured control directory identity changed";
        return false;
    }
    return ValidateDirectoryMetadata(current, m_expectedOwnerUid,
                                     m_expectedGroupGid, detail);
}

IbPaperKillSwitchObservation IbPaperKillSwitch::Observe() const
{
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_directoryUncertain)
            return Uncertain(m_directoryUncertainDetail);
    }

    std::string detail;
    if (!DirectoryIdentityValid(detail))
    {
        LatchDirectoryUncertain(detail);
        return Uncertain(detail);
    }

    struct stat first;
    if (::fstatat(m_directoryFd, MarkerName(), &first, AT_SYMLINK_NOFOLLOW) != 0)
    {
        if (errno != ENOENT)
            return Uncertain(ErrnoDetail("inspect kill-switch marker"));
        if (!DirectoryIdentityValid(detail))
        {
            LatchDirectoryUncertain(detail);
            return Uncertain(detail);
        }
        struct stat confirmation;
        if (::fstatat(m_directoryFd, MarkerName(), &confirmation,
                      AT_SYMLINK_NOFOLLOW) == 0)
            return Uncertain("kill-switch marker appeared during absence observation");
        if (errno != ENOENT)
            return Uncertain(ErrnoDetail("confirm absent kill-switch marker"));
        const IbPaperKillSwitchObservation maintenance = ObserveMaintenanceHold();
        if (maintenance.state != IbPaperKillSwitchState::Disarmed)
            return maintenance;
        if (m_persistentMode) return ObservePersistentMode();
        return Observation(IbPaperKillSwitchState::Disarmed, "", std::string());
    }

    if (!S_ISREG(first.st_mode) ||
        first.st_uid != static_cast<uid_t>(m_expectedOwnerUid) ||
        first.st_gid != static_cast<gid_t>(m_expectedGroupGid) ||
        (first.st_mode & 07777) != kMarkerMode || first.st_nlink != 1 ||
        static_cast<std::uint64_t>(first.st_dev) != m_device)
        return Uncertain("kill-switch marker metadata is unsafe");

    const int markerFd = ::openat(m_directoryFd, MarkerName(),
                                  O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (markerFd < 0)
        return Uncertain(ErrnoDetail("open kill-switch marker"));
    struct stat opened;
    const bool openedOk = ::fstat(markerFd, &opened) == 0;
    const int saved = errno;
    ::close(markerFd);
    errno = saved;
    if (!openedOk || !SameIdentity(first, opened))
        return Uncertain(openedOk ? "kill-switch marker changed while opening" :
                                   ErrnoDetail("fstat kill-switch marker"));

    struct stat second;
    if (::fstatat(m_directoryFd, MarkerName(), &second,
                  AT_SYMLINK_NOFOLLOW) != 0 || !SameIdentity(first, second))
        return Uncertain("kill-switch marker changed during observation");
    if (!DirectoryIdentityValid(detail))
    {
        LatchDirectoryUncertain(detail);
        return Uncertain(detail);
    }
    return Observation(IbPaperKillSwitchState::Engaged,
                       "IB_PAPER_KILL_SWITCH_ENGAGED", std::string());
}



IbPaperKillSwitchObservation IbPaperKillSwitch::ObserveMaintenanceHold() const
{
    if (m_maintenanceHoldDirectory.empty())
        return Observation(IbPaperKillSwitchState::Disarmed, "", std::string());

    // This pathname is derived from the trusted passwd database by the
    // real user runtime, never HOME/XDG/env or a remote tool request. Resolve
    // every component without symlinks and verify the final private directory.
    const int directory = OpenDirectoryAnchored(m_rootFd, m_maintenanceHoldDirectory);
    if (directory < 0)
        return Uncertain("private operator maintenance directory is missing or unsafe");
    struct stat parent{};
    const bool goodParent = ::fstat(directory, &parent) == 0 &&
        S_ISDIR(parent.st_mode) &&
        parent.st_uid == static_cast<uid_t>(m_expectedOwnerUid) &&
        parent.st_gid == static_cast<gid_t>(m_expectedGroupGid) &&
        (parent.st_mode & 07777) == kMaintenanceDirectoryMode &&
        // Private maintenance storage also holds rollout evidence directories.
        // Child directories do not change the fixed hold filename or its owner,
        // mode, single-link and anchored-identity checks below.
        parent.st_nlink >= 2;

    IbPaperKillSwitchObservation observed;
    if (!goodParent)
        observed = Uncertain("private operator maintenance directory metadata unsafe");
    else
    {
        struct stat before{};
        if (::fstatat(directory, kMaintenanceHoldName,
                      &before, AT_SYMLINK_NOFOLLOW) != 0)
        {
            if (errno != ENOENT)
                observed = Uncertain("operator maintenance marker inspection failed");
            else
            {
                // A second anchored lookup must see the SAME directory inode
                // and continued absence. Never treat a concurrent hold write
                // as a stable idle/off state.
                const int reopened =
                    OpenDirectoryAnchored(m_rootFd, m_maintenanceHoldDirectory);
                struct stat confirmParent{}, ignored{};
                const bool stable = reopened >= 0 &&
                    ::fstat(reopened, &confirmParent) == 0 &&
                    SameIdentity(parent, confirmParent) &&
                    confirmParent.st_mode == parent.st_mode &&
                    confirmParent.st_uid == parent.st_uid &&
                    confirmParent.st_gid == parent.st_gid &&
                    ::fstatat(reopened, kMaintenanceHoldName, &ignored,
                              AT_SYMLINK_NOFOLLOW) != 0 &&
                    errno == ENOENT;
                if (reopened >= 0) ::close(reopened);
                observed = stable ?
                    Observation(IbPaperKillSwitchState::Disarmed, "", std::string()) :
                    Uncertain("operator maintenance marker changed during absence");
            }
        }
        else if (!S_ISREG(before.st_mode) ||
                 before.st_uid != static_cast<uid_t>(m_expectedOwnerUid) ||
                 before.st_gid != static_cast<gid_t>(m_expectedGroupGid) ||
                 (before.st_mode & 07777) != kMaintenanceHoldMode ||
                 before.st_nlink != 1 ||
                 before.st_size == 0 || before.st_size > 4096 ||
                 before.st_dev != parent.st_dev)
            observed = Uncertain("operator maintenance marker metadata unsafe");
        else
        {
            const int fd = ::openat(directory, kMaintenanceHoldName,
                                     O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
            struct stat after{}, named{};
            const bool stable = fd >= 0 &&
                ::fstat(fd, &after) == 0 &&
                ::fstatat(directory, kMaintenanceHoldName, &named,
                          AT_SYMLINK_NOFOLLOW) == 0 &&
                SameIdentity(before, after) && SameIdentity(before, named) &&
                before.st_mode == after.st_mode &&
                before.st_mode == named.st_mode &&
                before.st_uid == named.st_uid &&
                before.st_gid == named.st_gid &&
                before.st_size == after.st_size &&
                before.st_size == named.st_size;
            if (fd >= 0) ::close(fd);
            observed = stable ?
                Observation(IbPaperKillSwitchState::Engaged,
                            "IB_PAPER_OPERATOR_MAINTENANCE_HOLD", "") :
                Uncertain("operator maintenance marker changed during lookup");
        }
    }
    ::close(directory);
    // Double check the parent path is still that same private directory.
    const int nowDirectory = OpenDirectoryAnchored(m_rootFd, m_maintenanceHoldDirectory);
    struct stat nowParent{};
    const bool sameParent = nowDirectory >= 0 &&
        ::fstat(nowDirectory, &nowParent) == 0 &&
        SameIdentity(parent, nowParent) &&
        parent.st_mode == nowParent.st_mode &&
        parent.st_uid == nowParent.st_uid &&
        parent.st_gid == nowParent.st_gid;
    if (nowDirectory >= 0) ::close(nowDirectory);
    if (!sameParent)
        return Uncertain("operator maintenance directory identity changed");
    return observed;
}

std::string IbPaperKillSwitch::UserPersistentControlDirectoryPath()
{
    // Never derive authority paths from caller-controlled HOME/XDG variables.
    struct passwd entry; struct passwd* found=nullptr; char buffer[16384];
    if (::getpwuid_r(::geteuid(), &entry, buffer, sizeof(buffer), &found)!=0 ||
        !found || !entry.pw_dir || !SafeAbsoluteDirectoryPath(entry.pw_dir))
        return std::string();
    return std::string(entry.pw_dir)+"/.local/state/heptatrader/operator-control";
}

IbPaperKillSwitchObservation IbPaperKillSwitch::ObservePersistentMode() const
{
    // Opt-in persistent profile only. Absence means HOLD, not implicit enable.
    // The independent kill-switch marker was checked first and always wins.
    const int fd=::openat(m_directoryFd,"operator-mode",
                         O_RDONLY|O_CLOEXEC|O_NOFOLLOW|O_NONBLOCK);
    if(fd<0) {
        if(errno==ENOENT)
            return Observation(IbPaperKillSwitchState::Engaged,
                               "IB_PAPER_OPERATOR_HOLD", "persistent mode missing");
        return Uncertain(ErrnoDetail("open persistent operator mode"));
    }
    struct stat before{},after{},named{};
    const bool first=::fstat(fd,&before)==0 && S_ISREG(before.st_mode) &&
        before.st_uid==m_expectedOwnerUid && before.st_gid==m_expectedGroupGid &&
        (before.st_mode&07777)==0440 && before.st_nlink==1 &&
        static_cast<std::uint64_t>(before.st_dev)==m_device &&
        before.st_size>0 && before.st_size<=32;
    char bytes[33]={}; ssize_t count=-1;
    if(first) do { count=::read(fd,bytes,sizeof(bytes)); } while(count<0 && errno==EINTR);
    const bool stable=first && count==before.st_size && ::fstat(fd,&after)==0 &&
        ::fstatat(m_directoryFd,"operator-mode",&named,AT_SYMLINK_NOFOLLOW)==0 &&
        SameIdentity(before,after) && SameIdentity(before,named) &&
        before.st_mode==after.st_mode && before.st_mode==named.st_mode &&
        before.st_uid==named.st_uid && before.st_gid==named.st_gid &&
        named.st_nlink==1 && after.st_nlink==1 && before.st_size==named.st_size &&
        before.st_size==after.st_size &&
        before.st_ctim.tv_sec==after.st_ctim.tv_sec &&
        before.st_ctim.tv_nsec==after.st_ctim.tv_nsec &&
        before.st_ctim.tv_sec==named.st_ctim.tv_sec &&
        before.st_ctim.tv_nsec==named.st_ctim.tv_nsec;
    ::close(fd);
    if(!stable) return Uncertain("persistent operator mode unsafe or changed");
    std::string detail;
    if(!DirectoryIdentityValid(detail)) {
        LatchDirectoryUncertain(detail);return Uncertain(detail);
    }
    const std::string mode(bytes,static_cast<std::size_t>(count));
    if(mode=="HOLD\n")
        return Observation(IbPaperKillSwitchState::Engaged,"IB_PAPER_OPERATOR_HOLD","");
    if(mode=="PAPER_ENABLED\n")
        return Observation(IbPaperKillSwitchState::Disarmed,"","");
    return Uncertain("unrecognized persistent operator mode");
}
