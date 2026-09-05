#include "trading_tool_registry.h"
#include "trading_tool_wire_contract.h"
#include "../intent/authoritative_decision_snapshot.h"
#include "../intent/bounded_json.h"

#include <cmath>
#include <cctype>
#include <exception>
#include <algorithm>
#include <chrono>
#include <iomanip>
#include <limits>
#include <mutex>
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <locale>
#include <sstream>

namespace {

const char* kReadResultSchema =
    "{\"type\":\"object\",\"additionalProperties\":true}";

const char* const kWatchSnapshotCapabilities[] = {
    "system.read",
    "market.read",
    "account.read",
    "portfolio.read",
    "orders.read",
    "risk.read"
};

const char* const kWatchSnapshotDescriptorTools[] = {
    "system.get_health",
    "account.get_summary",
    "portfolio.list_positions",
    "orders.list",
    "risk.get_limits",
    "market.get_quote"
};

const char* const kWatchSnapshotReadTools[] = {
    "account.get_summary",
    "portfolio.list_positions",
    "orders.list",
    "risk.get_limits",
    "market.get_quote",
    "system.get_health"
};

// A zero reference price is the canonical "not supplied" sentinel for
// ordinary raw-order calls and is also a valid zero slippage bound for target
// intents.  Keep discovery aligned with the wire and MCP validators
// (minimum, not exclusiveMinimum).
const char* kPlaceInputSchema =
    "{\"type\":\"object\",\"required\":[\"instrument\",\"side\",\"quantity\",\"order_type\",\"tif\",\"expires_at_ms\",\"preview_permit\"],"
    "\"properties\":{\"instrument\":{\"type\":\"string\"},\"side\":{\"enum\":[\"BUY\",\"SELL\"]},"
    "\"quantity\":{\"type\":\"number\",\"exclusiveMinimum\":0},\"order_type\":{\"enum\":[\"MKT\",\"LMT\"]},"
    "\"tif\":{\"enum\":[\"DAY\"]},\"limit_price\":{\"type\":\"number\",\"exclusiveMinimum\":0},"
    "\"reference_price\":{\"type\":\"number\",\"minimum\":0},\"expires_at_ms\":{\"type\":\"integer\"},"
    "\"symbol\":{\"type\":\"string\"},\"currency\":{\"type\":\"string\"},"
    "\"sec_type\":{\"type\":\"string\"},\"exchange\":{\"type\":\"string\"},"
    "\"preview_permit\":{\"type\":\"string\",\"minLength\":71,\"maxLength\":71}},\"additionalProperties\":false}";

const char* kPreviewInputSchema =
    "{\"type\":\"object\",\"required\":[\"instrument\",\"side\",\"quantity\",\"order_type\",\"tif\",\"expires_at_ms\"],"
    "\"properties\":{\"instrument\":{\"type\":\"string\"},\"side\":{\"enum\":[\"BUY\",\"SELL\"]},"
    "\"quantity\":{\"type\":\"number\",\"exclusiveMinimum\":0},\"order_type\":{\"enum\":[\"MKT\",\"LMT\"]},"
    "\"tif\":{\"enum\":[\"DAY\"]},\"limit_price\":{\"type\":\"number\",\"exclusiveMinimum\":0},"
    "\"reference_price\":{\"type\":\"number\",\"minimum\":0},\"expires_at_ms\":{\"type\":\"integer\"},"
    "\"symbol\":{\"type\":\"string\"},\"currency\":{\"type\":\"string\"},"
    "\"sec_type\":{\"type\":\"string\"},\"exchange\":{\"type\":\"string\"}},\"additionalProperties\":false}";

const char* kCancelInputSchema =
    "{\"type\":\"object\",\"required\":[\"order_id\"],\"additionalProperties\":false}";

const char* kExecutionResultSchema =
    "{\"type\":\"object\",\"required\":[\"status\",\"command_id\",\"order_id\"],\"additionalProperties\":false}";

const char* kCommandStatusInputSchema =
    "{\"type\":\"object\",\"required\":[\"command_id\"],"
    "\"properties\":{\"command_id\":{\"type\":\"string\",\"minLength\":8,\"maxLength\":128}},"
    "\"additionalProperties\":false}";

const char* kCommandStatusResultSchema =
    "{\"type\":\"object\",\"required\":[\"authoritative\",\"command_id\",\"command_status\","
    "\"order_id\",\"reason_code\",\"execution_service_epoch\","
    "\"execution_service_fencing_generation\"],\"properties\":{"
    "\"authoritative\":{\"const\":true},\"command_id\":{\"type\":\"string\"},"
    "\"command_status\":{\"enum\":[\"accepted\",\"rejected\",\"uncertain\"]},"
    "\"order_id\":{\"type\":\"integer\"},\"reason_code\":{\"type\":\"string\"},"
    "\"execution_service_epoch\":{\"type\":\"string\"},"
    "\"execution_service_fencing_generation\":{\"type\":\"integer\",\"minimum\":1}},"
    "\"additionalProperties\":false}";

std::string EscapeJson(const std::string& value)
{
    static const char hex[] = "0123456789abcdef";
    std::string escaped;
    escaped.reserve(value.size());
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const unsigned char c = static_cast<unsigned char>(*it);
        if (c == '"') escaped += "\\\"";
        else if (c == '\\') escaped += "\\\\";
        else if (c == '\b') escaped += "\\b";
        else if (c == '\f') escaped += "\\f";
        else if (c == '\n') escaped += "\\n";
        else if (c == '\r') escaped += "\\r";
        else if (c == '\t') escaped += "\\t";
        else if (c < 0x20)
        {
            // Escape every remaining JSON control byte, including NUL, with
            // fixed lowercase ASCII hex.  Do not emit a raw control or rely
            // on locale-sensitive stream formatting.
            escaped += "\\u00";
            escaped.push_back(hex[(c >> 4) & 0x0f]);
            escaped.push_back(hex[c & 0x0f]);
        }
        else escaped.push_back(static_cast<char>(c));
    }
    return escaped;
}

template <typename T>
std::string CanonicalNumber(T value)
{
    // Preview payloads are JSON authority output.  Keep formatting stable if
    // an embedding process changes its global locale, and collapse signed
    // zero so the payload cannot carry two spellings of one target/price.
    if (value == 0) return "0";
    std::ostringstream output;
    output.imbue(std::locale::classic());
    output << std::setprecision(17) << value;
    return output.str();
}

std::string DescriptorJson(const TradingToolDescriptor& descriptor)
{
    std::ostringstream out;
    // Discovery is wire data; keep integer formatting independent of the
    // embedding process's global locale.
    out.imbue(std::locale::classic());
    out << "{\"name\":\"" << EscapeJson(descriptor.name)
        << "\",\"description\":\"" << EscapeJson(descriptor.description)
        << "\",\"required_capability\":\"" << EscapeJson(descriptor.requiredCapability)
        << "\",\"effect\":\"" << (descriptor.effect == TradingToolEffect::Trade ? "trade" : "read")
        << "\",\"timeout_ms\":" << descriptor.timeoutMs
        << ",\"schema_hash\":\"" << TradingToolRegistry::DescriptorSchemaHash(descriptor) << "\""
        << ",\"input_schema\":" << descriptor.inputSchema
        << ",\"result_schema\":" << descriptor.resultSchema << "}";
    return out.str();
}

std::string Sha256(const std::string& value)
{
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int length = 0;
    EVP_MD_CTX* context = EVP_MD_CTX_new();
    if (context == nullptr) return std::string();
    const bool ok = EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1 &&
        EVP_DigestUpdate(context, value.data(), value.size()) == 1 &&
        EVP_DigestFinal_ex(context, digest, &length) == 1;
    EVP_MD_CTX_free(context);
    if (!ok) return std::string();
    std::ostringstream out;
    out.imbue(std::locale::classic());
    out << "sha256:" << std::hex << std::setfill('0');
    for (unsigned int i = 0; i < length; ++i)
        out << std::setw(2) << static_cast<unsigned int>(digest[i]);
    return out.str();
}

void AppendSnapshotFingerprintField(std::string& out,
                                    const char* name,
                                    const std::string& value)
{
    out.append(name);
    out.push_back('=');
    out.append(std::to_string(value.size()));
    out.push_back(':');
    out.append(value);
    out.push_back(';');
}

void AppendCanonicalJsonString(std::string& out, const std::string& value)
{
    static const char hex[] = "0123456789abcdef";
    out.push_back('"');
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const unsigned char c = static_cast<unsigned char>(*it);
        if (c == '"') out.append("\\\"");
        else if (c == '\\') out.append("\\\\");
        else if (c == '\b') out.append("\\b");
        else if (c == '\f') out.append("\\f");
        else if (c == '\n') out.append("\\n");
        else if (c == '\r') out.append("\\r");
        else if (c == '\t') out.append("\\t");
        else if (c < 0x20)
        {
            out.append("\\u00");
            out.push_back(hex[(c >> 4) & 0x0f]);
            out.push_back(hex[c & 0x0f]);
        }
        else out.push_back(static_cast<char>(c));
    }
    out.push_back('"');
}

bool AppendCanonicalJson(const BoundedJsonValue& value, std::string& out)
{
    if (value.IsNull())
    {
        out.append("null");
        return true;
    }
    if (value.IsBoolean())
    {
        bool boolean = false;
        if (!value.Boolean(boolean)) return false;
        out.append(boolean ? "true" : "false");
        return true;
    }
    if (value.IsNumber())
    {
        double number = 0.0;
        if (!value.Number(number) || !std::isfinite(number)) return false;
        const std::string& lexical = value.NumberText();
        if (lexical.empty()) return false;

        // Keep the validated lexical token instead of formatting the binary64
        // convenience value.  A double round-trip aliases distinct authority
        // integers (for example 9007199254740992 and 9007199254740993),
        // which would let two different snapshots share a permit fingerprint.
        // The parser already enforces JSON number grammar and finite range, so
        // retaining the token is deterministic and lossless.  Normalize every
        // signed/decimal zero to one spelling for stable fingerprints.
        bool allZero = true;
        for (std::string::const_iterator it = lexical.begin();
             it != lexical.end(); ++it)
        {
            if (*it == 'e' || *it == 'E') break;
            if (*it >= '1' && *it <= '9')
            {
                allZero = false;
                break;
            }
        }
        if (allZero) out.push_back('0');
        else out.append(lexical);
        return true;
    }
    if (value.IsString())
    {
        std::string stringValue;
        if (!value.String(stringValue)) return false;
        AppendCanonicalJsonString(out, stringValue);
        return true;
    }
    if (value.IsArray())
    {
        out.push_back('[');
        const std::vector<BoundedJsonValue>& values = value.Array();
        for (std::size_t i = 0; i < values.size(); ++i)
        {
            if (i != 0) out.push_back(',');
            if (!AppendCanonicalJson(values[i], out)) return false;
        }
        out.push_back(']');
        return true;
    }
    if (value.IsObject())
    {
        out.push_back('{');
        bool first = true;
        const std::map<std::string, BoundedJsonValue>& fields = value.Object();
        for (std::map<std::string, BoundedJsonValue>::const_iterator it =
                 fields.begin(); it != fields.end(); ++it)
        {
            if (!first) out.push_back(',');
            first = false;
            AppendCanonicalJsonString(out, it->first);
            out.push_back(':');
            if (!AppendCanonicalJson(it->second, out)) return false;
        }
        out.push_back('}');
        return true;
    }
    return false;
}

