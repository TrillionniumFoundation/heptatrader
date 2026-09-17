#include "xt_gateway_adapter.h"

#include <limits>
#include <sstream>

namespace
{
const std::size_t kHxqMaximumFrameBytes = 256U * 1024U;
}

bool HeptaXTGatewayAdapter::SafeToken(const std::string& value, std::size_t maximum)
{
    if (value.empty() || value.size() > maximum) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '.' || c == ':' ||
              c == '-' || c == '_'))
            return false;
    }
    return true;
}

bool HeptaXTGatewayAdapter::LowerHexSha256(const std::string& value)
{
    if (value.size() != 64U) return false;
    for (std::size_t i = 0; i < value.size(); ++i)
        if (!((value[i] >= '0' && value[i] <= '9') ||
              (value[i] >= 'a' && value[i] <= 'f')))
            return false;
    return true;
}

std::string HeptaXTGatewayAdapter::EscapeJson(const std::string& value)
{
    std::string escaped;
    escaped.reserve(value.size() + 8U);
    static const char hex[] = "0123456789abcdef";
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        if (c == '"' || c == '\\')
        {
            escaped.push_back('\\');
            escaped.push_back(static_cast<char>(c));
        }
        else if (c < 0x20U)
        {
            escaped += "\\u00";
            escaped.push_back(hex[(c >> 4U) & 0x0fU]);
            escaped.push_back(hex[c & 0x0fU]);
        }
        else
            escaped.push_back(static_cast<char>(c));
    }
    return escaped;
}

bool HeptaXTGatewayAdapter::EncodeFrame(
    const std::string& canonicalJson, std::string& frame)
{
    if (canonicalJson.empty() || canonicalJson.size() > kHxqMaximumFrameBytes ||
        canonicalJson.front() != '{' || canonicalJson.back() != '}')
        return false;
    const std::uint32_t size = static_cast<std::uint32_t>(canonicalJson.size());
    frame.clear();
    frame.reserve(4U + canonicalJson.size());
    frame.push_back(static_cast<char>((size >> 24U) & 0xffU));
    frame.push_back(static_cast<char>((size >> 16U) & 0xffU));
    frame.push_back(static_cast<char>((size >> 8U) & 0xffU));
    frame.push_back(static_cast<char>(size & 0xffU));
    frame.append(canonicalJson);
    return true;
}

bool HeptaXTGatewayAdapter::DecodeFrame(
    const std::string& frame, std::string& canonicalJson)
{
    canonicalJson.clear();
    if (frame.size() < 6U) return false;
    const unsigned char* bytes =
        reinterpret_cast<const unsigned char*>(frame.data());
    const std::uint32_t size =
        (static_cast<std::uint32_t>(bytes[0]) << 24U) |
        (static_cast<std::uint32_t>(bytes[1]) << 16U) |
        (static_cast<std::uint32_t>(bytes[2]) << 8U) |
        static_cast<std::uint32_t>(bytes[3]);
    if (size == 0U || size > kHxqMaximumFrameBytes ||
        frame.size() != static_cast<std::size_t>(size) + 4U)
        return false;
    canonicalJson.assign(frame.data() + 4U, size);
    return canonicalJson.front() == '{' && canonicalJson.back() == '}' &&
        canonicalJson.find('\n') == std::string::npos &&
        canonicalJson.find('\r') == std::string::npos;
}

bool HeptaXTGatewayAdapter::Init(const HeptaXTConfig& cfg)
{
    m_initialized = false;
    m_connected = false;
    m_readOnlyTransportConfigured = false;
    m_nextRequestId = 1;
    m_config = HeptaXTConfig();
    if (cfg.mode != "XT")
    {
        m_lastRejectReason = "XT_MODE_INVALID";
        return false;
    }
    m_initialized = true;
    if (!cfg.admittedReadOnlyExchange)
    {
        m_lastRejectReason = "XT_TRANSPORT_NOT_IMPLEMENTED";
        return true;
    }
    if (!SafeToken(cfg.account, 128U) ||
        !SafeToken(cfg.serviceEpoch, 128U) ||
        cfg.connectionEpoch == 0U || !LowerHexSha256(cfg.peerProfileSha256))
    {
        m_initialized = false;
        m_lastRejectReason = "XT_READ_ONLY_PROFILE_INVALID";
        return false;
    }
    m_config = cfg;
    m_readOnlyTransportConfigured = true;
    m_lastRejectReason = "XT_READ_ONLY_NOT_CONNECTED";
    return true;
}

