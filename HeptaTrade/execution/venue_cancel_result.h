#pragma once

#include <string>

// In-process venue result, not a new wire or persistent schema. Only an
// explicit pre-I/O refusal proves that nothing was sent. Default/exception,
// missing evidence and post-send bookkeeping failure are always Uncertain.
enum class VenueCancelDisposition { Submitted, Deferred, RejectedBeforeSend, Uncertain };
struct VenueCancelResult
{
    VenueCancelDisposition disposition = VenueCancelDisposition::Uncertain;
    std::string detail;

    static VenueCancelResult Submitted() { return Make(VenueCancelDisposition::Submitted, ""); }
    static VenueCancelResult Deferred() { return Make(VenueCancelDisposition::Deferred, ""); }
    static VenueCancelResult RejectedBeforeSend(const std::string& reason)
    { return Make(VenueCancelDisposition::RejectedBeforeSend, reason); }
    static VenueCancelResult Uncertain(const std::string& reason)
    { return Make(VenueCancelDisposition::Uncertain, reason); }
private:
    static VenueCancelResult Make(VenueCancelDisposition state, const std::string& reason)
    {
        VenueCancelResult result;
        result.disposition = state;
        result.detail = reason;
        return result;
    }
};