std::string CanonicalJson(const std::string& raw)
{
    BoundedJsonValue value;
    std::string reason;
    if (!ParseBoundedJson(raw, value, reason, 1024u * 1024u))
        return std::string();
    std::string canonical;
    if (!AppendCanonicalJson(value, canonical)) return std::string();
    return canonical;
}

std::string SnapshotPayloadFingerprint(
    const AuthoritativeDecisionSnapshotPayloads& payloads)
{
    std::string canonical;
    const std::string values[] = {
        CanonicalJson(payloads.healthBefore),
        CanonicalJson(payloads.healthAfter),
        CanonicalJson(payloads.quote),
        CanonicalJson(payloads.account),
        CanonicalJson(payloads.positions),
        CanonicalJson(payloads.orders),
        CanonicalJson(payloads.riskLimits)};
    static const char* names[] = {
        "health_before", "health_after", "quote", "account",
        "positions", "orders", "risk_limits"};
    for (std::size_t i = 0; i < sizeof(values) / sizeof(values[0]); ++i)
        AppendSnapshotFingerprintField(canonical, names[i], values[i]);
    return Sha256(canonical);
}

bool IsCanonicalReasonCode(const std::string& value)
{
    if (value.empty() || value.size() > 128) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const char c = value[i];
        if ((c < 'A' || c > 'Z') && (c < '0' || c > '9') && c != '_')
            return false;
    }
    return value[0] >= 'A' && value[0] <= 'Z';
}

// Callback diagnostics are untrusted input at the registry boundary. Keep
// ordinary human-readable diagnostics for compatibility, but never retain an
// unbounded (or NUL-containing) string that could inflate an in-process
// result or be echoed by a transport adapter. The wire server applies a
// second UTF-8/JSON validation pass before sending.
std::string BoundedCallbackDetail(const std::string& value,
                                  const char* fallback)
{
    if (value.size() > 1024u ||
        value.find('\0') != std::string::npos ||
        value.find('/') != std::string::npos ||
        value.find('\\') != std::string::npos)
        return fallback;
    // Reject raw C0/C1/DEL controls without mistaking UTF-8 continuation
    // bytes for standalone C1 controls.  The native wire codec repeats this
    // validation, but embedded registry callers do not necessarily traverse
    // that codec before observing a result.
    for (std::size_t offset = 0; offset < value.size();)
    {
        const unsigned char first =
            static_cast<unsigned char>(value[offset]);
        if (first < 0x20u || first == 0x7fu ||
            (first >= 0x80u && first <= 0x9fu))
            return fallback;
        if (first < 0x80u)
        {
            ++offset;
            continue;
        }
        std::size_t continuationCount = 0;
        if (first >= 0xc2u && first <= 0xdfu)
            continuationCount = 1;
        else if (first >= 0xe0u && first <= 0xefu)
            continuationCount = 2;
        else if (first >= 0xf0u && first <= 0xf4u)
            continuationCount = 3;
        else
            return fallback;
        if (value.size() - offset <= continuationCount) return fallback;
        const unsigned char second =
            static_cast<unsigned char>(value[offset + 1]);
        if ((first == 0xe0u && second < 0xa0u) ||
            (first == 0xedu && second >= 0xa0u) ||
            (first == 0xf0u && second < 0x90u) ||
            (first == 0xf4u && second > 0x8fu))
            return fallback;
        std::uint32_t codepoint = first &
            (continuationCount == 1 ? 0x1fu :
             continuationCount == 2 ? 0x0fu : 0x07u);
        for (std::size_t i = 1; i <= continuationCount; ++i)
        {
            const unsigned char continuation =
                static_cast<unsigned char>(value[offset + i]);
            if (continuation < 0x80u || continuation > 0xbfu)
                return fallback;
            codepoint = (codepoint << 6) | (continuation & 0x3fu);
        }
        if (codepoint < 0x20u || codepoint == 0x7fu ||
            (codepoint >= 0x80u && codepoint <= 0x9fu))
            return fallback;
        offset += continuationCount + 1u;
    }
    // Callback diagnostics are useful when they are ordinary prose, but
    // identifiers that look like credentials/paths must never be echoed to an
    // Agent.  Keep this deliberately small and case-insensitive; canonical
    // machine reason codes are handled separately and do not reach here.
    std::string lower;
    lower.reserve(value.size());
    for (std::string::const_iterator it = value.begin(); it != value.end(); ++it)
    {
        const char c = *it;
        lower.push_back(c >= 'A' && c <= 'Z' ?
            static_cast<char>(c - 'A' + 'a') : c);
    }
    const char* const sensitive[] = {
        "secret", "credential", "password", "bearer", "token", "private key",
        "api_key", "apikey", "uri", "file", "path", "exception",
        "what()", "stack trace", "errno", "not found", "failed"};
    for (const char* marker : sensitive)
        if (lower.find(marker) != std::string::npos)
            return fallback;
    return value;
}

std::int64_t EpochMs()
{
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}

std::string RandomOpaque(const char* prefix)
{
    unsigned char bytes[32];
    if (RAND_bytes(bytes, sizeof(bytes)) != 1) return std::string();
    std::ostringstream out;
    out.imbue(std::locale::classic());
    out << prefix << std::hex << std::setfill('0');
    for (std::size_t i = 0; i < sizeof(bytes); ++i)
        out << std::setw(2) << static_cast<unsigned int>(bytes[i]);
    return out.str();
}

bool JsonStringField(const std::string& json,
           const std::string& key,
           std::string& value)
{
    BoundedJsonValue root;
    std::string reason;
    if (!ParseBoundedJson(json, root, reason, 1024u * 1024u) || !root.IsObject())
        return false;
    const BoundedJsonValue* field = root.Find(key);
    return field != nullptr && field->String(value);
}

bool JsonObjectHasField(const std::string& json, const std::string& key)
{
    BoundedJsonValue root;
    std::string reason;
    if (!ParseBoundedJson(json, root, reason, 1024u * 1024u) ||
        !root.IsObject())
        return false;
    return root.Find(key) != nullptr;
}

bool JsonLongField(const std::string& json,
         const std::string& key,
         std::int64_t& value)
{
    BoundedJsonValue root;
    std::string reason;
    if (!ParseBoundedJson(json, root, reason, 1024u * 1024u) || !root.IsObject())
        return false;
    const BoundedJsonValue* field = root.Find(key);
    double number = 0.0;
    if (field == nullptr || !field->Number(number) || !std::isfinite(number) ||
        std::floor(number) != number ||
        // BoundedJson stores JSON numbers as doubles.  Refuse values outside
        // the exactly representable integer range instead of allowing a
        // rounded INT64 boundary to reach a narrowing cast.
        number < -9007199254740991.0 ||
        number > 9007199254740991.0)
        return false;
    value = static_cast<std::int64_t>(number);
    return true;
}

// Every successful Agent-facing read is transported as the typed result
// envelope, whose payload is an object (or the explicit null sentinel).  A
// callback is an authority-side extension point, but it is still a trust
// boundary: do not let malformed JSON, duplicate keys, non-finite values or a
// scalar payload escape into the wire response and make the client interpret
// a partial result.  The bounded parser also keeps callback-controlled output
// within the same byte/depth/node limits used by decision snapshots.
bool ValidReadPayload(const std::string& payload)
{
    if (payload.empty()) return true; // encoded as the protocol's null value
    BoundedJsonValue value;
    std::string reason;
    return ParseBoundedJson(
               payload, value, reason,
               TradingToolWireLimits::MaximumResultEnvelopeBytes()) &&
        value.IsObject();
}

bool IsCanonicalExecutionPreviewPermit(const std::string& permit)
{
    if (permit.size() != 71 || permit.compare(0, 7, "sha256:") != 0)
        return false;
    for (std::size_t i = 7; i < permit.size(); ++i)
    {
        const unsigned char c = static_cast<unsigned char>(permit[i]);
        if (!((c >= '0' && c <= '9') ||
              (c >= 'a' && c <= 'f')))
            return false;
    }
    return true;
}

bool IsCanonicalMutationCommandId(const std::string& value)
{
    if (value.size() < 8 || value.size() > 128) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        // Do not use locale-dependent ctype predicates here: a callback
        // response is an untrusted authority boundary and must accept only
        // the protocol's explicit ASCII identifier alphabet.
        const bool asciiAlphaNumeric =
            (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
            (c >= '0' && c <= '9');
        if (!asciiAlphaNumeric && c != '.' && c != '_' && c != ':' &&
            c != '-')
            return false;
    }
    return true;
}

bool NearlyEqualIntent(double left, double right)
{
    // Idempotency and permit revalidation compare normalized request/state
    // values, not display values.  A tolerance would alias two distinct
    // payloads (and let a changed target or snapshot reuse a permit).  The
    // call/snapshot validators reject non-finite numbers before this helper;
    // retain an explicit finite check here as a defensive fail-closed guard.
    return std::isfinite(left) && std::isfinite(right) && left == right;
}

bool SameDecisionSnapshot(const TargetPositionDecisionSnapshot& left,
                const TargetPositionDecisionSnapshot& right)
{
    return left.agentId == right.agentId &&
        left.sessionId == right.sessionId &&
        left.account == right.account &&
        left.executionDomain == right.executionDomain &&
        left.executionServiceEpoch == right.executionServiceEpoch &&
        left.fencingGeneration == right.fencingGeneration &&
        left.collectionWatermark == right.collectionWatermark &&
        left.eventWatermark == right.eventWatermark &&
        left.snapshotWatermark == right.snapshotWatermark &&
        left.instrument == right.instrument &&
        left.quoteObservedAtMs == right.quoteObservedAtMs &&
        NearlyEqualIntent(left.bid, right.bid) &&
        NearlyEqualIntent(left.ask, right.ask) &&
        NearlyEqualIntent(left.currentPosition, right.currentPosition);
}

bool SameTargetPlan(const TargetPositionExecutionPlan& left,
          const TargetPositionExecutionPlan& right)
{
    return left.noOp == right.noOp && left.side == right.side &&
        left.orderType == right.orderType &&
        left.timeInForce == right.timeInForce &&
        NearlyEqualIntent(left.quantity, right.quantity) &&
        NearlyEqualIntent(left.referencePrice, right.referencePrice) &&
        NearlyEqualIntent(left.limitPrice, right.limitPrice);
}

std::string TargetOwnerKey(const TradingToolSession& session)
{
    // Bind the local permit/replay namespace to every server-owned identity
    // component.  Agent/session ids alone are insufficient if a restored
    // session is ever re-bound to a different strategy, account or execution
    // domain.
    std::string key;
    const std::string components[] = {
        session.executionContext.agentId,
        session.executionContext.sessionId,
        session.executionContext.strategy,
        session.executionContext.account,
        session.executionContext.executionDomain,
        session.executionContext.venue};
    for (std::size_t i = 0;
         i < sizeof(components) / sizeof(components[0]); ++i)
    {
        key.append(std::to_string(components[i].size()));
        key.push_back(':');
        key.append(components[i]);
    }
    return key;
}

std::string TargetApplyReplayKey(const TradingToolSession& session)
{
    std::string key = TargetOwnerKey(session);
    key.append(std::to_string(session.executionContext.toolCallId.size()));
    key.push_back(':');
    key.append(session.executionContext.toolCallId);
    return key;
}

// Only the normalized target request participates in idempotency.  The
// target preview permit is a one-time credential and is intentionally omitted
// so an accepted command can be replayed after the credential was consumed or
// an IPC retry reconstructed the request envelope.
bool SameTargetApplyRequest(const TradingToolCall& left,
                            const TradingToolCall& right)
{
    return left.name == right.name &&
        left.instrument == right.instrument &&
        left.expiresAtMs == right.expiresAtMs &&
        NearlyEqualIntent(left.ibOrder.totalQuantity,
                          right.ibOrder.totalQuantity) &&
        NearlyEqualIntent(left.referencePrice, right.referencePrice);
}

