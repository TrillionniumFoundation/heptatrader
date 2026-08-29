#pragma once

#include "../oms_recover.h"

#include <string>
#include <unordered_map>
#include <vector>

struct ReconcileBrokerOrder {
    std::string instrument;
    std::string side;
    double qty = 0.0;
    std::string status;
};

struct ReconcileBrokerSnapshot {
    std::vector<ReconcileBrokerOrder> openOrders;
    std::unordered_map<std::string, double> netPositions;
    bool cashKnown = false;
    double cash = 0.0;
};

struct ReconcileStartupInput {
    std::string venue;
    std::string account;
    std::string omsJournalPath;
    std::string outputPath;

    // Broker startup snapshot files (CSV/text)
    std::string brokerOpenOrdersPath; // instrument,side,qty,status
    std::string brokerPositionsPath;  // instrument,net_qty
    std::string brokerCashPath;       // a single number or key=value lines (cash/netliq/available)

    // Optional OMS side cash hint (e.g. from risk bootstrap env)
    bool omsCashKnown = false;
    double omsCash = 0.0;

    int criticalBlockExitCode = -16;
};

struct ReconcileCheckResult {
    std::string name;
    std::string severity;     // INFO/WARN/CRITICAL
    std::string reasonCode;
    std::string action;       // block/warn/auto-fix/manual
    std::string detail;
};

struct ReconcileStartupResult {
    std::string overallSeverity = "INFO";
    bool hasCritical = false;
    std::string startupAction = "auto-fix"; // block/warn/auto-fix/manual
    int blockExitCode = 0;
    std::vector<ReconcileCheckResult> checks;
};

class ReconcileEngine {
public:
    bool GenerateStartupReport(const ReconcileStartupInput& input, std::string& err, ReconcileStartupResult* outResult = nullptr);

private:
    bool LoadBrokerSnapshot(const ReconcileStartupInput& input, ReconcileBrokerSnapshot& out, std::vector<ReconcileCheckResult>& preloadChecks) const;
    bool LoadOmsReplay(const std::string& journalPath, OmsRecoverResult& out, std::string& err) const;
};
