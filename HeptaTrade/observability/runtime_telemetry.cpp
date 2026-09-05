#include "runtime_telemetry.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <iomanip>
#include <limits>
#include <locale>
#include <set>
#include <sstream>
#include <vector>

namespace
{
const std::uint64_t kLatencyBuckets[12] = {
    10, 50, 100, 500, 1000, 5000,
    10000, 50000, 100000, 500000, 1000000, 5000000
};

std::uint64_t Fnv1a(const std::string& value)
{
    std::uint64_t hash = 1469598103934665603ULL;
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        hash ^= static_cast<unsigned char>(*it);
        hash *= 1099511628211ULL;
    }
    return hash;
}

std::string Hex64(std::uint64_t value)
{
    std::ostringstream out;
    out.imbue(std::locale::classic());
    out << 'h' << std::hex << std::setw(16) << std::setfill('0') << value;
    return out.str();
}

bool SafeMetricName(const std::string& value)
{
    if (value.empty() || value.size() > 96) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const char c = value[i];
        if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_'))
  return false;
    }
    return true;
}

bool SafeLabelName(const std::string& value)
{
    if (value.empty() || value.size() > 32) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        if (i == 0)
        {
            if (c < 'a' || c > 'z') return false;
        }
        else if (!((c >= 'a' && c <= 'z') ||
                   (c >= '0' && c <= '9') || c == '_'))
            return false;
    }
    return true;
}

std::string LowerAscii(const std::string& value)
{
    std::string lower;
    lower.reserve(value.size());
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        lower.push_back(static_cast<char>(
            c >= 'A' && c <= 'Z' ? c - 'A' + 'a' : c));
    }
    return lower;
}

bool HasSensitiveNamePart(const std::string& labelName)
{
    const std::string lower = LowerAscii(labelName);
    // A name containing one of these words is itself a useful signal that its
    // value is identity/secret material.  Redact instead of hashing: hashes
    // of short account IDs and tokens are cheap to dictionary attack.
    static const char* const sensitiveParts[] = {
        "account", "credential", "token", "secret", "password",
        "prompt", "authorization", "api_key", "apikey", "private_key",
        "session", "session_id", "command", "command_id", "order", "order_id",
        "client", "client_id", "owner", "owner_id", "user", "user_id", "uid",
        "strategy", "strategy_id", "raw", "detail", "message",
        "path", "file", "uri", "url"
    };
    for (std::size_t i = 0;
         i < sizeof(sensitiveParts) / sizeof(sensitiveParts[0]); ++i)
    {
        if (lower.find(sensitiveParts[i]) != std::string::npos) return true;
    }
    return false;
}

bool HasSensitiveMetricPart(const std::string& metric)
{
    const std::string lower = LowerAscii(metric);
    static const char* const sensitiveParts[] = {
        "account", "credential", "token", "secret", "password",
        "prompt", "authorization", "api_key", "apikey", "private_key"
    };
    for (std::size_t i = 0;
         i < sizeof(sensitiveParts) / sizeof(sensitiveParts[0]); ++i)
        if (lower.find(sensitiveParts[i]) != std::string::npos) return true;
    return false;
}

bool LooksLikeBrokerAccount(const std::string& value)
{
    // IB account identifiers conventionally begin with DU/U and may carry a
    // short suffix (for example, a sub-account marker).  Require a digit right
    // after the prefix so ordinary words such as "user" are unaffected.
    const std::string lower = LowerAscii(value);
    const bool du = lower.size() > 2 && lower.compare(0, 2, "du") == 0;
    const bool u = lower.size() > 1 && lower[0] == 'u';
    const std::size_t offset = du ? 2 : (u ? 1 : 0);
    if (offset == 0 || value.size() <= offset ||
        value[offset] < '0' || value[offset] > '9') return false;
    if (value.size() > 32) return false;
    for (std::size_t i = offset; i < value.size(); ++i)
    {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        if (!((c >= '0' && c <= '9') || c == '-' || c == '_')) return false;
    }
    return true;
}

