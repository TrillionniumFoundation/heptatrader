#pragma once

#include "rollout_file_boundary.h"
#include <cstdint>
#include <filesystem>
#include <iomanip>
#include <limits>
#include <locale>
#include <map>
#include <mutex>
#include <sstream>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

struct DurableRolloutRecord
{
    std::string moduleId, version, artifactDigest, configDigest, modelDigest, desiredState;
    std::uint64_t generation = 0;
    std::uint64_t updatedAtMs = 0;
};
struct DurableRolloutResult
{
    bool accepted = false;
    bool duplicate = false;
    std::string reasonCode;
    DurableRolloutRecord record;
};
struct ObservedRolloutState
{
    std::string moduleId, artifactDigest, configDigest, state;
};
struct RolloutReconciliationAction
{
    std::string moduleId, action, reasonCode;
    std::uint64_t desiredGeneration = 0;
};

// Local, cooperating single-writer authority. Load must succeed before use.
// V2 tightens admission/I/O while retaining canonical physical V1 bytes.
// No deployment executor, consensus, model/version attestation or signature.
class DurableRolloutStore
{
public:
    explicit DurableRolloutStore(std::filesystem::path path,
                                 std::size_t maximumRecords = 1024,
                                 std::size_t maximumFileBytes = 4 * 1024 * 1024)
        : m_path(std::move(path)), m_maximumRecords(maximumRecords),
          m_maximumFileBytes(maximumFileBytes) {}

    static const char* Version() noexcept { return "hepta.durable-rollout-store.v2"; }

