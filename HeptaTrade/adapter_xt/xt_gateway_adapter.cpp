#include "xt_gateway_adapter.h"

#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <map>
#include <set>
#include <sstream>

namespace
{
const std::size_t kHxqMaximumFrameBytes = 256U * 1024U;
const std::size_t kHxqMaximumPositions = 1024U;

class CanonicalCursor
{
public:
    explicit CanonicalCursor(const std::string& input) : m_input(input) {}

    bool Consume(const char* literal)
    {
        const std::string wanted(literal);
        if (m_input.compare(m_offset, wanted.size(), wanted) != 0) return false;
        m_offset += wanted.size();
        return true;
    }

    bool String(std::string& value)
    {
        value.clear();
        const std::size_t end = m_input.find('"', m_offset);
        if (end == std::string::npos) return false;
        for (std::size_t i = m_offset; i < end; ++i)
        {
            const unsigned char byte = static_cast<unsigned char>(m_input[i]);
            if (byte < 0x20U || byte == '\\') return false;
        }
        value.assign(m_input, m_offset, end - m_offset);
        m_offset = end + 1U;
        return true;
    }

    bool Unsigned(char delimiter, std::uint64_t& value)
    {
        const std::size_t end = m_input.find(delimiter, m_offset);
        if (end == std::string::npos || end == m_offset) return false;
        std::uint64_t parsed = 0;
        if (end - m_offset > 1U && m_input[m_offset] == '0') return false;
        for (std::size_t i = m_offset; i < end; ++i)
        {
            if (m_input[i] < '0' || m_input[i] > '9') return false;
            const std::uint64_t digit =
                static_cast<std::uint64_t>(m_input[i] - '0');
            if (parsed >
                (std::numeric_limits<std::uint64_t>::max() - digit) / 10U)
                return false;
            parsed = parsed * 10U + digit;
        }
        value = parsed;
        m_offset = end + 1U;
        return true;
    }

    bool Number(char delimiter, double& value)
    {
        const std::size_t end = m_input.find(delimiter, m_offset);
        if (end == std::string::npos || end == m_offset) return false;
        const std::string token = m_input.substr(m_offset, end - m_offset);
        if (token.find_first_of(" \t\r\n") != std::string::npos)
            return false;
        // strtod accepts spellings outside JSON (for example hexadecimal
        // floats, .5, 1. and leading-zero integers). Validate the JSON number
        // grammar before conversion so authority-bearing payloads stay exact.
        std::size_t grammar = 0;
        if (token[grammar] == '-')
        {
            ++grammar;
            if (grammar == token.size()) return false;
        }
        if (token[grammar] == '0')
        {
            ++grammar;
            if (grammar < token.size() &&
                token[grammar] >= '0' && token[grammar] <= '9')
                return false;
        }
        else
        {
            if (token[grammar] < '1' || token[grammar] > '9') return false;
            while (grammar < token.size() &&
                   token[grammar] >= '0' && token[grammar] <= '9')
                ++grammar;
        }
        if (grammar < token.size() && token[grammar] == '.')
        {
            ++grammar;
            const std::size_t fraction = grammar;
            while (grammar < token.size() &&
                   token[grammar] >= '0' && token[grammar] <= '9')
                ++grammar;
            if (grammar == fraction) return false;
        }
        if (grammar < token.size() &&
            (token[grammar] == 'e' || token[grammar] == 'E'))
        {
            ++grammar;
            if (grammar < token.size() &&
                (token[grammar] == '+' || token[grammar] == '-'))
                ++grammar;
            const std::size_t exponent = grammar;
            while (grammar < token.size() &&
                   token[grammar] >= '0' && token[grammar] <= '9')
                ++grammar;
            if (grammar == exponent) return false;
        }
        if (grammar != token.size()) return false;
        char* parsedEnd = nullptr;
        errno = 0;
        const double parsed = std::strtod(token.c_str(), &parsedEnd);
        if (errno == ERANGE || parsedEnd == token.c_str() ||
            *parsedEnd != '\0' || !std::isfinite(parsed))
            return false;
        if (parsed == 0.0 &&
            token.find_first_of("123456789") != std::string::npos)
            return false;
        value = parsed;
        m_offset = end + 1U;
        return true;
    }