bool LooksLikeOpaqueSecret(const std::string& value)
{
    if (value.empty()) return false;
    const std::string lower = LowerAscii(value);
    static const char* const sensitiveWords[] = {
        "token", "secret", "credential", "password", "bearer",
        "authorization", "api_key", "apikey", "private_key"
    };
    for (std::size_t i = 0;
         i < sizeof(sensitiveWords) / sizeof(sensitiveWords[0]); ++i)
    {
        if (lower.find(sensitiveWords[i]) != std::string::npos) return true;
    }

    // Common broker account forms are short and entirely alphanumeric, so a
    // character-only sanitizer would otherwise expose them verbatim.
    if (LooksLikeBrokerAccount(value)) return true;

    // Other venues may use a different account namespace.  A compact mixed
    // alpha/numeric identifier is not a useful low-cardinality dimension, so
    // treat it as opaque at the untyped boundary as well.  Typed dimensions
    // (for example the decimal `series` fixture) are validated separately.
    if (value.size() >= 4)
    {
        bool hasAlpha = false;
        bool hasDigit = false;
        bool allAlphaNumeric = true;
        for (std::size_t i = 0; i < value.size(); ++i)
        {
            const unsigned char c = static_cast<unsigned char>(value[i]);
            hasAlpha = hasAlpha || ((c >= 'a' && c <= 'z') ||
                                    (c >= 'A' && c <= 'Z'));
            hasDigit = hasDigit || (c >= '0' && c <= '9');
            allAlphaNumeric = allAlphaNumeric &&
                (((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')) ||
                 (c >= '0' && c <= '9'));
        }
        if (allAlphaNumeric && hasAlpha && hasDigit) return true;
    }

    // Long mixed alphanumeric strings are overwhelmingly opaque credentials,
    // hashes or session material.  Do not preserve them as labels even when
    // they happen to satisfy the printable-character check below.
    if (value.size() >= 16)
    {
        bool hasAlpha = false;
        bool hasDigit = false;
        bool allAlphaNumeric = true;
        for (std::size_t i = 0; i < value.size(); ++i)
        {
            const unsigned char c = static_cast<unsigned char>(value[i]);
            hasAlpha = hasAlpha || ((c >= 'a' && c <= 'z') ||
                                    (c >= 'A' && c <= 'Z'));
            hasDigit = hasDigit || (c >= '0' && c <= '9');
            allAlphaNumeric = allAlphaNumeric &&
                (((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')) ||
                 (c >= '0' && c <= '9'));
        }
        if (allAlphaNumeric || (hasAlpha && hasDigit)) return true;
    }
    return false;
}

bool SafeLabelValueCharacters(const std::string& value)
{
    if (value.empty() || value.size() > 96) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '_' || c == '-' ||
              c == '.' || c == ':'))
            return false;
    }
    return true;
}

bool InSet(const std::string& value,
           const char* const* values,
           std::size_t count)
{
    for (std::size_t i = 0; i < count; ++i)
        if (value == values[i]) return true;
    return false;
}

bool CanonicalReasonCode(const std::string& value)
{
    if (value == "none") return true;
    if (value.empty() || value.size() > 96) return false;
    bool underscore = false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        if (c == '_')
        {
            underscore = true;
            continue;
        }
        if (c < 'A' || c > 'Z') return false;
    }
    if (!underscore) return false;

    const std::size_t separator = value.find('_');
    const std::string prefix = value.substr(0, separator);
    static const char* const prefixes[] = {
        "ACCOUNT", "ACCEPTED", "ACTIVATED", "AGENT", "ALREADY",
        "AUTHORITATIVE", "BAD", "BROKER", "BUSY", "CANCEL",
        "CAPABILITY", "CATALOG", "CAUSAL", "CLOCK", "COMMAND",
        "COMPLETED", "CONTRACT", "CONTROL", "DECISION", "DISCOVERY",
        "DUPLICATE", "EVENT", "EXECUTION", "EXPIRED", "EXTERNAL",
        "FENCE", "FENCED", "FENCING", "FLATTEN", "FRAME", "GATEWAY",
        "GLOBAL", "IB", "INVALID", "JOURNAL", "KILL", "MISSING",
        "NO", "NOT", "OMS", "OPERATOR", "ORDER", "OWNER", "PAPER",
        "PEER", "PENDING", "PLACE", "POSITION", "PROTOCOL", "QUOTE",
        "QUEUE", "READ", "RECONCILE", "RECOVERY", "REQUEST", "RISK",
        "SERVER", "SERVICE", "SESSION", "SIM", "SIMULATOR", "SOCKET",
        "STATE", "SYSTEMD", "TERMINAL", "TOOL", "UNAVAILABLE",
        "UNSUPPORTED", "UNKNOWN", "UNEXPECTED", "UNCLASSIFIED", "WRITE",
        "FAILURE", "ERROR", "STALE", "SNAPSHOT", "OBSERVATION", "SCHEMA",
        "TARGET", "MUTATION", "SUPERVISOR", "LEASE", "AUDIT", "REQUIRED",
        "RESULT", "VENUE", "INCONSISTENT", "LOCAL", "REDUCE", "TRADE",
        "CURRENT"
    };
    if (!InSet(prefix, prefixes, sizeof(prefixes) / sizeof(prefixes[0])))
        return false;

    // A canonical reason code can mention credential/account *state*, but a
    // value that contains a secret-bearing field must never become a label.
    // Check the complete code, not only its first component, so values such as
    // ACCOUNT_SECRET and INVALID_TOKEN are collapsed as well.
    static const char* const forbiddenWords[] = {
        "TOKEN", "SECRET", "PASSWORD", "BEARER", "AUTHORIZATION",
        "API_KEY", "PRIVATE_KEY"
    };
    for (std::size_t i = 0;
         i < sizeof(forbiddenWords) / sizeof(forbiddenWords[0]); ++i)
        if (value.find(forbiddenWords[i]) != std::string::npos) return false;
    return true;
}

