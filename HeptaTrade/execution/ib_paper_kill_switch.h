#pragma once

#include <cstdint>
#include <memory>
#include <mutex>
#include <string>

enum class IbPaperKillSwitchState
{
    Disarmed = 0,
    Engaged,
    Uncertain
};

struct IbPaperKillSwitchObservation
{
    IbPaperKillSwitchState state = IbPaperKillSwitchState::Uncertain;
    std::string reasonCode;
    std::string detail;
};

// Read-only policy seam. Production uses IbPaperKillSwitch; tests may inject
// an in-memory implementation without weakening the daemon configuration.
class IbPaperKillSwitchReader
{
public:
    virtual ~IbPaperKillSwitchReader() {}
    virtual IbPaperKillSwitchObservation Observe() const = 0;

    bool BlocksRiskIncrease(std::string& reason) const;
};

// A root-owned, service-read-only PAPER control plane. The directory is opened
// once through a no-symlink traversal and kept pinned for the process lifetime.
// Every observation also resolves the configured pathname again and compares
// its identity, so rename/replacement cannot turn an engaged/unknown state into
// a false "absent" result.
class IbPaperKillSwitch final : public IbPaperKillSwitchReader
{
public:
    ~IbPaperKillSwitch();

    static const char* MarkerName();

    static bool OpenAndPinProduction(
        const std::string& controlDirectory,
        std::shared_ptr<IbPaperKillSwitch>& result,
        std::string& reason);

    // Filesystem-only test seam. It is not referenced by the production daemon
    // and cannot be selected through environment or argv configuration.
    static bool OpenAndPinForTesting(
        const std::string& controlDirectory,
        std::uint32_t expectedOwnerUid,
        std::uint32_t expectedGroupGid,
        std::shared_ptr<IbPaperKillSwitch>& result,
        std::string& reason);

    IbPaperKillSwitchObservation Observe() const override;

    IbPaperKillSwitch(const IbPaperKillSwitch&) = delete;
    IbPaperKillSwitch& operator=(const IbPaperKillSwitch&) = delete;

private:
    IbPaperKillSwitch(const std::string& controlDirectory,
                      int rootFd,
                      int directoryFd,
                      std::uint32_t expectedOwnerUid,
                      std::uint32_t expectedGroupGid,
                      std::uint64_t device,
                      std::uint64_t inode);

    static bool OpenAndPin(
        const std::string& controlDirectory,
        std::uint32_t expectedOwnerUid,
        std::uint32_t expectedGroupGid,
        bool requireNonRootService,
        std::shared_ptr<IbPaperKillSwitch>& result,
        std::string& reason);

    bool DirectoryIdentityValid(std::string& detail) const;
    void LatchDirectoryUncertain(const std::string& detail) const;
    IbPaperKillSwitchObservation Uncertain(const std::string& detail) const;

    std::string m_controlDirectory;
    int m_rootFd;
    int m_directoryFd;
    std::uint32_t m_expectedOwnerUid;
    std::uint32_t m_expectedGroupGid;
    std::uint64_t m_device;
    std::uint64_t m_inode;
    mutable std::mutex m_mutex;
    mutable bool m_directoryUncertain;
    mutable std::string m_directoryUncertainDetail;
};
