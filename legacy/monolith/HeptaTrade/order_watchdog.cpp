#include "order_watchdog.h"
#include <cstdlib>
#include <algorithm>
#include <cstdio>
#include "adapter_ib/ib_gateway_adapter.h"
#include "heptaFtdTradeSpi.h"

OrderWatchdog::OrderWatchdog(OmsJournal& oms, heptaBasicCout& log)
    : m_oms(oms), m_log(log) {
    InitFromEnv();
}

void OrderWatchdog::InitFromEnv() {
    const char* envOrder = std::getenv("HEPTA_ORDER_TIMEOUT_SEC");
    if (envOrder) {
        int sec = std::atoi(envOrder);
        if (sec > 0) m_orderTimeoutMs = sec * 1000;
    }
    const char* envCancel = std::getenv("HEPTA_CANCEL_TIMEOUT_SEC");
    if (envCancel) {
        int sec = std::atoi(envCancel);
        if (sec > 0) m_cancelTimeoutMs = sec * 1000;
    }
    const char* envPollMaxDue = std::getenv("HEPTA_WATCHDOG_POLL_MAX_DUE");
    if (envPollMaxDue) {
        int val = std::atoi(envPollMaxDue);
        if (val >= 0) m_pollMaxDue = static_cast<size_t>(val);
    }
}

bool OrderWatchdog::IsIbVenue(const std::string& venue) {
    return venue == "IB" || venue == "ib";
}

bool OrderWatchdog::IsFinalStatus(const std::string& venue, const std::string& status) {
    if (IsIbVenue(venue)) {
        return (status == "Filled" || status == "Cancelled" || status == "ApiCancelled" || status == "Inactive" || status == "Rejected");
    }
    return (status == "Filled" || status == "Cancelled" || status == "Rejected");
}

std::string OrderWatchdog::BuildFallbackKey(const std::string& venue, long orderId, const std::string& orderRef) {
    if (!orderRef.empty()) return orderRef;

    const std::string orderIdStr = std::to_string(orderId);
    std::string key;
    key.reserve(venue.size() + 1 + orderIdStr.size());
    key.append(venue);
    key.push_back('_');
    key.append(orderIdStr);
    return key;
}

unsigned long long OrderWatchdog::NextToken() {
    return m_nextToken++;
}

void OrderWatchdog::ScheduleIb(long orderId, int stage, long long dueMs, unsigned long long token) {
    DueItem item;
    item.dueMs = dueMs;
    item.isIb = true;
    item.orderId = orderId;
    item.stage = stage;
    item.token = token;
    m_dueQueue.push(item);
}

void OrderWatchdog::ScheduleRef(const std::string& key, int stage, long long dueMs, unsigned long long token) {
    DueItem item;
    item.dueMs = dueMs;
    item.isIb = false;
    item.orderId = 0;
    item.refKey = key;
    item.stage = stage;
    item.token = token;
    m_dueQueue.push(item);
}

void OrderWatchdog::EraseIbOrder(long orderId) {
    m_trackedIbOrders.erase(orderId);
    m_ibTokens.erase(orderId);
}

void OrderWatchdog::EraseRefOrder(const std::string& key) {
    std::unordered_map<std::string, WatchdogOrder>::iterator ordIt = m_trackedRefOrders.find(key);
    if (ordIt != m_trackedRefOrders.end()) {
        if (ordIt->second.venue == "CTP" && !ordIt->second.orderRef.empty()) {
            m_ctpOrderRefToActiveKey.erase(ordIt->second.orderRef);
        }
        m_trackedRefOrders.erase(ordIt);
    }
    m_refTokens.erase(key);
}