    bool Peek(char byte) const
    {
        return m_offset < m_input.size() && m_input[m_offset] == byte;
    }

    bool End() const { return m_offset == m_input.size(); }

private:
    const std::string& m_input;
    std::size_t m_offset = 0;
};
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

void HeptaXTGatewayAdapter::ResetReadState()
{
    m_accountSnapshot = HeptaXTAccountSnapshot();
    m_positionSnapshot = HeptaXTPositionSnapshot();
    m_orderSnapshot = HeptaXTOrderSnapshot();
    m_tradeSnapshot = HeptaXTTradeSnapshot();
    m_quoteSnapshot = HeptaXTQuoteSnapshot();
}

bool HeptaXTGatewayAdapter::Init(const HeptaXTConfig& cfg)
{
    m_initialized = false;
    m_connected = false;
    m_readOnlyTransportConfigured = false;
    m_nextRequestId = 1;
    m_config = HeptaXTConfig();
    ResetReadState();
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
    const std::string& operation, const std::string& payloadJson,
    std::string* responsePayloadJson)
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
             << "\",\"ok\":true";
    if (responsePayloadJson == nullptr)
    {
        expected << "}";
        if (response != expected.str())
        {
            m_lastRejectReason = "XT_HXQ1_RESPONSE_BINDING_INVALID";
            return false;
        }
    }
    else
    {
        const std::string prefix = expected.str() + ",\"payload\":";
        if (response.size() <= prefix.size() + 2U ||
            response.compare(0, prefix.size(), prefix) != 0 ||
            response.back() != '}')
        {
            m_lastRejectReason = "XT_HXQ1_RESPONSE_BINDING_INVALID";
            return false;
        }
        responsePayloadJson->assign(
            response, prefix.size(), response.size() - prefix.size() - 1U);
        if (responsePayloadJson->empty() ||
            responsePayloadJson->front() != '{' ||
            responsePayloadJson->back() != '}')
        {
            responsePayloadJson->clear();
            m_lastRejectReason = "XT_HXQ1_RESPONSE_PAYLOAD_INVALID";
            return false;
        }
    }
    m_lastRejectReason.clear();
    return true;
}

bool HeptaXTGatewayAdapter::ParseAccountSnapshot(
    const std::string& payload, std::uint64_t connectionEpoch,
    HeptaXTAccountSnapshot& out)
{
    out = HeptaXTAccountSnapshot();
    CanonicalCursor cursor(payload);
    std::uint64_t generation = 0;
    std::string currency;
    double cash = 0.0, total = 0.0, available = 0.0;
    if (!cursor.Consume(
            "{\"schema\":\"heptatrader.xt.account.v1\",\"generation\":") ||
        !cursor.Unsigned(',', generation) || generation == 0 ||
        !cursor.Consume("\"complete\":true,\"currency\":\"") ||
        !cursor.String(currency) || !SafeToken(currency, 16U) ||
        !cursor.Consume(",\"cash\":") || !cursor.Number(',', cash) ||
        !cursor.Consume("\"total_asset\":") || !cursor.Number(',', total) ||
        !cursor.Consume("\"available_cash\":") ||
        !cursor.Number('}', available) || !cursor.End() ||
        cash < 0.0 || total < 0.0 || available < 0.0 ||
        available > total)
        return false;
    out.complete = true;
    out.connectionEpoch = connectionEpoch;
    out.generation = generation;
    out.currency = currency;
    out.cash = cash;
    out.totalAsset = total;
    out.availableCash = available;
    return true;
}

