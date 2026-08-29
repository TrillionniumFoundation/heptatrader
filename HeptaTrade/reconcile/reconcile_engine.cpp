#include "reconcile_engine.h"

#include "../oms_journal.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cctype>
#include <cstdio>
#include <ctime>
#include <fstream>
#include <set>
#include <sstream>
#include <unordered_map>

namespace {
std::string UtcNowIso8601() {
    using namespace std::chrono;
    auto now = system_clock::now();
    std::time_t t = system_clock::to_time_t(now);
    std::tm tmUtc{};
#ifdef _WIN32
    gmtime_s(&tmUtc, &t);
#else
    gmtime_r(&t, &tmUtc);
#endif
    char buf[32] = { 0 };
    std::snprintf(buf, sizeof(buf), "%04d-%02d-%02dT%02d:%02d:%02dZ",
        tmUtc.tm_year + 1900, tmUtc.tm_mon + 1, tmUtc.tm_mday,
        tmUtc.tm_hour, tmUtc.tm_min, tmUtc.tm_sec);
    return buf;
}

std::string EscapeJson(const std::string& s)
{
    std::string out;
    out.reserve(s.size() + 8);
    for (char ch : s)
    {
        switch (ch)
        {
        case '\\': out += "\\\\"; break;
        case '"': out += "\\\""; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default:
            if ((unsigned char)ch < 0x20) out += ' ';
            else out += ch;
            break;
        }
    }
    return out;
}

std::string Trim(const std::string& s)
{
    std::size_t b = 0;
    while (b < s.size() && std::isspace((unsigned char)s[b])) ++b;
    std::size_t e = s.size();
    while (e > b && std::isspace((unsigned char)s[e - 1])) --e;
    return s.substr(b, e - b);
}

std::vector<std::string> SplitCsv(const std::string& line)
{
    std::vector<std::string> out;
    std::stringstream ss(line);
    std::string token;
    while (std::getline(ss, token, ',')) out.push_back(Trim(token));
    return out;
}

bool IsTerminalStatus(const std::string& status)
{
    if (status == "Filled" || status == "Cancelled" || status == "ApiCancelled" || status == "Inactive") return true;
    if (status == "Rejected" || status == "rejected" || status == "blocked") return true;
    return false;
}

int SeverityRank(const std::string& s)
{
    if (s == "CRITICAL") return 3;
    if (s == "WARN") return 2;
    return 1;
}

std::string ToSeverity(int rank)
{
    if (rank >= 3) return "CRITICAL";
    if (rank == 2) return "WARN";
    return "INFO";
}

int ActionRank(const std::string& a)
{
    if (a == "block") return 4;
    if (a == "manual") return 3;
    if (a == "warn") return 2;
    return 1; // auto-fix
}

std::string ResolveActionFromReasonCode(const std::string& reasonCode, const std::string& severity)
{
    static const std::unordered_map<std::string, std::string> kReasonAction = {
        {"RISK_RECON_OPEN_ORDER_MISMATCH", "block"},
        {"RISK_RECON_POSITION_MISMATCH", "block"},
        {"RISK_RECON_CASH_MISMATCH", "warn"},
        {"RISK_RECON_CASH_UNAVAILABLE", "warn"},
        {"RISK_RECON_BROKER_OPEN_ORDERS_MISSING", "manual"},
        {"RISK_RECON_BROKER_POSITIONS_MISSING", "manual"},
        {"RISK_RECON_BROKER_CASH_MISSING", "manual"},
        {"RISK_RECON_BROKER_CASH_EMPTY", "manual"},
        {"RISK_RECON_BROKER_OPEN_ORDERS_BAD_LINE", "manual"},
        {"RISK_RECON_BROKER_POSITIONS_BAD_LINE", "manual"},
        {"RISK_RECON_ORDERS_MATCH", "auto-fix"},
        {"RISK_RECON_POSITIONS_MATCH", "auto-fix"},
        {"RISK_RECON_CASH_MATCH", "auto-fix"},
        {"RISK_RECON_OMS_REPLAY_SUMMARY", "auto-fix"}
    };

    std::unordered_map<std::string, std::string>::const_iterator it = kReasonAction.find(reasonCode);
    if (it != kReasonAction.end()) return it->second;

    if (severity == "CRITICAL") return "block";
    if (severity == "WARN") return "warn";
    return "auto-fix";
}

void PushCheck(std::vector<ReconcileCheckResult>& checks,
    const char* name,
    const char* severity,
    const char* reasonCode,
    const std::string& detail)
{
    ReconcileCheckResult c;
    c.name = name;
    c.severity = severity;
    c.reasonCode = reasonCode;
    c.action = ResolveActionFromReasonCode(c.reasonCode, c.severity);
    c.detail = detail;
    checks.push_back(c);
}

std::string JsonNumber(double v)
{
    char buf[64] = { 0 };
    std::snprintf(buf, sizeof(buf), "%.8f", v);
    return buf;
}
} // namespace

