#include "openclaw_0dte_bridge.h"
#include "state/ib_contract_identity.h"

#include <algorithm>
#include <cstdlib>
#include <cctype>
#include <cmath>
#include <ctime>
#include <fstream>
#include <functional>
#include <sstream>

namespace {

std::string JsonGetString(const std::string& json, const std::string& key) {
    const std::string pat = "\"" + key + "\":\"";
    std::size_t p = json.find(pat);
    if (p == std::string::npos) return "";
    p += pat.size();

    std::string out;
    bool esc = false;
    for (; p < json.size(); ++p) {
        const char c = json[p];
        if (esc) {
            switch (c) {
            case 'n': out.push_back('\n'); break;
            case 'r': out.push_back('\r'); break;
            case 't': out.push_back('\t'); break;
            default: out.push_back(c); break;
            }
            esc = false;
            continue;
        }
        if (c == '\\') {
            esc = true;
            continue;
        }
        if (c == '"') break;
        out.push_back(c);
    }
    return out;
}

long long JsonGetLongLong(const std::string& json, const std::string& key, long long defVal) {
    const std::string pat = "\"" + key + "\":";
    std::size_t p = json.find(pat);
    if (p == std::string::npos) return defVal;
    p += pat.size();

    std::size_t e = p;
    while (e < json.size() && (json[e] == '-' || (json[e] >= '0' && json[e] <= '9'))) ++e;
    if (e == p) return defVal;
    return std::atoll(json.substr(p, e - p).c_str());
}

double JsonGetDouble(const std::string& json, const std::string& key, double defVal) {
    const std::string pat = "\"" + key + "\":";
    std::size_t p = json.find(pat);
    if (p == std::string::npos) return defVal;
    p += pat.size();

    std::size_t e = p;
    while (e < json.size()) {
        const char c = json[e];
        if ((c >= '0' && c <= '9') || c == '-' || c == '+' || c == '.' || c == 'e' || c == 'E') ++e;
        else break;
    }
    if (e == p) return defVal;
    return std::atof(json.substr(p, e - p).c_str());
}

bool JsonGetBool(const std::string& json, const std::string& key, bool defVal) {
    const std::string pat = "\"" + key + "\":";
    std::size_t p = json.find(pat);
    if (p == std::string::npos) return defVal;
    p += pat.size();
    while (p < json.size() && (json[p] == ' ' || json[p] == '\t')) ++p;
    if (json.compare(p, 4, "true") == 0) return true;
    if (json.compare(p, 5, "false") == 0) return false;
    if (json.compare(p, 1, "1") == 0) return true;
    if (json.compare(p, 1, "0") == 0) return false;
    return defVal;
}

std::string Upper(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char ch) { return static_cast<char>(std::toupper(ch)); });
    return s;
}

std::string JsonEscape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (char c : s) {
        switch (c) {
        case '\\': out += "\\\\"; break;
        case '"': out += "\\\""; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default: out.push_back(c); break;
        }
    }
    return out;
}

std::string NormalizeRight(std::string right) {
    right = Upper(right);
    if (right == "CALL") return "C";
    if (right == "PUT") return "P";
    return right;
}

long long NowEpochMs() {
    return static_cast<long long>(std::time(nullptr)) * 1000LL;
}

bool ParseHourMinute(const std::string& value, int& minutes) {
    if (value.empty()) return false;
    const std::size_t colon = value.find(':');
    if (colon == std::string::npos) return false;
    const int hh = std::atoi(value.substr(0, colon).c_str());
    const int mm = std::atoi(value.substr(colon + 1).c_str());
    if (hh < 0 || hh > 23 || mm < 0 || mm > 59) return false;
    minutes = hh * 60 + mm;
    return true;
}

int UtcMinutesNow() {
    const std::time_t now = std::time(nullptr);
    const std::tm* utc = std::gmtime(&now);
    if (utc == nullptr) return -1;
    return utc->tm_hour * 60 + utc->tm_min;
}

bool EntryWindowClosedUtc(const std::string& windowUtc) {
    if (windowUtc.empty()) return false;
    const std::size_t dash = windowUtc.find('-');
    if (dash == std::string::npos) return false;
    int startMinutes = 0;
    int endMinutes = 0;
    if (!ParseHourMinute(windowUtc.substr(0, dash), startMinutes) ||
        !ParseHourMinute(windowUtc.substr(dash + 1), endMinutes)) return false;
    const int nowMinutes = UtcMinutesNow();
    if (nowMinutes < 0) return false;
    return !(startMinutes <= nowMinutes && nowMinutes < endMinutes);
}

