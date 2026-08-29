#pragma once

#include <chrono>
#include <cstdint>
#include <functional>
#include <mutex>
#include <string>
#include <unordered_map>

struct DecisionLeaseKey
{
    std::string executionDomain;
    std::string account;
    std::string instrument;

    bool operator==(const DecisionLeaseKey& other) const;
};

struct DecisionLeaseOwner
{
    std::string agentId;
    std::string sessionId;

    bool operator==(const DecisionLeaseOwner& other) const;
};

struct DecisionLeaseCredential
{
    // Fencing tokens are strictly increasing across all grants made by one
    // manager instance.  A recovered process may seed the initial watermark.
    std::uint64_t fencingToken = 0;

    // Generation is strictly increasing for each individual lease key.
    std::uint64_t generation = 0;

    bool operator==(const DecisionLeaseCredential& other) const;
};

enum class DecisionLeaseStatus
{
    Acquired = 0,
    Renewed,
    Released,
    Valid,
    Busy,
    NotFound,
    Expired,
    OwnerMismatch,
    StaleFence,
    InvalidArgument,
    ClockFailure,
    FencingExhausted
};

struct DecisionLeaseResult
{
    DecisionLeaseStatus status = DecisionLeaseStatus::InvalidArgument;
    DecisionLeaseCredential credential;
    std::chrono::steady_clock::time_point expiresAt;

    bool Succeeded() const;
};

// Serializes Agent decisions for one execution-domain/account/instrument.
//
// All methods are thread safe and fail closed.  A caller must present the
// exact owner and both credential fields on every Renew/Release/Validate.
// Expiration or release makes the credential permanently unusable; the next
// successful Acquire advances both the per-key generation and the global
// fencing token.
class DecisionLeaseManager
{
public:
    typedef std::chrono::steady_clock Clock;
    typedef Clock::time_point TimePoint;
    typedef std::function<TimePoint()> NowProvider;

    DecisionLeaseManager();
    explicit DecisionLeaseManager(const NowProvider& nowProvider,
                                  std::chrono::milliseconds maxTtl = std::chrono::hours(24),
                                  std::uint64_t initialFencingToken = 0);

    DecisionLeaseResult Acquire(const DecisionLeaseKey& key,
                                const DecisionLeaseOwner& owner,
                                std::chrono::milliseconds ttl);
    DecisionLeaseResult Renew(const DecisionLeaseKey& key,
                              const DecisionLeaseOwner& owner,
                              const DecisionLeaseCredential& credential,
                              std::chrono::milliseconds ttl);
    DecisionLeaseResult Release(const DecisionLeaseKey& key,
                                const DecisionLeaseOwner& owner,
                                const DecisionLeaseCredential& credential);
    DecisionLeaseResult Validate(const DecisionLeaseKey& key,
                                 const DecisionLeaseOwner& owner,
                                 const DecisionLeaseCredential& credential);
    std::size_t FenceOwner(const DecisionLeaseOwner& owner);

    static const char* StatusName(DecisionLeaseStatus status);

private:
    struct KeyHash
    {
        std::size_t operator()(const DecisionLeaseKey& key) const;
    };

    struct Entry
    {
        DecisionLeaseOwner owner;
        DecisionLeaseCredential credential;
        TimePoint expiresAt;
        bool active = false;
    };

    static bool ValidKey(const DecisionLeaseKey& key);
    static bool ValidOwner(const DecisionLeaseOwner& owner);
    static bool ValidCredential(const DecisionLeaseCredential& credential);
    bool ValidTtl(std::chrono::milliseconds ttl) const;
    bool ReadNowLocked(TimePoint& now);
    bool ComputeExpiryLocked(TimePoint now,
                             std::chrono::milliseconds ttl,
                             TimePoint& expiresAt) const;
    static bool IsExpired(const Entry& entry, TimePoint now);
    static void Deactivate(Entry& entry);
    static DecisionLeaseResult Result(DecisionLeaseStatus status);
    static DecisionLeaseResult Result(DecisionLeaseStatus status, const Entry& entry);
    DecisionLeaseResult CheckActiveLocked(Entry& entry,
                                          const DecisionLeaseOwner& owner,
                                          const DecisionLeaseCredential& credential,
                                          TimePoint now);

private:
    mutable std::mutex m_mutex;
    std::unordered_map<DecisionLeaseKey, Entry, KeyHash> m_entries;
    NowProvider m_nowProvider;
    std::chrono::milliseconds m_maxTtl;
    std::uint64_t m_lastFencingToken;
    TimePoint m_lastNow;
    bool m_haveLastNow;
};