bool ReconcileEngine::LoadBrokerSnapshot(const ReconcileStartupInput& input, ReconcileBrokerSnapshot& out, std::vector<ReconcileCheckResult>& preloadChecks) const
{
    out = ReconcileBrokerSnapshot{};

    if (!input.brokerOpenOrdersPath.empty())
    {
        std::ifstream ifs(input.brokerOpenOrdersPath.c_str());
        if (ifs.is_open())
        {
            std::string line;
            int lineNo = 0;
            while (std::getline(ifs, line))
            {
                ++lineNo;
                line = Trim(line);
                if (line.empty() || line[0] == '#') continue;
                std::vector<std::string> cols = SplitCsv(line);
                if (cols.size() < 4)
                {
                    PushCheck(preloadChecks, "broker_open_orders_input", "WARN", "RISK_RECON_BROKER_OPEN_ORDERS_BAD_LINE",
                        "line=" + std::to_string(lineNo) + " expected=instrument,side,qty,status");
                    continue;
                }

                ReconcileBrokerOrder o;
                o.instrument = cols[0];
                o.side = cols[1];
                o.qty = std::atof(cols[2].c_str());
                o.status = cols[3];
                out.openOrders.push_back(o);
            }
        }
        else
        {
            PushCheck(preloadChecks, "broker_open_orders_input", "WARN", "RISK_RECON_BROKER_OPEN_ORDERS_MISSING",
                "missing=" + input.brokerOpenOrdersPath);
        }
    }

    if (!input.brokerPositionsPath.empty())
    {
        std::ifstream ifs(input.brokerPositionsPath.c_str());
        if (ifs.is_open())
        {
            std::string line;
            int lineNo = 0;
            while (std::getline(ifs, line))
            {
                ++lineNo;
                line = Trim(line);
                if (line.empty() || line[0] == '#') continue;
                std::vector<std::string> cols = SplitCsv(line);
                if (cols.size() < 2)
                {
                    PushCheck(preloadChecks, "broker_positions_input", "WARN", "RISK_RECON_BROKER_POSITIONS_BAD_LINE",
                        "line=" + std::to_string(lineNo) + " expected=instrument,net_qty");
                    continue;
                }
                out.netPositions[cols[0]] = std::atof(cols[1].c_str());
            }
        }
        else
        {
            PushCheck(preloadChecks, "broker_positions_input", "WARN", "RISK_RECON_BROKER_POSITIONS_MISSING",
                "missing=" + input.brokerPositionsPath);
        }
    }

    if (!input.brokerCashPath.empty())
    {
        std::ifstream ifs(input.brokerCashPath.c_str());
        if (ifs.is_open())
        {
            std::string line;
            while (std::getline(ifs, line))
            {
                line = Trim(line);
                if (line.empty() || line[0] == '#') continue;
                std::size_t eq = line.find('=');
                std::string v = (eq == std::string::npos) ? line : Trim(line.substr(eq + 1));
                out.cash = std::atof(v.c_str());
                out.cashKnown = true;
                break;
            }
            if (!out.cashKnown)
            {
                PushCheck(preloadChecks, "broker_cash_input", "WARN", "RISK_RECON_BROKER_CASH_EMPTY",
                    "empty=" + input.brokerCashPath);
            }
        }
        else
        {
            PushCheck(preloadChecks, "broker_cash_input", "WARN", "RISK_RECON_BROKER_CASH_MISSING",
                "missing=" + input.brokerCashPath);
        }
    }

    return true;
}

