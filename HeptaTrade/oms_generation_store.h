#pragma once

#include "oms_journal.h"

#include <cstdint>
#include <functional>
#include <set>
#include <string>
#include <sys/stat.h>
#include <vector>

struct OmsGenerationCommandRecord
{
    std::string agentId;
    std::string sessionId;
    std::string commandId;
    std::string requestHash;
    std::string operation;
    std::string status;
    long orderId = -1;
    std::string reasonCode;
    std::string venueCorrelationId;
    std::uint64_t lastSequence = 0;
    std::string account;
    std::string executionDomain;
    bool durableMutationIntent = false;
};

struct OmsGenerationMutationSummary
{
    std::uint64_t commandCount = 0;
    std::uint64_t correlationReferenceCount = 0;
    std::string commandBindingSha256;
    std::string correlationBindingSha256;
};

struct OmsGenerationSendAttempt
{
    std::string requestKey;
    std::int64_t tsMs = 0;
    std::uint64_t sequence = 0;
};

enum class OmsGenerationLookupStatus
{
    Missing = 0,
    Found,
    Error
};

class OmsGenerationStore
{
public:
    explicit OmsGenerationStore(const std::string& journalPath);
    ~OmsGenerationStore() noexcept;

    OmsGenerationStore(const OmsGenerationStore&) = delete;
    OmsGenerationStore& operator=(const OmsGenerationStore&) = delete;

    bool HasStore() const;
    bool IsActive() const { return m_active; }
    std::uint64_t HistoricalCommandCount() const { return m_commandRecords; }
    std::uint64_t HistoricalSendAttemptCount() const { return m_sendAttemptRecords; }
    const std::string& Generation() const { return m_generation; }

    bool Recover(
        std::size_t maxTailBytes,
        std::size_t maxTailRecords,
        std::size_t maxRecordBytes,
        const std::function<void(const OmsJournalEvent&)>& onEvent,
        std::string& reason);

    bool RecoveryCapacity(std::uint64_t& bytes,
                          std::uint64_t& records,
                          std::string& reason) const;

    OmsGenerationLookupStatus LookupCommand(
        const std::string& agentId,
        const std::string& sessionId,
        const std::string& commandId,
        OmsGenerationCommandRecord& record,
        std::string& reason) const;

    bool ReadPlaceSendAttemptTimes(
        const std::string& account,
        const std::string& executionDomain,
        std::int64_t cutoffMs,
        const std::set<std::string>& excludedRequestKeys,
        std::vector<OmsGenerationSendAttempt>& attempts,
        std::string& reason) const;

    bool SummarizeMutationRecords(
        const std::string& agentId,
        const std::string& sessionId,
        const std::string& account,
        const std::string& executionDomain,
        OmsGenerationMutationSummary& summary,
        std::string& reason) const;

private:
    void Close() noexcept;
    bool Prepare(std::string& reason);
    bool ValidatePinnedIndex(int fd, const std::string& name,
                             const struct stat& expected) const;

    bool PrepareGenerationV1(std::string& reason);
    bool RecoverGenerationV1(
        std::size_t maxTailBytes,
        std::size_t maxTailRecords,
        std::size_t maxRecordBytes,
        const std::function<void(const OmsJournalEvent&)>& onEvent,
        std::string& reason);
    bool RecoveryCapacityGenerationV1(std::uint64_t& bytes,
                                      std::uint64_t& records,
                                      std::string& reason) const;

private:
    std::string m_journalPath;
    std::string m_storePath;
    std::string m_generation;
    bool m_active = false;
    bool m_segmentedTail = false;
    bool m_sendIndexWindowSorted = false;
    int m_storeFd = -1;
    int m_generationFd = -1;
    int m_commandIndexFd = -1;
    int m_sendIndexFd = -1;
    struct stat m_commandIndexIdentity {};
    struct stat m_sendIndexIdentity {};
    std::uint64_t m_commandRecords = 0;
    std::uint64_t m_sendAttemptRecords = 0;
    std::uint64_t m_hotReplayRecords = 0;
    std::uint64_t m_journalPrefixBytes = 0;
    std::string m_journalPrefixSha256;
};
