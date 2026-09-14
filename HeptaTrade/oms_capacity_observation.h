#pragma once

#include "oms_journal.h"
#include <chrono>
#include <limits>
#include <locale>
#include <sstream>

// Bounded, identifier-free process telemetry. It grants no authority and does
// not certify schema validity, checksums, successful replay or economic state.
inline std::string OmsCapacityObservation(const OmsJournalHealthSnapshot& h, long long observedAtMs,
    const std::string& serviceEpoch = std::string(), std::uint64_t monotonicMs = 0)
{
    const std::uint64_t maximum = std::numeric_limits<std::uint64_t>::max();
    const bool pendingFits = h.queueDepth <= maximum - h.bufferedDepth;
    const std::uint64_t pending = pendingFits ? h.queueDepth + h.bufferedDepth : 0;
    const bool known = h.capacityKnown && !h.writePoisoned && h.replayMaxBytes > 0 &&
        h.replayMaxRecords > 0 && pendingFits && h.currentRecords <= maximum - pending;
    const std::uint64_t projected = known ? h.currentRecords + pending : 0;
    const char* status = "UNKNOWN";
    if (known)
    {
        if (h.currentBytes > h.replayMaxBytes || projected > h.replayMaxRecords) status = "EXCEEDED";
        else if (h.currentBytes >= h.replayMaxBytes - h.replayMaxBytes / 5U ||
                 projected >= h.replayMaxRecords - h.replayMaxRecords / 5U) status = "WARNING";
        else status = "OK";
    }
    std::ostringstream out;
    out.imbue(std::locale::classic());
    out << "{\"schema\":\"heptatrader.oms-capacity.v1\",\"observed_at_ms\":" << observedAtMs
        << ",\"status\":\"" << status << "\",\"known\":" << (known ? "true" : "false")
        << ",\"max_bytes\":" << h.replayMaxBytes << ",\"max_records\":" << h.replayMaxRecords
        << ",\"pending_records\":" << pending << ",\"queue_depth\":" << h.queueDepth
        << ",\"buffered_depth\":" << h.bufferedDepth
        << ",\"pending_bytes\":" << h.pendingBytes
        << ",\"max_pending_bytes\":" << h.maxPendingBytes
        << ",\"max_pending_records\":" << h.maxPendingRecords
        << ",\"queue_capacity_rejections\":" << h.queueCapacityRejections
        << ",\"write_poisoned\":" << (h.writePoisoned ? "true" : "false");
    if (known)
        out << ",\"bytes\":" << h.currentBytes << ",\"records\":" << h.currentRecords
            << ",\"byte_headroom\":" << (h.currentBytes < h.replayMaxBytes ? h.replayMaxBytes - h.currentBytes : 0)
            << ",\"record_headroom\":" << (projected < h.replayMaxRecords ? h.replayMaxRecords - projected : 0);
    else out << ",\"bytes\":null,\"records\":null,\"byte_headroom\":null,\"record_headroom\":null";
    out << ",\"gzip_storage\":" << (h.gzipStorage ? "true" : "false")
        << ",\"storage_bytes\":";
    if (known) out << h.storageBytes; else out << "null";
    const bool safeEpoch = !serviceEpoch.empty() && serviceEpoch.size() <= 128 &&
        serviceEpoch.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_:.") == std::string::npos;
    out << ",\"service_epoch\":";
    if (safeEpoch) out << '\"' << serviceEpoch << '\"';
    else out << "null";
    out << ",\"monotonic_ms\":" << monotonicMs;
    out << ",\"append_latency\":";
    WriteOmsLatencyJson(out, h.appendLatency);
    out << ",\"data_sync_latency\":";
    WriteOmsLatencyJson(out, h.dataSyncLatency);
    out << ",\"replay_validation_latency\":";
    WriteOmsLatencyJson(out, h.replayValidationLatency);
    out << ",\"authorization_effect\":\"NONE\"}";
    return out.str();
}

class OmsCapacityCadence
{
public:
    bool Due(std::chrono::steady_clock::time_point now)
    {
        if (m_started && now < m_next) return false;
        m_started = true;
        m_next = now + std::chrono::seconds(5);
        return true;
    }
private:
    bool m_started = false;
    std::chrono::steady_clock::time_point m_next;
};
