#include "decision_lease_manager.h"

#include <cctype>
#include <limits>

namespace {

bool ValidComponent(const std::string& value)
{
    if (value.empty() || value.size() > 256) return false;
    bool hasNonSpace = false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char ch = static_cast<unsigned char>(value[i]);
        if (ch < 0x20 || ch == 0x7f) return false;
        if (!std::isspace(ch)) hasNonSpace = true;
    }
    return hasNonSpace;
}

std::size_t CombineHash(std::size_t seed, const std::string& value)
{
    const std::size_t hashed = std::hash<std::string>()(value);
    return seed ^ (hashed + static_cast<std::size_t>(0x9e3779b9U) + (seed << 6) + (seed >> 2));
}

} // namespace

bool DecisionLeaseKey::operator==(const DecisionLeaseKey& other) const
{
    return executionDomain == other.executionDomain &&
           account == other.account &&
           instrument == other.instrument;
}

bool DecisionLeaseOwner::operator==(const DecisionLeaseOwner& other) const
{
    return agentId == other.agentId && sessionId == other.sessionId;
}

bool DecisionLeaseCredential::operator==(const DecisionLeaseCredential& other) const
{
    return fencingToken == other.fencingToken && generation == other.generation;
}

bool DecisionLeaseResult::Succeeded() const
{
    return status == DecisionLeaseStatus::Acquired ||
           status == DecisionLeaseStatus::Renewed ||
           status == DecisionLeaseStatus::Released ||
           status == DecisionLeaseStatus::Valid;
}

DecisionLeaseManager::DecisionLeaseManager()
    : m_maxTtl(std::chrono::hours(24)),
      m_lastFencingToken(0),
      m_haveLastNow(false)
{
}

DecisionLeaseManager::DecisionLeaseManager(const NowProvider& nowProvider,
                                           std::chrono::milliseconds maxTtl,
                                           std::uint64_t initialFencingToken)
    : m_nowProvider(nowProvider),
      m_maxTtl(maxTtl),
      m_lastFencingToken(initialFencingToken),
      m_haveLastNow(false)
{
}

std::size_t DecisionLeaseManager::KeyHash::operator()(const DecisionLeaseKey& key) const
{
    std::size_t seed = CombineHash(0, key.executionDomain);
    seed = CombineHash(seed, key.account);
    return CombineHash(seed, key.instrument);
}

bool DecisionLeaseManager::ValidKey(const DecisionLeaseKey& key)
{
    return ValidComponent(key.executionDomain) &&
           ValidComponent(key.account) &&
           ValidComponent(key.instrument);
}

bool DecisionLeaseManager::ValidOwner(const DecisionLeaseOwner& owner)
{
    return ValidComponent(owner.agentId) && ValidComponent(owner.sessionId);
}

bool DecisionLeaseManager::ValidCredential(const DecisionLeaseCredential& credential)
{
    return credential.fencingToken != 0 && credential.generation != 0;
}

bool DecisionLeaseManager::ValidTtl(std::chrono::milliseconds ttl) const
{
    return ttl.count() > 0 && m_maxTtl.count() > 0 && ttl <= m_maxTtl;
}

bool DecisionLeaseManager::ReadNowLocked(TimePoint& now)
{
    try
    {
        now = m_nowProvider ? m_nowProvider() : Clock::now();
    }
    catch (...)
    {
        return false;
    }
    if (m_haveLastNow && now < m_lastNow) return false;
    m_lastNow = now;
    m_haveLastNow = true;
    return true;
}

bool DecisionLeaseManager::ComputeExpiryLocked(TimePoint now,
                                               std::chrono::milliseconds ttl,
                                               TimePoint& expiresAt) const
{
    if (!ValidTtl(ttl)) return false;
    const Clock::duration duration =
        std::chrono::duration_cast<Clock::duration>(ttl);
    if (duration <= Clock::duration::zero() || now > TimePoint::max() - duration)
        return false;
    expiresAt = now + duration;
    return true;
}