bool NoNewEntriesDueUtc(const std::string& cutoffUtc) {
    int cutoffMinutes = 0;
    if (!ParseHourMinute(cutoffUtc, cutoffMinutes)) return false;
    const int nowMinutes = UtcMinutesNow();
    if (nowMinutes < 0) return false;
    return nowMinutes >= cutoffMinutes;
}

std::string FallbackEventId(const std::string& line) {
    std::hash<std::string> h;
    std::ostringstream oss;
    oss << "openclaw-line-" << h(line);
    return oss.str();
}

} // namespace

void OpenClaw0DteBridgeConsumer::Configure(const Options& options) {
    m_options = options;
    if (m_options.maxBatch <= 0) m_options.maxBatch = 1;
    if (m_options.maxSignalAgeMs < 0) m_options.maxSignalAgeMs = 0;
    if (m_options.maxQty <= 0.0) m_options.maxQty = 1.0;
    if (m_options.maxPremiumUsd <= 0.0) m_options.maxPremiumUsd = 250.0;
    if (m_options.cursorPath.empty() && !m_options.path.empty()) m_options.cursorPath = m_options.path + ".cursor";
    if (m_options.consumedEventIdsPath.empty() && !m_options.path.empty()) m_options.consumedEventIdsPath = m_options.path + ".consumed";
    m_offset = 0;
    m_seenEventIds.clear();
    LoadConsumedEventIds();
    LoadCursor();
}

void OpenClaw0DteBridgeConsumer::LoadCursor() {
    if (m_options.cursorPath.empty()) return;
    std::ifstream in(m_options.cursorPath.c_str(), std::ios::in | std::ios::binary);
    if (!in.is_open()) return;
    std::ostringstream buf;
    buf << in.rdbuf();
    const std::string json = buf.str();
    const std::string path = JsonGetString(json, "path");
    const long long offset = JsonGetLongLong(json, "offset", -1);
    if (!path.empty() && path != m_options.path) return;
    if (offset >= 0) m_offset = offset;
}

void OpenClaw0DteBridgeConsumer::SaveCursor() const {
    if (m_options.cursorPath.empty()) return;
    std::ofstream out(m_options.cursorPath.c_str(), std::ios::out | std::ios::trunc);
    if (!out.is_open()) return;
    out << "{"
        << "\"ts_ms\":" << NowEpochMs()
        << ",\"path\":\"" << JsonEscape(m_options.path) << "\""
        << ",\"offset\":" << m_offset
        << "}\n";
}

void OpenClaw0DteBridgeConsumer::LoadConsumedEventIds() {
    if (m_options.consumedEventIdsPath.empty()) return;
    std::ifstream in(m_options.consumedEventIdsPath.c_str(), std::ios::in);
    if (!in.is_open()) return;
    std::string line;
    while (std::getline(in, line)) {
        if (!line.empty()) m_seenEventIds.insert(line);
    }
}

void OpenClaw0DteBridgeConsumer::AppendConsumedEventId(const std::string& eventId) const {
    if (m_options.consumedEventIdsPath.empty() || eventId.empty()) return;
    std::ofstream out(m_options.consumedEventIdsPath.c_str(), std::ios::out | std::ios::app);
    if (!out.is_open()) return;
    out << eventId << "\n";
}

std::vector<OpenClaw0DteIntent> OpenClaw0DteBridgeConsumer::Poll(std::vector<OpenClaw0DteBridgeReject>* rejects) {
    std::vector<OpenClaw0DteIntent> out;
    if (m_options.path.empty()) return out;

    std::ifstream in(m_options.path.c_str(), std::ios::in | std::ios::binary);
    if (!in.is_open()) return out;

    in.seekg(0, std::ios::end);
    const long long endPos = static_cast<long long>(in.tellg());
    const long long prevOffset = m_offset;
    if (endPos < m_offset) m_offset = 0;
    in.seekg(m_offset, std::ios::beg);

    std::string line;
    int scanned = 0;
    while (scanned < m_options.maxBatch && std::getline(in, line)) {
        ++scanned;
        if (line.empty()) continue;
        OpenClaw0DteIntent intent;
        OpenClaw0DteBridgeReject reject;
        if (ParseIntentLine(line, intent, reject)) {
            out.push_back(intent);
        } else if (rejects != nullptr) {
            rejects->push_back(reject);
        }
    }

    const std::streampos pos = in.tellg();
    m_offset = (pos == std::streampos(-1)) ? endPos : static_cast<long long>(pos);
    if (m_offset != prevOffset) SaveCursor();
    return out;
}

