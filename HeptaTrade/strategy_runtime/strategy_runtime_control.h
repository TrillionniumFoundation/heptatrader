#pragma once

#include <cstddef>
#include <cstdint>
#include <limits>
#include <map>
#include <mutex>
#include <string>

enum class StrategyRuntimePhase
{
    Admitted,
    Running,
    Quarantined,
    Stopped
};

struct StrategyRuntimeBudget
{
    std::uint32_t maxThreads = 0;
    std::uint32_t maxFileDescriptors = 0;
    std::uint64_t maxMemoryBytes = 0;
    std::uint64_t maxCheckpointBytes = 0;
};

struct StrategyArtifactDescriptor
{
    std::string moduleId;
    std::string version;
    std::string artifactDigest;
    std::string configDigest;
    std::string modelDigest;
    StrategyRuntimeBudget budget;
};

struct StrategyRuntimeSnapshot
{
    bool found = false;
    StrategyArtifactDescriptor descriptor;
    StrategyRuntimePhase phase = StrategyRuntimePhase::Admitted;
    std::uint64_t generation = 0;
    std::uint64_t updatedAtMs = 0;
    std::uint64_t checkpointSequence = 0;
    std::string checkpointDigest;
    std::string reasonCode;
};

struct StrategyRuntimeControlResult
{
    bool accepted = false;
    bool duplicate = false;
    std::string reasonCode;
    StrategyRuntimeSnapshot snapshot;
};

// This controller deliberately does not execute untrusted code. It is the
// fail-closed admission/checkpoint/quarantine boundary that an OS sandbox
// must call after independently enforcing process, memory, CPU and FD limits.
class StrategyRuntimeControl
{
public:
    explicit StrategyRuntimeControl(std::size_t maximumModules = 256)
        : m_maximumModules(maximumModules)
    {
    }

    static const char* Version() noexcept
    {
        return "hepta.strategy-runtime-control.v1";
    }

    StrategyRuntimeControlResult Admit(const StrategyArtifactDescriptor& descriptor,
                                       std::uint64_t observedAtMs)
    {
        if (!ValidDescriptor(descriptor) || observedAtMs == 0)
            return Reject("STRATEGY_ADMISSION_INVALID", nullptr);

        std::lock_guard<std::mutex> lock(m_mutex);
        const auto found = m_records.find(descriptor.moduleId);
        if (found != m_records.end())
        {
            if (SameDescriptor(found->second.descriptor, descriptor) &&
                found->second.phase == StrategyRuntimePhase::Admitted)
            {
                StrategyRuntimeControlResult result = Accept(
                    "STRATEGY_ADMISSION_DUPLICATE", found->second);
                result.duplicate = true;
                return result;
            }
            return Reject("STRATEGY_ALREADY_ADMITTED", &found->second);
        }
        if (m_records.size() >= m_maximumModules)
            return Reject("STRATEGY_CAPACITY_EXHAUSTED", nullptr);

        StrategyRuntimeSnapshot snapshot;
        snapshot.found = true;
        snapshot.descriptor = descriptor;
        snapshot.phase = StrategyRuntimePhase::Admitted;
        snapshot.generation = 1;
        snapshot.updatedAtMs = observedAtMs;
        snapshot.reasonCode = "STRATEGY_ADMITTED";
        m_records.emplace(descriptor.moduleId, snapshot);
        return Accept(snapshot.reasonCode.c_str(), snapshot);
    }

