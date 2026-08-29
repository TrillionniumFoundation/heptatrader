#pragma once

#include "../execution/trading_contract.h"

#include <functional>
#include <map>
#include <mutex>
#include <set>
#include <string>
#include <vector>

struct SimulatedOrderEvent
{
    long orderId = -1;
    std::string instrument;
    std::string side;
    std::string status;
    double filledQuantity = 0.0;
    double remainingQuantity = 0.0;
    double averageFillPrice = 0.0;
};

struct SimulatedRecoveryAuditSnapshot
{
    std::uint64_t connectionEpoch = 1;
    std::uint64_t generation = 0;
    bool complete = false;
    std::set<long> activeOrderIds;
    std::map<std::string, long> activeCorrelations;
    std::map<std::string, long> terminalCorrelations;
    std::map<long, std::string> terminalStatuses;
    std::set<long> executionOrderIds;
};

class DeterministicExecutionVenue
{
public:
    typedef std::function<void(const SimulatedOrderEvent&)> EventSink;

    DeterministicExecutionVenue();
    void SetEventSink(const EventSink& sink);
    void SetQuote(const std::string& instrument, double bid, double ask);
    void SetQuoteObserved(const std::string& instrument, double bid, double ask,
                          std::uint64_t observedAtMs, std::uint64_t staleAfterMs);
    bool GetQuote(const std::string& instrument, double& bid, double& ask) const;
    MarketQuoteSnapshot GetQuoteSnapshot(const std::string& instrument,
                                         std::uint64_t nowMs) const;

    bool PlaceOrder(const InstrumentRef& contract, const OrderIntent& order, long* orderId);
    bool PlaceOrderCorrelated(const InstrumentRef& contract, const OrderIntent& order,
                              const std::string& correlationId, long* orderId);
    bool CanCancelOrder(long orderId, std::string* reason) const;
    bool CancelOrder(long orderId);
    std::string LastRejectReason() const;
    void RestoreNextOrderIdAtLeast(long nextOrderId);
    void Process();
    double Position(const std::string& instrument) const;
    std::map<std::string, double> Positions() const;
    std::set<long> ActiveOrderIds() const;
    std::map<std::string, long> ActiveOrderCorrelations() const;
    std::map<std::string, long> TerminalOrderCorrelations() const;
    std::map<long, std::string> TerminalOrderStatuses() const;
    std::set<long> ExecutionOrderIds() const;
    SimulatedRecoveryAuditSnapshot RecoveryAuditSnapshot() const;

private:
    struct Quote
    {
        double bid = 0.0;
        double ask = 0.0;
        std::uint64_t observedAtMs = 0;
        std::uint64_t staleAfterMs = 0;
    };
    struct Order
    {
        long id = -1;
        std::string instrument;
        OrderIntent request;
        bool submitted = false;
        bool cancelRequested = false;
        bool terminal = false;
        std::string terminalStatus;
        std::string correlationId;
    };
    static std::string Instrument(const InstrumentRef& contract);

private:
    mutable std::mutex m_mutex;
    long m_nextOrderId;
    std::uint64_t m_generation;
    std::map<std::string, Quote> m_quotes;
    std::map<long, Order> m_orders;
    std::map<std::string, double> m_positions;
    EventSink m_sink;
    std::string m_lastRejectReason;
};