TradingToolResult ReplayTargetApplyResult(const TradingToolResult& stored)
{
    TradingToolResult result = stored;
    if (result.status == TradingToolCallStatus::Ok)
    {
        result.status = TradingToolCallStatus::Duplicate;
        result.reasonCode = "DUPLICATE_TOOL_CALL";
        result.detail = "previous_status=accepted";
    }
    return result;
}

// `Ok`, `Duplicate` and `Uncertain` all mean that the request crossed the
// registry/Execution authority boundary: an accepted command may already have
// reached the venue, while an uncertain command must be recovered from the
// durable Execution ledger.  A plain `Rejected`/`Error` is still a pre-commit
// failure at this boundary, so its target permit remains available for the
// exact retry.  The Execution server itself consumes its raw permit at its
// own commit point; this distinction prevents a registry validation/lease
// failure from needlessly stranding the Agent-facing credential.
bool TargetApplyCrossedCommit(const TradingToolResult& result)
{
    return result.status == TradingToolCallStatus::Ok ||
        result.status == TradingToolCallStatus::Duplicate ||
        result.status == TradingToolCallStatus::Uncertain;
}

// A mutation authority call is a commit-point boundary. If the implementation
// throws after accepting the request (for example while an IPC transport is
// being torn down), the caller cannot know whether the venue observed it.
// Convert every exception to the same durable UNCERTAIN outcome instead of
// letting it unwind through the tool server and accidentally allowing a retry
// to issue a second command.
ExecutionCommandResult AuthorityExceptionResult(
    const AgentExecutionContext& context)
{
    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Uncertain;
    result.commandId = context.toolCallId;
    result.reasonCode = "EXECUTION_AUTHORITY_EXCEPTION";
    result.detail = "execution authority outcome is uncertain";
    return result;
}

} // namespace

TradingToolRegistry::TradingToolRegistry(ExecutionAuthority& execution,
                                         const TradingToolReadCallbacks& readCallbacks,
                                         const TradingToolTradeCallbacks& tradeCallbacks)
    : m_execution(execution), m_readCallbacks(readCallbacks), m_tradeCallbacks(tradeCallbacks)
{
    RegisterDefaults();
}

void TradingToolRegistry::RevokeTargetPermitsForOwner(
    const TradingToolSession& owner) const
{
    const std::string ownerKey = TargetOwnerKey(owner);
    std::lock_guard<std::mutex> lock(m_targetPreviewMutex);
    for (std::unordered_map<std::string, TargetPreviewRecord>::iterator it =
             m_targetPreviews.begin(); it != m_targetPreviews.end();)
    {
        if (it->second.ownerKey == ownerKey)
            it = m_targetPreviews.erase(it);
        else
            ++it;
    }
    for (std::unordered_map<std::string,
             TargetApplyReplayRecord>::iterator it =
             m_targetApplyReplays.begin();
         it != m_targetApplyReplays.end();)
    {
        if (it->second.ownerKey == ownerKey)
            it = m_targetApplyReplays.erase(it);
        else
            ++it;
    }
}

void TradingToolRegistry::RevokeTargetPermitsForIdentity(
    const std::string& agentId, const std::string& sessionId) const
{
    if (agentId.empty() || sessionId.empty()) return;
    std::lock_guard<std::mutex> lock(m_targetPreviewMutex);
    for (std::unordered_map<std::string, TargetPreviewRecord>::iterator it =
             m_targetPreviews.begin(); it != m_targetPreviews.end();)
    {
        if (it->second.ownerAgentId == agentId &&
            it->second.ownerSessionId == sessionId)
            it = m_targetPreviews.erase(it);
        else
            ++it;
    }
    for (std::unordered_map<std::string,
             TargetApplyReplayRecord>::iterator it =
             m_targetApplyReplays.begin();
         it != m_targetApplyReplays.end();)
    {
        if (it->second.ownerAgentId == agentId &&
            it->second.ownerSessionId == sessionId)
            it = m_targetApplyReplays.erase(it);
        else
            ++it;
    }
}

const char* TradingToolRegistry::StatusName(TradingToolCallStatus status)
{
    return TradingToolWireContract::StatusName(status);
}

void TradingToolRegistry::RegisterReadTool(const std::string& name,
                                           const std::string& description,
                                           const std::string& capability,
                                           int timeoutMs,
                                           const std::string& inputSchema,
                                           const ReadHandler& handler)
{
    TradingToolDescriptor descriptor;
    descriptor.name = name;
    descriptor.description = description;
    descriptor.requiredCapability = capability;
    descriptor.effect = TradingToolEffect::Read;
    descriptor.timeoutMs = timeoutMs;
    descriptor.inputSchema = inputSchema;
    descriptor.resultSchema = kReadResultSchema;
    m_descriptors[name] = descriptor;
    m_readHandlers[name] = handler;
}

void TradingToolRegistry::RegisterDefaults()
{
    RegisterReadTool("system.tools.list", "List versioned tools visible to this session.",
                     "system.read", 1000, "{\"type\":\"object\",\"additionalProperties\":false}",
                     ReadHandler());
    RegisterReadTool("system.tools.describe", "Describe one versioned tool visible to this session.",
                     "system.read", 1000,
                     "{\"type\":\"object\",\"required\":[\"tool_name\"],\"properties\":{\"tool_name\":{\"type\":\"string\"}},\"additionalProperties\":false}",
                     ReadHandler());
    RegisterReadTool("system.cancel_request", "Cancel one pending request owned by this session.",
                     "system.read", 1000,
                     "{\"type\":\"object\",\"required\":[\"tool_call_id\"],\"properties\":{\"tool_call_id\":{\"type\":\"string\"}},\"additionalProperties\":false}",
                     ReadHandler());
    RegisterReadTool("market.get_quote", "Read the latest normalized quote for one instrument.",
                     "market.read", 8000,
                     "{\"type\":\"object\",\"required\":[\"instrument\"],"
                     "\"properties\":{\"instrument\":{\"type\":\"string\"}},"
                     "\"additionalProperties\":false}",
                     m_readCallbacks.marketGetQuote);
    RegisterReadTool("account.get_summary", "Read the bound account summary.",
                     "account.read", 8000, "{\"type\":\"object\",\"additionalProperties\":false}",
                     m_readCallbacks.accountGetSummary);
    RegisterReadTool("portfolio.list_positions", "Read authoritative positions visible to this session.",
                     "portfolio.read", 8000, "{\"type\":\"object\",\"additionalProperties\":false}",
                     m_readCallbacks.portfolioListPositions);
    RegisterReadTool("orders.list", "Read active and recent orders visible to this session.",
                     "orders.read", 8000, "{\"type\":\"object\",\"additionalProperties\":false}",
                     m_readCallbacks.ordersList);
    RegisterReadTool("execution.get_command_status",
                     "Read one execution command result owned by this Agent session.",
                     "orders.read", 8000, kCommandStatusInputSchema,
                     m_readCallbacks.executionGetCommandStatus);
    m_descriptors["execution.get_command_status"].resultSchema =
        kCommandStatusResultSchema;
    RegisterReadTool("risk.get_limits", "Read immutable limits bound to this Agent session.",
                     "risk.read", 8000, "{\"type\":\"object\",\"additionalProperties\":false}",
                     m_readCallbacks.riskGetLimits);
    RegisterReadTool("risk.preview_order", "Evaluate an operator order without broker side effects.",
                     "operator.risk.preview", 16000, kPreviewInputSchema, m_readCallbacks.riskPreviewOrder);
    RegisterReadTool("events.wait", "Wait for the next bounded order, fill, reject or market event.",
                     "events.read", 36000,
                     "{\"type\":\"object\",\"properties\":{\"after_sequence\":{\"type\":\"integer\"},\"timeout_ms\":{\"type\":\"integer\"}},\"additionalProperties\":false}",
                     m_readCallbacks.eventsWait);
    RegisterReadTool("system.get_health", "Read authoritative recovery and contract subscription health.",
                     "system.read", 6000, "{\"type\":\"object\",\"additionalProperties\":false}",
                     m_readCallbacks.systemGetHealth);
    RegisterReadTool(
        "watch.get_snapshot",
        "Read one fixed WATCH catalog, descriptor and authoritative state set.",
        "system.read", 8000,
        "{\"type\":\"object\",\"required\":[\"instrument\"],"
        "\"properties\":{\"instrument\":{\"type\":\"string\"}},"
        "\"additionalProperties\":false}",
        ReadHandler());

    RegisterReadTool(
        "decision.get_snapshot",
        "Read one generation-consistent authoritative decision snapshot.",
        "system.read", 16000,
        "{\"type\":\"object\",\"required\":[\"instrument\"],"
        "\"properties\":{\"instrument\":{\"type\":\"string\"}},"
        "\"additionalProperties\":false}", ReadHandler());
    RegisterReadTool(
        "intent.preview_target_position",
        "Compile a bounded target position from authoritative state and issue one opaque permit.",
        "risk.read", 16000,
        "{\"type\":\"object\",\"required\":[\"instrument\",\"quantity\","
        "\"reference_price\",\"expires_at_ms\"],\"properties\":{"
        "\"instrument\":{\"type\":\"string\"},"
        "\"quantity\":{\"type\":\"number\",\"description\":\"signed target position\"},"
        "\"reference_price\":{\"type\":\"number\",\"minimum\":0,\"maximum\":1000,"
        "\"description\":\"maximum slippage in basis points\"},"
        "\"expires_at_ms\":{\"type\":\"integer\"}},\"additionalProperties\":false}",
        ReadHandler());

    TradingToolDescriptor applyTarget;
    applyTarget.name = "intent.apply_target_position";
    applyTarget.description =
        "Atomically consume a target-position permit and dispatch its exact service-previewed plan. The command_id must equal the Execution-issued mutation_command_id returned by the matching preview.";
    applyTarget.requiredCapability = "intent.apply";
    applyTarget.effect = TradingToolEffect::Trade;
    applyTarget.timeoutMs = 16000;
    applyTarget.inputSchema =
        "{\"type\":\"object\",\"required\":[\"instrument\",\"quantity\","
        "\"reference_price\",\"expires_at_ms\",\"preview_permit\"],"
        "\"properties\":{\"instrument\":{\"type\":\"string\"},"
        "\"quantity\":{\"type\":\"number\"},"
        "\"reference_price\":{\"type\":\"number\",\"minimum\":0,\"maximum\":1000},"
        "\"expires_at_ms\":{\"type\":\"integer\"},"
        "\"preview_permit\":{\"type\":\"string\",\"minLength\":71,\"maxLength\":71}},"
        "\"additionalProperties\":false}";
    applyTarget.resultSchema = kExecutionResultSchema;
    m_descriptors[applyTarget.name] = applyTarget;

    TradingToolDescriptor place;
    place.name = "trade.place_order";
    place.description = "Submit a real order through the Hepta C++ execution authority.";
    place.requiredCapability = "operator.trade.place";
    place.effect = TradingToolEffect::Trade;
    // Trade dispatch first performs the Gateway's explicit liveness/readiness
    // probe, then re-resolves the mutation/event identity pair at the
    // ExecutionAuthority boundary before the actual command RPC.
    place.timeoutMs = 16000;
    place.inputSchema = kPlaceInputSchema;
    place.resultSchema = kExecutionResultSchema;
    m_descriptors[place.name] = place;

    TradingToolDescriptor cancel;
    cancel.name = "trade.cancel_order";
    cancel.description = "Cancel an order owned by this Agent session.";
    cancel.requiredCapability = "trade.cancel";
    cancel.effect = TradingToolEffect::Trade;
    cancel.timeoutMs = 16000;
    cancel.inputSchema = kCancelInputSchema;
    cancel.resultSchema = kExecutionResultSchema;
    m_descriptors[cancel.name] = cancel;

    // Flatten is not equivalent to a client-side position read followed by a
    // place call: that construction has a state-of-check/state-of-use race and
    // can increase exposure.  Publish the descriptor only when the concrete
    // Execution composition installs an authoritative reduce-only handler.
    if (m_tradeCallbacks.flattenPosition &&
        m_readCallbacks.riskPreviewFlatten)
    {
        RegisterReadTool(
            "risk.preview_flatten",
            "Preview an authoritative reduce-only close and issue one permit.",
            "trade.flatten", 16000,
            "{\"type\":\"object\",\"required\":[\"instrument\"],"
            "\"properties\":{\"instrument\":{\"type\":\"string\"}},"
            "\"additionalProperties\":false}",
            m_readCallbacks.riskPreviewFlatten);
        TradingToolDescriptor flatten;
        flatten.name = "trade.flatten_position";
        flatten.description =
            "Close the Agent-visible position without increasing absolute exposure.";
        flatten.requiredCapability = "trade.flatten";
        flatten.effect = TradingToolEffect::Trade;
        flatten.timeoutMs = 16000;
        flatten.inputSchema =
            "{\"type\":\"object\",\"required\":[\"instrument\","
            "\"preview_permit\"],\"properties\":{"
            "\"instrument\":{\"type\":\"string\"},"
            "\"preview_permit\":{\"type\":\"string\","
            "\"minLength\":71,\"maxLength\":71}},"
            "\"additionalProperties\":false}";
        flatten.resultSchema = kExecutionResultSchema;
        m_descriptors[flatten.name] = flatten;
    }
}

