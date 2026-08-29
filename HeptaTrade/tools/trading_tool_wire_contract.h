#pragma once

#include "trading_tool_registry.h"

#include <cctype>
#include <cmath>
#include <sstream>
#include <string>

// Pure wire-side validation shared by the privileged registry and the
// unprivileged native client.  Keep this header free of authority objects and
// out-of-line project symbols so the installed native client archive is a
// complete link closure rather than a hidden dependency on Execution core.
class TradingToolWireContract
{
public:
    static const char* StatusName(TradingToolCallStatus status)
    {
        switch (status)
        {
        case TradingToolCallStatus::Ok: return "ok";
        case TradingToolCallStatus::PermissionDenied: return "permission_denied";
        case TradingToolCallStatus::InvalidTool: return "invalid_tool";
        case TradingToolCallStatus::Rejected: return "rejected";
        case TradingToolCallStatus::Duplicate: return "duplicate";
        case TradingToolCallStatus::Uncertain: return "uncertain";
        case TradingToolCallStatus::Error: return "error";
        }
        return "unknown";
    }

    // This is the single authoritative result serializer used both by the
    // Unix protocol and by bounded compound tools before they report success.
    // Keeping the preflight and transport on the same encoder makes the
    // maximum-frame proof exact rather than an estimate of JSON overhead.
    static std::string EncodeResultEnvelope(const TradingToolResult& result)
    {
        std::ostringstream out;
        out << "{\"status\":\"" << StatusName(result.status)
            << "\",\"tool\":\"" << EscapeJson(result.toolName)
            << "\",\"reason_code\":\"" << EscapeJson(result.reasonCode)
            << "\",\"detail\":\"" << EscapeJson(result.detail)
            << "\",\"order_id\":" << result.orderId << ",\"payload\":";
        if (result.payloadJson.empty()) out << "null";
        else out << result.payloadJson;
        out << "}";
        return out.str();
    }

    static bool IsCanonicalToolName(const std::string& value)
    {
        if (value.size() < 3 || value.size() > 64) return false;
        bool segmentStart = true;
        bool sawSeparator = false;
        for (std::string::const_iterator it = value.begin();
             it != value.end(); ++it)
        {
            const unsigned char c = static_cast<unsigned char>(*it);
            if (segmentStart)
            {
                if (c < 'a' || c > 'z') return false;
                segmentStart = false;
            }
            else if (c == '.')
            {
                sawSeparator = true;
                segmentStart = true;
            }
            else if (!((c >= 'a' && c <= 'z') ||
                       (c >= '0' && c <= '9') || c == '_'))
                return false;
        }
        return sawSeparator && !segmentStart;
    }

    static bool IsCanonicalCommandId(const std::string& value)
    {
        if (value.size() < 8 || value.size() > 128) return false;
        for (std::string::const_iterator it = value.begin();
             it != value.end(); ++it)
        {
            const unsigned char c = static_cast<unsigned char>(*it);
            if (!std::isalnum(c) && c != '.' && c != '_' && c != ':' &&
                c != '-') return false;
        }
        return true;
    }

