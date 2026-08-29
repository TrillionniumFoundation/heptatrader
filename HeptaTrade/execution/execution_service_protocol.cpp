#include "execution_service_protocol.h"

#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <map>
#include <set>
#include <sstream>

namespace
{
const char kMagic[] = {'H', 'E', 'X', '1'};

enum Field : unsigned int
{
    AgentId = 1, SessionId, ToolCallId, Strategy, Account, Venue, ExecutionDomain,
    LeaseToken, LeaseGeneration, AllowCancelAny, Instrument, ExpiresAtMs, ReferencePrice,
    Symbol, SecType, Exchange, PrimaryExchange, Currency, ContractMonth, Right, Strike,
    Multiplier, TradingClass, LocalSymbol, Action, OrderType, Quantity, LimitPrice,
    AuxPrice, OutsideRth, OrderId, Side, TargetCommandId,
    ExpectedServiceEpoch, ExpectedServiceFencingGeneration, ReadQuery,
    TimeInForce, OrderRef, PreviewPermit, RecoveryIngressFence,
    TerminalPreliminaryReceiptSha256,
    ResultStatus = 100, ResultCommandId, ResultOrderId, ResultReasonCode, ResultDetail,
    ResultTargetCommandId, ResultTargetStatus, ResultAffectedCount, ResultMutationBlocked,
    ResultServiceEpoch, ResultServiceFencingGeneration,
    ResultOwnerAuditAuthoritative, ResultOwnerAuditComplete,
    ResultOwnerActiveOrderCount, ResultOwnerUncertainCommandCount,
    ResultBrokerConnectionEpoch, ResultBrokerActiveGeneration,
    ResultBrokerTerminalGeneration, ResultOwnerAccount,
    ResultOwnerExecutionDomain, ResultBrokerRiskGeneration,
    ResultBrokerAccountGeneration, ResultBrokerPositionGeneration,
    ResultBrokerFxCashGeneration, ResultBrokerExposureGeneration,
    ResultBrokerTerminalExposureGeneration,
    ResultBrokerRiskAbsorbedExposureGeneration,
    ResultBrokerGlobalActiveOrderCount,
    ResultBrokerPostFillRiskReconciliationPending,
    ResultBrokerRecoveryAuditBarrierComplete,
    ResultBrokerRecoveryAuditNewConnectionEpochRequired,
    ResultBrokerPositionQuantity, ResultBrokerGrossAbsolutePosition,
    ResultTerminalizationServiceEpoch,
    ResultTerminalizationServiceFencingGeneration,
    ResultTerminalizationGeneration, ResultTerminalLatchSha256,
    ResultTerminalMutationGateClosed,
    ResultTerminalBrokerTransportConnected,
    ResultTerminalBrokerEventIngressHalted,
    ResultTerminalBrokerCallbackQueueDrained,
    ResultTerminalBrokerCallbacksInFlight,
    ResultTerminalBrokerReconnectPermitted,
    ResultTerminalLatchDurable, ResultTerminalRuntimeLatchLoaded,
    ResultTerminalRuntimeVerified, ResultTerminalReplay
};

void AppendU16(std::string& out, unsigned int value)
{
    out.push_back(static_cast<char>((value >> 8) & 0xff));
    out.push_back(static_cast<char>(value & 0xff));
}

void AppendU32(std::string& out, std::size_t value)
{
    out.push_back(static_cast<char>((value >> 24) & 0xff));
    out.push_back(static_cast<char>((value >> 16) & 0xff));
    out.push_back(static_cast<char>((value >> 8) & 0xff));
    out.push_back(static_cast<char>(value & 0xff));
}

bool ReadU16(const std::string& in, std::size_t& offset, unsigned int& value)
{
    if (offset + 2 > in.size()) return false;
    value = (static_cast<unsigned char>(in[offset]) << 8) |
        static_cast<unsigned char>(in[offset + 1]);
    offset += 2;
    return true;
}

bool ReadU32(const std::string& in, std::size_t& offset, std::size_t& value)
{
    if (offset + 4 > in.size()) return false;
    value = (static_cast<std::size_t>(static_cast<unsigned char>(in[offset])) << 24) |
        (static_cast<std::size_t>(static_cast<unsigned char>(in[offset + 1])) << 16) |
        (static_cast<std::size_t>(static_cast<unsigned char>(in[offset + 2])) << 8) |
        static_cast<unsigned char>(in[offset + 3]);
    offset += 4;
    return true;
}

void AppendField(std::string& out, unsigned int tag, const std::string& value)
{
    AppendU16(out, tag);
    AppendU32(out, value.size());
    out.append(value);
}

template <typename T>
std::string Number(T value)
{
    std::ostringstream out;
    out.precision(17);
    out << value;
    return out.str();
}

bool DecodeEnvelope(const std::string& body, unsigned int& kind,
                    std::map<unsigned int, std::string>& fields,
                    bool allowLargeResultDetail, std::string& reason)
{
    if (body.size() < 8 || body.compare(0, 4, kMagic, 4) != 0)
    {
        reason = "EXECUTION_PROTOCOL_BAD_MAGIC";
        return false;
    }
    std::size_t offset = 4;
    unsigned int version = 0;
    if (!ReadU16(body, offset, version) || version != ExecutionServiceProtocol::ProtocolVersion() ||
        !ReadU16(body, offset, kind))
    {
        reason = "EXECUTION_PROTOCOL_UNSUPPORTED_VERSION";
        return false;
    }
    while (offset < body.size())
    {
        unsigned int tag = 0;
        std::size_t length = 0;
        if (!ReadU16(body, offset, tag) || !ReadU32(body, offset, length))
        {
            reason = "EXECUTION_PROTOCOL_INVALID_FIELD";
            return false;
        }
        const std::size_t maximumLength =
            allowLargeResultDetail && tag == ResultDetail ? 32768 : 4096;
        if (length > maximumLength || offset + length > body.size() ||
            fields.count(tag) != 0)
        {
            reason = "EXECUTION_PROTOCOL_INVALID_FIELD";
            return false;
        }
        fields[tag] = body.substr(offset, length);
        offset += length;
    }
    return true;
}

bool Require(const std::map<unsigned int, std::string>& fields, unsigned int tag,
             std::string& out, std::string& reason)
{
    const std::map<unsigned int, std::string>::const_iterator found = fields.find(tag);
    if (found == fields.end())
    {
        reason = "EXECUTION_PROTOCOL_MISSING_FIELD";
        return false;
    }
    out = found->second;
    return true;
}

bool HasExactFields(const std::map<unsigned int, std::string>& fields,
                    const std::set<unsigned int>& expected,
                    std::string& reason)
{
    if (fields.size() != expected.size())
    {
        reason = "EXECUTION_PROTOCOL_FIELD_SET_MISMATCH";
        return false;
    }
    for (std::set<unsigned int>::const_iterator it = expected.begin();
         it != expected.end(); ++it)
    {
        if (fields.find(*it) == fields.end())
        {
            reason = "EXECUTION_PROTOCOL_FIELD_SET_MISMATCH";
            return false;
        }
    }
    return true;
}

bool CanonicalSha256(const std::string& value)
{
    if (value.size() != 71 || value.compare(0, 7, "sha256:") != 0)
        return false;
    for (std::size_t i = 7; i < value.size(); ++i)
        if (!((value[i] >= '0' && value[i] <= '9') ||
              (value[i] >= 'a' && value[i] <= 'f')))
            return false;
    return true;
}

std::set<unsigned int> ContextFields()
{
    const unsigned int values[] = {
        AgentId, SessionId, ToolCallId, Strategy, Account, Venue,
        ExecutionDomain, AllowCancelAny};
    return std::set<unsigned int>(values, values + sizeof(values) / sizeof(values[0]));
}

void AddServiceIdentityFields(std::set<unsigned int>& fields)
{
    fields.insert(ExpectedServiceEpoch);
    fields.insert(ExpectedServiceFencingGeneration);
}

bool ParseLongLong(const std::string& value, long long& out)
{
    if (value.empty()) return false;
    char* end = nullptr;
    errno = 0;
    const long long parsed = std::strtoll(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0') return false;
    out = parsed;
    return true;
}

bool ParseUnsigned(const std::string& value, std::uint64_t& out)
{
    if (value.empty() || value[0] == '-') return false;
    char* end = nullptr;
    errno = 0;
    const unsigned long long parsed = std::strtoull(value.c_str(), &end, 10);
    if (errno != 0 || end == value.c_str() || *end != '\0') return false;
    out = static_cast<std::uint64_t>(parsed);
    return true;
}

bool ParseDouble(const std::string& value, double& out)
{
    if (value.empty()) return false;
    char* end = nullptr;
    errno = 0;
    const double parsed = std::strtod(value.c_str(), &end);
    if (errno != 0 || end == value.c_str() || *end != '\0' || !std::isfinite(parsed)) return false;
    out = parsed;
    return true;
}

bool IsCanonicalDecimal(const std::string& value)
{
    if (value.empty()) return true;
    if (value == "0") return true;
    std::size_t offset = 0;
    if (value[0] == '-')
    {
        if (value.size() == 1) return false;
        offset = 1;
    }
    if (value[offset] == '0')
    {
        ++offset;
        if (offset == value.size()) return false;
    }
    else
    {
        if (value[offset] < '1' || value[offset] > '9') return false;
        while (offset < value.size() && value[offset] >= '0' &&
               value[offset] <= '9')
            ++offset;
    }
    if (offset == value.size()) return true;
    if (value[offset++] != '.' || offset == value.size()) return false;
    for (; offset < value.size(); ++offset)
        if (value[offset] < '0' || value[offset] > '9') return false;
    return value.back() != '0';
}

void EncodeContext(const AgentExecutionContext& context, std::string& body)
{
    AppendField(body, AgentId, context.agentId);
    AppendField(body, SessionId, context.sessionId);
    AppendField(body, ToolCallId, context.toolCallId);
    AppendField(body, Strategy, context.strategy);
    AppendField(body, Account, context.account);
    AppendField(body, Venue, context.venue);
    AppendField(body, ExecutionDomain, context.executionDomain);
    AppendField(body, AllowCancelAny, context.allowCancelAny ? "1" : "0");
}

bool DecodeContext(const std::map<unsigned int, std::string>& fields,
                   AgentExecutionContext& context, std::string& reason)
{
    std::string allowCancelAny;
    if (!Require(fields, AgentId, context.agentId, reason) ||
        !Require(fields, SessionId, context.sessionId, reason) ||
        !Require(fields, ToolCallId, context.toolCallId, reason) ||
        !Require(fields, Strategy, context.strategy, reason) ||
        !Require(fields, Account, context.account, reason) ||
        !Require(fields, Venue, context.venue, reason) ||
        !Require(fields, ExecutionDomain, context.executionDomain, reason) ||
        !Require(fields, AllowCancelAny, allowCancelAny, reason) ||
        (allowCancelAny != "0" && allowCancelAny != "1"))
    {
        if (reason.empty()) reason = "EXECUTION_PROTOCOL_INVALID_CONTEXT";
        return false;
    }
    context.allowCancelAny = allowCancelAny == "1";
    return !context.agentId.empty() && !context.sessionId.empty() && !context.toolCallId.empty();
}
}

unsigned int ExecutionServiceProtocol::ProtocolVersion()
{
    return 10;
}

bool ExecutionServiceProtocol::EncodeRequest(const ExecutionServiceRequest& request,
                                             std::string& body, std::string& reason)
{
    body.assign(kMagic, sizeof(kMagic));
    AppendU16(body, ProtocolVersion());
    AppendU16(body, static_cast<unsigned int>(request.operation));
    if (request.operation != ExecutionServiceOperation::GetServiceIdentity)
    {
        if (request.expectedServiceEpoch.empty() || request.expectedServiceEpoch.size() > 128 ||
            request.expectedServiceFencingGeneration == 0)
        {
            reason = "EXECUTION_PROTOCOL_SERVICE_EPOCH_REQUIRED";
            return false;
        }
        AppendField(body, ExpectedServiceEpoch, request.expectedServiceEpoch);
        AppendField(body, ExpectedServiceFencingGeneration,
            Number(request.expectedServiceFencingGeneration));
    }
    if (request.operation == ExecutionServiceOperation::PlaceIbOrder ||
        request.operation == ExecutionServiceOperation::PreviewOrder)
    {
        const IbPlaceOrderCommand& command = request.place;
        EncodeContext(command.context, body);
        AppendField(body, Instrument, command.instrument);
        AppendField(body, ExpiresAtMs, Number(command.expiresAtMs));
        AppendField(body, ReferencePrice, Number(command.referencePrice));
        AppendField(body, Symbol, command.contract.symbol);
        AppendField(body, SecType, command.contract.secType);
        AppendField(body, Exchange, command.contract.exchange);
        AppendField(body, PrimaryExchange, command.contract.primaryExchange);
        AppendField(body, Currency, command.contract.currency);
        AppendField(body, ContractMonth, command.contract.lastTradeDateOrContractMonth);
        AppendField(body, Right, command.contract.right);
        AppendField(body, Strike, Number(command.contract.strike));
        AppendField(body, Multiplier, command.contract.multiplier);
        AppendField(body, TradingClass, command.contract.tradingClass);
        AppendField(body, LocalSymbol, command.contract.localSymbol);
        AppendField(body, Action, command.order.action);
        AppendField(body, OrderType, command.order.orderType);
        AppendField(body, Quantity, Number(command.order.totalQuantity));
        AppendField(body, LimitPrice, Number(command.order.lmtPrice));
        AppendField(body, AuxPrice, Number(command.order.auxPrice));
        AppendField(body, OutsideRth, command.order.outsideRth ? "1" : "0");
        AppendField(body, TimeInForce, command.timeInForce);
        AppendField(body, OrderRef, command.order.orderRef);
        AppendField(body, PreviewPermit, command.previewPermit);
    }
    else if (request.operation == ExecutionServiceOperation::CancelIbOrder)
    {
        const IbCancelOrderCommand& command = request.cancel;
        EncodeContext(command.context, body);
        AppendField(body, OrderId, Number(command.orderId));
        AppendField(body, Instrument, command.instrument);
        AppendField(body, Side, command.side);
    }
    else if (request.operation == ExecutionServiceOperation::FlattenPosition ||
             request.operation ==
                 ExecutionServiceOperation::PreviewFlattenPosition)
    {
        const FlattenPositionCommand& command = request.flatten;
        EncodeContext(command.context, body);
        AppendField(body, Instrument, command.instrument);
        AppendField(body, Symbol, command.contract.symbol);
        AppendField(body, SecType, command.contract.secType);
        AppendField(body, Exchange, command.contract.exchange);
        AppendField(body, PrimaryExchange, command.contract.primaryExchange);
        AppendField(body, Currency, command.contract.currency);
        AppendField(body, ContractMonth,
            command.contract.lastTradeDateOrContractMonth);
        AppendField(body, Right, command.contract.right);
        AppendField(body, Strike, Number(command.contract.strike));
        AppendField(body, Multiplier, command.contract.multiplier);
        AppendField(body, TradingClass, command.contract.tradingClass);
        AppendField(body, LocalSymbol, command.contract.localSymbol);
        AppendField(body, PreviewPermit, command.previewPermit);
    }
    else if (request.operation == ExecutionServiceOperation::QueryCommandStatus ||
             request.operation == ExecutionServiceOperation::RecoveryQueryCommandStatus ||
             request.operation == ExecutionServiceOperation::FenceSessionOwner ||
             request.operation == ExecutionServiceOperation::ReleaseSessionOwnerFence ||
             request.operation == ExecutionServiceOperation::ReconcileAuthoritativeState ||
             request.operation == ExecutionServiceOperation::RecoveryAuditOwner ||
             request.operation ==
                 ExecutionServiceOperation::TerminalizeRecoveryOwner)
    {
        EncodeContext(request.control.context, body);
        if (request.operation == ExecutionServiceOperation::QueryCommandStatus ||
            request.operation == ExecutionServiceOperation::RecoveryQueryCommandStatus ||
            request.operation ==
                ExecutionServiceOperation::TerminalizeRecoveryOwner)
            AppendField(body, TargetCommandId, request.control.targetCommandId);
        if (request.operation == ExecutionServiceOperation::RecoveryQueryCommandStatus ||
            request.operation == ExecutionServiceOperation::RecoveryAuditOwner ||
            request.operation ==
                ExecutionServiceOperation::TerminalizeRecoveryOwner)
            AppendField(body, RecoveryIngressFence,
                Number(request.control.recoveryIngressFence));
        if (request.operation ==
                ExecutionServiceOperation::TerminalizeRecoveryOwner)
        {
            if (!CanonicalSha256(
                    request.control.terminalPreliminaryReceiptSha256))
            {
                reason = "EXECUTION_PROTOCOL_INVALID_TERMINAL_BINDING";
                return false;
            }
            AppendField(body, TerminalPreliminaryReceiptSha256,
                request.control.terminalPreliminaryReceiptSha256);
        }
    }
    else if (request.operation == ExecutionServiceOperation::ReadAuthoritativeState)
    {
        EncodeContext(request.read.context, body);
        AppendField(body, ReadQuery, request.read.query);
        AppendField(body, Instrument, request.read.instrument);
    }
    else if (request.operation != ExecutionServiceOperation::GetServiceIdentity)
    {
        reason = "EXECUTION_PROTOCOL_INVALID_OPERATION";
        return false;
    }
    reason.clear();
    return true;
}

bool ExecutionServiceProtocol::DecodeRequest(const std::string& body,
                                             ExecutionServiceRequest& request, std::string& reason)
{
    unsigned int kind = 0;
    std::map<unsigned int, std::string> fields;
    if (!DecodeEnvelope(body, kind, fields, false, reason)) return false;
    request = ExecutionServiceRequest();
    std::set<unsigned int> expectedFields;
    if (kind == static_cast<unsigned int>(ExecutionServiceOperation::PlaceIbOrder) ||
        kind == static_cast<unsigned int>(ExecutionServiceOperation::PreviewOrder))
    {
        expectedFields = ContextFields();
        AddServiceIdentityFields(expectedFields);
        const unsigned int placeFields[] = {
            Instrument, ExpiresAtMs, ReferencePrice, Symbol, SecType, Exchange,
            PrimaryExchange, Currency, ContractMonth, Right, Strike, Multiplier,
            TradingClass, LocalSymbol, Action, OrderType, Quantity, LimitPrice,
            AuxPrice, OutsideRth, TimeInForce, OrderRef, PreviewPermit};
        expectedFields.insert(placeFields,
            placeFields + sizeof(placeFields) / sizeof(placeFields[0]));
    }
    else if (kind == static_cast<unsigned int>(ExecutionServiceOperation::CancelIbOrder))
    {
        expectedFields = ContextFields();
        AddServiceIdentityFields(expectedFields);
        expectedFields.insert(OrderId);
        expectedFields.insert(Instrument);
        expectedFields.insert(Side);
    }
    else if (kind ==
                 static_cast<unsigned int>(
                     ExecutionServiceOperation::FlattenPosition) ||
             kind ==
                 static_cast<unsigned int>(
                     ExecutionServiceOperation::PreviewFlattenPosition))
    {
        expectedFields = ContextFields();
        AddServiceIdentityFields(expectedFields);
        const unsigned int flattenFields[] = {
            Instrument, Symbol, SecType, Exchange, PrimaryExchange, Currency,
            ContractMonth, Right, Strike, Multiplier, TradingClass,
            LocalSymbol, PreviewPermit};
        expectedFields.insert(
            flattenFields,
            flattenFields +
                sizeof(flattenFields) / sizeof(flattenFields[0]));
    }
    else if (kind == static_cast<unsigned int>(ExecutionServiceOperation::QueryCommandStatus) ||
             kind == static_cast<unsigned int>(ExecutionServiceOperation::RecoveryQueryCommandStatus) ||
             kind == static_cast<unsigned int>(ExecutionServiceOperation::FenceSessionOwner) ||
             kind == static_cast<unsigned int>(ExecutionServiceOperation::ReleaseSessionOwnerFence) ||
             kind == static_cast<unsigned int>(ExecutionServiceOperation::ReconcileAuthoritativeState) ||
             kind == static_cast<unsigned int>(ExecutionServiceOperation::RecoveryAuditOwner) ||
             kind == static_cast<unsigned int>(
                 ExecutionServiceOperation::TerminalizeRecoveryOwner))
    {
        expectedFields = ContextFields();
        AddServiceIdentityFields(expectedFields);
        if (kind == static_cast<unsigned int>(ExecutionServiceOperation::QueryCommandStatus) ||
            kind == static_cast<unsigned int>(ExecutionServiceOperation::RecoveryQueryCommandStatus) ||
            kind == static_cast<unsigned int>(
                ExecutionServiceOperation::TerminalizeRecoveryOwner))
            expectedFields.insert(TargetCommandId);
        if (kind == static_cast<unsigned int>(ExecutionServiceOperation::RecoveryQueryCommandStatus) ||
            kind == static_cast<unsigned int>(ExecutionServiceOperation::RecoveryAuditOwner) ||
            kind == static_cast<unsigned int>(
                ExecutionServiceOperation::TerminalizeRecoveryOwner))
            expectedFields.insert(RecoveryIngressFence);
        if (kind == static_cast<unsigned int>(
                ExecutionServiceOperation::TerminalizeRecoveryOwner))
            expectedFields.insert(TerminalPreliminaryReceiptSha256);
    }
    else if (kind == static_cast<unsigned int>(ExecutionServiceOperation::ReadAuthoritativeState))
    {
        expectedFields = ContextFields();
        AddServiceIdentityFields(expectedFields);
        expectedFields.insert(ReadQuery);
        expectedFields.insert(Instrument);
    }
    else if (kind != static_cast<unsigned int>(ExecutionServiceOperation::GetServiceIdentity))
    {
        reason = "EXECUTION_PROTOCOL_INVALID_OPERATION";
        return false;
    }
    if (!HasExactFields(fields, expectedFields, reason)) return false;
    std::string expectedServiceFencingGeneration;
    if (kind != static_cast<unsigned int>(ExecutionServiceOperation::GetServiceIdentity) &&
        (!Require(fields, ExpectedServiceEpoch, request.expectedServiceEpoch, reason) ||
         !Require(fields, ExpectedServiceFencingGeneration,
             expectedServiceFencingGeneration, reason) ||
         !ParseUnsigned(expectedServiceFencingGeneration,
             request.expectedServiceFencingGeneration) ||
         request.expectedServiceEpoch.empty() || request.expectedServiceEpoch.size() > 128 ||
         request.expectedServiceFencingGeneration == 0))
    {
        if (reason.empty()) reason = "EXECUTION_PROTOCOL_INVALID_SERVICE_EPOCH";
        return false;
    }
    if (kind == static_cast<unsigned int>(ExecutionServiceOperation::PlaceIbOrder) ||
        kind == static_cast<unsigned int>(ExecutionServiceOperation::PreviewOrder))
    {
        request.operation = static_cast<ExecutionServiceOperation>(kind);
        IbPlaceOrderCommand& command = request.place;
        std::string expiresAt;
        std::string reference;
        std::string strike;
        std::string quantity;
        std::string limitPrice;
        std::string auxPrice;
        std::string outsideRth;
        if (!DecodeContext(fields, command.context, reason) ||
            !Require(fields, Instrument, command.instrument, reason) ||
            !Require(fields, ExpiresAtMs, expiresAt, reason) ||
            !Require(fields, ReferencePrice, reference, reason) ||
            !Require(fields, Symbol, command.contract.symbol, reason) ||
            !Require(fields, SecType, command.contract.secType, reason) ||
            !Require(fields, Exchange, command.contract.exchange, reason) ||
            !Require(fields, PrimaryExchange, command.contract.primaryExchange, reason) ||
            !Require(fields, Currency, command.contract.currency, reason) ||
            !Require(fields, ContractMonth, command.contract.lastTradeDateOrContractMonth, reason) ||
            !Require(fields, Right, command.contract.right, reason) ||
            !Require(fields, Strike, strike, reason) ||
            !Require(fields, Multiplier, command.contract.multiplier, reason) ||
            !Require(fields, TradingClass, command.contract.tradingClass, reason) ||
            !Require(fields, LocalSymbol, command.contract.localSymbol, reason) ||
            !Require(fields, Action, command.order.action, reason) ||
            !Require(fields, OrderType, command.order.orderType, reason) ||
            !Require(fields, Quantity, quantity, reason) ||
            !Require(fields, LimitPrice, limitPrice, reason) ||
            !Require(fields, AuxPrice, auxPrice, reason) ||
            !Require(fields, OutsideRth, outsideRth, reason) ||
            !Require(fields, TimeInForce, command.timeInForce, reason) ||
            !Require(fields, OrderRef, command.order.orderRef, reason) ||
            !Require(fields, PreviewPermit, command.previewPermit, reason) ||
            !ParseLongLong(expiresAt, command.expiresAtMs) ||
            !ParseDouble(reference, command.referencePrice) ||
            !ParseDouble(strike, command.contract.strike) ||
            !ParseDouble(quantity, command.order.totalQuantity) ||
            !ParseDouble(limitPrice, command.order.lmtPrice) ||
            !ParseDouble(auxPrice, command.order.auxPrice) ||
            (outsideRth != "0" && outsideRth != "1") ||
            command.timeInForce.size() > 16 ||
            command.order.orderRef.size() > 128 ||
            command.previewPermit.size() > 80 ||
            (request.operation == ExecutionServiceOperation::PreviewOrder &&
             !command.previewPermit.empty()))
        {
            if (reason.empty()) reason = "EXECUTION_PROTOCOL_INVALID_PLACE";
            return false;
        }
        command.order.outsideRth = outsideRth == "1";
    }
    else if (kind == static_cast<unsigned int>(ExecutionServiceOperation::CancelIbOrder))
    {
        request.operation = ExecutionServiceOperation::CancelIbOrder;
        std::string orderId;
        long long parsedOrderId = -1;
        if (!DecodeContext(fields, request.cancel.context, reason) ||
            !Require(fields, OrderId, orderId, reason) ||
            !Require(fields, Instrument, request.cancel.instrument, reason) ||
            !Require(fields, Side, request.cancel.side, reason) ||
            !ParseLongLong(orderId, parsedOrderId) || parsedOrderId < std::numeric_limits<long>::min() ||
            parsedOrderId > std::numeric_limits<long>::max())
        {
            if (reason.empty()) reason = "EXECUTION_PROTOCOL_INVALID_CANCEL";
            return false;
        }
        request.cancel.orderId = static_cast<long>(parsedOrderId);
    }
    else if (kind ==
                 static_cast<unsigned int>(
                     ExecutionServiceOperation::FlattenPosition) ||
             kind ==
                 static_cast<unsigned int>(
                     ExecutionServiceOperation::PreviewFlattenPosition))
    {
        request.operation = static_cast<ExecutionServiceOperation>(kind);
        std::string strike;
        if (!DecodeContext(fields, request.flatten.context, reason) ||
            !Require(fields, Instrument, request.flatten.instrument, reason) ||
            !Require(fields, Symbol, request.flatten.contract.symbol, reason) ||
            !Require(fields, SecType, request.flatten.contract.secType, reason) ||
            !Require(fields, Exchange, request.flatten.contract.exchange,
                     reason) ||
            !Require(fields, PrimaryExchange,
                     request.flatten.contract.primaryExchange, reason) ||
            !Require(fields, Currency, request.flatten.contract.currency,
                     reason) ||
            !Require(fields, ContractMonth,
                     request.flatten.contract.lastTradeDateOrContractMonth,
                     reason) ||
            !Require(fields, Right, request.flatten.contract.right, reason) ||
            !Require(fields, Strike, strike, reason) ||
            !Require(fields, Multiplier, request.flatten.contract.multiplier,
                     reason) ||
            !Require(fields, TradingClass,
                     request.flatten.contract.tradingClass, reason) ||
            !Require(fields, LocalSymbol,
                     request.flatten.contract.localSymbol, reason) ||
            !Require(fields, PreviewPermit,
                     request.flatten.previewPermit, reason) ||
            !ParseDouble(strike, request.flatten.contract.strike) ||
            request.flatten.instrument.empty() ||
            request.flatten.instrument.size() > 128 ||
            request.flatten.previewPermit.size() > 80 ||
            (request.operation ==
                 ExecutionServiceOperation::PreviewFlattenPosition &&
             !request.flatten.previewPermit.empty()))
        {
            if (reason.empty())
                reason = "EXECUTION_PROTOCOL_INVALID_FLATTEN";
            return false;
        }
    }
    else if (kind == static_cast<unsigned int>(ExecutionServiceOperation::QueryCommandStatus) ||
             kind == static_cast<unsigned int>(ExecutionServiceOperation::RecoveryQueryCommandStatus) ||
             kind == static_cast<unsigned int>(ExecutionServiceOperation::FenceSessionOwner) ||
             kind == static_cast<unsigned int>(ExecutionServiceOperation::ReleaseSessionOwnerFence) ||
             kind == static_cast<unsigned int>(ExecutionServiceOperation::ReconcileAuthoritativeState) ||
             kind == static_cast<unsigned int>(ExecutionServiceOperation::RecoveryAuditOwner) ||
             kind == static_cast<unsigned int>(
                 ExecutionServiceOperation::TerminalizeRecoveryOwner))
    {
        request.operation = static_cast<ExecutionServiceOperation>(kind);
        std::string ingressFence;
        if (!DecodeContext(fields, request.control.context, reason) ||
            ((request.operation == ExecutionServiceOperation::QueryCommandStatus ||
              request.operation == ExecutionServiceOperation::RecoveryQueryCommandStatus ||
              request.operation ==
                  ExecutionServiceOperation::TerminalizeRecoveryOwner) &&
             (!Require(fields, TargetCommandId, request.control.targetCommandId, reason) ||
              request.control.targetCommandId.empty())) ||
            ((request.operation == ExecutionServiceOperation::RecoveryQueryCommandStatus ||
              request.operation == ExecutionServiceOperation::RecoveryAuditOwner ||
              request.operation ==
                  ExecutionServiceOperation::TerminalizeRecoveryOwner) &&
             (!Require(fields, RecoveryIngressFence, ingressFence, reason) ||
              !ParseUnsigned(ingressFence,
                  request.control.recoveryIngressFence))) ||
            ((request.operation == ExecutionServiceOperation::RecoveryQueryCommandStatus ||
              request.operation ==
                  ExecutionServiceOperation::TerminalizeRecoveryOwner) &&
             request.control.recoveryIngressFence == 0) ||
            (request.operation ==
                 ExecutionServiceOperation::TerminalizeRecoveryOwner &&
             (!Require(fields, TerminalPreliminaryReceiptSha256,
                 request.control.terminalPreliminaryReceiptSha256, reason) ||
              !CanonicalSha256(
                  request.control.terminalPreliminaryReceiptSha256))))
        {
            if (reason.empty()) reason = "EXECUTION_PROTOCOL_INVALID_CONTROL";
            return false;
        }
    }
    else if (kind == static_cast<unsigned int>(ExecutionServiceOperation::GetServiceIdentity))
        request.operation = ExecutionServiceOperation::GetServiceIdentity;
    else if (kind == static_cast<unsigned int>(ExecutionServiceOperation::ReadAuthoritativeState))
    {
        request.operation = ExecutionServiceOperation::ReadAuthoritativeState;
        if (!DecodeContext(fields, request.read.context, reason) ||
            !Require(fields, ReadQuery, request.read.query, reason) ||
            !Require(fields, Instrument, request.read.instrument, reason) ||
            request.read.query.empty() || request.read.query.size() > 64 ||
            request.read.instrument.size() > 128)
        {
            if (reason.empty()) reason = "EXECUTION_PROTOCOL_INVALID_READ";
            return false;
        }
    }
    else
    {
        reason = "EXECUTION_PROTOCOL_INVALID_OPERATION";
        return false;
    }
    reason.clear();
    return true;
}

bool ExecutionServiceProtocol::EncodeResponse(const ExecutionCommandResult& response,
                                              std::string& body, std::string& reason)
{
    body.assign(kMagic, sizeof(kMagic));
    AppendU16(body, ProtocolVersion());
    AppendU16(body, 0);
    AppendField(body, ResultStatus, Number(static_cast<int>(response.status)));
    AppendField(body, ResultCommandId, response.commandId);
    AppendField(body, ResultOrderId, Number(response.orderId));
    AppendField(body, ResultReasonCode, response.reasonCode);
    AppendField(body, ResultDetail, response.detail);
    AppendField(body, ResultServiceEpoch, response.serviceEpoch);
    AppendField(body, ResultServiceFencingGeneration,
        Number(response.serviceFencingGeneration));
    reason.clear();
    return true;
}

bool ExecutionServiceProtocol::DecodeResponse(const std::string& body,
                                              ExecutionCommandResult& response, std::string& reason)
{
    unsigned int kind = 0;
    std::map<unsigned int, std::string> fields;
    if (!DecodeEnvelope(body, kind, fields, true, reason) || kind != 0)
        return false;
    const std::set<unsigned int> expectedFields{
        ResultStatus, ResultCommandId, ResultOrderId, ResultReasonCode,
        ResultDetail, ResultServiceEpoch, ResultServiceFencingGeneration};
    if (!HasExactFields(fields, expectedFields, reason)) return false;
    std::string status;
    std::string orderId;
    std::string serviceFencingGeneration;
    long long parsedStatus = -1;
    long long parsedOrderId = -1;
    if (!Require(fields, ResultStatus, status, reason) ||
        !Require(fields, ResultCommandId, response.commandId, reason) ||
        !Require(fields, ResultOrderId, orderId, reason) ||
        !Require(fields, ResultReasonCode, response.reasonCode, reason) ||
        !Require(fields, ResultDetail, response.detail, reason) ||
        !Require(fields, ResultServiceEpoch, response.serviceEpoch, reason) ||
        !Require(fields, ResultServiceFencingGeneration,
            serviceFencingGeneration, reason) ||
        !ParseUnsigned(serviceFencingGeneration,
            response.serviceFencingGeneration) ||
        response.serviceEpoch.empty() || response.serviceEpoch.size() > 128 ||
        response.serviceFencingGeneration == 0 ||
        !ParseLongLong(status, parsedStatus) || parsedStatus < 0 || parsedStatus > 3 ||
        !ParseLongLong(orderId, parsedOrderId) || parsedOrderId < std::numeric_limits<long>::min() ||
        parsedOrderId > std::numeric_limits<long>::max())
    {
        if (reason.empty()) reason = "EXECUTION_PROTOCOL_INVALID_RESPONSE";
        return false;
    }
    response.status = static_cast<ExecutionCommandStatus>(parsedStatus);
    response.orderId = static_cast<long>(parsedOrderId);
    reason.clear();
    return true;
}

bool ExecutionServiceProtocol::EncodeControlResponse(const ExecutionControlResult& response,
                                                     std::string& body, std::string& reason)
{
    if ((!response.terminalLatchSha256.empty() &&
         !CanonicalSha256(response.terminalLatchSha256)) ||
        (response.terminalRuntimeVerified &&
         (response.status != ExecutionCommandStatus::Accepted ||
          response.terminalizationServiceEpoch.empty() ||
          response.terminalizationServiceEpoch.size() > 128 ||
          response.terminalizationServiceFencingGeneration == 0 ||
          response.terminalizationGeneration == 0 ||
          !CanonicalSha256(response.terminalLatchSha256) ||
          !response.terminalMutationGateClosed ||
          response.terminalBrokerTransportConnected ||
          !response.terminalBrokerEventIngressHalted ||
          !response.terminalBrokerCallbackQueueDrained ||
          response.terminalBrokerCallbacksInFlight != 0 ||
          response.terminalBrokerReconnectPermitted ||
          !response.terminalLatchDurable ||
          !response.terminalRuntimeLatchLoaded)))
    {
        reason = "EXECUTION_PROTOCOL_INVALID_TERMINAL_WITNESS";
        return false;
    }
    body.assign(kMagic, sizeof(kMagic));
    AppendU16(body, ProtocolVersion());
    AppendU16(body, 0);
    AppendField(body, ResultStatus, Number(static_cast<int>(response.status)));
    AppendField(body, ResultCommandId, response.commandId);
    AppendField(body, ResultOrderId, Number(response.orderId));
    AppendField(body, ResultReasonCode, response.reasonCode);
    AppendField(body, ResultDetail, response.detail);
    AppendField(body, ResultTargetCommandId, response.targetCommandId);
    AppendField(body, ResultTargetStatus, Number(static_cast<int>(response.targetStatus)));
    AppendField(body, ResultAffectedCount, Number(response.affectedCount));
    AppendField(body, ResultMutationBlocked, response.mutationBlocked ? "1" : "0");
    AppendField(body, ResultServiceEpoch, response.serviceEpoch);
    AppendField(body, ResultServiceFencingGeneration,
        Number(response.serviceFencingGeneration));
    AppendField(body, ResultOwnerAuditAuthoritative,
        response.ownerAuditAuthoritative ? "1" : "0");
    AppendField(body, ResultOwnerAuditComplete,
        response.ownerAuditComplete ? "1" : "0");
    AppendField(body, ResultOwnerActiveOrderCount,
        Number(response.ownerActiveOrderCount));
    AppendField(body, ResultOwnerUncertainCommandCount,
        Number(response.ownerUncertainCommandCount));
    AppendField(body, ResultBrokerConnectionEpoch,
        Number(response.brokerConnectionEpoch));
    AppendField(body, ResultBrokerActiveGeneration,
        Number(response.brokerActiveGeneration));
    AppendField(body, ResultBrokerTerminalGeneration,
        Number(response.brokerTerminalGeneration));
    AppendField(body, ResultOwnerAccount, response.ownerAccount);
    AppendField(body, ResultOwnerExecutionDomain,
        response.ownerExecutionDomain);
    AppendField(body, ResultBrokerRiskGeneration,
        Number(response.brokerRiskGeneration));
    AppendField(body, ResultBrokerAccountGeneration,
        Number(response.brokerAccountGeneration));
    AppendField(body, ResultBrokerPositionGeneration,
        Number(response.brokerPositionGeneration));
    AppendField(body, ResultBrokerFxCashGeneration,
        Number(response.brokerFxCashGeneration));
    AppendField(body, ResultBrokerExposureGeneration,
        Number(response.brokerExposureGeneration));
    AppendField(body, ResultBrokerTerminalExposureGeneration,
        Number(response.brokerTerminalExposureGeneration));
    AppendField(body, ResultBrokerRiskAbsorbedExposureGeneration,
        Number(response.brokerRiskAbsorbedExposureGeneration));
    AppendField(body, ResultBrokerGlobalActiveOrderCount,
        Number(response.brokerGlobalActiveOrderCount));
    AppendField(body, ResultBrokerPostFillRiskReconciliationPending,
        response.brokerPostFillRiskReconciliationPending ? "1" : "0");
    AppendField(body, ResultBrokerRecoveryAuditBarrierComplete,
        response.brokerRecoveryAuditBarrierComplete ? "1" : "0");
    AppendField(body, ResultBrokerRecoveryAuditNewConnectionEpochRequired,
        response.brokerRecoveryAuditNewConnectionEpochRequired ? "1" : "0");
    AppendField(body, ResultBrokerPositionQuantity,
        response.brokerPositionQuantity);
    AppendField(body, ResultBrokerGrossAbsolutePosition,
        response.brokerGrossAbsolutePosition);
    AppendField(body, ResultTerminalizationServiceEpoch,
        response.terminalizationServiceEpoch);
    AppendField(body, ResultTerminalizationServiceFencingGeneration,
        Number(response.terminalizationServiceFencingGeneration));
    AppendField(body, ResultTerminalizationGeneration,
        Number(response.terminalizationGeneration));
    AppendField(body, ResultTerminalLatchSha256,
        response.terminalLatchSha256);
    AppendField(body, ResultTerminalMutationGateClosed,
        response.terminalMutationGateClosed ? "1" : "0");
    AppendField(body, ResultTerminalBrokerTransportConnected,
        response.terminalBrokerTransportConnected ? "1" : "0");
    AppendField(body, ResultTerminalBrokerEventIngressHalted,
        response.terminalBrokerEventIngressHalted ? "1" : "0");
    AppendField(body, ResultTerminalBrokerCallbackQueueDrained,
        response.terminalBrokerCallbackQueueDrained ? "1" : "0");
    AppendField(body, ResultTerminalBrokerCallbacksInFlight,
        Number(response.terminalBrokerCallbacksInFlight));
    AppendField(body, ResultTerminalBrokerReconnectPermitted,
        response.terminalBrokerReconnectPermitted ? "1" : "0");
    AppendField(body, ResultTerminalLatchDurable,
        response.terminalLatchDurable ? "1" : "0");
    AppendField(body, ResultTerminalRuntimeLatchLoaded,
        response.terminalRuntimeLatchLoaded ? "1" : "0");
    AppendField(body, ResultTerminalRuntimeVerified,
        response.terminalRuntimeVerified ? "1" : "0");
    AppendField(body, ResultTerminalReplay,
        response.terminalReplay ? "1" : "0");
    reason.clear();
    return true;
}

bool ExecutionServiceProtocol::DecodeControlResponse(const std::string& body,
                                                     ExecutionControlResult& response,
                                                     std::string& reason)
{
    unsigned int kind = 0;
    std::map<unsigned int, std::string> fields;
    if (!DecodeEnvelope(body, kind, fields, true, reason) || kind != 0)
        return false;
    const std::set<unsigned int> expectedFields{
        ResultStatus, ResultCommandId, ResultOrderId, ResultReasonCode,
        ResultDetail, ResultTargetCommandId, ResultTargetStatus,
        ResultAffectedCount, ResultMutationBlocked, ResultServiceEpoch,
        ResultServiceFencingGeneration, ResultOwnerAuditAuthoritative,
        ResultOwnerAuditComplete, ResultOwnerActiveOrderCount,
        ResultOwnerUncertainCommandCount, ResultBrokerConnectionEpoch,
        ResultBrokerActiveGeneration, ResultBrokerTerminalGeneration,
        ResultOwnerAccount, ResultOwnerExecutionDomain,
        ResultBrokerRiskGeneration, ResultBrokerAccountGeneration,
        ResultBrokerPositionGeneration, ResultBrokerFxCashGeneration,
        ResultBrokerExposureGeneration,
        ResultBrokerTerminalExposureGeneration,
        ResultBrokerRiskAbsorbedExposureGeneration,
        ResultBrokerGlobalActiveOrderCount,
        ResultBrokerPostFillRiskReconciliationPending,
        ResultBrokerRecoveryAuditBarrierComplete,
        ResultBrokerRecoveryAuditNewConnectionEpochRequired,
        ResultBrokerPositionQuantity,
        ResultBrokerGrossAbsolutePosition,
        ResultTerminalizationServiceEpoch,
        ResultTerminalizationServiceFencingGeneration,
        ResultTerminalizationGeneration, ResultTerminalLatchSha256,
        ResultTerminalMutationGateClosed,
        ResultTerminalBrokerTransportConnected,
        ResultTerminalBrokerEventIngressHalted,
        ResultTerminalBrokerCallbackQueueDrained,
        ResultTerminalBrokerCallbacksInFlight,
        ResultTerminalBrokerReconnectPermitted,
        ResultTerminalLatchDurable, ResultTerminalRuntimeLatchLoaded,
        ResultTerminalRuntimeVerified, ResultTerminalReplay};
    if (!HasExactFields(fields, expectedFields, reason)) return false;
    std::string status;
    std::string targetStatus;
    std::string orderId;
    std::string affectedCount;
    std::string mutationBlocked;
    std::string serviceFencingGeneration;
    std::string ownerAuditAuthoritative;
    std::string ownerAuditComplete;
    std::string ownerActiveOrderCount;
    std::string ownerUncertainCommandCount;
    std::string brokerConnectionEpoch;
    std::string brokerActiveGeneration;
    std::string brokerTerminalGeneration;
    std::string brokerRiskGeneration;
    std::string brokerAccountGeneration;
    std::string brokerPositionGeneration;
    std::string brokerFxCashGeneration;
    std::string brokerExposureGeneration;
    std::string brokerTerminalExposureGeneration;
    std::string brokerRiskAbsorbedExposureGeneration;
    std::string brokerGlobalActiveOrderCount;
    std::string brokerPostFillRiskReconciliationPending;
    std::string brokerRecoveryAuditBarrierComplete;
    std::string brokerRecoveryAuditNewConnectionEpochRequired;
    std::string terminalizationServiceFencingGeneration;
    std::string terminalizationGeneration;
    std::string terminalMutationGateClosed;
    std::string terminalBrokerTransportConnected;
    std::string terminalBrokerEventIngressHalted;
    std::string terminalBrokerCallbackQueueDrained;
    std::string terminalBrokerCallbacksInFlight;
    std::string terminalBrokerReconnectPermitted;
    std::string terminalLatchDurable;
    std::string terminalRuntimeLatchLoaded;
    std::string terminalRuntimeVerified;
    std::string terminalReplay;
    long long parsedStatus = -1;
    long long parsedTargetStatus = -1;
    long long parsedOrderId = -1;
    std::uint64_t parsedAffectedCount = 0;
    if (!Require(fields, ResultStatus, status, reason) ||
        !Require(fields, ResultCommandId, response.commandId, reason) ||
        !Require(fields, ResultOrderId, orderId, reason) ||
        !Require(fields, ResultReasonCode, response.reasonCode, reason) ||
        !Require(fields, ResultDetail, response.detail, reason) ||
        !Require(fields, ResultTargetCommandId, response.targetCommandId, reason) ||
        !Require(fields, ResultTargetStatus, targetStatus, reason) ||
        !Require(fields, ResultAffectedCount, affectedCount, reason) ||
        !Require(fields, ResultMutationBlocked, mutationBlocked, reason) ||
        !Require(fields, ResultServiceEpoch, response.serviceEpoch, reason) ||
        !Require(fields, ResultServiceFencingGeneration,
            serviceFencingGeneration, reason) ||
        !Require(fields, ResultOwnerAuditAuthoritative,
            ownerAuditAuthoritative, reason) ||
        !Require(fields, ResultOwnerAuditComplete,
            ownerAuditComplete, reason) ||
        !Require(fields, ResultOwnerActiveOrderCount,
            ownerActiveOrderCount, reason) ||
        !Require(fields, ResultOwnerUncertainCommandCount,
            ownerUncertainCommandCount, reason) ||
        !Require(fields, ResultBrokerConnectionEpoch,
            brokerConnectionEpoch, reason) ||
        !Require(fields, ResultBrokerActiveGeneration,
            brokerActiveGeneration, reason) ||
        !Require(fields, ResultBrokerTerminalGeneration,
            brokerTerminalGeneration, reason) ||
        !Require(fields, ResultOwnerAccount, response.ownerAccount, reason) ||
        !Require(fields, ResultOwnerExecutionDomain,
            response.ownerExecutionDomain, reason) ||
        !Require(fields, ResultBrokerRiskGeneration,
            brokerRiskGeneration, reason) ||
        !Require(fields, ResultBrokerAccountGeneration,
            brokerAccountGeneration, reason) ||
        !Require(fields, ResultBrokerPositionGeneration,
            brokerPositionGeneration, reason) ||
        !Require(fields, ResultBrokerFxCashGeneration,
            brokerFxCashGeneration, reason) ||
        !Require(fields, ResultBrokerExposureGeneration,
            brokerExposureGeneration, reason) ||
        !Require(fields, ResultBrokerTerminalExposureGeneration,
            brokerTerminalExposureGeneration, reason) ||
        !Require(fields, ResultBrokerRiskAbsorbedExposureGeneration,
            brokerRiskAbsorbedExposureGeneration, reason) ||
        !Require(fields, ResultBrokerGlobalActiveOrderCount,
            brokerGlobalActiveOrderCount, reason) ||
        !Require(fields, ResultBrokerPostFillRiskReconciliationPending,
            brokerPostFillRiskReconciliationPending, reason) ||
        !Require(fields, ResultBrokerRecoveryAuditBarrierComplete,
            brokerRecoveryAuditBarrierComplete, reason) ||
        !Require(fields, ResultBrokerRecoveryAuditNewConnectionEpochRequired,
            brokerRecoveryAuditNewConnectionEpochRequired, reason) ||
        !Require(fields, ResultBrokerPositionQuantity,
            response.brokerPositionQuantity, reason) ||
        !Require(fields, ResultBrokerGrossAbsolutePosition,
            response.brokerGrossAbsolutePosition, reason) ||
        !Require(fields, ResultTerminalizationServiceEpoch,
            response.terminalizationServiceEpoch, reason) ||
        !Require(fields, ResultTerminalizationServiceFencingGeneration,
            terminalizationServiceFencingGeneration, reason) ||
        !Require(fields, ResultTerminalizationGeneration,
            terminalizationGeneration, reason) ||
        !Require(fields, ResultTerminalLatchSha256,
            response.terminalLatchSha256, reason) ||
        !Require(fields, ResultTerminalMutationGateClosed,
            terminalMutationGateClosed, reason) ||
        !Require(fields, ResultTerminalBrokerTransportConnected,
            terminalBrokerTransportConnected, reason) ||
        !Require(fields, ResultTerminalBrokerEventIngressHalted,
            terminalBrokerEventIngressHalted, reason) ||
        !Require(fields, ResultTerminalBrokerCallbackQueueDrained,
            terminalBrokerCallbackQueueDrained, reason) ||
        !Require(fields, ResultTerminalBrokerCallbacksInFlight,
            terminalBrokerCallbacksInFlight, reason) ||
        !Require(fields, ResultTerminalBrokerReconnectPermitted,
            terminalBrokerReconnectPermitted, reason) ||
        !Require(fields, ResultTerminalLatchDurable,
            terminalLatchDurable, reason) ||
        !Require(fields, ResultTerminalRuntimeLatchLoaded,
            terminalRuntimeLatchLoaded, reason) ||
        !Require(fields, ResultTerminalRuntimeVerified,
            terminalRuntimeVerified, reason) ||
        !Require(fields, ResultTerminalReplay,
            terminalReplay, reason) ||
        !ParseUnsigned(serviceFencingGeneration,
            response.serviceFencingGeneration) ||
        response.serviceEpoch.empty() || response.serviceEpoch.size() > 128 ||
        response.serviceFencingGeneration == 0 ||
        !ParseLongLong(status, parsedStatus) || parsedStatus < 0 || parsedStatus > 3 ||
        !ParseLongLong(targetStatus, parsedTargetStatus) ||
        parsedTargetStatus < 0 || parsedTargetStatus > 3 ||
        !ParseLongLong(orderId, parsedOrderId) ||
        parsedOrderId < std::numeric_limits<long>::min() ||
        parsedOrderId > std::numeric_limits<long>::max() ||
        !ParseUnsigned(affectedCount, parsedAffectedCount) ||
        !ParseUnsigned(ownerActiveOrderCount,
            response.ownerActiveOrderCount) ||
        !ParseUnsigned(ownerUncertainCommandCount,
            response.ownerUncertainCommandCount) ||
        !ParseUnsigned(brokerConnectionEpoch,
            response.brokerConnectionEpoch) ||
        !ParseUnsigned(brokerActiveGeneration,
            response.brokerActiveGeneration) ||
        !ParseUnsigned(brokerTerminalGeneration,
            response.brokerTerminalGeneration) ||
        !ParseUnsigned(brokerRiskGeneration,
            response.brokerRiskGeneration) ||
        !ParseUnsigned(brokerAccountGeneration,
            response.brokerAccountGeneration) ||
        !ParseUnsigned(brokerPositionGeneration,
            response.brokerPositionGeneration) ||
        !ParseUnsigned(brokerFxCashGeneration,
            response.brokerFxCashGeneration) ||
        !ParseUnsigned(brokerExposureGeneration,
            response.brokerExposureGeneration) ||
        !ParseUnsigned(brokerTerminalExposureGeneration,
            response.brokerTerminalExposureGeneration) ||
        !ParseUnsigned(brokerRiskAbsorbedExposureGeneration,
            response.brokerRiskAbsorbedExposureGeneration) ||
        !ParseUnsigned(brokerGlobalActiveOrderCount,
            response.brokerGlobalActiveOrderCount) ||
        !ParseUnsigned(terminalizationServiceFencingGeneration,
            response.terminalizationServiceFencingGeneration) ||
        !ParseUnsigned(terminalizationGeneration,
            response.terminalizationGeneration) ||
        !ParseUnsigned(terminalBrokerCallbacksInFlight,
            response.terminalBrokerCallbacksInFlight) ||
        (mutationBlocked != "0" && mutationBlocked != "1") ||
        (ownerAuditAuthoritative != "0" &&
         ownerAuditAuthoritative != "1") ||
        (ownerAuditComplete != "0" && ownerAuditComplete != "1") ||
        (brokerPostFillRiskReconciliationPending != "0" &&
         brokerPostFillRiskReconciliationPending != "1") ||
        (brokerRecoveryAuditBarrierComplete != "0" &&
         brokerRecoveryAuditBarrierComplete != "1") ||
        (brokerRecoveryAuditNewConnectionEpochRequired != "0" &&
         brokerRecoveryAuditNewConnectionEpochRequired != "1") ||
        (terminalMutationGateClosed != "0" &&
         terminalMutationGateClosed != "1") ||
        (terminalBrokerTransportConnected != "0" &&
         terminalBrokerTransportConnected != "1") ||
        (terminalBrokerEventIngressHalted != "0" &&
         terminalBrokerEventIngressHalted != "1") ||
        (terminalBrokerCallbackQueueDrained != "0" &&
         terminalBrokerCallbackQueueDrained != "1") ||
        (terminalBrokerReconnectPermitted != "0" &&
         terminalBrokerReconnectPermitted != "1") ||
        (terminalLatchDurable != "0" && terminalLatchDurable != "1") ||
        (terminalRuntimeLatchLoaded != "0" &&
         terminalRuntimeLatchLoaded != "1") ||
        (terminalRuntimeVerified != "0" &&
         terminalRuntimeVerified != "1") ||
        (terminalReplay != "0" && terminalReplay != "1") ||
        !IsCanonicalDecimal(response.brokerPositionQuantity) ||
        !IsCanonicalDecimal(response.brokerGrossAbsolutePosition) ||
        (!response.terminalLatchSha256.empty() &&
         !CanonicalSha256(response.terminalLatchSha256)) ||
        response.ownerAccount.size() > 128 ||
        response.ownerExecutionDomain.size() > 128)
    {
        if (reason.empty()) reason = "EXECUTION_PROTOCOL_INVALID_CONTROL_RESPONSE";
        return false;
    }
    response.status = static_cast<ExecutionCommandStatus>(parsedStatus);
    response.targetStatus = static_cast<ExecutionCommandStatus>(parsedTargetStatus);
    response.orderId = static_cast<long>(parsedOrderId);
    response.affectedCount = parsedAffectedCount;
    response.mutationBlocked = mutationBlocked == "1";
    response.ownerAuditAuthoritative = ownerAuditAuthoritative == "1";
    response.ownerAuditComplete = ownerAuditComplete == "1";
    response.brokerPostFillRiskReconciliationPending =
        brokerPostFillRiskReconciliationPending == "1";
    response.brokerRecoveryAuditBarrierComplete =
        brokerRecoveryAuditBarrierComplete == "1";
    response.brokerRecoveryAuditNewConnectionEpochRequired =
        brokerRecoveryAuditNewConnectionEpochRequired == "1";
    response.terminalMutationGateClosed =
        terminalMutationGateClosed == "1";
    response.terminalBrokerTransportConnected =
        terminalBrokerTransportConnected == "1";
    response.terminalBrokerEventIngressHalted =
        terminalBrokerEventIngressHalted == "1";
    response.terminalBrokerCallbackQueueDrained =
        terminalBrokerCallbackQueueDrained == "1";
    response.terminalBrokerReconnectPermitted =
        terminalBrokerReconnectPermitted == "1";
    response.terminalLatchDurable = terminalLatchDurable == "1";
    response.terminalRuntimeLatchLoaded =
        terminalRuntimeLatchLoaded == "1";
    response.terminalRuntimeVerified = terminalRuntimeVerified == "1";
    response.terminalReplay = terminalReplay == "1";
    if (response.terminalRuntimeVerified &&
        (response.status != ExecutionCommandStatus::Accepted ||
         response.terminalizationServiceEpoch.empty() ||
         response.terminalizationServiceEpoch.size() > 128 ||
         response.terminalizationServiceFencingGeneration == 0 ||
         response.terminalizationGeneration == 0 ||
         !CanonicalSha256(response.terminalLatchSha256) ||
         !response.terminalMutationGateClosed ||
         response.terminalBrokerTransportConnected ||
         !response.terminalBrokerEventIngressHalted ||
         !response.terminalBrokerCallbackQueueDrained ||
         response.terminalBrokerCallbacksInFlight != 0 ||
         response.terminalBrokerReconnectPermitted ||
         !response.terminalLatchDurable ||
         !response.terminalRuntimeLatchLoaded))
    {
        reason = "EXECUTION_PROTOCOL_INVALID_TERMINAL_WITNESS";
        return false;
    }
    reason.clear();
    return true;
}