bool TradingToolRegistry::ValidateCallSemantics(const TradingToolCall& call,
                                                std::string& reasonCode,
                                                std::string& detail)
{
    return TradingToolWireContract::ValidateCallSemantics(
        call, reasonCode, detail);
}

bool TradingToolRegistry::EnvironmentAllows(const TradingToolSession& session,
                                            const TradingToolDescriptor& descriptor,
                                            std::string& reasonCode)
{
    if (session.environment != "WATCH" && session.environment != "PAPER")
    {
        reasonCode = "INVALID_SESSION_ENVIRONMENT";
        return false;
    }
    if (descriptor.name == "watch.get_snapshot" &&
        session.environment != "WATCH")
    {
        reasonCode = "WATCH_SNAPSHOT_ENVIRONMENT_REQUIRED";
        return false;
    }
    if (descriptor.name == "execution.get_command_status" &&
        session.environment == "WATCH")
    {
        reasonCode = "WATCH_COMMAND_STATUS_UNAVAILABLE";
        return false;
    }
    if (descriptor.effect == TradingToolEffect::Trade && session.environment == "WATCH")
    {
        reasonCode = "WATCH_SESSION_CANNOT_TRADE";
        return false;
    }
    reasonCode.clear();
    return true;
}

bool TradingToolRegistry::HasCapability(const TradingToolSession& session,
                                        const std::string& capability) const
{
    return session.capabilities.find(capability) != session.capabilities.end();
}

bool TradingToolRegistry::HasRequiredCapabilities(
    const TradingToolSession& session,
    const TradingToolDescriptor& descriptor,
    std::string& missingCapability) const
{
    if (descriptor.name != "watch.get_snapshot")
    {
        if (HasCapability(session, descriptor.requiredCapability))
        {
            missingCapability.clear();
            return true;
        }
        missingCapability = descriptor.requiredCapability;
        return false;
    }
    for (std::size_t i = 0;
         i < sizeof(kWatchSnapshotCapabilities) /
             sizeof(kWatchSnapshotCapabilities[0]); ++i)
    {
        if (HasCapability(session, kWatchSnapshotCapabilities[i])) continue;
        missingCapability = kWatchSnapshotCapabilities[i];
        return false;
    }
    missingCapability.clear();
    return true;
}

std::vector<TradingToolDescriptor> TradingToolRegistry::ListTools(const TradingToolSession& session) const
{
    std::vector<TradingToolDescriptor> result;
    for (std::unordered_map<std::string, TradingToolDescriptor>::const_iterator it = m_descriptors.begin();
         it != m_descriptors.end(); ++it)
    {
        std::string environmentReason;
        std::string missingCapability;
        if (HasRequiredCapabilities(session, it->second, missingCapability) &&
            EnvironmentAllows(session, it->second, environmentReason)) result.push_back(it->second);
    }
    return result;
}

bool TradingToolRegistry::GetDescriptor(const std::string& name, TradingToolDescriptor& out) const
{
    const std::unordered_map<std::string, TradingToolDescriptor>::const_iterator it = m_descriptors.find(name);
    if (it == m_descriptors.end()) return false;
    out = it->second;
    return true;
}

std::string TradingToolRegistry::DescriptorSchemaHash(const TradingToolDescriptor& descriptor)
{
    std::ostringstream canonical;
    canonical.imbue(std::locale::classic());
    canonical << descriptor.name << '\0' << descriptor.description << '\0'
              << descriptor.requiredCapability << '\0'
              << (descriptor.effect == TradingToolEffect::Trade ? "trade" : "read") << '\0'
              << descriptor.timeoutMs << '\0' << descriptor.inputSchema << '\0'
              << descriptor.resultSchema;
    return Sha256(canonical.str());
}

unsigned int TradingToolRegistry::DiscoverySchemaVersion()
{
    return 2;
}

std::string TradingToolRegistry::CatalogSchemaHash(const TradingToolSession& session) const
{
    std::vector<TradingToolDescriptor> tools = ListTools(session);
    std::sort(tools.begin(), tools.end(), [](const TradingToolDescriptor& left,
                                             const TradingToolDescriptor& right) {
        return left.name < right.name;
    });
    std::ostringstream canonical;
    canonical.imbue(std::locale::classic());
    for (std::size_t i = 0; i < tools.size(); ++i)
        canonical << tools[i].name << '=' << DescriptorSchemaHash(tools[i]) << '\n';
    return Sha256(canonical.str());
}

TradingToolResult TradingToolRegistry::InvokeRead(const TradingToolSession& session,
                                                  const TradingToolDescriptor& descriptor,
                                                  const TradingToolCall& call) const
{
    if (call.name == "watch.get_snapshot")
        return InvokeWatchSnapshot(session, call);
    TradingToolResult result;
    result.toolName = call.name;
    const std::unordered_map<std::string, ReadHandler>::const_iterator handlerIt = m_readHandlers.find(call.name);
    if (handlerIt == m_readHandlers.end() || !handlerIt->second)
    {
        result.status = TradingToolCallStatus::Error;
        result.reasonCode = "TOOL_HANDLER_UNAVAILABLE";
        result.detail = descriptor.name + " is registered but not wired to live C++ state";
        return result;
    }

    std::string payload;
    std::string reason;
    bool ok = false;
    bool handlerThrew = false;
    try
    {
        ok = handlerIt->second(session, call, payload, reason);
    }
    catch (const std::exception&)
    {
        handlerThrew = true;
        // Exception text is implementation/venue data and may contain
        // filesystem paths, credentials or control bytes.  It must never
        // become an Agent-facing result detail; preserve only a stable code.
        reason = "READ_TOOL_EXCEPTION";
    }
    catch (...)
    {
        handlerThrew = true;
        reason = "READ_TOOL_EXCEPTION";
    }

    result.status = ok ? TradingToolCallStatus::Ok : TradingToolCallStatus::Error;
    const bool canonicalFailure = !ok && IsCanonicalReasonCode(reason);
    result.reasonCode = ok ? "" :
        (canonicalFailure ? reason : "READ_TOOL_FAILED");
    result.detail = ok ? std::string() :
        (handlerThrew ? "read tool handler threw" :
            (canonicalFailure ? std::string() :
                BoundedCallbackDetail(reason, "read tool handler failed")));
    // A callback that reports failure may have populated its output before
    // returning. Never expose that partial/possibly sensitive payload as an
    // error response; only successful callbacks are allowed to carry data.
    result.payloadJson = ok ? payload : std::string();
    if (ok && !ValidReadPayload(payload))
    {
        result.status = TradingToolCallStatus::Error;
        result.reasonCode = "READ_TOOL_PAYLOAD_INVALID";
        result.detail.clear();
        result.payloadJson.clear();
    }
    return result;
}

TradingToolResult TradingToolRegistry::InvokeWatchSnapshot(
    const TradingToolSession& session,
    const TradingToolCall& call) const
{
    TradingToolResult result;
    result.toolName = call.name;
    if (session.environment != "WATCH")
    {
        result.status = TradingToolCallStatus::PermissionDenied;
        result.reasonCode = "WATCH_SNAPSHOT_ENVIRONMENT_REQUIRED";
        return result;
    }
    if (session.visibleInstruments.find(call.instrument) ==
        session.visibleInstruments.end())
    {
        result.status = TradingToolCallStatus::PermissionDenied;
        result.reasonCode = "INSTRUMENT_NOT_ALLOWED";
        return result;
    }

    TradingToolCall catalogCall;
    catalogCall.name = "system.tools.list";
    const TradingToolResult catalog = InvokeDiscovery(session, catalogCall);
    if (catalog.status != TradingToolCallStatus::Ok)
    {
        result.status = catalog.status;
        result.reasonCode = catalog.reasonCode.empty() ?
            "WATCH_SNAPSHOT_DISCOVERY_FAILED" : catalog.reasonCode;
        return result;
    }

    std::vector<TradingToolResult> descriptors;
    descriptors.reserve(sizeof(kWatchSnapshotDescriptorTools) /
                        sizeof(kWatchSnapshotDescriptorTools[0]));
    for (std::size_t i = 0;
         i < sizeof(kWatchSnapshotDescriptorTools) /
             sizeof(kWatchSnapshotDescriptorTools[0]); ++i)
    {
        TradingToolCall describeCall;
        describeCall.name = "system.tools.describe";
        describeCall.targetToolName = kWatchSnapshotDescriptorTools[i];
        const TradingToolResult described =
            InvokeDiscovery(session, describeCall);
        if (described.status != TradingToolCallStatus::Ok)
        {
            result.status = described.status;
            result.reasonCode = described.reasonCode.empty() ?
                "WATCH_SNAPSHOT_DISCOVERY_FAILED" : described.reasonCode;
            return result;
        }
        descriptors.push_back(described);
    }

    std::vector<TradingToolResult> reads;
    std::vector<long long> readFinishedAtMs;
    reads.reserve(sizeof(kWatchSnapshotReadTools) /
                  sizeof(kWatchSnapshotReadTools[0]));
    readFinishedAtMs.reserve(sizeof(kWatchSnapshotReadTools) /
                            sizeof(kWatchSnapshotReadTools[0]));
    for (std::size_t i = 0;
         i < sizeof(kWatchSnapshotReadTools) /
             sizeof(kWatchSnapshotReadTools[0]); ++i)
    {
        TradingToolCall readCall;
        readCall.name = kWatchSnapshotReadTools[i];
        if (readCall.name == "market.get_quote")
            readCall.instrument = call.instrument;
        TradingToolDescriptor readDescriptor;
        if (!GetDescriptor(readCall.name, readDescriptor))
        {
            result.status = TradingToolCallStatus::Error;
            result.reasonCode = "WATCH_SNAPSHOT_TOOL_UNAVAILABLE";
            return result;
        }
        const TradingToolResult read =
            InvokeRead(session, readDescriptor, readCall);
        if (read.status != TradingToolCallStatus::Ok)
        {
            result.status = read.status;
            result.reasonCode = read.reasonCode.empty() ?
                "WATCH_SNAPSHOT_SUBREAD_FAILED" : read.reasonCode;
            result.payloadJson.clear();
            return result;
        }
        reads.push_back(read);
        readFinishedAtMs.push_back(
            std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::system_clock::now().time_since_epoch()).count());
    }

    std::ostringstream output;
    output.imbue(std::locale::classic());
    output << "{\"schema\":\"hepta.watch-read-set.v1\",\"catalog\":"
           << catalog.payloadJson << ",\"descriptors\":{";
    for (std::size_t i = 0; i < descriptors.size(); ++i)
    {
        if (i != 0) output << ',';
        output << '\"' << kWatchSnapshotDescriptorTools[i] << "\":"
               << descriptors[i].payloadJson;
    }
    output << "},\"reads\":{";
    for (std::size_t i = 0; i < reads.size(); ++i)
    {
        if (i != 0) output << ',';
        output << '\"' << kWatchSnapshotReadTools[i] << "\":"
               << reads[i].payloadJson;
    }
    output << "},\"read_finished_at_ms\":{";
    for (std::size_t i = 0; i < readFinishedAtMs.size(); ++i)
    {
        if (i != 0) output << ',';
        output << '\"' << kWatchSnapshotReadTools[i] << "\":"
               << readFinishedAtMs[i];
    }
    output << "}}";
    result.status = TradingToolCallStatus::Ok;
    result.payloadJson = output.str();
    if (TradingToolWireContract::EncodedResultEnvelopeSize(result) >
        TradingToolWireLimits::MaximumResultEnvelopeBytes())
    {
        result.status = TradingToolCallStatus::Error;
        result.reasonCode = "WATCH_SNAPSHOT_RESPONSE_TOO_LARGE";
        result.payloadJson.clear();
    }
    return result;
}

