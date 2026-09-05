#include "module_lifecycle.h"

#include <algorithm>
#include <limits>
#include <type_traits>
#include <utility>

namespace
{
bool CanonicalId(const std::string& value, std::size_t maximum)
{
    if (value.empty() || value.size() > maximum) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        const bool alphaNumeric =
            (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
            (c >= '0' && c <= '9');
        if (!(alphaNumeric || c == '-' || c == '_' || c == '.' || c == ':'))
            return false;
    }
    return true;
}

bool CanonicalDigest(const std::string& value)
{
    if (value.size() != 71u || value.compare(0, 7, "sha256:") != 0)
        return false;
    for (std::size_t i = 7; i < value.size(); ++i)
    {
        const char c = value[i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')))
            return false;
    }
    return true;
}

bool SameIdentity(const ModuleArtifactIdentity& left,
                  const ModuleArtifactIdentity& right)
{
    return left.moduleId == right.moduleId &&
        left.version == right.version &&
        left.artifactDigest == right.artifactDigest &&
        left.configDigest == right.configDigest &&
        left.modelDigest == right.modelDigest;
}
}

const char* ModuleLifecycleRegistry::Version()
{
    return "hepta.module-lifecycle.v1";
}

const char* ModuleLifecycleRegistry::StateName(ModuleLifecycleState state)
{
    switch (state)
    {
    case ModuleLifecycleState::Registered: return "registered";
    case ModuleLifecycleState::Warming: return "warming";
    case ModuleLifecycleState::Shadow: return "shadow";
    case ModuleLifecycleState::Active: return "active";
    case ModuleLifecycleState::Quarantined: return "quarantined";
    case ModuleLifecycleState::Draining: return "draining";
    case ModuleLifecycleState::Stopped: return "stopped";
    }
    return "unknown";
}

bool ModuleLifecycleRegistry::ValidIdentity(
    const ModuleArtifactIdentity& identity)
{
    return CanonicalId(identity.moduleId, 128u) &&
        identity.moduleId.compare(0, 6, "hepta.") == 0 &&
        CanonicalId(identity.version, 64u) &&
        CanonicalDigest(identity.artifactDigest) &&
        CanonicalDigest(identity.configDigest) &&
        (identity.modelDigest.empty() ||
         CanonicalDigest(identity.modelDigest));
}

bool ModuleLifecycleRegistry::ValidHealth(
    const ModuleHealthEvidence& health,
    std::uint64_t observedAtMs)
{
    return health.healthy && health.observedAtMs != 0 &&
        health.observedAtMs <= observedAtMs &&
        observedAtMs - health.observedAtMs <= 30000u &&
        CanonicalDigest(health.evidenceDigest);
}

bool ModuleLifecycleRegistry::Allowed(
    ModuleLifecycleState from,
    ModuleLifecycleState to)
{
    return (from == ModuleLifecycleState::Registered &&
            to == ModuleLifecycleState::Warming) ||
        (from == ModuleLifecycleState::Warming &&
         to == ModuleLifecycleState::Shadow) ||
        (from == ModuleLifecycleState::Shadow &&
         to == ModuleLifecycleState::Active) ||
        (from == ModuleLifecycleState::Active &&
         to == ModuleLifecycleState::Draining) ||
        (from == ModuleLifecycleState::Draining &&
         to == ModuleLifecycleState::Stopped) ||
        (from == ModuleLifecycleState::Quarantined &&
         to == ModuleLifecycleState::Stopped) ||
        (from == ModuleLifecycleState::Stopped &&
         to == ModuleLifecycleState::Warming);
}

ModuleLifecycleResult ModuleLifecycleRegistry::Reject(
    const char* code,
    const ModuleLifecycleSnapshot* snapshot)
{
    ModuleLifecycleResult result;
    result.reasonCode = code;
    if (snapshot != nullptr) result.snapshot = *snapshot;
    return result;
}

ModuleLifecycleResult ModuleLifecycleRegistry::Commit(
    Record& current, Record proposed, const char* code)
{
    static_assert(std::is_nothrow_move_assignable<Record>::value,
                  "lifecycle record publication must not throw");
    static_assert(std::is_nothrow_move_constructible<ModuleLifecycleResult>::value,
                  "returning an accepted lifecycle result must not throw");
    ModuleLifecycleResult result;
    result.accepted = true;
    result.reasonCode = code;
    result.snapshot = proposed.current;
    // Everything that may allocate has succeeded, including the acknowledgement.
    current = std::move(proposed);
    return result;
}

ModuleLifecycleResult ModuleLifecycleRegistry::Register(
    const ModuleArtifactIdentity& identity,
    std::uint64_t observedAtMs)
{
    if (!ValidIdentity(identity) || observedAtMs == 0)
        return Reject("MODULE_REGISTRATION_INVALID", nullptr);
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<std::string, Record>::const_iterator existing =
        m_records.find(identity.moduleId);
    if (existing != m_records.end())
    {
        if (observedAtMs < existing->second.current.updatedAtMs)
            return Reject("MODULE_TIME_REGRESSION", &existing->second.current);
        if (SameIdentity(existing->second.current.identity, identity))
        {
            ModuleLifecycleResult duplicate;
            duplicate.accepted = true;
            duplicate.reasonCode = "MODULE_REGISTRATION_DUPLICATE";
            duplicate.snapshot = existing->second.current;
            return duplicate;
        }
        return Reject("MODULE_ALREADY_REGISTERED", &existing->second.current);
    }
    Record record;
    record.current.found = true;
    record.current.identity = identity;
    record.current.state = ModuleLifecycleState::Registered;
    record.current.generation = 1;
    record.current.updatedAtMs = observedAtMs;
    record.current.reasonCode = "MODULE_REGISTERED";
    ModuleLifecycleResult result;
    result.accepted = true;
    result.reasonCode = "MODULE_REGISTERED";
    result.snapshot = record.current;
    // emplace either inserts a complete record or leaves the map unchanged.
    m_records.emplace(identity.moduleId, std::move(record));
    return result;
}

ModuleLifecycleResult ModuleLifecycleRegistry::StageUpgrade(
    const ModuleArtifactIdentity& identity,
    std::uint64_t expectedGeneration,
    std::uint64_t observedAtMs)
{
    if (!ValidIdentity(identity) || observedAtMs == 0)
        return Reject("MODULE_UPGRADE_INVALID", nullptr);
    std::lock_guard<std::mutex> lock(m_mutex);
    std::map<std::string, Record>::iterator found =
        m_records.find(identity.moduleId);
    if (found == m_records.end())
        return Reject("MODULE_NOT_FOUND", nullptr);
    Record& record = found->second;
    if (record.current.generation != expectedGeneration)
        return Reject("MODULE_GENERATION_STALE", &record.current);
    if (observedAtMs < record.current.updatedAtMs)
        return Reject("MODULE_TIME_REGRESSION", &record.current);
    if (record.current.state != ModuleLifecycleState::Active &&
        record.current.state != ModuleLifecycleState::Stopped)
        return Reject("MODULE_UPGRADE_STATE_INVALID", &record.current);
    if (SameIdentity(record.current.identity, identity))
        return Reject("MODULE_UPGRADE_IDENTITY_UNCHANGED", &record.current);
    if (record.current.generation ==
        std::numeric_limits<std::uint64_t>::max())
        return Reject("MODULE_GENERATION_EXHAUSTED", &record.current);
    Record proposed = record;
    if (proposed.current.state == ModuleLifecycleState::Active)
    {
        proposed.previousActive = proposed.current;
        proposed.havePreviousActive = true;
    }
    proposed.current.identity = identity;
    proposed.current.state = ModuleLifecycleState::Warming;
    ++proposed.current.generation;
    proposed.current.updatedAtMs = observedAtMs;
    proposed.current.health = ModuleHealthEvidence();
    proposed.current.reasonCode = "MODULE_UPGRADE_STAGED";
    return Commit(record, std::move(proposed), "MODULE_UPGRADE_STAGED");
}

ModuleLifecycleResult ModuleLifecycleRegistry::Transition(
    const std::string& moduleId,
    std::uint64_t expectedGeneration,
    ModuleLifecycleState target,
    const ModuleHealthEvidence& health,
    std::uint64_t observedAtMs)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    std::map<std::string, Record>::iterator found = m_records.find(moduleId);
    if (found == m_records.end()) return Reject("MODULE_NOT_FOUND", nullptr);
    Record& record = found->second;
    if (record.current.generation != expectedGeneration)
        return Reject("MODULE_GENERATION_STALE", &record.current);
    if (observedAtMs < record.current.updatedAtMs)
        return Reject("MODULE_TIME_REGRESSION", &record.current);
    if (!Allowed(record.current.state, target))
        return Reject("MODULE_TRANSITION_INVALID", &record.current);
    const bool healthRequired = target == ModuleLifecycleState::Shadow ||
        target == ModuleLifecycleState::Active;
    if (healthRequired && !ValidHealth(health, observedAtMs))
        return Reject("MODULE_HEALTH_EVIDENCE_INVALID", &record.current);
    if (record.current.generation ==
        std::numeric_limits<std::uint64_t>::max())
        return Reject("MODULE_GENERATION_EXHAUSTED", &record.current);
    Record proposed = record;
    proposed.current.state = target;
    ++proposed.current.generation;
    proposed.current.updatedAtMs = observedAtMs;
    proposed.current.health = healthRequired ? health : ModuleHealthEvidence();
    proposed.current.reasonCode = std::string("MODULE_") +
        (target == ModuleLifecycleState::Warming ? "WARMING" :
         target == ModuleLifecycleState::Shadow ? "SHADOW" :
         target == ModuleLifecycleState::Active ? "ACTIVE" :
         target == ModuleLifecycleState::Draining ? "DRAINING" : "STOPPED");
    // Keep the code pointer alive until Commit has prepared its result.
    const std::string code = proposed.current.reasonCode;
    return Commit(record, std::move(proposed), code.c_str());
}