bool OpenClaw0DteBridgeConsumer::ParseIntentLine(const std::string& line, OpenClaw0DteIntent& out, OpenClaw0DteBridgeReject& reject) {
    out = OpenClaw0DteIntent();
    reject = OpenClaw0DteBridgeReject();
    reject.rawLine = line;

    if (JsonGetString(line, "event") != "order_intent") {
        reject.riskCode = "OPENCLAW_EVENT_NOT_ORDER_INTENT";
        reject.detail = "event must be order_intent";
        return false;
    }
    if (Upper(JsonGetString(line, "venue")) != "IB") {
        reject.riskCode = "OPENCLAW_VENUE_NOT_IB";
        reject.detail = "venue must be IB";
        return false;
    }

    out.eventId = JsonGetString(line, "event_id");
    if (out.eventId.empty()) out.eventId = FallbackEventId(line);
    reject.eventId = out.eventId;
    if (!m_seenEventIds.insert(out.eventId).second) {
        reject.riskCode = "OPENCLAW_DUPLICATE_EVENT";
        reject.detail = "event_id already consumed";
        return false;
    }
    AppendConsumedEventId(out.eventId);

    out.reqId = JsonGetString(line, "req_id");
    if (out.reqId.empty()) out.reqId = JsonGetString(line, "client_req_id");
    out.traceId = JsonGetString(line, "trace_id");
    out.source = JsonGetString(line, "source");
    out.strategy = JsonGetString(line, "strategy");
    if (out.strategy.empty()) out.strategy = "0dte_openclaw_signal";
    out.instrument = JsonGetString(line, "instrument");
    out.side = Upper(JsonGetString(line, "side"));
    out.reason = JsonGetString(line, "reason");
    out.tsMs = JsonGetLongLong(line, "ts_ms", 0);
    out.confidence = JsonGetDouble(line, "confidence", 0.0);
    out.reduceOnly = JsonGetBool(line, "reduceOnly", false);

    out.contract.symbol = Upper(JsonGetString(line, "symbol"));
    if (out.contract.symbol.empty()) {
        const std::string underlying = Upper(JsonGetString(line, "underlying"));
        out.contract.symbol = underlying;
    }
    out.contract.secType = Upper(JsonGetString(line, "secType"));
    out.contract.exchange = Upper(JsonGetString(line, "exchange"));
    out.contract.primaryExchange = JsonGetString(line, "primaryExchange");
    out.contract.currency = Upper(JsonGetString(line, "currency"));
    out.contract.lastTradeDateOrContractMonth = JsonGetString(line, "expiry");
    if (out.contract.lastTradeDateOrContractMonth.empty()) {
        out.contract.lastTradeDateOrContractMonth = JsonGetString(line, "lastTradeDateOrContractMonth");
    }
    out.contract.right = NormalizeRight(JsonGetString(line, "right"));
    out.contract.strike = JsonGetDouble(line, "strike", 0.0);
    out.contract.multiplier = JsonGetString(line, "multiplier");
    out.contract.tradingClass = JsonGetString(line, "tradingClass");
    out.contract.localSymbol = JsonGetString(line, "localSymbol");

    out.order.action = out.side;
    out.order.orderType = Upper(JsonGetString(line, "orderType"));
    out.order.totalQuantity = JsonGetDouble(line, "qty", 0.0);
    out.order.lmtPrice = JsonGetDouble(line, "limitPrice", JsonGetDouble(line, "price", 0.0));
    out.order.outsideRth = false;

    if (out.contract.exchange.empty()) out.contract.exchange = "SMART";
    if (out.contract.currency.empty()) out.contract.currency = "USD";
    if (out.contract.secType.empty()) out.contract.secType = "OPT";
    if (out.contract.multiplier.empty()) out.contract.multiplier = "100";
    if (out.contract.tradingClass.empty()) out.contract.tradingClass = out.contract.symbol;
    out.instrument = BuildIBAuthoritativeInstrumentIdentity(out.contract, out.instrument);

    return ValidateIntent(line, out, reject);
}