bool TradingToolRegistry::BuildDecisionSnapshot(
    const TradingToolSession& session,
    const TradingToolCall& call,
    TargetPositionDecisionSnapshot& snapshot,
    std::string& outputJson,
    std::string& reasonCode,
    std::string& detail,
    std::int64_t collectionStartedAtMsFloor) const
{
    if (call.instrument.empty() ||
        session.visibleInstruments.find(call.instrument) ==
  session.visibleInstruments.end())
    {
        reasonCode = "INSTRUMENT_NOT_ALLOWED";
        return false;
    }
    struct Read
    {
        const char* name;
        ReadHandler handler;
        std::string payload;
    };
    Read reads[] = {
        {"system.get_health", m_readCallbacks.systemGetHealth, std::string()},
        {"market.get_quote", m_readCallbacks.marketGetQuote, std::string()},
        {"account.get_summary", m_readCallbacks.accountGetSummary, std::string()},
        {"portfolio.list_positions", m_readCallbacks.portfolioListPositions, std::string()},
        {"orders.list", m_readCallbacks.ordersList, std::string()},
        {"risk.get_limits", m_readCallbacks.riskGetLimits, std::string()},
        {"system.get_health", m_readCallbacks.systemGetHealth, std::string()}
    };
    const std::string generationKey = TargetOwnerKey(session) +
        std::to_string(call.instrument.size()) + ":" + call.instrument;
    const std::int64_t started = EpochMs();
    for (std::size_t i = 0; i < sizeof(reads) / sizeof(reads[0]); ++i)
    {
        if (!reads[i].handler)
        {
  reasonCode = "DECISION_SNAPSHOT_HANDLER_UNAVAILABLE";
  detail = reads[i].name;
  return false;
        }
        TradingToolCall readCall;
        readCall.name = reads[i].name;
        if (readCall.name == "market.get_quote")
  readCall.instrument = call.instrument;
        std::string readReason;
        bool ok = false;
        try
        {
  ok = reads[i].handler(
      session, readCall, reads[i].payload, readReason);
        }
        catch (const std::exception&)
        {
            // Callback exception text is process-local diagnostic data, not
            // an Agent-facing reason.  Keep the wire result stable and avoid
            // leaking venue/library paths, credentials, or implementation
            // details through the snapshot/preview boundary.
            readReason = "DECISION_SNAPSHOT_SUBREAD_EXCEPTION";
        }
        catch (...)
        {
            readReason = "DECISION_SNAPSHOT_SUBREAD_EXCEPTION";
        }
        if (!ok)
        {
  reasonCode = IsCanonicalReasonCode(readReason) ? readReason :
      "DECISION_SNAPSHOT_SUBREAD_FAILED";
  detail = IsCanonicalReasonCode(readReason) ? std::string() :
      // Snapshot collection is an Agent-facing compound boundary.  Do not
      // echo callback prose (which can contain venue paths or credentials);
      // machine-readable authority codes are retained above, while all
      // human/exception diagnostics collapse to one bounded phrase.
      "decision snapshot sub-read failed";
  return false;
        }
    }
    const std::int64_t completed = EpochMs();
    AuthoritativeDecisionSnapshotPayloads payloads;
    payloads.healthBefore = reads[0].payload;
    payloads.quote = reads[1].payload;
    payloads.account = reads[2].payload;
    payloads.positions = reads[3].payload;
    payloads.orders = reads[4].payload;
    payloads.riskLimits = reads[5].payload;
    payloads.healthAfter = reads[6].payload;
    const std::string fingerprint = SnapshotPayloadFingerprint(payloads);
    std::uint64_t watermark = 0;
    std::int64_t cachedQuoteObservedAtMs = 0;
    std::int64_t cachedCollectionStartedAtMs = 0;
    bool reusedGeneration = false;
    {
        std::lock_guard<std::mutex> lock(m_snapshotGenerationMutex);
        std::unordered_map<std::string, SnapshotGenerationRecord>::iterator
            existing = m_snapshotGenerations.find(generationKey);
        if (existing == m_snapshotGenerations.end() &&
            m_snapshotGenerations.size() >= 4096)
        {
            // Never evict the entry being evaluated.  A bounded eviction can
            // invalidate an old permit, but must not make the just-created
            // snapshot self-inconsistent before it is returned.
            m_snapshotGenerations.erase(m_snapshotGenerations.begin());
        }
        SnapshotGenerationRecord& generation =
            m_snapshotGenerations[generationKey];
        if (generation.generation != 0 &&
            generation.fingerprint == fingerprint)
        {
            reusedGeneration = true;
            cachedQuoteObservedAtMs = generation.quoteObservedAtMs;
            cachedCollectionStartedAtMs = generation.collectionStartedAtMs;
        }
        if (generation.generation == 0 || generation.fingerprint != fingerprint)
        {
            // A load followed by a store is not a unique counter under
            // concurrent snapshot requests: two owners can observe the same
            // value and issue the same generation. Reserve the next value
            // with a CAS loop while retaining the explicit exhaustion gate.
            std::uint64_t previous = m_snapshotWatermark.load();
            for (;;)
            {
                if (previous == std::numeric_limits<std::uint64_t>::max())
                {
                    reasonCode = "DECISION_SNAPSHOT_WATERMARK_EXHAUSTED";
                    detail = "authoritative snapshot generation counter exhausted";
                    return false;
                }
                const std::uint64_t candidate = previous + 1;
                if (m_snapshotWatermark.compare_exchange_weak(
                        previous, candidate))
                {
                    watermark = candidate;
                    break;
                }
            }
            generation.generation = watermark;
            generation.fingerprint = fingerprint;
            // A new payload fingerprint starts a fresh attestation domain;
            // never carry quote/window floors from the previous generation
            // into it, even if this collection later fails validation.
            generation.quoteObservedAtMs = 0;
            generation.collectionStartedAtMs = 0;
        }
        else
            watermark = generation.generation;

        // Bound retained identity state.  Eviction is fail-closed for an
        // outstanding permit (the next collection gets a new generation)
        // rather than allowing an unbounded per-session memory surface.
        // The pre-insertion eviction above keeps this map at the bounded
        // maximum without ever dropping the current owner/instrument entry.
    }
    // A permit apply re-read may intentionally use the same stable quote
    // identity captured by the original preview.  Its market timestamp can
    // therefore precede the *new* wall-clock read start even though it was
    // inside the original collection window.  The caller supplies that
    // previously attested collection start as a floor only for revalidation;
    // never move the window into the future or below a positive epoch.
    // Prefer the highest previously attested collection start.  This keeps a
    // stable generation's window monotonic even if a re-read happens after
    // the wall clock advances (or if the clock briefly moves backwards).  A
    // cached quote is only a fallback for legacy records that predate the
    // stored collection-start floor; when both floors exist, Build() below
    // must reject an inconsistent quote rather than silently moving the
    // attested window backwards.
    std::int64_t historicalStarted = 0;
    // An apply floor is meaningful only when this collection reuses the exact
    // generation that issued the permit.  If any component changed, the
    // generation was advanced above and borrowing the old window would make
    // the new payload appear attested to the prior collection.
    if (reusedGeneration && collectionStartedAtMsFloor > historicalStarted)
        historicalStarted = collectionStartedAtMsFloor;
    if (cachedCollectionStartedAtMs > historicalStarted)
        historicalStarted = cachedCollectionStartedAtMs;
    const std::int64_t effectiveStarted = historicalStarted > 0 ?
        historicalStarted :
        ((cachedQuoteObservedAtMs > 0 && cachedQuoteObservedAtMs < started) ?
            cachedQuoteObservedAtMs : started);
    const bool built = AuthoritativeDecisionSnapshotCodec::Build(
        session.executionContext.agentId,
        session.executionContext.sessionId,
        session.executionContext.account,
        session.executionContext.executionDomain,
        call.instrument,
        effectiveStarted,
        completed,
        watermark,
        payloads,
        snapshot,
        outputJson,
        reasonCode,
        detail);
    if (built)
    {
        std::lock_guard<std::mutex> lock(m_snapshotGenerationMutex);
        std::unordered_map<std::string, SnapshotGenerationRecord>::iterator
            generation = m_snapshotGenerations.find(generationKey);
        if (generation != m_snapshotGenerations.end() &&
            generation->second.generation == watermark &&
            generation->second.fingerprint == fingerprint)
        {
            generation->second.quoteObservedAtMs = snapshot.quoteObservedAtMs;
            generation->second.collectionStartedAtMs =
                snapshot.collectionStartedAtMs;
        }
    }
    return built;
}

TradingToolResult TradingToolRegistry::InvokeDecisionSnapshot(
    const TradingToolSession& session,
    const TradingToolCall& call) const
{
    TradingToolResult result;
    result.toolName = call.name;
    TargetPositionDecisionSnapshot snapshot;
    if (!BuildDecisionSnapshot(session, call, snapshot, result.payloadJson,
                     result.reasonCode, result.detail))
    {
        result.status = TradingToolCallStatus::Rejected;
        return result;
    }
    result.status = TradingToolCallStatus::Ok;
    return result;
}

