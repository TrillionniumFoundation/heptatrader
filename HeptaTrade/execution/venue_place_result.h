#pragma once

#include <string>
#include <utility>

// In-process result, NOT a wire/OMS schema. Rejected asserts that the adapter
// did not submit. Any possible side effect or missing evidence is Uncertain.
enum class VenuePlaceDisposition { Submitted, Reserved, Rejected, Uncertain };
struct VenuePlaceResult
{
    VenuePlaceDisposition disposition = VenuePlaceDisposition::Uncertain;
    long orderId = -1;
    std::string detail;

    static VenuePlaceResult Submitted(long id) { return Make(VenuePlaceDisposition::Submitted, id, ""); }
    static VenuePlaceResult Reserved(long id) { return Make(VenuePlaceDisposition::Reserved, id, ""); }
    static VenuePlaceResult Rejected(const std::string& reason, long id = -1)
    { return Make(VenuePlaceDisposition::Rejected, id, reason); }
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
