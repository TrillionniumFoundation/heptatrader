#pragma once

#include "../oms_generation_store.h"
#include "../oms_journal.h"

#include <openssl/evp.h>

#include <cerrno>
#include <cstdint>
#include <fcntl.h>
#include <limits>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

// The coordinator deliberately recovers only its bounded hot working set from
// V2 generations.  The simulator has one additional requirement: its synthetic
// venue risk state is process-local, so a cold service restart must rebuild the
// exact historical positions, admitted-order count and order-id watermark.
//
// This helper is intentionally separate from OmsGenerationStore::Recover().  It
// walks the digest-bound immutable segment lineage only for simulator state,
// streams records through the production OmsJournal parser, and never feeds the
// historical stream back into the coordinator's hot command store.
namespace hepta_simulator_generation_recovery
{

struct State
{
    State() : admittedOrderCount(0), maximumOrderId(999999) {}
    std::map<std::string, double> positions;
    std::uint64_t admittedOrderCount;
    long maximumOrderId;
};

namespace detail
{

static const std::size_t kMaximumMetadataBytes = 16U * 1024U * 1024U;
static const std::size_t kReadBlockBytes = 1024U * 1024U;
static const char* const kTailHeader = "HEPTA_OMS_ACTIVE_TAIL_V1\t";
static const char* const kRuntimeCurrentHeader = "HEPTA_OMS_RUNTIME_CURRENT_V1";
static const char* const kRuntimeManifestV1 = "HEPTA_OMS_RUNTIME_GENERATION_V1";
static const char* const kRuntimeManifestV2 = "HEPTA_OMS_RUNTIME_GENERATION_V2";

inline bool SameIdentity(const struct stat& a, const struct stat& b)
{
    return a.st_dev == b.st_dev && a.st_ino == b.st_ino &&
        a.st_uid == b.st_uid && a.st_mode == b.st_mode;
}

inline bool PrivateRegular(const struct stat& value)
{
    return S_ISREG(value.st_mode) && value.st_uid == ::geteuid() &&
        (value.st_mode & 0777) == 0600;
}

inline bool PrivateDirectoryPath(const std::string& path)
{
    struct stat value;
    return ::lstat(path.c_str(), &value) == 0 && S_ISDIR(value.st_mode) &&
        value.st_uid == ::geteuid() && (value.st_mode & 0777) == 0700;
}

inline bool SafeName(const std::string& value)
{
    if (value.empty()) return false;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const char c = *it;
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.'))
            return false;
    }
    return true;
}

inline bool HexDigest(const std::string& value)
{
    if (value.size() != 64U) return false;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
        if (!((*it >= '0' && *it <= '9') || (*it >= 'a' && *it <= 'f')))
            return false;
    return true;
}

inline std::string Hex(const unsigned char* bytes, std::size_t size)
{
    static const char digits[] = "0123456789abcdef";
    std::string out;
    out.resize(size * 2U);
    for (std::size_t i = 0; i < size; ++i)
    {
        out[i * 2U] = digits[(bytes[i] >> 4) & 0x0f];
        out[i * 2U + 1U] = digits[bytes[i] & 0x0f];
    }
    return out;
}

inline bool HashBytes(const std::string& data, std::string& digest)
{
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (!context) return false;
    unsigned char bytes[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    const bool ok = EVP_DigestInit_ex(context, EVP_sha256(), NULL) == 1 &&
        (data.empty() || EVP_DigestUpdate(context, data.data(), data.size()) == 1) &&
        EVP_DigestFinal_ex(context, bytes, &length) == 1;
    EVP_MD_CTX_free(context);
    if (!ok || length != 32U) return false;
    digest = Hex(bytes, length);
    return true;
}

inline bool ReadPrivateText(const std::string& path, std::size_t maximum,
                            std::string& output, std::string& digest)
{
    output.clear();
    digest.clear();
    const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return false;
    struct stat before, named;
    bool ok = ::fstat(fd, &before) == 0 && PrivateRegular(before) &&
        before.st_size >= 0 && static_cast<std::uint64_t>(before.st_size) <= maximum &&
        ::lstat(path.c_str(), &named) == 0 && PrivateRegular(named) &&
        SameIdentity(before, named);
    if (ok)
    {
        try
        {
            output.resize(static_cast<std::size_t>(before.st_size));
        }
        catch (...)
        {
            ok = false;
        }
    }
    std::size_t offset = 0;
    while (ok && offset < output.size())
    {
        ssize_t count;
        do
        {
            count = ::pread(fd, &output[offset], output.size() - offset,
                            static_cast<off_t>(offset));
        } while (count < 0 && errno == EINTR);
        if (count <= 0) { ok = false; break; }
        offset += static_cast<std::size_t>(count);
    }
    struct stat after, namedAfter;
    ok = ok && ::fstat(fd, &after) == 0 &&
        ::lstat(path.c_str(), &namedAfter) == 0 &&
        SameIdentity(before, after) && SameIdentity(after, namedAfter) &&
        after.st_size == before.st_size &&
        after.st_mtim.tv_sec == before.st_mtim.tv_sec &&
        after.st_mtim.tv_nsec == before.st_mtim.tv_nsec &&
        after.st_ctim.tv_sec == before.st_ctim.tv_sec &&
        after.st_ctim.tv_nsec == before.st_ctim.tv_nsec;
    if (::close(fd) != 0) ok = false;
    return ok && HashBytes(output, digest);
}

inline bool ParseFields(const std::string& input, const std::string& header,
                        std::map<std::string, std::string>& fields)
{
    fields.clear();
    std::istringstream stream(input);
    std::string line;
    if (!std::getline(stream, line) || line != header) return false;
    while (std::getline(stream, line))
    {
        if (line.empty()) continue;
        const std::size_t equal = line.find('=');
        if (equal == std::string::npos || equal == 0U ||
            line.find('=', equal + 1U) != std::string::npos)
            return false;
        const std::string key = line.substr(0, equal);
        const std::string value = line.substr(equal + 1U);
        if (value.empty() || fields.find(key) != fields.end()) return false;
        fields[key] = value;
    }
    return true;
}

inline bool ExtractCanonicalJsonString(const std::string& input,
                                       const std::string& key,
                                       std::string& value)
{
    const std::string needle = std::string("\"") + key + "\":\"";
    const std::size_t begin = input.find(needle);
    if (begin == std::string::npos || input.find(needle, begin + 1U) != std::string::npos)
        return false;
    const std::size_t valueBegin = begin + needle.size();
    const std::size_t end = input.find('"', valueBegin);
    if (end == std::string::npos) return false;
    value = input.substr(valueBegin, end - valueBegin);
    return value.find('\\') == std::string::npos;
}

class Projection
{
public:
    Projection() : m_valid(true), m_maximumOrderId(999999) {}

    bool Apply(const OmsJournalEvent& event)
    {
        if (event.orderId > m_maximumOrderId) m_maximumOrderId = event.orderId;
        const bool fill = event.eventType == "status" && event.status == "Filled";
        if (event.eventType != "place_sent" && !fill) return true;
        if (event.orderId < 0 || event.venue != "SIMULATOR" || event.account != "SIM" ||
            event.instrument.empty() || (event.side != "BUY" && event.side != "SELL") ||
            !std::isfinite(event.qty) || event.qty <= 0.0)
            return m_valid = false;
        if (!fill)
        {
            const std::map<long, OmsJournalEvent>::const_iterator prior =
                m_admitted.find(event.orderId);
            if (prior != m_admitted.end() &&
                (prior->second.instrument != event.instrument ||
                 prior->second.side != event.side || prior->second.qty != event.qty ||
                 prior->second.reqId != event.reqId ||
                 prior->second.requestHash != event.requestHash))
                return m_valid = false;
            m_admitted[event.orderId] = event;
            return true;
        }
        const std::map<long, OmsJournalEvent>::const_iterator owner =
            m_admitted.find(event.orderId);
        if (!std::isfinite(event.price) || event.price <= 0.0 ||
            owner == m_admitted.end() || owner->second.instrument != event.instrument ||
            owner->second.side != event.side || owner->second.qty != event.qty)
            return m_valid = false;
        const std::map<long, OmsJournalEvent>::const_iterator prior =
            m_fills.find(event.orderId);
        if (prior != m_fills.end() &&
            (prior->second.instrument != event.instrument ||
             prior->second.side != event.side || prior->second.qty != event.qty ||
             prior->second.price != event.price))
            return m_valid = false;
        m_fills[event.orderId] = event;
        return true;
    }

    bool Finish(State& state, std::string& reason) const
    {
        if (!m_valid)
        {
            reason = "EXECUTION_SIMULATOR_RISK_REPLAY_CONFLICT";
            return false;
        }
        if (m_maximumOrderId == std::numeric_limits<long>::max())
        {
            reason = "EXECUTION_ORDER_ID_WATERMARK_EXHAUSTED";
            return false;
        }
        state.positions.clear();
        for (std::map<long, OmsJournalEvent>::const_iterator it = m_fills.begin();
             it != m_fills.end(); ++it)
        {
            const OmsJournalEvent& event = it->second;
            state.positions[event.instrument] += event.side == "BUY" ? event.qty : -event.qty;
            if (!std::isfinite(state.positions[event.instrument]))
            {
                reason = "EXECUTION_SIMULATOR_POSITION_REPLAY_OVERFLOW";
                return false;
            }
        }
        state.admittedOrderCount = static_cast<std::uint64_t>(m_admitted.size());
        state.maximumOrderId = m_maximumOrderId;
        reason.clear();
        return true;
    }

private:
    bool m_valid;
    long m_maximumOrderId;
    std::map<long, OmsJournalEvent> m_admitted;
    std::map<long, OmsJournalEvent> m_fills;
};

inline bool ReplayPinnedFile(const std::string& path, std::size_t start,
                             std::size_t maximumRecordBytes,
                             const std::string& expectedDigest,
                             Projection& projection, std::string& reason,
                             const char* failureCode)
{
    const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) { reason = failureCode; return false; }
    struct stat before, named;
    bool ok = ::fstat(fd, &before) == 0 && PrivateRegular(before) && before.st_size >= 0 &&
        static_cast<std::uint64_t>(before.st_size) >= start &&
        ::lstat(path.c_str(), &named) == 0 && PrivateRegular(named) &&
        SameIdentity(before, named);
    EVP_MD_CTX* context = ok ? EVP_MD_CTX_new() : NULL;
    if (ok && (!context || EVP_DigestInit_ex(context, EVP_sha256(), NULL) != 1)) ok = false;
    std::string pending;
    if (ok) pending.reserve(std::min<std::size_t>(8192U, maximumRecordBytes));
    std::vector<char> buffer(kReadBlockBytes);
    std::uint64_t offset = 0;
    while (ok && offset < static_cast<std::uint64_t>(before.st_size))
    {
        const std::size_t request = static_cast<std::size_t>(std::min<std::uint64_t>(
            buffer.size(), static_cast<std::uint64_t>(before.st_size) - offset));
        ssize_t count;
        do
        {
            count = ::pread(fd, &buffer[0], request, static_cast<off_t>(offset));
        } while (count < 0 && errno == EINTR);
        if (count <= 0) { ok = false; break; }
        if (context && EVP_DigestUpdate(context, &buffer[0], static_cast<std::size_t>(count)) != 1)
        { ok = false; break; }
        const std::uint64_t blockBegin = offset;
        const std::uint64_t blockEnd = offset + static_cast<std::uint64_t>(count);
        std::size_t begin = start > blockBegin ?
            static_cast<std::size_t>(std::min<std::uint64_t>(start - blockBegin, count)) : 0U;
        if (blockEnd > start)
        {
            for (std::size_t i = begin; i < static_cast<std::size_t>(count); ++i)
            {
                if (buffer[i] != '\n') continue;
                const std::size_t length = i - begin;
                if (length > maximumRecordBytes - pending.size())
                { reason = "EXECUTION_SIMULATOR_REPLAY_RECORD_BYTE_LIMIT"; ok = false; break; }
                pending.append(&buffer[begin], length);
                if (pending.empty())
                { reason = "EXECUTION_SIMULATOR_REPLAY_EMPTY_RECORD"; ok = false; break; }
                OmsJournalEvent event;
                if (!OmsJournal::ParseJsonLine(pending, event) || !projection.Apply(event))
                { reason = "EXECUTION_SIMULATOR_RISK_REPLAY_CONFLICT"; ok = false; break; }
                pending.clear();
                begin = i + 1U;
            }
            if (ok && begin < static_cast<std::size_t>(count))
            {
                const std::size_t trailing = static_cast<std::size_t>(count) - begin;
                if (trailing > maximumRecordBytes - pending.size())
                { reason = "EXECUTION_SIMULATOR_REPLAY_RECORD_BYTE_LIMIT"; ok = false; }
                else pending.append(&buffer[begin], trailing);
            }
        }
        offset = blockEnd;
    }
    unsigned char digestBytes[EVP_MAX_MD_SIZE];
    unsigned int digestLength = 0;
    if (ok && context && EVP_DigestFinal_ex(context, digestBytes, &digestLength) != 1) ok = false;
    if (context) EVP_MD_CTX_free(context);
    if (ok && !pending.empty())
    { reason = "EXECUTION_SIMULATOR_REPLAY_TORN_RECORD"; ok = false; }
    if (ok && !expectedDigest.empty() &&
        (digestLength != 32U || Hex(digestBytes, digestLength) != expectedDigest))
    { reason = "EXECUTION_SIMULATOR_GENERATION_SEGMENT_DIGEST_MISMATCH"; ok = false; }
    struct stat after, namedAfter;
    ok = ok && ::fstat(fd, &after) == 0 && ::lstat(path.c_str(), &namedAfter) == 0 &&
        SameIdentity(before, after) && SameIdentity(after, namedAfter) &&
        after.st_size == before.st_size &&
        after.st_mtim.tv_sec == before.st_mtim.tv_sec &&
        after.st_mtim.tv_nsec == before.st_mtim.tv_nsec &&
        after.st_ctim.tv_sec == before.st_ctim.tv_sec &&
        after.st_ctim.tv_nsec == before.st_ctim.tv_nsec;
    if (::close(fd) != 0) ok = false;
    if (!ok && reason.empty()) reason = failureCode;
    return ok;
}

struct Segment
{
    std::string path;
    std::string digest;
};

inline bool ReadTailGeneration(const std::string& journalPath,
                               std::string& generation,
                               std::size_t& headerBytes)
{
    generation.clear();
    headerBytes = 0;
    const int fd = ::open(journalPath.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return false;
    struct stat metadata;
    bool ok = ::fstat(fd, &metadata) == 0 && PrivateRegular(metadata) && metadata.st_size >= 0;
    char data[512];
    ssize_t count = 0;
    if (ok)
    {
        do
        {
            count = ::pread(fd, data, static_cast<std::size_t>(std::min<off_t>(metadata.st_size, sizeof(data))), 0);
        } while (count < 0 && errno == EINTR);
        if (count < 0) ok = false;
    }
    ::close(fd);
    if (!ok) return false;
    const std::string prefix(kTailHeader);
    const std::string raw(data, data + count);
    const std::size_t newline = raw.find('\n');
    if (newline == std::string::npos) return false;
    const std::string first = raw.substr(0, newline);
    if (first.compare(0, prefix.size(), prefix) != 0) return false;
    generation = first.substr(prefix.size());
    headerBytes = newline + 1U;
    return SafeName(generation);
}

inline bool BuildSegmentChain(const std::string& journalPath,
                              const std::string& currentGeneration,
                              std::vector<Segment>& segments,
                              std::string& reason)
{
    const std::string storePath = journalPath + ".generations";
    if (!PrivateDirectoryPath(storePath))
    { reason = "EXECUTION_SIMULATOR_GENERATION_STORE_UNSAFE"; return false; }

    std::string runtimeCurrent, runtimeCurrentDigest;
    if (!ReadPrivateText(storePath + "/CURRENT.runtime", kMaximumMetadataBytes,
                         runtimeCurrent, runtimeCurrentDigest))
    { reason = "EXECUTION_SIMULATOR_GENERATION_CURRENT_UNSAFE"; return false; }
    std::map<std::string, std::string> selected;
    if (!ParseFields(runtimeCurrent, kRuntimeCurrentHeader, selected) ||
        selected["generation"] != currentGeneration ||
        !HexDigest(selected["current_sha256"]) ||
        !HexDigest(selected["manifest_sha256"]) ||
        !HexDigest(selected["runtime_manifest_sha256"]))
    { reason = "EXECUTION_SIMULATOR_GENERATION_CURRENT_INVALID"; return false; }
    std::string current, currentDigest;
    if (!ReadPrivateText(storePath + "/CURRENT", kMaximumMetadataBytes, current, currentDigest) ||
        currentDigest != selected["current_sha256"])
    { reason = "EXECUTION_SIMULATOR_GENERATION_CURRENT_BINDING_INVALID"; return false; }
    const std::string expectedCurrent = std::string("{\"generation\":\"") +
        currentGeneration + "\",\"manifest_sha256\":\"" + selected["manifest_sha256"] +
        "\",\"runtime_manifest_sha256\":\"" + selected["runtime_manifest_sha256"] +
        "\",\"schema\":\"heptatrader.oms-current.v1\"}\n";
    if (current != expectedCurrent)
    { reason = "EXECUTION_SIMULATOR_GENERATION_CURRENT_BINDING_INVALID"; return false; }

    std::set<std::string> seen;
    std::vector<Segment> reverse;
    std::string generation = currentGeneration;
    std::string expectedManifestDigest = selected["manifest_sha256"];
    std::string expectedRuntimeDigest = selected["runtime_manifest_sha256"];
    for (;;)
    {
        if (!SafeName(generation) || !seen.insert(generation).second)
        { reason = "EXECUTION_SIMULATOR_GENERATION_PARENT_CHAIN_INVALID"; return false; }
        const std::string directory = storePath + "/" + generation;
        if (!PrivateDirectoryPath(directory))
        { reason = "EXECUTION_SIMULATOR_GENERATION_DIRECTORY_UNSAFE"; return false; }
        std::string manifestJson, manifestDigest;
        if (!ReadPrivateText(directory + "/manifest.json", kMaximumMetadataBytes,
                             manifestJson, manifestDigest) ||
            manifestDigest != expectedManifestDigest)
        { reason = "EXECUTION_SIMULATOR_GENERATION_MANIFEST_DIGEST_MISMATCH"; return false; }
        std::string runtimeDigest;
        if (!ExtractCanonicalJsonString(manifestJson, "runtime_manifest_sha256", runtimeDigest) ||
            !HexDigest(runtimeDigest) ||
            (!expectedRuntimeDigest.empty() && runtimeDigest != expectedRuntimeDigest))
        { reason = "EXECUTION_SIMULATOR_GENERATION_RUNTIME_BINDING_INVALID"; return false; }
        std::string runtime, observedRuntimeDigest;
        if (!ReadPrivateText(directory + "/runtime-manifest.txt", kMaximumMetadataBytes,
                             runtime, observedRuntimeDigest) || observedRuntimeDigest != runtimeDigest)
        { reason = "EXECUTION_SIMULATOR_GENERATION_RUNTIME_DIGEST_MISMATCH"; return false; }
        const std::size_t newline = runtime.find('\n');
        if (newline == std::string::npos)
        { reason = "EXECUTION_SIMULATOR_GENERATION_RUNTIME_MANIFEST_INVALID"; return false; }
        const std::string header = runtime.substr(0, newline);
        const bool v1 = header == kRuntimeManifestV1;
        const bool v2 = header == kRuntimeManifestV2;
        std::map<std::string, std::string> fields;
        if ((!v1 && !v2) || !ParseFields(runtime, header, fields) ||
            fields["generation"] != generation || !HexDigest(fields["segment_sha256"]) ||
            fields["authorization_effect"] != "NONE" ||
            fields["paper_authorized"] != "0" || fields["live_authorized"] != "0")
        { reason = "EXECUTION_SIMULATOR_GENERATION_RUNTIME_MANIFEST_INVALID"; return false; }
        Segment segment;
        segment.path = directory + "/segment-000001.jsonl";
        segment.digest = fields["segment_sha256"];
        reverse.push_back(segment);
        if (v1) break; // newest v1 segment is a complete journal snapshot
        const std::string parent = fields["parent_generation"];
        const std::string parentDigest = fields["parent_manifest_sha256"];
        if (parent == "-")
        {
            if (parentDigest != "-")
            { reason = "EXECUTION_SIMULATOR_GENERATION_PARENT_CHAIN_INVALID"; return false; }
            break;
        }
        if (!SafeName(parent) || !HexDigest(parentDigest))
        { reason = "EXECUTION_SIMULATOR_GENERATION_PARENT_CHAIN_INVALID"; return false; }
        generation = parent;
        expectedManifestDigest = parentDigest;
        expectedRuntimeDigest.clear();
    }
    segments.assign(reverse.rbegin(), reverse.rend());
    reason.clear();
    return true;
}

} // namespace detail

inline bool Recover(const OmsJournal& journal, State& state, std::string& reason)
{
    const std::string journalPath = journal.GetPath();
    std::string tailGeneration;
    std::size_t tailHeaderBytes = 0;
    if (!detail::ReadTailGeneration(journalPath, tailGeneration, tailHeaderBytes))
    {
        detail::Projection legacyProjection;
        const int replayed = journal.Replay([&](const OmsJournalEvent& event) {
            legacyProjection.Apply(event);
        });
        if (replayed < 0)
        {
            reason = "EXECUTION_OMS_REPLAY_FAILED";
            return false;
        }
        return legacyProjection.Finish(state, reason);
    }

    const OmsJournalHealthSnapshot health = journal.GetHealthSnapshot();
    OmsGenerationStore validator(journalPath);
    if (!validator.HasStore())
    {
        reason = "EXECUTION_SIMULATOR_GENERATION_STORE_MISSING";
        return false;
    }
    std::string validationReason;
    if (!validator.Recover(health.replayMaxBytes, health.replayMaxRecords,
                           health.replayMaxRecordBytes,
                           std::function<void(const OmsJournalEvent&)>(), validationReason) ||
        validator.Generation() != tailGeneration)
    {
        reason = validationReason.empty() ?
            "EXECUTION_SIMULATOR_GENERATION_VALIDATION_FAILED" : validationReason;
        return false;
    }

    std::vector<detail::Segment> segments;
    if (!detail::BuildSegmentChain(journalPath, tailGeneration, segments, reason)) return false;
    detail::Projection projection;
    for (std::vector<detail::Segment>::const_iterator it = segments.begin();
         it != segments.end(); ++it)
    {
        if (!detail::ReplayPinnedFile(it->path, 0U, health.replayMaxRecordBytes,
                                      it->digest, projection, reason,
                                      "EXECUTION_SIMULATOR_GENERATION_SEGMENT_REPLAY_FAILED"))
            return false;
    }
    if (!detail::ReplayPinnedFile(journalPath, tailHeaderBytes,
                                  health.replayMaxRecordBytes, std::string(),
                                  projection, reason,
                                  "EXECUTION_SIMULATOR_ACTIVE_TAIL_REPLAY_FAILED"))
        return false;
    return projection.Finish(state, reason);
}

} // namespace hepta_simulator_generation_recovery
