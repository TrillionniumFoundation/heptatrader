#include "ib_paper_execution_hook_authority.h"

IbPaperExecutionHookAuthority::IbPaperExecutionHookAuthority(
    ExecutionAuthority& inner,
    const std::function<void(const char*)>& onStage,
    const std::function<bool(std::string*)>& fatalCheck)
    : m_inner(inner), m_onStage(onStage), m_fatalCheck(fatalCheck)
{
}

ExecutionCommandResult IbPaperExecutionHookAuthority::PlaceOrder(
    const PlaceOrderCommand& command)
{
    ExecutionCommandResult fatal = RejectFatal(command.context);
    if (fatal.status == ExecutionCommandStatus::Rejected) return fatal;
    Notify("before_dispatch");
    fatal = RejectFatal(command.context);
    if (fatal.status == ExecutionCommandStatus::Rejected) return fatal;
    const ExecutionCommandResult result = m_inner.PlaceOrder(command);
    if (result.status == ExecutionCommandStatus::Accepted)
        Notify("after_receipt");
    return result;
}

ExecutionCommandResult IbPaperExecutionHookAuthority::CancelOrder(
    const CancelOrderCommand& command)
{
    Notify("before_cancel_dispatch");
    const ExecutionCommandResult result = m_inner.CancelOrder(command);
    if (result.status == ExecutionCommandStatus::Accepted)
        Notify("after_cancel_receipt");
    return result;
}

ExecutionCommandResult IbPaperExecutionHookAuthority::FlattenPosition(
    const FlattenPositionCommand& command)
{
    ExecutionCommandResult fatal = RejectFatal(command.context);
    if (fatal.status == ExecutionCommandStatus::Rejected) return fatal;
    Notify("before_flatten_dispatch");
    fatal = RejectFatal(command.context);
    if (fatal.status == ExecutionCommandStatus::Rejected) return fatal;
    const ExecutionCommandResult result = m_inner.FlattenPosition(command);
    if (result.status == ExecutionCommandStatus::Accepted)
        Notify("after_flatten_receipt");
    return result;
}

ExecutionCommandResult IbPaperExecutionHookAuthority::RejectFatal(
    const AgentExecutionContext& context) const
{
    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Accepted;
    std::string reason;
    if (!m_fatalCheck || !m_fatalCheck(&reason)) return result;
    result.status = ExecutionCommandStatus::Rejected;
    result.commandId = context.toolCallId;
    result.reasonCode =
        reason.empty() ? "IB_PAPER_RUNTIME_FATAL" : reason;
    result.detail =
        "IB PAPER runtime is fail-closed after a fatal adapter event";
    return result;
}

void IbPaperExecutionHookAuthority::Notify(const char* stage) const
{
    if (m_onStage) m_onStage(stage);
}