    static bool ValidateCallSemantics(const TradingToolCall& call,
                                      std::string& reasonCode,
                                      std::string& detail)
    {
        reasonCode.clear();
        detail.clear();

        if (call.name != "execution.get_command_status" &&
            !call.targetCommandId.empty())
            return Reject("UNEXPECTED_TOOL_FIELD", "command_id",
                          reasonCode, detail);

        if (call.name == "execution.get_command_status")
        {
            if (call.targetCommandId.empty())
                return Reject("MISSING_REQUIRED_FIELD", "command_id",
                              reasonCode, detail);
            if (!IsCanonicalCommandId(call.targetCommandId))
                return Reject("INVALID_COMMAND_ID",
                              "command_id must be a bounded canonical identifier",
                              reasonCode, detail);
            if (HasFieldsOtherThanCommandId(call))
                return Reject("UNEXPECTED_TOOL_FIELD",
                              "execution.get_command_status accepts only command_id",
                              reasonCode, detail);
            return true;
        }

        if (call.name == "market.get_quote" ||
            call.name == "watch.get_snapshot" ||
            call.name == "risk.preview_flatten")
        {
            if (call.instrument.empty())
                return Reject("MISSING_REQUIRED_FIELD", "instrument", reasonCode, detail);
            if (!IsCanonicalInstrument(call.instrument))
                return Reject("INVALID_INSTRUMENT",
                              "instrument must be a bounded canonical identifier",
                              reasonCode, detail);
            if (((call.name == "market.get_quote" ||
                  call.name == "watch.get_snapshot") &&
                 HasFieldsOtherThanInstrument(call)) ||
                (call.name == "risk.preview_flatten" &&
                 HasFieldsOtherThanInstrumentAndContract(call)))
                return Reject("UNEXPECTED_TOOL_FIELD",
                              "tool accepts only instrument",
                              reasonCode, detail);
            return true;
        }

        if (call.name == "account.get_summary" ||
            call.name == "portfolio.list_positions" ||
            call.name == "orders.list" || call.name == "risk.get_limits" ||
            call.name == "system.get_health" || call.name == "system.tools.list")
        {
            if (!call.instrument.empty() || HasFieldsOtherThanInstrument(call))
                return Reject("UNEXPECTED_TOOL_FIELD", "tool accepts no input fields",
                              reasonCode, detail);
            return true;
        }

        if (call.name == "system.tools.describe")
        {
            if (call.targetToolName.empty())
                return Reject("MISSING_REQUIRED_FIELD", "tool_name", reasonCode, detail);
            if (!call.instrument.empty() || call.orderId != -1 ||
                HasContractFields(call.ibContract) || HasOrderFields(call.ibOrder) ||
                !call.timeInForce.empty() || call.referencePrice != 0.0 ||
                call.expiresAtMs != 0 || call.waitTimeoutMs != 0 ||
                call.afterEventSequence != 0)
                return Reject("UNEXPECTED_TOOL_FIELD",
                              "system.tools.describe accepts only tool_name",
                              reasonCode, detail);
            return true;
        }

        if (call.name == "events.wait")
        {
            if (!call.instrument.empty() || call.orderId != -1 ||
                HasContractFields(call.ibContract) || HasOrderFields(call.ibOrder) ||
                !call.timeInForce.empty() || call.referencePrice != 0.0 ||
                call.expiresAtMs != 0)
                return Reject("UNEXPECTED_TOOL_FIELD",
                              "events.wait accepts only after_sequence and timeout_ms",
                              reasonCode, detail);
            if (call.waitTimeoutMs < 0 || call.waitTimeoutMs > 30000)
                return Reject("INVALID_WAIT_TIMEOUT",
                              "timeout_ms must be between 0 and 30000",
                              reasonCode, detail);
            return true;
        }

        if (call.name == "trade.cancel_order")
        {
            if (call.orderId < 0)
                return Reject("INVALID_ORDER_ID", "order_id must be non-negative",
                              reasonCode, detail);
            if (!call.instrument.empty() || HasContractFields(call.ibContract) ||
                HasOrderFields(call.ibOrder) || !call.timeInForce.empty() ||
                call.referencePrice != 0.0 || call.expiresAtMs != 0 ||
                call.waitTimeoutMs != 0 || call.afterEventSequence != 0)
                return Reject("UNEXPECTED_TOOL_FIELD",
                              "trade.cancel_order accepts only order_id",
                              reasonCode, detail);
            return true;
        }

        if (call.name == "trade.flatten_position")
        {
            if (call.instrument.empty())
                return Reject("MISSING_REQUIRED_FIELD", "instrument", reasonCode, detail);
            if (!IsCanonicalInstrument(call.instrument))
                return Reject("INVALID_INSTRUMENT",
                              "instrument must be a bounded canonical identifier",
                              reasonCode, detail);
            if (call.previewPermit.size() != 71 ||
                call.previewPermit.compare(0, 7, "sha256:") != 0)
                return Reject("PREVIEW_PERMIT_INVALID", "preview_permit",
                              reasonCode, detail);
            if (HasFieldsOtherThanInstrumentContractAndPreviewPermit(call))
                return Reject("UNEXPECTED_TOOL_FIELD",
                              "trade.flatten_position accepts only instrument and preview_permit",
                              reasonCode, detail);
            return true;
        }

        if (call.name == "trade.place_order" || call.name == "risk.preview_order")
        {
            if (call.instrument.empty())
                return Reject("MISSING_REQUIRED_FIELD", "instrument", reasonCode, detail);
            if (!IsCanonicalInstrument(call.instrument))
                return Reject("INVALID_INSTRUMENT",
                              "instrument must be a bounded canonical identifier",
                              reasonCode, detail);
            if (call.orderId != -1 || call.waitTimeoutMs != 0 ||
                call.afterEventSequence != 0 || call.ibOrder.outsideRth)
                return Reject("UNEXPECTED_TOOL_FIELD",
                              "order_id, wait fields and outside_rth are not accepted",
                              reasonCode, detail);
            if (call.ibOrder.action != "BUY" && call.ibOrder.action != "SELL")
                return Reject("INVALID_SIDE", "side must be BUY or SELL",
                              reasonCode, detail);
            if (!std::isfinite(call.ibOrder.totalQuantity) ||
                call.ibOrder.totalQuantity <= 0.0)
                return Reject("INVALID_QUANTITY",
                              "quantity must be finite and greater than zero",
                              reasonCode, detail);
            if (call.ibOrder.orderType != "MKT" && call.ibOrder.orderType != "LMT")
                return Reject("INVALID_ORDER_TYPE",
                              "order_type must be MKT or LMT",
                              reasonCode, detail);
            if (call.timeInForce != "DAY")
                return Reject("INVALID_TIME_IN_FORCE", "tif must be DAY",
                              reasonCode, detail);
            if (call.ibOrder.orderType == "LMT")
            {
                if (!std::isfinite(call.ibOrder.lmtPrice) ||
                    call.ibOrder.lmtPrice <= 0.0)
                    return Reject("INVALID_LIMIT_PRICE",
                                  "LMT requires a finite positive limit_price",
                                  reasonCode, detail);
            }
            else if (call.ibOrder.lmtPrice != 0.0)
                return Reject("INVALID_LIMIT_PRICE",
                              "MKT must not include limit_price",
                              reasonCode, detail);
            if (!std::isfinite(call.referencePrice) || call.referencePrice < 0.0)
                return Reject("INVALID_REFERENCE_PRICE",
                              "reference_price must be finite and non-negative",
                              reasonCode, detail);
            if (call.expiresAtMs <= 0)
                return Reject("INVALID_EXPIRY", "expires_at_ms must be positive",
                              reasonCode, detail);
            if (call.name == "trade.place_order" && !call.previewPermit.empty() &&
                (call.previewPermit.size() != 71 ||
                 call.previewPermit.compare(0, 7, "sha256:") != 0))
                return Reject("PREVIEW_PERMIT_INVALID", "preview_permit",
                              reasonCode, detail);
            if (call.name == "risk.preview_order" && !call.previewPermit.empty())
                return Reject("UNEXPECTED_TOOL_FIELD", "preview_permit",
                              reasonCode, detail);
            return true;
        }

        // Unknown names are rejected by the authoritative registry lookup.
        return true;
    }

private:
    static std::string EscapeJson(const std::string& value)
    {
        static const char digits[] = "0123456789abcdef";
        std::string escaped;
        escaped.reserve(value.size());
        for (std::string::const_iterator it = value.begin();
             it != value.end(); ++it)
        {
            const unsigned char c = static_cast<unsigned char>(*it);
            if (c == '"') escaped += "\\\"";
            else if (c == '\\') escaped += "\\\\";
            else if (c == '\n') escaped += "\\n";
            else if (c == '\r') escaped += "\\r";
            else if (c == '\t') escaped += "\\t";
            else if (c < 0x20)
            {
                escaped += "\\u00";
                escaped.push_back(digits[(c >> 4) & 0x0f]);
                escaped.push_back(digits[c & 0x0f]);
            }
            else escaped.push_back(static_cast<char>(c));
        }
        return escaped;
    }