bool HeptaXTGatewayAdapter::ParsePositionSnapshot(
    const std::string& payload, std::uint64_t connectionEpoch,
    HeptaXTPositionSnapshot& out)
{
    out = HeptaXTPositionSnapshot();
    CanonicalCursor cursor(payload);
    std::uint64_t generation = 0;
    if (!cursor.Consume(
            "{\"schema\":\"heptatrader.xt.positions.v1\",\"generation\":") ||
        !cursor.Unsigned(',', generation) || generation == 0 ||
        !cursor.Consume("\"complete\":true,\"positions\":["))
        return false;
    std::set<std::string> instruments;
    while (!cursor.Peek(']'))
    {
        if (out.positions.size() >= kHxqMaximumPositions ||
            !cursor.Consume("{\"instrument\":\""))
            return false;
        HeptaXTPosition position;
        if (!cursor.String(position.instrument) ||
            !SafeToken(position.instrument, 128U) ||
            !instruments.insert(position.instrument).second ||
            !cursor.Consume(",\"quantity\":") ||
            !cursor.Number(',', position.quantity) ||
            !cursor.Consume("\"sellable_quantity\":") ||
            !cursor.Number(',', position.sellableQuantity) ||
            !cursor.Consume("\"cost\":") ||
            !cursor.Number('}', position.cost) ||
            position.quantity < 0.0 ||
            position.sellableQuantity < 0.0 ||
            position.sellableQuantity > position.quantity ||
            position.cost < 0.0)
            return false;
        out.positions.push_back(position);
        if (cursor.Peek(','))
        {
            if (!cursor.Consume(",")) return false;
            continue;
        }
        break;
    }
    if (!cursor.Consume("]}") || !cursor.End()) return false;
    out.complete = true;
    out.connectionEpoch = connectionEpoch;
    out.generation = generation;
    return true;
}

bool HeptaXTGatewayAdapter::ParseOrderSnapshot(
    const std::string& payload, std::uint64_t connectionEpoch,
    HeptaXTOrderSnapshot& out)
{
    out = HeptaXTOrderSnapshot();
    CanonicalCursor cursor(payload);
    std::uint64_t generation = 0;
    if (!cursor.Consume(
            "{\"schema\":\"heptatrader.xt.orders.v1\",\"generation\":") ||
        !cursor.Unsigned(',', generation) || generation == 0 ||
        !cursor.Consume("\"complete\":true,\"orders\":["))
        return false;
    std::set<std::string> orderIds;
    while (!cursor.Peek(']'))
    {
        if (out.orders.size() >= kHxqMaximumPositions ||
            !cursor.Consume("{\"order_id\":\""))
            return false;
        HeptaXTOrder order;
        if (!cursor.String(order.orderId) ||
            !SafeToken(order.orderId, 128U) ||
            !orderIds.insert(order.orderId).second ||
            !cursor.Consume(",\"instrument\":\"") ||
            !cursor.String(order.instrument) ||
            !SafeToken(order.instrument, 128U) ||
            !cursor.Consume(",\"side\":\"") ||
            !cursor.String(order.side) ||
            (order.side != "BUY" && order.side != "SELL") ||
            !cursor.Consume(",\"status\":\"") ||
            !cursor.String(order.status) ||
            (order.status != "SUBMITTED" &&
             order.status != "PARTIALLY_FILLED" &&
             order.status != "FILLED" &&
             order.status != "CANCELLED" &&
             order.status != "REJECTED") ||
            !cursor.Consume(",\"quantity\":") ||
            !cursor.Number(',', order.quantity) ||
            !cursor.Consume("\"filled_quantity\":") ||
            !cursor.Number(',', order.filledQuantity) ||
            !cursor.Consume("\"limit_price\":") ||
            !cursor.Number('}', order.limitPrice) ||
            order.quantity <= 0.0 ||
            order.filledQuantity < 0.0 ||
            order.filledQuantity > order.quantity ||
            order.limitPrice <= 0.0 ||
            (order.status == "SUBMITTED" && order.filledQuantity != 0.0) ||
            (order.status == "PARTIALLY_FILLED" &&
             !(order.filledQuantity > 0.0 &&
               order.filledQuantity < order.quantity)) ||
            (order.status == "FILLED" &&
             order.filledQuantity != order.quantity) ||
            (order.status == "REJECTED" &&
             order.filledQuantity != 0.0))
            return false;
        out.orders.push_back(order);
        if (cursor.Peek(','))
        {
            if (!cursor.Consume(",")) return false;
            continue;
        }
        break;
    }
    if (!cursor.Consume("]}") || !cursor.End()) return false;
    out.complete = true;
    out.connectionEpoch = connectionEpoch;
    out.generation = generation;
    return true;
}