bool ReconcileEngine::LoadOmsReplay(const std::string& journalPath, OmsRecoverResult& out, std::string& err) const
{
    OmsJournal journal;
    if (!journal.Init(journalPath))
    {
        err = "open_oms_journal_failed:" + journalPath;
        return false;
    }

    out = OmsRecover::Replay(journal);
    return true;
}

bool ReconcileEngine::GenerateStartupReport(const ReconcileStartupInput& input, std::string& err, ReconcileStartupResult* outResult)
{
    std::vector<ReconcileCheckResult> checks;
    ReconcileBrokerSnapshot broker;
    LoadBrokerSnapshot(input, broker, checks);

    OmsRecoverResult oms;
    if (!LoadOmsReplay(input.omsJournalPath, oms, err))
    {
        return false;
    }

    int omsOpenCount = 0;
    std::unordered_map<std::string, double> omsNetByInstrument;
    for (std::unordered_map<long, OmsRecoveredOrder>::const_iterator it = oms.orders.begin(); it != oms.orders.end(); ++it)
    {
        const OmsRecoveredOrder& o = it->second;
        if (o.placeSent && !o.rejected && !IsTerminalStatus(o.status))
        {
            ++omsOpenCount;
        }

        if (!o.instrument.empty() && o.qty > 0.0)
        {
            const double sign = (o.side == "SELL") ? -1.0 : 1.0;
            omsNetByInstrument[o.instrument] += sign * o.qty;
        }
    }

    const int brokerOpenCount = (int)broker.openOrders.size();
    if (brokerOpenCount == omsOpenCount)
    {
        PushCheck(checks, "orders", "INFO", "RISK_RECON_ORDERS_MATCH",
            "broker_open=" + std::to_string(brokerOpenCount) + " oms_open=" + std::to_string(omsOpenCount));
    }
    else
    {
        PushCheck(checks, "orders", "CRITICAL", "RISK_RECON_OPEN_ORDER_MISMATCH",
            "broker_open=" + std::to_string(brokerOpenCount) + " oms_open=" + std::to_string(omsOpenCount));
    }

    std::set<std::string> symbols;
    for (std::unordered_map<std::string, double>::const_iterator it = broker.netPositions.begin(); it != broker.netPositions.end(); ++it) symbols.insert(it->first);
    for (std::unordered_map<std::string, double>::const_iterator it = omsNetByInstrument.begin(); it != omsNetByInstrument.end(); ++it) symbols.insert(it->first);

    bool posMismatch = false;
    std::ostringstream posDetail;
    for (std::set<std::string>::const_iterator it = symbols.begin(); it != symbols.end(); ++it)
    {
        const std::string& symbol = *it;
        double b = 0.0;
        double o = 0.0;
        std::unordered_map<std::string, double>::const_iterator bit = broker.netPositions.find(symbol);
        if (bit != broker.netPositions.end()) b = bit->second;
        std::unordered_map<std::string, double>::const_iterator oit = omsNetByInstrument.find(symbol);
        if (oit != omsNetByInstrument.end()) o = oit->second;

        if (std::fabs(b - o) > 1e-6)
        {
            posMismatch = true;
            posDetail << symbol << "(broker=" << b << ",oms=" << o << ") ";
        }
    }

    if (!posMismatch)
    {
        PushCheck(checks, "positions", "INFO", "RISK_RECON_POSITIONS_MATCH", "all_symbols_match");
    }
    else
    {
        PushCheck(checks, "positions", "CRITICAL", "RISK_RECON_POSITION_MISMATCH", posDetail.str());
    }

    if (broker.cashKnown && input.omsCashKnown)
    {
        const double diff = std::fabs(broker.cash - input.omsCash);
        if (diff <= 1e-6)
        {
            PushCheck(checks, "cash", "INFO", "RISK_RECON_CASH_MATCH",
                "broker_cash=" + JsonNumber(broker.cash) + " oms_cash=" + JsonNumber(input.omsCash));
        }
        else
        {
            PushCheck(checks, "cash", "WARN", "RISK_RECON_CASH_MISMATCH",
                "broker_cash=" + JsonNumber(broker.cash) + " oms_cash=" + JsonNumber(input.omsCash) + " diff=" + JsonNumber(diff));
        }
    }
    else
    {
        PushCheck(checks, "cash", "INFO", "RISK_RECON_CASH_UNAVAILABLE", "cash_compare_skipped");
    }

    PushCheck(checks, "oms_replay", "INFO", "RISK_RECON_OMS_REPLAY_SUMMARY",
        "events=" + std::to_string(oms.totalRead) + " dedup_skipped=" + std::to_string(oms.dedupSkipped) + " orders=" + std::to_string((int)oms.orders.size()));

    int maxRank = 1;
    int maxAction = 1;
    for (std::size_t i = 0; i < checks.size(); ++i)
    {
        maxRank = std::max(maxRank, SeverityRank(checks[i].severity));
        maxAction = std::max(maxAction, ActionRank(checks[i].action));
    }

    ReconcileStartupResult result;
    result.checks = checks;
    result.overallSeverity = ToSeverity(maxRank);
    result.hasCritical = (maxRank >= 3);
    if (maxAction >= 4) result.startupAction = "block";
    else if (maxAction >= 3) result.startupAction = "manual";
    else if (maxAction >= 2) result.startupAction = "warn";
    else result.startupAction = "auto-fix";
    result.blockExitCode = (result.startupAction == "block") ? input.criticalBlockExitCode : 0;

    std::ofstream ofs(input.outputPath.c_str(), std::ios::out | std::ios::trunc);
    if (!ofs.is_open()) {
        err = "open_report_failed:" + input.outputPath;
        return false;
    }

    ofs << "{\n";
    ofs << "  \"ts\": \"" << UtcNowIso8601() << "\",\n";
    ofs << "  \"report_type\": \"reconcile_startup\",\n";
    ofs << "  \"status\": \"" << result.overallSeverity << "\",\n";
    ofs << "  \"venue\": \"" << EscapeJson(input.venue) << "\",\n";
    ofs << "  \"account\": \"" << EscapeJson(input.account) << "\",\n";
    ofs << "  \"oms_journal\": \"" << EscapeJson(input.omsJournalPath) << "\",\n";
    ofs << "  \"broker_inputs\": {\n";
    ofs << "    \"open_orders\": \"" << EscapeJson(input.brokerOpenOrdersPath) << "\",\n";
    ofs << "    \"positions\": \"" << EscapeJson(input.brokerPositionsPath) << "\",\n";
    ofs << "    \"cash\": \"" << EscapeJson(input.brokerCashPath) << "\"\n";
    ofs << "  },\n";
    ofs << "  \"rules_version\": \"RECONCILE-RULES-v3\",\n";
    ofs << "  \"startup_action\": {\n";
    ofs << "    \"decision\": \"" << result.startupAction << "\",\n";
    ofs << "    \"has_critical\": " << (result.hasCritical ? "true" : "false") << ",\n";
    ofs << "    \"block_exit_code\": " << result.blockExitCode << "\n";
    ofs << "  },\n";
    ofs << "  \"checks\": [\n";
    for (std::size_t i = 0; i < result.checks.size(); ++i)
    {
        const ReconcileCheckResult& c = result.checks[i];
        ofs << "    {\"name\":\"" << EscapeJson(c.name)
            << "\",\"severity\":\"" << EscapeJson(c.severity)
            << "\",\"reason_code\":\"" << EscapeJson(c.reasonCode)
            << "\",\"action\":\"" << EscapeJson(c.action)
            << "\",\"detail\":\"" << EscapeJson(c.detail)
            << "\"}";
        if (i + 1 < result.checks.size()) ofs << ",";
        ofs << "\n";
    }
    ofs << "  ]\n";
    ofs << "}\n";

    if (outResult) *outResult = result;
    return true;
}