bool CanonicalTool(const std::string& value)
{
    static const char* const tools[] = {
        "system.tools.list", "system.tools.describe", "system.cancel_request",
        "system.get_health", "market.get_quote", "account.get_summary",
        "portfolio.list_positions", "orders.list", "execution.get_command_status",
        "risk.get_limits", "decision.get_snapshot", "intent.preview_target_position",
        "intent.apply_target_position", "events.wait", "watch.get_snapshot",
        "risk.preview_order", "trade.place_order", "trade.cancel_order",
        "risk.preview_flatten", "trade.flatten_position"
    };
    return InSet(value, tools, sizeof(tools) / sizeof(tools[0]));
}

bool CanonicalEventType(const std::string& value)
{
    static const char* const events[] = {
        "order_intent", "place_send_attempt", "flatten_intent",
        "flatten_send_attempt", "cancel_send_attempt", "place_sent",
        "flatten_sent", "flatten_noop", "flatten_reject",
        "flatten_outcome_uncertain", "place_outcome_uncertain", "status",
        "cancel", "reject", "risk_blocked", "session_owner_fenced",
        "session_owner_fence_release", "session_owner_recovery_only",
        "paper_terminal_fence", "order_owner_reconciled_terminal",
        "execution_projection_failed", "execution_projection_resolved",
        "execution_command_resolved", "cancel_command_resolved",
        "cancel_sent", "fill", "ack", "correction",
        "broker_order_status", "broker_order_accepted", "broker_error",
        "broker_execution", "broker_completed_order", "broker_completed_orders_end",
        "broker_execution_details_end"
    };
    return InSet(value, events, sizeof(events) / sizeof(events[0]));
}