    DurableRolloutResult Load()
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_ready = false;
        if (!ValidLimits()) return Reject("ROLLOUT_STORE_LIMITS_INVALID", nullptr);
        try
        {
            hepta_rollout_detail::FileTransaction file(m_path, m_maximumFileBytes);
            const char* error = file.Open();
            if (*error) return Reject(error, nullptr);
            std::string document; bool present = false;
            error = file.Read(document, present);
            if (*error) return Reject(error, nullptr);
            if (!present && m_hadDocument)
                return Reject("ROLLOUT_STORE_DISAPPEARED", nullptr);
            std::map<std::string, DurableRolloutRecord> parsed;
            std::string reason;
            if (present && !ParseDocument(document, parsed, reason))
                return Reject(reason.c_str(), nullptr);
            // A live handle may observe other cooperating commits, never
            // forget a known module or move its committed history backwards.
            for (const auto& old : m_records)
            {
                const auto next = parsed.find(old.first);
                if (next == parsed.end() || next->second.generation < old.second.generation ||
                    next->second.updatedAtMs < old.second.updatedAtMs ||
                    (next->second.generation == old.second.generation &&
                     !SameRecord(next->second, old.second)))
                    return Reject("ROLLOUT_STORE_HISTORY_REGRESSION", nullptr);
            }
            DurableRolloutResult result = Accept(
                present ? "ROLLOUT_STORE_LOADED" : "ROLLOUT_STORE_EMPTY", nullptr);
            m_records.swap(parsed); m_document.swap(document);
            m_hadDocument = present; m_ready = true;
            return result;
        }
        catch (const std::bad_alloc&) { return Reject("ROLLOUT_STORE_MEMORY_FAILED", nullptr); }
    }

    DurableRolloutResult Put(const DurableRolloutRecord& record)
    {
        if (!ValidRecord(record)) return Reject("ROLLOUT_RECORD_INVALID", nullptr);
        std::lock_guard<std::mutex> lock(m_mutex);
        if (!m_ready) return Reject("ROLLOUT_STORE_NOT_LOADED", nullptr);
        try
        {
            hepta_rollout_detail::FileTransaction file(m_path, m_maximumFileBytes);
            const char* error = file.Open();
            if (*error) return Fail(error);
            std::string current; bool present = false;
            error = file.Read(current, present);
            if (*error) return Fail(error);
            if (present != m_hadDocument || current != m_document)
                return Fail("ROLLOUT_STORE_CONCURRENT_CHANGE");
            const auto found = m_records.find(record.moduleId);
            if (found == m_records.end())
            {
                if (record.generation != 1)
                    return Reject("ROLLOUT_GENERATION_INITIAL_INVALID", nullptr);
                if (m_records.size() >= m_maximumRecords)
                    return Reject("ROLLOUT_STORE_CAPACITY_EXHAUSTED", nullptr);
            }
            else
            {
                if (SameRecord(found->second, record))
                {
                    DurableRolloutResult result = Accept("ROLLOUT_RECORD_DUPLICATE", &found->second);
                    result.duplicate = true; return result;
                }
                if (found->second.generation == std::numeric_limits<std::uint64_t>::max())
                    return Reject("ROLLOUT_GENERATION_EXHAUSTED", &found->second);
                if (record.generation <= found->second.generation)
                    return Reject("ROLLOUT_GENERATION_STALE", &found->second);
                if (record.generation != found->second.generation + 1)
                    return Reject("ROLLOUT_GENERATION_GAP", &found->second);
                if (record.updatedAtMs < found->second.updatedAtMs)
                    return Reject("ROLLOUT_TIME_REGRESSION", &found->second);
            }
            std::map<std::string, DurableRolloutRecord> proposed = m_records;
            proposed[record.moduleId] = record;
            std::string document = SerializeDocument(proposed);
            if (document.size() > m_maximumFileBytes)
                return Reject("ROLLOUT_STORE_SIZE_LIMIT", nullptr);
            // No allocating acknowledgement/state work after durable rename.
            DurableRolloutResult result = Accept("ROLLOUT_RECORD_COMMITTED", &record);
            error = file.Write(document);
            if (*error) return Fail(error);
            m_records.swap(proposed); m_document.swap(document);
            m_hadDocument = true;
            return result;
        }
        catch (const std::bad_alloc&) { return Fail("ROLLOUT_STORE_MEMORY_FAILED"); }
    }

    bool Ready() const { std::lock_guard<std::mutex> lock(m_mutex); return m_ready; }
    bool Get(const std::string& moduleId, DurableRolloutRecord& out) const
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        // moduleId may alias out.moduleId. Resolve it before clearing output.
        const auto found = m_ready ? m_records.find(moduleId) : m_records.end();
        if (found == m_records.end()) { out = DurableRolloutRecord(); return false; }
        out = found->second; return true;
    }
    std::vector<DurableRolloutRecord> List() const
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        std::vector<DurableRolloutRecord> result;
        if (!m_ready) return result;
        result.reserve(m_records.size());
        for (const auto& entry : m_records) result.push_back(entry.second);
        return result;
    }

    std::vector<RolloutReconciliationAction> Reconcile(
        const std::vector<ObservedRolloutState>& observed) const
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (!m_ready) return Blocked("ROLLOUT_STORE_NOT_LOADED");
        if (observed.size() > m_maximumRecords)
            return Blocked("ROLLOUT_OBSERVED_LIMIT");
        std::map<std::string, ObservedRolloutState> byModule;
        for (const auto& state : observed)
        {
            if (!CanonicalId(state.moduleId, 128) || !CanonicalDigest(state.artifactDigest) ||
                !CanonicalDigest(state.configDigest) || !ValidState(state.state))
                return Blocked("ROLLOUT_OBSERVED_INVALID");
            if (!byModule.emplace(state.moduleId, state).second)
                return Blocked("ROLLOUT_OBSERVED_DUPLICATE");
        }
        std::vector<RolloutReconciliationAction> actions;
        actions.reserve(m_records.size());
        for (const auto& entry : m_records)
        {
            const auto& desired = entry.second;
            RolloutReconciliationAction action;
            action.moduleId = desired.moduleId; action.desiredGeneration = desired.generation;
            const auto current = byModule.find(desired.moduleId);
            if (current == byModule.end())
            { action.action = "apply"; action.reasonCode = "ROLLOUT_OBSERVED_MISSING"; }
            else if (current->second.artifactDigest != desired.artifactDigest ||
                     current->second.configDigest != desired.configDigest)
            { action.action = "replace"; action.reasonCode = "ROLLOUT_IDENTITY_DRIFT"; }
            else if (current->second.state != desired.desiredState)
            { action.action = "transition"; action.reasonCode = "ROLLOUT_STATE_DRIFT"; }
            else
            { action.action = "noop"; action.reasonCode = "ROLLOUT_CONVERGED"; }
            actions.push_back(action);
        }
        return actions;
    }

