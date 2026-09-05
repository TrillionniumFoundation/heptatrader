#pragma once
#include <string>
#include <unordered_map>
#include <vector>
#include <queue>
#include <ctime>
#include "oms_journal.h"
#include "heptaBasicCout.h"
#include "heptaTradeCommonDefine.h"

struct WatchdogOrder {
    std::string venue;       // "IB" or "CTP"
    long orderId;            // For IB
    std::string orderRef;    // For CTP
    std::string instrument;
    std::string side;
    std::string strategy;

    long long sendTimeMs;
    long long cancelTimeMs;
    long long orderTimeoutMs = 15000;
    bool cancelSent;
};

class OrderWatchdog {
public:
    OrderWatchdog(OmsJournal& oms, heptaBasicCout& log);

    // Initialize config from env (HEPTA_ORDER_TIMEOUT_SEC, HEPTA_CANCEL_TIMEOUT_SEC, HEPTA_WATCHDOG_POLL_MAX_DUE)
    void InitFromEnv();

    // Track a new order
    void TrackOrder(const std::string& venue, long orderId, const std::string& orderRef,
                    const std::string& instrument, const std::string& side, const std::string& strategy);

    // Mark an order as reaching a final state
    void OnOrderStatus(const std::string& venue, long orderId, const std::string& orderRef, const std::string& status);

    // Poll to check timeouts and perform actions
    void Poll(class HeptaIBGatewayAdapter* ibAdapter, class heptaFtdTradeSpi* ctpAdapter);

private:
    struct DueItem {
        long long dueMs;
        bool isIb;
        long orderId;
        std::string refKey;
        int stage; // 0: order timeout -> cancel, 1: cancel timeout -> fatal/alarm
        unsigned long long token;
    };

    struct DueItemGreater {
        bool operator()(const DueItem& a, const DueItem& b) const {
            return a.dueMs > b.dueMs;
        }
    };

    long long m_orderTimeoutMs = 15000;
    long long m_cancelTimeoutMs = 5000;

    // Burst protection: max due items handled per Poll call.
    // 0 means unlimited (compatible with historical behavior).
    size_t m_pollMaxDue = 0;

    // Minimal internal observability counters for maintenance/aggregation.
    unsigned long long m_pollProcessedTotal = 0;
    unsigned long long m_pollCappedTotal = 0;

    // High-frequency IB path: structured key (orderId), avoids string key churn.
    std::unordered_map<long, WatchdogOrder> m_trackedIbOrders;
    // Non-IB fallback path keyed by orderRef (lower frequency).
    std::unordered_map<std::string, WatchdogOrder> m_trackedRefOrders;

    // Expiration scheduler: Poll only handles due candidates (no full-map scans).
    std::priority_queue<DueItem, std::vector<DueItem>, DueItemGreater> m_dueQueue;
    std::unordered_map<long, unsigned long long> m_ibTokens;
    std::unordered_map<std::string, unsigned long long> m_refTokens;
    // CTP cancel fast path: orderRef -> active order key (for GetActiveOrders map lookup).
    std::unordered_map<std::string, heptaActiveOrderKey> m_ctpOrderRefToActiveKey;
    unsigned long long m_nextToken = 1;

    OmsJournal& m_oms;
    heptaBasicCout& m_log;

    static bool IsIbVenue(const std::string& venue);
    static bool IsFinalStatus(const std::string& venue, const std::string& status);

    static std::string BuildFallbackKey(const std::string& venue, long orderId, const std::string& orderRef);

    unsigned long long NextToken();
    void ScheduleIb(long orderId, int stage, long long dueMs, unsigned long long token);
    void ScheduleRef(const std::string& key, int stage, long long dueMs, unsigned long long token);
    void EraseIbOrder(long orderId);
    void EraseRefOrder(const std::string& key);
    bool CancelCtpOrderByRef(heptaFtdTradeSpi* ctpAdapter, WatchdogOrder& o);
};