bool CanonicalValueForName(const std::string& name,
                           const std::string& value)
{
    if (name == "reason_code") return CanonicalReasonCode(value);
    if (name == "tool") return CanonicalTool(value);
    if (name == "event_type" || name == "kind")
        return CanonicalEventType(value);

    static const char* const decisions[] = {"allow", "reject"};
    if (name == "decision")
        return InSet(value, decisions, sizeof(decisions) / sizeof(decisions[0]));

    static const char* const statuses[] = {
        "ok", "permission_denied", "invalid_tool", "rejected", "duplicate",
        "uncertain", "error", "unknown", "attempt", "complete", "observed",
        "accepted", "cancelrequested", "cancelled", "filled", "executiondetails",
        "end", "sent", "pending", "cancel_sent", "cancel_requested",
        "authoritative_resync_complete", "projection_failed", "projection_resolved"
    };
    if (name == "status")
    {
        std::string normalized = LowerAscii(value);
        // Keep the value itself only for a finite status vocabulary.  The
        // caller may use title case for broker callback statuses.
        return InSet(normalized, statuses,
                     sizeof(statuses) / sizeof(statuses[0]));
    }

    static const char* const operations[] = {
        "place", "cancel", "flatten", "startup", "evaluate", "reconcile"
    };
    if (name == "operation")
        return InSet(value, operations, sizeof(operations) / sizeof(operations[0])) ||
            CanonicalEventType(value);

    static const char* const outcomes[] = {
        "complete", "observed", "accepted", "rejected", "uncertain", "error",
        "pending", "resolved"
    };
    if (name == "outcome")
        return InSet(value, outcomes, sizeof(outcomes) / sizeof(outcomes[0]));

    static const char* const states[] = {
        "open", "blocked", "closed", "unknown", "enabled", "disabled"
    };
    if (name == "state")
        return InSet(value, states, sizeof(states) / sizeof(states[0]));

    static const char* const environments[] = {"sim", "paper", "watch", "unknown"};
    if (name == "environment")
        return InSet(LowerAscii(value), environments,
                     sizeof(environments) / sizeof(environments[0]));

    static const char* const venues[] = {"simulator", "ib", "unknown"};
    if (name == "venue")
        return InSet(LowerAscii(value), venues, sizeof(venues) / sizeof(venues[0]));

    // `series` is useful in bounded test fixtures and is intentionally limited
    // to decimal digits; no production helper emits it.
    if (name == "series")
    {
        if (value.empty() || value.size() > 20) return false;
        for (std::size_t i = 0; i < value.size(); ++i)
            if (value[i] < '0' || value[i] > '9') return false;
        return true;
    }
    return false;
}

std::string SanitizeLabels(const std::string& labels)
{
    if (labels.empty()) return std::string();
    // Keep parsing bounded even when a caller accidentally forwards a large
    // JSON/detail string.  Hashing the complete input avoids retaining any
    // delimiter or secret fragment in the series key.
    if (labels.size() > 512) return "labels=redacted";

    std::ostringstream out;
    out.imbue(std::locale::classic());
    std::set<std::string> names;
    std::size_t start = 0;
    std::size_t count = 0;
    bool first = true;
    while (start <= labels.size())
    {
        const std::size_t end = labels.find('|', start);
        const std::size_t length = end == std::string::npos ?
            labels.size() - start : end - start;
        const std::string part = labels.substr(start, length);
        const std::size_t equal = part.find('=');
        if (part.empty() || equal == std::string::npos || equal == 0 ||
            part.find('=', equal + 1) != std::string::npos)
            return "labels=redacted";

        const std::string name = part.substr(0, equal);
        const std::string value = part.substr(equal + 1);
        if (!SafeLabelName(name) || !names.insert(name).second || ++count > 8)
            return "labels=redacted";

        if (!first) out << '|';
        first = false;
        out << name << '=' << RuntimeTelemetry::BoundedLabelFor(name, value);
        if (end == std::string::npos) break;
        start = end + 1;
    }
    const std::string sanitized = out.str();
    return sanitized.size() > 512 ? std::string("labels=redacted") : sanitized;
}

std::string EscapeJson(const std::string& value)
{
    std::ostringstream out;
    out.imbue(std::locale::classic());
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const unsigned char c = static_cast<unsigned char>(*it);
        if (c == '"') out << "\\\"";
        else if (c == '\\') out << "\\\\";
        else if (c < 0x20) out << '?';
        else out << *it;
    }
    return out.str();
}

std::string Labels1(const char* name, const std::string& value)
{
    return std::string(name) + '=' +
        RuntimeTelemetry::BoundedLabelFor(name, value);
}

std::string Labels2(const char* firstName, const std::string& firstValue,
          const char* secondName, const std::string& secondValue)
{
    return Labels1(firstName, firstValue) + '|' +
        Labels1(secondName, secondValue);
}

