#pragma once

#include "../adapter_ib/ib_api_wrapper.h"

#include "ib_authoritative_order_projector.h"

#include <cstdint>
#include <map>
#include <mutex>
#include <string>
#include <vector>

struct IBAuthoritativeOpenOrderCompletion
{
    bool accepted = false;
    std::string reasonCode;
    std::vector<AuthoritativeActiveOrder> orders;
};

class IBAuthoritativeOpenOrderConsumer
{
public:
    IBAuthoritativeOpenOrderConsumer(AuthoritativeTradingSnapshotStore& store,
                                     const std::string& configuredAccount);

    bool ConfigureAccount(const std::string& configuredAccount);

    void BeginRefresh(std::uint64_t generation);
    void AbortRefresh(std::uint64_t generation);
    bool IsRefreshInFlight() const;

    IBAuthoritativeOrderProjectionResult ProjectPlaced(const IbPlaceOrderCommand& command,
                                                       long orderId,
                                                       std::uint64_t observedAtMs);
    IBAuthoritativeOrderProjectionResult ProjectCancelSent(long orderId,
                                                           std::uint64_t observedAtMs);
    IBAuthoritativeOrderProjectionResult ProjectOrderStatus(long orderId,
                                                            const std::string& status,
                                                            double filledQuantity,
                                                            double remainingQuantity,
                                                            double averageFillPrice,
                                                            bool executionEvidence,
                                                            std::uint64_t observedAtMs);
    IBAuthoritativeOrderProjectionResult ConsumeOpenOrder(const IBEvent& event,
                                                          std::uint64_t observedAtMs);
    IBAuthoritativeOpenOrderCompletion CompleteRefresh(std::uint64_t generation,
                                                       std::uint64_t observedAtMs);

private:
    void ApplyProjectionLocked(long orderId,
                               const IBAuthoritativeOrderProjectionResult& projection);

private:
    AuthoritativeTradingSnapshotStore& m_store;
    IBAuthoritativeOrderProjector m_projector;
    std::string m_configuredAccount;
    mutable std::mutex m_mutex;
    std::uint64_t m_generation = 0;
    bool m_rejected = false;
    std::string m_rejectReason;
    std::map<long, AuthoritativeActiveOrder> m_orders;
};