ModuleLifecycleResult ModuleLifecycleRegistry::Quarantine(
    const std::string& moduleId,
    std::uint64_t expectedGeneration,
    const std::string& reasonCode,
    std::uint64_t observedAtMs)
{
    if (!CanonicalId(reasonCode, 96u))
        return Reject("MODULE_QUARANTINE_REASON_INVALID", nullptr);
    std::lock_guard<std::mutex> lock(m_mutex);
    std::map<std::string, Record>::iterator found = m_records.find(moduleId);
    if (found == m_records.end()) return Reject("MODULE_NOT_FOUND", nullptr);
    Record& record = found->second;
    if (record.current.generation != expectedGeneration)
        return Reject("MODULE_GENERATION_STALE", &record.current);
    if (observedAtMs < record.current.updatedAtMs)
        return Reject("MODULE_TIME_REGRESSION", &record.current);
    if (record.current.state == ModuleLifecycleState::Stopped)
        return Reject("MODULE_QUARANTINE_STATE_INVALID", &record.current);
    if (record.current.generation ==
        std::numeric_limits<std::uint64_t>::max())
        return Reject("MODULE_GENERATION_EXHAUSTED", &record.current);
    Record proposed = record;
    proposed.current.state = ModuleLifecycleState::Quarantined;
    ++proposed.current.generation;
    proposed.current.updatedAtMs = observedAtMs;
    proposed.current.health = ModuleHealthEvidence();
    proposed.current.reasonCode = reasonCode;
    return Commit(record, std::move(proposed), "MODULE_QUARANTINED");
}

