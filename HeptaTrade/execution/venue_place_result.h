#pragma once

#include <string>

// In-process result, NOT a wire/OMS schema. Rejected asserts that the adapter
// did not submit. Any possible side effect or missing evidence is Uncertain.
enum class VenuePlaceDisposition { Submitted, Reserved, Rejected, Uncertain };

// Fixed classification is independent of diagnostic prose. Values are never
// serialized; the stable strings below preserve the existing wire/OMS contract.
enum class VenuePlaceRejection
{
    Generic,
    KillSwitchEngaged,
    PostFillRefreshPending,
    KillSwitchUncertain,
    QuoteBindingRequired,
    ContractMismatch,
    QuoteChangedBeforeSend
};

inline const char* VenuePlaceRejectionCode(VenuePlaceRejection reason) noexcept
{
    switch (reason)
    {
    case VenuePlaceRejection::Generic: return "IB_PLACE_REJECT";
    case VenuePlaceRejection::KillSwitchEngaged: return "IB_PAPER_KILL_SWITCH_ENGAGED";
    case VenuePlaceRejection::PostFillRefreshPending: return "IB_POST_FILL_RISK_REFRESH_PENDING";
    case VenuePlaceRejection::KillSwitchUncertain: return "IB_PAPER_KILL_SWITCH_STATE_UNCERTAIN";
    case VenuePlaceRejection::QuoteBindingRequired: return "IB_PAPER_PLACE_QUOTE_BINDING_REQUIRED";
    case VenuePlaceRejection::ContractMismatch: return "IB_PAPER_PLACE_CONTRACT_MISMATCH";
    case VenuePlaceRejection::QuoteChangedBeforeSend: return "IB_PAPER_PLACE_QUOTE_CHANGED_BEFORE_SEND";
    }
    return nullptr; // Malformed adapter classification is not a proven rejection.
}

inline VenuePlaceRejection LegacyVenuePlaceRejection(const std::string& reason)
{
    // Compatibility conversion only at the existing producer factory. The
    // coordinator never reinterprets a subsequently edited detail string.
    const VenuePlaceRejection known[] = {
        VenuePlaceRejection::KillSwitchEngaged,
        VenuePlaceRejection::PostFillRefreshPending,
        VenuePlaceRejection::KillSwitchUncertain,
        VenuePlaceRejection::QuoteBindingRequired,
        VenuePlaceRejection::ContractMismatch,
        VenuePlaceRejection::QuoteChangedBeforeSend};
    for (const auto code : known)
        if (reason == VenuePlaceRejectionCode(code)) return code;
    return VenuePlaceRejection::Generic;
}

struct VenuePlaceResult
{
    VenuePlaceDisposition disposition = VenuePlaceDisposition::Uncertain;
    long orderId = -1;
    std::string detail;
    VenuePlaceRejection rejection = VenuePlaceRejection::Generic;

    static VenuePlaceResult Submitted(long id) { return Make(VenuePlaceDisposition::Submitted, id, ""); }
    static VenuePlaceResult Reserved(long id) { return Make(VenuePlaceDisposition::Reserved, id, ""); }
    static VenuePlaceResult Rejected(VenuePlaceRejection code, const std::string& detail, long id = -1)
    {
        VenuePlaceResult result = Make(VenuePlaceDisposition::Rejected, id, detail);
        result.rejection = code;
        return result;
    }
    static VenuePlaceResult Rejected(const std::string& reason, long id = -1)
    { return Rejected(LegacyVenuePlaceRejection(reason), reason, id); }
    static VenuePlaceResult Uncertain(const std::string& reason, long id = -1)
    { return Make(VenuePlaceDisposition::Uncertain, id, reason); }
private:
    static VenuePlaceResult Make(VenuePlaceDisposition state, long id, const std::string& reason)
    {
        VenuePlaceResult result;
        result.disposition = state; result.orderId = id; result.detail = reason;
        return result;
    }
};

struct VenueActivationResult
{
    bool activated = false;
    std::string detail;
    static VenueActivationResult Activated()
    { VenueActivationResult result; result.activated = true; return result; }
    static VenueActivationResult Uncertain(const std::string& reason)
    { VenueActivationResult result; result.detail = reason; return result; }
};