std::string Labels3(const char* firstName, const std::string& firstValue,
          const char* secondName, const std::string& secondValue,
          const char* thirdName, const std::string& thirdValue)
{
    return Labels2(firstName, firstValue, secondName, secondValue) + '|' +
        Labels1(thirdName, thirdValue);
}

std::string StatusName(int status)
{
    switch (status)
    {
    case 0: return "ok";
    case 1: return "permission_denied";
    case 2: return "invalid_tool";
    case 3: return "rejected";
    case 4: return "duplicate";
    case 5: return "uncertain";
    case 6: return "error";
    default: return "unknown";
    }
}

bool Contains(const std::string& value, const char* token)
{
    return value.find(token) != std::string::npos;
}
}

RuntimeTelemetry& RuntimeTelemetry::Global()
{
    static RuntimeTelemetry telemetry;
    return telemetry;
}

std::string RuntimeTelemetry::BoundedLabel(const std::string& value)
{
    if (value.empty()) return "none";
    if (LooksLikeOpaqueSecret(value)) return "redacted";
    // Uppercase underscore tokens are normally reason codes.  Keep only the
    // reviewed vocabulary when this untyped entry point is used; otherwise an
    // arbitrary value such as `ABC_DEF` could masquerade as a harmless code.
    bool uppercaseCode = value.size() > 1;
    bool hasUnderscore = false;
    for (std::size_t i = 0; uppercaseCode && i < value.size(); ++i)
    {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        if (c == '_')
        {
            hasUnderscore = true;
            continue;
        }
        if (c < 'A' || c > 'Z') uppercaseCode = false;
    }
    if (uppercaseCode && hasUnderscore && !CanonicalReasonCode(value))
        return "redacted";
    return SafeLabelValueCharacters(value) ? value : Hex64(Fnv1a(value));
}

std::string RuntimeTelemetry::BoundedLabelFor(const std::string& labelName,
                                               const std::string& value)
{
    if (!SafeLabelName(labelName) || HasSensitiveNamePart(labelName))
        return "redacted";
    if (value.empty()) return "none";

    // Typed labels are retained only when they belong to a finite vocabulary.
    // Unknown values are deliberately collapsed rather than hashed, because a
    // hash of a short identifier (for example, an account number) remains
    // vulnerable to dictionary attacks.
    if (labelName == "reason_code" || labelName == "tool" ||
        labelName == "event_type" || labelName == "kind" || labelName == "decision" ||
        labelName == "status" || labelName == "operation" ||
        labelName == "outcome" || labelName == "state" ||
        labelName == "environment" || labelName == "venue" ||
        labelName == "series")
    {
        if (!CanonicalValueForName(labelName, value)) return "redacted";
        if (labelName == "status" || labelName == "venue" ||
            labelName == "environment")
            return LowerAscii(value);
        return value;
    }
    // Do not provide a permissive fallback for an unknown label name.  New
    // telemetry dimensions must be added to the finite vocabulary above so
    // their privacy/cardinality properties are reviewed explicitly.
    return "redacted";
}

std::string RuntimeTelemetry::Key(const std::string& metric,
                        const std::string& labels)
{
    if (!SafeMetricName(metric) || HasSensitiveMetricPart(metric))
        return std::string();
    const std::string sanitizedLabels = SanitizeLabels(labels);
    return sanitizedLabels.empty() ? metric :
        metric + '{' + sanitizedLabels + '}';
}

void RuntimeTelemetry::IncrementKey(const std::string& metric,
                          const std::string& boundedLabels)
{
    const std::string key = Key(metric, boundedLabels);
    if (key.empty()) return;
    std::lock_guard<std::mutex> lock(m_mutex);
    std::map<std::string, std::uint64_t>::iterator found = m_counters.find(key);
    if (found == m_counters.end())
    {
        if (m_counters.size() + m_gauges.size() + m_histograms.size() >=
  kMaximumSeries)
        {
  ++m_droppedSeries;
  return;
        }
        found = m_counters.insert(std::make_pair(key, 0)).first;
    }
    if (found->second != std::numeric_limits<std::uint64_t>::max())
        ++found->second;
}

