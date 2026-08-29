#pragma once

#include <cstdint>
#include <string>
#include <unordered_map>

// Pure process-local projection of IB order lifecycle evidence. It owns no
// broker transport and cannot submit or cancel an order.
class IbOrderLifecycleTracker
{
public:
    void ActivateConnectionEpoch(std::uint64_t connectionEpoch);
    void InvalidateConnectionEpoch();
    void BeginLocalOrderGeneration(long orderId);
    bool RecordBrokerOpenOrder(long orderId, const std::string& status);
    void RecordBrokerStatus(long orderId, const std::string& status,
                            bool economicFillEvidence = false);
    void Forget(long orderId);

    bool CanCancel(long orderId,
                   std::string* suppressReason = nullptr) const;

private:
    struct State
    {
        bool sentToBroker = false;
        bool brokerAck = false;
        bool finalState = false;
        std::string lastStatus;
    };

    static bool IsBrokerAckStatus(const std::string& status);
    static bool IsFinalStatus(const std::string& status);

    std::unordered_map<long, State> m_states;
    std::uint64_t m_connectionEpoch = 0;
    bool m_connectionEpochActive = false;
};