bool HeptaXTGatewayAdapter::ParseTradeSnapshot(
    const std::string& payload, std::uint64_t connectionEpoch,
    HeptaXTTradeSnapshot& out)
{
    out = HeptaXTTradeSnapshot();
    CanonicalCursor cursor(payload);
    std::uint64_t generation = 0;
    if (!cursor.Consume(
            "{\"schema\":\"heptatrader.xt.trades.v1\",\"generation\":") ||
        !cursor.Unsigned(',', generation) || generation == 0 ||
        !cursor.Consume("\"complete\":true,\"trades\":["))
        return false;
    std::set<std::string> tradeIds;
    while (!cursor.Peek(']'))
    {
        if (out.trades.size() >= kHxqMaximumPositions ||
            !cursor.Consume("{\"trade_id\":\""))
            return false;
        HeptaXTTrade trade;
        if (!cursor.String(trade.tradeId) ||
            !SafeToken(trade.tradeId, 128U) ||
            !tradeIds.insert(trade.tradeId).second ||
            !cursor.Consume(",\"order_id\":\"") ||
            !cursor.String(trade.orderId) ||
            !SafeToken(trade.orderId, 128U) ||
            !cursor.Consume(",\"instrument\":\"") ||
            !cursor.String(trade.instrument) ||
            !SafeToken(trade.instrument, 128U) ||
            !cursor.Consume(",\"side\":\"") ||
            !cursor.String(trade.side) ||
            (trade.side != "BUY" && trade.side != "SELL") ||
            !cursor.Consume(",\"quantity\":") ||
            !cursor.Number(',', trade.quantity) ||
            !cursor.Consume("\"price\":") ||
            !cursor.Number(',', trade.price) ||
            !cursor.Consume("\"occurred_at_ms\":") ||
            !cursor.Unsigned('}', trade.occurredAtMs) ||
            trade.quantity <= 0.0 || trade.price <= 0.0 ||
            trade.occurredAtMs == 0)
            return false;
        out.trades.push_back(trade);
        if (cursor.Peek(','))
        {
            if (!cursor.Consume(",")) return false;
            continue;
        }
        break;
    }
    if (!cursor.Consume("]}") || !cursor.End()) return false;
    out.complete = true;
    out.connectionEpoch = connectionEpoch;
    out.generation = generation;
    return true;
}