bool HeptaXTGatewayAdapter::ExchangeReadOnly(
    const std::string& operation, const std::string& payloadJson)
{
    if (!m_initialized)
    {
        m_lastRejectReason = "XT_NOT_INITIALIZED";
        return false;
    }
    if (!m_readOnlyTransportConfigured)
    {
        m_lastRejectReason = "XT_TRANSPORT_NOT_IMPLEMENTED";
        return false;
    }
    if (operation != "identity" && !m_connected)
    {
        m_lastRejectReason = "XT_READ_ONLY_NOT_CONNECTED";
        return false;
    }
    if (m_nextRequestId == std::numeric_limits<std::uint64_t>::max())
    {
        m_lastRejectReason = "XT_REQUEST_ID_EXHAUSTED";
        return false;
    }
    const std::uint64_t requestId = m_nextRequestId++;
    std::ostringstream request;
    request << "{\"protocol\":\"HXQ1\",\"version\":1,\"request_id\":"
            << requestId
            << ",\"service_epoch\":\"" << EscapeJson(m_config.serviceEpoch)
            << "\",\"connection_epoch\":" << m_config.connectionEpoch
            << ",\"operation\":\"" << operation
            << "\",\"account\":\"" << EscapeJson(m_config.account)
            << "\",\"venue_command_id\":\"\",\"payload\":"
            << payloadJson << "}";
    std::string requestFrame;
    if (!EncodeFrame(request.str(), requestFrame))
    {
        m_lastRejectReason = "XT_HXQ1_REQUEST_INVALID";
        return false;
    }
    std::string responseFrame;
    if (!m_config.admittedReadOnlyExchange(requestFrame, responseFrame))
    {
        m_lastRejectReason = "XT_HXQ1_TRANSPORT_FAILED";
        return false;
    }
    std::string response;
    if (!DecodeFrame(responseFrame, response))
    {
        m_lastRejectReason = "XT_HXQ1_RESPONSE_FRAME_INVALID";
        return false;
    }
    std::ostringstream expected;
    expected << "{\"protocol\":\"HXQ1\",\"version\":1,\"request_id\":"
             << requestId
             << ",\"service_epoch\":\"" << EscapeJson(m_config.serviceEpoch)
             << "\",\"connection_epoch\":" << m_config.connectionEpoch
             << ",\"operation\":\"" << operation
             << "\",\"account\":\"" << EscapeJson(m_config.account)
             << "\",\"ok\":true}";
    if (response != expected.str())
    {
        m_lastRejectReason = "XT_HXQ1_RESPONSE_BINDING_INVALID";
        return false;
    }
    m_lastRejectReason.clear();
    return true;
}

bool HeptaXTGatewayAdapter::Connect()
{
    if (!m_initialized)
    {
        m_lastRejectReason = "XT_NOT_INITIALIZED";
        return false;
    }
    if (!m_readOnlyTransportConfigured)
    {
        m_lastRejectReason = "XT_TRANSPORT_NOT_IMPLEMENTED";
        return false;
    }
    const std::string payload = std::string("{\"peer_profile_sha256\":\"") +
        m_config.peerProfileSha256 + "\"}";
    if (!ExchangeReadOnly("identity", payload)) return false;
    m_connected = true;
    m_lastRejectReason = "XT_READ_ONLY_READY";
    return true;
}

void HeptaXTGatewayAdapter::Disconnect()
{
    m_connected = false;
    m_lastRejectReason = m_readOnlyTransportConfigured ?
        "XT_READ_ONLY_NOT_CONNECTED" :
        (m_initialized ? "XT_TRANSPORT_NOT_IMPLEMENTED" : "XT_NOT_INITIALIZED");
}

bool HeptaXTGatewayAdapter::ReqAccountSummary()
{
    return ExchangeReadOnly("account_snapshot", "{}");
}

bool HeptaXTGatewayAdapter::ReqPositions()
{
    return ExchangeReadOnly("position_snapshot", "{}");
}

bool HeptaXTGatewayAdapter::ReqMktData(const std::string& instrument)
{
    if (!SafeToken(instrument, 128U))
    {
        m_lastRejectReason = "XT_INSTRUMENT_INVALID";
        return false;
    }
    return ExchangeReadOnly("quote_subscribe",
        std::string("{\"instrument\":\"") + EscapeJson(instrument) + "\"}");
}

bool HeptaXTGatewayAdapter::RejectUnsupportedMutation()
{
    if (!m_initialized)
        m_lastRejectReason = "XT_NOT_INITIALIZED";
    else if (!m_readOnlyTransportConfigured)
        m_lastRejectReason = "XT_TRANSPORT_NOT_IMPLEMENTED";
    else
        m_lastRejectReason = "XT_MUTATION_DISABLED";
    return false;
}

bool HeptaXTGatewayAdapter::CancelOrder(long long)
{
    return RejectUnsupportedMutation();
}

bool HeptaXTGatewayAdapter::PlaceOrder(const std::string&, const std::string&,
                                      double, double, long long* outOrderId)
{
    if (outOrderId) *outOrderId = 0;
    return RejectUnsupportedMutation();
}
