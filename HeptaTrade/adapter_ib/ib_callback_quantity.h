#pragma once

#include <cmath>
#include <limits>

// IB decimal filled/remaining values are converted independently to binary64.
// Their correctly conserved decimal sum can round one representable value
// above the independently converted original total. Allow only that adjacent
// sum, never an oversized component, negative value, non-finite arithmetic,
// or a subnormal total where one ULP could represent a material overfill.
// This predicate does not change quantities or prove a Filled terminal state.
inline bool IbCallbackQuantitySumWithinTotal(
    double filled, double remaining, double total)
{
    if (!std::isfinite(total) || total < 0.0 ||
        !std::isfinite(filled) || filled < 0.0 || filled > total ||
        !std::isfinite(remaining) || remaining < 0.0 || remaining > total)
        return false;
    const double sum = filled + remaining;
    if (!std::isfinite(sum)) return false;
    if (sum <= total) return true;
    return filled > 0.0 && remaining > 0.0 && std::isnormal(total) &&
        sum == std::nextafter(total, std::numeric_limits<double>::infinity());
}