bool HeptaXTGatewayAdapter::ParseQuoteSnapshot(
    const std::string& payload, std::uint64_t connectionEpoch,
    const std::string& expectedInstrument, HeptaXTQuoteSnapshot& out)
{
    out = HeptaXTQuoteSnapshot();
    CanonicalCursor cursor(payload);
    std::uint64_t generation = 0, observedAtMs = 0;
    std::string instrument;
    double bid = 0.0, ask = 0.0;
    if (!cursor.Consume(
            "{\"schema\":\"heptatrader.xt.quote.v1\",\"generation\":") ||
        !cursor.Unsigned(',', generation) || generation == 0 ||
        !cursor.Consume("\"complete\":true,\"instrument\":\"") ||
        !cursor.String(instrument) || instrument != expectedInstrument ||
        !cursor.Consume(",\"bid\":") || !cursor.Number(',', bid) ||
        !cursor.Consume("\"ask\":") || !cursor.Number(',', ask) ||
        !cursor.Consume("\"observed_at_ms\":") ||
        !cursor.Unsigned('}', observedAtMs) || observedAtMs == 0 ||
        !cursor.End() || bid <= 0.0 || ask <= 0.0 || ask < bid)
        return false;
    out.complete = true;
    out.connectionEpoch = connectionEpoch;
    out.generation = generation;
    out.instrument = instrument;
    out.bid = bid;
    out.ask = ask;
    out.observedAtMs = observedAtMs;
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
    ResetReadState();
    const std::string payload = std::string("{\"peer_profile_sha256\":\"") +
        m_config.peerProfileSha256 + "\"}";
    if (!ExchangeReadOnly("identity", payload)) return false;
    m_connected = true;
    m_lastRejectReason = "XT_READ_ONLY_CONNECTED";
    return true;
}

void HeptaXTGatewayAdapter::Disconnect()
{
    m_connected = false;
    ResetReadState();
    m_lastRejectReason = m_readOnlyTransportConfigured ?
        "XT_READ_ONLY_NOT_CONNECTED" :
        (m_initialized ? "XT_TRANSPORT_NOT_IMPLEMENTED" : "XT_NOT_INITIALIZED");
}

bool HeptaXTGatewayAdapter::ReqAccountSummary()
{
    m_accountSnapshot = HeptaXTAccountSnapshot();
    std::string payload;
    if (!ExchangeReadOnly("account_snapshot", "{}", &payload)) return false;
    if (!ParseAccountSnapshot(
            payload, m_config.connectionEpoch, m_accountSnapshot))
    {
        m_lastRejectReason = "XT_ACCOUNT_SNAPSHOT_INVALID";
        return false;
    }
    return true;
}

bool HeptaXTGatewayAdapter::ReqPositions()
{
    m_positionSnapshot = HeptaXTPositionSnapshot();
    std::string payload;
    if (!ExchangeReadOnly("position_snapshot", "{}", &payload)) return false;
    if (!ParsePositionSnapshot(
            payload, m_config.connectionEpoch, m_positionSnapshot))
    {
        m_lastRejectReason = "XT_POSITION_SNAPSHOT_INVALID";
        return false;
    }
    return true;
}

bool HeptaXTGatewayAdapter::ReqOrders()
{
    m_orderSnapshot = HeptaXTOrderSnapshot();
    std::string payload;
    if (!ExchangeReadOnly("order_snapshot", "{}", &payload)) return false;
    if (!ParseOrderSnapshot(
            payload, m_config.connectionEpoch, m_orderSnapshot))
    {
        m_lastRejectReason = "XT_ORDER_SNAPSHOT_INVALID";
        return false;
    }
    return true;
}

bool HeptaXTGatewayAdapter::ReqTrades()
{
    m_tradeSnapshot = HeptaXTTradeSnapshot();
    std::string payload;
    if (!ExchangeReadOnly("trade_snapshot", "{}", &payload)) return false;
    if (!ParseTradeSnapshot(
            payload, m_config.connectionEpoch, m_tradeSnapshot))
    {
        m_lastRejectReason = "XT_TRADE_SNAPSHOT_INVALID";
        return false;
    }
    return true;
}

bool HeptaXTGatewayAdapter::ReqMktData(const std::string& instrument)
{
    m_quoteSnapshot = HeptaXTQuoteSnapshot();
    if (!SafeToken(instrument, 128U))
    {
        m_lastRejectReason = "XT_INSTRUMENT_INVALID";
        return false;
    }
    std::string payload;
    if (!ExchangeReadOnly(
            "quote_subscribe",
            std::string("{\"instrument\":\"") +
                EscapeJson(instrument) + "\"}",
            &payload))
        return false;
    if (!ParseQuoteSnapshot(
            payload, m_config.connectionEpoch,
            instrument, m_quoteSnapshot))
    {
        m_lastRejectReason = "XT_QUOTE_SNAPSHOT_INVALID";
        return false;
    }
    return true;
}