    static bool HasContractFields(const InstrumentRef& contract)
    {
        return !contract.symbol.empty() || !contract.secType.empty() ||
            !contract.exchange.empty() || !contract.primaryExchange.empty() ||
            !contract.currency.empty() ||
            !contract.lastTradeDateOrContractMonth.empty() ||
            !contract.right.empty() || contract.strike != 0.0 ||
            !contract.multiplier.empty() || !contract.tradingClass.empty() ||
            !contract.localSymbol.empty();
    }

    static bool HasOrderFields(const OrderIntent& order)
    {
        return !order.action.empty() || !order.orderType.empty() ||
            order.totalQuantity != 0.0 || order.lmtPrice != 0.0 ||
            order.outsideRth;
    }

    static bool HasFieldsOtherThanInstrument(const TradingToolCall& call)
    {
        return !call.targetToolName.empty() || call.orderId != -1 ||
            HasContractFields(call.ibContract) || HasOrderFields(call.ibOrder) ||
            !call.timeInForce.empty() || call.referencePrice != 0.0 ||
            call.expiresAtMs != 0 || call.waitTimeoutMs != 0 ||
            call.afterEventSequence != 0 || !call.previewPermit.empty();
    }

    static bool HasFieldsOtherThanInstrumentAndContract(
        const TradingToolCall& call)
    {
        return !call.targetToolName.empty() || call.orderId != -1 ||
            HasOrderFields(call.ibOrder) ||
            !call.timeInForce.empty() || call.referencePrice != 0.0 ||
            call.expiresAtMs != 0 || call.waitTimeoutMs != 0 ||
            call.afterEventSequence != 0 || !call.previewPermit.empty();
    }

