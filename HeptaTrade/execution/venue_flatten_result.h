#pragma once

#include <string>

// One in-process outcome captured under the venue send lock. This is not a
// wire or journal version. RejectedBeforeSend must never describe possible IO.
enum class VenueFlattenDisposition { Submitted, RejectedBeforeSend, Uncertain };
enum class VenueFlattenRejection
{
    Generic, QuoteChangedBeforeSend, PositionSnapshotMismatch,
    PositionChangedBeforeSend, PositionChangedBeforeNoop,
    ActiveOrderSnapshotUnsafe, NotExactReduceOnly, KillSwitchEngaged,
    KillSwitchUncertain, BrokerConnectionClosed, EventStreamOverflow,
    RuntimeFatal, RuntimeNotReady
};

inline const char* VenueFlattenRejectionCode(VenueFlattenRejection value) noexcept
{
    switch (value)
    {
    case VenueFlattenRejection::Generic: return "IB_FLATTEN_REJECT";
    case VenueFlattenRejection::QuoteChangedBeforeSend: return "IB_PAPER_FLATTEN_QUOTE_CHANGED_BEFORE_SEND";
    case VenueFlattenRejection::PositionSnapshotMismatch: return "IB_FLATTEN_POSITION_SNAPSHOT_MISMATCH";
    case VenueFlattenRejection::PositionChangedBeforeSend: return "IB_FLATTEN_POSITION_CHANGED_BEFORE_SEND";
    case VenueFlattenRejection::PositionChangedBeforeNoop: return "IB_FLATTEN_POSITION_CHANGED_BEFORE_NOOP";
    case VenueFlattenRejection::ActiveOrderSnapshotUnsafe: return "IB_FLATTEN_ACTIVE_ORDER_SNAPSHOT_UNSAFE";
    case VenueFlattenRejection::NotExactReduceOnly: return "IB_FLATTEN_NOT_EXACT_REDUCE_ONLY";
    case VenueFlattenRejection::KillSwitchEngaged: return "IB_PAPER_KILL_SWITCH_ENGAGED";
    case VenueFlattenRejection::KillSwitchUncertain: return "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN";
    case VenueFlattenRejection::BrokerConnectionClosed: return "IB_PAPER_BROKER_CONNECTION_CLOSED";
    case VenueFlattenRejection::EventStreamOverflow: return "IB_PAPER_EVENT_STREAM_OVERFLOW";
    case VenueFlattenRejection::RuntimeFatal: return "IB_PAPER_RUNTIME_FATAL";
    case VenueFlattenRejection::RuntimeNotReady: return "IB_PAPER_RUNTIME_NOT_READY";
    }
    return nullptr;
}

// Conversion is confined to a producer's already-stable machine reason code;
// the coordinator never reinterprets mutable error state or diagnostic prose.
inline VenueFlattenRejection VenueFlattenRejectionFromReasonCode(const std::string& code)
{
    const VenueFlattenRejection values[] = {
        VenueFlattenRejection::QuoteChangedBeforeSend,
        VenueFlattenRejection::PositionSnapshotMismatch,
        VenueFlattenRejection::PositionChangedBeforeSend,
        VenueFlattenRejection::PositionChangedBeforeNoop,
        VenueFlattenRejection::ActiveOrderSnapshotUnsafe,
        VenueFlattenRejection::NotExactReduceOnly,
        VenueFlattenRejection::KillSwitchEngaged,
        VenueFlattenRejection::KillSwitchUncertain,
        VenueFlattenRejection::BrokerConnectionClosed,
        VenueFlattenRejection::EventStreamOverflow,
        VenueFlattenRejection::RuntimeFatal,
        VenueFlattenRejection::RuntimeNotReady};
    for (const auto value : values)
        if (code == VenueFlattenRejectionCode(value)) return value;
    return VenueFlattenRejection::Generic;
}

struct VenueFlattenResult
{
    VenueFlattenDisposition disposition = VenueFlattenDisposition::Uncertain;
    VenueFlattenRejection rejection = VenueFlattenRejection::Generic;
    long orderId = -1;
    std::string detail;

    static VenueFlattenResult Submitted(long id)
    { return Make(VenueFlattenDisposition::Submitted, id, ""); }
    static VenueFlattenResult RejectedBeforeSend(VenueFlattenRejection code,
                                                 const std::string& detail)
    {
        auto result = Make(VenueFlattenDisposition::RejectedBeforeSend, -1, detail);
        result.rejection = code;
        return result;
    }
    static VenueFlattenResult Uncertain(const std::string& detail, long id = -1)
    { return Make(VenueFlattenDisposition::Uncertain, id, detail); }
private:
    static VenueFlattenResult Make(VenueFlattenDisposition state, long id,
                                    const std::string& detail)
    {
        VenueFlattenResult result;
        result.disposition = state; result.orderId = id; result.detail = detail;
        return result;
    }
};