bool OrderWatchdog::CancelCtpOrderByRef(heptaFtdTradeSpi* ctpAdapter, WatchdogOrder& o) {
    if (!ctpAdapter || o.orderRef.empty()) return false;

    std::unordered_map<std::string, heptaActiveOrderKey>::iterator idxIt = m_ctpOrderRefToActiveKey.find(o.orderRef);

    std::map<heptaActiveOrderKey, heptaOrderPtr> active = ctpAdapter->GetActiveOrders(false);

    if (idxIt != m_ctpOrderRefToActiveKey.end()) {
        std::map<heptaActiveOrderKey, heptaOrderPtr>::iterator fastIt = active.find(idxIt->second);
        if (fastIt != active.end() && fastIt->second && fastIt->second->OrderRef && std::string(fastIt->second->OrderRef) == o.orderRef) {
            ctpAdapter->CancelOrder(fastIt->second);
            return true;
        }
        m_ctpOrderRefToActiveKey.erase(idxIt);
    }

    for (std::map<heptaActiveOrderKey, heptaOrderPtr>::iterator actIt = active.begin(); actIt != active.end(); ++actIt) {
        if (actIt->second && actIt->second->OrderRef && std::string(actIt->second->OrderRef) == o.orderRef) {
            m_ctpOrderRefToActiveKey.erase(o.orderRef);
            m_ctpOrderRefToActiveKey.emplace(o.orderRef, actIt->first);
            ctpAdapter->CancelOrder(actIt->second);
            return true;
        }
    }

    return false;
}

void OrderWatchdog::TrackOrder(const std::string& venue, long orderId, const std::string& orderRef,
                               const std::string& instrument, const std::string& side, const std::string& strategy) {
    WatchdogOrder o;
    o.venue = venue;
    o.orderId = orderId;
    o.orderRef = orderRef;
    o.instrument = instrument;
    o.side = side;
    o.strategy = strategy;
    o.sendTimeMs = OmsJournal::NowEpochMs();
    o.cancelTimeMs = 0;
    o.cancelSent = false;

    long long orderTimeoutMs = m_orderTimeoutMs;
    if (strategy == "fx_market_making") {
        const char* envMmMs = std::getenv("HEPTA_IB_MM_ORDER_TIMEOUT_MS");
        if (envMmMs && envMmMs[0]) {
            const int v = std::atoi(envMmMs);
            if (v > 0) orderTimeoutMs = v;
        } else {
            const char* envMmSec = std::getenv("HEPTA_IB_MM_ORDER_TIMEOUT_SEC");
            if (envMmSec && envMmSec[0]) {
                const int v = std::atoi(envMmSec);
                if (v > 0) orderTimeoutMs = static_cast<long long>(v) * 1000LL;
            }
        }
    }
    o.orderTimeoutMs = orderTimeoutMs;

    const long long orderDueMs = o.sendTimeMs + orderTimeoutMs + 1; // keep strict ">" semantics

    if (IsIbVenue(venue) && orderId > 0) {
        m_trackedIbOrders[orderId] = o;
        const unsigned long long token = NextToken();
        m_ibTokens[orderId] = token;
        ScheduleIb(orderId, 0, orderDueMs, token);
    }
    else {
        const std::string key = BuildFallbackKey(venue, orderId, orderRef);
        m_trackedRefOrders[key] = o;
        const unsigned long long token = NextToken();
        m_refTokens[key] = token;
        ScheduleRef(key, 0, orderDueMs, token);
    }
}

void OrderWatchdog::OnOrderStatus(const std::string& venue, long orderId, const std::string& orderRef, const std::string& status) {
    if (!IsFinalStatus(venue, status)) return;

    if (IsIbVenue(venue) && orderId > 0) {
        EraseIbOrder(orderId);
    }
    else {
        const std::string key = BuildFallbackKey(venue, orderId, orderRef);
        EraseRefOrder(key);
        if ((venue == "CTP" || venue == "ctp") && !orderRef.empty()) {
            m_ctpOrderRefToActiveKey.erase(orderRef);
        }
    }
}