ModuleLifecycleResult ModuleLifecycleRegistry::Rollback(
    const std::string& moduleId,
    std::uint64_t expectedGeneration,
    const ModuleHealthEvidence& health,
    std::uint64_t observedAtMs)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    std::map<std::string, Record>::iterator found = m_records.find(moduleId);
    if (found == m_records.end()) return Reject("MODULE_NOT_FOUND", nullptr);
    Record& record = found->second;
    if (record.current.generation != expectedGeneration)
        return Reject("MODULE_GENERATION_STALE", &record.current);
    if (observedAtMs < record.current.updatedAtMs)
        return Reject("MODULE_TIME_REGRESSION", &record.current);
    if (!record.havePreviousActive ||
        (record.current.state != ModuleLifecycleState::Warming &&
         record.current.state != ModuleLifecycleState::Shadow &&
         record.current.state != ModuleLifecycleState::Quarantined))
        return Reject("MODULE_ROLLBACK_UNAVAILABLE", &record.current);
    if (!ValidHealth(health, observedAtMs))
        return Reject("MODULE_HEALTH_EVIDENCE_INVALID", &record.current);
    if (record.current.generation ==
        std::numeric_limits<std::uint64_t>::max())
        return Reject("MODULE_GENERATION_EXHAUSTED", &record.current);
    Record proposed = record;
    proposed.current = record.previousActive;
    proposed.current.generation = record.current.generation + 1u;
    proposed.current.updatedAtMs = observedAtMs;
    proposed.current.state = ModuleLifecycleState::Active;
    proposed.current.health = health;
    proposed.current.reasonCode = "MODULE_ROLLBACK_ACTIVE";
    proposed.previousActive = ModuleLifecycleSnapshot();
    proposed.havePreviousActive = false;
    return Commit(record, std::move(proposed), "MODULE_ROLLBACK_ACTIVE");
}

bool ModuleLifecycleRegistry::Get(
    const std::string& moduleId,
    ModuleLifecycleSnapshot& out) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const std::map<std::string, Record>::const_iterator found =
        m_records.find(moduleId);
    if (found == m_records.end())
    {
        out = ModuleLifecycleSnapshot();
        return false;
    }
    // Lookup precedes any output mutation because moduleId may alias out.
    // Copy failure leaves the caller's output unchanged as well as the registry.
    ModuleLifecycleSnapshot snapshot = found->second.current;
    static_assert(std::is_nothrow_move_assignable<ModuleLifecycleSnapshot>::value,
                  "lifecycle snapshot publication must not throw");
    out = std::move(snapshot);
    return true;
}

std::vector<ModuleLifecycleSnapshot> ModuleLifecycleRegistry::ListActive() const
{
    std::vector<ModuleLifecycleSnapshot> active;
    std::lock_guard<std::mutex> lock(m_mutex);
    for (std::map<std::string, Record>::const_iterator it = m_records.begin();
         it != m_records.end(); ++it)
    {
        if (it->second.current.state == ModuleLifecycleState::Active)
            active.push_back(it->second.current);
    }
    return active;
}
