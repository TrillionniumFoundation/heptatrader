#pragma once

#include "session_supervisor_protocol.h"

#include <cstdint>
#include <mutex>
#include <string>

struct stat;

struct ToolDecisionAuditRecord
{
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

class SessionSupervisorAuditJournal
{
public:
    SessionSupervisorAuditJournal();
    ~SessionSupervisorAuditJournal();

    bool Init(const std::string& path, std::string& reason);
    bool Append(const SessionSupervisorRequest& request, const std::string& issuer,
                const std::string& phase, const std::string& outcome,
                std::uint64_t leaseGeneration, std::string& reason);
    bool AppendToolDecision(const ToolDecisionAuditRecord& record, std::string& reason);

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
    bool AppendRecord(const std::string& recordType, const std::string& payload,
                      std::string& reason);
    bool ValidateOpenFile(FileState& state, std::string& reason) const;

    std::mutex m_mutex;
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