TradingToolResult TradingToolRegistry::InvokeTargetPreview(
    const TradingToolSession& session,
    const TradingToolCall& call) const
{
    TradingToolResult result;
    result.toolName = call.name;
    const std::int64_t now = EpochMs();
    if (!std::isfinite(call.ibOrder.totalQuantity) ||
        !std::isfinite(call.referencePrice) || call.referencePrice < 0.0 ||
        call.referencePrice > 1000.0 || call.expiresAtMs <= now ||
        call.expiresAtMs > now + 60000)
    {
        result.status = TradingToolCallStatus::Rejected;
        result.reasonCode = "INTENT_REQUEST_INVALID";
        return result;
    }

    TargetPositionDecisionSnapshot snapshot;
    std::string snapshotJson;
    if (!BuildDecisionSnapshot(session, call, snapshot, snapshotJson,
                     result.reasonCode, result.detail))
    {
        result.status = TradingToolCallStatus::Rejected;
        return result;
    }

    // Snapshot/risk callbacks are authority calls and may take non-zero
    // time.  Re-read the clock immediately before compiling the plan so a
    // request that expired while those reads ran cannot receive a permit that
    // is already stale on return.
    const std::int64_t planNow = EpochMs();
    if (call.expiresAtMs <= planNow ||
        call.expiresAtMs > planNow + 60000)
    {
        result.status = TradingToolCallStatus::Rejected;
        result.reasonCode = "INTENT_REQUEST_INVALID";
        return result;
    }

    TargetPositionIntentRequest request;
    request.targetPosition = call.ibOrder.totalQuantity;
    request.maxSlippageBps = call.referencePrice;
    request.expiresAtMs = call.expiresAtMs;
    TargetPositionIntentPolicy policy;
    policy.version = "execution-preview-authority-v1";
    // Host-bound sessions carry the same per-order ceiling used by raw
    // placement.  Keep the unbounded fallback only for legacy trusted
    // in-process registry callers that predate the session field; network
    // callers always pass through TradingToolHost, which overwrites it from
    // the supervisor policy before dispatch.
    if (session.maxOrderQuantity != 0.0)
    {
        if (!std::isfinite(session.maxOrderQuantity) ||
            session.maxOrderQuantity <= 0.0)
        {
            result.status = TradingToolCallStatus::Rejected;
            result.reasonCode = "INTENT_SESSION_LIMIT_INVALID";
            return result;
        }
        policy.maxOrderQuantity = session.maxOrderQuantity;
    }
    else
        policy.maxOrderQuantity = 1000000000.0;
    policy.maxAbsoluteTargetPosition = 1000000000.0;
    policy.maxSlippageBps = 1000.0;
    policy.maxIntentLifetimeMs = 60000;
    TargetPositionExecutionPlan plan;
    if (!TargetPositionIntentContract::BuildPlan(
  snapshot, request, policy, planNow, plan,
  result.reasonCode, result.detail))
    {
        result.status = TradingToolCallStatus::Rejected;
        return result;
    }

    std::string rawPermit;
    std::string mutationCommandId;
    std::int64_t permitExpiry = call.expiresAtMs;
    if (!plan.noOp)
    {
        const std::unordered_map<std::string, InstrumentRef>::const_iterator contract =
  session.boundInstrumentContracts.find(call.instrument);
        if (contract == session.boundInstrumentContracts.end())
        {
  result.status = TradingToolCallStatus::Rejected;
  result.reasonCode = "INSTRUMENT_CONTRACT_NOT_BOUND";
  return result;
        }
        if (!m_readCallbacks.riskPreviewOrder)
        {
  result.status = TradingToolCallStatus::Error;
  result.reasonCode = "TARGET_PREVIEW_AUTHORITY_UNAVAILABLE";
  return result;
        }
        TradingToolCall raw;
        raw.name = "risk.preview_order";
        raw.instrument = call.instrument;
        raw.ibContract = contract->second;
        raw.ibOrder.action = plan.side;
        raw.ibOrder.orderType = plan.orderType;
        raw.ibOrder.totalQuantity = plan.quantity;
        raw.ibOrder.lmtPrice = plan.limitPrice;
        raw.timeInForce = plan.timeInForce;
        raw.referencePrice = plan.referencePrice;
        raw.expiresAtMs = call.expiresAtMs;
        std::string payload;
        std::string reason;
        bool riskPreviewOk = false;
        bool riskPreviewThrew = false;
        try
        {
            riskPreviewOk = m_readCallbacks.riskPreviewOrder(
                session, raw, payload, reason);
        }
        catch (const std::exception&)
        {
            riskPreviewThrew = true;
            reason = "TARGET_PREVIEW_AUTHORITY_EXCEPTION";
        }
        catch (...)
        {
            riskPreviewThrew = true;
            reason = "TARGET_PREVIEW_AUTHORITY_EXCEPTION";
        }
        if (!riskPreviewOk)
        {
  result.status = TradingToolCallStatus::Rejected;
  result.reasonCode = riskPreviewThrew ? "TARGET_PREVIEW_AUTHORITY_EXCEPTION" :
      (IsCanonicalReasonCode(reason) ? reason : "TARGET_PREVIEW_RISK_REJECTED");
  result.detail = riskPreviewThrew || IsCanonicalReasonCode(reason) ?
      std::string() : BoundedCallbackDetail(
          // Never expose callback prose at the target preview boundary.
          // Keep the helper call's bounded fallback semantics for source
          // compatibility, but pass a fixed literal rather than `reason`.
          "target-position risk preview failed",
          "target-position risk preview failed");
  payload.clear();
  return result;
        }
        // Accept the in-process legacy spelling or the Execution wire
        // spelling, but never silently choose one when an authority returns
        // both (or returns a malformed canonical field alongside a valid
        // alias).  Ambiguous bindings must fail closed at this boundary.
        const bool hasMutationCommandIdField =
            JsonObjectHasField(payload, "mutation_command_id");
        const bool hasCommandIdField =
            JsonObjectHasField(payload, "command_id");
        const char* mutationIdField = nullptr;
        if (hasMutationCommandIdField != hasCommandIdField)
            mutationIdField = hasMutationCommandIdField ?
                "mutation_command_id" : "command_id";
        const bool hasMutationId = mutationIdField != nullptr &&
            JsonStringField(payload, mutationIdField, mutationCommandId);
        // Execution's canonical response calls this binding
        // `permit_expires_at_ms`.  Keep the older `expires_at_ms` spelling as
        // a narrow compatibility alias for in-process authorities, but never
        // accept both spellings or silently ignore a malformed field.
        const bool hasCanonicalPermitExpiry =
            JsonObjectHasField(payload, "permit_expires_at_ms");
        const bool hasLegacyPermitExpiry =
            JsonObjectHasField(payload, "expires_at_ms");
        const char* permitExpiryField = nullptr;
        if (hasCanonicalPermitExpiry == hasLegacyPermitExpiry)
            permitExpiryField = nullptr;
        else
            permitExpiryField = hasCanonicalPermitExpiry ?
                "permit_expires_at_ms" : "expires_at_ms";
        // The callback is an authority boundary.  Require a canonical
        // server-issued permit and a bounded, explicitly present expiry; do
        // not fall back to the Agent request's expiry when the callback
        // omitted or malformed its own binding.
        if (!JsonStringField(payload, "preview_permit", rawPermit) ||
            !IsCanonicalExecutionPreviewPermit(rawPermit) ||
            !hasMutationId || !IsCanonicalMutationCommandId(mutationCommandId) ||
            permitExpiryField == nullptr ||
            !JsonLongField(payload, permitExpiryField, permitExpiry) ||
            permitExpiry <= planNow || permitExpiry > call.expiresAtMs)
        {
  result.status = TradingToolCallStatus::Error;
  result.reasonCode = "TARGET_PREVIEW_RESPONSE_INVALID";
  return result;
        }
    }
    else
    {
        mutationCommandId = RandomOpaque("intent-");
        rawPermit.clear();
        if (mutationCommandId.empty())
        {
  result.status = TradingToolCallStatus::Error;
  result.reasonCode = "TARGET_PREVIEW_RANDOM_FAILED";
            return result;
        }
    }

    // The authority callback above may itself consume time.  Recheck both
    // expiry bounds at the commit point so the returned target permit always
    // has a positive lifetime.
    const std::int64_t permitNow = EpochMs();
    if (call.expiresAtMs <= permitNow)
    {
        result.status = TradingToolCallStatus::Rejected;
        result.reasonCode = "INTENT_REQUEST_INVALID";
        return result;
    }
    if (permitExpiry <= permitNow)
    {
        result.status = plan.noOp ? TradingToolCallStatus::Rejected :
            TradingToolCallStatus::Error;
        result.reasonCode = plan.noOp ? "INTENT_REQUEST_INVALID" :
            "TARGET_PREVIEW_RESPONSE_INVALID";
        return result;
    }

    const std::string targetPermit = RandomOpaque("sha256:");
    if (targetPermit.empty())
    {
        result.status = TradingToolCallStatus::Error;
        result.reasonCode = "TARGET_PREVIEW_RANDOM_FAILED";
        return result;
    }
    TargetPreviewRecord record;
    record.ownerKey = TargetOwnerKey(session);
    record.ownerAgentId = session.executionContext.agentId;
    record.ownerSessionId = session.executionContext.sessionId;
    record.mutationCommandId = mutationCommandId;
    record.rawExecutionPermit = rawPermit;
    record.expiresAtMs = std::min(call.expiresAtMs, permitExpiry);
    record.steadyExpiresAt = std::chrono::steady_clock::now() +
        std::chrono::milliseconds(
            std::max<std::int64_t>(1, record.expiresAtMs - permitNow));
    record.snapshot = snapshot;
    record.request = request;
    record.policy = policy;
    record.plan = plan;
    {
        std::lock_guard<std::mutex> lock(m_targetPreviewMutex);
        const std::chrono::steady_clock::time_point steadyNow =
  std::chrono::steady_clock::now();
        for (std::unordered_map<std::string, TargetPreviewRecord>::iterator it =
       m_targetPreviews.begin(); it != m_targetPreviews.end();)
        {
  if (it->second.steadyExpiresAt <= steadyNow)
      it = m_targetPreviews.erase(it);
  else
      ++it;
        }
        // The Execution-issued mutation id is the idempotency namespace for
        // the eventual apply. A faulty or replaying preview authority must
        // not be able to issue two live target permits with the same owner
        // and command id: concurrent applies could otherwise both pass the
        // replay lookup before either result is cached. Reject the ambiguous
        // authority response while preserving all existing permits.
        for (std::unordered_map<std::string, TargetPreviewRecord>::const_iterator
                 it = m_targetPreviews.begin();
             it != m_targetPreviews.end(); ++it)
        {
            if (it->second.ownerKey == record.ownerKey &&
                it->second.mutationCommandId == record.mutationCommandId)
            {
                result.status = TradingToolCallStatus::Error;
                result.reasonCode = "TARGET_PREVIEW_MUTATION_ID_REUSED";
                result.detail =
                    "authority returned a mutation command id that is already active for this owner";
                return result;
            }
        }
        if (m_targetPreviews.size() >= 1024)
        {
  result.status = TradingToolCallStatus::Rejected;
  result.reasonCode = "TARGET_PREVIEW_CAPACITY_EXHAUSTED";
  return result;
        }
        m_targetPreviews[targetPermit] = record;
    }

    std::ostringstream payload;
    payload.imbue(std::locale::classic());
    payload << "{\"schema\":\"hepta.target-position-preview.v2\","
              "\"authoritative\":true,\"instrument\":\""
  << EscapeJson(call.instrument)
  << "\",\"target_position\":" << CanonicalNumber(request.targetPosition)
  << ",\"current_position\":" << CanonicalNumber(snapshot.currentPosition)
  << ",\"no_op\":" << (plan.noOp ? "true" : "false")
  << ",\"side\":\"" << EscapeJson(plan.side)
  << "\",\"quantity\":" << CanonicalNumber(plan.quantity)
  << ",\"reference_price\":" << CanonicalNumber(plan.referencePrice)
  << ",\"limit_price\":" << CanonicalNumber(plan.limitPrice)
  << ",\"preview_permit\":\"" << targetPermit
  << "\",\"mutation_command_id\":\""
  << EscapeJson(mutationCommandId)
  << "\",\"expires_at_ms\":" << CanonicalNumber(record.expiresAtMs)
  << ",\"decision_snapshot\":" << snapshotJson << '}';
    result.status = TradingToolCallStatus::Ok;
    result.payloadJson = payload.str();
    if (TradingToolWireContract::EncodedResultEnvelopeSize(result) >
        TradingToolWireLimits::MaximumResultEnvelopeBytes())
    {
        // Do not strand a one-time authority permit when the response cannot
        // be serialized within the wire envelope budget.  Remove only the
        // exact record just inserted; a (theoretical) random-key collision
        // or concurrent owner record must remain untouched.
        std::lock_guard<std::mutex> lock(m_targetPreviewMutex);
        const std::unordered_map<std::string, TargetPreviewRecord>::iterator
            inserted = m_targetPreviews.find(targetPermit);
        if (inserted != m_targetPreviews.end() &&
            inserted->second.ownerKey == record.ownerKey &&
            inserted->second.mutationCommandId == record.mutationCommandId)
            m_targetPreviews.erase(inserted);
        result.status = TradingToolCallStatus::Error;
        result.reasonCode = "TARGET_PREVIEW_RESPONSE_TOO_LARGE";
        result.payloadJson.clear();
        result.detail.clear();
    }
    return result;
}