    static bool HasFieldsOtherThanInstrumentContractAndPreviewPermit(
        const TradingToolCall& call)
    {
        return !call.targetToolName.empty() || call.orderId != -1 ||
            HasOrderFields(call.ibOrder) || !call.timeInForce.empty() ||
            call.referencePrice != 0.0 || call.expiresAtMs != 0 ||
            call.waitTimeoutMs != 0 || call.afterEventSequence != 0;
    }

    static bool HasFieldsOtherThanCommandId(const TradingToolCall& call)
    {
        return !call.targetToolName.empty() || !call.instrument.empty() ||
            call.orderId != -1 || HasContractFields(call.ibContract) ||
            HasOrderFields(call.ibOrder) || !call.timeInForce.empty() ||
            call.referencePrice != 0.0 || call.expiresAtMs != 0 ||
            call.waitTimeoutMs != 0 || call.afterEventSequence != 0 ||
            !call.previewPermit.empty();
    }

    static bool Reject(const char* code,
                       const char* field,
                       std::string& reasonCode,
                       std::string& detail)
    {
        reasonCode = code;
        detail = field;
        return false;
    }

    static bool IsCanonicalInstrument(const std::string& instrument)
    {
        if (instrument.empty() || instrument.size() > 128) return false;
        for (std::string::const_iterator it = instrument.begin();
             it != instrument.end(); ++it)
        {
            const unsigned char c = static_cast<unsigned char>(*it);
            if (!std::isalnum(c) && c != '.' && c != '-' && c != '_' &&
                c != '/' && c != ':')
                return false;
        }
        return true;
    }
};
