#pragma once

#include <exception>
#include <functional>
#include <string>

// Transport admission is not a durable command result or economic fill.
// The coordinator must still journal and project this observation.
enum class VenueSubmissionStatus { Accepted, Rejected, Uncertain };

struct VenueSubmissionResult
{
    VenueSubmissionStatus status = VenueSubmissionStatus::Uncertain;
    long orderId = -1;
    std::string reason;
};

// Normalize the retained bool/out-ID adapter boundary exactly once. An
// exception, missing ID or missing reliable rejection never means safe retry.
// Do not catch this helper's allocation failure and turn it into acceptance.
template <typename Send>
VenueSubmissionResult ObserveVenueSubmission(
    const Send& send, const std::function<std::string()>& readRejection)
{
    VenueSubmissionResult result;
    bool accepted = false;
    try
    {
        accepted = send(&result.orderId);
    }
    catch (const std::exception& error)
    {
        result.reason = error.what();
        return result;
    }
    catch (...)
    {
        result.reason = "unknown venue submission exception";
        return result;
    }
    if (accepted)
    {
        if (result.orderId >= 0) result.status = VenueSubmissionStatus::Accepted;
        else result.reason = "adapter accepted submission without an order id";
        return result;
    }
    if (readRejection)
    {
        try
        {
            result.reason = readRejection();
            if (!result.reason.empty()) result.status = VenueSubmissionStatus::Rejected;
        }
        catch (const std::exception& error) { result.reason = error.what(); }
        catch (...) { result.reason = "venue rejection reader threw"; }
    }
    if (result.reason.empty())
        result.reason = "adapter returned false without a reliable rejection reason";
    return result;
}