void RuntimeTelemetry::SetGaugeKey(const std::string& metric,
                         double value,
                         const std::string& boundedLabels)
{
    if (!std::isfinite(value)) return;
    const std::string key = Key(metric, boundedLabels);
    if (key.empty()) return;
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_gauges.find(key) == m_gauges.end() &&
        m_counters.size() + m_gauges.size() + m_histograms.size() >=
  kMaximumSeries)
    {
        ++m_droppedSeries;
        return;
    }
    m_gauges[key] = value;
}

void RuntimeTelemetry::ObserveLatencyKey(const std::string& metric,
                               std::uint64_t micros,
                               const std::string& boundedLabels)
{
    const std::string key = Key(metric, boundedLabels);
    if (key.empty()) return;
    std::lock_guard<std::mutex> lock(m_mutex);
    std::map<std::string, RuntimeHistogramSnapshot>::iterator found =
        m_histograms.find(key);
    if (found == m_histograms.end())
    {
        if (m_counters.size() + m_gauges.size() + m_histograms.size() >=
  kMaximumSeries)
        {
  ++m_droppedSeries;
  return;
        }
        found = m_histograms.insert(
  std::make_pair(key, RuntimeHistogramSnapshot())).first;
    }
    RuntimeHistogramSnapshot& sample = found->second;
    if (sample.count != std::numeric_limits<std::uint64_t>::max()) ++sample.count;
    sample.totalMicros =
        micros > std::numeric_limits<std::uint64_t>::max() - sample.totalMicros ?
        std::numeric_limits<std::uint64_t>::max() : sample.totalMicros + micros;
    sample.maxMicros = std::max(sample.maxMicros, micros);
    for (std::size_t i = 0; i < sample.buckets.size(); ++i)
        if (micros <= kLatencyBuckets[i] &&
  sample.buckets[i] != std::numeric_limits<std::uint64_t>::max())
  ++sample.buckets[i];
}

std::string RuntimeTelemetry::SnapshotJson() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    std::ostringstream out;
    // Telemetry snapshots are JSON emitted at an authority boundary.  A
    // hosting process may have installed a comma-decimal locale; imbuing the
    // classic locale keeps gauge values valid and byte-stable regardless of
    // that process-global setting.
    out.imbue(std::locale::classic());
    out << "{\"schema\":\"hepta.runtime-metrics.v1\",\"counters\":{";
    bool first = true;
    for (std::map<std::string, std::uint64_t>::const_iterator it =
   m_counters.begin(); it != m_counters.end(); ++it)
    {
        if (!first) out << ',';
        first = false;
        out << '"' << EscapeJson(it->first) << "\":" << it->second;
    }
    out << "},\"gauges\":{";
    first = true;
    for (std::map<std::string, double>::const_iterator it =
   m_gauges.begin(); it != m_gauges.end(); ++it)
    {
        if (!first) out << ',';
        first = false;
        out << '"' << EscapeJson(it->first) << "\":"
  << std::setprecision(17) << it->second;
    }
    out << "},\"histograms\":{";
    first = true;
    for (std::map<std::string, RuntimeHistogramSnapshot>::const_iterator it =
   m_histograms.begin(); it != m_histograms.end(); ++it)
    {
        if (!first) out << ',';
        first = false;
        out << '"' << EscapeJson(it->first) << "\":{\"count\":"
  << it->second.count << ",\"total_us\":" << it->second.totalMicros
  << ",\"max_us\":" << it->second.maxMicros << ",\"buckets\":[";
        for (std::size_t i = 0; i < it->second.buckets.size(); ++i)
        {
  if (i != 0) out << ',';
  out << it->second.buckets[i];
        }
        out << "]}";
    }
    out << "},\"dropped_series\":" << m_droppedSeries << '}';
    return out.str();
}

std::size_t RuntimeTelemetry::SeriesCount() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_counters.size() + m_gauges.size() + m_histograms.size();
}

void RuntimeTelemetry::ResetForTests()
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_counters.clear();
    m_gauges.clear();
    m_histograms.clear();
    m_droppedSeries = 0;
}