void OrderWatchdog::Poll(HeptaIBGatewayAdapter* ibAdapter, heptaFtdTradeSpi* ctpAdapter) {
    const long long nowMs = OmsJournal::NowEpochMs();
    size_t processedThisPoll = 0;

    while (!m_dueQueue.empty() && m_dueQueue.top().dueMs <= nowMs) {
        if (m_pollMaxDue > 0 && processedThisPoll >= m_pollMaxDue) {
            ++m_pollCappedTotal;
            break;
        }

        const DueItem item = m_dueQueue.top();
        m_dueQueue.pop();
        ++processedThisPoll;

        if (item.isIb) {
            std::unordered_map<long, unsigned long long>::iterator tokIt = m_ibTokens.find(item.orderId);
            if (tokIt == m_ibTokens.end() || tokIt->second != item.token) continue;

            std::unordered_map<long, WatchdogOrder>::iterator ordIt = m_trackedIbOrders.find(item.orderId);
            if (ordIt == m_trackedIbOrders.end()) {
                m_ibTokens.erase(tokIt);
                continue;
            }

            WatchdogOrder& o = ordIt->second;

            if (item.stage == 0) {
                if (o.cancelSent || nowMs - o.sendTimeMs <= o.orderTimeoutMs) continue;

                o.cancelSent = true;
                o.cancelTimeMs = nowMs;

                char ibKeyLabel[32] = { 0 };
                std::snprintf(ibKeyLabel, sizeof(ibKeyLabel), "IB_%ld", item.orderId);
                m_log.AddLog("[Watchdog] Order timeout, cancelling %s order: %s", o.venue.c_str(), ibKeyLabel);

                OmsJournalEvent evt;
                evt.eventType = "watchdog_timeout";
                evt.tsMs = nowMs;
                evt.orderId = o.orderId;
                evt.instrument = o.instrument;
                evt.side = o.side;
                evt.venue = o.venue;
                evt.strategy = o.strategy;
                evt.reason = "watchdog_timeout";
                m_oms.Append(evt);

                if (ibAdapter) {
                    std::string suppressReason;
                    if (!ibAdapter->CanCancelOrder(o.orderId, &suppressReason)) {
                        m_log.AddLog("[Watchdog] Cancel suppressed for IB order: %s reason=%s", ibKeyLabel, suppressReason.c_str());
                        OmsJournalEvent suppressEvt;
                        suppressEvt.eventType = "watchdog_cancel_suppressed";
                        suppressEvt.tsMs = nowMs;
                        suppressEvt.orderId = o.orderId;
                        suppressEvt.instrument = o.instrument;
                        suppressEvt.side = o.side;
                        suppressEvt.venue = o.venue;
                        suppressEvt.strategy = o.strategy;
                        suppressEvt.reason = suppressReason;
                        m_oms.Append(suppressEvt);
                        EraseIbOrder(item.orderId);
                        continue;
                    }
                    ibAdapter->CancelOrder(o.orderId);
                }

                OmsJournalEvent cancelEvt;
                cancelEvt.eventType = "watchdog_cancel_sent";
                cancelEvt.tsMs = nowMs;
                cancelEvt.orderId = o.orderId;
                cancelEvt.instrument = o.instrument;
                cancelEvt.side = o.side;
                cancelEvt.venue = o.venue;
                cancelEvt.strategy = o.strategy;
                m_oms.Append(cancelEvt);

                const unsigned long long nextToken = NextToken();
                m_ibTokens[item.orderId] = nextToken;
                ScheduleIb(item.orderId, 1, o.cancelTimeMs + m_cancelTimeoutMs + 1, nextToken);
            }
            else {
                if (!o.cancelSent || nowMs - o.cancelTimeMs <= m_cancelTimeoutMs) continue;

                char ibKeyLabel[32] = { 0 };
                std::snprintf(ibKeyLabel, sizeof(ibKeyLabel), "IB_%ld", item.orderId);

                if (ibAdapter) {
                    std::string suppressReason;
                    if (!ibAdapter->CanCancelOrder(o.orderId, &suppressReason)) {
                        m_log.AddLog("[Watchdog] Cancel timeout downgraded for IB order: %s reason=%s", ibKeyLabel, suppressReason.c_str());
                        OmsJournalEvent evt;
                        evt.eventType = "watchdog_cancel_timeout_downgraded";
                        evt.tsMs = nowMs;
                        evt.orderId = o.orderId;
                        evt.instrument = o.instrument;
                        evt.side = o.side;
                        evt.venue = o.venue;
                        evt.strategy = o.strategy;
                        evt.reason = suppressReason;
                        m_oms.Append(evt);
                        EraseIbOrder(item.orderId);
                        continue;
                    }
                }

                m_log.AddLog("[Watchdog] FATAL ALARM: Cancel not confirmed for %s order: %s after %lld ms",
                    o.venue.c_str(), ibKeyLabel, m_cancelTimeoutMs);

                OmsJournalEvent evt;
                evt.eventType = "watchdog_critical";
                evt.tsMs = nowMs;
                evt.orderId = o.orderId;
                evt.instrument = o.instrument;
                evt.side = o.side;
                evt.venue = o.venue;
                evt.strategy = o.strategy;
                evt.reason = "cancel_timeout";
                m_oms.Append(evt);

                EraseIbOrder(item.orderId);
            }
        }
        else {
            std::unordered_map<std::string, unsigned long long>::iterator tokIt = m_refTokens.find(item.refKey);
            if (tokIt == m_refTokens.end() || tokIt->second != item.token) continue;

            std::unordered_map<std::string, WatchdogOrder>::iterator ordIt = m_trackedRefOrders.find(item.refKey);
            if (ordIt == m_trackedRefOrders.end()) {
                m_refTokens.erase(tokIt);
                continue;
            }

            WatchdogOrder& o = ordIt->second;
            const std::string& keyLabel = item.refKey;

            if (item.stage == 0) {
                if (o.cancelSent || nowMs - o.sendTimeMs <= o.orderTimeoutMs) continue;

                o.cancelSent = true;
                o.cancelTimeMs = nowMs;

                m_log.AddLog("[Watchdog] Order timeout, cancelling %s order: %s", o.venue.c_str(), keyLabel.c_str());

                OmsJournalEvent evt;
                evt.eventType = "watchdog_timeout";
                evt.tsMs = nowMs;
                evt.orderId = o.orderId;
                evt.instrument = o.instrument;
                evt.side = o.side;
                evt.venue = o.venue;
                evt.strategy = o.strategy;
                evt.reason = "watchdog_timeout";
                m_oms.Append(evt);

                if (o.venue == "CTP") {
                    CancelCtpOrderByRef(ctpAdapter, o);
                }

                OmsJournalEvent cancelEvt;
                cancelEvt.eventType = "watchdog_cancel_sent";
                cancelEvt.tsMs = nowMs;
                cancelEvt.orderId = o.orderId;
                cancelEvt.instrument = o.instrument;
                cancelEvt.side = o.side;
                cancelEvt.venue = o.venue;
                cancelEvt.strategy = o.strategy;
                m_oms.Append(cancelEvt);

                const unsigned long long nextToken = NextToken();
                m_refTokens[item.refKey] = nextToken;
                ScheduleRef(item.refKey, 1, o.cancelTimeMs + m_cancelTimeoutMs + 1, nextToken);
            }
            else {
                if (!o.cancelSent || nowMs - o.cancelTimeMs <= m_cancelTimeoutMs) continue;

                m_log.AddLog("[Watchdog] FATAL ALARM: Cancel not confirmed for %s order: %s after %lld ms",
                    o.venue.c_str(), keyLabel.c_str(), m_cancelTimeoutMs);

                OmsJournalEvent evt;
                evt.eventType = "watchdog_critical";
                evt.tsMs = nowMs;
                evt.orderId = o.orderId;
                evt.instrument = o.instrument;
                evt.side = o.side;
                evt.venue = o.venue;
                evt.strategy = o.strategy;
                evt.reason = "cancel_timeout";
                m_oms.Append(evt);

                EraseRefOrder(item.refKey);
            }
        }
    }

    m_pollProcessedTotal += processedThisPoll;
}