private:
    bool ValidLimits() const
    {
        return m_maximumRecords > 0 && m_maximumRecords <= 4096 &&
            m_maximumFileBytes >= 64 && m_maximumFileBytes <= 16u * 1024u * 1024u;
    }
    static bool CanonicalId(const std::string& value, std::size_t maximum)
    {
        if (value.empty() || value.size() > maximum) return false;
        for (unsigned char c : value)
        {
            const bool alnum = (c >= 'A' && c <= 'Z') ||
                (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9');
            if (!(alnum || c == '-' || c == '_' || c == '.' || c == ':')) return false;
        }
        return true;
    }
    static bool CanonicalDigest(const std::string& value)
    {
        if (value.size() != 71 || value.compare(0, 7, "sha256:") != 0) return false;
        for (std::size_t i = 7; i < value.size(); ++i)
            if (!((value[i] >= '0' && value[i] <= '9') || (value[i] >= 'a' && value[i] <= 'f')))
                return false;
        return true;
    }
    static bool ValidState(const std::string& value)
    {
        return value == "registered" || value == "warming" || value == "shadow" ||
            value == "active" || value == "quarantined" || value == "draining" || value == "stopped";
    }
    static bool ValidRecord(const DurableRolloutRecord& record)
    {
        return CanonicalId(record.moduleId, 128) && record.moduleId.compare(0, 6, "hepta.") == 0 &&
            CanonicalId(record.version, 64) && CanonicalDigest(record.artifactDigest) &&
            CanonicalDigest(record.configDigest) &&
            (record.modelDigest.empty() || CanonicalDigest(record.modelDigest)) &&
            ValidState(record.desiredState) && record.generation != 0 && record.updatedAtMs != 0;
    }
    static bool SameRecord(const DurableRolloutRecord& a, const DurableRolloutRecord& b)
    {
        return a.moduleId == b.moduleId && a.version == b.version && a.artifactDigest == b.artifactDigest &&
            a.configDigest == b.configDigest && a.modelDigest == b.modelDigest && a.desiredState == b.desiredState &&
            a.generation == b.generation && a.updatedAtMs == b.updatedAtMs;
    }
    static bool ParseUnsigned(const std::string& value, std::uint64_t& out)
    {
        if (value.empty() || (value.size() > 1 && value[0] == '0')) return false;
        std::uint64_t parsed = 0;
        for (char c : value)
        {
            if (c < '0' || c > '9') return false;
            const std::uint64_t digit = static_cast<std::uint64_t>(c - '0');
            if (parsed > (std::numeric_limits<std::uint64_t>::max() - digit) / 10) return false;
            parsed = parsed * 10 + digit;
        }
        out = parsed; return true;
    }
    static bool ParseString(const std::string& token, std::string& value)
    {
        const auto separator = token.find(':'); std::uint64_t length;
        if (separator == std::string::npos || !ParseUnsigned(token.substr(0, separator), length) ||
            length != token.size() - separator - 1) return false;
        value.assign(token, separator + 1, std::string::npos); return true;
    }
    static std::string ChecksumText(const std::string& value)
    {
        std::uint64_t hash = 1469598103934665603ULL;
        for (unsigned char c : value) { hash ^= c; hash *= 1099511628211ULL; }
        std::ostringstream output; output.imbue(std::locale::classic());
        output << std::hex << std::setfill('0') << std::setw(16) << hash; return output.str();
    }
    static std::string SerializeDocument(const std::map<std::string, DurableRolloutRecord>& records)
    {
        std::string payload = "HEPTA_ROLLOUT_STORE_V1\n";
        for (const auto& entry : records)
        {
            const auto& r = entry.second;
            payload += "R|" + std::to_string(r.generation) + "|" + std::to_string(r.updatedAtMs);
            for (const auto* s : {&r.moduleId, &r.version, &r.artifactDigest,
                                  &r.configDigest, &r.modelDigest, &r.desiredState})
                payload += "|" + std::to_string(s->size()) + ":" + *s;
            payload += '\n';
        }
        return payload + "CHECKSUM|" + ChecksumText(payload) + "\n";
    }
    bool ParseDocument(const std::string& document,
                       std::map<std::string, DurableRolloutRecord>& records,
                       std::string& reason) const
    {
        const auto checksumStart = document.rfind("CHECKSUM|");
        if (checksumStart == std::string::npos || checksumStart == 0 || document.back() != '\n')
        { reason = "ROLLOUT_STORE_FORMAT_INVALID"; return false; }
        const std::string payload = document.substr(0, checksumStart);
        if (document.substr(checksumStart) != "CHECKSUM|" + ChecksumText(payload) + "\n")
        { reason = "ROLLOUT_STORE_CHECKSUM_MISMATCH"; return false; }
        std::istringstream input(payload); std::string line;
        if (!std::getline(input, line) || line != "HEPTA_ROLLOUT_STORE_V1")
        { reason = "ROLLOUT_STORE_VERSION_INVALID"; return false; }
        while (std::getline(input, line))
        {
            std::vector<std::string> fields;
            std::size_t start = 0;
            for (;;)
            {
                const auto separator = line.find('|', start);
                fields.push_back(line.substr(start, separator == std::string::npos ? separator : separator - start));
                if (fields.size() > 9 || separator == std::string::npos) break;
                start = separator + 1;
            }
            if (fields.size() != 9 || fields[0] != "R")
            { reason = "ROLLOUT_STORE_RECORD_FORMAT_INVALID"; return false; }
            DurableRolloutRecord r;
            if (!ParseUnsigned(fields[1], r.generation) || !ParseUnsigned(fields[2], r.updatedAtMs) ||
                !ParseString(fields[3], r.moduleId) || !ParseString(fields[4], r.version) ||
                !ParseString(fields[5], r.artifactDigest) || !ParseString(fields[6], r.configDigest) ||
                !ParseString(fields[7], r.modelDigest) || !ParseString(fields[8], r.desiredState) || !ValidRecord(r))
            { reason = "ROLLOUT_STORE_RECORD_INVALID"; return false; }
            if (records.size() >= m_maximumRecords || !records.emplace(r.moduleId, r).second)
            { reason = "ROLLOUT_STORE_RECORD_DUPLICATE_OR_LIMIT"; return false; }
        }
        if (SerializeDocument(records) != document)
        { reason = "ROLLOUT_STORE_NONCANONICAL"; return false; }
        return true;
    }
    static DurableRolloutResult Accept(const char* code, const DurableRolloutRecord* record)
    {
        static_assert(std::is_nothrow_move_constructible<DurableRolloutResult>::value,
                      "Acknowledgement return must not throw after commit");
        DurableRolloutResult r; r.accepted = true; r.reasonCode = code;
        if (record) r.record = *record;
        return r;
    }
    static DurableRolloutResult Reject(const char* code, const DurableRolloutRecord* record)
    {
        DurableRolloutResult r; r.reasonCode = code;
        if (record) r.record = *record;
        return r;
    }
    DurableRolloutResult Fail(const char* code) { m_ready = false; return Reject(code, nullptr); }
    static std::vector<RolloutReconciliationAction> Blocked(const char* code)
    {
        RolloutReconciliationAction a; a.action = "blocked"; a.reasonCode = code;
        return {a}; // Empty module ID means the complete reconciliation is blocked.
    }
    std::filesystem::path m_path;
    std::size_t m_maximumRecords, m_maximumFileBytes;
    mutable std::mutex m_mutex;
    std::map<std::string, DurableRolloutRecord> m_records;
    std::string m_document;
    bool m_ready = false;
    bool m_hadDocument = false;
};