bool DecisionLeaseManager::IsExpired(const Entry& entry, TimePoint now)
{
    return entry.active && now >= entry.expiresAt;
}

void DecisionLeaseManager::Deactivate(Entry& entry)
{
    entry.active = false;
    entry.owner = DecisionLeaseOwner();
    entry.expiresAt = TimePoint();
}

DecisionLeaseResult DecisionLeaseManager::Result(DecisionLeaseStatus status)
{
    DecisionLeaseResult result;
    result.status = status;
    return result;
}

DecisionLeaseResult DecisionLeaseManager::Result(DecisionLeaseStatus status, const Entry& entry)
{
    DecisionLeaseResult result;
    result.status = status;
    result.credential = entry.credential;
    result.expiresAt = entry.expiresAt;
    return result;
}

DecisionLeaseResult DecisionLeaseManager::CheckActiveLocked(
    Entry& entry,
    const DecisionLeaseOwner& owner,
    const DecisionLeaseCredential& credential,
    TimePoint now)
{
    if (!entry.active) return Result(DecisionLeaseStatus::NotFound);
    if (IsExpired(entry, now))
    {
        Deactivate(entry);
        return Result(DecisionLeaseStatus::Expired);
    }
    if (!(entry.owner == owner)) return Result(DecisionLeaseStatus::OwnerMismatch);
    if (!(entry.credential == credential)) return Result(DecisionLeaseStatus::StaleFence);
    return Result(DecisionLeaseStatus::Valid, entry);
}

DecisionLeaseResult DecisionLeaseManager::Acquire(const DecisionLeaseKey& key,
                                                  const DecisionLeaseOwner& owner,
                                                  std::chrono::milliseconds ttl)
{
    if (!ValidKey(key) || !ValidOwner(owner) || !ValidTtl(ttl))
        return Result(DecisionLeaseStatus::InvalidArgument);

    std::lock_guard<std::mutex> lock(m_mutex);
    TimePoint now;
    if (!ReadNowLocked(now)) return Result(DecisionLeaseStatus::ClockFailure);
    TimePoint expiresAt;
    if (!ComputeExpiryLocked(now, ttl, expiresAt))
        return Result(DecisionLeaseStatus::InvalidArgument);

    Entry& entry = m_entries[key];
    if (IsExpired(entry, now)) Deactivate(entry);
    if (entry.active) return Result(DecisionLeaseStatus::Busy);
    if (entry.credential.generation == std::numeric_limits<std::uint64_t>::max() ||
        m_lastFencingToken == std::numeric_limits<std::uint64_t>::max())
        return Result(DecisionLeaseStatus::FencingExhausted);

    ++entry.credential.generation;
    entry.credential.fencingToken = ++m_lastFencingToken;
    entry.owner = owner;
    entry.expiresAt = expiresAt;
    entry.active = true;
    return Result(DecisionLeaseStatus::Acquired, entry);
}

DecisionLeaseResult DecisionLeaseManager::Renew(const DecisionLeaseKey& key,
                                                const DecisionLeaseOwner& owner,
                                                const DecisionLeaseCredential& credential,
                                                std::chrono::milliseconds ttl)
{
    if (!ValidKey(key) || !ValidOwner(owner) || !ValidCredential(credential) || !ValidTtl(ttl))
        return Result(DecisionLeaseStatus::InvalidArgument);

    std::lock_guard<std::mutex> lock(m_mutex);
    TimePoint now;
    if (!ReadNowLocked(now)) return Result(DecisionLeaseStatus::ClockFailure);
    std::unordered_map<DecisionLeaseKey, Entry, KeyHash>::iterator found = m_entries.find(key);
    if (found == m_entries.end()) return Result(DecisionLeaseStatus::NotFound);
    DecisionLeaseResult checked = CheckActiveLocked(found->second, owner, credential, now);
    if (!checked.Succeeded()) return checked;

    TimePoint expiresAt;
    if (!ComputeExpiryLocked(now, ttl, expiresAt))
        return Result(DecisionLeaseStatus::InvalidArgument);
    found->second.expiresAt = expiresAt;
    return Result(DecisionLeaseStatus::Renewed, found->second);
}