bool HeptaXTGatewayAdapter::GetAccountSnapshot(
    HeptaXTAccountSnapshot& out) const
{
    out = m_accountSnapshot;
    return out.complete;
}

bool HeptaXTGatewayAdapter::GetPositionSnapshot(
    HeptaXTPositionSnapshot& out) const
{
    out = m_positionSnapshot;
    return out.complete;
}

bool HeptaXTGatewayAdapter::GetOrderSnapshot(
    HeptaXTOrderSnapshot& out) const
{
    out = m_orderSnapshot;
    return out.complete;
}

bool HeptaXTGatewayAdapter::GetTradeSnapshot(
    HeptaXTTradeSnapshot& out) const
{
    out = m_tradeSnapshot;
    return out.complete;
}

bool HeptaXTGatewayAdapter::GetQuoteSnapshot(
    const std::string& instrument, HeptaXTQuoteSnapshot& out) const
{
    out = m_quoteSnapshot;
    return out.complete && out.instrument == instrument;
}

bool HeptaXTGatewayAdapter::AccountPositionReadReady() const
{
    return m_connected &&
        m_accountSnapshot.complete && m_positionSnapshot.complete &&
        m_accountSnapshot.connectionEpoch == m_config.connectionEpoch &&
        m_positionSnapshot.connectionEpoch == m_config.connectionEpoch &&
        m_accountSnapshot.generation != 0 &&
        m_accountSnapshot.generation == m_positionSnapshot.generation;
}

bool HeptaXTGatewayAdapter::AccountPositionOrderTradeReadReady() const
{
    if (!AccountPositionReadReady() ||
        !m_orderSnapshot.complete || !m_tradeSnapshot.complete ||
        m_orderSnapshot.connectionEpoch != m_config.connectionEpoch ||
        m_tradeSnapshot.connectionEpoch != m_config.connectionEpoch ||
        m_orderSnapshot.generation == 0 ||
        m_orderSnapshot.generation != m_accountSnapshot.generation ||
        m_tradeSnapshot.generation != m_accountSnapshot.generation)
        return false;

    std::map<std::string, const HeptaXTOrder*> orders;
    std::set<std::string> ordersRequiringTradeEvidence;
    for (std::size_t i = 0; i < m_orderSnapshot.orders.size(); ++i)
    {
        const HeptaXTOrder& order = m_orderSnapshot.orders[i];
        orders[order.orderId] = &order;
        if (order.filledQuantity > 0.0)
            ordersRequiringTradeEvidence.insert(order.orderId);
    }
    for (std::size_t i = 0; i < m_tradeSnapshot.trades.size(); ++i)
    {
        const HeptaXTTrade& trade = m_tradeSnapshot.trades[i];
        const auto found = orders.find(trade.orderId);
        if (found == orders.end() ||
            found->second->instrument != trade.instrument ||
            found->second->side != trade.side)
            return false;
        ordersRequiringTradeEvidence.erase(trade.orderId);
    }
    return ordersRequiringTradeEvidence.empty();
}

bool HeptaXTGatewayAdapter::QuoteFresh(
    const std::string& instrument,
    std::uint64_t evaluationAtMs,
    std::uint64_t maximumAgeMs) const
{
    return m_connected && maximumAgeMs != 0 && evaluationAtMs != 0 &&
        m_quoteSnapshot.complete &&
        m_quoteSnapshot.connectionEpoch == m_config.connectionEpoch &&
        m_quoteSnapshot.instrument == instrument &&
        m_quoteSnapshot.observedAtMs != 0 &&
        m_quoteSnapshot.observedAtMs <= evaluationAtMs &&
        evaluationAtMs - m_quoteSnapshot.observedAtMs <= maximumAgeMs;
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