TradingToolResult TradingToolRegistry::InvokeTargetApply(
    const TradingToolSession& session,
    const TradingToolCall& call) const
{
    TradingToolResult result;
    result.toolName = call.name;

    // Keep the target-position wire contract in front of the replay lookup.
    // A replay must never become a way to smuggle raw-order fields or an
    // unbounded permit through the registry.
    std::string semanticReason;
    std::string semanticDetail;
    if (!ValidateCallSemantics(call, semanticReason, semanticDetail))
    {
        result.status = TradingToolCallStatus::Rejected;
        result.reasonCode = semanticReason;
        result.detail = semanticDetail;
        return result;
    }
    // Replays are still authorization-sensitive: a session whose server-side
    // visibility scope no longer includes the instrument must not learn or
    // reuse an old result merely by presenting its command id.
    if (session.visibleInstruments.find(call.instrument) ==
        session.visibleInstruments.end())
    {
        result.status = TradingToolCallStatus::PermissionDenied;
        result.reasonCode = "INSTRUMENT_NOT_ALLOWED";
        return result;
    }

    const std::string ownerKey = TargetOwnerKey(session);
    const std::string replayKey = TargetApplyReplayKey(session);

    // A successful apply removes the one-time target permit, so check the
    // durable replay ledger before looking up that permit.  This is the
    // registry-side equivalent of the Execution Service's durable command
    // replay hook and makes an IPC retry deterministic.
    {
        std::lock_guard<std::mutex> lock(m_targetPreviewMutex);
        const std::chrono::steady_clock::time_point steadyNow =
            std::chrono::steady_clock::now();
        for (std::unordered_map<std::string,
                 TargetApplyReplayRecord>::iterator it =
                 m_targetApplyReplays.begin();
             it != m_targetApplyReplays.end();)
        {
            if (it->second.steadyExpiresAt <= steadyNow)
                it = m_targetApplyReplays.erase(it);
            else
                ++it;
        }
        const std::unordered_map<std::string,
            TargetApplyReplayRecord>::const_iterator replay =
            m_targetApplyReplays.find(replayKey);
        if (replay != m_targetApplyReplays.end())
        {
            if (!SameTargetApplyRequest(call, replay->second.call))
            {
                result.status = TradingToolCallStatus::Rejected;
                result.reasonCode = "IDEMPOTENCY_KEY_CONFLICT";
                result.detail =
                    "target apply command id was already used for a different normalized request";
                return result;
            }
            return ReplayTargetApplyResult(replay->second.result);
        }
    }

    TargetPreviewRecord record;
    {
        std::lock_guard<std::mutex> lock(m_targetPreviewMutex);
        const std::unordered_map<std::string, TargetPreviewRecord>::const_iterator found =
            m_targetPreviews.find(call.previewPermit);
        if (found == m_targetPreviews.end())
        {
            result.status = TradingToolCallStatus::Rejected;
            result.reasonCode = "TARGET_PREVIEW_PERMIT_UNKNOWN";
            return result;
        }
        if (found->second.applyInFlight)
        {
            // Do not let a concurrent retry race the authority call.  The
            // first caller owns the one-time transition; once it completes,
            // the replay ledger below returns the deterministic outcome.
            result.status = TradingToolCallStatus::Uncertain;
            result.reasonCode = "TARGET_APPLY_IN_FLIGHT";
            result.detail =
                "the bound target mutation is still being dispatched";
            return result;
        }
        record = found->second;
    }
    const std::int64_t now = EpochMs();
    if (record.ownerKey != TargetOwnerKey(session) ||
        record.mutationCommandId != session.executionContext.toolCallId)
    {
        result.status = TradingToolCallStatus::Rejected;
        result.reasonCode = "TARGET_PREVIEW_PERMIT_BINDING_MISMATCH";
        return result;
    }
    if (now >= record.expiresAtMs ||
        std::chrono::steady_clock::now() >= record.steadyExpiresAt)
    {
        result.status = TradingToolCallStatus::Rejected;
        result.reasonCode = "TARGET_PREVIEW_PERMIT_EXPIRED";
        return result;
    }
    if (call.instrument != record.snapshot.instrument ||
        call.expiresAtMs != record.request.expiresAtMs ||
        !NearlyEqualIntent(call.ibOrder.totalQuantity,
                 record.request.targetPosition) ||
        !NearlyEqualIntent(call.referencePrice,
                 record.request.maxSlippageBps))
    {
        result.status = TradingToolCallStatus::Rejected;
        result.reasonCode = "TARGET_PREVIEW_REQUEST_CHANGED";
        return result;
    }

    TargetPositionDecisionSnapshot current;
    std::string snapshotJson;
    if (!BuildDecisionSnapshot(session, call, current, snapshotJson,
                     result.reasonCode, result.detail,
                     record.snapshot.collectionStartedAtMs))
    {
        result.status = TradingToolCallStatus::Rejected;
        return result;
    }
    if (!SameDecisionSnapshot(record.snapshot, current))
    {
        result.status = TradingToolCallStatus::Rejected;
        result.reasonCode = "TARGET_PREVIEW_GENERATION_CHANGED";
        return result;
    }
    TargetPositionExecutionPlan currentPlan;
    if (!TargetPositionIntentContract::BuildPlan(
  current, record.request, record.policy, now, currentPlan,
  result.reasonCode, result.detail) ||
        !SameTargetPlan(record.plan, currentPlan))
    {
        result.status = TradingToolCallStatus::Rejected;
        if (result.reasonCode.empty())
  result.reasonCode = "TARGET_PREVIEW_PLAN_CHANGED";
        return result;
    }
    const bool noOp = record.plan.noOp;
    PlaceOrderCommand command;
    if (!noOp)
    {
        const std::unordered_map<std::string, InstrumentRef>::const_iterator contract =
            session.boundInstrumentContracts.find(call.instrument);
        if (contract == session.boundInstrumentContracts.end())
        {
            result.status = TradingToolCallStatus::Rejected;
            result.reasonCode = "INSTRUMENT_CONTRACT_NOT_BOUND";
            return result;
        }
        command.context = session.executionContext;
        command.contract = contract->second;
        command.order.action = record.plan.side;
        command.order.orderType = record.plan.orderType;
        command.order.totalQuantity = record.plan.quantity;
        command.order.lmtPrice = record.plan.limitPrice;
        command.instrument = call.instrument;
        command.timeInForce = record.plan.timeInForce;
        command.referencePrice = record.plan.referencePrice;
        command.expiresAtMs = record.request.expiresAtMs;
        command.previewPermit = record.rawExecutionPermit;
    }

    // All target permit validation is complete.  Serialize the one-time
    // transition against a concurrent apply.  Keep the record marked
    // in-flight until the authority reports whether its own commit point was
    // crossed; this lets a clear pre-dispatch rejection retry the same permit
    // without allowing a second concurrent dispatch.
    {
        std::lock_guard<std::mutex> lock(m_targetPreviewMutex);
        const std::unordered_map<std::string,
            TargetApplyReplayRecord>::const_iterator replay =
            m_targetApplyReplays.find(replayKey);
        if (replay != m_targetApplyReplays.end())
        {
            if (!SameTargetApplyRequest(call, replay->second.call))
            {
                result.status = TradingToolCallStatus::Rejected;
                result.reasonCode = "IDEMPOTENCY_KEY_CONFLICT";
                result.detail =
                    "target apply command id was already used for a different normalized request";
                return result;
            }
            return ReplayTargetApplyResult(replay->second.result);
        }
        const std::unordered_map<std::string, TargetPreviewRecord>::iterator found =
            m_targetPreviews.find(call.previewPermit);
        if (found == m_targetPreviews.end())
        {
            result.status = TradingToolCallStatus::Rejected;
            result.reasonCode = "TARGET_PREVIEW_PERMIT_UNKNOWN";
            return result;
        }
        if (found->second.ownerKey != record.ownerKey ||
            found->second.mutationCommandId != record.mutationCommandId)
        {
            result.status = TradingToolCallStatus::Rejected;
            result.reasonCode = "TARGET_PREVIEW_PERMIT_BINDING_MISMATCH";
            return result;
        }
        if (found->second.applyInFlight)
        {
            result.status = TradingToolCallStatus::Uncertain;
            result.reasonCode = "TARGET_APPLY_IN_FLIGHT";
            result.detail =
                "the bound target mutation is still being dispatched";
            return result;
        }
        // The first expiry check above protects the potentially expensive
        // snapshot/plan revalidation, but that work may itself consume the
        // permit's remaining lifetime. Revalidate at the serialized
        // one-time transition immediately before marking the record in
        // flight; otherwise a short-lived permit could cross the authority
        // boundary after it has expired.
        const std::int64_t finalNow = EpochMs();
        const std::chrono::steady_clock::time_point finalSteadyNow =
            std::chrono::steady_clock::now();
        if (found->second.expiresAtMs <= finalNow ||
            found->second.steadyExpiresAt <= finalSteadyNow)
        {
            result.status = TradingToolCallStatus::Rejected;
            result.reasonCode = "TARGET_PREVIEW_PERMIT_EXPIRED";
            return result;
        }
        found->second.applyInFlight = true;
    }

    if (noOp)
    {
        result.status = TradingToolCallStatus::Ok;
        result.reasonCode = "INTENT_NO_CHANGE";
    }
    else
    {
        ExecutionCommandResult execution;
        try
        {
            execution = m_execution.PlaceOrder(command);
        }
        catch (const std::exception&)
        {
            // The target permit has crossed the one-time boundary.  An
            // authority exception is therefore represented as an uncertain
            // durable outcome and cached for deterministic retries rather
            // than leaking an exception or returning permit-unknown.
            execution.status = ExecutionCommandStatus::Uncertain;
            execution.commandId = command.context.toolCallId;
            execution.reasonCode = "EXECUTION_AUTHORITY_EXCEPTION";
            execution.detail = "execution authority outcome is uncertain";
        }
        catch (...)
        {
            execution.status = ExecutionCommandStatus::Uncertain;
            execution.commandId = command.context.toolCallId;
            execution.reasonCode = "EXECUTION_AUTHORITY_EXCEPTION";
            execution.detail = "execution authority outcome is uncertain";
        }
        result = FromExecution(call.name, execution);
    }

    const bool crossedCommit = TargetApplyCrossedCommit(result);

    // Retain the result long enough for retries after the target permit has
    // been consumed.  The raw Execution authority remains the durable source
    // of truth across process restart; this bounded in-process ledger covers
    // the registry/IPC retry window and never contains the raw permit.  A
    // clear rejection/error is released back to the permit record instead of
    // being cached as a terminal replay, so a transient pre-dispatch gate can
    // be retried with the same command and credential.
    {
        std::lock_guard<std::mutex> lock(m_targetPreviewMutex);
        const std::unordered_map<std::string, TargetPreviewRecord>::iterator
            permit = m_targetPreviews.find(call.previewPermit);
        if (permit != m_targetPreviews.end() &&
            permit->second.ownerKey == ownerKey &&
            permit->second.mutationCommandId == record.mutationCommandId)
        {
            if (crossedCommit)
                m_targetPreviews.erase(permit);
            else
                permit->second.applyInFlight = false;
        }
        if (!crossedCommit)
            return result;
        const std::chrono::steady_clock::time_point steadyNow =
            std::chrono::steady_clock::now();
        for (std::unordered_map<std::string,
                 TargetApplyReplayRecord>::iterator it =
                 m_targetApplyReplays.begin();
             it != m_targetApplyReplays.end();)
        {
            if (it->second.steadyExpiresAt <= steadyNow)
                it = m_targetApplyReplays.erase(it);
            else
                ++it;
        }
        // Keep the ledger bounded even if a long-lived Agent rotates command
        // ids rapidly.  Eviction only affects the registry cache; a replay
        // that has reached Execution is still resolved by its durable store.
        static const std::size_t kMaxTargetApplyReplays = 2048;
        if (m_targetApplyReplays.size() >= kMaxTargetApplyReplays)
            m_targetApplyReplays.erase(m_targetApplyReplays.begin());
        TargetApplyReplayRecord replay;
        replay.ownerKey = ownerKey;
        replay.ownerAgentId = session.executionContext.agentId;
        replay.ownerSessionId = session.executionContext.sessionId;
        replay.call = call;
        // The target permit is a credential, not part of the normalized
        // idempotency payload.  Do not retain it in the replay cache after
        // the one-time transition has completed.
        replay.call.previewPermit.clear();
        replay.result = result;
        replay.steadyExpiresAt = steadyNow + std::chrono::hours(24);
        m_targetApplyReplays[replayKey] = replay;
    }
    return result;
}