RuntimeLatencyScope::RuntimeLatencyScope(
    const std::string& metric,
    const std::string& labelName,
    const std::string& labelValue)
    : m_metric(metric),
      m_labels(labelName.empty() ? std::string() :
          labelName + '=' + RuntimeTelemetry::BoundedLabelFor(
              labelName, labelValue)),
      m_started(std::chrono::steady_clock::now())
{
}

RuntimeLatencyScope::~RuntimeLatencyScope()
{
    const std::chrono::steady_clock::duration elapsed =
        std::chrono::steady_clock::now() - m_started;
    const std::uint64_t micros = static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::microseconds>(elapsed).count());
    RuntimeTelemetry::Global().ObserveLatencyKey(m_metric, micros, m_labels);
}

void RuntimeRecordRiskDecision(bool allowed, const std::string& reasonCode)
{
    RuntimeTelemetry::Global().IncrementKey(
        "hepta_risk_decisions_total",
        Labels2("decision", allowed ? "allow" : "reject",
      "reason_code", reasonCode.empty() ? "RISK_REASON_MISSING" : reasonCode));
}

void RuntimeRecordToolOutcome(const std::string& tool,
                    int status,
                    const std::string& reasonCode)
{
    const std::string normalizedStatus = StatusName(status);
    RuntimeTelemetry::Global().IncrementKey(
        "hepta_tool_calls_total",
        Labels3("tool", tool, "status", normalizedStatus,
      "reason_code", reasonCode.empty() ? "none" : reasonCode));
    if (status == 1 || Contains(reasonCode, "SESSION") ||
        Contains(reasonCode, "PEER_") || Contains(reasonCode, "CAPABILITY"))
        RuntimeTelemetry::Global().IncrementKey(
  "hepta_session_rejections_total",
  Labels1("reason_code", reasonCode.empty() ?
      "SESSION_REJECTION_UNCLASSIFIED" : reasonCode));
}

void RuntimeRecordJournalEvent(const std::string& eventType,
                     const std::string& status,
                     const std::string& reasonCode)
{
    RuntimeTelemetry::Global().IncrementKey(
        "hepta_execution_events_total", Labels1("event_type", eventType));
    if (Contains(eventType, "send_attempt"))
    {
        std::string operation = Contains(eventType, "cancel") ? "cancel" :
  (Contains(eventType, "flatten") ? "flatten" : "place");
        RuntimeTelemetry::Global().IncrementKey(
  "hepta_venue_sends_total",
  Labels2("operation", operation, "status", "attempt"));
    }
    if (!status.empty())
        RuntimeTelemetry::Global().IncrementKey(
  "hepta_execution_commands_total",
  Labels2("status", status, "reason_code",
          reasonCode.empty() ? "none" : reasonCode));
    if (Contains(eventType, "projection_failed") ||
        Contains(eventType, "state_break"))
        RuntimeTelemetry::Global().IncrementKey(
  "hepta_state_breaks_total", Labels1("kind", eventType));
    if (Contains(eventType, "reconcile") ||
        Contains(eventType, "projection_resolved") ||
        Contains(eventType, "command_resolved"))
        RuntimeRecordReconcile(eventType, status.empty() ? "observed" : status,
                     reasonCode);
}

void RuntimeRecordJournalFailure(const std::string& reasonCode)
{
    RuntimeTelemetry::Global().IncrementKey(
        "hepta_oms_journal_failures_total",
        Labels1("reason_code", reasonCode.empty() ?
  "OMS_FAILURE_UNCLASSIFIED" : reasonCode));
}

void RuntimeRecordReconcile(const std::string& operation,
                  const std::string& outcome,
                  const std::string& reasonCode)
{
    RuntimeTelemetry::Global().IncrementKey(
        "hepta_reconcile_runs_total",
        Labels3("operation", operation, "outcome", outcome,
      "reason_code", reasonCode.empty() ? "none" : reasonCode));
}

void RuntimeRecordKillSwitch(const std::string& state)
{
    RuntimeTelemetry::Global().IncrementKey(
        "hepta_kill_switch_transitions_total", Labels1("state", state));
}
