#pragma once

#include "execution_authority.h"
#include "venue_place_result.h"
#include <functional>
#include <stdexcept>
#include <utility>

// A frozen coordinator binding: disabled/read-only, immediate, or reserving.
// Factories reject half-configured venues before any intent or external I/O.
// Submission receives the complete privileged command and durable correlation;
// no three-way callback fallback or separate place-error reader remains.
class VenuePlacement
{
public:
    using Submit = std::function<VenuePlaceResult(const PlaceOrderCommand&, const std::string&)>;
    using Activate = std::function<VenueActivationResult(long)>;
    VenuePlacement() = default; // Read/control-only coordinator; placement rejected.
    static VenuePlacement Immediate(Submit submit)
    {
        if (!submit) throw std::invalid_argument("immediate venue requires submit");
        return VenuePlacement(std::move(submit), Activate());
    }
    static VenuePlacement Reserving(Submit reserve, Activate activate)
    {
        if (!reserve || !activate)
            throw std::invalid_argument("reserving venue requires reserve and activate");
        return VenuePlacement(std::move(reserve), std::move(activate));
    }
    bool Configured() const { return static_cast<bool>(m_submit); }
    bool RequiresActivation() const { return static_cast<bool>(m_activate); }

    VenuePlaceResult Dispatch(const PlaceOrderCommand& command, const std::string& correlation) const
    {
        if (!m_submit) throw std::logic_error("disabled placement binding");
        VenuePlaceResult result = m_submit(command, correlation);
        switch (result.disposition)
        {
        case VenuePlaceDisposition::Submitted:
        case VenuePlaceDisposition::Reserved:
            if (result.orderId < 0)
                return VenuePlaceResult::Uncertain("venue accepted without an order id", result.orderId);
            if ((result.disposition == VenuePlaceDisposition::Reserved) != RequiresActivation())
                return VenuePlaceResult::Uncertain("venue placement mode mismatch", result.orderId);
            return result;
        case VenuePlaceDisposition::Rejected:
            if (!result.detail.empty() && VenuePlaceRejectionCode(result.rejection)) return result;
            return VenuePlaceResult::Uncertain("venue rejected without a reliable rejection reason", result.orderId);
        case VenuePlaceDisposition::Uncertain:
            if (result.detail.empty()) result.detail = "venue outcome uncertain";
            return result;
        }
        return VenuePlaceResult::Uncertain("unknown venue placement disposition", result.orderId);
    }
    VenueActivationResult ActivateReserved(long orderId) const
    {
        if (!m_activate) throw std::logic_error("immediate venue cannot activate reservations");
        return m_activate(orderId);
    }
private:
    VenuePlacement(Submit submit, Activate activate)
        : m_submit(std::move(submit)), m_activate(std::move(activate)) {}
    Submit m_submit;
    Activate m_activate;
};