TradingToolResult TradingToolRegistry::InvokeDiscovery(const TradingToolSession& session,
                                                       const TradingToolCall& call) const
{
    TradingToolResult result;
    result.status = TradingToolCallStatus::Ok;
    result.toolName = call.name;
    std::vector<TradingToolDescriptor> tools = ListTools(session);
    std::sort(tools.begin(), tools.end(), [](const TradingToolDescriptor& left,
                                             const TradingToolDescriptor& right) {
        return left.name < right.name;
    });

    std::ostringstream payload;
    payload.imbue(std::locale::classic());
    payload << "{\"protocol\":\"hepta.agent-tools\",\"protocol_version\":1,"
            << "\"protocol_min_version\":1,\"protocol_max_version\":1,"
            << "\"schema_version\":" << DiscoverySchemaVersion()
            << ",\"catalog_schema_hash\":\""
            << CatalogSchemaHash(session) << "\"";
    if (call.name == "system.tools.list")
    {
        payload << ",\"tools\":[";
        for (std::size_t i = 0; i < tools.size(); ++i)
        {
            if (i != 0) payload << ',';
            payload << DescriptorJson(tools[i]);
        }
        payload << "]}";
        result.payloadJson = payload.str();
        return result;
    }

    for (std::size_t i = 0; i < tools.size(); ++i)
    {
        if (tools[i].name == call.targetToolName)
        {
            payload << ",\"tool\":" << DescriptorJson(tools[i]) << "}";
            result.payloadJson = payload.str();
            return result;
        }
    }
    result.status = TradingToolCallStatus::InvalidTool;
    result.reasonCode = "TOOL_NOT_VISIBLE";
    result.detail = call.targetToolName;
    return result;
}

TradingToolResult TradingToolRegistry::FromExecution(const std::string& toolName,
                                                     const ExecutionCommandResult& execution)
{
    TradingToolResult result;
    result.toolName = toolName;
    result.orderId = execution.orderId;
    // Embedded callers can hand us an Execution result without traversing the
    // Unix service's final response sanitizer.  Apply the same stable reason
    // and bounded-detail policy here, while retaining ordinary human details
    // for compatibility with local callers.
    const bool canonicalReason = execution.reasonCode.empty() ||
        IsCanonicalReasonCode(execution.reasonCode);
    switch (execution.status)
    {
    case ExecutionCommandStatus::Accepted:
        result.status = TradingToolCallStatus::Ok;
        result.reasonCode = canonicalReason ? execution.reasonCode :
            "EXECUTION_AUTHORITY_RESPONSE_INVALID";
        // Accepted details are normally empty, but preserve a bounded
        // ordinary diagnostic for embedded compatibility. Sensitive/path or
        // malformed text collapses to an empty detail.
        result.detail = BoundedCallbackDetail(execution.detail, "");
        break;
    case ExecutionCommandStatus::Rejected:
        result.status = TradingToolCallStatus::Rejected;
        result.reasonCode = canonicalReason ? execution.reasonCode :
            "EXECUTION_REQUEST_REJECTED";
        if (result.reasonCode.empty())
            result.reasonCode = "EXECUTION_REQUEST_REJECTED";
        result.detail = BoundedCallbackDetail(
            execution.detail, "execution request was rejected");
        break;
    case ExecutionCommandStatus::Duplicate:
        result.status = TradingToolCallStatus::Duplicate;
        result.reasonCode = canonicalReason ? execution.reasonCode :
            "DUPLICATE_TOOL_CALL";
        if (result.reasonCode.empty()) result.reasonCode = "DUPLICATE_TOOL_CALL";
        result.detail = BoundedCallbackDetail(execution.detail,
            "duplicate tool call");
        break;
    case ExecutionCommandStatus::Uncertain:
        result.status = TradingToolCallStatus::Uncertain;
        result.reasonCode = canonicalReason ? execution.reasonCode :
            "EXECUTION_AUTHORITY_EXCEPTION";
        if (result.reasonCode.empty())
            result.reasonCode = "EXECUTION_AUTHORITY_EXCEPTION";
        // An uncertain mutation must remain a deterministic reconciliation
        // signal; never carry authority/venue prose through the embedded
        // result, even when it looks ordinary.
        result.detail = "execution authority outcome is uncertain";
        break;
    default:
        result.status = TradingToolCallStatus::Error;
        result.reasonCode = "EXECUTION_AUTHORITY_RESPONSE_INVALID";
        result.detail = "execution authority response was invalid";
        break;
    }
    return result;
}

TradingToolResult TradingToolRegistry::Invoke(const TradingToolSession& session,
                                              const TradingToolCall& call)
{
    TradingToolDescriptor descriptor;
    if (!GetDescriptor(call.name, descriptor))
    {
        TradingToolResult result;
        result.status = TradingToolCallStatus::InvalidTool;
        result.toolName = call.name;
        result.reasonCode = "UNKNOWN_TOOL";
        return result;
    }
    std::string missingCapability;
    if (!HasRequiredCapabilities(session, descriptor, missingCapability))
    {
        TradingToolResult result;
        result.status = TradingToolCallStatus::PermissionDenied;
        result.toolName = call.name;
        result.reasonCode = "CAPABILITY_REQUIRED";
        result.detail = missingCapability;
        return result;
    }
    std::string environmentReason;
    if (!EnvironmentAllows(session, descriptor, environmentReason))
    {
        TradingToolResult result;
        result.status = TradingToolCallStatus::PermissionDenied;
        result.toolName = call.name;
        result.reasonCode = environmentReason;
        return result;
    }

    // Validate the complete typed call before any dispatch or permit lookup.
    // The wire decoder performs the same check, but callers embedded in the
    // process can invoke the registry directly and must not bypass the
    // schema/field boundary.
    std::string semanticReason;
    std::string semanticDetail;
    if (!ValidateCallSemantics(call, semanticReason, semanticDetail))
    {
        TradingToolResult result;
        result.status = TradingToolCallStatus::Rejected;
        result.toolName = call.name;
        result.reasonCode = semanticReason;
        result.detail = semanticDetail;
        return result;
    }
    if (call.name == "decision.get_snapshot")
        return InvokeDecisionSnapshot(session, call);
    if (call.name == "intent.preview_target_position")
        return InvokeTargetPreview(session, call);
    if (call.name == "intent.apply_target_position")
        return InvokeTargetApply(session, call);
    if (call.name == "system.tools.list" || call.name == "system.tools.describe")
        return InvokeDiscovery(session, call);
    if (descriptor.effect == TradingToolEffect::Read) return InvokeRead(session, descriptor, call);

    if (call.name == "trade.place_order")
    {
        IbPlaceOrderCommand command;
        command.context = session.executionContext;
        command.contract = call.ibContract;
        command.order = call.ibOrder;
        command.instrument = call.instrument;
        command.timeInForce = call.timeInForce;
        command.referencePrice = call.referencePrice;
        command.expiresAtMs = call.expiresAtMs;
        command.previewPermit = call.previewPermit;
        ExecutionCommandResult execution;
        try
        {
            execution = m_execution.PlaceOrder(command);
        }
        catch (const std::exception&)
        {
            execution = AuthorityExceptionResult(command.context);
        }
        catch (...)
        {
            execution = AuthorityExceptionResult(command.context);
        }
        return FromExecution(call.name, execution);
    }
    if (call.name == "trade.cancel_order")
    {
        IbCancelOrderCommand command;
        command.context = session.executionContext;
        command.orderId = call.orderId;
        // Both values are resolved from the server-side ownership projection.
        // Agent input for either field would be spoofable and is rejected by
        // ValidateCallSemantics().
        ExecutionCommandResult execution;
        try
        {
            execution = m_execution.CancelOrder(command);
        }
        catch (const std::exception&)
        {
            execution = AuthorityExceptionResult(command.context);
        }
        catch (...)
        {
            execution = AuthorityExceptionResult(command.context);
        }
        return FromExecution(call.name, execution);
    }
    if (call.name == "trade.flatten_position")
    {
        if (!m_tradeCallbacks.flattenPosition)
        {
            TradingToolResult result;
            result.status = TradingToolCallStatus::Error;
            result.toolName = call.name;
            result.reasonCode = "TOOL_HANDLER_UNAVAILABLE";
            return result;
        }
        ExecutionCommandResult execution;
        try
        {
            execution = m_tradeCallbacks.flattenPosition(session, call);
        }
        catch (const std::exception&)
        {
            execution = AuthorityExceptionResult(session.executionContext);
        }
        catch (...)
        {
            execution = AuthorityExceptionResult(session.executionContext);
        }
        return FromExecution(call.name, execution);
    }

    TradingToolResult result;
    result.status = TradingToolCallStatus::InvalidTool;
    result.toolName = call.name;
    result.reasonCode = "TOOL_NOT_IMPLEMENTED";
    return result;
}