    StrategyRuntimeControlResult Start(const std::string& moduleId,
                                       std::uint64_t expectedGeneration,
                                       const std::string& artifactDigest,
                                       std::uint64_t observedAtMs)
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        auto found = m_records.find(moduleId);
        if (found == m_records.end())
            return Reject("STRATEGY_NOT_FOUND", nullptr);
        StrategyRuntimeSnapshot& snapshot = found->second;
        if (!Guard(snapshot, expectedGeneration, observedAtMs))
            return GuardFailure(snapshot, expectedGeneration, observedAtMs);
        if (snapshot.phase != StrategyRuntimePhase::Admitted)
            return Reject("STRATEGY_START_STATE_INVALID", &snapshot);
        if (artifactDigest != snapshot.descriptor.artifactDigest)
            return Reject("STRATEGY_ARTIFACT_DIGEST_MISMATCH", &snapshot);
        if (!Advance(snapshot, observedAtMs))
            return Reject("STRATEGY_GENERATION_EXHAUSTED", &snapshot);
        snapshot.phase = StrategyRuntimePhase::Running;
        snapshot.reasonCode = "STRATEGY_RUNNING";
        return Accept(snapshot.reasonCode.c_str(), snapshot);
    }

    StrategyRuntimeControlResult Checkpoint(
        const std::string& moduleId,
        std::uint64_t expectedGeneration,
        std::uint64_t checkpointSequence,
        const std::string& checkpointDigest,
        std::uint64_t checkpointBytes,
        std::uint64_t observedAtMs)
    {
        if (!CanonicalDigest(checkpointDigest) || checkpointSequence == 0)
            return Reject("STRATEGY_CHECKPOINT_INVALID", nullptr);

        std::lock_guard<std::mutex> lock(m_mutex);
        auto found = m_records.find(moduleId);
        if (found == m_records.end())
            return Reject("STRATEGY_NOT_FOUND", nullptr);
        StrategyRuntimeSnapshot& snapshot = found->second;
        if (!Guard(snapshot, expectedGeneration, observedAtMs))
            return GuardFailure(snapshot, expectedGeneration, observedAtMs);
        if (snapshot.phase != StrategyRuntimePhase::Running)
            return Reject("STRATEGY_CHECKPOINT_STATE_INVALID", &snapshot);
        if (checkpointBytes == 0 ||
            checkpointBytes > snapshot.descriptor.budget.maxCheckpointBytes)
            return Reject("STRATEGY_CHECKPOINT_BUDGET_EXCEEDED", &snapshot);
        if (checkpointSequence < snapshot.checkpointSequence)
            return Reject("STRATEGY_CHECKPOINT_SEQUENCE_STALE", &snapshot);
        if (checkpointSequence == snapshot.checkpointSequence)
        {
            if (checkpointDigest != snapshot.checkpointDigest)
                return Reject("STRATEGY_CHECKPOINT_SEQUENCE_CONFLICT", &snapshot);
            StrategyRuntimeControlResult result = Accept(
                "STRATEGY_CHECKPOINT_DUPLICATE", snapshot);
            result.duplicate = true;
            return result;
        }
        if (!Advance(snapshot, observedAtMs))
            return Reject("STRATEGY_GENERATION_EXHAUSTED", &snapshot);
        snapshot.checkpointSequence = checkpointSequence;
        snapshot.checkpointDigest = checkpointDigest;
        snapshot.reasonCode = "STRATEGY_CHECKPOINT_COMMITTED";
        return Accept(snapshot.reasonCode.c_str(), snapshot);
    }

    StrategyRuntimeControlResult Quarantine(const std::string& moduleId,
                                            std::uint64_t expectedGeneration,
                                            const std::string& reasonCode,
                                            std::uint64_t observedAtMs)
    {
        if (!CanonicalId(reasonCode, 96))
            return Reject("STRATEGY_QUARANTINE_REASON_INVALID", nullptr);
        std::lock_guard<std::mutex> lock(m_mutex);
        auto found = m_records.find(moduleId);
        if (found == m_records.end())
            return Reject("STRATEGY_NOT_FOUND", nullptr);
        StrategyRuntimeSnapshot& snapshot = found->second;
        if (!Guard(snapshot, expectedGeneration, observedAtMs))
            return GuardFailure(snapshot, expectedGeneration, observedAtMs);
        if (snapshot.phase == StrategyRuntimePhase::Stopped)
            return Reject("STRATEGY_QUARANTINE_STATE_INVALID", &snapshot);
        if (!Advance(snapshot, observedAtMs))
            return Reject("STRATEGY_GENERATION_EXHAUSTED", &snapshot);
        snapshot.phase = StrategyRuntimePhase::Quarantined;
        snapshot.reasonCode = reasonCode;
        return Accept("STRATEGY_QUARANTINED", snapshot);
    }

    StrategyRuntimeControlResult Replace(const StrategyArtifactDescriptor& descriptor,
                                         std::uint64_t expectedGeneration,
                                         std::uint64_t observedAtMs)
    {
        if (!ValidDescriptor(descriptor) || observedAtMs == 0)
            return Reject("STRATEGY_REPLACEMENT_INVALID", nullptr);
        std::lock_guard<std::mutex> lock(m_mutex);
        auto found = m_records.find(descriptor.moduleId);
        if (found == m_records.end())
            return Reject("STRATEGY_NOT_FOUND", nullptr);
        StrategyRuntimeSnapshot& snapshot = found->second;
        if (!Guard(snapshot, expectedGeneration, observedAtMs))
            return GuardFailure(snapshot, expectedGeneration, observedAtMs);
        if (snapshot.phase != StrategyRuntimePhase::Quarantined &&
            snapshot.phase != StrategyRuntimePhase::Stopped)
            return Reject("STRATEGY_REPLACEMENT_STATE_INVALID", &snapshot);
        if (SameDescriptor(snapshot.descriptor, descriptor))
            return Reject("STRATEGY_REPLACEMENT_IDENTITY_UNCHANGED", &snapshot);
        if (!Advance(snapshot, observedAtMs))
            return Reject("STRATEGY_GENERATION_EXHAUSTED", &snapshot);
        snapshot.descriptor = descriptor;
        snapshot.phase = StrategyRuntimePhase::Admitted;
        snapshot.checkpointSequence = 0;
        snapshot.checkpointDigest.clear();
        snapshot.reasonCode = "STRATEGY_REPLACEMENT_ADMITTED";
        return Accept(snapshot.reasonCode.c_str(), snapshot);
    }

    StrategyRuntimeControlResult Stop(const std::string& moduleId,
                                      std::uint64_t expectedGeneration,
                                      std::uint64_t observedAtMs)
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        auto found = m_records.find(moduleId);
        if (found == m_records.end())
            return Reject("STRATEGY_NOT_FOUND", nullptr);
        StrategyRuntimeSnapshot& snapshot = found->second;
        if (!Guard(snapshot, expectedGeneration, observedAtMs))
            return GuardFailure(snapshot, expectedGeneration, observedAtMs);
        if (snapshot.phase == StrategyRuntimePhase::Stopped)
        {
            StrategyRuntimeControlResult result =
                Accept("STRATEGY_STOP_DUPLICATE", snapshot);
            result.duplicate = true;
            return result;
        }
        if (!Advance(snapshot, observedAtMs))
            return Reject("STRATEGY_GENERATION_EXHAUSTED", &snapshot);
        snapshot.phase = StrategyRuntimePhase::Stopped;
        snapshot.reasonCode = "STRATEGY_STOPPED";
        return Accept(snapshot.reasonCode.c_str(), snapshot);
    }

    bool Get(const std::string& moduleId, StrategyRuntimeSnapshot& out) const
    {
        out = StrategyRuntimeSnapshot();
        std::lock_guard<std::mutex> lock(m_mutex);
        const auto found = m_records.find(moduleId);
        if (found == m_records.end()) return false;
        out = found->second;
        return true;
    }

