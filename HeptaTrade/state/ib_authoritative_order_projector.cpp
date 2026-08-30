#include "ib_authoritative_order_projector.h"
#include "ib_contract_identity.h"

#include <algorithm>
#include <cmath>
#include <limits>

IBAuthoritativeOrderProjector::IBAuthoritativeOrderProjector(
    AuthoritativeTradingSnapshotStore& store)
    : m_store(store)
{
}

std::string IBAuthoritativeOrderProjector::NormalizeInstrument(const std::string& value)
{
    std::string normalized = value;
    std::transform(normalized.begin(), normalized.end(), normalized.begin(), [](unsigned char character) {
        return character >= static_cast<unsigned char>('a') &&
                character <= static_cast<unsigned char>('z') ?
            static_cast<char>(character - static_cast<unsigned char>('a') +
                              static_cast<unsigned char>('A')) :
            static_cast<char>(character);
    });
    std::replace(normalized.begin(), normalized.end(), '/', '.');
    return normalized;
}

std::string IBAuthoritativeOrderProjector::InstrumentFromEvent(const IBEvent& event)
{
    return BuildIBAuthoritativeInstrumentIdentity(event.contract, event.key);
}

bool IBAuthoritativeOrderProjector::BuildOrder(const std::string& account,
                                               const std::string& instrument,
                                               const IBOrderLite& request,
                                               long orderId,
                                               const std::string& status,
                                               AuthoritativeActiveOrder& out,
                                               std::string& reason)
{
    out = AuthoritativeActiveOrder();
    out.venue = "IB";
    out.orderId = orderId;
    out.account = account;
    out.instrument = NormalizeInstrument(instrument);
    out.side = request.action == "SELL" ? AuthoritativeOrderSide::Sell : AuthoritativeOrderSide::Buy;
    if (request.action != "BUY" && request.action != "SELL")
    {
        reason = "UNSUPPORTED_AUTHORITATIVE_ORDER_SIDE";
        return false;
    }
    if (request.orderType == "MKT")
    {
        out.type = AuthoritativeOrderType::Market;
    }
    else if (request.orderType == "LMT")
    {
        out.type = AuthoritativeOrderType::Limit;
        out.limitPrice = request.lmtPrice;
    }
    else if (request.orderType == "STP")
    {
        out.type = AuthoritativeOrderType::Stop;
        out.stopPrice = request.auxPrice;
    }
    else if (request.orderType == "STP LMT")
    {
        out.type = AuthoritativeOrderType::StopLimit;
        out.limitPrice = request.lmtPrice;
        out.stopPrice = request.auxPrice;
    }
    else
    {
        reason = "UNSUPPORTED_AUTHORITATIVE_ORDER_TYPE";
        return false;
    }

    if (status == "PreSubmitted") out.status = AuthoritativeActiveOrderStatus::PreSubmitted;
    else if (status == "Submitted") out.status = AuthoritativeActiveOrderStatus::Submitted;
    else if (status == "PendingCancel") out.status = AuthoritativeActiveOrderStatus::PendingCancel;
    else out.status = AuthoritativeActiveOrderStatus::PendingSubmit;
    out.totalQuantity = request.totalQuantity;
    out.remainingQuantity = request.totalQuantity;
    return true;
}

bool IBAuthoritativeOrderProjector::IsTerminalStatus(const std::string& status)
{
    return status == "Filled" || status == "Cancelled" || status == "ApiCancelled" ||
           status == "Inactive" || status == "Rejected";
}

bool IBAuthoritativeOrderProjector::ApplyActiveStatus(
    const std::string& status,
    double filledQuantity,
    AuthoritativeActiveOrderStatus& out)
{
    if (status == "PendingSubmit" || status == "ApiPending")
        out = AuthoritativeActiveOrderStatus::PendingSubmit;
    else if (status == "PreSubmitted")
        out = AuthoritativeActiveOrderStatus::PreSubmitted;
    else if (status == "PendingCancel")
        out = AuthoritativeActiveOrderStatus::PendingCancel;
    else if (status == "Submitted")
        out = filledQuantity > 0.0 ? AuthoritativeActiveOrderStatus::PartiallyFilled :
                                    AuthoritativeActiveOrderStatus::Submitted;
    else if (status == "PartiallyFilled")
        out = AuthoritativeActiveOrderStatus::PartiallyFilled;
    else
        return false;
    return true;
}

IBAuthoritativeOrderProjectionResult IBAuthoritativeOrderProjector::FromWrite(
    const AuthoritativeSnapshotWriteResult& write,
    const AuthoritativeActiveOrder* order)
{
    IBAuthoritativeOrderProjectionResult result;
    result.status = write.accepted ? IBAuthoritativeOrderProjectionStatus::Applied :
                                     IBAuthoritativeOrderProjectionStatus::Rejected;
    result.reasonCode = write.reasonCode;
    if (order != nullptr)
    {
        result.hasOrder = true;
        result.order = *order;
    }
    return result;
}

