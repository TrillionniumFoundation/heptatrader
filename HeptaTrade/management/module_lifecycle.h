#pragma once

#include <cstdint>
#include <map>
#include <mutex>
#include <string>
#include <vector>

enum class ModuleLifecycleState
{
    Registered,
    Warming,
    Shadow,
    Active,
    Quarantined,
    Draining,
    Stopped
};

struct ModuleArtifactIdentity
{
    std::string moduleId;
    std::string version;
    std::string artifactDigest;
    std::string configDigest;
    std::string modelDigest;
};

struct ModuleHealthEvidence
{
    bool healthy = false;
    std::uint64_t observedAtMs = 0;
    std::string evidenceDigest;
};

struct ModuleLifecycleSnapshot
{
    bool found = false;
    ModuleArtifactIdentity identity;
    ModuleLifecycleState state = ModuleLifecycleState::Registered;
    std::uint64_t generation = 0;
    std::uint64_t updatedAtMs = 0;
    ModuleHealthEvidence health;
    std::string reasonCode;
};

struct ModuleLifecycleResult
{
    bool accepted = false;
    std::string reasonCode;
    ModuleLifecycleSnapshot snapshot;
};

class ModuleLifecycleRegistry
{
public:
    static const char* Version();
    static const char* StateName(ModuleLifecycleState state);

    ModuleLifecycleResult Register(
        const ModuleArtifactIdentity& identity,
        std::uint64_t observedAtMs);
    ModuleLifecycleResult StageUpgrade(
        const ModuleArtifactIdentity& identity,
        std::uint64_t expectedGeneration,
        std::uint64_t observedAtMs);
    ModuleLifecycleResult Transition(
        const std::string& moduleId,
        std::uint64_t expectedGeneration,
        ModuleLifecycleState target,
        const ModuleHealthEvidence& health,
        std::uint64_t observedAtMs);
    ModuleLifecycleResult Quarantine(
        const std::string& moduleId,
        std::uint64_t expectedGeneration,
        const std::string& reasonCode,
        std::uint64_t observedAtMs);
    ModuleLifecycleResult Rollback(
        const std::string& moduleId,
        std::uint64_t expectedGeneration,
        const ModuleHealthEvidence& health,
        std::uint64_t observedAtMs);

    bool Get(const std::string& moduleId, ModuleLifecycleSnapshot& out) const;
    std::vector<ModuleLifecycleSnapshot> ListActive() const;

private:
    struct Record
    {
        ModuleLifecycleSnapshot current;
        ModuleLifecycleSnapshot previousActive;
        bool havePreviousActive = false;
    };

    static bool ValidIdentity(const ModuleArtifactIdentity& identity);
    static bool ValidHealth(const ModuleHealthEvidence& health,
                            std::uint64_t observedAtMs);
    static bool Allowed(ModuleLifecycleState from,
                        ModuleLifecycleState to);
    static ModuleLifecycleResult Reject(const char* code,
                                        const ModuleLifecycleSnapshot* snapshot);

private:
    mutable std::mutex m_mutex;
    std::map<std::string, Record> m_records;
};