bool OpenClaw0DteBridgeConsumer::ValidateIntent(const std::string&, OpenClaw0DteIntent& out, OpenClaw0DteBridgeReject& reject) {
    if (out.tsMs > 0 && m_options.maxSignalAgeMs > 0 && (NowEpochMs() - out.tsMs) > m_options.maxSignalAgeMs) {
        reject.riskCode = "OPENCLAW_SIGNAL_STALE";
        reject.detail = "signal exceeded max age";
        return false;
    }
    if (out.contract.symbol.empty() || out.contract.secType != "OPT") {
        reject.riskCode = "OPENCLAW_CONTRACT_INVALID";
        reject.detail = "contract must be OPT with symbol";
        return false;
    }
    if (out.contract.lastTradeDateOrContractMonth.empty() || (out.contract.right != "C" && out.contract.right != "P") ||
        !(out.contract.strike > 0.0) || std::isnan(out.contract.strike) || std::isinf(out.contract.strike)) {
        reject.riskCode = "OPENCLAW_OPTION_FIELDS_INVALID";
        reject.detail = "missing expiry/right/strike";
        return false;
    }
    if (out.side != "BUY" && out.side != "SELL") {
        reject.riskCode = "OPENCLAW_SIDE_INVALID";
        reject.detail = "side must be BUY or SELL";
        return false;
    }
    if (out.side == "SELL" && out.reduceOnly && out.source != "openclaw.0dte_exit_manager") {
        reject.riskCode = "OPENCLAW_REDUCE_ONLY_SOURCE_INVALID";
        reject.detail = "reduce-only sells must come from exit manager";
        return false;
    }
    if (!m_options.allowSell && out.side != "BUY" && !out.reduceOnly) {
        reject.riskCode = "OPENCLAW_SELL_DISABLED";
        reject.detail = "sell intents disabled";
        return false;
    }
    if (out.side == "BUY" && EntryWindowClosedUtc(m_options.entryWindowUtc)) {
        reject.riskCode = "OPENCLAW_ENTRY_WINDOW_CLOSED";
        reject.detail = "new BUY entries disabled outside UTC entry window";
        return false;
    }
    if (out.side == "BUY" && NoNewEntriesDueUtc(m_options.noNewEntriesAfterUtc)) {
        reject.riskCode = "OPENCLAW_NO_NEW_ENTRIES_TIME";
        reject.detail = "new BUY entries disabled after UTC cutoff";
        return false;
    }
    if (out.order.orderType != "LMT") {
        reject.riskCode = "OPENCLAW_MARKET_ORDERS_DISABLED";
        reject.detail = "only LMT orders allowed";
        return false;
    }
    if (!(out.order.totalQuantity > 0.0) || out.order.totalQuantity > m_options.maxQty || std::fabs(out.order.totalQuantity - std::round(out.order.totalQuantity)) > 1e-9) {
        reject.riskCode = "OPENCLAW_QTY_LIMIT";
        reject.detail = "invalid or oversized qty";
        return false;
    }
    if (!(out.order.lmtPrice > 0.0) || std::isnan(out.order.lmtPrice) || std::isinf(out.order.lmtPrice)) {
        reject.riskCode = "OPENCLAW_LIMIT_PRICE_INVALID";
        reject.detail = "limit price must be finite and > 0";
        return false;
    }
    const double multiplier = std::atof(out.contract.multiplier.c_str());
    if (!(multiplier > 0.0)) {
        reject.riskCode = "OPENCLAW_MULTIPLIER_INVALID";
        reject.detail = "multiplier must be numeric and > 0";
        return false;
    }
    out.premiumAtRiskUsd = out.order.totalQuantity * out.order.lmtPrice * multiplier;
    if (out.side == "BUY" && out.premiumAtRiskUsd > m_options.maxPremiumUsd) {
        reject.riskCode = "OPENCLAW_PREMIUM_LIMIT";
        reject.detail = "premium at risk exceeds bridge limit";
        return false;
    }
    return true;
}