IBAuthoritativeOrderProjectionResult IBAuthoritativeOrderProjector::ProjectPlaced(
    const IbPlaceOrderCommand& command,
    long orderId,
    std::uint64_t observedAtMs)
{
    AuthoritativeActiveOrder order;
    std::string reason;
    const std::string instrument = BuildIBAuthoritativeInstrumentIdentity(
        command.contract, command.instrument);
    if (instrument.empty())
    {
        IBAuthoritativeOrderProjectionResult result;
        result.status = IBAuthoritativeOrderProjectionStatus::Rejected;
        result.reasonCode = "ORDER_CONTRACT_IDENTITY_REQUIRED";
        return result;
    }
    if (!BuildOrder(command.context.account, instrument, command.order, orderId, "PendingSubmit", order, reason))
    {
        IBAuthoritativeOrderProjectionResult result;
        result.status = IBAuthoritativeOrderProjectionStatus::Rejected;
        result.reasonCode = reason;
        return result;
    }
    return FromWrite(m_store.UpsertActiveOrder(order, observedAtMs, "ib.execution_coordinator.place"), &order);
}

IBAuthoritativeOrderProjectionResult IBAuthoritativeOrderProjector::ProjectCancelSent(
    long orderId,
    std::uint64_t observedAtMs)
{
    AuthoritativeActiveOrderRecord record = m_store.GetActiveOrder(
        "IB", orderId, observedAtMs, std::numeric_limits<std::uint64_t>::max());
    if (record.state.availability == AuthoritativeSnapshotAvailability::Missing)
    {
        IBAuthoritativeOrderProjectionResult result;
        result.status = IBAuthoritativeOrderProjectionStatus::Missing;
        result.reasonCode = "AUTHORITATIVE_ORDER_NOT_FOUND";
        return result;
    }
    record.value.status = AuthoritativeActiveOrderStatus::PendingCancel;
    return FromWrite(m_store.UpsertActiveOrder(
        record.value, observedAtMs, "ib.execution_coordinator.cancel"), &record.value);
}

IBAuthoritativeOrderProjectionResult IBAuthoritativeOrderProjector::ProjectOpenOrder(
    const IBEvent& event,
    const std::string& defaultAccount,
    std::uint64_t observedAtMs)
{
    AuthoritativeActiveOrder order;
    std::string reason;
    if (!BuildOrder(event.account.empty() ? defaultAccount : event.account,
                    InstrumentFromEvent(event),
                    event.order,
                    static_cast<long>(event.id),
                    event.value,
                    order,
                    reason))
    {
        IBAuthoritativeOrderProjectionResult result;
        result.status = IBAuthoritativeOrderProjectionStatus::Rejected;
        result.reasonCode = reason;
        return result;
    }
    return FromWrite(m_store.UpsertActiveOrder(order, observedAtMs, "ib.open_order"), &order);
}

IBAuthoritativeOrderProjectionResult IBAuthoritativeOrderProjector::ProjectOrderStatus(
    long orderId,
    const std::string& status,
    double filledQuantity,
    double remainingQuantity,
    double averageFillPrice,
    bool executionEvidence,
    std::uint64_t observedAtMs)
{
    AuthoritativeActiveOrderRecord record = m_store.GetActiveOrder(
        "IB", orderId, observedAtMs, std::numeric_limits<std::uint64_t>::max());
    if (IsTerminalStatus(status))
    {
        if (status == "Filled" &&
            !(executionEvidence ||
              (std::isfinite(filledQuantity) && filledQuantity > 0.0 &&
               std::isfinite(averageFillPrice) && averageFillPrice > 0.0)))
        {
            IBAuthoritativeOrderProjectionResult result;
            result.status = IBAuthoritativeOrderProjectionStatus::Rejected;
            result.reasonCode = "IB_FILLED_ECONOMIC_EVIDENCE_REQUIRED";
            return result;
        }
        if (record.state.availability == AuthoritativeSnapshotAvailability::Missing)
        {
            IBAuthoritativeOrderProjectionResult result;
            result.status = IBAuthoritativeOrderProjectionStatus::Ignored;
            return result;
        }
        return FromWrite(m_store.EraseActiveOrder(
            "IB", orderId, observedAtMs, "ib.order_status.terminal"));
    }
    if (record.state.availability == AuthoritativeSnapshotAvailability::Missing)
    {
        IBAuthoritativeOrderProjectionResult result;
        AuthoritativeActiveOrderStatus ignoredStatus;
        if (!ApplyActiveStatus(status, filledQuantity, ignoredStatus)) return result;
        result.status = IBAuthoritativeOrderProjectionStatus::Missing;
        result.reasonCode = "AUTHORITATIVE_ORDER_NOT_FOUND";
        return result;
    }
    if (!ApplyActiveStatus(status, filledQuantity, record.value.status))
        return IBAuthoritativeOrderProjectionResult();
    if (!std::isfinite(filledQuantity) || !std::isfinite(remainingQuantity) ||
        filledQuantity < 0.0 || remainingQuantity <= 0.0)
    {
        IBAuthoritativeOrderProjectionResult result;
        result.status = IBAuthoritativeOrderProjectionStatus::Rejected;
        result.reasonCode = "INVALID_BROKER_ORDER_QUANTITY";
        return result;
    }
    record.value.filledQuantity = filledQuantity;
    record.value.remainingQuantity = remainingQuantity;
    record.value.totalQuantity = filledQuantity + remainingQuantity;
    return FromWrite(m_store.UpsertActiveOrder(
        record.value, observedAtMs, "ib.order_status.active"), &record.value);
}
