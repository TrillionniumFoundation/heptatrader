#pragma once

#include "authoritative_trading_snapshot_store.h"
#include "../adapter_ib/ib_api_wrapper.h"
#include "../execution/execution_coordinator.h"

#include <cstdint>
#include <string>

enum class IBAuthoritativeOrderProjectionStatus
{
    Applied = 0,
    Ignored,
    Missing,
    Rejected
};

struct IBAuthoritativeOrderProjectionResult
{
    IBAuthoritativeOrderProjectionStatus status = IBAuthoritativeOrderProjectionStatus::Ignored;
    std::string reasonCode;
    bool hasOrder = false;
    AuthoritativeActiveOrder order;
};

class IBAuthoritativeOrderProjector
{
public:
    explicit IBAuthoritativeOrderProjector(AuthoritativeTradingSnapshotStore& store);

    IBAuthoritativeOrderProjectionResult ProjectPlaced(const IbPlaceOrderCommand& command,
                                                       long orderId,
                                                       std::uint64_t observedAtMs);
    IBAuthoritativeOrderProjectionResult ProjectCancelSent(long orderId,
                                                           std::uint64_t observedAtMs);
    IBAuthoritativeOrderProjectionResult ProjectOpenOrder(const IBEvent& event,
                                                          const std::string& defaultAccount,
                                                          std::uint64_t observedAtMs);
    IBAuthoritativeOrderProjectionResult ProjectOrderStatus(long orderId,
                                                            const std::string& status,
                                                            double filledQuantity,
                                                            double remainingQuantity,
                                                            double averageFillPrice,
                                                            bool executionEvidence,
                                                            std::uint64_t observedAtMs);

private:
    static std::string NormalizeInstrument(const std::string& value);
    static std::string InstrumentFromEvent(const IBEvent& event);
    static bool BuildOrder(const std::string& account,
                           const std::string& instrument,
                           const IBOrderLite& request,
                           long orderId,
                           const std::string& status,
                           AuthoritativeActiveOrder& out,
                           std::string& reason);
    static bool IsTerminalStatus(const std::string& status);
    static bool ApplyActiveStatus(const std::string& status,
                                  double filledQuantity,
                                  AuthoritativeActiveOrderStatus& out);
    static IBAuthoritativeOrderProjectionResult FromWrite(
        const AuthoritativeSnapshotWriteResult& write,
        const AuthoritativeActiveOrder* order = nullptr);

private:
    AuthoritativeTradingSnapshotStore& m_store;
};
