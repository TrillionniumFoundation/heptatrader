#pragma once

#include "execution_authority.h"

#include <functional>
#include <string>

// Broker-process-only decorator used to keep fatal-runtime gating adjacent to
// the final Execution authority without enlarging the runtime composition.
class IbPaperExecutionHookAuthority final : public ExecutionAuthority
{
public:
    IbPaperExecutionHookAuthority(
        ExecutionAuthority& inner,
        const std::function<void(const char*)>& onStage,
        const std::function<bool(std::string*)>& fatalCheck);

    ExecutionCommandResult PlaceOrder(
        const PlaceOrderCommand& command) override;
    ExecutionCommandResult CancelOrder(
        const CancelOrderCommand& command) override;
    ExecutionCommandResult FlattenPosition(
        const FlattenPositionCommand& command) override;

private:
    ExecutionCommandResult RejectFatal(
        const AgentExecutionContext& context) const;
    void Notify(const char* stage) const;

    ExecutionAuthority& m_inner;
    std::function<void(const char*)> m_onStage;
    std::function<bool(std::string*)> m_fatalCheck;
};