DecisionLeaseResult DecisionLeaseManager::Release(const DecisionLeaseKey& key,
                                                  const DecisionLeaseOwner& owner,
                                                  const DecisionLeaseCredential& credential)
{
    if (!ValidKey(key) || !ValidOwner(owner) || !ValidCredential(credential))
        return Result(DecisionLeaseStatus::InvalidArgument);

    std::lock_guard<std::mutex> lock(m_mutex);
    TimePoint now;
    if (!ReadNowLocked(now)) return Result(DecisionLeaseStatus::ClockFailure);
    std::unordered_map<DecisionLeaseKey, Entry, KeyHash>::iterator found = m_entries.find(key);
    if (found == m_entries.end()) return Result(DecisionLeaseStatus::NotFound);
    DecisionLeaseResult checked = CheckActiveLocked(found->second, owner, credential, now);
    if (!checked.Succeeded()) return checked;

    const DecisionLeaseResult released = Result(DecisionLeaseStatus::Released, found->second);
    Deactivate(found->second);
    return released;
}

DecisionLeaseResult DecisionLeaseManager::Validate(const DecisionLeaseKey& key,
                                                   const DecisionLeaseOwner& owner,
                                                   const DecisionLeaseCredential& credential)
{
    if (!ValidKey(key) || !ValidOwner(owner) || !ValidCredential(credential))
        return Result(DecisionLeaseStatus::InvalidArgument);

    std::lock_guard<std::mutex> lock(m_mutex);
    TimePoint now;
    if (!ReadNowLocked(now)) return Result(DecisionLeaseStatus::ClockFailure);
    std::unordered_map<DecisionLeaseKey, Entry, KeyHash>::iterator found = m_entries.find(key);
    if (found == m_entries.end()) return Result(DecisionLeaseStatus::NotFound);
    return CheckActiveLocked(found->second, owner, credential, now);
}

std::size_t DecisionLeaseManager::FenceOwner(const DecisionLeaseOwner& owner)
{
    if (!ValidOwner(owner)) return 0;

    std::lock_guard<std::mutex> lock(m_mutex);
    std::size_t fenced = 0;
    for (std::unordered_map<DecisionLeaseKey, Entry, KeyHash>::iterator it =
             m_entries.begin(); it != m_entries.end(); ++it)
    {
        if (it->second.active && it->second.owner == owner)
        {
            Deactivate(it->second);
            ++fenced;
        }
    }
    return fenced;
}

const char* DecisionLeaseManager::StatusName(DecisionLeaseStatus status)
{
    switch (status)
    {
    case DecisionLeaseStatus::Acquired: return "ACQUIRED";
    case DecisionLeaseStatus::Renewed: return "RENEWED";
    case DecisionLeaseStatus::Released: return "RELEASED";
    case DecisionLeaseStatus::Valid: return "VALID";
    case DecisionLeaseStatus::Busy: return "BUSY";
    case DecisionLeaseStatus::NotFound: return "NOT_FOUND";
    case DecisionLeaseStatus::Expired: return "EXPIRED";
    case DecisionLeaseStatus::OwnerMismatch: return "OWNER_MISMATCH";
    case DecisionLeaseStatus::StaleFence: return "STALE_FENCE";
    case DecisionLeaseStatus::InvalidArgument: return "INVALID_ARGUMENT";
    case DecisionLeaseStatus::ClockFailure: return "CLOCK_FAILURE";
    case DecisionLeaseStatus::FencingExhausted: return "FENCING_EXHAUSTED";
    }
    return "UNKNOWN";
}