private:
    static bool CanonicalId(const std::string& value, std::size_t maximum)
    {
        if (value.empty() || value.size() > maximum) return false;
        for (unsigned char c : value)
        {
            const bool alnum = (c >= 'A' && c <= 'Z') ||
                (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9');
            if (!(alnum || c == '-' || c == '_' || c == '.' || c == ':'))
                return false;
        }
        return true;
    }

    static bool CanonicalDigest(const std::string& value)
    {
        if (value.size() != 71 || value.compare(0, 7, "sha256:") != 0)
            return false;
        for (std::size_t i = 7; i < value.size(); ++i)
        {
            const char c = value[i];
            if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')))
                return false;
        }
        return true;
    }

    static bool ValidDescriptor(const StrategyArtifactDescriptor& value)
    {
        const StrategyRuntimeBudget& budget = value.budget;
        return CanonicalId(value.moduleId, 128) &&
            value.moduleId.compare(0, 6, "hepta.") == 0 &&
            CanonicalId(value.version, 64) &&
            CanonicalDigest(value.artifactDigest) &&
            CanonicalDigest(value.configDigest) &&
            (value.modelDigest.empty() || CanonicalDigest(value.modelDigest)) &&
            budget.maxThreads > 0 && budget.maxThreads <= 64 &&
            budget.maxFileDescriptors > 0 &&
            budget.maxFileDescriptors <= 4096 &&
            budget.maxMemoryBytes > 0 &&
            budget.maxMemoryBytes <= (16ULL << 30) &&
            budget.maxCheckpointBytes > 0 &&
            budget.maxCheckpointBytes <= budget.maxMemoryBytes;
    }

    static bool SameDescriptor(const StrategyArtifactDescriptor& left,
                               const StrategyArtifactDescriptor& right)
    {
        return left.moduleId == right.moduleId &&
            left.version == right.version &&
            left.artifactDigest == right.artifactDigest &&
            left.configDigest == right.configDigest &&
            left.modelDigest == right.modelDigest &&
            left.budget.maxThreads == right.budget.maxThreads &&
            left.budget.maxFileDescriptors == right.budget.maxFileDescriptors &&
            left.budget.maxMemoryBytes == right.budget.maxMemoryBytes &&
            left.budget.maxCheckpointBytes == right.budget.maxCheckpointBytes;
    }

    static bool Guard(const StrategyRuntimeSnapshot& snapshot,
                      std::uint64_t expectedGeneration,
                      std::uint64_t observedAtMs)
    {
        return snapshot.generation == expectedGeneration &&
            observedAtMs >= snapshot.updatedAtMs && observedAtMs != 0;
    }

    static StrategyRuntimeControlResult GuardFailure(
        const StrategyRuntimeSnapshot& snapshot,
        std::uint64_t expectedGeneration,
        std::uint64_t observedAtMs)
    {
        if (snapshot.generation != expectedGeneration)
            return Reject("STRATEGY_GENERATION_STALE", &snapshot);
        return observedAtMs == 0
            ? Reject("STRATEGY_TIME_INVALID", &snapshot)
            : Reject("STRATEGY_TIME_REGRESSION", &snapshot);
    }

    static bool Advance(StrategyRuntimeSnapshot& snapshot,
                        std::uint64_t observedAtMs)
    {
        if (snapshot.generation == std::numeric_limits<std::uint64_t>::max())
            return false;
        ++snapshot.generation;
        snapshot.updatedAtMs = observedAtMs;
        return true;
    }

    static StrategyRuntimeControlResult Accept(
        const char* code, const StrategyRuntimeSnapshot& snapshot)
    {
        StrategyRuntimeControlResult result;
        result.accepted = true;
        result.reasonCode = code;
        result.snapshot = snapshot;
        return result;
    }

    static StrategyRuntimeControlResult Reject(
        const char* code, const StrategyRuntimeSnapshot* snapshot)
    {
        StrategyRuntimeControlResult result;
        result.reasonCode = code;
        if (snapshot != nullptr) result.snapshot = *snapshot;
        return result;
    }

    std::size_t m_maximumModules;
    mutable std::mutex m_mutex;
    std::map<std::string, StrategyRuntimeSnapshot> m_records;
};
