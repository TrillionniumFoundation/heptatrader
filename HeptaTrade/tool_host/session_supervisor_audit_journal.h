#pragma once

#include "session_supervisor_protocol.h"

#include <cstdint>
#include <mutex>
#include <string>

struct stat;

struct ToolDecisionAuditRecord
{
    // Only the trusted registry classification may mark a routine observation.
    bool observational = false;
    // Trusted Gateway classification only, never a wire field or execution
    // permission. Missing eligibility must not consume the exit reserve.
    bool safetyReserveEligible = false;
    bool peerCredentialAvailable = false;
    std::uint32_t peerUid = 0;
    std::string daemonIdentity;
    std::string executionDomain;
    std::string agentId;
    std::string sessionId;
    std::string account;
    std::string venue;
    std::string environment;
    std::string toolCallId;
    std::string toolName;
    std::string expectedSchemaHash;
    std::string requestFingerprint;
    std::string phase;
    std::string outcome;
    std::string reasonCode;
};

struct SessionSupervisorAuditCapacity
{
    bool known = false;
    std::uint64_t bytes = 0;
    std::uint64_t maximumBytes = 0;
    std::uint64_t safetyReserveBytes = 0;
    std::uint64_t observationsShed = 0;
};

class SessionSupervisorAuditJournal
{
public:
    explicit SessionSupervisorAuditJournal(
        std::uint64_t maximumBytes = 1073741824ULL,
        std::uint64_t safetyReserveBytes = 16777216ULL);
    ~SessionSupervisorAuditJournal();

    bool Init(const std::string& path, std::string& reason);
    bool Append(const SessionSupervisorRequest& request, const std::string& issuer,
                const std::string& phase, const std::string& outcome,
                std::uint64_t leaseGeneration, std::string& reason);
    // A shed observation returns true with OBSERVATION_SHED and a counter,
    // never a durable receipt. Mutation/unknown records are never shed.
    bool AppendToolDecision(const ToolDecisionAuditRecord& record, std::string& reason);

    SessionSupervisorAuditCapacity CapacitySnapshot() const;
    // Offline maintenance only. Existing open writers become fenced by inode
    // replacement. No history is deleted and no execution authority is issued.
    static bool SealSegment(const std::string& path, std::string& reason);
    static bool Verify(const std::string& path, std::uint64_t& chainedRecords,
                       std::string& reason);

private:
    struct FileState
    {
        std::uint64_t fileSize = 0;
        std::int64_t modifiedSeconds = 0;
        std::int64_t modifiedNanoseconds = 0;
        std::int64_t changedSeconds = 0;
        std::int64_t changedNanoseconds = 0;
    };

    static std::string HexEncode(const std::string& value);
    static std::string OperationName(SessionSupervisorOperation operation);
    static std::string Sha256Hex(const std::string& value);
    static FileState CaptureFileState(const struct stat& metadata);
    static bool SameFileState(const FileState& left, const FileState& right);
    static bool LoadChain(int fd, std::uint64_t fileSize,
                          std::uint64_t& nextSequence,
                          std::string& previousHash,
                          std::uint64_t& chainedRecords,
                          std::string& reason);
    void StartChangeWatch(int fd);
    bool ConsumeChanges();
    static bool VerifyHistory(int activeFd, const std::string& path,
                              std::uint64_t& records, std::string& reason);
    enum class RecordClass { Observation, Admission, Safety };
    bool AppendRecord(const std::string& recordType, const std::string& payload,
                      std::string& reason, RecordClass recordClass);
    bool ValidateOpenFile(FileState& state, std::string& reason) const;

    mutable std::mutex m_mutex;
    std::uint64_t m_maximumBytes;
    std::uint64_t m_safetyReserveBytes;
    std::uint64_t m_observationsShed = 0;
    int m_changeFd = -1;
    int m_fd;
    std::string m_path;
    std::string m_canonicalPath;
    std::uint64_t m_device;
    std::uint64_t m_inode;
    FileState m_fileState;
    std::uint64_t m_nextSequence;
    std::uint64_t m_chainedRecords;
    std::string m_previousHash;
    bool m_cacheValid;
};
